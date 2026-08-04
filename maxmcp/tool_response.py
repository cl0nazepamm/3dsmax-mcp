"""Structured result envelopes for MCP tool calls."""

from __future__ import annotations

import json
import os
import tempfile
import time
from base64 import b64decode, b64encode
from enum import Enum
from functools import wraps
from inspect import Parameter, Signature, signature
from itertools import count
from pathlib import Path
from typing import Any, Callable, get_type_hints

from pydantic import BaseModel, ConfigDict

from .helpers.error_hints import (
    extract_tool_hint,
    normalize_hint,
    resolve_error_hint,
)


class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    PLUGIN_MISSING = "PLUGIN_MISSING"
    BRIDGE_DOWN = "BRIDGE_DOWN"
    RENDER_BUSY = "RENDER_BUSY"
    SAFE_MODE = "SAFE_MODE"
    BAD_PARAM = "BAD_PARAM"


class ToolEnvelope(BaseModel):
    """Stable MCP tool tripback. Optional fields stay omitted in minimal mode.

    A Pydantic model (not a TypedDict) because FastMCP marks TypedDict
    ``NotRequired`` fields as required in the generated outputSchema.
    """

    model_config = ConfigDict(extra="allow")

    ok: bool
    result: Any | None = None
    error: dict[str, Any] | None = None
    warnings: list[Any] | None = None
    hint: dict[str, Any] | None = None
    transport: dict[str, Any] | None = None
    elapsed_ms: float | None = None


_ERROR_PREFIXES = (
    "error:",
    "unknown action:",
    "unsupported ",
    "no image files found",
    "no textures matched",
    "failed:",
    "failed to ",
    "blocked by safe mode",
    "maxscript error",
)
_ERROR_SUBSTRINGS = (" not found:",)
_RETRYABLE_CODES = {ErrorCode.BRIDGE_DOWN, ErrorCode.RENDER_BUSY}


def tripback_mode() -> str:
    """minimal: ok/result (or ok/error) only. full: include transport + elapsed_ms."""
    value = (
        os.environ.get("MCP_TRIPBACK_MODE")
        or os.environ.get("THREEDSMAX_MCP_TRIPBACK_MODE")
        or "minimal"
    ).strip().lower()
    return value if value in {"minimal", "full"} else "minimal"


def _slim_transport(transport: dict[str, Any] | None) -> dict[str, Any] | None:
    if not transport:
        return None
    slim: dict[str, Any] = {}
    if transport.get("transport"):
        slim["transport"] = transport["transport"]
    if transport.get("fallback_error"):
        slim["fallback_error"] = transport["fallback_error"]
    return slim or None


def _attach_hint(
    payload: dict[str, Any],
    result: Any,
    *,
    tool_name: str = "",
    error: dict[str, Any] | None = None,
    script: str | None = None,
) -> None:
    """Attach a canonical hint: tool-authored first, else auto-resolved on errors."""
    if isinstance(error, dict) and error.get("hint") is not None:
        # Promote to top level (normalized); drop from error to avoid duplication.
        promoted = normalize_hint(error.pop("hint"))
        if promoted is not None:
            payload["hint"] = _json_safe(promoted)
            return

    authored = extract_tool_hint(result)
    if authored is not None:
        payload["hint"] = _json_safe(authored)
        return

    if error is None:
        # Success path: only promote an authored hint (already handled).
        return

    auto = resolve_error_hint(
        tool_name=tool_name,
        error=error,
        raw_result=result,
        script=script,
    )
    if auto is not None:
        payload["hint"] = _json_safe(auto)


def _json_or_raw(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return value
    if stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except (TypeError, ValueError):
        return value


def _coerce_warnings(result: Any) -> list[Any]:
    if isinstance(result, dict):
        warnings = result.get("warnings")
        if isinstance(warnings, list):
            return warnings
        if warnings:
            return [warnings]
    return []


_SPILL_DIR = Path(tempfile.gettempdir()) / "3dsmax-mcp" / "payloads"
_INLINE_BYTES_LIMIT = 8_192
_SPILL_MAX_AGE_SECONDS = 24 * 60 * 60
_MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
}
_spill_counter = count()


