"""Pure XML helpers for temporary Max Creation Graph workspaces.

The public functions in this module deliberately know nothing about the MCP
server or a live 3ds Max process.  They operate on ``.maxtool`` and
``.maxcompound`` XML, enforce graph invariants, and use optimistic hashes plus
atomic replacement for mutations.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


Pathish = str | os.PathLike[str]


class MCGGraphError(ValueError):
    """Base error for malformed graph inputs and unsupported operations."""


class MCGHashConflict(MCGGraphError):
    """Raised when optimistic concurrency detects a stale graph hash."""

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"MCG graph hash mismatch: expected {expected or '<empty>'}, actual {actual}"
        )


class MCGValidationError(MCGGraphError):
    """Raised when a graph violates structural MCG invariants."""

    def __init__(self, errors: str | list[str] | tuple[str, ...]) -> None:
        values = [errors] if isinstance(errors, str) else errors
        self.errors = tuple(str(error) for error in values)
        detail = "; ".join(self.errors) if self.errors else "unknown validation error"
        super().__init__(f"Invalid MCG graph: {detail}")


class MCGSecurityError(MCGGraphError):
    """Raised when a graph contains executable MCG content."""

    def __init__(self, reasons: str | list[str] | tuple[str, ...]) -> None:
        values = [reasons] if isinstance(reasons, str) else reasons
        self.reasons = tuple(str(reason) for reason in values)
        detail = "; ".join(self.reasons) if self.reasons else "executable graph content"
        super().__init__(f"Executable MCG content is blocked: {detail}")


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}
_ID_RE = re.compile(r"^\d+$")
_TAG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_OUTPUT_RE = re.compile(r"^output(?:\s*:|$)", re.IGNORECASE)
_META_ALIASES = {
    "display_name": "displayName",
    "displayname": "displayName",
    "graph_editor_ui": "graph_editor_UI",
    "grapheditorui": "graph_editor_UI",
}
_PROTECTED_META_KEYS = {
    "guid",
    "uuid",
    "graphuuid",
    "graphversion",
    "graphversionguid",
    "graphversionnumber",
    "version",
}


def _path(path: Pathish) -> Path:
    return Path(os.fspath(path)).expanduser()


def _lock_for(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def is_within(path: Pathish, root: Pathish) -> bool:
    """Return whether *path* resolves to *root* or one of its descendants.

    ``resolve(strict=False)`` resolves existing symlink/reparse-point parents
    while still accepting a not-yet-created destination.  ``commonpath`` also
    avoids sibling-prefix mistakes such as ``C:\\tmp\\mcg-evil``.
    """

    try:
        candidate = os.path.normcase(str(_path(path).resolve(strict=False)))
        base = os.path.normcase(str(_path(root).resolve(strict=False)))
        return os.path.commonpath((candidate, base)) == base
    except (OSError, RuntimeError, ValueError):
        return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise MCGGraphError(f"Could not read MCG graph {path}: {exc}") from exc


def graph_hash(path: Pathish) -> str:
    """Return the SHA-256 hash of a graph's exact on-disk bytes."""

    return _sha256(_read_bytes(_path(path)))


def _xml_parser() -> ET.XMLParser:
    return ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _qualified_tag(parent: ET.Element, local_name: str) -> str:
    tag = parent.tag if isinstance(parent.tag, str) else ""
    if tag.startswith("{") and "}" in tag:
        return f"{tag.split('}', 1)[0]}}}{local_name}"
    return local_name


def _children(parent: ET.Element | None, local_name: str) -> list[ET.Element]:
    if parent is None:
        return []
    wanted = local_name.casefold()
    return [
        child
        for child in list(parent)
        if _local_name(child.tag).casefold() == wanted
    ]


def _child(parent: ET.Element | None, local_name: str) -> ET.Element | None:
    matches = _children(parent, local_name)
    return matches[0] if matches else None


def _parse_document(path: Path) -> tuple[ET.ElementTree, bytes, bool]:
    raw = _read_bytes(path)
    try:
        root = ET.fromstring(raw, parser=_xml_parser())
    except ET.ParseError as exc:
        raise MCGGraphError(f"Could not parse MCG XML {path}: {exc}") from exc
    if _local_name(root.tag).casefold() != "graph":
        raise MCGValidationError(["root element must be <graph>"])
    has_declaration = raw.lstrip().startswith(b"<?xml")
    return ET.ElementTree(root), raw, has_declaration


def _serialize(tree: ET.ElementTree, *, xml_declaration: bool) -> bytes:
    # MCG is whitespace-insensitive.  A stable two-space layout gives hashes
    # predictable meaning after any structured edit.
    ET.indent(tree, space="  ")
    stream = io.BytesIO()
    tree.write(
        stream,
        encoding="utf-8",
        xml_declaration=xml_declaration,
        short_empty_elements=True,
    )
    data = stream.getvalue()
    return data if data.endswith(b"\n") else data + b"\n"


