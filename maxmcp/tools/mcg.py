"""Agent-facing Max Creation Graph compile, patch, and verification loop.

Generated graphs are process-scoped and live only under a private temporary
workspace.  Installed Autodesk graphs and optional sample corpora are exposed
as read-only sources that must be forked before mutation.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import tempfile
import threading
from collections.abc import Mapping
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..helpers.maxscript import safe_name, safe_string
from ..helpers.mcg_catalog import (
    compound_root,
    find_max_installations,
    find_tool_template,
    iter_graph_files,
    max_root_for_year,
    sample_roots,
    search_offline_operators,
)
from ..helpers.mcg_graph import (
    MCGGraphError,
    MCGHashConflict,
    MCGSecurityError,
    MCGValidationError,
    create_graph_from_template,
    graph_hash,
    inspect_graph,
    is_within,
    patch_graph,
    restore_checkpoint,
)
from ..helpers.mcg_models import MCGPatchOperation, MCGVerificationSpec
from ..server import client, mcp


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROPERTY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_ ]*$")
_PLUGIN_DECL_RE = re.compile(
    r"^\s*plugin\s+(?:simpleObject|modifier)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE | re.MULTILINE,
)
_GRAPH_SUFFIXES = {".maxtool", ".maxcompound"}
_STATE_LOCK = threading.RLock()
_TRANSACTION_LOCK = threading.RLock()
_WORKSPACE_TEMP: tempfile.TemporaryDirectory | None = None
_GRAPHS: dict[str, Path] = {}
_PATH_TO_GRAPH: dict[str, str] = {}
_SOURCES: dict[str, Path] = {}
_CHECKPOINTS: dict[tuple[str, str], Path] = {}
_USED_IDENTIFIERS: set[str] = set()
_OPERATOR_CACHE: dict[str, dict[str, Any] | None] = {}


def _serialized_mcg_transaction(function):
    """Serialize graph write/compile/verify/cleanup transactions in one server process."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        with _TRANSACTION_LOCK:
            return function(*args, **kwargs)

    return wrapped


def _workspace_root() -> Path:
    global _WORKSPACE_TEMP
    with _STATE_LOCK:
        if _WORKSPACE_TEMP is None:
            parent_value = os.environ.get("MCP_MCG_TEMP_ROOT", "").strip()
            parent = Path(parent_value).expanduser() if parent_value else None
            if parent is not None:
                parent.mkdir(parents=True, exist_ok=True)
            _WORKSPACE_TEMP = tempfile.TemporaryDirectory(
                prefix="3dsmax-mcp-mcg-",
                dir=str(parent) if parent is not None else None,
            )
        return Path(_WORKSPACE_TEMP.name).resolve()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _register_graph(path: Path) -> str:
    resolved = path.resolve()
    if not is_within(resolved, _workspace_root()):
        raise MCGSecurityError("Session graph must stay inside the MCG temp workspace")
    key = _path_key(resolved)
    with _STATE_LOCK:
        existing = _PATH_TO_GRAPH.get(key)
        if existing:
            return existing
        graph_id = f"graph_{uuid4().hex[:16]}"
        _GRAPHS[graph_id] = resolved
        _PATH_TO_GRAPH[key] = graph_id
        return graph_id


def _source_id(path: Path) -> str:
    import hashlib

    resolved = path.resolve()
    source_id = "source_" + hashlib.sha256(
        os.path.normcase(str(resolved)).encode("utf-8")
    ).hexdigest()[:16]
    with _STATE_LOCK:
        _SOURCES[source_id] = resolved
    return source_id


def _resolve_graph(graph_id: str, *, allow_source: bool = False) -> Path:
    value = (graph_id or "").strip()
    with _STATE_LOCK:
        path = _GRAPHS.get(value)
        if path is None and allow_source:
            path = _SOURCES.get(value)
    if path is None:
        raise MCGGraphError(f"Unknown MCG graph id: {value or '<empty>'}")
    resolved = path.resolve()
    if value.startswith("graph_") and not is_within(resolved, _workspace_root()):
        raise MCGSecurityError("Registered graph escaped the MCG temp workspace")
    if not resolved.is_file():
        raise MCGGraphError(f"MCG graph file no longer exists: {value}")
    return resolved


def _b64_decode(value: str) -> str:
    if not value:
        return ""
    return base64.b64decode(value.encode("ascii")).decode("utf-8", errors="replace")


def _context_script() -> str:
    return r'''(
try (
    local bridge = dotNetClass "Viper3dsMaxBridge.Main"
    local utf8 = (dotNetClass "System.Text.Encoding").UTF8
    local convert = dotNetClass "System.Convert"
    fn b64 value = convert.ToBase64String (utf8.GetBytes (value as string))
    local maxYear = ((1998 + ((maxVersion())[1] / 1000)) as integer)
    local secure = try (maxOps.isInSecureMode()) catch (false)
    local userTools = try (bridge.UserToolsDirectory as string) catch ("")
    local userCompounds = try (bridge.UserCompoundsDirectory as string) catch ("")
    local userPackages = try (bridge.UserPackagesDirectory as string) catch ("")
    "MCG_CONTEXT|" + (maxYear as string) + "|" + (secure as string) + "|" +
        (b64 userTools) + "|" + (b64 userCompounds) + "|" + (b64 userPackages)
) catch (
    "__ERROR__|" + (getCurrentException() as string)
)
)'''


def _live_context() -> dict[str, Any]:
    response = client.send_command(_context_script(), timeout=30)
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    parts = raw.split("|")
    if len(parts) != 6 or parts[0] != "MCG_CONTEXT":
        raise RuntimeError(f"Unexpected MCG context response: {raw[:240]}")
    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    year = int(parts[1])
    max_root = max_root_for_year(year)
    return {
        "bridge_available": True,
        "max_year": year,
        "max_root": str(max_root) if max_root else "",
        "secure_mode": parts[2].strip().lower() == "true",
        "safe_mode": bool(meta.get("safeMode", meta.get("safe_mode", True))),
        "user_tools": _b64_decode(parts[3]),
        "user_compounds": _b64_decode(parts[4]),
        "user_packages": _b64_decode(parts[5]),
        "transport": meta.get("transport", ""),
    }


def _context_with_fallback() -> dict[str, Any]:
    try:
        context = _live_context()
    except Exception as exc:
        installations = find_max_installations()
        latest = installations[0] if installations else None
        context = {
            "bridge_available": False,
            "bridge_error": str(exc),
            "max_year": int(latest["year"]) if latest else 0,
            "max_root": str(latest["root"]) if latest else "",
            "secure_mode": None,
            "safe_mode": None,
            "user_tools": "",
            "user_compounds": "",
            "user_packages": "",
            "transport": "",
        }
    max_root = Path(context["max_root"]) if context.get("max_root") else None
    context.update(
        {
            "workspace": str(_workspace_root()),
            "workspace_policy": "process-scoped temporary files; no MCG path registration",
            "system_compounds": str(compound_root(max_root)) if max_root else "",
            "sample_roots": [str(path) for path in sample_roots()],
            "supported_kinds": ["geometry", "modifier"],
            "normal_loop": "inspect -> apply_patch(compile+verify) -> diagnose -> retry",
        }
    )
    if max_root:
        templates: dict[str, str] = {}
        for kind in ("geometry", "modifier"):
            try:
                templates[kind] = str(find_tool_template(max_root, kind))
            except (FileNotFoundError, ValueError):
                pass
        context["templates"] = templates
    else:
        context["templates"] = {}
    return context


def _error(
    message: str,
    *,
    error_type: str = "MCGError",
    code: str = "BAD_PARAM",
    retryable: bool = False,
    details: dict[str, Any] | None = None,
    hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "error_type": error_type,
        "error": message,
        "code": code,
        "retryable": retryable,
        "details": details or {},
        "hint": hint
        or {
            "message": "Inspect the returned MCG proof and diagnostics before the next patch.",
            "suggested_tools": ["mcg_inspect_graph", "mcg_search_operators", "mcg_apply_patch"],
        },
    }


