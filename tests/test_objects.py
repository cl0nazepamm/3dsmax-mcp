import json
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from scripts.gen_tool_registry import extract_tools
from maxmcp.helpers.spatial import normalize_pos_mode, type_axis_hints
from maxmcp.tools.objects import create_object


class CreateObjectToolTests(unittest.TestCase):
    def test_create_object_merges_structured_args_with_defaults(self) -> None:
        spatial_result = {
            "name": "Box001",
            "class": "Box",
            "type": "Box",
            "placement": {"pos_mode": "ground"},
            "bbox": {"min": [-12.5, -12.5, 0], "max": [12.5, 12.5, 25]},
        }
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch(
                "maxmcp.tools.objects.client.send_command",
                return_value={"result": json.dumps(spatial_result)},
            ) as mocked_send,
        ):
            result = create_object("Box", pos=[10, 20, 30])

        payload = json.loads(result)
        self.assertEqual(payload["name"], "Box001")
        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["type"], "Box")
        self.assertEqual(sent["pos_mode"], "ground")
        self.assertIn("pos:[10,20,30]", sent["params"])
        self.assertIn("length:25", sent["params"])

    def test_create_object_defaults_to_ground_pos_mode(self) -> None:
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch(
                "maxmcp.tools.objects.client.send_command",
                return_value={"result": json.dumps({"name": "Box001", "type": "box"})},
            ) as mocked_send,
        ):
            create_object("box", length=10, width=8, height=5)

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["pos_mode"], "ground")

    def test_create_object_keeps_explicit_size_and_backfills_missing_ones(self) -> None:
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch(
                "maxmcp.tools.objects.client.send_command",
                return_value={"result": json.dumps({"name": "BoxWide", "type": "box"})},
            ) as mocked_send,
        ):
            create_object("Box", params="width:40")

        payload = json.loads(mocked_send.call_args.args[0])
        self.assertIn("width:40", payload["params"])
        self.assertNotIn("width:25", payload["params"])
        self.assertIn("length:25", payload["params"])
        self.assertIn("height:25", payload["params"])

    def test_tool_registry_exposes_structured_create_object_fields(self) -> None:
        tools = extract_tools(Path("maxmcp/tools/objects.py"))
        create_schema = next(t["schema"] for t in tools if t["name"] == "create_object")
        props = create_schema["properties"]

        self.assertEqual(props["type"]["type"], "string")
        self.assertEqual(props["params"]["type"], "string")
        self.assertEqual(props["pos"]["type"], "array")
        self.assertEqual(props["pos_mode"]["type"], "string")
        self.assertEqual(props["length"]["type"], "number")
        self.assertEqual(props["radius"]["type"], "number")


class SpatialHelperTests(unittest.TestCase):
    def test_normalize_pos_mode(self) -> None:
        self.assertEqual(normalize_pos_mode(None), "ground")
        self.assertEqual(normalize_pos_mode("center"), "center")
        self.assertEqual(normalize_pos_mode("bbox_center"), "center")
        self.assertEqual(normalize_pos_mode("pivot"), "pivot")

    def test_type_axis_hints_for_box(self) -> None:
        hints = type_axis_hints("box")
        self.assertEqual(hints["width"], "X")
        self.assertEqual(hints["length"], "Y")
        self.assertEqual(hints["height"], "Z")


if __name__ == "__main__":
    unittest.main()