def _validate_serialized(data: bytes, *, allow_executable: bool) -> None:
    """Prove the exact bytes we will write remain parseable, valid MCG XML."""

    try:
        root = ET.fromstring(data, parser=_xml_parser())
    except ET.ParseError as exc:
        raise MCGValidationError([f"serialized graph is not valid XML: {exc}"]) from exc
    _validate(root, allow_executable=allow_executable)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            try:
                os.chmod(temporary, path.stat().st_mode)
            except OSError:
                pass
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _sections(
    root: ET.Element,
) -> tuple[ET.Element | None, ET.Element | None, ET.Element | None]:
    return _child(root, "meta_info"), _child(root, "nodes"), _child(root, "connections")


def _node_elements(root: ET.Element) -> list[ET.Element]:
    return _children(_child(root, "nodes"), "node")


def _connection_elements(root: ET.Element) -> list[ET.Element]:
    return _children(_child(root, "connections"), "connection")


def _normalized_id(value: object) -> str:
    text = str(value).strip()
    if not _ID_RE.fullmatch(text):
        raise MCGGraphError(f"Node id must be a non-negative integer, got {value!r}")
    return str(int(text))


def _group_members(node: ET.Element) -> list[str]:
    members = _child(node, "nodes")
    if members is None or not (members.text or "").strip():
        return []
    return [part for part in re.split(r"[,\s]+", members.text or "") if part]


def _is_group_node(node: ET.Element) -> bool:
    return "groupnode" in node.attrib


def _is_output_operator(operator: str) -> bool:
    return bool(_OUTPUT_RE.match(operator.strip()))


def _executable_content(root: ET.Element) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for node in _node_elements(root):
        operator = node.get("operator", "")
        compact = re.sub(r"[\s_-]+", "", operator).casefold()
        if "evalmaxscript" in compact:
            message = (
                f"node {node.get('id', '?')} uses executable operator "
                f"{operator or 'EvalMAXScript'}"
            )
            findings.append(
                {
                    "kind": "operator",
                    "node_id": node.get("id", ""),
                    "operator": operator or "EvalMAXScript",
                    "message": message,
                }
            )

    meta_info = _child(root, "meta_info")
    for custom_ui in _children(meta_info, "customui"):
        has_text = any((text or "").strip() for text in custom_ui.itertext())
        has_structure = bool(custom_ui.attrib) or bool(list(custom_ui))
        if has_text or has_structure:
            findings.append(
                {
                    "kind": "customui",
                    "path": "meta_info.customui",
                    "message": "meta_info.customui contains executable UI content",
                }
            )
            break
    return findings


def _executable_reasons(root: ET.Element) -> list[str]:
    return [str(finding["message"]) for finding in _executable_content(root)]


