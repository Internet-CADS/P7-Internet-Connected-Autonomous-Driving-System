"""REST, server-sent events and the static page (docs/ARCHITECTURE.md, sections 6.3 and 6.4)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from aiohttp import web

from icads.gateway.core import Event, Gateway, Viewer

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
KEEPALIVE_S = 15.0


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, allow_nan=False))


async def _revalidate_page_files(request: web.Request, response: web.StreamResponse) -> None:
    """The page's files: browsers must check for a newer version (a cheap ETag check) before reusing a
    cached copy, so a rebuilt page never runs an old app.js."""
    if not request.path.startswith(("/v1/", "/healthz", "/load")):
        response.headers["Cache-Control"] = "no-cache"


class Http:
    def __init__(self, gateway: Gateway, services: Any, web_dir: Path = WEB_DIR):
        self.gw = gateway
        self.services = services
        self.web_dir = web_dir
        self.loop_lag_ms = 0.0
        self._models: list[dict] | None = None

    def app(self) -> web.Application:
        app = web.Application()
        app.on_response_prepare.append(_revalidate_page_files)
        r = app.router
        r.add_get("/v1/runs", self.runs)
        r.add_get("/v1/runs/{run_id}", self.run)
        r.add_delete("/v1/runs/{run_id}", self.abort)
        r.add_get("/v1/runs/{run_id}/log", self.log)
        r.add_get("/v1/runs/{run_id}/events", self.run_events)
        r.add_get("/v1/events", self.events)
        r.add_get("/v1/models", self.models)
        r.add_get("/healthz", self.healthz)
        r.add_get("/load", self.load)
        if self.web_dir.exists():
            r.add_get("/", self.index)
            r.add_static("/", self.web_dir, show_index=False)
        return app

    # ------------------------------------------------------------ REST
    async def index(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(self.web_dir / "index.html")

    async def runs(self, request: web.Request) -> web.Response:
        q = request.query
        try:
            limit = min(int(q.get("limit", "100")), 10_000)
            offset = int(q.get("offset", "0"))
        except ValueError:
            return _json({"error": "limit and offset must be integers"}, 400)
        synthetic = q.get("synthetic", "false")
        if synthetic not in ("false", "true", "all"):
            return _json({"error": "synthetic must be false, true or all"}, 400)
        runs = await asyncio.to_thread(self.gw.store.read_runs, q.get("status"), q.get("model"), q.get("attack"),
                                       q.get("experiment"), synthetic, limit, offset)
        return _json({"runs": [self.gw.with_live_totals(r) for r in runs]})

    async def _run_or_404(self, run_id: str) -> dict | None:
        return await asyncio.to_thread(self.gw.store.read_run, run_id)

    async def run(self, request: web.Request) -> web.Response:
        run = await self._run_or_404(request.match_info["run_id"])
        if run is None:
            return _json({"error": "unknown run"}, 404)
        return _json(self.gw.with_live_totals(run))

    async def abort(self, request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        summary = await self.gw.abort(run_id)
        if summary is None:
            run = await self._run_or_404(run_id)
            if run is None:
                return _json({"error": "unknown run"}, 404)
            return _json({"error": f"run is {run['status']}, not running"}, 409)
        return _json({"runId": run_id, "status": summary.status, "reason": summary.reason})

    async def log(self, request: web.Request) -> web.StreamResponse:
        run_id = request.match_info["run_id"]
        run = await self._run_or_404(run_id)
        if run is None:
            return _json({"error": "unknown run"}, 404)
        steps = await asyncio.to_thread(self.gw.store.read_steps, run_id)
        resp = web.StreamResponse(headers={
            "Content-Type": "application/x-ndjson",
            "Content-Disposition": f'attachment; filename="{run_id}.jsonl"'})
        await resp.prepare(request)
        settings = {k: v for k, v in run.items() if k not in ("steps", "totalReward", "crashed", "finalPoses",
                                                              "status", "reason", "message", "endedAt")}
        await resp.write((json.dumps({"type": "run", **settings}) + "\n").encode())
        for s in steps:
            await resp.write((json.dumps({"type": "step", **s}) + "\n").encode())
        end = {"type": "end", "status": run["status"], "reason": run["reason"], "message": run["message"],
               "steps": run["steps"], "totalReward": run["totalReward"], "crashed": run["crashed"],
               "finalPoses": run["finalPoses"], "endedAt": run["endedAt"]}
        await resp.write((json.dumps(end) + "\n").encode())
        await resp.write_eof()
        return resp

    async def models(self, request: web.Request) -> web.Response:
        if self._models is None:
            try:
                self._models = await self.services.list_models()
            except Exception as e:  # noqa: BLE001
                return _json({"error": f"inference service: {e}"}, 503)
        return _json({"models": self._models})

    async def healthz(self, request: web.Request) -> web.Response:
        store_ok = True
        try:
            await asyncio.to_thread(self.gw.store.read_runs, None, None, None, None, "all", 1, 0)
        except Exception:  # noqa: BLE001
            store_ok = False
        services = await self.services.health()
        ok = store_ok and services["inference"] and services["perturbation"]
        return _json({"ok": ok, "store": store_ok, **services}, 200 if ok else 503)

    async def load(self, request: web.Request) -> web.Response:
        return _json({
            "activeRuns": len(self.gw.sessions),
            "maxRuns": self.gw.max_runs,
            "viewers": self.gw.viewer_count(),
            "eventLoopLagMs": round(self.loop_lag_ms, 3),
            "writerQueue": self.gw.store.queue_length(),
            "inFlight": self.services.in_flight(),
            "refused": self.gw.refused,
        })

    # ------------------------------------------------------------ server-sent events
    @staticmethod
    async def _sse(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                           "X-Accel-Buffering": "no"})
        await resp.prepare(request)
        return resp

    async def _pump(self, resp: web.StreamResponse, viewer: Viewer, after_t: int = -1) -> None:
        """Send queued events until the close marker; skip steps already sent."""
        while True:
            try:
                ev = await asyncio.wait_for(viewer.queue.get(), KEEPALIVE_S)
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
                continue
            if ev is None:
                return
            if ev.name == "step" and ev.id is not None and ev.id <= after_t:
                continue
            await resp.write(ev.encode())
            if ev.name == "step" and ev.id is not None:
                after_t = ev.id

    async def events(self, request: web.Request) -> web.StreamResponse:
        resp = await self._sse(request)
        viewer = Viewer()
        self.gw.run_listeners.add(viewer)
        try:
            await resp.write(b"retry: 2000\n\n")
            await self._pump(resp, viewer)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.gw.run_listeners.discard(viewer)
        return resp

    async def run_events(self, request: web.Request) -> web.StreamResponse:
        run_id = request.match_info["run_id"]
        try:
            last = int(request.headers.get("Last-Event-ID", request.query.get("lastEventId", "-1")))
        except ValueError:
            last = -1
        viewer = Viewer()
        session = self.gw.active_feed(run_id)
        if session is not None:
            session.feed.viewers.add(viewer)  # subscribe first, so nothing is missed in between
            meta = session.meta
            buffered = list(session.feed.buffer)
        else:
            run = await self._run_or_404(run_id)
            if run is None:
                return _json({"error": "unknown run"}, 404)
            meta = json.dumps({"runId": run_id, "status": run["status"], "settings": {
                "seed": run["seed"], "maxSteps": run["maxSteps"], "modelId": run["modelId"],
                "attack": run["attack"], "epsilon": run["epsilon"], "paceHz": run["paceHz"],
                "labels": run["labels"], "scenario": run["scenario"]},
                "road": run["road"], "obsSpec": run["obsSpec"], "actionNames": run["actionNames"],
                "createdAt": run["createdAt"]}).encode()
            buffered = []
        resp = await self._sse(request)
        try:
            await resp.write(Event("meta", meta).encode())
            # catch up: from memory if the buffer reaches back far enough, otherwise from SQLite
            if buffered and buffered[0][0] <= last + 1:
                for t, ev in buffered:
                    if t > last:
                        await resp.write(ev.encode())
                        last = t
            else:
                for s in await asyncio.to_thread(self.gw.store.read_steps, run_id, last):
                    await resp.write(Event("step", json.dumps(s).encode(), id=s["t"]).encode())
                    last = s["t"]
            if session is not None:
                await self._pump(resp, viewer, last)
            else:
                run = await self._run_or_404(run_id)
                assert run is not None
                end = {"status": run["status"], "reason": run["reason"], "message": run["message"],
                       "steps": run["steps"], "totalReward": run["totalReward"], "crashed": run["crashed"],
                       "finalPoses": run["finalPoses"]}
                await resp.write(Event("end", json.dumps(end).encode(), id=run["steps"]).encode())
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if session is not None:
                session.feed.viewers.discard(viewer)
        return resp
