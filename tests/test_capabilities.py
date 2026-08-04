import json
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

# Import the registration host first so capabilities is loaded through the same
# module order used by the real server (and not as a circular partial import).
from maxmcp import server as _server  # noqa: F401
from maxmcp.max_client import MaxBridgeError
from maxmcp.tools.capabilities import get_plugin_capabilities


class PluginCapabilitiesToolTests(unittest.TestCase):
    def test_native_bridge_is_preferred_and_result_is_preserved(self) -> None:
        expected = {
            "maxVersion": 2026,
            "renderer": "Arnold",
            "renderers": ["Default_Scanline_Renderer", "Arnold"],
            "plugins": {
                "forestPack": True,
                "forestLite": False,
                "tyFlow": True,
                "railClone": False,
                "phoenixFD": True,
            },
            "materialClasses": 83,
            "geometryClasses": 147,
            "modifierClasses": 212,
        }
        raw = json.dumps(expected)

        with (
            patch(
                "maxmcp.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "maxmcp.tools.capabilities.client.send_command",
                return_value={"result": raw},
            ) as mocked_send,
        ):
            result = get_plugin_capabilities()

        self.assertEqual(result, raw)
        self.assertEqual(json.loads(result), expected)
        mocked_send.assert_called_once_with(
            "{}",
            cmd_type="native:get_plugin_capabilities",
        )

    def test_maxscript_fallback_is_retained_without_native_bridge(self) -> None:
        raw = '{"maxVersion":2026,"renderer":"Arnold"}'
        with (
            patch(
                "maxmcp.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch(
                "maxmcp.tools.capabilities.client.send_command",
                return_value={"result": raw},
            ) as mocked_send,
        ):
            result = get_plugin_capabilities()

        self.assertEqual(result, raw)
        mocked_send.assert_called_once()
        script = mocked_send.call_args.args[0]
        self.assertIn("RendererClass.classes", script)
        self.assertIn("Material.classes.count", script)
        self.assertNotIn("cmd_type", mocked_send.call_args.kwargs)

    def test_missing_native_route_falls_back_to_maxscript(self) -> None:
        error = "Unknown command type: native:get_plugin_capabilities"
        with (
            patch(
                "maxmcp.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "maxmcp.tools.capabilities.client.send_command",
                side_effect=[
                    MaxBridgeError(error, {"success": False, "error": error}),
                    {"result": '{"maxVersion":2026}'},
                ],
            ) as mocked_send,
        ):
            result = get_plugin_capabilities()

        self.assertEqual(json.loads(result), {"maxVersion": 2026})
        self.assertEqual(mocked_send.call_count, 2)
        self.assertEqual(
            mocked_send.call_args_list[0].kwargs["cmd_type"],
            "native:get_plugin_capabilities",
        )
        self.assertNotIn("cmd_type", mocked_send.call_args_list[1].kwargs)

    def test_native_handler_error_is_not_hidden_by_fallback(self) -> None:
        error = "Renderer class directory is unavailable"
        bridge_error = MaxBridgeError(
            error,
            {"success": False, "error": error},
        )
        with (
            patch(
                "maxmcp.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "maxmcp.tools.capabilities.client.send_command",
                side_effect=bridge_error,
            ),
            self.assertRaises(MaxBridgeError) as raised,
        ):
            get_plugin_capabilities()

        self.assertIs(raised.exception, bridge_error)

    def test_native_handler_contains_no_maxscript_execution(self) -> None:
        source = (
            Path(__file__).parents[1]
            / "native"
            / "src"
            / "handlers"
            / "capabilities_handlers.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("DllDir::GetInstance()", source)
        self.assertIn("GetCurrentRenderer(false)", source)
        self.assertIn("RENDERER_CLASS_ID", source)
        self.assertIn("GetClassList(superClassId)", source)
        self.assertIn("GetFirst(accessType)", source)
        self.assertIn("GetNext(accessType)", source)
        self.assertIn("ACC_PUBLIC, ACC_PRIVATE", source)
        self.assertIn("MaxScriptVisibleClassName", source)
        self.assertIn("left.classId.PartA()", source)
        self.assertNotIn("superClass->get_classes", source)
        self.assertNotIn("MAXClass::classes[index]", source)
        self.assertNotIn("MAXClass::n_classes", source)
        self.assertNotIn("ExecuteMAXScriptScript", source)
        self.assertNotIn("RunMAXScript", source)


if __name__ == "__main__":
    unittest.main()