def _validation_errors(root: ET.Element) -> list[str]:
    errors: list[str] = []
    meta_info, nodes_parent, connections_parent = _sections(root)
    if meta_info is None:
        errors.append("missing <meta_info>")
    if nodes_parent is None:
        errors.append("missing <nodes>")
    if connections_parent is None:
        errors.append("missing <connections>")

    graph_uuid = (root.get("uuid") or "").strip()
    if not graph_uuid:
        errors.append("graph uuid is missing")
    graph_version = _child(meta_info, "graph_version")
    if graph_version is None:
        errors.append("missing <graph_version>")
    else:
        if not (graph_version.get("guid") or "").strip():
            errors.append("graph_version guid is missing")
        if not (graph_version.get("number") or "").strip():
            errors.append("graph_version number is missing")

    nodes = _node_elements(root)
    node_by_id: dict[str, ET.Element] = {}
    raw_to_normalized: dict[str, str] = {}
    for index, node in enumerate(nodes):
        raw_id = (node.get("id") or "").strip()
        if not raw_id:
            errors.append(f"node at index {index} has no id")
            continue
        if not _ID_RE.fullmatch(raw_id):
            errors.append(f"node id {raw_id!r} is not a non-negative integer")
            continue
        normalized = str(int(raw_id))
        raw_to_normalized[raw_id] = normalized
        if normalized in node_by_id:
            errors.append(f"duplicate node id {raw_id}")
        else:
            node_by_id[normalized] = node

        has_operator = bool((node.get("operator") or "").strip())
        has_group = _is_group_node(node)
        if has_operator == has_group:
            errors.append(
                f"node {raw_id} must define exactly one of operator or groupnode"
            )

    outputs = [
        node
        for node in nodes
        if not _is_group_node(node) and _is_output_operator(node.get("operator", ""))
    ]
    if len(outputs) != 1:
        errors.append(f"graph must contain exactly one terminal Output node, found {len(outputs)}")
    output_ids = {
        str(int(node.get("id", "0")))
        for node in outputs
        if _ID_RE.fullmatch((node.get("id") or "").strip())
    }

    group_ids = {
        str(int(node.get("id", "0")))
        for node in nodes
        if _is_group_node(node) and _ID_RE.fullmatch((node.get("id") or "").strip())
    }
    membership_owner: dict[str, str] = {}
    group_edges: dict[str, list[str]] = {group_id: [] for group_id in group_ids}
    for group in [node for node in nodes if _is_group_node(node)]:
        raw_group_id = (group.get("id") or "").strip()
        if not _ID_RE.fullmatch(raw_group_id):
            continue
        group_id = str(int(raw_group_id))
        seen_members: set[str] = set()
        for raw_member in _group_members(group):
            if not _ID_RE.fullmatch(raw_member):
                errors.append(f"group {raw_group_id} has invalid member id {raw_member!r}")
                continue
            member = str(int(raw_member))
            if member in seen_members:
                errors.append(f"group {raw_group_id} contains duplicate member {raw_member}")
                continue
            seen_members.add(member)
            if member == group_id:
                errors.append(f"group {raw_group_id} cannot contain itself")
            if member not in node_by_id:
                errors.append(f"group {raw_group_id} references missing node {raw_member}")
                continue
            previous = membership_owner.get(member)
            if previous is not None and previous != group_id:
                errors.append(
                    f"node {raw_member} belongs to multiple groups: {previous} and {raw_group_id}"
                )
            else:
                membership_owner[member] = group_id
            if member in group_ids:
                group_edges[group_id].append(member)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit_group(group_id: str) -> None:
        if group_id in visiting:
            errors.append(f"group membership cycle includes group {group_id}")
            return
        if group_id in visited:
            return
        visiting.add(group_id)
        for child_group in group_edges.get(group_id, []):
            visit_group(child_group)
        visiting.remove(group_id)
        visited.add(group_id)

    for group_id in group_ids:
        visit_group(group_id)

    destination_ports: set[tuple[str, str]] = set()
    incoming_sources: dict[str, list[str]] = {}
    for index, connection in enumerate(_connection_elements(root)):
        raw_source = (connection.get("sourcenode") or "").strip()
        raw_destination = (connection.get("destnode") or "").strip()
        source_port = (connection.get("sourceport") or "").strip()
        destination_port = (connection.get("destport") or "").strip()
        label = f"connection {index}"
        if not _ID_RE.fullmatch(raw_source):
            errors.append(f"{label} has invalid source node {raw_source!r}")
            source = ""
        else:
            source = str(int(raw_source))
        if not _ID_RE.fullmatch(raw_destination):
            errors.append(f"{label} has invalid destination node {raw_destination!r}")
            destination = ""
        else:
            destination = str(int(raw_destination))
        if not _ID_RE.fullmatch(source_port):
            errors.append(f"{label} has invalid source port {source_port!r}")
        elif int(source_port) not in {0, 1}:
            errors.append(f"{label} source port must be 0 or 1, found {source_port}")
        if not _ID_RE.fullmatch(destination_port):
            errors.append(f"{label} has invalid destination port {destination_port!r}")

        if source and source not in node_by_id:
            errors.append(f"{label} references missing source node {raw_source}")
        if destination and destination not in node_by_id:
            errors.append(f"{label} references missing destination node {raw_destination}")
        if source in group_ids:
            errors.append(f"{label} uses group node {raw_source} as a source")
        if destination in group_ids:
            errors.append(f"{label} uses group node {raw_destination} as a destination")

        if destination and _ID_RE.fullmatch(destination_port):
            destination_key = (destination, str(int(destination_port)))
            if destination_key in destination_ports:
                errors.append(
                    f"duplicate destination port {raw_destination}:{destination_port}"
                )
            else:
                destination_ports.add(destination_key)
            if destination in output_ids and int(destination_port) != 0:
                errors.append(
                    f"terminal Output node {raw_destination} must use destination port 0, "
                    f"found {destination_port}"
                )
        if source and destination:
            incoming_sources.setdefault(destination, []).append(source)

    if len(outputs) == 1:
        raw_terminal_id = (outputs[0].get("id") or "").strip()
        if _ID_RE.fullmatch(raw_terminal_id):
            terminal_id = str(int(raw_terminal_id))
            terminal_inputs = incoming_sources.get(terminal_id, [])
            if len(terminal_inputs) != 1:
                errors.append(
                    f"terminal Output node {terminal_id} must have exactly one input connection, "
                    f"found {len(terminal_inputs)}"
                )
            reachable: set[str] = set()
            pending = [terminal_id]
            while pending:
                current = pending.pop()
                if current in reachable:
                    continue
                reachable.add(current)
                pending.extend(incoming_sources.get(current, []))
            for node_id, node in node_by_id.items():
                operator = (node.get("operator") or "").strip()
                if operator.startswith(("Parameter:", "Input:")) and node_id not in reachable:
                    errors.append(
                        f"exposed input node {node_id} ({operator}) is not connected to the terminal"
                    )

    # Keep reports deterministic while suppressing duplicate cycle messages.
    return list(dict.fromkeys(errors))


def _validate(root: ET.Element, *, allow_executable: bool) -> None:
    errors = _validation_errors(root)
    if errors:
        raise MCGValidationError(errors)
    executable = _executable_reasons(root)
    if executable and not allow_executable:
        raise MCGSecurityError(executable)


def _meta_text(meta_info: ET.Element | None, local_name: str) -> str:
    element = _child(meta_info, local_name)
    return "" if element is None else (element.text or "").strip()


def _normalized_node(node: ET.Element) -> dict[str, Any]:
    attributes = dict(node.attrib)
    node_id = attributes.pop("id", "")
    operator = attributes.pop("operator", "")
    group_name = attributes.pop("groupnode", "")
    payload: dict[str, Any] = {
        "id": node_id,
        "kind": "group" if group_name or _is_group_node(node) else "operator",
        "operator": operator,
        "name": attributes.get("name", ""),
        "attributes": attributes,
    }
    if _is_group_node(node):
        payload["group_name"] = group_name
        payload["members"] = _group_members(node)
    return payload


