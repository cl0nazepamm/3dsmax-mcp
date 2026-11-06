"""Cosmos local gRPC-Web transport. Internal schemas verified against API 0.194.0."""
import base64
import json
import os
import socket
import urllib.error
from uuid import UUID
import struct
import time
import urllib.request
from pathlib import Path


class CosmosError(RuntimeError):
    def __init__(self, message, code="COSMOS_ERROR", retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def asset_id(value):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("asset_id must be a Cosmos UUID") from None


def _varint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def _field(number, value):
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    if isinstance(value, str):
        value = value.encode("utf-8")
    return _varint((number << 3) | 2) + _varint(len(value)) + value


def _decode(data):
    """Decode wire fields only; interpretation of bytes is schema-specific."""
    pos, fields = 0, {}
    def readvar():
        nonlocal pos
        value = shift = 0
        while pos < len(data) and shift < 70:
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
            shift += 7
        raise ValueError("Truncated or invalid protobuf varint")
    while pos < len(data):
        tag = readvar()
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise ValueError("Invalid protobuf field")
        if wire == 0:
            value = readvar()
        elif wire in (1, 2, 5):
            size = readvar() if wire == 2 else (8 if wire == 1 else 4)
            if pos + size > len(data):
                raise ValueError("Truncated protobuf field")
            value = data[pos:pos + size]
            pos += size
        else:
            raise ValueError("Unsupported protobuf wire type")
        fields.setdefault(number, []).append(value)
    return fields


def _one(fields, number, default=b""):
    return fields.get(number, [default])[0]


def _text(fields, number):
    return _one(fields, number).decode("utf-8")


KIND_TAGS = ((1, "model"), (38, "material"), (427, "hdri"))


def kind_of(tag_ids):
    tag_ids = set(tag_ids)
    return next((kind for tag, kind in KIND_TAGS if tag in tag_ids), "other")


def _package(data):
    p = _decode(data)
    identity = _decode(_one(p, 3))
    details = _decode(_one(p, 4))
    revision = _decode(_one(details, 8))
    tags = [_decode(t) for t in details.get(7, [])]
    return {
        "id": _text(identity, 1), "name": _text(details, 1),
        "availability": _one(p, 2, 0),
        "revision": _one(revision, 1, 0),
        "size": _one(details, 10, 0),
        "paid": bool(_one(details, 15, 0)),
        "preview_path": _text(_decode(details.get(6, [b""])[0]), 1),
        "kind": kind_of(_one(t, 1, 0) for t in tags),
        "tags": [{"id": _one(t, 1, 0),
                  "label": _text(_decode(_one(t, 3)), 1)} for t in tags],
    }


# Chaos's documented local Cosmos address; runtime.json may name another instance.
DEFAULT_BASE_URL = "http://127.0.0.1:30305"
# Cosmos Browser 2026.11.04+ serves Chaos's documented MCP endpoint at <base>/mcp.
MCP_PROTOCOL = "2025-06-18"
MCP_MISSING = ("This Cosmos Browser has no MCP endpoint; image search and asset sizes "
               "need Cosmos Browser 2026.11.04 or newer.")


def _mcp_message(raw, content_type, request_id):
    if "text/event-stream" in content_type:
        for line in raw.splitlines():
            if line.startswith("data:"):
                message = json.loads(line[5:].strip())
                if message.get("id") == request_id:
                    return message
        raise CosmosError("Cosmos MCP stream ended without a response.", "COSMOS_MCP_ERROR", True)
    return json.loads(raw)


def runtime_base_url():
    runtime = Path(os.environ["LOCALAPPDATA"]) / "Chaos/Cosmos/runtime.json"
    try:
        server = json.loads(runtime.read_text())["servers"]["http"]
    except (OSError, ValueError, KeyError) as exc:
        raise CosmosError("Cosmos is not running. Open Chaos Cosmos in 3ds Max.",
                          "COSMOS_UNAVAILABLE", True) from exc
    if server["host"] not in ("127.0.0.1", "localhost", "::1"):
        raise CosmosError("Cosmos runtime must point to a local service.",
                          "COSMOS_UNAVAILABLE")
    return "http://%s:%s" % (server["host"], server["port"])


class Cosmos:
    def __init__(self, base_url=None, importer_id=None, timeout=30):
        self.base_url = (base_url or runtime_base_url()).rstrip("/")
        self.importer_id = importer_id
        self.timeout = timeout
        self._mcp_session = None
        self._mcp_ready = False
        self._mcp_id = 1

    def _mcp_post(self, body):
        headers = {"Content-Type": "application/json", "MCP-Protocol-Version": MCP_PROTOCOL,
                   "Accept": "application/json, text/event-stream"}
        if self._mcp_session:
            headers["Mcp-Session-Id"] = self._mcp_session
        request = urllib.request.Request(self.base_url + "/mcp", data=json.dumps(body).encode(),
                                         headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self._mcp_session = response.headers.get("Mcp-Session-Id") or self._mcp_session
                raw = response.read(16 * 1024 * 1024 + 1)
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 405):
                raise CosmosError(MCP_MISSING, "COSMOS_MCP_UNAVAILABLE") from exc
            raise CosmosError("Cosmos MCP HTTP %s." % exc.code, "COSMOS_UNAVAILABLE",
                              exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise CosmosError("Could not complete the Cosmos MCP request: %s" % exc,
                              "COSMOS_UNAVAILABLE", True) from exc
        if len(raw) > 16 * 1024 * 1024:
            raise CosmosError("Cosmos MCP response exceeded the size limit.")
        if raw and "json" not in content_type and "event-stream" not in content_type:
            # Older builds answer unknown paths with the web UI instead of an MCP reply.
            raise CosmosError(MCP_MISSING, "COSMOS_MCP_UNAVAILABLE")
        return raw.decode("utf-8", "replace"), content_type

    def mcp_tool(self, name, arguments):
        """Call a tool on Chaos's Cosmos MCP server; returns its structured result."""
        if not self._mcp_ready:
            raw, content_type = self._mcp_post({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                           "clientInfo": {"name": "3dsmax-mcp", "version": "1"}}})
            if "error" in _mcp_message(raw, content_type, 1):
                raise CosmosError("Cosmos MCP refused the session.", "COSMOS_MCP_ERROR")
            self._mcp_post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self._mcp_ready = True
        self._mcp_id += 1
        raw, content_type = self._mcp_post({"jsonrpc": "2.0", "id": self._mcp_id, "method": "tools/call",
                                            "params": {"name": name, "arguments": arguments}})
        message = _mcp_message(raw, content_type, self._mcp_id)
        if "error" in message:
            raise CosmosError("Cosmos %s failed: %s" % (name, message["error"].get("message", "unknown error")),
                              "COSMOS_MCP_ERROR")
        result = message.get("result") or {}
        text = " ".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise CosmosError("Cosmos %s failed: %s" % (name, text or "unknown error"), "COSMOS_MCP_ERROR")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        try:
            return json.loads(text)
        except ValueError:
            return {"text": text}

    def _rpc(self, service, method, message=b""):
        headers = {"Content-Type": "application/grpc-web-text", "X-Grpc-Web": "1"}
        if self.importer_id:
            headers["x-importer-id"] = self.importer_id
        framed = b"\x00" + struct.pack(">I", len(message)) + message
        request = urllib.request.Request(
            self.base_url + "/content.v1beta2." + service + "/" + method,
            data=base64.b64encode(framed), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise CosmosError("Cosmos response exceeded the size limit.")
                encoded = b"".join(raw.split())
                status = response.headers.get("grpc-status")
                error = response.headers.get("grpc-message", "")
        except urllib.error.HTTPError as exc:
            code = "COSMOS_AUTH_REQUIRED" if exc.code in (401, 403) else "COSMOS_UNAVAILABLE"
            raise CosmosError("Cosmos HTTP %s. Check Cosmos sign-in and service status." % exc.code,
                              code, exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise CosmosError("Could not complete the Cosmos request: %s" % exc,
                              "COSMOS_UNAVAILABLE", True) from exc
        # gRPC-Web may concatenate separately padded base64 frames.
        data = b"".join(base64.b64decode(encoded[i:i+4], validate=True)
                        for i in range(0, len(encoded), 4))
        messages = []
        while data:
            if len(data) < 5:
                raise ValueError("Truncated gRPC-Web frame")
            flag, size = data[0], struct.unpack(">I", data[1:5])[0]
            if len(data) < size + 5:
                raise ValueError("Truncated gRPC-Web payload")
            payload, data = data[5:5+size], data[5+size:]
            if flag & 128:
                trailers = dict(line.split(": ", 1) for line in
                                payload.decode().splitlines() if ": " in line)
                status = trailers.get("grpc-status", status)
                error = trailers.get("grpc-message", error)
            elif flag == 0:
                messages.append(payload)
            else:
                raise ValueError("Compressed gRPC-Web frames are unsupported")
        if status != "0":
            raise CosmosError("Cosmos gRPC status %s: %s" % (status, error),
                              "COSMOS_AUTH_REQUIRED" if status in ("7", "16") else "COSMOS_ERROR",
                              status in ("4", "14"))
        if len(messages) != 1:
            raise RuntimeError("Expected one unary response")
        return _decode(messages[0])

    def importers(self):
        result = self._rpc("ImportAPI", "ListImporters")
        out = []
        for raw in result.get(1, []):
            item = _decode(raw)
            info, plugin, host = [_decode(_one(item, n)) for n in (1, 2, 3)]
            out.append({"id": _text(info, 2), "name": _text(info, 1),
                        "pid": _one(info, 3, 0), "renderer": _text(plugin, 1),
                        "renderer_version": _text(plugin, 2), "host": _text(host, 1)})
        return out

    def search(self, query, limit=10, offset=0, tag_ids=(), downloaded=False):
        if not self.importer_id:
            raise ValueError("Select a live importer_id for compatible search results")
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Use limit 1..100 and a nonnegative offset")
        conditions = [_field(1, 1) + _field(3, query)] if query else []
        conditions += [_field(1, 1) + _field(2, t) for t in tag_ids]
        expression = _field(1, 1) + b"".join(_field(2, c) for c in conditions)
        filters = _field(1, expression)
        if downloaded:
            filters += _field(4, bytes([3]))
        request = (_field(1, filters) + _field(2, _field(1, 3) + _field(2, 1))
                   + _field(3, _field(1, offset) + _field(2, limit)))
        result = self._rpc("PackagesAPI", "FindPackages", request)
        return {"items": [_package(p) for p in result.get(1, [])],
                "total_count": _one(result, 2, 0),
                "matching_count": _one(result, 3, 0)}

    def asset(self, package_id):
        result = self._rpc("PackagesAPI", "FindPackageById", _field(1, asset_id(package_id)))
        return _package(_one(result, 1))

    def download(self, asset_id, revision):
        self.require_sign_in()
        # Success means queued, not complete. Poll asset().availability.
        self._rpc("PackagesAPI", "DownloadPackage",
                  _field(1, asset_id) + _field(2, revision))

    def require_sign_in(self):
        try:
            with urllib.request.urlopen(self.base_url + "/api/v1/userinfo",
                                        timeout=self.timeout) as response:
                if response.status != 200:
                    raise CosmosError("Sign in to Chaos Cosmos.", "COSMOS_AUTH_REQUIRED")
        except urllib.error.HTTPError as exc:
            raise CosmosError("Sign in to Chaos Cosmos with an account that can download this asset.",
                              "COSMOS_AUTH_REQUIRED") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            raise CosmosError("Cosmos sign-in status is unavailable.",
                              "COSMOS_UNAVAILABLE", True) from exc

    def import_asset(self, asset_id, revision):
        if not self.importer_id:
            raise ValueError("Select a live importer_id")
        result = self._rpc("ImportAPI", "Import",
                          _field(1, _field(2, self.importer_id))
                          + _field(2, asset_id) + _field(3, revision))
        status = _one(result, 1, 0)
        if status != 1:
            raise RuntimeError("Cosmos import returned status %s" % status)
        return {"status": "ok", "asset_id": asset_id}
