"""Decode the tyFlow event graph from the flow's own save stream.

`native:tyflow_graph_stream` runs the flow's Save() into memory and returns the
chunks in the .max file layout: USHORT id, ULONG length (header included, high
bit set on chunks that hold chunks). The event graph is chunk 0x0258. It is
the only place tyFlow exposes wiring and operator type identity.

Layout (tyFlow 2.x):
  0x0258 graph
    0x07D0 events: per event 0x07D1 opens, 0x0BB7 closes
      0x07F8 name (UTF-16)  0x07DA uid  0x07E4/0x07EE x/y  0x0807 width
      0x0802 enabled        0x1770 operators: per operator 0x1775 opens
        0x1775 type id      0x1784 name  0x177A uid  0x178E enabled
    0x2328 connections: 0x2332 (source operator uid, target event uid)
"""

from __future__ import annotations

import base64
import struct
from typing import Any

GRAPH = 0x0258
EVENTS = 0x07D0
EVENT_BEGIN = 0x07D1
EVENT_NAME = 0x07F8
EVENT_UID = 0x07DA
EVENT_X = 0x07E4
EVENT_Y = 0x07EE
EVENT_WIDTH = 0x0807
EVENT_ENABLED = 0x0802
OPERATORS = 0x1770
OPERATOR_TYPE = 0x1775
OPERATOR_NAME = 0x1784
OPERATOR_UID = 0x177A
OPERATOR_ENABLED = 0x178E
CONNECTIONS = 0x2328
CONNECTION = 0x2332


class StreamError(ValueError):
    """The stream is not a chunk stream holding a tyFlow graph."""


def parse_chunks(data: bytes, start: int = 0, end: int | None = None) -> list[dict[str, Any]]:
    end = len(data) if end is None else end
    chunks: list[dict[str, Any]] = []
    offset = start
    while offset < end:
        if offset + 6 > end:
            raise StreamError(f"truncated chunk header at {offset}")
        chunk_id, length = struct.unpack_from("<HI", data, offset)
        header = 6
        if length == 0:
            if offset + 14 > end:
                raise StreamError(f"truncated chunk header at {offset}")
            (length,) = struct.unpack_from("<Q", data, offset + 6)
            header = 14
            container = bool(length >> 63)
            length &= (1 << 63) - 1
        else:
            container = bool(length & 0x80000000)
            length &= 0x7FFFFFFF
        if length < header or offset + length > end:
            raise StreamError(f"chunk 0x{chunk_id:04x} at {offset} overruns its parent")
        chunk: dict[str, Any] = {"id": chunk_id, "data": data[offset + header: offset + length]}
        if container:
            chunk["children"] = parse_chunks(data, offset + header, offset + length)
        chunks.append(chunk)
        offset += length
    return chunks


def _u32(chunk: dict[str, Any]) -> int | None:
    raw = chunk["data"]
    return struct.unpack_from("<I", raw)[0] if len(raw) >= 4 else None


def _i32(chunk: dict[str, Any]) -> int | None:
    raw = chunk["data"]
    return struct.unpack_from("<i", raw)[0] if len(raw) >= 4 else None


def _flag(chunk: dict[str, Any]) -> bool | None:
    raw = chunk["data"]
    return bool(raw[0]) if raw else None


def _text(chunk: dict[str, Any]) -> str:
    return chunk["data"].decode("utf-16-le", "replace").rstrip("\x00")


def _find_graph(chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for chunk in chunks:
        children = chunk.get("children")
        if children is None:
            continue
        if chunk["id"] == GRAPH and any(c["id"] == EVENTS for c in children):
            return chunk
        found = _find_graph(children)
        if found is not None:
            return found
    return None


def _operators(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    operators: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for item in chunk.get("children") or []:
        if item["id"] == OPERATOR_TYPE:
            current = {"type_id": _u32(item), "name": "", "uid": None, "enabled": None}
            operators.append(current)
        elif current is None:
            continue
        elif item["id"] == OPERATOR_NAME:
            current["name"] = _text(item)
        elif item["id"] == OPERATOR_UID:
            current["uid"] = _u32(item)
        elif item["id"] == OPERATOR_ENABLED:
            current["enabled"] = _flag(item)
    return operators


def decode_graph(data: bytes) -> dict[str, Any]:
    """Events (editor order) with their operators, and connections by uid."""
    graph = _find_graph(parse_chunks(data))
    if graph is None:
        raise StreamError("no tyFlow event graph in the stream")
    events: list[dict[str, Any]] = []
    connections: list[tuple[int, int]] = []
    for chunk in graph["children"]:
        if chunk["id"] == EVENTS:
            event: dict[str, Any] | None = None
            for item in chunk.get("children") or []:
                if item["id"] == EVENT_BEGIN:
                    event = {"name": "", "uid": None, "position": [None, None], "width": None,
                             "enabled": None, "operators": []}
                    events.append(event)
                elif event is None:
                    continue
                elif item["id"] == EVENT_NAME:
                    event["name"] = _text(item)
                elif item["id"] == EVENT_UID:
                    event["uid"] = _u32(item)
                elif item["id"] == EVENT_X:
                    event["position"][0] = _i32(item)
                elif item["id"] == EVENT_Y:
                    event["position"][1] = _i32(item)
                elif item["id"] == EVENT_WIDTH:
                    event["width"] = _i32(item)
                elif item["id"] == EVENT_ENABLED:
                    event["enabled"] = _flag(item)
                elif item["id"] == OPERATORS:
                    event["operators"] = _operators(item)
        elif chunk["id"] == CONNECTIONS:
            for item in chunk.get("children") or []:
                if item["id"] == CONNECTION and len(item["data"]) >= 8:
                    connections.append(struct.unpack_from("<II", item["data"]))
    return {"events": events, "connections": connections}


def decode_base64(stream: str) -> dict[str, Any]:
    try:
        data = base64.b64decode(stream, validate=True)
    except (ValueError, TypeError) as exc:
        raise StreamError(f"stream is not base64: {exc}") from exc
    return decode_graph(data)