def _normalized_connection(connection: ET.Element) -> dict[str, Any]:
    known = {"sourcenode", "sourceport", "destnode", "destport"}
    return {
        "source_node": connection.get("sourcenode", ""),
        "source_port": connection.get("sourceport", ""),
        "dest_node": connection.get("destnode", ""),
        "dest_port": connection.get("destport", ""),
        "attributes": {
            key: value for key, value in connection.attrib.items() if key not in known
        },
    }


def _inspect_tree(root: ET.Element, *, path: Path, digest: str) -> dict[str, Any]:
    meta_info, _, _ = _sections(root)
    graph_version = _child(meta_info, "graph_version")
    nodes = [_normalized_node(node) for node in _node_elements(root)]
    connections = [
        _normalized_connection(connection) for connection in _connection_elements(root)
    ]
    output_nodes = [
        node
        for node in nodes
        if node["kind"] == "operator" and _is_output_operator(node["operator"])
    ]
    output = output_nodes[0] if len(output_nodes) == 1 else None
    output_operator = output["operator"] if output else ""
    output_type = output_operator.split(":", 1)[1].strip() if ":" in output_operator else ""

    parameters: list[dict[str, Any]] = []
    for node in nodes:
        operator = node["operator"]
        prefix, separator, exposed_type = operator.partition(":")
        if separator and prefix.strip().casefold() in {"parameter", "input"}:
            attrs = node["attributes"]
            parameters.append(
                {
                    "id": node["id"],
                    "name": node["name"],
                    "kind": prefix.strip().casefold(),
                    "type": exposed_type.strip(),
                    "default_value": attrs.get("default_value"),
                    "min_value": attrs.get("min_value"),
                    "max_value": attrs.get("max_value"),
                    "system_units_value": attrs.get("system_units_value"),
                }
            )

    metadata: dict[str, Any] = {}
    if meta_info is not None:
        for child in list(meta_info):
            name = _local_name(child.tag)
            if not name or name.casefold() == "graph_version":
                continue
            if list(child):
                metadata[name] = ET.tostring(child, encoding="unicode")
            else:
                metadata[name] = (child.text or "").strip()

    validation_errors = _validation_errors(root)
    executable_content = _executable_content(root)
    executable_reasons = [str(finding["message"]) for finding in executable_content]
    graph_uuid = (root.get("uuid") or "").strip()
    graph_version_guid = "" if graph_version is None else (graph_version.get("guid") or "").strip()
    graph_version_number = "" if graph_version is None else (graph_version.get("number") or "").strip()
    groups = [node for node in nodes if node["kind"] == "group"]
    return {
        "path": str(path.resolve(strict=False)),
        "hash": digest,
        "extension": path.suffix.lower(),
        "graph_schema_version": (root.get("version") or "").strip(),
        "uuid": graph_uuid,
        "graph_version_guid": graph_version_guid,
        "graph_version_number": graph_version_number,
        "identity": {
            "uuid": graph_uuid,
            "graph_version_guid": graph_version_guid,
            "graph_version_number": graph_version_number,
        },
        "identifier": _meta_text(meta_info, "identifier"),
        "display_name": _meta_text(meta_info, "displayName"),
        "description": _meta_text(meta_info, "description"),
        "category": _meta_text(meta_info, "category"),
        "metadata": metadata,
        "nodes": nodes,
        "connections": connections,
        "groups": groups,
        "parameters": parameters,
        "terminal": output,
        "terminal_output_type": output_type,
        "node_count": len(nodes),
        "connection_count": len(connections),
        "group_count": len(groups),
        "valid": not validation_errors,
        "validation_errors": validation_errors,
        "executable": bool(executable_reasons),
        "executable_content": executable_content,
        "risky_operators": [
            finding
            for finding in executable_content
            if finding.get("kind") == "operator"
        ],
        "executable_reasons": executable_reasons,
    }


def inspect_graph(path: Pathish) -> dict[str, Any]:
    """Return normalized graph metadata, nodes, connections, and validation."""

    graph_path = _path(path)
    tree, raw, _ = _parse_document(graph_path)
    return _inspect_tree(tree.getroot(), path=graph_path, digest=_sha256(raw))


def _canonical_meta_tag(key: object) -> str:
    raw = str(key).strip()
    if not raw:
        raise MCGGraphError("Metadata key cannot be empty")
    collapsed = re.sub(r"[^a-z0-9]", "", raw.casefold())
    if collapsed in _PROTECTED_META_KEYS:
        raise MCGGraphError(f"Metadata identity field {raw!r} cannot be changed")
    tag = _META_ALIASES.get(raw.casefold(), raw)
    if not _TAG_RE.fullmatch(tag):
        raise MCGGraphError(f"Invalid metadata element name {raw!r}")
    return tag


def _find_meta_element(meta_info: ET.Element, tag: str) -> ET.Element | None:
    wanted = re.sub(r"[^a-z0-9]", "", tag.casefold())
    for child in list(meta_info):
        current = re.sub(r"[^a-z0-9]", "", _local_name(child.tag).casefold())
        if current == wanted:
            return child
    return None


