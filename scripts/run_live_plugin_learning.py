"""Live plugin-learning smoke test for self-describing plugin runtimes."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from maxmcp.tools.bridge import get_bridge_status
from maxmcp.tools.plugins import discover_plugin_surface, get_plugin_manifest, inspect_plugin_class, inspect_plugin_instance
from maxmcp.tools.railclone import get_railclone_style_graph


def _load_json(raw: str) -> dict[str, Any]:
    return json.loads(raw)


def _normalize(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def _print_result(name: str, payload: dict[str, Any]) -> None:
    print(f"[ok] {name}")
    print(json.dumps(payload, indent=2))


def _fail(name: str, message: str) -> None:
    print(f"[fail] {name}: {message}", file=sys.stderr)


def _expect(condition: bool, name: str, message: str) -> bool:
    if condition:
        return True
    _fail(name, message)
    return False


def _choose_class(manifest: dict[str, Any], fallback_class: str) -> str:
    if fallback_class:
        return fallback_class

    for field in ("entryClassesDetected", "entryClassesExpected", "entryClasses", "relatedClasses"):
        values = manifest.get(field, [])
        if isinstance(values, list) and values:
            return str(values[0])
    return ""


def _run_railclone_extension(object_name: str) -> bool:
    step = "get_railclone_style_graph"
    try:
        graph = _load_json(
            get_railclone_style_graph(
                object_name,
                include_bases=True,
                include_segments=True,
                include_parameters=True,
                include_raw_style_desc=True,
                max_style_desc_chars=8000,
            )
        )
    except Exception as exc:  # pragma: no cover - live smoke path
        _fail(step, str(exc))
        return False

    checks = [
        _expect(graph.get("name") == object_name, step, f"returned object name {graph.get('name')!r}"),
        _expect(int(graph.get("baseCount", 0)) >= 1, step, "expected at least one RailClone base"),
        _expect(int(graph.get("segmentCount", 0)) >= 1, step, "expected at least one RailClone segment"),
    ]
    _print_result(step, graph)
    return all(checks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True, help="Plugin family name, e.g. RailClone")
    parser.add_argument("--object", required=True, help="Live scene object to inspect")
    parser.add_argument("--class-name", default="", help="Optional runtime class override")
    args = parser.parse_args()

    failed = False
    normalized_plugin = _normalize(args.plugin)

    step = "get_bridge_status"
    try:
        bridge = _load_json(get_bridge_status())
        failed |= not _expect(bool(bridge.get("connected")), step, "bridge is not connected")
        _print_result(step, bridge)
    except Exception as exc:  # pragma: no cover - live smoke path
        _fail(step, str(exc))
        return 1

    step = "discover_plugin_surface"
    try:
        discovered = _load_json(discover_plugin_surface(plugin_name=args.plugin))
        failed |= not _expect(bool(discovered.get("installed")), step, "plugin is not reported installed")
        failed |= not _expect(
            _normalize(str(discovered.get("plugin", ""))) == normalized_plugin,
            step,
            f"reported plugin {discovered.get('plugin')!r}",
        )
        _print_result(step, discovered)
    except Exception as exc:  # pragma: no cover - live smoke path
        _fail(step, str(exc))
        return 1

    step = "get_plugin_manifest"
    try:
        manifest = _load_json(get_plugin_manifest(args.plugin))
        failed |= not _expect(bool(manifest.get("installed")), step, "manifest says plugin is not installed")
        failed |= not _expect(
            _normalize(str(manifest.get("plugin", ""))) == normalized_plugin,
            step,
            f"manifest plugin {manifest.get('plugin')!r}",
        )
        failed |= not _expect("warnings" in manifest, step, "manifest is missing warnings")
        failed |= not _expect("classes" in manifest, step, "manifest is missing class summaries")
        _print_result(step, manifest)
    except Exception as exc:  # pragma: no cover - live smoke path
        _fail(step, str(exc))
        return 1

    class_name = _choose_class(manifest, args.class_name)
    step = "inspect_plugin_class"
    try:
        inspected_class = _load_json(inspect_plugin_class(class_name))
        failed |= not _expect("error" not in inspected_class, step, inspected_class.get("error", "unknown inspect_plugin_class error"))
        failed |= not _expect(inspected_class.get("class") == class_name, step, f"returned class {inspected_class.get('class')!r}")
        failed |= not _expect(isinstance(inspected_class.get("methods"), list), step, "methods is not a list")
        _print_result(step, inspected_class)
    except Exception as exc:  # pragma: no cover - live smoke path
        _fail(step, f"{class_name}: {exc}")
        return 1

    step = "inspect_plugin_instance"
    try:
        inspected_instance = _load_json(inspect_plugin_instance(args.object, detail="normal"))
        failed |= not _expect(
            _normalize(str(inspected_instance.get("primaryPlugin", ""))) == normalized_plugin,
            step,
            f"primary plugin {inspected_instance.get('primaryPlugin')!r}",
        )
        failed |= not _expect(
            normalized_plugin in {_normalize(str(item)) for item in inspected_instance.get("pluginsDetected", [])},
            step,
            f"pluginsDetected did not include {args.plugin!r}",
        )
        failed |= not _expect("manifest" in inspected_instance, step, "instance payload is missing manifest")
        _print_result(step, inspected_instance)
    except Exception as exc:  # pragma: no cover - live smoke path
        _fail(step, f"{args.object}: {exc}")
        return 1

    if normalized_plugin == "railclone":
        failed |= not _run_railclone_extension(args.object)

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
