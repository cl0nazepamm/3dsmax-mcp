import json
import unittest
from unittest.mock import patch

from maxmcp.tools.bridge import get_bridge_status


class BridgeToolTests(unittest.TestCase):
    def test_get_bridge_status_merges_result_and_transport_meta(self) -> None:
        response = {
            "result": '{"pong": true, "server": "3dsmax-mcp"}',
            "requestId": "abc123",
            "meta": {"protocolVersion": 2, "clientRoundTripMs": 1.5},
        }
        with patch("maxmcp.tools.bridge.client.send_command", return_value=response) as mocked_send:
            result = json.loads(get_bridge_status())

        mocked_send.assert_called_once_with("", cmd_type="ping", timeout=5.0)
        self.assertEqual(result["pong"], True)
        self.assertEqual(result["server"], "3dsmax-mcp")
        self.assertEqual(result["requestId"], "abc123")
        self.assertEqual(result["meta"]["protocolVersion"], 2)
        self.assertEqual(result["connected"], True)
        self.assertEqual(result["legacyTransport"], False)

    def test_get_bridge_status_falls_back_for_legacy_listener(self) -> None:
        with (
            patch("maxmcp.tools.bridge.client.send_command", side_effect=[
                RuntimeError("MAXScript error: Empty command"),
                {"result": '{"pong": true, "server": "3dsmax-mcp"}', "requestId": None, "meta": {}},
            ]) as mocked_send,
        ):
            result = json.loads(get_bridge_status())

        self.assertEqual(mocked_send.call_count, 2)
        self.assertEqual(result["legacyTransport"], True)
        self.assertEqual(result["connected"], True)


if __name__ == "__main__":
    unittest.main()