def _set_meta_text(meta_info: ET.Element, key: object, value: object) -> None:
    tag = _canonical_meta_tag(key)
    element = _find_meta_element(meta_info, tag)
    if element is None:
        element = ET.SubElement(meta_info, _qualified_tag(meta_info, tag))
    # Structured custom UI/XML is intentionally not accepted through this API.
    element.text = "" if value is None else _string_value(value)


def _string_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def _attribute_name(value: object) -> str:
    name = str(value)
    if not _TAG_RE.fullmatch(name):
        raise MCGGraphError(f"Invalid node attribute name {name!r}")
    return name


def _set_graph_version_number(root: ET.Element, number: str) -> None:
    meta_info = _child(root, "meta_info")
    graph_version = _child(meta_info, "graph_version")
    if graph_version is None:
        raise MCGValidationError(["missing <graph_version>"])
    graph_version.set("number", number)


def _bumped_version(number: str) -> str:
    match = re.fullmatch(r"(\d+(?:\.\d+)*)", number.strip())
    if not match:
        return "0.0.1"
    parts = [int(part) for part in match.group(1).split(".")]
    parts[-1] += 1
    return ".".join(str(part) for part in parts)


def _proof_counts(root: ET.Element) -> dict[str, int]:
    nodes = _node_elements(root)
    return {
        "nodes": len(nodes),
        "connections": len(_connection_elements(root)),
        "groups": sum(1 for node in nodes if _is_group_node(node)),
    }


def create_graph_from_template(
    template_path: Pathish,
    destination: Pathish,
    identifier: str,
    display_name: str = "",
    description: str = "",
    category: str = "",
) -> dict[str, Any]:
    """Fork an installed graph template into a fresh graph identity.

    The destination must not exist.  Executable templates are rejected because
    this API has no unsafe override; callers can inspect such a template but
    cannot accidentally fork it into the compile workspace.
    """

    source = _path(template_path)
    target = _path(destination)
    if not str(identifier).strip():
        raise MCGGraphError("identifier is required")
    with _lock_for(target):
        if target.exists():
            raise MCGGraphError(f"Destination already exists: {target}")
        tree, source_raw, declaration = _parse_document(source)
        root = tree.getroot()
        _validate(root, allow_executable=False)
        source_graph_version = _child(_child(root, "meta_info"), "graph_version")
        if source_graph_version is None:
            raise MCGValidationError(["template identity metadata is incomplete"])
        source_identity = {
            "uuid": root.get("uuid", ""),
            "graph_version_guid": source_graph_version.get("guid", ""),
        }

        root.set("uuid", str(uuid4()))
        meta_info = _child(root, "meta_info")
        graph_version = _child(meta_info, "graph_version")
        if meta_info is None or graph_version is None:
            raise MCGValidationError(["template identity metadata is incomplete"])
        graph_version.set("guid", str(uuid4()))
        graph_version.set("number", "0.0.1")
        _set_meta_text(meta_info, "identifier", str(identifier).strip())
        _set_meta_text(meta_info, "displayName", display_name or str(identifier).strip())
        _set_meta_text(meta_info, "description", description)
        _set_meta_text(meta_info, "category", category)

        _validate(root, allow_executable=False)
        data = _serialize(tree, xml_declaration=declaration)
        _validate_serialized(data, allow_executable=False)
        _atomic_write(target, data)
        written_hash = graph_hash(target)
        counts = _proof_counts(root)
        return {
            "path": str(target.resolve(strict=False)),
            "source_path": str(source.resolve(strict=False)),
            "source_hash": _sha256(source_raw),
            "hash": written_hash,
            "before_hash": None,
            "after_hash": written_hash,
            "uuid": root.get("uuid", ""),
            "graph_version_guid": graph_version.get("guid", ""),
            "graph_version_number": graph_version.get("number", ""),
            "source_identity": source_identity,
            "identity_changed": (
                source_identity["uuid"] != root.get("uuid", "")
                and source_identity["graph_version_guid"] != graph_version.get("guid", "")
            ),
            "identifier": str(identifier).strip(),
            "display_name": display_name or str(identifier).strip(),
            "counts": counts,
            "node_count": counts["nodes"],
            "connection_count": counts["connections"],
            "created": True,
        }


def _nodes_parent(root: ET.Element) -> ET.Element:
    parent = _child(root, "nodes")
    if parent is None:
        raise MCGValidationError(["missing <nodes>"])
    return parent


def _connections_parent(root: ET.Element) -> ET.Element:
    parent = _child(root, "connections")
    if parent is None:
        raise MCGValidationError(["missing <connections>"])
    return parent


def _node_index(root: ET.Element) -> dict[str, ET.Element]:
    result: dict[str, ET.Element] = {}
    for node in _node_elements(root):
        node_id = _normalized_id(node.get("id", ""))
        if node_id in result:
            raise MCGValidationError([f"duplicate node id {node_id}"])
        result[node_id] = node
    return result


def _require_node(root: ET.Element, node_id: object) -> tuple[str, ET.Element]:
    normalized = _normalized_id(node_id)
    node = _node_index(root).get(normalized)
    if node is None:
        raise MCGGraphError(f"Node {node_id!r} does not exist")
    return normalized, node


