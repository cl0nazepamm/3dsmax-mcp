import json
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from scripts.gen_tool_registry import extract_tools
from src.tools.objects import create_object


class CreateObjectToolTests(unittest.TestCase):
    def test_create_object_merges_structured_args_with_defaults(self) -> None:
        with (
            patch("src.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("src.tools.objects.client.send_command", return_value={"result": "Box001"}) as mocked_send,
        ):
            result = create_object("Box", pos=[10, 20, 30])

        self.assertEqual(result, "Box001")
        payload = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(payload["type"], "Box")
        self.assertIn("pos:[10,20,30]", payload["params"])
        self.assertIn("length:25", payload["params"])
        self.assertIn("width:25", payload["params"])
        self.assertIn("height:25", payload["params"])

    def test_create_object_keeps_explicit_size_and_backfills_missing_ones(self) -> None:
        with (
            patch("src.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("src.tools.objects.client.send_command", return_value={"result": "BoxWide"}) as mocked_send,
        ):
            create_object("Box", params="width:40")

        payload = json.loads(mocked_send.call_args.args[0])
        self.assertIn("width:40", payload["params"])
        self.assertNotIn("width:25", payload["params"])
        self.assertIn("length:25", payload["params"])
        self.assertIn("height:25", payload["params"])

    def test_tool_registry_exposes_structured_create_object_fields(self) -> None:
        tools = extract_tools(Path("src/tools/objects.py"))
        create_schema = next(t["schema"] for t in tools if t["name"] == "create_object")
        props = create_schema["properties"]

        self.assertEqual(props["type"]["type"], "string")
        self.assertEqual(props["params"]["type"], "string")
        self.assertEqual(props["pos"]["type"], "array")
        self.assertEqual(props["length"]["type"], "number")
        self.assertEqual(props["radius"]["type"], "number")


if __name__ == "__main__":
    unittest.main()
