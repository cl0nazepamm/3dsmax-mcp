"""Offline discovery helpers for Autodesk Max Creation Graph content.

The live Viper operator depot is the authoritative catalog when 3ds Max is
reachable.  These helpers provide deterministic path/template discovery and a
useful read-only fallback when the bridge is offline.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Iterable


GRAPH_EXTENSIONS = {".maxtool", ".maxcompound"}
_MAX_DIR_RE = re.compile(r"^3ds Max (?P<year>\d{4})$", re.IGNORECASE)


def bundled_sample_root() -> Path:
    """Return the read-only Autodesk MCG XML corpus shipped with the package."""
    return (
        Path(__file__).resolve().parents[1]
        / "resources"
        / "mcg_samples"
        / "autodesk_mcg_2017"
    )


def _candidate_program_files() -> list[Path]:
    roots: list[Path] = []
    for key in ("ProgramFiles", "PROGRAMFILES"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value))
    default = Path(r"C:\Program Files")
    if default not in roots:
        roots.append(default)
    return roots


def find_max_installations() -> list[dict[str, object]]:
    """Return installed Max roots newest-first without loading Autodesk DLLs."""
    found: dict[int, Path] = {}

    for year in range(2035, 2015, -1):
        env_value = os.environ.get(f"ADSK_3DSMAX_x64_{year}")
        if env_value:
            path = Path(env_value).expanduser()
            if path.is_dir():
                found[year] = path.resolve()

    for program_files in _candidate_program_files():
        autodesk = program_files / "Autodesk"
        if not autodesk.is_dir():
            continue
        try:
            children = list(autodesk.iterdir())
        except OSError:
            continue
        for child in children:
            match = _MAX_DIR_RE.match(child.name)
            if match and child.is_dir():
                found.setdefault(int(match.group("year")), child.resolve())

    return [
        {"year": year, "root": str(found[year])}
        for year in sorted(found, reverse=True)
    ]


def max_root_for_year(year: int) -> Path | None:
    for item in find_max_installations():
        if int(item["year"]) == int(year):
            return Path(str(item["root"]))
    return None


def operator_metadata_path(max_root: Path) -> Path:
    return max_root / "MaxCreationGraph" / "OperatorMetaInfo.xml"


def compound_root(max_root: Path) -> Path:
    return max_root / "MaxCreationGraph" / "Compounds"


def find_tool_template(max_root: Path, kind: str, locale: str = "en-US") -> Path:
    normalized = kind.strip().lower()
    names = {
        "geometry": "MCG_objectTemplate.maxtool",
        "object": "MCG_objectTemplate.maxtool",
        "modifier": "MCG_modifierTemplate.maxtool",
    }
    filename = names.get(normalized)
    if not filename:
        raise ValueError("kind must be geometry or modifier")

    preferred = max_root / locale / "plugcfg" / "MCG" / "Tools" / filename
    if preferred.is_file():
        return preferred

    matches = sorted(max_root.glob(f"*/plugcfg/MCG/Tools/{filename}"))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"MCG {normalized} template not found under {max_root}")


def sample_roots() -> list[Path]:
    """Return configured read-only sample corpora that exist on disk."""
    values: list[Path] = []
    configured = os.environ.get("MCP_MCG_SAMPLE_ROOTS", "")
    for item in configured.split(os.pathsep):
        if item.strip():
            values.append(Path(item.strip()).expanduser())
    values.append(bundled_sample_root())

    result: list[Path] = []
    seen: set[str] = set()
    for path in values:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key not in seen and resolved.is_dir():
            seen.add(key)
            result.append(resolved)
    return result


def iter_graph_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if root.is_file() and root.suffix.lower() in GRAPH_EXTENSIONS:
            yield root.resolve()
            continue
        if not root.is_dir():
            continue
        try:
            paths = sorted(root.rglob("*"), key=lambda item: str(item).casefold())
            for path in paths:
                if path.is_file() and path.suffix.lower() in GRAPH_EXTENSIONS:
                    yield path.resolve()
        except OSError:
            continue


def _text(parent: ET.Element | None, tag: str, default: str = "") -> str:
    if parent is None:
        return default
    child = parent.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _bool_text(parent: ET.Element | None, tag: str) -> bool:
    return _text(parent, tag).casefold() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=16)
def parse_operator_metadata(path_string: str) -> tuple[dict[str, object], ...]:
    path = Path(path_string)
    root = ET.parse(path).getroot()
    records: list[dict[str, object]] = []
    for elem in root.findall("operator"):
        identifier = _text(elem, "identifier")
        if not identifier:
            continue
        inputs = []
        for child in elem:
            if child.tag.startswith("arg") and child.text:
                try:
                    index = int(child.tag[3:])
                except ValueError:
                    continue
                inputs.append({"index": index, "name": child.text.strip(), "type": ""})
        inputs.sort(key=lambda item: int(item["index"]))
        records.append(
            {
                "identifier": identifier,
                "display_name": _text(elem, "displayName", identifier),
                "category": _text(elem, "category"),
                "description": _text(elem, "description"),
                "inputs": inputs,
                "return_type": "",
                "typed": False,
                "outputs": [
                    {"index": 0, "name": "value", "kind": "value", "type": ""},
                    {"index": 1, "name": "function", "kind": "function", "type": ""},
                ],
                "deprecated": False,
                "impure": True if identifier.casefold() == "evalmaxscript" else None,
                "impure_known": identifier.casefold() == "evalmaxscript",
                "source": "OperatorMetaInfo.xml",
                "source_path": str(path),
            }
        )
    return tuple(records)


@lru_cache(maxsize=4096)
def parse_compound_metadata(path_string: str) -> dict[str, object] | None:
    path = Path(path_string)
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    meta = root.find("meta_info")
    identifier = _text(meta, "identifier", path.stem)
    if not identifier:
        return None
    inputs: list[dict[str, object]] = []
    nodes = root.find("nodes")
    node_by_id: dict[str, ET.Element] = {}
    terminal_id = ""
    if nodes is not None:
        for node in nodes.findall("node"):
            node_id = node.get("id", "")
            if node_id:
                node_by_id[node_id] = node
            operator = node.get("operator", "")
            if operator == "Output: compound":
                terminal_id = node_id

    # MCG computes a compound's public input ordering by walking backwards from
    # Output:compound in destination-port order, not by XML node order or ID.
    # Reproduce that traversal so an agent can connect compounds by port name.
    incoming: dict[str, list[tuple[int, str]]] = {}
    connections = root.find("connections")
    if connections is not None:
        for connection in connections.findall("connection"):
            source = connection.get("sourcenode", "")
            destination = connection.get("destnode", "")
            try:
                destination_port = int(connection.get("destport", "0"))
            except ValueError:
                destination_port = 0
            incoming.setdefault(destination, []).append((destination_port, source))
    for edges in incoming.values():
        edges.sort(key=lambda edge: edge[0])

    visited: set[str] = set()
    exposed: set[str] = set()

    def visit(node_id: str) -> None:
        if not node_id or node_id in visited:
            return
        visited.add(node_id)
        node = node_by_id.get(node_id)
        if node is None:
            return
        operator = node.get("operator", "")
        if operator.startswith("Input:") and node_id not in exposed:
            exposed.add(node_id)
            inputs.append(
                {
                    "index": len(inputs),
                    "name": node.get("name", f"arg{len(inputs)}"),
                    "type": operator.partition(":")[2].strip(),
                }
            )
            return
        for _port, source_id in incoming.get(node_id, []):
            visit(source_id)

    visit(terminal_id)
    return {
        "identifier": identifier,
        "display_name": _text(meta, "displayName", identifier),
        "category": _text(meta, "category"),
        "description": _text(meta, "description"),
        "inputs": inputs,
        "return_type": "",
        "typed": False,
        "outputs": [
            {"index": 0, "name": "value", "kind": "value", "type": ""},
            {"index": 1, "name": "function", "kind": "function", "type": ""},
        ],
        "deprecated": _bool_text(meta, "deprecated"),
        "impure": False,
        "impure_known": True,
        "source": "compound",
        "source_path": str(path),
    }


def _match_score(record: dict[str, object], query: str, category: str) -> int | None:
    category_value = str(record.get("category", ""))
    if category and category.casefold() not in category_value.casefold():
        return None
    if not query:
        return 10
    needle = query.casefold()
    identifier = str(record.get("identifier", ""))
    display = str(record.get("display_name", ""))
    description = str(record.get("description", ""))
    fields = (identifier.casefold(), display.casefold(), category_value.casefold(), description.casefold())
    if needle == fields[0]:
        return 100
    if fields[0].startswith(needle):
        return 90
    if fields[1].startswith(needle):
        return 80
    if needle in fields[0]:
        return 70
    if needle in fields[1]:
        return 60
    if needle in fields[2]:
        return 40
    if needle in fields[3]:
        return 20
    return None


def search_offline_operators(
    max_root: Path | None,
    *,
    query: str = "",
    category: str = "",
    limit: int = 50,
    include_samples: bool = True,
) -> dict[str, object]:
    """Search built-ins and installed/sample compounds without loading Max DLLs."""
    limit = max(1, min(int(limit), 250))
    records: list[dict[str, object]] = []
    if max_root:
        metadata = operator_metadata_path(max_root)
        if metadata.is_file():
            records.extend(parse_operator_metadata(str(metadata)))
        compounds = compound_root(max_root)
        for path in iter_graph_files([compounds]):
            record = parse_compound_metadata(str(path))
            if record:
                records.append(record)
    if include_samples:
        for root in sample_roots():
            compounds = root / "Compounds"
            for path in iter_graph_files([compounds]):
                record = parse_compound_metadata(str(path))
                if record:
                    tagged = dict(record)
                    tagged["source"] = "sample_compound"
                    records.append(tagged)

    best: dict[str, tuple[int, dict[str, object]]] = {}
    for record in records:
        score = _match_score(record, query.strip(), category.strip())
        if score is None:
            continue
        identifier = str(record.get("identifier", ""))
        key = identifier.casefold()
        current = best.get(key)
        if current is None or score > current[0] or (
            score == current[0] and record.get("source") == "OperatorMetaInfo.xml"
        ):
            result = dict(record)
            result["match_score"] = score
            best[key] = (score, result)

    matches = [item[1] for item in best.values()]
    matches.sort(
        key=lambda item: (
            -int(item.get("match_score", 0)),
            str(item.get("display_name", "")).casefold(),
            str(item.get("identifier", "")).casefold(),
        )
    )
    return {
        "source": "offline",
        "query": query,
        "category": category,
        "matched": len(matches),
        "returned": min(len(matches), limit),
        "operators": matches[:limit],
    }


__all__ = [
    "GRAPH_EXTENSIONS",
    "bundled_sample_root",
    "compound_root",
    "find_max_installations",
    "find_tool_template",
    "iter_graph_files",
    "max_root_for_year",
    "operator_metadata_path",
    "parse_compound_metadata",
    "parse_operator_metadata",
    "sample_roots",
    "search_offline_operators",
]