def _next_node_id(root: ET.Element) -> str:
    identifiers = [int(node_id) for node_id in _node_index(root)]
    return str(max(identifiers, default=-1) + 1)


def _operation_name(operation: Mapping[str, Any]) -> str:
    value = operation.get("op", operation.get("action", operation.get("type", "")))
    return str(value).strip().casefold().replace("-", "_")


def _attribute_payload(
    payload: Mapping[str, Any], *, excluded: set[str]
) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    explicit = payload.get("attributes", payload.get("attrs", payload.get("set", {})))
    if explicit is not None:
        if not isinstance(explicit, Mapping):
            raise MCGGraphError("node attributes must be an object")
        attributes.update(explicit)
    for key, value in payload.items():
        if key not in excluded and key not in {"attributes", "attrs", "set"}:
            attributes[str(key)] = value
    return attributes


def _set_members(node: ET.Element, members: object) -> None:
    if not _is_group_node(node):
        raise MCGGraphError(f"Node {node.get('id', '?')} is not a group node")
    if isinstance(members, str):
        raw_members = [part for part in re.split(r"[,\s]+", members) if part]
    elif isinstance(members, (list, tuple, set)):
        raw_members = list(members)
    else:
        raise MCGGraphError("group members must be an array or comma-separated string")
    normalized = [_normalized_id(member) for member in raw_members]
    element = _child(node, "nodes")
    if element is None:
        element = ET.SubElement(node, _qualified_tag(node, "nodes"))
    element.text = ",".join(normalized)


def _add_node(root: ET.Element, operation: Mapping[str, Any]) -> dict[str, Any]:
    nested = operation.get("node")
    if nested is not None and not isinstance(nested, Mapping):
        raise MCGGraphError("add_node.node must be an object")
    payload: Mapping[str, Any] = nested if isinstance(nested, Mapping) else operation
    node_id = _normalized_id(payload.get("id", _next_node_id(root)))
    if node_id in _node_index(root):
        raise MCGValidationError([f"duplicate node id {node_id}"])

    attributes = _attribute_payload(
        payload,
        excluded={"op", "action", "type", "node", "id", "members"},
    )
    operator = str(attributes.pop("operator", "") or "").strip()
    group_name = str(attributes.pop("groupnode", "") or "").strip()
    if bool(operator) == bool(group_name):
        raise MCGGraphError("add_node requires exactly one of operator or groupnode")

    element = ET.Element(_qualified_tag(_nodes_parent(root), "node"))
    element.set("operator" if operator else "groupnode", operator or group_name)
    element.set("id", node_id)
    for key, value in attributes.items():
        key = _attribute_name(key)
        if key == "id":
            continue
        if value is not None:
            element.set(key, _string_value(value))
    if "members" in payload:
        _set_members(element, payload["members"])
    _nodes_parent(root).append(element)
    return {"op": "add_node", "id": node_id, "operator": operator, "groupnode": group_name}


def _remove_node(root: ET.Element, operation: Mapping[str, Any]) -> dict[str, Any]:
    node_id, node = _require_node(root, operation.get("id", operation.get("node_id", "")))
    _nodes_parent(root).remove(node)
    removed_connections = 0
    connections_parent = _connections_parent(root)
    for connection in list(_connection_elements(root)):
        source = _normalized_id(connection.get("sourcenode", ""))
        destination = _normalized_id(connection.get("destnode", ""))
        if source == node_id or destination == node_id:
            connections_parent.remove(connection)
            removed_connections += 1

    removed_memberships = 0
    for group in [item for item in _node_elements(root) if _is_group_node(item)]:
        members = [_normalized_id(member) for member in _group_members(group)]
        filtered = [member for member in members if member != node_id]
        if len(filtered) != len(members):
            removed_memberships += len(members) - len(filtered)
            _set_members(group, filtered)
    return {
        "op": "remove_node",
        "id": node_id,
        "removed_connections": removed_connections,
        "removed_memberships": removed_memberships,
    }


def _set_node(root: ET.Element, operation: Mapping[str, Any]) -> dict[str, Any]:
    node_id, node = _require_node(root, operation.get("id", operation.get("node_id", "")))
    attributes = _attribute_payload(
        operation,
        excluded={"op", "action", "type", "id", "node_id", "members"},
    )
    if "id" in attributes:
        raise MCGGraphError("set_node cannot change a node id")
    for key, value in attributes.items():
        key = _attribute_name(key)
        if value is None:
            node.attrib.pop(key, None)
        else:
            node.set(key, _string_value(value))
    if "members" in operation:
        _set_members(node, operation["members"])
    return {"op": "set_node", "id": node_id, "attributes": sorted(attributes)}


def _replace_operator(root: ET.Element, operation: Mapping[str, Any]) -> dict[str, Any]:
    node_id, node = _require_node(root, operation.get("id", operation.get("node_id", "")))
    if _is_group_node(node):
        raise MCGGraphError(f"Cannot replace the operator of group node {node_id}")
    operator = str(
        operation.get("operator", operation.get("new_operator", operation.get("value", "")))
    ).strip()
    if not operator:
        raise MCGGraphError("replace_operator requires operator")
    previous = node.get("operator", "")
    node.set("operator", operator)
    return {"op": "replace_operator", "id": node_id, "before": previous, "after": operator}


