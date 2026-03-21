import unittest
from unittest.mock import MagicMock, patch

from src.max_client import MaxClient


class MaxClientTests(unittest.TestCase):
    def test_send_command_uses_ascii_escaped_json_and_decodes_bom_response(self) -> None:
        fake_socket = MagicMock()
        fake_socket.recv.side_effect = [
            b'\xef\xbb\xbf{"success":true,"result":"ok","error":""}\n',
        ]

        with patch("src.max_client.socket.socket", return_value=fake_socket):
            client = MaxClient(timeout=1.0, transport="tcp")
            response = client.send_command('print("Merhaba ğüş")')

        self.assertEqual(response["result"], "ok")
        self.assertIn("requestId", response)
        self.assertIn("meta", response)
        self.assertIn("clientRoundTripMs", response["meta"])
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

        with patch("src.max_client.socket.socket", return_value=fake_socket):
            client = MaxClient(timeout=1.0, transport="tcp")
            response = client.send_command("x")

        self.assertEqual(response["result"], "bad\ufffd")

    def test_send_command_rejects_mismatched_request_id(self) -> None:
        fake_socket = MagicMock()
        fake_socket.recv.side_effect = [
            b'{"success":true,"requestId":"wrong","result":"ok","error":"","meta":{}}\n',
        ]

        with patch("src.max_client.socket.socket", return_value=fake_socket):
            client = MaxClient(timeout=1.0, transport="tcp")
            with self.assertRaisesRegex(RuntimeError, "Mismatched response requestId"):
                client.send_command("x")


if __name__ == "__main__":
    unittest.main()