def _exception_code(exc: BaseException) -> str:
    bridge_response = getattr(exc, "bridge_response", {})
    response_error = bridge_response.get("error", "") if isinstance(bridge_response, Mapping) else ""
    candidates: list[Any] = [getattr(exc, "bridge_message", ""), response_error, str(exc)]
    for candidate in candidates:
        if isinstance(candidate, Mapping) and candidate.get("code"):
            return str(candidate["code"])
        if not isinstance(candidate, str):
            continue
        variants = [candidate]
        json_start = candidate.find("{")
        if json_start > 0:
            variants.append(candidate[json_start:])
        for variant in variants:
            try:
                payload = json.loads(variant)
            except (TypeError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("code"):
                return str(payload["code"])
    message = str(exc).casefold()
    if "safe mode" in message or isinstance(exc, MCGSecurityError):
        return "SAFE_MODE"
    connection_markers = (
        "bridge",
        "could not connect",
        "connection refused",
        "named pipe",
        "127.0.0.1",
        "timed out",
        "timeout",
        "transport",
    )
    return "BRIDGE_DOWN" if any(marker in message for marker in connection_markers) else "BAD_PARAM"


def _graph_terminal_type(info: dict[str, Any]) -> str:
    terminal = info.get("terminal_output")
    if isinstance(terminal, dict):
        value = terminal.get("type") or terminal.get("output_type") or terminal.get("operator")
        if value:
            return str(value).partition(":")[2].strip().lower() or str(value).lower()
    value = info.get("terminal_output_type")
    if value:
        return str(value).lower()
    for node in info.get("nodes", []) if isinstance(info.get("nodes"), list) else []:
        operator = str(node.get("operator", "")) if isinstance(node, dict) else ""
        if operator.startswith("Output:"):
            return operator.partition(":")[2].strip().lower()
    return ""


def _graph_identifier(info: dict[str, Any], path: Path) -> str:
    metadata = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
    value = (
        info.get("identifier")
        or metadata.get("identifier")
        or info.get("display_identifier")
        or path.stem
    )
    return str(value).strip()


def _executable_findings(info: dict[str, Any]) -> list[dict[str, Any]]:
    findings = info.get("executable_content") or info.get("risky_operators") or []
    if isinstance(findings, list):
        return [item if isinstance(item, dict) else {"kind": str(item)} for item in findings]
    return [{"kind": str(findings)}] if findings else []


def _compile_script(path: Path) -> str:
    path_literal = safe_string(str(path))
    return f'''(
try (
    local graphPath = "{path_literal}"
    local bridge = dotNetClass "Viper3dsMaxBridge.Main"
    local messages = dotNetObject "System.Text.StringBuilder"
    local utf8 = (dotNetClass "System.Text.Encoding").UTF8
    local convert = dotNetClass "System.Convert"
    fn b64 value = convert.ToBase64String (utf8.GetBytes (value as string))
    local asset = dotNetObject "Viper3dsMaxBridge.ProceduralAsset" graphPath
    local started = timeStamp()
    local validated = false
    try (
        asset.Validate messages
        validated = true
    ) catch (
        messages.AppendLine ("Validation failed: " + (getCurrentException() as string))
    )
    local compiled = false
    if validated do compiled = bridge.CompileGraph graphPath messages false
    local elapsed = timeStamp() - started
    local identifier = try (asset.GraphIdentifier as string) catch ("")
    local displayName = try (asset.DisplayName as string) catch ("")
    local assetType = try (asset.TypeDescription() as string) catch (asset.Type as string)
    local classA = try (asset.ClassIdA as string) catch ("0")
    local classB = try (asset.ClassIdB as string) catch ("0")
    local classAvailable = false
    if compiled and identifier != "" do classAvailable = try ((execute identifier) != undefined) catch (false)
    "MCG_COMPILE|" + (validated as string) + "|" + (compiled as string) + "|" +
        (elapsed as string) + "|" + classA + "|" + classB + "|" +
        (classAvailable as string) + "|" + (b64 identifier) + "|" +
        (b64 displayName) + "|" + (b64 assetType) + "|" + (b64 (messages.ToString()))
) catch (
    "__ERROR__|" + (getCurrentException() as string)
)
)'''


def _generated_class_from_wrapper(path: Path) -> str:
    wrapper = path.with_suffix(".ms")
    if not wrapper.is_file():
        return ""
    try:
        source = wrapper.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    match = _PLUGIN_DECL_RE.search(source)
    return match.group(1) if match else ""


def _class_is_available(identifier: str) -> bool:
    if not _IDENTIFIER_RE.fullmatch(identifier):
        return False
    response = client.send_command(
        f'(try ((execute "{identifier}") != undefined) catch (false))',
        timeout=30,
    )
    return str(response.get("result", "")).strip().lower() == "true"


def _compile_path(path: Path, timeout_seconds: int = 120) -> dict[str, Any]:
    response = client.send_command(
        _compile_script(path),
        timeout=max(10, min(int(timeout_seconds), 600)),
    )
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    parts = raw.split("|", 10)
    if len(parts) != 11 or parts[0] != "MCG_COMPILE":
        raise RuntimeError(f"Unexpected MCG compile response: {raw[:500]}")
    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    ms_path = path.with_suffix(".ms")
    txt_path = path.with_suffix(".txt")
    generated_class = _generated_class_from_wrapper(path)
    identifier_class_available = parts[6].lower() == "true"
    class_available = identifier_class_available
    if generated_class and generated_class.casefold() != _b64_decode(parts[7]).casefold():
        class_available = _class_is_available(generated_class)
    return {
        "validated": parts[1].lower() == "true",
        "compiled": parts[2].lower() == "true",
        "elapsed_ms": int(parts[3]),
        "class_id": [int(parts[4]), int(parts[5])],
        "class_available": class_available,
        "identifier_class_available": identifier_class_available,
        "generated_class": generated_class,
        "identifier": _b64_decode(parts[7]),
        "display_name": _b64_decode(parts[8]),
        "asset_type": _b64_decode(parts[9]),
        "diagnostics": _b64_decode(parts[10]).strip(),
        "artifacts": {
            "maxscript": str(ms_path) if ms_path.is_file() else "",
            "diagnostic_graph": str(txt_path) if txt_path.is_file() else "",
        },
        "transport": meta.get("transport", ""),
        "safe_mode": meta.get("safeMode", meta.get("safe_mode")),
    }


def _operator_search_script(query: str, category: str, limit: int) -> str:
    query_value = safe_string(query.casefold())
    category_value = safe_string(category.casefold())
    return f'''(
try (
    local query = "{query_value}"
    local categoryQuery = "{category_value}"
    local maxResults = {max(1, min(int(limit), 100))}
    local ops = (dotNetClass "ViperEngine.ViperContext").GlobalContext.GetAllOps()
    local utf8 = (dotNetClass "System.Text.Encoding").UTF8
    local convert = dotNetClass "System.Convert"
    fn b64 value = convert.ToBase64String (utf8.GetBytes (value as string))
    local rows = stringStream ""
    local ranked = #(#(), #(), #(), #(), #(), #())
    local matched = 0
    local returned = 0
    local iterator = ops.Keys.GetEnumerator()
    while iterator.MoveNext() do (
        local key = iterator.Current
        local mcgOp = ops.Item[key]
        local identifier = try (mcgOp.Identifier as string) catch (key as string)
        local displayName = try (mcgOp.DisplayName as string) catch (identifier)
        local opCategory = try (mcgOp.Category as string) catch ("")
        local description = try (mcgOp.Description as string) catch ("")
        local identifierLower = toLower identifier
        local displayLower = toLower displayName
        local categoryLower = toLower opCategory
        local descriptionLower = toLower description
        local haystack = identifierLower + " " + displayLower + " " + categoryLower + " " + descriptionLower
        local categoryOk = (categoryQuery == "" or findString categoryLower categoryQuery != undefined)
        local queryOk = (query == "" or findString haystack query != undefined)
        if queryOk and categoryOk do (
            matched += 1
            local rank = 6
            if query != "" do (
                if identifierLower == query then rank = 1
                else if (findString identifierLower query) == 1 then rank = 2
                else if (findString displayLower query) == 1 then rank = 3
                else if findString identifierLower query != undefined then rank = 4
                else if findString displayLower query != undefined then rank = 5
            )
            local returnType = try ((mcgOp.GetReturnType()).FullName as string) catch ("")
            local impure = try (mcgOp.Impure as string) catch ("false")
            local deprecated = try (mcgOp.Deprecated as string) catch ("false")
            local source = try (mcgOp.LoadingFileName as string) catch ("")
            local inputs = stringStream ""
            local ports = try (mcgOp.InputPorts) catch (#())
            for i = 1 to ports.count do (
                local port = ports[i]
                local portName = try (port.Name as string) catch ("arg" + ((i - 1) as string))
                local portType = try (port.Type.FullName as string) catch ("")
                if i > 1 do format "," to:inputs
                format "%:%:%" (i - 1) (b64 portName) (b64 portType) to:inputs
            )
            local row = stringStream ""
            format "%\t%\t%\t%\t%\t%\t%\t%\t%\n" \
                (b64 identifier) (b64 displayName) (b64 opCategory) (b64 description) \
                (b64 returnType) impure deprecated (b64 source) (inputs as string) to:row
            append ranked[rank] (row as string)
        )
    )
    for bucket in ranked do (
        sort bucket
        for row in bucket while returned < maxResults do (
            format "%" row to:rows
            returned += 1
        )
    )
    "MCG_OPS|" + (matched as string) + "|" + (returned as string) + "\n" + (rows as string)
) catch (
    "__ERROR__|" + (getCurrentException() as string)
)
)'''


def _parse_live_operators(raw: str) -> dict[str, Any]:
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    lines = raw.splitlines()
    if not lines or not lines[0].startswith("MCG_OPS|"):
        raise RuntimeError(f"Unexpected live operator response: {raw[:240]}")
    header = lines[0].split("|")
    operators: list[dict[str, Any]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 9:
            continue
        inputs: list[dict[str, Any]] = []
        if fields[8]:
            for item in fields[8].split(","):
                pieces = item.split(":", 2)
                if len(pieces) == 3:
                    inputs.append(
                        {
                            "index": int(pieces[0]),
                            "name": _b64_decode(pieces[1]),
                            "type": _b64_decode(pieces[2]),
                        }
                    )
        operators.append(
            {
                "identifier": _b64_decode(fields[0]),
                "display_name": _b64_decode(fields[1]),
                "category": _b64_decode(fields[2]),
                "description": _b64_decode(fields[3]),
                "return_type": _b64_decode(fields[4]),
                "typed": True,
                "outputs": [
                    {
                        "index": 0,
                        "name": "value",
                        "kind": "value",
                        "type": _b64_decode(fields[4]),
                    },
                    {
                        "index": 1,
                        "name": "function",
                        "kind": "function",
                        "type": "",
                    },
                ],
                "impure": fields[5].lower() == "true",
                "impure_known": True,
                "deprecated": fields[6].lower() == "true",
                "source_path": _b64_decode(fields[7]),
                "inputs": inputs,
            }
        )
    return {
        "source": "live_viper_context",
        "matched": int(header[1]),
        "returned": int(header[2]),
        "operators": operators,
    }


def _live_operator_search(query: str, category: str, limit: int) -> dict[str, Any]:
    response = client.send_command(
        _operator_search_script(query, category, limit),
        timeout=60,
    )
    result = _parse_live_operators(str(response.get("result", "")))
    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    result["transport"] = meta.get("transport", "")
    result["query"] = query
    result["category"] = category
    return result


def _operator_record(identifier: str) -> dict[str, Any] | None:
    cache_key = identifier.casefold()
    with _STATE_LOCK:
        if cache_key in _OPERATOR_CACHE:
            return _OPERATOR_CACHE[cache_key]
    resolved: dict[str, Any] | None = None
    try:
        live = _live_operator_search(identifier, "", 10)
        for record in live["operators"]:
            if str(record.get("identifier", "")).casefold() == identifier.casefold():
                resolved = record
                break
    except Exception:
        pass
    if resolved is None:
        context = _context_with_fallback()
        max_root = Path(context["max_root"]) if context.get("max_root") else None
        offline = search_offline_operators(max_root, query=identifier, limit=10)
        for record in offline["operators"]:
            if str(record.get("identifier", "")).casefold() == identifier.casefold():
                resolved = record
                break
    if resolved is not None:
        with _STATE_LOCK:
            _OPERATOR_CACHE[cache_key] = resolved
    return resolved


def _dependency_security(
    info: dict[str, Any],
    *,
    max_root: Path | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve compound dependencies and surface hidden code/impurity."""
    pending = [
        str(node.get("operator", ""))
        for node in info.get("nodes", [])
        if isinstance(node, dict) and node.get("operator")
    ]
    seen: set[str] = set()
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    while pending and len(seen) < 512:
        operator = pending.pop()
        key = operator.casefold()
        if key in seen or operator.startswith(("Output:", "Parameter:", "Input:")):
            continue
        seen.add(key)
        record = _operator_record(operator)
        if not isinstance(record, dict):
            blockers.append(
                {
                    "kind": "unresolved_dependency",
                    "operator": operator,
                    "message": (
                        f"{operator} could not be resolved in the live or offline operator "
                        "catalog, so its executable dependencies cannot be verified"
                    ),
                }
            )
            continue
        if record.get("impure"):
            blockers.append(
                {
                    "kind": "impure_operator",
                    "operator": operator,
                    "message": (
                        f"{operator} is marked impure by the Viper catalog and may mutate "
                        "scene state during semantic verification"
                    ),
                }
            )
        elif record.get("impure_known") is False:
            blockers.append(
                {
                    "kind": "unknown_impurity",
                    "operator": operator,
                    "message": (
                        f"{operator} was resolved only through untyped offline metadata; "
                        "its scene-mutation behavior must be confirmed by the live Viper catalog"
                    ),
                }
            )
        source_value = str(record.get("source_path", "")).strip()
        if not source_value:
            continue
        source = Path(source_value)
        if source.suffix.casefold() == ".maxcompound" and source.is_file():
            dependency = inspect_graph(source)
            for finding in _executable_findings(dependency):
                nested = dict(finding)
                nested["dependency"] = operator
                nested["source_path"] = str(source)
                blockers.append(nested)
            pending.extend(
                str(node.get("operator", ""))
                for node in dependency.get("nodes", [])
                if isinstance(node, dict) and node.get("operator")
            )
        elif source.suffix.casefold() in {".dll", ".exe"} and (
            max_root is None or not is_within(source, max_root)
        ):
            blockers.append(
                {
                    "kind": "custom_assembly",
                    "operator": operator,
                    "source_path": str(source),
                    "message": f"{operator} resolves through an unapproved executable dependency",
                }
            )
    if pending:
        blockers.append(
            {
                "kind": "dependency_limit",
                "message": "MCG dependency scan exceeded 512 resolved operators and was blocked",
            }
        )
    return blockers, warnings


def _model_mapping(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(exclude_none=True))
    if isinstance(value, Mapping):
        return dict(value)
    raise MCGValidationError("MCG patch operations must be objects")


def _verification_mapping(value: MCGVerificationSpec | Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(exclude_none=True))
    if isinstance(value, Mapping):
        return dict(value)
    raise MCGValidationError("verification must be an object")


def _normalize_node_id(value: Any) -> str:
    if isinstance(value, bool):
        raise MCGValidationError("node id cannot be boolean")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise MCGValidationError(f"node id must be a whole number, not {value!r}")
        number = int(value)
    else:
        text_value = str(value).strip()
        if not text_value.isdigit():
            raise MCGValidationError(f"node id must be a non-negative integer, not {value!r}")
        number = int(text_value)
    if number < 0:
        raise MCGValidationError("node id must be non-negative")
    return str(number)


def _normalize_port_index(value: Any, *, name: str, source: bool) -> int:
    if isinstance(value, bool):
        raise MCGValidationError(f"{name} cannot be boolean")
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        raise MCGValidationError(f"{name} must be a whole number, not {value!r}")
    try:
        index = int(value)
    except (TypeError, ValueError) as exc:
        raise MCGValidationError(f"{name} must be an integer, not {value!r}") from exc
    if index < 0 or (source and index not in {0, 1}):
        expected = "0 or 1" if source else "non-negative"
        raise MCGValidationError(f"{name} must be {expected}, not {index}")
    return index


def _normalize_patch_ports(info: dict[str, Any], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    node_ops: dict[str, str] = {}
    for node in info.get("nodes", []) if isinstance(info.get("nodes"), list) else []:
        if isinstance(node, dict) and node.get("id") is not None and node.get("operator"):
            node_ops[_normalize_node_id(node["id"])] = str(node["operator"])

    normalized: list[dict[str, Any]] = []
    operator_cache: dict[str, dict[str, Any] | None] = {}
    for raw in operations:
        operation = _model_mapping(raw)
        action = str(
            operation.get("op") or operation.get("action") or operation.get("type") or ""
        ).strip().lower().replace("-", "_")
        operation["op"] = action
        if action == "add_node":
            nested = operation.get("node")
            payload = nested if isinstance(nested, Mapping) else operation
            attributes = payload.get("attributes") if isinstance(payload.get("attributes"), Mapping) else {}
            node_id = payload.get("id", payload.get("node_id"))
            operator = payload.get("operator") or attributes.get("operator")
            if node_id is not None and operator:
                node_ops[_normalize_node_id(node_id)] = str(operator)
        elif action in {"set_node", "replace_operator"}:
            node_id = operation.get("id", operation.get("node_id"))
            attributes = operation.get("attributes") if isinstance(operation.get("attributes"), Mapping) else {}
            operator = (
                operation.get("operator")
                or operation.get("new_operator")
                or operation.get("value")
                or attributes.get("operator")
            )
            if node_id is not None and operator:
                node_ops[_normalize_node_id(node_id)] = str(operator)
        elif action == "remove_node":
            node_id = operation.get("id", operation.get("node_id"))
            if node_id is not None:
                node_ops.pop(_normalize_node_id(node_id), None)
        elif action in {"connect", "disconnect"}:
            nested = operation.get("connection")
            connection = dict(nested) if isinstance(nested, Mapping) else operation

            def pick(*names: str, default: Any = None) -> Any:
                for key_name in names:
                    if key_name in connection:
                        return connection[key_name]
                return default

            source_id = _normalize_node_id(
                pick("source_node", "sourceNode", "sourcenode", "source")
            )
            dest_id = _normalize_node_id(
                pick("dest_node", "destNode", "destnode", "destination")
            )
            connection["source_node"] = source_id
            connection["dest_node"] = dest_id
            for side, node_id in (("source", source_id), ("dest", dest_id)):
                key = f"{side}_port"
                value = pick(key, f"{side}Port", f"{side}port", default=0)
                if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
                    port_name = value.strip()
                    if side == "source":
                        source_name = port_name.casefold()
                        if source_name in {"function", "closure", "lambda"}:
                            connection[key] = 1
                        elif source_name in {"value", "result", "output", "geometry", "modifier"}:
                            connection[key] = 0
                        else:
                            raise MCGValidationError(
                                "source_port must be value (0) or function (1), "
                                f"not {port_name!r}"
                            )
                    else:
                        operator = node_ops.get(node_id, "")
                        if operator.startswith("Output:"):
                            connection[key] = 0
                        else:
                            if operator not in operator_cache:
                                operator_cache[operator] = _operator_record(operator) if operator else None
                            record = operator_cache.get(operator)
                            ports = record.get("inputs", []) if isinstance(record, dict) else []
                            matches = [
                                port for port in ports
                                if str(port.get("name", "")).casefold() == port_name.casefold()
                            ]
                            if not matches:
                                available = [str(port.get("name", "")) for port in ports]
                                raise MCGValidationError(
                                    f"Input port {port_name!r} not found on {operator or node_id}; available: {available}"
                                )
                            connection[key] = _normalize_port_index(
                                matches[0]["index"], name=key, source=False
                            )
                else:
                    connection[key] = _normalize_port_index(
                        value,
                        name=key,
                        source=side == "source",
                    )
            if isinstance(nested, Mapping):
                operation["connection"] = connection
            else:
                operation.update(connection)
        normalized.append(operation)
    return normalized


def _mxs_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MCGValidationError("Verification values must be finite")
        return repr(value)
    if isinstance(value, str):
        return f'"{safe_string(value)}"'
    if isinstance(value, (list, tuple)) and 2 <= len(value) <= 4:
        if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            raise MCGValidationError("Vector verification values must be numeric")
        return "[" + ",".join(repr(float(item)) for item in value) + "]"
    raise MCGValidationError(f"Unsupported verification value: {value!r}")


def _parameter_script(target: str, parameters: dict[str, Any]) -> tuple[str, list[str]]:
    lines: list[str] = []
    names: list[str] = []
    for name, value in parameters.items():
        if not _PROPERTY_RE.fullmatch(str(name)):
            raise MCGValidationError(f"Unsafe MCG parameter name: {name!r}")
        escaped = safe_name(str(name))
        lines.append(f"setProperty {target} #'{escaped}' {_mxs_value(value)}")
        names.append(str(name))
    return "\n        ".join(lines), names


def _property_readback_expression(target: str, property_names: list[str]) -> str:
    expressions: list[str] = []
    for name in property_names:
        escaped = safe_name(name)
        expressions.append(
            f'(b64 "{safe_string(name)}") + ":" + '
            f"(b64 ((getProperty {target} #'{escaped}') as string))"
        )
    return ' + ";" + '.join(expressions) if expressions else '""'


def _geometry_verify_script(identifier: str, parameters: dict[str, Any]) -> str:
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise MCGValidationError(f"Unsafe generated class identifier: {identifier!r}")
    object_name = f"MCP_MCG_VERIFY_{uuid4().hex[:10]}"
    assignments, property_names = _parameter_script("testObject", parameters)
    props_expr = _property_readback_expression("testObject", property_names)
    return f'''(
local testObject = undefined
local testMesh = undefined
try (
    local utf8 = (dotNetClass "System.Text.Encoding").UTF8
    local convert = dotNetClass "System.Convert"
    fn b64 value = convert.ToBase64String (utf8.GetBytes (value as string))
    local toolClass = execute "{identifier}"
    testObject = toolClass name:"{object_name}"
    {assignments}
    testMesh = snapshotAsMesh testObject
    local bbMin = testObject.min
    local bbMax = testObject.max
    local dims = bbMax - bbMin
    local className = (classOf testObject) as string
    local propData = {props_expr}
    local numVerts = testMesh.numVerts
    local numFaces = testMesh.numFaces
    try (delete testMesh) catch ()
    testMesh = undefined
    try (delete testObject) catch ()
    testObject = undefined
    local disposed = (getNodeByName "{object_name}" == undefined)
    local result = "MCG_VERIFY|geometry|" + (b64 className) + "|" +
        (numVerts as string) + "|" + (numFaces as string) + "|" +
        (dims.x as string) + "|" + (dims.y as string) + "|" + (dims.z as string) + "|" +
        (bbMin.x as string) + "|" + (bbMin.y as string) + "|" + (bbMin.z as string) + "|" +
        (bbMax.x as string) + "|" + (bbMax.y as string) + "|" + (bbMax.z as string) + "|" +
        (b64 propData) + "|" + (disposed as string)
    result
) catch (
    local message = getCurrentException() as string
    try (if testMesh != undefined do delete testMesh) catch ()
    try (if testObject != undefined do delete testObject) catch ()
    "__ERROR__|" + message
)
)'''


def _modifier_verify_script(identifier: str, parameters: dict[str, Any]) -> str:
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise MCGValidationError(f"Unsafe generated class identifier: {identifier!r}")
    object_name = f"MCP_MCG_VERIFY_{uuid4().hex[:10]}"
    assignments, property_names = _parameter_script("testModifier", parameters)
    props_expr = _property_readback_expression("testModifier", property_names)
    return f'''(
local testObject = undefined
local beforeMesh = undefined
local afterMesh = undefined
try (
    local utf8 = (dotNetClass "System.Text.Encoding").UTF8
    local convert = dotNetClass "System.Convert"
    fn b64 value = convert.ToBase64String (utf8.GetBytes (value as string))
    testObject = Box name:"{object_name}" width:10 length:10 height:10
    beforeMesh = snapshotAsMesh testObject
    local beforeMin = testObject.min
    local beforeMax = testObject.max
    local beforeDims = beforeMax - beforeMin
    local beforeCenter = (beforeMin + beforeMax) / 2.0
    local toolClass = execute "{identifier}"
    local testModifier = toolClass()
    {assignments}
    addModifier testObject testModifier
    afterMesh = snapshotAsMesh testObject
    local afterMin = testObject.min
    local afterMax = testObject.max
    local afterDims = afterMax - afterMin
    local afterCenter = (afterMin + afterMax) / 2.0
    local className = (classOf testModifier) as string
    local propData = {props_expr}
    local beforeVerts = beforeMesh.numVerts
    local beforeFaces = beforeMesh.numFaces
    local afterVerts = afterMesh.numVerts
    local afterFaces = afterMesh.numFaces
    try (delete beforeMesh) catch ()
    try (delete afterMesh) catch ()
    beforeMesh = undefined
    afterMesh = undefined
    try (delete testObject) catch ()
    testObject = undefined
    local disposed = (getNodeByName "{object_name}" == undefined)
    local result = "MCG_VERIFY|modifier|" + (b64 className) + "|" +
        (beforeVerts as string) + "|" + (beforeFaces as string) + "|" +
        (beforeDims.x as string) + "|" + (beforeDims.y as string) + "|" + (beforeDims.z as string) + "|" +
        (beforeCenter.x as string) + "|" + (beforeCenter.y as string) + "|" + (beforeCenter.z as string) + "|" +
        (afterVerts as string) + "|" + (afterFaces as string) + "|" +
        (afterDims.x as string) + "|" + (afterDims.y as string) + "|" + (afterDims.z as string) + "|" +
        (afterCenter.x as string) + "|" + (afterCenter.y as string) + "|" + (afterCenter.z as string) + "|" +
        (b64 propData) + "|" + (disposed as string)
    result
) catch (
    local message = getCurrentException() as string
    try (if beforeMesh != undefined do delete beforeMesh) catch ()
    try (if afterMesh != undefined do delete afterMesh) catch ()
    try (if testObject != undefined do delete testObject) catch ()
    "__ERROR__|" + message
)
)'''


def _parse_verification(raw: str) -> dict[str, Any]:
    if raw.startswith("__ERROR__|"):
        raise RuntimeError(raw.split("|", 1)[1])
    parts = raw.split("|")
    if len(parts) < 3 or parts[0] != "MCG_VERIFY":
        raise RuntimeError(f"Unexpected MCG verification response: {raw[:500]}")
    kind = parts[1]
    if kind == "geometry" and len(parts) == 16:
        properties: dict[str, str] = {}
        prop_data = _b64_decode(parts[14])
        for item in prop_data.split(";") if prop_data else []:
            key, separator, value = item.partition(":")
            if separator:
                properties[_b64_decode(key)] = _b64_decode(value)
        return {
            "kind": kind,
            "class": _b64_decode(parts[2]),
            "num_vertices": int(parts[3]),
            "num_faces": int(parts[4]),
            "dimensions": [float(parts[5]), float(parts[6]), float(parts[7])],
            "bounds": {
                "min": [float(parts[8]), float(parts[9]), float(parts[10])],
                "max": [float(parts[11]), float(parts[12]), float(parts[13])],
            },
            "parameters": properties,
            "disposed": parts[15].lower() == "true",
        }
    if kind == "modifier" and len(parts) == 21:
        properties: dict[str, str] = {}
        prop_data = _b64_decode(parts[19])
        for item in prop_data.split(";") if prop_data else []:
            key, separator, value = item.partition(":")
            if separator:
                properties[_b64_decode(key)] = _b64_decode(value)
        return {
            "kind": kind,
            "class": _b64_decode(parts[2]),
            "input": {
                "num_vertices": int(parts[3]),
                "num_faces": int(parts[4]),
                "dimensions": [float(parts[5]), float(parts[6]), float(parts[7])],
                "center": [float(parts[8]), float(parts[9]), float(parts[10])],
            },
            "output": {
                "num_vertices": int(parts[11]),
                "num_faces": int(parts[12]),
                "dimensions": [float(parts[13]), float(parts[14]), float(parts[15])],
                "center": [float(parts[16]), float(parts[17]), float(parts[18])],
            },
            "parameters": properties,
            "disposed": parts[20].lower() == "true",
        }
    raise RuntimeError(f"Malformed MCG verification response: {raw[:500]}")


def _parameter_matches(actual: str, expected: Any, tolerance: float) -> bool:
    if isinstance(expected, bool):
        return actual.strip().casefold() == ("true" if expected else "false")
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - float(expected)) <= tolerance
        except ValueError:
            return False
    if isinstance(expected, list):
        numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", actual)
        if len(numbers) != len(expected):
            return False
        return all(abs(float(item) - float(target)) <= tolerance for item, target in zip(numbers, expected))
    return actual == str(expected)


def _evaluate_acceptance(result: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    expect = spec.get("expect") if isinstance(spec.get("expect"), dict) else {}
    tolerance = float(spec.get("tolerance", 1e-4))
    checks: list[dict[str, Any]] = []

    def check(name: str, actual: Any, expected: Any, passed: bool) -> None:
        checks.append({"check": name, "actual": actual, "expected": expected, "passed": passed})

    if "class_matches" in result:
        check("generated_class", result.get("class"), result.get("generated_class"), result.get("class_matches") is True)
    check("disposed", result.get("disposed"), True, result.get("disposed") is True)
    if result["kind"] == "geometry":
        unknown = set(expect) - {"num_vertices", "num_faces", "dimensions", "center"}
        if unknown:
            raise MCGValidationError(f"Unsupported geometry expectations: {sorted(unknown)}")
        check("mesh_created", result["num_vertices"], "> 0", result["num_vertices"] > 0)
        check("faces_created", result["num_faces"], "> 0", result["num_faces"] > 0)
        if "num_vertices" in expect:
            check("num_vertices", result["num_vertices"], int(expect["num_vertices"]), result["num_vertices"] == int(expect["num_vertices"]))
        if "num_faces" in expect:
            check("num_faces", result["num_faces"], int(expect["num_faces"]), result["num_faces"] == int(expect["num_faces"]))
        if "dimensions" in expect:
            expected_dims = [float(value) for value in expect["dimensions"]]
            passed = len(expected_dims) == 3 and all(
                abs(actual - expected) <= tolerance
                for actual, expected in zip(result["dimensions"], expected_dims)
            )
            check("dimensions", result["dimensions"], expected_dims, passed)
        if "center" in expect:
            expected_center = [float(value) for value in expect["center"]]
            actual_center = [
                (result["bounds"]["min"][index] + result["bounds"]["max"][index]) / 2.0
                for index in range(3)
            ]
            passed = all(abs(actual - expected) <= tolerance for actual, expected in zip(actual_center, expected_center))
            check("center", actual_center, expected_center, passed)
    else:
        unknown = set(expect) - {"num_vertices", "num_faces", "dimensions", "center", "changed"}
        if unknown:
            raise MCGValidationError(f"Unsupported modifier expectations: {sorted(unknown)}")
        allow_empty = bool(spec.get("allow_empty", False))
        check(
            "modifier_evaluated",
            result["output"]["num_vertices"],
            "> 0" if not allow_empty else ">= 0",
            result["output"]["num_vertices"] > 0 if not allow_empty else result["output"]["num_vertices"] >= 0,
        )
        check(
            "modifier_faces",
            result["output"]["num_faces"],
            "> 0" if not allow_empty else ">= 0",
            result["output"]["num_faces"] > 0 if not allow_empty else result["output"]["num_faces"] >= 0,
        )
        if "num_vertices" in expect:
            check("num_vertices", result["output"]["num_vertices"], int(expect["num_vertices"]), result["output"]["num_vertices"] == int(expect["num_vertices"]))
        if "num_faces" in expect:
            check("num_faces", result["output"]["num_faces"], int(expect["num_faces"]), result["output"]["num_faces"] == int(expect["num_faces"]))
        if "dimensions" in expect:
            expected_dims = [float(value) for value in expect["dimensions"]]
            passed = len(expected_dims) == 3 and all(
                abs(actual - expected) <= tolerance
                for actual, expected in zip(result["output"]["dimensions"], expected_dims)
            )
            check("dimensions", result["output"]["dimensions"], expected_dims, passed)
        if "center" in expect:
            expected_center = [float(value) for value in expect["center"]]
            passed = all(
                abs(actual - expected) <= tolerance
                for actual, expected in zip(result["output"]["center"], expected_center)
            )
            check("center", result["output"]["center"], expected_center, passed)
        if "changed" in expect:
            changed = (
                result["input"]["num_vertices"] != result["output"]["num_vertices"]
                or result["input"]["num_faces"] != result["output"]["num_faces"]
                or any(
                    abs(a - b) > tolerance
                    for a, b in zip(result["input"]["dimensions"], result["output"]["dimensions"])
                )
                or any(
                    abs(a - b) > tolerance
                    for a, b in zip(result["input"]["center"], result["output"]["center"])
                )
            )
            check("changed", changed, bool(expect["changed"]), changed == bool(expect["changed"]))

    requested_parameters = spec.get("parameters") if isinstance(spec.get("parameters"), dict) else {}
    actual_parameters = result.get("parameters") if isinstance(result.get("parameters"), dict) else {}
    for name, expected_value in requested_parameters.items():
        actual_value = actual_parameters.get(name)
        check(
            f"parameter:{name}",
            actual_value,
            expected_value,
            actual_value is not None and _parameter_matches(actual_value, expected_value, tolerance),
        )
    return {"passed": all(item["passed"] for item in checks), "checks": checks}


def _verify_path(
    path: Path,
    info: dict[str, Any],
    verification: MCGVerificationSpec | Mapping[str, Any] | None,
    *,
    generated_class: str = "",
) -> dict[str, Any]:
    spec = _verification_mapping(verification)
    parameters = spec.get("parameters") if isinstance(spec.get("parameters"), dict) else {}
    kind = _graph_terminal_type(info)
    identifier = generated_class or _generated_class_from_wrapper(path) or _graph_identifier(info, path)
    if kind == "geometry":
        script = _geometry_verify_script(identifier, parameters)
    elif kind == "modifier":
        script = _modifier_verify_script(identifier, parameters)
    else:
        raise MCGValidationError(
            f"V1 verification supports geometry and modifier graphs, not {kind or 'unknown'}"
        )
    response = client.send_command(script, timeout=120)
    result = _parse_verification(str(response.get("result", "")))
    result["generated_class"] = identifier
    result["class_matches"] = str(result.get("class", "")).casefold() == identifier.casefold()
    result["acceptance"] = _evaluate_acceptance(result, spec)
    txt_path = path.with_suffix(".txt")
    if txt_path.is_file():
        result["diagnostic_graph"] = str(txt_path)
    return result


def _compile_and_verify(
    graph_id: str,
    *,
    verify: bool,
    verification: MCGVerificationSpec | Mapping[str, Any] | None,
    allow_executable: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    path = _resolve_graph(graph_id)
    info = inspect_graph(path)
    findings = _executable_findings(info)
    context = _context_with_fallback()
    if not context.get("bridge_available"):
        raise RuntimeError(str(context.get("bridge_error") or "3ds Max bridge is unavailable"))
    max_root = Path(context["max_root"]) if context.get("max_root") else None
    dependency_findings, dependency_warnings = _dependency_security(info, max_root=max_root)
    findings.extend(dependency_findings)
    if findings and not allow_executable:
        raise MCGSecurityError(
            [
                str(finding.get("message") or finding.get("kind") or "unsafe MCG content")
                for finding in findings
            ]
        )
    if findings and context.get("safe_mode") is not False:
        raise MCGSecurityError(
            "Executable or scene-mutating MCG content cannot compile while MCP safe mode is enabled"
        )
    compile_result = _compile_path(path, timeout_seconds=timeout_seconds)
    proof: dict[str, Any] = {
        "graph_id": graph_id,
        "path": str(path),
        "hash": graph_hash(path),
        "identity": info.get("identity", {}),
        "unsafe_content": findings,
        "executable_content": findings,
        "dependency_warnings": dependency_warnings,
        "compile": compile_result,
        "secure_mode": context.get("secure_mode"),
    }
    if not compile_result["compiled"]:
        proof["verified"] = False
        return proof
    if verify:
        verification_result = _verify_path(
            path,
            info,
            verification,
            generated_class=str(compile_result.get("generated_class", "")),
        )
        proof["verification"] = verification_result
        proof["verified"] = bool(verification_result["acceptance"]["passed"])
        txt_path = path.with_suffix(".txt")
        if txt_path.is_file():
            compile_result["artifacts"]["diagnostic_graph"] = str(txt_path)
    else:
        proof["verified"] = None
    return proof


def _checkpoint_token(graph_id: str, path_value: Any) -> str:
    path = Path(str(path_value)).resolve()
    if not is_within(path, _workspace_root()):
        raise MCGSecurityError("Checkpoint escaped the MCG temp workspace")
    token = f"checkpoint_{uuid4().hex[:16]}"
    with _STATE_LOCK:
        _CHECKPOINTS[(graph_id, token)] = path
    return token


def _journal_failed_candidate(
    graph_id: str,
    path: Path,
    *,
    proof: dict[str, Any],
    operations: list[dict[str, Any]],
) -> dict[str, str]:
    journal_dir = _workspace_root() / ".journal" / graph_id
    journal_dir.mkdir(parents=True, exist_ok=True)
    iteration = uuid4().hex
    candidate_path = journal_dir / f"{iteration}.failed{path.suffix}"
    record_path = journal_dir / f"{iteration}.json"
    shutil.copy2(path, candidate_path)
    record = {
        "graph_id": graph_id,
        "candidate_hash": graph_hash(candidate_path),
        "operations": operations,
        "proof": proof,
    }
    temporary = record_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, record_path)
    return {
        "source_id": _source_id(candidate_path),
        "candidate": str(candidate_path),
        "iteration": str(record_path),
    }


def _source_summary(path: Path) -> dict[str, Any]:
    try:
        info = inspect_graph(path)
        return {
            "source_id": _source_id(path),
            "name": path.name,
            "path": str(path),
            "kind": _graph_terminal_type(info),
            "identifier": _graph_identifier(info, path),
            "display_name": str(
                info.get("display_name")
                or (info.get("metadata") or {}).get("displayName", "")
            ),
            "hash": info.get("hash") or graph_hash(path),
            "node_count": info.get("node_count", len(info.get("nodes", []))),
            "connection_count": info.get("connection_count", len(info.get("connections", []))),
        }
    except Exception as exc:
        return {
            "source_id": _source_id(path),
            "name": path.name,
            "path": str(path),
            "error": str(exc),
        }


def _node_selector(name: str, handle: int, *, label: str) -> dict[str, Any]:
    if isinstance(handle, bool) or not isinstance(handle, int) or handle < 0:
        raise ValueError(f"{label}_handle must be a non-negative integer")
    if handle:
        return {"handle": handle}
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError(f"{label}_name or {label}_handle is required")
    return {"name": normalized_name}


def _node_parameter_selectors(values: Mapping[str, str | int] | None) -> dict[str, dict[str, Any]]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise ValueError("node_parameters must map MCG parameter names to object names or handles")
    if len(values) > 32:
        raise ValueError("node_parameters is limited to 32 entries per request")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_parameter, raw_node in values.items():
        parameter = str(raw_parameter or "").strip()
        if not parameter or not _PROPERTY_RE.fullmatch(parameter):
            raise ValueError(f"Invalid MCG node parameter name: {raw_parameter!r}")
        if isinstance(raw_node, bool):
            raise ValueError(f"Node selector for {parameter!r} cannot be boolean")
        if isinstance(raw_node, int):
            if raw_node <= 0:
                raise ValueError(f"Node handle for {parameter!r} must be positive")
            normalized[parameter] = {"handle": raw_node}
        elif isinstance(raw_node, str) and raw_node.strip():
            normalized[parameter] = {"name": raw_node.strip()}
        else:
            raise ValueError(
                f"Node selector for {parameter!r} must be a non-empty object name or positive handle"
            )
    return normalized


def _native_json_result(response: Mapping[str, Any]) -> dict[str, Any]:
    raw = response.get("result")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise RuntimeError("Native MCG handler returned a non-object result")
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"Native MCG handler returned invalid JSON: {raw[:500]}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Native MCG handler returned a non-object JSON result")
    return parsed


def _native_modifier_payload(
    graph_id: str,
    *,
    allow_executable: bool,
    timeout_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = _resolve_graph(graph_id)
    info = inspect_graph(path)
    kind = _graph_terminal_type(info)
    if kind != "modifier":
        raise MCGValidationError(f"Native MCG instancing supports modifier graphs, not {kind or 'unknown'}")
    proof = _compile_and_verify(
        graph_id,
        verify=False,
        verification=None,
        allow_executable=allow_executable,
        timeout_seconds=timeout_seconds,
    )
    compile_result = proof.get("compile") if isinstance(proof.get("compile"), dict) else {}
    if not compile_result.get("compiled"):
        diagnostics = str(compile_result.get("diagnostics") or "unknown compiler failure")
        raise RuntimeError(f"MCG compilation failed: {diagnostics}")
    class_id = compile_result.get("class_id")
    if (
        not isinstance(class_id, list)
        or len(class_id) != 2
        or any(isinstance(part, bool) or not isinstance(part, int) or part < 0 for part in class_id)
    ):
        raise RuntimeError("MCG compiler did not return a valid two-part class ID")
    expected_identifier = str(
        compile_result.get("generated_class") or compile_result.get("identifier") or ""
    )
    payload = {
        "class_id": class_id,
        "graph_path": str(path),
        "expected_identifier": expected_identifier,
    }
    return proof, payload


def _native_mcg_result(
    graph_id: str,
    proof: dict[str, Any],
    native_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "graph_id": graph_id,
        "graph": {
            "path": proof.get("path", ""),
            "hash": proof.get("hash", ""),
            "identity": proof.get("identity", {}),
        },
        "compile": proof.get("compile", {}),
        "instance": native_result,
    }


@mcp.tool()
def mcg_get_context() -> dict[str, Any]:
    """Return the live MCG compiler, temp workspace, templates, and safety context."""
    return _context_with_fallback()


@mcp.tool()
def mcg_list_graphs(
    scope: str = "session",
    query: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """List session graphs or read-only installed/sample graph sources.

    Mutating tools accept only ``graph_id`` values from the session scope.
    A ``source_id`` can be passed to ``mcg_create_graph(template_id=...)`` to
    fork a read-only graph into the temp workspace.
    """
    normalized = (scope or "session").strip().lower()
    limit = max(1, min(int(limit), 500))
    needle = query.casefold().strip()
    if normalized == "session":
        rows: list[dict[str, Any]] = []
        with _STATE_LOCK:
            items = list(_GRAPHS.items())
        for graph_id, path in items:
            if not path.is_file():
                continue
            info = inspect_graph(path)
            row = {
                "graph_id": graph_id,
                "name": path.name,
                "path": str(path),
                "identifier": _graph_identifier(info, path),
                "kind": _graph_terminal_type(info),
                "hash": info.get("hash") or graph_hash(path),
                "node_count": info.get("node_count", len(info.get("nodes", []))),
                "connection_count": info.get("connection_count", len(info.get("connections", []))),
            }
            haystack = " ".join(str(value) for value in row.values()).casefold()
            if not needle or needle in haystack:
                rows.append(row)
        return {"scope": normalized, "count": len(rows), "graphs": rows[:limit]}

    roots: list[Path] = []
    if normalized in {"samples", "all"}:
        roots.extend(sample_roots())
    if normalized in {"installed", "all"}:
        context = _context_with_fallback()
        max_root = Path(context["max_root"]) if context.get("max_root") else None
        if max_root:
            roots.append(compound_root(max_root))
            for template in (context.get("templates") or {}).values():
                roots.append(Path(str(template)))
    if normalized not in {"samples", "installed", "all"}:
        raise ValueError("scope must be session, samples, installed, or all")

    rows = []
    for path in iter_graph_files(roots):
        if needle and needle not in str(path).casefold():
            continue
        rows.append(_source_summary(path))
        if len(rows) >= limit:
            break
    return {"scope": normalized, "count": len(rows), "sources": rows}


@mcp.tool()
def mcg_inspect_graph(graph_id: str) -> dict[str, Any]:
    """Inspect a session graph or read-only source as normalized graph data."""
    path = _resolve_graph(graph_id, allow_source=True)
    info = inspect_graph(path)
    info["graph_id" if graph_id.startswith("graph_") else "source_id"] = graph_id
    info["path"] = str(path)
    info["mutable"] = graph_id.startswith("graph_")
    return info


@mcp.tool()
def mcg_search_operators(
    query: str = "",
    category: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Search typed inputs plus value(0)/function(1) outputs; fall back to XML offline."""
    limit = max(1, min(int(limit), 100))
    try:
        return _live_operator_search(query, category, limit)
    except Exception as exc:
        context = _context_with_fallback()
        max_root = Path(context["max_root"]) if context.get("max_root") else None
        result = search_offline_operators(
            max_root,
            query=query,
            category=category,
            limit=limit,
        )
        result["warning"] = f"Live typed operator catalog unavailable: {exc}"
        return result


@mcp.tool()
@_serialized_mcg_transaction
def mcg_create_graph(
    kind: str,
    identifier: str,
    display_name: str = "",
    description: str = "",
    category: str = "",
    template_id: str = "",
    compile_graph: bool = True,
    verify: bool = True,
    verification: MCGVerificationSpec | None = None,
    allow_executable: bool = False,
) -> dict[str, Any]:
    """Fork an Autodesk/source template into temp storage and optionally compile/test it."""
    normalized_kind = (kind or "").strip().lower()
    if normalized_kind == "object":
        normalized_kind = "geometry"
    if normalized_kind not in {"geometry", "modifier"}:
        raise ValueError("kind must be geometry or modifier")
    if not _IDENTIFIER_RE.fullmatch(identifier or ""):
        raise ValueError("identifier must be a MAXScript-safe symbol: letters, digits, underscore")
    identifier_key = identifier.casefold()
    with _STATE_LOCK:
        if identifier_key in _USED_IDENTIFIERS:
            raise ValueError(f"identifier already used in this MCG session: {identifier}")

    context = _context_with_fallback()
    if context.get("bridge_available"):
        try:
            symbol_exists = _class_is_available(identifier)
        except Exception:
            symbol_exists = False
        if symbol_exists:
            raise ValueError(
                f"identifier already resolves to a class or symbol in 3ds Max: {identifier}"
            )
    if template_id:
        template = _resolve_graph(template_id, allow_source=True)
        if not template_id.startswith("source_"):
            raise MCGSecurityError("template_id must name a read-only source")
    else:
        max_root = Path(context["max_root"]) if context.get("max_root") else None
        if max_root is None:
            raise FileNotFoundError("No installed 3ds Max MCG templates were found")
        template = find_tool_template(max_root, normalized_kind)

    template_info = inspect_graph(template)
    template_kind = _graph_terminal_type(template_info)
    if template.suffix.lower() != ".maxtool" or template_kind != normalized_kind:
        raise MCGValidationError(
            f"Template must be a {normalized_kind} .maxtool graph, not "
            f"{template.suffix or '<no extension>'} {template_kind or 'unknown'}"
        )

    destination = _workspace_root() / f"{identifier}.maxtool"
    proof = create_graph_from_template(
        template,
        destination,
        identifier,
        display_name or identifier,
        description=description,
        category=category,
    )
    graph_id = _register_graph(destination)
    with _STATE_LOCK:
        _USED_IDENTIFIERS.add(identifier_key)
    result: dict[str, Any] = {
        "graph_id": graph_id,
        "template": str(template),
        "temporary": True,
        "creation": proof,
        "graph": inspect_graph(destination),
    }
    if not compile_graph:
        return result
    try:
        live_proof = _compile_and_verify(
            graph_id,
            verify=verify,
            verification=verification,
            allow_executable=allow_executable,
            timeout_seconds=120,
        )
    except Exception as exc:
        return _error(
            str(exc),
            error_type=exc.__class__.__name__,
            code=_exception_code(exc),
            retryable=_exception_code(exc) == "BRIDGE_DOWN",
            details=result,
        )
    result["proof"] = live_proof
    if not live_proof["compile"]["compiled"]:
        return _error(
            "MCG graph was created but compilation failed",
            error_type="MCGCompileError",
            details=result,
        )
    if verify and not live_proof.get("verified"):
        return _error(
            "MCG graph compiled but its acceptance test failed",
            error_type="MCGVerificationError",
            details=result,
        )
    return result


@mcp.tool()
@_serialized_mcg_transaction
def mcg_compile_graph(
    graph_id: str,
    verify: bool = True,
    verification: MCGVerificationSpec | None = None,
    allow_executable: bool = False,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Validate, compile, and optionally test one temp MCG graph."""
    try:
        proof = _compile_and_verify(
            graph_id,
            verify=verify,
            verification=verification,
            allow_executable=allow_executable,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return _error(
            str(exc),
            error_type=exc.__class__.__name__,
            code=_exception_code(exc),
            retryable=_exception_code(exc) == "BRIDGE_DOWN",
            details={"graph_id": graph_id},
        )
    if not proof["compile"]["compiled"]:
        return _error("MCG compilation failed", error_type="MCGCompileError", details=proof)
    if verify and not proof.get("verified"):
        return _error("MCG verification failed", error_type="MCGVerificationError", details=proof)
    return proof


@mcp.tool()
@_serialized_mcg_transaction
def mcg_resolve_class(
    graph_id: str,
    allow_executable: bool = False,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Compile a temp modifier graph and resolve its exact native class descriptor."""
    try:
        proof, payload = _native_modifier_payload(
            graph_id,
            allow_executable=allow_executable,
            timeout_seconds=timeout_seconds,
        )
        response = client.send_command(
            json.dumps(payload),
            cmd_type="native:mcg_resolve_class",
            timeout=max(10, min(int(timeout_seconds), 600)),
        )
        return _native_mcg_result(graph_id, proof, _native_json_result(response))
    except Exception as exc:
        code = _exception_code(exc)
        return _error(
            str(exc),
            error_type=exc.__class__.__name__,
            code=code,
            retryable=code == "BRIDGE_DOWN",
            details={"graph_id": graph_id},
        )


@mcp.tool()
@_serialized_mcg_transaction
def mcg_apply_modifier(
    graph_id: str,
    target_name: str = "",
    target_handle: int = 0,
    node_parameters: dict[str, str | int] | None = None,
    allow_executable: bool = False,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Compile and safely apply a temp MCG modifier using typed node references.

    ``node_parameters`` maps exact exposed PB2 names to scene object names or,
    preferably, stable node handles. Only scalar node-reference parameters are
    accepted by the native bridge.
    """
    try:
        target = _node_selector(target_name, target_handle, label="target")
        parameters = _node_parameter_selectors(node_parameters)
        proof, payload = _native_modifier_payload(
            graph_id,
            allow_executable=allow_executable,
            timeout_seconds=timeout_seconds,
        )
        payload.update(target)
        payload["node_parameters"] = parameters
        response = client.send_command(
            json.dumps(payload),
            cmd_type="native:mcg_apply_modifier",
            timeout=max(10, min(int(timeout_seconds), 600)),
        )
        return _native_mcg_result(graph_id, proof, _native_json_result(response))
    except Exception as exc:
        code = _exception_code(exc)
        return _error(
            str(exc),
            error_type=exc.__class__.__name__,
            code=code,
            retryable=code == "BRIDGE_DOWN",
            details={"graph_id": graph_id, "target_name": target_name, "target_handle": target_handle},
        )


@mcp.tool()
@_serialized_mcg_transaction
def mcg_set_node_parameter(
    graph_id: str,
    modifier_index: int,
    parameter: str,
    target_name: str = "",
    target_handle: int = 0,
    source_name: str = "",
    source_handle: int = 0,
    allow_executable: bool = False,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Safely retarget one scalar node parameter on a compiled MCG modifier."""
    try:
        if isinstance(modifier_index, bool) or modifier_index <= 0:
            raise ValueError("modifier_index must be a positive 1-based index")
        normalized_parameter = str(parameter or "").strip()
        if not normalized_parameter or not _PROPERTY_RE.fullmatch(normalized_parameter):
            raise ValueError("parameter must be an exact PB2 parameter name")
        target = _node_selector(target_name, target_handle, label="target")
        source = _node_selector(source_name, source_handle, label="source")
        proof, payload = _native_modifier_payload(
            graph_id,
            allow_executable=allow_executable,
            timeout_seconds=timeout_seconds,
        )
        payload.update(target)
        payload.update(
            {
                "modifier_index": modifier_index,
                "parameter": normalized_parameter,
                "source": source,
            }
        )
        response = client.send_command(
            json.dumps(payload),
            cmd_type="native:mcg_set_node_parameter",
            timeout=max(10, min(int(timeout_seconds), 600)),
        )
        return _native_mcg_result(graph_id, proof, _native_json_result(response))
    except Exception as exc:
        code = _exception_code(exc)
        return _error(
            str(exc),
            error_type=exc.__class__.__name__,
            code=code,
            retryable=code == "BRIDGE_DOWN",
            details={
                "graph_id": graph_id,
                "target_name": target_name,
                "target_handle": target_handle,
                "modifier_index": modifier_index,
                "parameter": parameter,
            },
        )


@mcp.tool()
@_serialized_mcg_transaction
def mcg_inspect_instance(
    graph_id: str,
    modifier_index: int = 1,
    target_name: str = "",
    target_handle: int = 0,
    allow_executable: bool = False,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Inspect one MCG modifier after proving its class ID and graph identity."""
    try:
        if isinstance(modifier_index, bool) or modifier_index <= 0:
            raise ValueError("modifier_index must be a positive 1-based index")
        target = _node_selector(target_name, target_handle, label="target")
        proof, payload = _native_modifier_payload(
            graph_id,
            allow_executable=allow_executable,
            timeout_seconds=timeout_seconds,
        )
        payload.update(target)
        payload["modifier_index"] = modifier_index
        response = client.send_command(
            json.dumps(payload),
            cmd_type="native:mcg_inspect_instance",
            timeout=max(10, min(int(timeout_seconds), 600)),
        )
        return _native_mcg_result(graph_id, proof, _native_json_result(response))
    except Exception as exc:
        code = _exception_code(exc)
        return _error(
            str(exc),
            error_type=exc.__class__.__name__,
            code=code,
            retryable=code == "BRIDGE_DOWN",
            details={
                "graph_id": graph_id,
                "target_name": target_name,
                "target_handle": target_handle,
                "modifier_index": modifier_index,
            },
        )


@mcp.tool()
@_serialized_mcg_transaction
def mcg_test_tool(
    graph_id: str,
    verification: MCGVerificationSpec | None = None,
    allow_executable: bool = False,
) -> dict[str, Any]:
    """Compile the current hash, then create/inspect/delete one disposable instance."""
    try:
        proof = _compile_and_verify(
            graph_id,
            verify=True,
            verification=verification,
            allow_executable=allow_executable,
            timeout_seconds=120,
        )
    except Exception as exc:
        return _error(
            str(exc),
            error_type=exc.__class__.__name__,
            code=_exception_code(exc),
            retryable=_exception_code(exc) == "BRIDGE_DOWN",
            details={"graph_id": graph_id},
        )
    if not proof["compile"]["compiled"]:
        return _error("MCG compilation failed", error_type="MCGCompileError", details=proof)
    result = proof.get("verification", {})
    if not result["acceptance"]["passed"]:
        return _error(
            "MCG acceptance test failed",
            error_type="MCGVerificationError",
            details=proof,
        )
    return proof


@mcp.tool()
@_serialized_mcg_transaction
def mcg_apply_patch(
    graph_id: str,
    expected_hash: str,
    operations: list[MCGPatchOperation],
    dry_run: bool = False,
    compile_graph: bool = True,
    verify: bool = True,
    verification: MCGVerificationSpec | None = None,
    rollback_on_failure: bool = True,
    allow_executable: bool = False,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Patch a temp graph transactionally, then compile/test and roll back on failure.

    Port names are accepted for ``connect.dest_port`` and resolved against the
    live typed operator depot before writing numeric MCG slot indices.
    """
    path = _resolve_graph(graph_id)
    before_info = inspect_graph(path)
    try:
        normalized_ops = _normalize_patch_ports(before_info, list(operations))
        checkpoint_dir = _workspace_root() / ".checkpoints" / graph_id
        patch_proof = patch_graph(
            path,
            expected_hash,
            normalized_ops,
            dry_run=dry_run,
            allow_executable=allow_executable,
            checkpoint_dir=checkpoint_dir,
        )
    except MCGHashConflict as exc:
        return _error(
            str(exc),
            error_type="MCGHashConflict",
            code="AMBIGUOUS",
            details={"graph_id": graph_id, "current_hash": graph_hash(path)},
            hint={"message": "The graph changed since inspection. Re-inspect and rebuild the patch."},
        )
    except (MCGGraphError, MCGValidationError, MCGSecurityError, ValueError) as exc:
        return _error(
            str(exc),
            error_type=exc.__class__.__name__,
            details={"graph_id": graph_id, "current_hash": graph_hash(path)},
        )

    result: dict[str, Any] = {
        "graph_id": graph_id,
        "operations": normalized_ops,
        "patch": patch_proof,
    }
    checkpoint_path = patch_proof.get("checkpoint_path") or patch_proof.get("checkpoint")
    checkpoint_token = ""
    if checkpoint_path:
        checkpoint_token = _checkpoint_token(graph_id, checkpoint_path)
        result["checkpoint_token"] = checkpoint_token
    if dry_run or not compile_graph:
        return result

    try:
        live_proof = _compile_and_verify(
            graph_id,
            verify=verify,
            verification=verification,
            allow_executable=allow_executable,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        live_proof = {
            "graph_id": graph_id,
            "compile": {"compiled": False, "diagnostics": str(exc)},
            "verified": False,
        }
    result["proof"] = live_proof
    succeeded = bool(live_proof.get("compile", {}).get("compiled")) and (
        not verify or bool(live_proof.get("verified"))
    )
    if succeeded:
        return result

    try:
        result["failed_candidate"] = _journal_failed_candidate(
            graph_id,
            path,
            proof=live_proof,
            operations=normalized_ops,
        )
    except Exception as exc:
        result["failed_candidate"] = {"error": str(exc)}
    if rollback_on_failure and checkpoint_token:
        checkpoint = _CHECKPOINTS[(graph_id, checkpoint_token)]
        try:
            restored = restore_checkpoint(
                path,
                checkpoint,
                expected_hash=str(patch_proof["after_hash"]),
                allow_executable=allow_executable,
            )
            result["rollback"] = restored
            try:
                result["rollback_compile"] = _compile_and_verify(
                    graph_id,
                    verify=False,
                    verification=None,
                    allow_executable=allow_executable,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as exc:
                result["rollback_compile"] = {"compiled": False, "error": str(exc)}
        except MCGHashConflict as exc:
            result["rollback_conflict"] = {
                "expected_hash": exc.expected,
                "current_hash": exc.actual,
                "message": "Rollback was skipped because the candidate changed after this iteration.",
            }
    return _error(
        "MCG patch did not pass compile/verification and was rolled back"
        if result.get("rollback")
        else "MCG patch did not pass compile/verification",
        error_type="MCGIterationError",
        details=result,
        hint={
            "message": "Use the compiler diagnostics and failed candidate journal to produce the next structured patch.",
            "suggested_tools": ["mcg_inspect_graph", "mcg_search_operators", "mcg_apply_patch"],
            "next": {
                "graph_id": graph_id,
                "expected_hash": graph_hash(path),
                "iteration_limit_recommendation": 8,
            },
        },
    )


@mcp.tool()
@_serialized_mcg_transaction
def mcg_restore_checkpoint(
    graph_id: str,
    checkpoint_token: str,
    expected_hash: str,
    compile_graph: bool = True,
    verify: bool = True,
    verification: MCGVerificationSpec | None = None,
    allow_executable: bool = False,
) -> dict[str, Any]:
    """Transactionally restore an opaque checkpoint, then optionally compile/test it."""
    path = _resolve_graph(graph_id)
    current_hash = graph_hash(path)
    normalized_expected = str(expected_hash or "").strip().lower()
    if current_hash != normalized_expected:
        return _error(
            "MCG graph changed since the checkpoint restore was requested",
            error_type="MCGHashConflict",
            code="AMBIGUOUS",
            details={"graph_id": graph_id, "current_hash": current_hash},
            hint={"message": "Re-inspect the graph and retry with its current hash."},
        )
    with _STATE_LOCK:
        checkpoint = _CHECKPOINTS.get((graph_id, checkpoint_token))
    if checkpoint is None or not checkpoint.is_file():
        raise ValueError("Unknown or expired MCG checkpoint token")
    checkpoint_dir = _workspace_root() / ".checkpoints" / graph_id
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    safety_path = checkpoint_dir / f"pre_restore.{uuid4().hex}.checkpoint{path.suffix}"
    shutil.copy2(path, safety_path)
    try:
        restored = restore_checkpoint(
            path,
            checkpoint,
            expected_hash=normalized_expected,
            allow_executable=allow_executable,
        )
    except MCGHashConflict as exc:
        safety_path.unlink(missing_ok=True)
        return _error(
            str(exc),
            error_type="MCGHashConflict",
            code="AMBIGUOUS",
            details={"graph_id": graph_id, "current_hash": exc.actual},
            hint={"message": "The graph changed during restore. Re-inspect before retrying."},
        )
    safety_token = _checkpoint_token(graph_id, safety_path)
    result: dict[str, Any] = {
        "graph_id": graph_id,
        "restore": restored,
        "safety_checkpoint_token": safety_token,
    }
    if compile_graph:
        try:
            proof = _compile_and_verify(
                graph_id,
                verify=verify,
                verification=verification,
                allow_executable=allow_executable,
                timeout_seconds=120,
            )
        except Exception as exc:
            proof = {
                "graph_id": graph_id,
                "compile": {"compiled": False, "diagnostics": str(exc)},
                "verified": False,
            }
        result["proof"] = proof
        if not proof["compile"]["compiled"] or (verify and not proof.get("verified")):
            result["failed_restore"] = inspect_graph(path)
            try:
                result["rollback"] = restore_checkpoint(
                    path,
                    safety_path,
                    expected_hash=str(restored["after_hash"]),
                    allow_executable=allow_executable,
                )
                try:
                    result["rollback_compile"] = _compile_and_verify(
                        graph_id,
                        verify=False,
                        verification=None,
                        allow_executable=allow_executable,
                        timeout_seconds=120,
                    )
                except Exception as exc:
                    result["rollback_compile"] = {"compiled": False, "error": str(exc)}
            except MCGHashConflict as exc:
                result["rollback_conflict"] = {
                    "expected_hash": exc.expected,
                    "current_hash": exc.actual,
                    "message": "Restore rollback was skipped because the graph changed.",
                }
            return _error("Restored checkpoint did not compile/verify", error_type="MCGRestoreError", details=result)
    return result


@mcp.tool()
@_serialized_mcg_transaction
def mcg_reload_operators() -> dict[str, Any]:
    """Explicitly refresh the global MCG depot; not used by the normal per-graph loop."""
    context = _context_with_fallback()
    if not context.get("bridge_available"):
        return _error(
            str(context.get("bridge_error") or "3ds Max bridge is unavailable"),
            error_type="MCGReloadError",
            code="BRIDGE_DOWN",
            retryable=True,
        )
    if context.get("safe_mode") is not False:
        return _error(
            "Global MCG operator reload is blocked while MCP safe mode is enabled",
            error_type="MCGSecurityError",
            code="SAFE_MODE",
        )
    script = '''(
try (
    local bridge = dotNetClass "Viper3dsMaxBridge.Main"
    bridge.ReloadOperators()
    "Operators reloaded."
) catch ("__ERROR__|" + (getCurrentException() as string))
)'''
    response = client.send_command(script, timeout=120)
    raw = str(response.get("result", ""))
    if raw.startswith("__ERROR__|"):
        return _error(raw.split("|", 1)[1], error_type="MCGReloadError")
    with _STATE_LOCK:
        _OPERATOR_CACHE.clear()
    return {
        "reloaded": True,
        "diagnostics": raw.strip(),
        "warning": "This is a global MCG operation; the normal temp graph loop does not require it.",
    }


@mcp.tool()
@_serialized_mcg_transaction
def mcg_cleanup_workspace(graph_id: str = "") -> dict[str, Any]:
    """Delete one temp graph family or the entire process-scoped MCG workspace."""
    global _WORKSPACE_TEMP
    if graph_id:
        path = _resolve_graph(graph_id)
        root = _workspace_root()
        removed: list[str] = []
        for candidate in (path, path.with_suffix(".ms"), path.with_suffix(".txt")):
            if candidate.is_file() and is_within(candidate, root):
                candidate.unlink()
                removed.append(str(candidate))
        for directory in (root / ".checkpoints" / graph_id, root / ".journal" / graph_id):
            if directory.is_dir() and is_within(directory, root):
                shutil.rmtree(directory)
                removed.append(str(directory))
        with _STATE_LOCK:
            _GRAPHS.pop(graph_id, None)
            _PATH_TO_GRAPH.pop(_path_key(path), None)
            for key in [key for key in _CHECKPOINTS if key[0] == graph_id]:
                _CHECKPOINTS.pop(key, None)
        return {
            "graph_id": graph_id,
            "removed": removed,
            "warning": "The compiled scripted class remains registered until 3ds Max exits.",
        }

    with _STATE_LOCK:
        workspace = str(_workspace_root())
        if _WORKSPACE_TEMP is not None:
            _WORKSPACE_TEMP.cleanup()
        _WORKSPACE_TEMP = None
        _GRAPHS.clear()
        _PATH_TO_GRAPH.clear()
        _SOURCES.clear()
        _CHECKPOINTS.clear()
        _OPERATOR_CACHE.clear()
    return {
        "removed_workspace": workspace,
        "warning": "Compiled scripted classes remain registered until 3ds Max exits.",
    }


def _reset_mcg_state_for_tests() -> None:
    """Private test hook; never registered as an MCP tool."""
    global _WORKSPACE_TEMP
    with _STATE_LOCK:
        if _WORKSPACE_TEMP is not None:
            _WORKSPACE_TEMP.cleanup()
        _WORKSPACE_TEMP = None
        _GRAPHS.clear()
        _PATH_TO_GRAPH.clear()
        _SOURCES.clear()
        _CHECKPOINTS.clear()
        _USED_IDENTIFIERS.clear()
        _OPERATOR_CACHE.clear()


__all__ = [
    "mcg_apply_modifier",
    "mcg_apply_patch",
    "mcg_cleanup_workspace",
    "mcg_compile_graph",
    "mcg_create_graph",
    "mcg_get_context",
    "mcg_inspect_graph",
    "mcg_inspect_instance",
    "mcg_list_graphs",
    "mcg_reload_operators",
    "mcg_resolve_class",
    "mcg_restore_checkpoint",
    "mcg_search_operators",
    "mcg_set_node_parameter",
    "mcg_test_tool",
]
