import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from maxmcp.max_client import AmbiguousMaxInstanceError, MaxBridgeError, MaxClient


class MaxClientTests(unittest.TestCase):
    def test_send_command_uses_ascii_escaped_json_and_decodes_bom_response(self) -> None:
        fake_socket = MagicMock()
        fake_socket.recv.side_effect = [
            b'\xef\xbb\xbf{"success":true,"result":"ok","error":""}\n',
        ]

        with patch("maxmcp.max_client.socket.socket", return_value=fake_socket):
            client = MaxClient(timeout=1.0, transport="tcp")
            response = client.send_command('print("Merhaba ğüş")')

        self.assertEqual(response["result"], "ok")
        self.assertIn("requestId", response)
        self.assertIn("meta", response)
        self.assertIn("clientRoundTripMs", response["meta"])
        self.assertEqual(response["meta"]["transport"], "tcp")
        self.assertEqual(response["meta"]["requestedTransport"], "tcp")
        self.assertEqual(client.get_last_transport()["transport"], "tcp")
        sent = fake_socket.sendall.call_args.args[0]
        self.assertIn(b'"protocolVersion": 2', sent)
        self.assertIn(b'"requestId": "', sent)
        self.assertIn(b"\\u011f", sent)
        self.assertIn(b"\\u00fc", sent)
        self.assertTrue(sent.endswith(b"\n"))
        fake_socket.close.assert_called_once()

    def test_send_command_replaces_invalid_utf8_bytes(self) -> None:
        fake_socket = MagicMock()
        fake_socket.recv.side_effect = [
            b'{"success":true,"result":"bad\xff","error":""}\n',
        ]

        with patch("maxmcp.max_client.socket.socket", return_value=fake_socket):
            client = MaxClient(timeout=1.0, transport="tcp")
            response = client.send_command("x")

        self.assertEqual(response["result"], "bad\ufffd")

    def test_send_command_rejects_mismatched_request_id(self) -> None:
        fake_socket = MagicMock()
        fake_socket.recv.side_effect = [
            b'{"success":true,"requestId":"wrong","result":"ok","error":"","meta":{}}\n',
        ]

        with patch("maxmcp.max_client.socket.socket", return_value=fake_socket):
            client = MaxClient(timeout=1.0, transport="tcp")
            with self.assertRaisesRegex(RuntimeError, "Mismatched response requestId"):
                client.send_command("x")

    def test_bridge_error_is_not_runtime_error_fallback_signal(self) -> None:
        client = MaxClient(timeout=1.0, transport="tcp")
        payload = {
            "success": False,
            "requestId": "req",
            "error": '{"type":"NativeError","message":"Ambiguous","code":"AMBIGUOUS","retryable":false}',
            "meta": {},
        }

        with self.assertRaises(MaxBridgeError) as raised:
            client._parse_response(json.dumps(payload).encode("utf-8"), "req", 0.0)

        self.assertNotIsInstance(raised.exception, RuntimeError)
        self.assertEqual(raised.exception.bridge_response["error"], payload["error"])

    def test_resolve_pipe_uses_active_instance_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "3dsmax-mcp"
            config.mkdir()
            active = {
                "instance_id": "pid-111",
                "pid": 111,
                "pipe": r"\\.\pipe\3dsmax-mcp-pid-111",
            }
            (config / "active_instance.json").write_text(json.dumps(active), "utf-8")

            with (
                patch.dict("os.environ", {"LOCALAPPDATA": tmp}, clear=False),
                patch.object(MaxClient, "_probe_pipe_available", return_value=True),
            ):
                self.assertEqual(MaxClient()._resolve_pipe_name(), active["pipe"])

    def test_resolve_pipe_requires_claim_when_multiple_instances_are_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            instances = Path(tmp) / "3dsmax-mcp" / "instances"
            instances.mkdir(parents=True)
            for pid in (111, 222):
                data = {
                    "instance_id": f"pid-{pid}",
                    "pid": pid,
                    "pipe": fr"\\.\pipe\3dsmax-mcp-pid-{pid}",
                }
                (instances / f"pid-{pid}.json").write_text(json.dumps(data), "utf-8")

            with (
                patch.dict("os.environ", {"LOCALAPPDATA": tmp}, clear=False),
                patch.object(MaxClient, "_probe_pipe_available", return_value=True),
            ):
                with self.assertRaisesRegex(AmbiguousMaxInstanceError, "MCP Claim This Max"):
                    MaxClient()._resolve_pipe_name()


if __name__ == "__main__":
    unittest.main()