def _connection_values(operation: Mapping[str, Any]) -> dict[str, str]:
    nested = operation.get("connection")
    if nested is not None and not isinstance(nested, Mapping):
        raise MCGGraphError("connection must be an object")
    source: Mapping[str, Any] = nested if isinstance(nested, Mapping) else operation

    def pick(*names: str) -> Any:
        for name in names:
            if name in source:
                return source[name]
        return None

    raw = {
        "sourcenode": pick("source_node", "sourceNode", "sourcenode", "source"),
        "sourceport": pick("source_port", "sourcePort", "sourceport"),
        "destnode": pick("dest_node", "destNode", "destnode", "destination"),
        "destport": pick("dest_port", "destPort", "destport"),
    }
    missing = [key for key, value in raw.items() if value is None or str(value).strip() == ""]
    if missing:
        raise MCGGraphError(f"connection operation is missing: {', '.join(missing)}")
    return {
        "sourcenode": _normalized_id(raw["sourcenode"]),
        "sourceport": _normalized_id(raw["sourceport"]),
        "destnode": _normalized_id(raw["destnode"]),
        "destport": _normalized_id(raw["destport"]),
    }


def _connect(root: ET.Element, operation: Mapping[str, Any]) -> dict[str, Any]:
    values = _connection_values(operation)
    element = ET.SubElement(
        _connections_parent(root), _qualified_tag(_connections_parent(root), "connection")
    )
    for key, value in values.items():
        element.set(key, value)
    return {
        "op": "connect",
        "source_node": values["sourcenode"],
        "source_port": values["sourceport"],
        "dest_node": values["destnode"],
        "dest_port": values["destport"],
    }


def _disconnect(root: ET.Element, operation: Mapping[str, Any]) -> dict[str, Any]:
    values = _connection_values(operation)
    parent = _connections_parent(root)
    removed = []
    for connection in list(_connection_elements(root)):
        current = {
            key: _normalized_id(connection.get(key, "")) for key in values
        }
        if current == values:
            parent.remove(connection)
            removed.append(connection)
    if not removed:
        raise MCGGraphError(
            "Connection does not exist: "
            f"{values['sourcenode']}:{values['sourceport']} -> "
            f"{values['destnode']}:{values['destport']}"
        )
    return {
        "op": "disconnect",
        "source_node": values["sourcenode"],
        "source_port": values["sourceport"],
        "dest_node": values["destnode"],
        "dest_port": values["destport"],
        "removed": len(removed),
    }


def _set_meta(root: ET.Element, operation: Mapping[str, Any]) -> dict[str, Any]:
    meta_info = _child(root, "meta_info")
    if meta_info is None:
        raise MCGValidationError(["missing <meta_info>"])
    values = operation.get("values", operation.get("meta"))
    if values is None:
        key = operation.get("key", operation.get("name"))
        if key is None:
            raise MCGGraphError("set_meta requires key/value or values")
        values = {str(key): operation.get("value", "")}
    if not isinstance(values, Mapping):
        raise MCGGraphError("set_meta values must be an object")
    changed: list[str] = []
    for key, value in values.items():
        _set_meta_text(meta_info, key, value)
        changed.append(str(key))
    return {"op": "set_meta", "keys": changed}


def _apply_operation(root: ET.Element, operation: Mapping[str, Any]) -> dict[str, Any]:
    name = _operation_name(operation)
    handlers = {
        "add_node": _add_node,
        "remove_node": _remove_node,
        "set_node": _set_node,
        "replace_operator": _replace_operator,
        "connect": _connect,
        "disconnect": _disconnect,
        "set_meta": _set_meta,
    }
    handler = handlers.get(name)
    if handler is None:
        raise MCGGraphError(
            f"Unsupported MCG patch operation {name or '<empty>'!r}; "
            f"expected one of {', '.join(sorted(handlers))}"
        )
    return handler(root, operation)


def _create_checkpoint(path: Path, checkpoint_dir: Path, digest: str) -> Path:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = checkpoint_dir / (
        f"{path.stem}.{digest[:12]}.{uuid4().hex[:8]}.checkpoint{path.suffix}"
    )
    try:
        shutil.copy2(path, checkpoint)
    except OSError as exc:
        raise MCGGraphError(f"Could not create MCG checkpoint {checkpoint}: {exc}") from exc
    return checkpoint


