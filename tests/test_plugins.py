import json
import unittest
from unittest.mock import patch

from src.tools.plugins import (
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
        with patch("src.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES):
            result = json.loads(list_plugin_classes(plugin_name="tyflow", limit=1))

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["classes"][0]["name"], "tyFlow")
        self.assertEqual(result["truncated"], True)

    def test_discover_plugin_surface_summarizes_known_plugin(self) -> None:
        with (
            patch("src.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES),
            patch("src.tools.plugins._get_scene_instance_counts", return_value={"tyFlow": 2, "tyMesher": 0}),
            patch("src.tools.plugins.get_plugin_capabilities", return_value='{"maxVersion":2026,"renderer":"Arnold","plugins":{"tyFlow":true}}'),
        ):
            result = json.loads(discover_plugin_surface(plugin_name="tyflow"))

        self.assertEqual(result["plugin"], "tyFlow")
        self.assertEqual(result["installed"], True)
        self.assertEqual(result["sceneInstances"]["tyFlow"], 2)
        self.assertIn("geometry", result["families"])

    def test_inspect_plugin_class_uses_showclass_reflection(self) -> None:
        with (
            patch("src.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES),
            patch("src.tools.plugins._fetch_showclass_lines", return_value=[
                "tyFlow : GeometryClass {31322891,70f5b9ca}",
                "  .simResetMode : integer",
                "  .allowCustomWirecolor : boolean",
            ]),
        ):
            result = json.loads(inspect_plugin_class("tyFlow"))

        self.assertEqual(result["class"], "tyFlow")
        self.assertEqual(result["category"], "geometry")
        self.assertEqual(result["properties"][0]["name"], "simResetMode")
        self.assertTrue(any("Method reflection" in warning for warning in result["warnings"]))

    def test_build_manifest_merges_overlay_and_runtime_data(self) -> None:
        with (
            patch("src.tools.plugins._fetch_runtime_classes", return_value=RUNTIME_CLASSES),
            patch("src.tools.plugins._get_scene_instance_counts", return_value={"tyFlow": 1, "tyMesher": 0}),
            patch("src.tools.plugins.get_plugin_capabilities", return_value='{"plugins":{"tyFlow":true}}'),
        ):
            manifest = _build_manifest("tyflow")

        self.assertEqual(manifest["plugin"], "tyFlow")
        self.assertEqual(manifest["installed"], True)
        self.assertIn("discover_plugin_surface", manifest["recommendedTools"])
        self.assertIn("Create a flow object and inspect its current graph before editing.", manifest["recipes"])

    def test_get_plugin_manifest_returns_json(self) -> None:
        with patch("src.tools.plugins._build_manifest", return_value={"plugin": "tyFlow", "installed": True}):
            result = json.loads(get_plugin_manifest("tyflow"))

        self.assertEqual(result["plugin"], "tyFlow")
        self.assertEqual(result["installed"], True)

    def test_plugin_guide_markdown_renders_sections(self) -> None:
        with patch("src.tools.plugins._build_manifest", return_value={
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
        with patch("src.tools.plugins._build_manifest", return_value={
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
            patch("src.tools.plugins.discover_plugin_surface", return_value='{"plugins":[{"plugin":"tyFlow"}]}'),
            patch("src.tools.plugins.get_plugin_manifest", return_value='{"plugin":"tyFlow","installed":true}'),
            patch("src.tools.plugins._plugin_guide_markdown", return_value="# tyFlow"),
            patch("src.tools.plugins._plugin_recipe_markdown", return_value="# tyFlow Recipes"),
            patch("src.tools.plugins._plugin_gotchas_markdown", return_value="# tyFlow Gotchas"),
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
            patch("src.tools.inspect.inspect_object", return_value='{"name":"Flow001","class":"tyFlow","baseObject":"tyFlow","modifiers":[],"material":null}'),
            patch("src.tools.inspect.inspect_properties", return_value='{"class":"tyFlow","propertyCount":2,"properties":[{"name":"simResetMode","value":"0","declaredType":"integer","runtimeType":"Integer"},{"name":"allowCustomWirecolor","value":"true","declaredType":"boolean","runtimeType":"BooleanClass"}]}'),
            patch("src.tools.plugins._build_manifest", return_value={"plugin": "tyFlow", "installed": True}),
        ):
            result = json.loads(inspect_plugin_instance("Flow001"))

        self.assertEqual(result["plugin"], "tyFlow")
        self.assertEqual(result["manifest"]["plugin"], "tyFlow")
        self.assertEqual(result["objectProperties"]["propertyCount"], 2)


if __name__ == "__main__":
    unittest.main()
