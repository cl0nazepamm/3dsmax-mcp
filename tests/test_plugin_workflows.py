import json
import unittest
from unittest.mock import patch

from src.tools.plugin_workflows import (
    _birth_settings_expr,
    _execute_tyflow_recipe,
    create_tyflow_basic_verified,
    create_tyflow_scatter_from_objects_verified,
)


class PluginWorkflowTests(unittest.TestCase):
    def test_birth_settings_expr_total_mode(self) -> None:
        expr = _birth_settings_expr("total", 150)
        self.assertIn("birthMode = 0", expr)
        self.assertIn("birthTotal = 150", expr)

    def test_birth_settings_expr_per_frame_mode(self) -> None:
        expr = _birth_settings_expr("per_frame", 12)
        self.assertIn("birthMode = 1", expr)
        self.assertIn("birthPerFrame = 12", expr)

    def test_execute_tyflow_recipe_returns_json_payload(self) -> None:
        with patch("src.tools.plugin_workflows.client.send_command", return_value={
            "result": '{"recipe":"basic_birth","flow":"Flow001","event":{"name":"Emit","position":[0,0]},"operators":[{"type":"Birth","name":"Birth"}],"missingSourceNames":[]}'
        }) as mocked_send:
            result = _execute_tyflow_recipe(
                flow_name="Flow001",
                position_expr="[0.0,0.0,0.0]",
                event_name="Emit",
                event_position_expr="[0,0]",
                birth_settings_expr=_birth_settings_expr("total", 100),
                source_names=[],
                recipe_name="basic_birth",
            )

        self.assertEqual(result["flow"], "Flow001")
        self.assertEqual(result["event"]["name"], "Emit")
        self.assertEqual(result["operators"][0]["type"], "Birth")
        mocked_send.assert_called_once()

    def test_create_tyflow_basic_verified_returns_action_and_readback(self) -> None:
        with (
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"added":[{"name":"Flow001"}]}']),
            patch("src.tools.plugin_workflows._execute_tyflow_recipe", return_value={
                "recipe": "basic_birth",
                "flow": "Flow001",
                "event": {"name": "Emit", "position": [0, 0]},
                "operators": [{"type": "Birth", "name": "Birth"}],
                "missingSourceNames": [],
            }),
            patch("src.tools.selection.select_objects", return_value="Selected 1 of 1 objects"),
            patch("src.tools.plugin_workflows._inspected_plugin_payload", return_value=(
                {"name": "Flow001", "class": "tyFlow"},
                {"plugin": "tyFlow"},
            )),
        ):
            result = json.loads(create_tyflow_basic_verified(name="Flow001"))

        self.assertEqual(result["recipe"], "tyflow_basic_birth")
        self.assertEqual(result["createResult"]["flow"], "Flow001")
        self.assertEqual(result["object"]["class"], "tyFlow")
        self.assertEqual(result["plugin"]["plugin"], "tyFlow")

    def test_create_tyflow_scatter_verified_returns_missing_sources(self) -> None:
        with (
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"added":[{"name":"Scatter001"}]}']),
            patch("src.tools.plugin_workflows._execute_tyflow_recipe", return_value={
                "recipe": "scatter_from_objects",
                "flow": "Scatter001",
                "event": {"name": "Scatter", "position": [0, 0]},
                "operators": [
                    {"type": "Birth", "name": "Birth"},
                    {"type": "Position Object", "name": "Position Object", "objectCount": 1},
                ],
                "missingSourceNames": ["MissingBox"],
            }),
            patch("src.tools.selection.select_objects", return_value="Selected 1 of 1 objects"),
            patch("src.tools.plugin_workflows._inspected_plugin_payload", return_value=(
                {"name": "Scatter001", "class": "tyFlow"},
                {"plugin": "tyFlow"},
            )),
        ):
            result = json.loads(create_tyflow_scatter_from_objects_verified(["Box001", "MissingBox"], flow_name="Scatter001"))

        self.assertEqual(result["recipe"], "tyflow_scatter_from_objects")
        self.assertEqual(result["createResult"]["missingSourceNames"], ["MissingBox"])
        self.assertEqual(result["createResult"]["operators"][1]["type"], "Position Object")


if __name__ == "__main__":
    unittest.main()
