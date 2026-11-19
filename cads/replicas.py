"""Round-robin inference routing across reusable WebSocket connections."""

import asyncio
from time import monotonic

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException

from cads.protocol import Infer, InferenceResult


class Replica:
    def __init__(self, url: str, connections: int):
        self.url = url
        self.available = asyncio.Semaphore(connections)
        self.idle: list[ClientConnection] = []
        self.failed_until = 0.0

    async def request(self, request: Infer, timeout: float) -> InferenceResult:
        async with self.available:
            socket = None
            try:
                async with asyncio.timeout(timeout):
                    socket = (
                        self.idle.pop()
                        if self.idle
                        else await connect(self.url, open_timeout=timeout, close_timeout=1, max_size=16384)
                    )
                    await socket.send(request.model_dump_json())
                    data = await socket.recv()
                self.idle.append(socket)
            except BaseException:
                if socket is not None:
                    await socket.close()
                raise
        response = InferenceResult.model_validate_json(data)
        if response.run_id != request.run_id or response.seq != request.seq:
            raise ValueError("Inference response does not match the requested step")
        return response


class InferencePool:
    def __init__(self, urls: list[str], timeout: float, connections: int = 4):
        self.replicas = [Replica(url, connections) for url in urls]
        if not self.replicas:
            raise ValueError("Configure at least one inference replica")
        self.timeout = timeout
        self.index = 0

    async def request(self, request: Infer) -> InferenceResult:
        for _ in self.replicas:
            replica = self.replicas[self.index]
            self.index = (self.index + 1) % len(self.replicas)
            if replica.failed_until <= monotonic():
                break
        else:
            raise RuntimeError("No inference replica is reachable")

        try:
            async with asyncio.timeout(self.timeout):
                return await replica.request(request, self.timeout)
        except (TimeoutError, OSError, WebSocketException) as exc:
            replica.failed_until = monotonic() + 5
            raise RuntimeError("Inference replica became unavailable") from exc
        except (ValidationError, ValueError) as exc:
            raise RuntimeError("Inference replica rejected the step or returned an invalid response") from exc

    async def close(self) -> None:
        await asyncio.gather(*(socket.close() for replica in self.replicas for socket in replica.idle))