def _prune_spill_dir() -> None:
    cutoff = time.time() - _SPILL_MAX_AGE_SECONDS
    try:
        entries = list(_SPILL_DIR.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            continue


def _spill_to_file(data: bytes, extension: str) -> Path:
    """Write an oversized binary payload to disk so envelopes stay token-sized."""
    _SPILL_DIR.mkdir(parents=True, exist_ok=True)
    _prune_spill_dir()
    name = f"payload_{os.getpid()}_{int(time.time() * 1000)}_{next(_spill_counter)}{extension}"
    path = _SPILL_DIR / name
    path.write_bytes(data)
    return path


def _json_safe(value: Any) -> Any:
    """Convert common MCP/Python return values into JSON-serializable data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        if len(value) > _INLINE_BYTES_LIMIT:
            try:
                return {
                    "type": "bytes_file",
                    "file": str(_spill_to_file(value, ".bin")),
                    "size": len(value),
                }
            except OSError:
                pass
        return {
            "type": "bytes",
            "encoding": "base64",
            "size": len(value),
            "data": b64encode(value).decode("ascii"),
        }
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]

    to_image_content = getattr(value, "to_image_content", None)
    if callable(to_image_content):
        content = to_image_content()
        if hasattr(content, "model_dump"):
            data = content.model_dump(by_alias=True)
        elif hasattr(content, "dict"):
            data = content.dict(by_alias=True)
        else:
            data = dict(content)
        mime_type = data.get("mimeType") or data.get("mime_type") or "image/png"
        raw_data = data.get("data") or ""
        try:
            img_bytes = b64decode(raw_data)
            spilled = _spill_to_file(img_bytes, _MIME_EXTENSIONS.get(mime_type, ".img"))
        except (OSError, ValueError):
            return {
                "type": "image",
                "mime_type": mime_type,
                "encoding": "base64",
                "data": raw_data,
            }
        return {
            "type": "image_file",
            "file": str(spilled),
            "mime_type": mime_type,
            "size_bytes": len(img_bytes),
        }

    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if hasattr(value, "dict"):
        return _json_safe(value.dict())
    return repr(value)


def _try_parse_error_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.lower().startswith("maxscript error:"):
        text = text.split(":", 1)[1].strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _classify_error_code(message: str, error_type: str = "") -> ErrorCode:
    lowered = f"{error_type} {message}".lower()
    if "safe mode" in lowered:
        return ErrorCode.SAFE_MODE
    if (
        "named pipe" in lowered
        or "connection refused" in lowered
        or "could not connect" in lowered
        or "timed out" in lowered
        or "transport" in lowered
        or "main thread execution timed out" in lowered
        or ("bridge" in lowered and "not found" in lowered)
    ):
        return ErrorCode.BRIDGE_DOWN
    if "render busy" in lowered or "renderer busy" in lowered or "already rendering" in lowered:
        return ErrorCode.RENDER_BUSY
    if "ambiguous" in lowered or ("multiple " in lowered and "matched" in lowered):
        return ErrorCode.AMBIGUOUS
    if "not found" in lowered or "no material assigned" in lowered or "no modifiers on" in lowered:
        return ErrorCode.NOT_FOUND
    if (
        "unknown object class" in lowered
        or "unknown modifier class" in lowered
        or "unknown material class" in lowered
        or "unknown class" in lowered
        or "plugin missing" in lowered
        or ("plugin" in lowered and "not loaded" in lowered)
    ):
        return ErrorCode.PLUGIN_MISSING
    return ErrorCode.BAD_PARAM


def _normalize_error(
    *,
    error_type: str = "ToolError",
    message: str,
    code: Any = None,
    retryable: Any = None,
    hint: Any | None = None,
    details: Any | None = None,
) -> dict[str, Any]:
    resolved = (
        ErrorCode(code)
        if isinstance(code, str) and code in ErrorCode._value2member_map_
        else _classify_error_code(message, error_type)
    )
    error: dict[str, Any] = {
        "type": error_type,
        "message": message,
        "code": resolved.value,
        "retryable": bool(retryable) if retryable is not None else resolved in _RETRYABLE_CODES,
    }
    if hint is not None:
        error["hint"] = hint
    if details is not None:
        error["details"] = _json_safe(details)
    return error


def _error_from_result(result: Any, raw: Any) -> dict[str, Any] | None:
    if isinstance(result, dict):
        status = str(result.get("status", "")).lower()
        error = result.get("error")
        if status == "error" or error:
            return _normalize_error(
                error_type=str(result.get("error_type") or result.get("type") or "ToolError"),
                message=str(error or result.get("message") or raw),
                code=result.get("code"),
                retryable=result.get("retryable"),
                hint=result.get("hint"),
                details=result.get("details"),
            )
    if isinstance(raw, str):
        stripped = raw.strip()
        structured = _try_parse_error_payload(stripped)
        structured_status = str(structured.get("status", "")).lower() if structured else ""
        if structured and (
            structured.get("error")
            or structured.get("code")
            or structured_status in {"error", "failed"}
        ):
            return _normalize_error(
                error_type=str(structured.get("error_type") or structured.get("type") or "ToolError"),
                message=str(structured.get("error") or structured.get("message") or stripped),
                code=structured.get("code"),
                retryable=structured.get("retryable"),
                hint=structured.get("hint"),
                details=structured.get("details"),
            )
        lowered = stripped.lower()
        if lowered.startswith(_ERROR_PREFIXES) or any(s in lowered for s in _ERROR_SUBSTRINGS):
            return _normalize_error(error_type="ToolError", message=stripped)
    return None


def _error_from_exception(exc: BaseException) -> dict[str, Any]:
    bridge_message = getattr(exc, "bridge_message", None)
    response = getattr(exc, "bridge_response", None)

    structured = _try_parse_error_payload(bridge_message)
    if structured:
        return _normalize_error(
            error_type=str(structured.get("error_type") or structured.get("type") or exc.__class__.__name__),
            message=str(structured.get("error") or structured.get("message") or bridge_message),
            code=structured.get("code"),
            retryable=structured.get("retryable"),
            hint=structured.get("hint"),
            details=structured.get("details"),
        )

    if isinstance(response, dict):
        structured = _try_parse_error_payload(response.get("error"))
        if structured:
            return _normalize_error(
                error_type=str(structured.get("error_type") or structured.get("type") or exc.__class__.__name__),
                message=str(structured.get("error") or structured.get("message") or response.get("error")),
                code=structured.get("code"),
                retryable=structured.get("retryable"),
                hint=structured.get("hint"),
                details=structured.get("details"),
            )

    return _normalize_error(error_type=exc.__class__.__name__, message=str(exc))


def _script_from_call(tool_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    """Extract MAXScript source for execute_maxscript hint resolution."""
    if tool_name != "execute_maxscript":
        return None
    code = kwargs.get("code")
    command = kwargs.get("command")
    if code or command:
        return str(code or command)
    if args:
        return str(args[0]) if args[0] else None
    return None


def _finalize_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate against ToolEnvelope and return a JSON-safe dict (omit nulls)."""
    validated = ToolEnvelope.model_validate(payload)
    return validated.model_dump(mode="json", exclude_none=True)


def envelope_result(
    raw: Any,
    *,
    elapsed_ms: float,
    transport: dict[str, Any] | None = None,
    tool_name: str = "",
    script: str | None = None,
) -> dict[str, Any]:
    """Wrap a tool return value in a stable envelope dict."""
    result = _json_or_raw(raw)
    error = _error_from_result(result, raw)
    warnings = _coerce_warnings(result)

    if tripback_mode() == "minimal":
        if error is None:
            payload: dict[str, Any] = {
                "ok": True,
                "result": _json_safe(result),
            }
            if warnings:
                payload["warnings"] = warnings
            _attach_hint(payload, result, tool_name=tool_name, error=None, script=script)
            return _finalize_envelope(payload)

        payload = {"ok": False, "error": error}
        _attach_hint(payload, result, tool_name=tool_name, error=error, script=script)
        slim = _slim_transport(transport)
        if slim:
            payload["transport"] = slim
        return _finalize_envelope(payload)

    payload = {
        "ok": error is None,
        "result": _json_safe(result) if error is None else None,
        "warnings": warnings,
        "error": error,
        "transport": transport,
        "elapsed_ms": round(elapsed_ms, 3),
    }
    _attach_hint(payload, result, tool_name=tool_name, error=error, script=script)
    return _finalize_envelope(payload)


def envelope_exception(
    exc: BaseException,
    *,
    elapsed_ms: float,
    transport: dict[str, Any] | None = None,
    tool_name: str = "",
    script: str | None = None,
) -> dict[str, Any]:
    error = _error_from_exception(exc)
    if tripback_mode() == "minimal":
        payload: dict[str, Any] = {"ok": False, "error": error}
        slim = _slim_transport(transport)
        if slim:
            payload["transport"] = slim
        _attach_hint(payload, None, tool_name=tool_name, error=error, script=script)
        return _finalize_envelope(payload)

    payload = {
        "ok": False,
        "result": None,
        "warnings": [],
        "error": error,
        "transport": transport,
        "elapsed_ms": round(elapsed_ms, 3),
    }
    _attach_hint(payload, None, tool_name=tool_name, error=error, script=script)
    return _finalize_envelope(payload)


def make_structured_tool(
    fn: Callable[..., Any],
    *,
    transport_provider: Callable[[], dict[str, Any] | None] | None = None,
    before_call: Callable[[], None] | None = None,
) -> Callable[..., dict[str, Any]]:
    """Return a wrapper that preserves input schema and advertises ToolEnvelope output."""

    fn_signature: Signature = signature(fn)
    try:
        resolved_annotations = get_type_hints(fn, include_extras=True)
    except Exception:
        resolved_annotations = dict(getattr(fn, "__annotations__", {}))

    resolved_params: list[Parameter] = []
    for param in fn_signature.parameters.values():
        if param.name in resolved_annotations:
            resolved_params.append(param.replace(annotation=resolved_annotations[param.name]))
        else:
            resolved_params.append(param)

    # Advertise the envelope shape to MCP clients, not the inner tool return type.
    fn_signature = fn_signature.replace(
        parameters=resolved_params,
        return_annotation=ToolEnvelope,
    )
    resolved_annotations = dict(resolved_annotations)
    resolved_annotations["return"] = ToolEnvelope
    tool_name = getattr(fn, "__name__", "") or ""

    @wraps(fn)
    def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if before_call:
            before_call()
        started_at = time.perf_counter()
        script = _script_from_call(tool_name, args, kwargs)
        try:
            raw = fn(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            transport = transport_provider() if transport_provider else None
            return envelope_result(
                raw,
                elapsed_ms=elapsed_ms,
                transport=transport,
                tool_name=tool_name,
                script=script,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            transport = transport_provider() if transport_provider else None
            return envelope_exception(
                exc,
                elapsed_ms=elapsed_ms,
                transport=transport,
                tool_name=tool_name,
                script=script,
            )

    wrapped.__signature__ = fn_signature  # type: ignore[attr-defined]
    wrapped.__annotations__ = resolved_annotations
    return wrapped


# Re-export for callers/tests.
__all__ = [
    "ErrorCode",
    "ToolEnvelope",
    "envelope_result",
    "envelope_exception",
    "make_structured_tool",
    "tripback_mode",
]
