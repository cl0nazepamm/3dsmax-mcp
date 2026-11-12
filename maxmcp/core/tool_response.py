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
from typing import Annotated, Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import undo_scope
from .hints import type_hints
from .max_client import expect_pid

from .error_hints import (
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
    USER_BUSY = "USER_BUSY"
    MAX_BUSY = "MAX_BUSY"
    MAX_NOT_RESPONDING = "MAX_NOT_RESPONDING"
    SAFE_MODE = "SAFE_MODE"
    BAD_PARAM = "BAD_PARAM"
    BAD_NODE_REF = "BAD_NODE_REF"
    NODE_REF_MISMATCH = "NODE_REF_MISMATCH"
    SCENE_CONFLICT = "SCENE_CONFLICT"
    SCENE_JOURNAL_UNAVAILABLE = "SCENE_JOURNAL_UNAVAILABLE"
    TRANSACTION_BUSY = "TRANSACTION_BUSY"
    HIERARCHY_CYCLE = "HIERARCHY_CYCLE"
    DUPLICATE_NAME = "DUPLICATE_NAME"
    PATCH_APPLY_FAILED = "PATCH_APPLY_FAILED"
    MAXSCRIPT_INTERRUPTED = "MAXSCRIPT_INTERRUPTED"
    UNSAVED_CHANGES = "UNSAVED_CHANGES"
    REQUEST_OUTCOME_UNKNOWN = "REQUEST_OUTCOME_UNKNOWN"
    BRIDGE_OUTDATED = "BRIDGE_OUTDATED"
    TRANSFORM_IGNORED = "TRANSFORM_IGNORED"
    CORE_STATIC = "CORE_STATIC"
    CORE_BUSY = "CORE_BUSY"
    CORE_LOAD_FAILED = "CORE_LOAD_FAILED"
    CORE_VERSION_MISMATCH = "CORE_VERSION_MISMATCH"


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
# The bridge returns an uncaught MAXScript exception as this sentinel-prefixed
# string with success=true. A tool that passes it through changed nothing it
# reported, so the envelope treats it as an error.
_MAXSCRIPT_ERROR_SENTINEL = "__MCP_MS_ERR__:"
_RETRYABLE_CODES = {
    ErrorCode.BRIDGE_DOWN,
    ErrorCode.MAX_BUSY,
    ErrorCode.RENDER_BUSY,
    ErrorCode.USER_BUSY,
    ErrorCode.SCENE_CONFLICT,
    ErrorCode.TRANSACTION_BUSY,
    ErrorCode.CORE_BUSY,
}


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
    for key in ("target_pid", "target_pipe", "target_source", "pinned", "selection_generation"):
        if key in transport:
            slim[key] = transport[key]
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


def parse_error_payload(value: Any) -> dict[str, Any] | None:
    """The structured error dict in a value: a dict, or JSON text (optionally "MAXScript error:" prefixed)."""
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
    if "maxscript did not complete" in lowered:
        return ErrorCode.MAXSCRIPT_INTERRUPTED
    if "3ds max (" in lowered and "not responding" in lowered:  # max_client's "3ds Max (PID n) is not responding"
        return ErrorCode.MAX_NOT_RESPONDING
    if "busy with another request" in lowered:
        return ErrorCode.MAX_BUSY
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
        code
        if isinstance(code, str) and code.strip()
        else _classify_error_code(message, error_type).value
    )
    error: dict[str, Any] = {
        "type": error_type,
        "message": message,
        "code": resolved,
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
        if stripped.startswith(_MAXSCRIPT_ERROR_SENTINEL):
            message = stripped[len(_MAXSCRIPT_ERROR_SENTINEL):].strip() or "MAXScript error"
            return _normalize_error(error_type="MAXScriptError", message=message)
        structured = parse_error_payload(stripped)
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
        # Phrase checks are for plain-text results. A JSON object reports its
        # own outcome; a partial success may quote "... not found: name".
        lowered = stripped.lower()
        if structured is None and (
            lowered.startswith(_ERROR_PREFIXES) or any(s in lowered for s in _ERROR_SUBSTRINGS)
        ):
            return _normalize_error(error_type="ToolError", message=stripped)
    return None


def _error_from_exception(exc: BaseException) -> dict[str, Any]:
    bridge_message = getattr(exc, "bridge_message", None)
    response = getattr(exc, "bridge_response", None)

    structured = parse_error_payload(bridge_message)
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
        structured = parse_error_payload(response.get("error"))
        if structured:
            return _normalize_error(
                error_type=str(structured.get("error_type") or structured.get("type") or exc.__class__.__name__),
                message=str(structured.get("error") or structured.get("message") or response.get("error")),
                code=structured.get("code"),
                retryable=structured.get("retryable"),
                hint=structured.get("hint"),
                details=structured.get("details"),
            )

    return _normalize_error(
        error_type=exc.__class__.__name__, message=str(exc),
        code=getattr(exc, "code", None),
        retryable=getattr(exc, "retryable", None),
        details=getattr(exc, "details", None) or None,
    )


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


def _add_dialog_warning(envelope: dict[str, Any], transport: dict[str, Any] | None, tool_name: str) -> dict[str, Any]:
    """Tell the caller about dialogs blocking Max, whoever opened them."""
    dialogs = (transport or {}).get("open_dialogs")
    if not dialogs or tool_name == "max_dialogs":
        return envelope
    titles = ", ".join(repr(d.get("title", "")) for d in dialogs[:3] if isinstance(d, dict))
    envelope.setdefault("warnings", []).append(
        f"3ds Max has {len(dialogs)} open dialog(s): {titles}. Read or answer it with max_dialogs.")
    return envelope


_QUIET_OVERRIDE_WARNINGS = {
    "file_command": (
        "Quiet mode was off for this script because it mentions a scene reset/open/fetch/quit command, "
        "so its prompts were shown; a save prompt returns BLOCKED_BY_DIALOG."),
    "file_command_quiet_ignored": (
        "quiet=True was not applied because the script mentions a scene reset/open/fetch/quit command, "
        "so its prompts were shown. Pass allow_discard=True with quiet=True only when the user "
        "agreed to discard unsaved changes."),
}


def _add_quiet_override_warning(envelope: dict[str, Any], transport: dict[str, Any] | None) -> dict[str, Any]:
    """Tell the caller that a script ran with Max's quiet mode off (meta.quietOverride)."""
    override = (transport or {}).get("quiet_override")
    if not override:
        return envelope
    envelope.setdefault("warnings", []).append(_QUIET_OVERRIDE_WARNINGS.get(
        str(override), f"Quiet mode was off for this script ({override}), so its prompts were shown."))
    return envelope


EXPECTED_PID_PARAM = "expected_pid"
# Every tool carries this parameter, so its description is paid for once per tool in
# each client request. The routing tools explain the shared Max selection.
_EXPECTED_PID_TYPE = Annotated[Optional[int], Field(description="Send nothing unless the call reaches the 3ds Max with this PID.")]


def _add_target_warning(envelope: dict[str, Any], transport: dict[str, Any] | None) -> dict[str, Any]:
    """Tell the caller that its default Max exited and the call went to the new default."""
    moved = (transport or {}).get("retargeted_from")
    if not isinstance(moved, dict):
        return envelope
    envelope.setdefault("warnings", []).append(
        f"The default 3ds Max target (PID {moved.get('pid')}) has exited, so this call went to the current default "
        f"(PID {(transport or {}).get('target_pid')}). Check that it is the Max you meant.")
    return envelope


IMAGE_MAX_EDGE = 1280  # pixels; a render or capture shown to the model costs tokens by its area
IMAGE_LIMIT = 4


def _image_block(path: str) -> Any | None:
    """An image file as MCP image content, downsized to IMAGE_MAX_EDGE; None when it cannot be read."""
    try:
        from io import BytesIO

        from mcp.types import ImageContent
        from PIL import Image

        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE))
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=85)
        return ImageContent(type="image", data=b64encode(buffer.getvalue()).decode("ascii"), mimeType="image/jpeg")
    except Exception:  # noqa: BLE001 - an EXR, a missing file: the reply still carries the path
        return None


