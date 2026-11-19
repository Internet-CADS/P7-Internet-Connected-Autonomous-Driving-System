"""Run coordination, bounded perturbations, monitoring and durable outcomes."""

import asyncio
import logging
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from multiprocessing import get_context
from pathlib import Path
from time import perf_counter
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocketState
from pydantic import ValidationError

from cads.policy import configure_cpu, perturb_step
from cads.protocol import FailRun, Infer, OpenRun, Outcome, Step, VehicleMessage, error
from cads.replicas import InferencePool
from cads.storage import RunStore, timestamp

LOGGER = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")


class Rejected(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def create_app(
    model_root: Path,
    db_path: Path,
    replica_urls: list[str],
    limit: int = 50,
    workers: int = 2,
    timeout: float = 30,
    idle_timeout: float = 60,
) -> FastAPI:
    if min(limit, workers, timeout, idle_timeout) <= 0:
        raise ValueError("Limits and timeouts must be positive")
    live: dict[str, asyncio.Event] = {}

    def notify(run_id: str) -> None:
        current = live[run_id]
        live[run_id] = asyncio.Event()
        current.set()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = RunStore(db_path)
        app.state.replicas = InferencePool(replica_urls, timeout)
        app.state.workers = ProcessPoolExecutor(
            workers, initializer=configure_cpu, mp_context=get_context("spawn")
        )
        try:
            yield
        finally:
            await app.state.replicas.close()
            await asyncio.to_thread(app.state.workers.shutdown, cancel_futures=True)
            app.state.store.db.close()

    app = FastAPI(title="CADS gateway", lifespan=lifespan)
    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    def find_run(run_id: UUID) -> dict:
        row = app.state.store.run(str(run_id))
        if row is None:
            raise HTTPException(404, "Run not found")
        return row

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/healthz")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/load")
    async def load() -> dict:
        return {"active_runs": len(live), "limit": limit}

    @app.get("/runs")
    async def runs(limit: int = Query(40, ge=1, le=200)) -> list[dict]:
        return app.state.store.runs(limit)

    @app.get("/runs/{run_id}")
    async def run(run_id: UUID) -> dict:
        return find_run(run_id)

    @app.get("/runs/{run_id}/log")
    async def download(run_id: UUID) -> Response:
        find_run(run_id)
        lines = (record + "\n" for _, record in app.state.store.encoded_records(str(run_id)))
        return Response(
            "".join(lines),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.jsonl"'},
        )

    async def next_message(socket: WebSocket):
        try:
            data = await asyncio.wait_for(socket.receive(), timeout=idle_timeout)
            if data["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(data["code"])
            if data.get("text") is None:
                raise Rejected("BAD_MESSAGE", "Vehicle messages must be JSON text")
            return VehicleMessage.validate_json(data["text"])
        except ValidationError as exc:
            raise Rejected("BAD_MESSAGE", "Invalid vehicle message") from exc
        except TimeoutError as exc:
            raise Rejected("IDLE", "Vehicle session timed out") from exc

    async def decide(run_id: str, config: dict, message: Step) -> dict:
        if any(abs(value) > 1.000001 for value in message.observation):
            raise Rejected("BAD_OBSERVATION", "Clean observation is outside the normalized range")
        received_at = timestamp()
        start = perf_counter()
        if config["epsilon"]:
            loop = asyncio.get_running_loop()
            async with asyncio.timeout(timeout):
                vector, magnitude = await loop.run_in_executor(
                    app.state.workers, perturb_step, model_root, config, message.observation, message.seq
                )
        else:
            vector, magnitude = message.observation, 0.0
        inference_start = perf_counter()
        choice = await app.state.replicas.request(
            Infer(
                run_id=UUID(run_id),
                seq=message.seq,
                model=config["model"],
                observation=vector,
                clean_observation=message.observation if config["epsilon"] else None,
            )
        )
        record = choice.model_dump(mode="json", exclude={"type"})
        record.update(
            model={**config["model"], "frame": config["frame"]},
            observation=message.observation,
            perturbed_observation=vector,
            perturbation={"method": config["method"], "epsilon": config["epsilon"], "linf": magnitude},
            vehicles_before=[pose.model_dump() for pose in message.vehicles],
            observation_received_at=received_at,
            decision_sent_at=timestamp(),
            perturbation_ms=(inference_start - start) * 1000,
            gateway_decision_ms=(perf_counter() - start) * 1000,
        )
        return record

    @app.websocket("/ws/vehicle")
    async def vehicle(socket: WebSocket) -> None:
        await socket.accept()
        store: RunStore = app.state.store
        run_id = None
        failure = "Vehicle disconnected before finishing"
        try:
            request = await next_message(socket)
            if not isinstance(request, OpenRun):
                raise Rejected("EXPECTED_OPEN", "Open a run before sending steps")
            if len(live) >= limit:
                raise Rejected("OVERLOADED", "The gateway reached its active run limit")
            config = request.config.model_dump()
            config["frame"] = request.config.model.frame
            run_id = store.open_run(config, [lane.model_dump() for lane in request.lanes])
            live[run_id] = asyncio.Event()
            await socket.send_json({"type": "opened", "run_id": run_id})
            for seq in range(request.config.max_steps):
                message = await next_message(socket)
                if isinstance(message, FailRun):
                    raise Rejected("VEHICLE_FAILED", message.reason)
                if not isinstance(message, Step) or message.seq != seq:
                    raise Rejected("STEP_ORDER", f"Expected step {seq}")
                record = await decide(run_id, config, message)
                await socket.send_json(
                    {"type": "action", "seq": seq, "action": record["action"], "scores": record["scores"]}
                )

                outcome = await next_message(socket)
                if isinstance(outcome, FailRun):
                    raise Rejected("VEHICLE_FAILED", outcome.reason)
                if not isinstance(outcome, Outcome) or outcome.seq != seq:
                    raise Rejected("STEP_ORDER", f"Expected outcome {seq}")
                completed = outcome.terminated or outcome.truncated or seq + 1 == request.config.max_steps
                record.update(outcome.model_dump(exclude={"type"}), committed_at=timestamp())
                store.commit(run_id, record, completed)
                notify(run_id)
                await socket.send_json({"type": "committed", "seq": seq, "completed": completed})
                if completed:
                    return
        except WebSocketDisconnect:
            return
        except Rejected as exc:
            failure = str(exc)
            if run_id is not None:
                store.fail(run_id, failure)
            await socket.send_json(error(exc.code, failure))
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            LOGGER.warning("Run %s could not complete a step: %s", run_id, exc)
            failure = "Step could not complete"
            if run_id is not None:
                store.fail(run_id, failure)
            await socket.send_json(error("STEP_FAILED", failure))
        finally:
            if run_id is not None:
                store.fail(run_id, failure)
                live.pop(run_id).set()
            if socket.client_state == WebSocketState.CONNECTED:
                try:
                    await socket.close()
                except WebSocketDisconnect:
                    pass

    @app.websocket("/ws/runs/{run_id}")
    async def watch(socket: WebSocket, run_id: UUID, after: int = -1) -> None:
        await socket.accept()
        key = str(run_id)
        store: RunStore = app.state.store
        summary = store.run(key)
        if summary is None:
            await socket.send_json(error("NOT_FOUND", "Run not found"))
            await socket.close()
            return
        disconnected = asyncio.create_task(socket.receive())
        try:
            await socket.send_json({"type": "run", "run": summary})
            while not disconnected.done():
                # Capture the signal before reading history so commits cannot be missed.
                signal = live.get(key)
                for seq, record in store.encoded_records(key, after):
                    event = f'{{"type":"step","record":{record}}}'
                    await asyncio.wait_for(socket.send_text(event), timeout=5)
                    after = seq
                if signal is None:
                    await socket.send_json({"type": "status", "run": store.run(key)})
                    await socket.close()
                    return
                changed = asyncio.create_task(signal.wait())
                try:
                    await asyncio.wait((changed, disconnected), return_when=asyncio.FIRST_COMPLETED)
                finally:
                    changed.cancel()
        except (WebSocketDisconnect, OSError, TimeoutError):
            return
        finally:
            disconnected.cancel()

    return app
