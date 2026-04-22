import json
import unittest
from unittest.mock import patch

from src.tools.chat import chat_clear, chat_reload, chat_status, send_to_chat


class ChatToolTests(unittest.TestCase):
    def test_send_to_chat_normalizes_payload_and_meta(self) -> None:
        response = {
            "result": '{"reply":"Done","toolCalls":[{"name":"get_scene_info","arguments":{},"result":"{}"}],"model":"anthropic/test"}',
            "requestId": "chat-123",
            "meta": {"threadMode": "direct"},
        }

        with patch("src.tools.chat.client.send_command", return_value=response) as mocked_send:
            result = json.loads(send_to_chat("hello", timeout_ms=1234, silent=True))

        mocked_send.assert_called_once_with(
            json.dumps({
                "action": "send",
                "message": "hello",
                "timeout_ms": 1234,
                "silent": True,
            }),
            cmd_type="native:chat_ui",
        )
        self.assertEqual(result["reply"], "Done")
        self.assertEqual(result["model"], "anthropic/test")
        self.assertEqual(result["requestId"], "chat-123")
        self.assertEqual(result["meta"]["threadMode"], "direct")

    def test_send_to_chat_raises_for_nested_chat_error(self) -> None:
        response = {
            "result": '{"error":"Chat is busy - another turn is in progress."}',
            "requestId": "chat-err",
            "meta": {},
        }

        with patch("src.tools.chat.client.send_command", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "Chat error: Chat is busy"):
                send_to_chat("hello")

    def test_chat_status_and_clear_normalize_response(self) -> None:
        with (
            patch(
                "src.tools.chat.client.send_command",
                side_effect=[
                    {
                        "result": '{"visible":true,"configured":true,"processing":false,"conversationLength":2}',
                        "requestId": "status-1",
                        "meta": {"threadMode": "mainThread"},
                    },
                    {
                        "result": '{"cleared":true}',
                        "requestId": "clear-1",
                        "meta": {"threadMode": "mainThread"},
                    },
                ],
            ) as mocked_send,
        ):
            status = json.loads(chat_status())
            cleared = json.loads(chat_clear())

        self.assertEqual(mocked_send.call_count, 2)
        self.assertEqual(status["conversationLength"], 2)
        self.assertEqual(status["requestId"], "status-1")
        self.assertEqual(cleared["cleared"], True)
        self.assertEqual(cleared["requestId"], "clear-1")

    def test_chat_reload_normalizes_response(self) -> None:
        response = {
            "result": '{"configured":true,"model":"openai/gpt-5.4-mini","baseUrl":"https://openrouter.ai/api/v1"}',
            "requestId": "reload-1",
            "meta": {"threadMode": "mainThread"},
        }

        with patch("src.tools.chat.client.send_command", return_value=response) as mocked_send:
            result = json.loads(chat_reload())

        mocked_send.assert_called_once_with(
            json.dumps({"action": "reload"}),
            cmd_type="native:chat_ui",
        )
        self.assertEqual(result["configured"], True)
        self.assertEqual(result["model"], "openai/gpt-5.4-mini")
        self.assertEqual(result["requestId"], "reload-1")


if __name__ == "__main__":
    unittest.main()