def _with_images(envelope: dict[str, Any]) -> Any:
    """The envelope, with the images a script asked to show (result.images) as image content.

    A run_python script lists image files with mx.show (render_setup shows its renders
    itself). They ride along as image blocks, so the model sees them without a second
    call to read the file. Without images, or when none can be read, the envelope is
    returned unchanged.
    """
    result = envelope.get("result")
    error = envelope.get("error")
    holder = result if isinstance(result, dict) else (error.get("details") if isinstance(error, dict) else None)
    images = holder.get("images") if isinstance(holder, dict) else None
    if not isinstance(images, list) or not images:
        return envelope
    blocks = [block for block in (_image_block(path) for path in images[:IMAGE_LIMIT] if isinstance(path, str))
              if block is not None]
    if not blocks:
        return envelope
    from mcp.types import CallToolResult, TextContent

    return CallToolResult(content=[TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False)), *blocks],
                          structuredContent=envelope, isError=False)


def make_structured_tool(
    fn: Callable[..., Any],
    *,
    transport_provider: Callable[[], dict[str, Any] | None] | None = None,
    before_call: Callable[[], None] | None = None,
    pid_guard: bool = False,
    show_images: bool = False,
) -> Callable[..., dict[str, Any]]:
    """Return a wrapper that preserves input schema and advertises ToolEnvelope output.

    pid_guard adds an optional expected_pid argument: every bridge call the tool makes on
    its thread is refused unless it reaches that Max. show_images returns the images a
    reply lists (result.images) as MCP image content beside the envelope; only a tool an
    MCP client calls directly sets it.
    """
    finish = _with_images if show_images else (lambda envelope: envelope)

    fn_signature: Signature = signature(fn)
    try:
        resolved_annotations = type_hints(fn)
    except Exception:
        resolved_annotations = dict(getattr(fn, "__annotations__", {}))

    resolved_params: list[Parameter] = []
    for param in fn_signature.parameters.values():
        if param.name in resolved_annotations:
            resolved_params.append(param.replace(annotation=resolved_annotations[param.name]))
        else:
            resolved_params.append(param)
    pid_guard = pid_guard and EXPECTED_PID_PARAM not in fn_signature.parameters
    if pid_guard:
        guard = Parameter(EXPECTED_PID_PARAM, Parameter.KEYWORD_ONLY, default=None, annotation=_EXPECTED_PID_TYPE)
        at = next((i for i, p in enumerate(resolved_params) if p.kind is Parameter.VAR_KEYWORD), len(resolved_params))
        resolved_params.insert(at, guard)
        resolved_annotations[EXPECTED_PID_PARAM] = _EXPECTED_PID_TYPE

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
        expected_pid = kwargs.pop(EXPECTED_PID_PARAM, None) if pid_guard else None
        script = _script_from_call(tool_name, args, kwargs)
        try:
            with expect_pid(expected_pid), undo_scope.tool_scope(tool_name):
                raw = fn(*args, **kwargs)
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            transport = transport_provider() if transport_provider else None
            return finish(_add_target_warning(_add_quiet_override_warning(_add_dialog_warning(envelope_result(
                raw,
                elapsed_ms=elapsed_ms,
                transport=transport,
                tool_name=tool_name,
                script=script,
            ), transport, tool_name), transport), transport))
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            transport = transport_provider() if transport_provider else None
            return _add_target_warning(_add_quiet_override_warning(_add_dialog_warning(envelope_exception(
                exc,
                elapsed_ms=elapsed_ms,
                transport=transport,
                tool_name=tool_name,
                script=script,
            ), transport, tool_name), transport), transport)

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
    "parse_error_payload",
    "tripback_mode",
]
