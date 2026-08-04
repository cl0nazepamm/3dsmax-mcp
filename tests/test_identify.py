import json
import unittest
from pathlib import Path
from unittest.mock import patch

from maxmcp.max_client import MaxBridgeError
from maxmcp.tools import identify


class FakeClient:
    def __init__(self, native_available: bool, response: dict) -> None:
        self.native_available = native_available
        self.response = response
        self.calls: list[tuple[tuple, dict]] = []

    def send_command(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class IdentifyToolTests(unittest.TestCase):
    def test_isolate_and_capture_selected_uses_native_bridge(self) -> None:
        native_result = json.dumps(
            [
                {
                    "name": "Car",
                    "image_path": "C:/Temp/identify/Car.png",
                    "instances": ["Car", "Car002"],
                }
            ]
        )
        fake_client = FakeClient(
            native_available=True,
            response={"result": native_result},
        )

        with (
            patch.object(identify, "client", fake_client),
            patch.object(identify, "COMMS_DIR", "C:/Temp/3dsmax-mcp"),
        ):
            result = identify.isolate_and_capture_selected()

        self.assertEqual(result, native_result)
        self.assertEqual(len(fake_client.calls), 1)
        args, kwargs = fake_client.calls[0]
        self.assertEqual(
            json.loads(args[0]),
            {"capture_dir": "C:/Temp/3dsmax-mcp/identify"},
        )
        self.assertEqual(
            kwargs,
            {"cmd_type": "native:isolate_and_capture_selected"},
        )

    def test_isolate_and_capture_selected_keeps_maxscript_fallback(self) -> None:
        fake_client = FakeClient(
            native_available=False,
            response={"result": "[]"},
        )

        with (
            patch.object(identify, "client", fake_client),
            patch.object(identify, "COMMS_DIR", "C:/Temp/3dsmax-mcp"),
        ):
            result = identify.isolate_and_capture_selected()

        self.assertEqual(result, "[]")
        self.assertEqual(len(fake_client.calls), 1)
        args, kwargs = fake_client.calls[0]
        script = args[0]
        self.assertEqual(kwargs, {})
        self.assertIn("fn getRootParent", script)
        self.assertIn("r.baseObject == groupBaseObjs[g]", script)
        self.assertIn("max zoomext sel", script)
        self.assertIn("gw.getViewportDib()", script)
        self.assertIn(
            'makeDir "C:/Temp/3dsmax-mcp/identify" all:true',
            script,
        )

    def test_missing_native_route_falls_back_to_maxscript(self) -> None:
        error = "Unknown command type: native:isolate_and_capture_selected"
        fake_client = FakeClient(native_available=True, response={"result": "[]"})

        with (
            patch.object(identify, "client", fake_client),
            patch.object(identify, "COMMS_DIR", "C:/Temp/3dsmax-mcp"),
            patch.object(
                fake_client,
                "send_command",
                side_effect=[
                    MaxBridgeError(error, {"success": False, "error": error}),
                    {"result": "[]"},
                ],
            ) as mocked_send,
        ):
            result = identify.isolate_and_capture_selected()

        self.assertEqual(result, "[]")
        self.assertEqual(mocked_send.call_count, 2)
        self.assertEqual(
            mocked_send.call_args_list[0].kwargs["cmd_type"],
            "native:isolate_and_capture_selected",
        )
        self.assertEqual(mocked_send.call_args_list[1].kwargs, {})

    def test_native_capture_error_is_not_hidden_by_fallback(self) -> None:
        error = "Could not save viewport capture"
        bridge_error = MaxBridgeError(
            error,
            {"success": False, "error": error},
        )
        fake_client = FakeClient(native_available=True, response={"result": "[]"})

        with (
            patch.object(identify, "client", fake_client),
            patch.object(
                fake_client,
                "send_command",
                side_effect=bridge_error,
            ),
            self.assertRaises(MaxBridgeError) as raised,
        ):
            identify.isolate_and_capture_selected()

        self.assertIs(raised.exception, bridge_error)

    def test_native_capture_restores_state_and_disambiguates_filenames(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "src"
            / "handlers"
            / "identify_handlers.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("class IdentifyStateGuard", source)
        self.assertIn("UniqueCapturePath(", source)
        self.assertIn("NodeHandle(representative)", source)
        self.assertNotIn("RunMAXScript", source)


if __name__ == "__main__":
    unittest.main()
