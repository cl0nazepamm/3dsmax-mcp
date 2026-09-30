import ctypes
import json
import unittest
from unittest.mock import patch

from maxmcp.max_client import MaxBridgeError, MaxClient
from maxmcp.tool_response import envelope_exception, envelope_result


class ExecutorOutcomeTests(unittest.TestCase):
    def test_cancelled_task_is_distinct_and_retryable(self):
        error = json.dumps({"type": "QueueDeadlineError", "message": "Task cancelled before execution", "code": "TASK_CANCELLED", "retryable": True})
        result = envelope_exception(MaxBridgeError(error, {"error": error}), elapsed_ms=2)
        self.assertEqual(result["error"]["code"], "TASK_CANCELLED")
        self.assertTrue(result["error"]["retryable"])

    def test_partial_request_must_not_be_replayed(self):
        error = json.dumps({"type": "QueueDeadlineError", "message": "Earlier work completed", "code": "REQUEST_PARTIALLY_EXECUTED", "retryable": False})
        result = envelope_exception(MaxBridgeError(error, {"error": error}), elapsed_ms=2)
        self.assertEqual(result["error"]["code"], "REQUEST_PARTIALLY_EXECUTED")
        self.assertFalse(result["error"]["retryable"])

    def test_late_result_reaches_minimal_mcp_response(self):
        client = MaxClient(transport="pipe")
        response = b'{"success":true,"result":"actual value","meta":{"executionStatus":"completed_late"}}\n'
        with patch.object(client, "_send_via_pipe", return_value=response) as send:
            value = client.send_command("operation", timeout=2)
        self.assertEqual(json.loads(send.call_args.args[0])["timeoutMs"], 2000)
        self.assertEqual(client.get_last_transport()["execution_status"], "completed_late")
        with patch.dict("os.environ", {"MCP_TRIPBACK_MODE": "minimal"}):
            result = envelope_result(value["result"], elapsed_ms=3000, transport=client.get_last_transport())
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"], "actual value")
        self.assertEqual(result["warnings"][0]["code"], "COMPLETED_LATE")

    def test_late_error_preserves_failure_and_reports_completion(self):
        client = MaxClient(transport="pipe")
        response = b'{"success":false,"error":"actual failure","meta":{"executionStatus":"completed_late"}}\n'
        with patch.object(client, "_send_via_pipe", return_value=response):
            with self.assertRaises(MaxBridgeError) as raised:
                client.send_command("operation", timeout=2)
        with patch.dict("os.environ", {"MCP_TRIPBACK_MODE": "minimal"}):
            result = envelope_exception(raised.exception, elapsed_ms=3000, transport=client.get_last_transport())
        self.assertFalse(result["ok"])
        self.assertIn("actual failure", result["error"]["message"])
        self.assertEqual(result["warnings"][0]["code"], "COMPLETED_LATE")

    def test_late_fragmented_pipe_reply_is_not_abandoned(self):
        client = MaxClient(transport="pipe")
        chunks = iter([b'{"success":true,', b'"result":"late"}\n'])

        def write(handle, data, size, written, overlapped):
            written._obj.value = size
            return 1

        def read(handle, buf, size, read_count, overlapped):
            chunk = next(chunks)
            ctypes.memmove(buf, chunk, len(chunk))
            read_count._obj.value = len(chunk)
            return 1

        with (
            patch.object(client, "_resolve_pipe_name", return_value="test-pipe"),
            patch.object(client, "_ensure_pipe_handle", return_value=123),
            patch.object(client, "_close_pipe_handle"),
            patch("maxmcp.max_client._kernel32.WriteFile", side_effect=write),
            patch("maxmcp.max_client._kernel32.ReadFile", side_effect=read),
            patch("maxmcp.max_client.time.perf_counter", side_effect=[0, 0, 10, 10, 10, 10]),
        ):
            reply = client.send_command("operation", timeout=0.001)
        self.assertEqual(reply["result"], "late")


if __name__ == "__main__":
    unittest.main()
