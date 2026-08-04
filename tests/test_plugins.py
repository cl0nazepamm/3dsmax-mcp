import json
import unittest
from unittest.mock import patch

from maxmcp.tools.plugins import (
    _build_manifest,
    _plugin_gotchas_markdown,
    _plugin_guide_markdown,
    _plugin_recipe_markdown,
    _parse_showclass_lines,
    discover_plugin_surface,
    get_plugin_manifest,
    inspect_plugin_class,
    inspect_plugin_instance,
    list_plugin_classes,
    plugin_gotchas_resource,
    plugin_guide_resource,
    plugin_index_resource,
    plugin_manifest_resource,
    plugin_recipes_resource,
)


RUNTIME_CLASSES = [
    {"name": "tyFlow", "superclass": "GeometryClass", "category": "geometry", "plugin": "tyFlow"},
    {"name": "tyMesher", "superclass": "GeometryClass", "category": "geometry", "plugin": "tyFlow"},
    {"name": "Forest_Pro", "superclass": "GeometryClass", "category": "geometry", "plugin": "Forest Pack"},
    {"name": "Bend", "superclass": "Modifier", "category": "modifier", "plugin": None},
]


class PluginToolTests(unittest.TestCase):
    def test_parse_showclass_lines_extracts_properties(self) -> None:
        parsed = _parse_showclass_lines([
            "tyFlow : GeometryClass {31322891,70f5b9ca}",
            "  .simResetMode : integer",
            "  .allowCustomWirecolor : boolean",
            "  .sourceNode : node",
        ])

        self.assertEqual(parsed["class_name"], "tyFlow")
        self.assertEqual(parsed["superclass"], "GeometryClass")
        self.assertEqual(parsed["properties"][0]["category"], "enum_like")
        self.assertEqual(parsed["properties"][1]["category"], "bool")
        self.assertEqual(parsed["properties"][2]["category"], "node")

    def test_list_plugin_classes_filters_by_plugin(self) -> None:
        with patch("maxmcp.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES):
            result = json.loads(list_plugin_classes(plugin_name="tyflow", limit=1))

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["classes"][0]["name"], "tyFlow")
        self.assertEqual(result["truncated"], True)

    def test_discover_plugin_surface_summarizes_known_plugin(self) -> None:
        with (
            patch("maxmcp.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES),
            patch("maxmcp.tools.plugins._get_scene_instance_counts", return_value={"tyFlow": 2, "tyMesher": 0}),
            patch("maxmcp.tools.plugins.get_plugin_capabilities", return_value='{"maxVersion":2026,"renderer":"Arnold","plugins":{"tyFlow":true}}'),
        ):
            result = json.loads(discover_plugin_surface(plugin_name="tyflow"))

        self.assertEqual(result["plugin"], "tyFlow")
        self.assertEqual(result["installed"], True)
        self.assertEqual(result["sceneInstances"]["tyFlow"], 2)
        self.assertEqual(result["installation"]["status"], "confirmed")
        self.assertEqual(result["entryClassesExpected"], ["tyFlow"])
        self.assertEqual(result["entryClassesDetected"], ["tyFlow"])
        self.assertIn("geometry", result["families"])

    def test_discover_plugin_surface_keeps_runtime_detection_when_capabilities_disagree(self) -> None:
        with (
            patch("maxmcp.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES),
            patch("maxmcp.tools.plugins._get_scene_instance_counts", return_value={"tyFlow": 1, "tyMesher": 0}),
            patch("maxmcp.tools.plugins.get_plugin_capabilities", return_value='{"plugins":{"tyFlow":false}}'),
        ):
            result = json.loads(discover_plugin_surface(plugin_name="tyflow"))

        self.assertEqual(result["installed"], True)
        self.assertEqual(result["installation"]["status"], "runtime_only")
        self.assertTrue(any("did not report this plugin" in note for note in result["notes"]))

    def test_inspect_plugin_class_uses_showclass_reflection(self) -> None:
        with (
            patch("maxmcp.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES),
            patch("maxmcp.tools.plugins._fetch_showclass_lines", return_value=[
                "tyFlow : GeometryClass {31322891,70f5b9ca}",
                "  .simResetMode : integer",
                "  .allowCustomWirecolor : boolean",
            ]),
        ):
            result = json.loads(inspect_plugin_class("tyFlow"))

        self.assertEqual(result["class"], "tyFlow")
        self.assertEqual(result["category"], "geometry")
        self.assertEqual(result["properties"][0]["name"], "simResetMode")
        self.assertEqual(result["methods"], [])
        self.assertEqual(result["methodReflectionSupported"], False)
        self.assertTrue(any("Method reflection" in warning for warning in result["warnings"]))

    def test_inspect_plugin_class_returns_stable_method_schema_when_disabled(self) -> None:
        with (
            patch("maxmcp.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES),
            patch("maxmcp.tools.plugins._fetch_showclass_lines", return_value=[
                "tyFlow : GeometryClass {31322891,70f5b9ca}",
                "  .simResetMode : integer",
            ]),
        ):
            result = json.loads(inspect_plugin_class("tyFlow", include_methods=False))

        self.assertEqual(result["methods"], [])
        self.assertEqual(result["methodReflectionSupported"], False)

    def test_inspect_plugin_class_matches_runtime_names_with_underscores(self) -> None:
        railclone_classes = [
            {"name": "RailClone_Pro", "superclass": "GeometryClass", "category": "geometry", "plugin": "RailClone"},
        ]
        with (
            patch("maxmcp.tools.plugins._fetch_runtime_classes", return_value=railclone_classes),
            patch("maxmcp.tools.plugins._fetch_showclass_lines", return_value=[
                "RailClone_Pro(RailClone Pro) : GeometryClass {39712def,10a72959}",
                "  .spline : node",
            ]),
        ):
            result = json.loads(inspect_plugin_class("RailClone_Pro"))

        self.assertEqual(result["class"], "RailClone_Pro")
        self.assertEqual(result["properties"][0]["name"], "spline")

    def test_build_manifest_merges_overlay_and_runtime_data(self) -> None:
        with (
            patch("maxmcp.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES),
            patch("maxmcp.tools.plugins._get_scene_instance_counts", return_value={"tyFlow": 1, "tyMesher": 0}),
            patch("maxmcp.tools.plugins._fetch_showclass_lines", return_value=[
                "tyFlow : GeometryClass {31322891,70f5b9ca}",
                "  .simResetMode : integer",
                "  .allowCustomWirecolor : boolean",
            ]),
            patch("maxmcp.tools.plugins.get_plugin_capabilities", return_value='{"plugins":{"tyFlow":true}}'),
        ):
            manifest = _build_manifest("tyflow")

        self.assertEqual(manifest["plugin"], "tyFlow")
        self.assertEqual(manifest["installed"], True)
        self.assertEqual(manifest["manifestVersion"], 1)
        self.assertEqual(manifest["installation"]["status"], "confirmed")
        self.assertEqual(manifest["entryClassesExpected"], ["tyFlow"])
        self.assertEqual(manifest["entryClassesDetected"], ["tyFlow"])
        self.assertEqual(manifest["classes"]["tyFlow"]["reflection"]["sampled"], True)
        self.assertEqual(manifest["sceneInstancesCoverage"]["truncated"], False)
        self.assertIn("discover_plugin_surface", manifest["recommendedTools"])
        self.assertIn(
            "For existing flows, start with get_tyflow_info (enable include_flow_properties/include_event_properties/include_operator_properties as needed), then mutate.",
            manifest["recipes"],
        )

    def test_build_manifest_marks_partial_scene_instance_coverage(self) -> None:
        many_classes = [
            {"name": f"tyFlow{i:02d}", "superclass": "GeometryClass", "category": "geometry", "plugin": "tyFlow"}
            for i in range(25)
        ]
        scene_counts = {item["name"]: 0 for item in many_classes[:20]}
        with (
            patch("maxmcp.tools.plugins._fetch_runtime_classes", return_value=many_classes),
            patch("maxmcp.tools.plugins._get_scene_instance_counts", return_value=scene_counts),
            patch("maxmcp.tools.plugins._fetch_showclass_lines", return_value=[]),
            patch("maxmcp.tools.plugins.get_plugin_capabilities", return_value='{"plugins":{"tyFlow":true}}'),
        ):
            manifest = _build_manifest("tyflow")

        self.assertEqual(manifest["sceneInstancesCoverage"]["classesRequested"], 25)
        self.assertEqual(manifest["sceneInstancesCoverage"]["classesCounted"], 20)
        self.assertEqual(manifest["sceneInstancesCoverage"]["truncated"], True)
        self.assertTrue(any("sampled for the first 20" in warning for warning in manifest["warnings"]))

    def test_get_plugin_manifest_returns_json(self) -> None:
        with patch("maxmcp.tools.plugins._build_manifest", return_value={"plugin": "tyFlow", "installed": True}):
            result = json.loads(get_plugin_manifest("tyflow"))

        self.assertEqual(result["plugin"], "tyFlow")
        self.assertEqual(result["installed"], True)

    def test_plugin_guide_markdown_renders_sections(self) -> None:
        with patch("maxmcp.tools.plugins._build_manifest", return_value={
            "plugin": "tyFlow",
            "installed": True,
            "category": "mixed",
            "classCount": 6,
            "entryClasses": ["tyFlow"],
            "recommendedTools": ["discover_plugin_surface", "inspect_plugin_class"],
            "recipes": ["Inspect before editing."],
            "gotchas": ["Nested graphs need care."],
            "relatedClasses": ["tyFlow", "tyMesher"],
        }):
            guide = _plugin_guide_markdown("tyflow")

        self.assertIn("# tyFlow", guide)
        self.assertIn("## Recommended Tools", guide)
        self.assertIn("Nested graphs need care.", guide)

    def test_recipe_and_gotcha_markdown_render_lists(self) -> None:
        with patch("maxmcp.tools.plugins._build_manifest", return_value={
            "plugin": "tyFlow",
            "recipes": ["Create a basic flow."],
            "gotchas": ["Events are not easily rediscoverable after creation."],
        }):
            recipes = _plugin_recipe_markdown("tyflow")
            gotchas = _plugin_gotchas_markdown("tyflow")

        self.assertIn("# tyFlow Recipes", recipes)
        self.assertIn("Create a basic flow.", recipes)
        self.assertIn("# tyFlow Gotchas", gotchas)
        self.assertIn("rediscoverable", gotchas)

    def test_plugin_resources_delegate_to_tooling(self) -> None:
        with (
            patch("maxmcp.tools.plugins.discover_plugin_surface", return_value='{"plugins":[{"plugin":"tyFlow"}]}'),
            patch("maxmcp.tools.plugins.get_plugin_manifest", return_value='{"plugin":"tyFlow","installed":true}'),
            patch("maxmcp.tools.plugins._plugin_guide_markdown", return_value="# tyFlow"),
            patch("maxmcp.tools.plugins._plugin_recipe_markdown", return_value="# tyFlow Recipes"),
            patch("maxmcp.tools.plugins._plugin_gotchas_markdown", return_value="# tyFlow Gotchas"),
        ):
            index_payload = json.loads(plugin_index_resource())
            manifest_payload = json.loads(plugin_manifest_resource("tyflow"))
            guide_payload = plugin_guide_resource("tyflow")
            recipes_payload = plugin_recipes_resource("tyflow")
            gotchas_payload = plugin_gotchas_resource("tyflow")

        self.assertEqual(index_payload["plugins"][0]["plugin"], "tyFlow")
        self.assertEqual(manifest_payload["plugin"], "tyFlow")
        self.assertEqual(guide_payload, "# tyFlow")
        self.assertEqual(recipes_payload, "# tyFlow Recipes")
        self.assertEqual(gotchas_payload, "# tyFlow Gotchas")

    def test_inspect_plugin_instance_adds_manifest_for_detected_plugin(self) -> None:
        with (
            patch("maxmcp.tools.inspect.inspect_object", return_value='{"name":"Flow001","class":"tyFlow","baseObject":"tyFlow","modifiers":[],"material":null}'),
            patch("maxmcp.tools.inspect.inspect_properties", return_value='{"class":"tyFlow","propertyCount":2,"properties":[{"name":"simResetMode","value":"0","declaredType":"integer","runtimeType":"Integer"},{"name":"allowCustomWirecolor","value":"true","declaredType":"boolean","runtimeType":"BooleanClass"}]}'),
            patch("maxmcp.tools.plugins._build_manifest", return_value={"plugin": "tyFlow", "installed": True}),
        ):
            result = json.loads(inspect_plugin_instance("Flow001"))

        self.assertEqual(result["plugin"], "tyFlow")
        self.assertEqual(result["manifest"]["plugin"], "tyFlow")
        self.assertEqual(result["primaryPlugin"], "tyFlow")
        self.assertEqual(result["pluginsDetected"], ["tyFlow"])
        self.assertEqual(result["manifestsByPlugin"]["tyFlow"]["plugin"], "tyFlow")
        self.assertEqual(result["objectProperties"]["propertyCount"], 2)

    def test_inspect_plugin_instance_reports_multiple_plugin_sources(self) -> None:
        with (
            patch("maxmcp.tools.inspect.inspect_object", return_value='{"name":"Hybrid001","class":"tyFlow","baseObject":"tyFlow","modifiers":[{"class":"Forest_Pro"}],"material":{"class":"RailClone_Pro"}}'),
            patch("maxmcp.tools.inspect.inspect_properties", return_value='{"class":"tyFlow","propertyCount":1,"properties":[{"name":"simResetMode","value":"0","declaredType":"integer","runtimeType":"Integer"}]}'),
            patch("maxmcp.tools.plugins._build_manifest", side_effect=lambda plugin: {"plugin": plugin}),
        ):
            result = json.loads(inspect_plugin_instance("Hybrid001"))

        self.assertEqual(result["primaryPlugin"], "tyFlow")
        self.assertIn("Detected from the object's runtime class", result["primaryPluginReason"])
        self.assertEqual(result["pluginsDetected"], ["tyFlow", "RailClone", "Forest Pack"])
        self.assertEqual(sorted(result["manifestsByPlugin"].keys()), ["Forest Pack", "RailClone", "tyFlow"])


if __name__ == "__main__":
    unittest.main()
