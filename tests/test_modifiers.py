import json
import unittest
from unittest.mock import MagicMock, patch

from maxmcp.tools.modifiers import (
    _maxscript_property_value,
    _modifier_property_payload,
    set_modifier_property,
)


class ModifierPropertyTests(unittest.TestCase):
    def test_payload_single_object_by_index(self) -> None:
        payload = _modifier_property_payload(
            property_name="iterations",
            property_value="2",
            name="Box001",
            modifier_index=1,
        )
        self.assertEqual(payload["name"], "Box001")
        self.assertEqual(payload["modifier_index"], 1)
        self.assertNotIn("modifier_class", payload)

    def test_maxscript_value_literals(self) -> None:
        self.assertEqual(_maxscript_property_value("true"), "true")
        self.assertEqual(_maxscript_property_value("2.5"), "2.5")
        self.assertEqual(_maxscript_property_value("hello"), '"hello"')

    def test_native_payload_uses_set_modifier_property_cmd(self) -> None:
        mock_client = MagicMock()
        mock_client.native_available = True
        mock_client.send_command.return_value = {
            "result": json.dumps({"modified": 1, "property": "autosmooth", "value": "true", "hits": []}),
        }
        with patch("maxmcp.tools.modifiers.client", mock_client):
            result = set_modifier_property(
                name="Box001",
                modifier_index=1,
                property_name="autosmooth",
                property_value="true",
            )
        mock_client.send_command.assert_called_once()
        cmd_type = mock_client.send_command.call_args.kwargs.get("cmd_type")
        self.assertEqual(cmd_type, "native:set_modifier_property")
        payload = json.loads(mock_client.send_command.call_args.args[0])
        self.assertEqual(payload["name"], "Box001")
        self.assertEqual(payload["modifier_index"], 1)
        self.assertEqual(json.loads(result)["modified"], 1)


if __name__ == "__main__":
    unittest.main()
