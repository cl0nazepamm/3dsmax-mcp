import json
import unittest
from unittest.mock import MagicMock, patch

from maxmcp.tools.data_channel import (
    _format_add_result,
    _mxs_value,
    _operator_lines,
    add_data_channel,
    add_dc_script_operator,
    inspect_data_channel,
    list_dc_operators,
    list_dc_presets,
    load_dc_preset,
    manage_data_channel_stack,
    set_data_channel_operator,
)


class DataChannelTests(unittest.TestCase):
    def test_empty_operators_returns_error(self) -> None:
        result = json.loads(add_data_channel(name="Box001", operators=[]))
        self.assertIn("error", result)

    def test_format_add_result_includes_active_and_storage_counts(self) -> None:
        payload = json.loads(
            _format_add_result("OK|1|1|2|2|4|#(2,3)||Box001")
        )
        self.assertTrue(payload["createdModifier"])
        self.assertEqual(payload["operatorsAdded"], 2)
        self.assertEqual(payload["operatorsTotal"], 2)
        self.assertEqual(payload["operatorStorageCount"], 4)

    def test_operator_lines_use_live_catalog_and_safe_property_assignment(self) -> None:
        lines = _operator_lines([
            {"type": "vertex_output", "params": {"output": 4, "channelNum": 1}},
        ])
        self.assertIn('mcpDcAddOperatorByName dcIF "Vertex Output" -1', lines[0])
        self.assertTrue(any("isProperty" in line and "#output" in line for line in lines))
        self.assertTrue(any("setProperty" in line and "#channelNum" in line for line in lines))
        self.assertFalse(any(".output =" in line for line in lines))

    def test_operator_lines_use_live_blend_values_and_default_first_input_to_replace(self) -> None:
        explicit = _operator_lines([{"type": "vertex_input", "blend": "replace"}])
        self.assertIn("dcMod.operator_ops[beforeStorageCount + 1] = 1", explicit)

        implicit = _operator_lines([{"type": "vertex_input"}])
        self.assertIn(
            "if beforeStackCount == 0 do dcMod.operator_ops[beforeStorageCount + 1] = 1",
            implicit,
        )

    def test_operator_lines_reject_property_name_injection(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid Data Channel operator property"):
            _operator_lines([{"type": "scale", "params": {"scale;delete objects": 2}}])

    def test_structured_value_serialization(self) -> None:
        self.assertEqual(_mxs_value({"type": "point3", "value": [1, 2, 3]}), "[1.0,2.0,3.0]")
        self.assertEqual(_mxs_value({"type": "color", "value": [1, 2, 3]}), "(color 1.0 2.0 3.0)")
        self.assertEqual(_mxs_value([1, True, "x"]), '#(1,true,"x")')
        self.assertIn("mcpDcRequireNode", _mxs_value("Target", property_name="node"))

    def test_add_preflights_and_reuses_existing_modifier(self) -> None:
        mock_client = MagicMock()
        sent: list[str] = []

        def capture(script: str, **_kwargs: object) -> dict:
            sent.append(script)
            return {"result": "OK|0|1|1|3|3|#(0,1,2)|no output|Box001"}

        mock_client.send_command = capture
        with patch("maxmcp.tools.data_channel.client", mock_client):
            payload = json.loads(add_data_channel(
                name="Box001",
                operators=[{"type": "smooth", "params": {"iteration": 2}}],
                expected_operator_count=2,
            ))
        self.assertEqual(payload["operatorsTotal"], 3)
        self.assertEqual(len(sent), 1)
        script = sent[0]
        self.assertIn("local probeMod = DataChannelModifier()", script)
        self.assertIn("beforeStorageCount = dcMod.operators.count", script)
        self.assertIn("if beforeStackCount != 2", script)
        self.assertIn('mcpDcAddOperatorByName dcIF "Smooth" -1', script)

    def test_executable_operator_is_blocked_by_default(self) -> None:
        mock_client = MagicMock()
        with patch("maxmcp.tools.data_channel.client", mock_client):
            payload = json.loads(add_data_channel(
                name="Box001",
                operators=[{"type": "maxscript_process"}],
            ))
        self.assertIn("blocked by default", payload["error"])
        mock_client.send_command.assert_not_called()

    def test_executable_operator_requires_safe_mode_off(self) -> None:
        mock_client = MagicMock()
        mock_client.send_command.return_value = {"result": "true", "meta": {"safeMode": True}}
        with patch("maxmcp.tools.data_channel.client", mock_client):
            payload = json.loads(add_data_channel(
                name="Box001",
                operators=[{"type": "expression_float"}],
                allow_executable=True,
            ))
        self.assertIn("safe_mode", payload["error"])
        self.assertEqual(mock_client.send_command.call_count, 1)

    def test_list_dc_operators_adds_roles_and_aliases(self) -> None:
        mock_client = MagicMock()
        mock_client.send_command.return_value = {
            "result": json.dumps({
                "catalogCount": 32,
                "count": 1,
                "operators": [{"name": "Vertex Input", "classId": ["1L", "0L"]}],
            })
        }
        with patch("maxmcp.tools.data_channel.client", mock_client):
            payload = json.loads(list_dc_operators(query="vertex", include_properties=True))
        self.assertEqual(payload["operators"][0]["role"], "input")
        self.assertIn("vertex_input", payload["operators"][0]["aliases"])
        script = mock_client.send_command.call_args.args[0]
        self.assertIn("NumberOperators()", script)
        self.assertIn("getPropNames op", script)
        self.assertNotIn("addModifier", script)

    def test_inspection_uses_active_storage_order_and_is_compact_by_default(self) -> None:
        mock_client = MagicMock()
        mock_client.send_command.return_value = {
            "result": json.dumps({
                "object": "Box001",
                "modifierStackIndex": 1,
                "operatorCount": 2,
                "operatorStorageCount": 4,
                "storageOrder": [2, 3],
                "uiActive": False,
                "uiErrorText": "",
                "operators": [
                    {"operatorIndex": 1, "storageIndex": 3, "name": "Vertex Input", "blend": 1},
                    {"operatorIndex": 2, "storageIndex": 4, "name": "Vertex Output", "blend": 0},
                ],
            })
        }
        with patch("maxmcp.tools.data_channel.client", mock_client):
            payload = json.loads(inspect_data_channel(name="Box001"))
        self.assertEqual(payload["operatorStorageCount"], 4)
        self.assertEqual(payload["operators"][0]["operatorIndex"], 1)
        self.assertEqual(payload["operators"][0]["blendName"], "replace")
        self.assertEqual(payload["validation"]["status"], "structurally_valid")
        self.assertIsNone(payload["validation"]["valid"])
        self.assertEqual(payload["warnings"], [])
        script = mock_client.send_command.call_args.args[0]
        self.assertIn("dcMod.operator_order[stackPosition] + 1", script)
        self.assertIn("windows.getChildrenHWND #max", script)
        self.assertIn('windowClass == "CustStatus"', script)
        self.assertNotIn("local props = getPropNames op", script)

    def test_inspection_returns_live_data_channel_error_text(self) -> None:
        mock_client = MagicMock()
        error_text = "Error : First operator must be an Input Operator and in Replace"
        mock_client.send_command.return_value = {
            "result": json.dumps({
                "object": "Box001",
                "modifierStackIndex": 1,
                "operatorCount": 2,
                "operatorStorageCount": 2,
                "storageOrder": [0, 1],
                "uiActive": True,
                "uiErrorText": error_text,
                "operators": [
                    {"operatorIndex": 1, "storageIndex": 1, "name": "Vertex Input", "blend": 0},
                    {"operatorIndex": 2, "storageIndex": 2, "name": "Vertex Output", "blend": 0},
                ],
            })
        }
        with patch("maxmcp.tools.data_channel.client", mock_client):
            payload = json.loads(inspect_data_channel(name="Box001"))
        self.assertEqual(payload["operators"][0]["blendName"], "none")
        self.assertEqual(payload["validation"]["status"], "invalid")
        self.assertFalse(payload["validation"]["valid"])
        self.assertEqual(payload["validation"]["errorText"], error_text)
        self.assertEqual(payload["validation"]["source"], "ui")
        self.assertIn(error_text, payload["warnings"])

    def test_manage_reorder_maps_visible_positions_to_storage_order(self) -> None:
        mock_client = MagicMock()
        mock_client.send_command.return_value = {"result": '{"operatorCount":3}'}
        with patch("maxmcp.tools.data_channel.client", mock_client):
            manage_data_channel_stack(
                name="Box001",
                action="reorder",
                order=[3, 1, 2],
                expected_operator_count=3,
            )
        script = mock_client.send_command.call_args.args[0]
        self.assertIn("requestedPositions = #(3,1,2)", script)
        self.assertIn("oldOrder[position]", script)
        self.assertIn("if activeCount != 3", script)

    def test_manage_reorder_rejects_duplicates_before_max(self) -> None:
        mock_client = MagicMock()
        with patch("maxmcp.tools.data_channel.client", mock_client):
            payload = json.loads(manage_data_channel_stack(
                name="Box001", action="reorder", order=[1, 1]
            ))
        self.assertIn("duplicates", payload["error"])
        mock_client.send_command.assert_not_called()

    def test_manage_delete_uses_visible_one_based_index(self) -> None:
        mock_client = MagicMock()
        mock_client.send_command.return_value = {"result": '{"operatorCount":1}'}
        with patch("maxmcp.tools.data_channel.client", mock_client):
            manage_data_channel_stack(name="Box001", action="delete", operator_index=2)
        script = mock_client.send_command.call_args.args[0]
        self.assertIn("dcIF.DeleteStackOperator 2", script)
        self.assertIn("dcMod.operator_order[2] + 1", script)

    def test_set_operator_uses_visible_stack_index_and_rollback(self) -> None:
        mock_client = MagicMock()
        mock_client.send_command.return_value = {"result": '{"updatedProperties":["scale"]}'}
        with patch("maxmcp.tools.data_channel.client", mock_client):
            set_data_channel_operator(
                name="Box001", operator_index=2, params={"scale": 3.0}
            )
        script = mock_client.send_command.call_args.args[0]
        self.assertIn("dcMod.operator_order[2] + 1", script)
        self.assertIn("append oldValues", script)
        self.assertIn("setProperty op #scale oldValues[1]", script)

    def test_script_operator_requires_explicit_authorization(self) -> None:
        payload = json.loads(add_dc_script_operator(name="Box001", script="return 1"))
        self.assertIn("blocked by default", payload["error"])

    def test_list_presets_does_not_create_scene_geometry(self) -> None:
        mock_client = MagicMock()
        mock_client.send_command.return_value = {"result": "[]"}
        with patch("maxmcp.tools.data_channel.client", mock_client):
            list_dc_presets()
        script = mock_client.send_command.call_args.args[0]
        self.assertNotIn("Box name:", script)
        self.assertNotIn("addModifier", script)
        self.assertNotIn("delete b", script)

    def test_load_preset_has_optimistic_count_guard(self) -> None:
        mock_client = MagicMock()
        mock_client.send_command.return_value = {"result": '{"preset":"Dirt Map"}'}
        with patch("maxmcp.tools.data_channel.client", mock_client):
            load_dc_preset(
                name="Box001", preset_name="Dirt Map", expected_operator_count=2
            )
        script = mock_client.send_command.call_args.args[0]
        self.assertIn("if beforeStackCount != 2", script)


if __name__ == "__main__":
    unittest.main()
