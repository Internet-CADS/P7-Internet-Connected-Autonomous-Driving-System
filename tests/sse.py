"""A small blocking server-sent-events reader for tests."""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Iterator


@dataclass
class SseEvent:
    name: str
    data: dict
    id: int | None


def sse(url: str, last_event_id: int | None = None, timeout: float = 30) -> Iterator[SseEvent]:
    req = urllib.request.Request(url)
    if last_event_id is not None:
        req.add_header("Last-Event-ID", str(last_event_id))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        name, data, ev_id = "message", [], None
        for raw in r:
            line = raw.decode().rstrip("\n")
            if line == "":
                if data:
                    yield SseEvent(name, json.loads("\n".join(data)), ev_id)
                name, data, ev_id = "message", [], None
            elif line.startswith(":"):
                continue
            else:
                field, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
                if field == "event":
                    name = value
                elif field == "data":
                    data.append(value)
                elif field == "id":
                    ev_id = int(value)
