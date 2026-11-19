"""WebSocket policy evaluation service."""

import asyncio
from pathlib import Path
from time import perf_counter

import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from cads.policy import configure_cpu, load_model
from cads.protocol import Infer, InferenceResult, error


def evaluate(root: Path, request: Infer) -> InferenceResult:
    model = load_model(root, request.model.group, request.model.seed)
    observations = [request.observation]
    if request.clean_observation is not None:
        observations.append(request.clean_observation)
    for observation in observations:
        if any(abs(value) > 1.010001 for value in observation):
            raise ValueError("Observation is outside the supported range")

    start = perf_counter()
    # Evaluate each vector the same way, irrespective of whether the baseline is present.
    with torch.inference_mode():
        values = [model(torch.tensor(obs, dtype=torch.float32).reshape(1, 50))[0] for obs in observations]
        scores, clean_scores = values[0], values[-1]
    return InferenceResult(
        run_id=request.run_id,
        seq=request.seq,
        action=int(scores.argmax()),
        scores=scores.tolist(),
        clean_action=int(clean_scores.argmax()),
        clean_scores=clean_scores.tolist(),
        inference_ms=(perf_counter() - start) * 1000,
    )


def create_app(model_root: Path) -> FastAPI:
    configure_cpu()
    app = FastAPI(title="CADS inference")

    @app.get("/healthz")
    async def health() -> dict:
        return {"ok": True}

    @app.websocket("/ws/infer")
    async def infer(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                data = await socket.receive()
                if data["type"] == "websocket.disconnect":
                    return
                if data.get("text") is None:
                    await socket.send_json(error("BAD_REQUEST", "Inference messages must be JSON text"))
                    continue
                try:
                    request = Infer.model_validate_json(data["text"])
                    response = await asyncio.to_thread(evaluate, model_root, request)
                    await socket.send_text(response.model_dump_json())
                except (ValidationError, ValueError):
                    await socket.send_json(error("BAD_REQUEST", "Invalid inference request or observation"))
                except (OSError, RuntimeError):
                    await socket.send_json(
                        error("MODEL_UNAVAILABLE", "The requested checkpoint could not be served")
                    )
        except WebSocketDisconnect:
            return

    return app