def patch_graph(
    path: Pathish,
    expected_hash: str,
    operations: list[dict[str, Any]],
    dry_run: bool = False,
    allow_executable: bool = False,
    checkpoint_dir: Pathish | None = None,
) -> dict[str, Any]:
    """Apply structured operations with optimistic locking and atomic replace.

    Supported operation names are ``add_node``, ``remove_node``, ``set_node``,
    ``replace_operator``, ``connect``, ``disconnect``, and ``set_meta``.  Node
    and graph identity GUIDs are immutable; the graph-version *number* receives
    a patch bump for every successful mutation or dry-run preview.
    """

    graph_path = _path(path)
    if not isinstance(operations, list) or not operations:
        raise MCGGraphError("operations must be a non-empty array")
    with _lock_for(graph_path):
        tree, raw, declaration = _parse_document(graph_path)
        before_hash = _sha256(raw)
        if before_hash != str(expected_hash).strip().lower():
            raise MCGHashConflict(str(expected_hash).strip(), before_hash)
        root = tree.getroot()
        initial_errors = _validation_errors(root)
        if initial_errors:
            raise MCGValidationError(initial_errors)

        meta_info = _child(root, "meta_info")
        graph_version = _child(meta_info, "graph_version")
        if graph_version is None:
            raise MCGValidationError(["missing <graph_version>"])
        original_uuid = root.get("uuid", "")
        original_guid = graph_version.get("guid", "")
        original_number = graph_version.get("number", "")
        before_counts = _proof_counts(root)

        applied: list[dict[str, Any]] = []
        for index, operation in enumerate(operations):
            if not isinstance(operation, Mapping):
                raise MCGGraphError(f"operation {index} must be an object")
            applied.append(_apply_operation(root, operation))

        if root.get("uuid", "") != original_uuid:
            raise MCGValidationError(["patch changed immutable graph uuid"])
        current_graph_version = _child(_child(root, "meta_info"), "graph_version")
        if current_graph_version is None or current_graph_version.get("guid", "") != original_guid:
            raise MCGValidationError(["patch changed immutable graph_version guid"])
        new_number = _bumped_version(original_number)
        _set_graph_version_number(root, new_number)
        _validate(root, allow_executable=allow_executable)

        data = _serialize(tree, xml_declaration=declaration)
        _validate_serialized(data, allow_executable=allow_executable)
        after_hash = _sha256(data)
        after_counts = _proof_counts(root)
        checkpoint: Path | None = None
        if not dry_run:
            if checkpoint_dir is not None:
                checkpoint = _create_checkpoint(
                    graph_path, _path(checkpoint_dir), before_hash
                )
            _atomic_write(graph_path, data)
            confirmed = graph_hash(graph_path)
            if confirmed != after_hash:
                raise MCGGraphError(
                    f"Atomic MCG write verification failed: expected {after_hash}, got {confirmed}"
                )

        executable_reasons = _executable_reasons(root)
        return {
            "path": str(graph_path.resolve(strict=False)),
            "dry_run": bool(dry_run),
            "changed": before_hash != after_hash,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "hash": before_hash if dry_run else after_hash,
            "uuid": original_uuid,
            "graph_version_guid": original_guid,
            "before_graph_version_number": original_number,
            "graph_version_number": new_number,
            "before_counts": before_counts,
            "after_counts": after_counts,
            "node_count": after_counts["nodes"],
            "connection_count": after_counts["connections"],
            "operation_count": len(applied),
            "operations": applied,
            "checkpoint_path": str(checkpoint.resolve(strict=False)) if checkpoint else None,
            "executable": bool(executable_reasons),
            "executable_reasons": executable_reasons,
            "identity_preserved": True,
        }


def restore_checkpoint(
    path: Pathish,
    checkpoint_path: Pathish,
    expected_hash: str = "",
    allow_executable: bool = False,
) -> dict[str, Any]:
    """Atomically restore a validated checkpoint verbatim."""

    graph_path = _path(path)
    checkpoint = _path(checkpoint_path)
    if graph_path.resolve(strict=False) == checkpoint.resolve(strict=False):
        raise MCGGraphError("Graph path and checkpoint path must differ")
    with _lock_for(graph_path):
        before_raw = _read_bytes(graph_path)
        before_hash = _sha256(before_raw)
        if expected_hash and before_hash != str(expected_hash).strip().lower():
            raise MCGHashConflict(str(expected_hash).strip(), before_hash)

        tree, checkpoint_raw, _ = _parse_document(checkpoint)
        _validate(tree.getroot(), allow_executable=allow_executable)
        after_hash = _sha256(checkpoint_raw)
        before_summary = inspect_graph(graph_path)
        checkpoint_summary = _inspect_tree(
            tree.getroot(), path=checkpoint, digest=after_hash
        )
        _atomic_write(graph_path, checkpoint_raw)
        confirmed = graph_hash(graph_path)
        if confirmed != after_hash:
            raise MCGGraphError(
                f"MCG checkpoint restore verification failed: expected {after_hash}, got {confirmed}"
            )
        return {
            "path": str(graph_path.resolve(strict=False)),
            "checkpoint_path": str(checkpoint.resolve(strict=False)),
            "restored": True,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "hash": after_hash,
            "before_counts": {
                "nodes": before_summary["node_count"],
                "connections": before_summary["connection_count"],
                "groups": before_summary["group_count"],
            },
            "after_counts": {
                "nodes": checkpoint_summary["node_count"],
                "connections": checkpoint_summary["connection_count"],
                "groups": checkpoint_summary["group_count"],
            },
            "uuid": checkpoint_summary["uuid"],
            "graph_version_guid": checkpoint_summary["graph_version_guid"],
            "graph_version_number": checkpoint_summary["graph_version_number"],
        }


__all__ = [
    "MCGGraphError",
    "MCGHashConflict",
    "MCGSecurityError",
    "MCGValidationError",
    "create_graph_from_template",
    "graph_hash",
    "inspect_graph",
    "is_within",
    "patch_graph",
    "restore_checkpoint",
]
