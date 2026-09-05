import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from scripts.gen_tool_registry import extract_tools
from maxmcp.max_client import MaxBridgeError
from maxmcp.tools.materials import backup_material_library, get_material_library


class MaterialLibraryToolTests(unittest.TestCase):
    def test_get_material_library_uses_native_route_when_available(self) -> None:
        with (
            patch(
                "maxmcp.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "maxmcp.tools.materials.client.send_command",
                return_value={"result": '{"source":"current"}'},
            ) as mocked_send,
        ):
            result = get_material_library(
                source="temporary",
                include_empty_slots=True,
            )

        self.assertEqual(json.loads(result), {"source": "current"})
        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(
            sent,
            {"source": "current", "include_empty_slots": True},
        )
        self.assertEqual(
            mocked_send.call_args.kwargs["cmd_type"],
            "native:get_material_library",
        )

    def test_get_material_library_reads_temporary_library_alias(self) -> None:
        with (
            patch(
                "maxmcp.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch(
                "maxmcp.tools.materials.client.send_command",
                return_value={"result": '{"source":"current"}'},
            ) as mocked_send,
        ):
            result = get_material_library(source="temporary")

        self.assertEqual(json.loads(result), {"source": "current"})
        script = mocked_send.call_args.args[0]
        self.assertIn('local requestedSource = "current"', script)
        self.assertIn("currentMaterialLibrary", script)
        self.assertEqual(mocked_send.call_args.kwargs["cmd_type"], "maxscript")

    def test_get_material_library_falls_back_on_unknown_native_runtime_error(self) -> None:
        with (
            patch(
                "maxmcp.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "maxmcp.tools.materials.client.send_command",
                side_effect=[
                    RuntimeError("Unknown native command"),
                    {"result": '{"source":"medit"}'},
                ],
            ) as mocked_send,
        ):
            result = get_material_library(source="medit")

        self.assertEqual(json.loads(result), {"source": "medit"})
        self.assertEqual(mocked_send.call_count, 2)
        self.assertEqual(
            mocked_send.call_args_list[0].kwargs["cmd_type"],
            "native:get_material_library",
        )
        self.assertEqual(
            mocked_send.call_args_list[1].kwargs["cmd_type"],
            "maxscript",
        )

    def test_get_material_library_falls_back_on_missing_native_route(self) -> None:
        error = "Unknown command type: native:get_material_library"
        with (
            patch(
                "maxmcp.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "maxmcp.tools.materials.client.send_command",
                side_effect=[
                    MaxBridgeError(error, {"success": False, "error": error}),
                    {"result": '{"source":"current"}'},
                ],
            ) as mocked_send,
        ):
            result = get_material_library(source="current")

        self.assertEqual(json.loads(result), {"source": "current"})
        self.assertEqual(mocked_send.call_count, 2)

    def test_get_material_library_propagates_native_handler_error(self) -> None:
        error = "Material library is unavailable"
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
                "maxmcp.tools.materials.client.send_command",
                side_effect=bridge_error,
            ),
            self.assertRaises(MaxBridgeError) as raised,
        ):
            get_material_library(source="current")

        self.assertIs(raised.exception, bridge_error)

    def test_get_material_library_rejects_unknown_source(self) -> None:
        payload = json.loads(get_material_library(source="unknown"))

        self.assertIn("source must be one of", payload["error"])

    def test_backup_material_library_rejects_file_path_for_all_sources(self) -> None:
        payload = json.loads(
            backup_material_library(source="all", file_path=r"C:\tmp\materials.mat")
        )

        self.assertIn("file_path can only be used", payload["error"])

    def test_backup_material_library_uses_exact_path_for_single_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exact_path = Path(tmp) / "nested" / "combined.mat"
            with (
                patch(
                    "maxmcp.max_client.MaxClient.native_available",
                    new_callable=PropertyMock,
                    return_value=False,
                ),
                patch(
                    "maxmcp.tools.materials.client.send_command",
                    return_value={"result": '{"source":"combined","saved":[]}'},
                ) as mocked_send,
            ):
                result = backup_material_library(
                    source="combined",
                    file_path=str(exact_path),
                )

            self.assertEqual(json.loads(result), {"source": "combined", "saved": []})
            self.assertTrue(exact_path.parent.exists())
            script = mocked_send.call_args.args[0]
            self.assertIn('local requestedSource = "combined"', script)
            self.assertIn(str(exact_path).replace("\\", "/"), script)
            self.assertEqual(mocked_send.call_args.kwargs["cmd_type"], "maxscript")
            self.assertEqual(mocked_send.call_args.kwargs["timeout"], 45.0)

    def test_backup_material_library_uses_native_route_and_exact_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exact_path = Path(tmp) / "nested" / "medit.mat"
            with (
                patch(
                    "maxmcp.max_client.MaxClient.native_available",
                    new_callable=PropertyMock,
                    return_value=True,
                ),
                patch(
                    "maxmcp.tools.materials.client.send_command",
                    return_value={"result": '{"source":"medit","saved":[]}'},
                ) as mocked_send,
            ):
                result = backup_material_library(
                    source="material_editor",
                    file_path=str(exact_path),
                )

        self.assertEqual(json.loads(result), {"source": "medit", "saved": []})
        sent = json.loads(mocked_send.call_args.args[0])
        expected_path = str(exact_path).replace("\\", "/")
        self.assertEqual(sent["source"], "medit")
        self.assertEqual(sent["current_path"], expected_path)
        self.assertEqual(sent["medit_path"], expected_path)
        self.assertEqual(sent["combined_path"], expected_path)
        self.assertEqual(
            mocked_send.call_args.kwargs["cmd_type"],
            "native:backup_material_library",
        )
        self.assertEqual(mocked_send.call_args.kwargs["timeout"], 45.0)

    def test_backup_material_library_falls_back_on_missing_native_route(self) -> None:
        error = "Unknown command type: native:backup_material_library"
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "maxmcp.max_client.MaxClient.native_available",
                    new_callable=PropertyMock,
                    return_value=True,
                ),
                patch(
                    "maxmcp.tools.materials.client.send_command",
                    side_effect=[
                        MaxBridgeError(error, {"success": False, "error": error}),
                        {"result": '{"source":"current","saved":[]}'},
                    ],
                ) as mocked_send,
            ):
                result = backup_material_library(
                    source="current",
                    backup_dir=tmp,
                )

        self.assertEqual(
            json.loads(result),
            {"source": "current", "saved": []},
        )
        self.assertEqual(mocked_send.call_count, 2)
        self.assertEqual(
            mocked_send.call_args_list[1].kwargs["cmd_type"],
            "maxscript",
        )

    def test_backup_material_library_flags_failed_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failed_result = {
                "source": "current",
                "saved": [
                    {
                        "source": "current",
                        "path": str(Path(tmp) / "current.mat"),
                        "count": 1,
                        "saved": False,
                        "skipped": False,
                        "error": "disk denied",
                    }
                ],
            }
            with (
                patch(
                    "maxmcp.max_client.MaxClient.native_available",
                    new_callable=PropertyMock,
                    return_value=True,
                ),
                patch(
                    "maxmcp.tools.materials.client.send_command",
                    return_value={"result": json.dumps(failed_result)},
                ),
            ):
                result = backup_material_library(source="current", backup_dir=tmp)

        payload = json.loads(result)
        self.assertEqual(payload["status"], "error")
        self.assertIn("backups failed", payload["error"])

    def test_native_material_library_handler_uses_sdk_without_maxscript(self) -> None:
        source_path = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "src"
            / "handlers"
            / "material_library_handlers.cpp"
        )
        source = source_path.read_text(encoding="utf-8")

        self.assertIn("GetMaterialLibrary()", source)
        self.assertIn("GetMtlSlot(", source)
        self.assertIn("SaveMaterialLib(", source)
        self.assertIn("MtlBaseLib", source)
        self.assertIn(
            '{"class", MaxScriptVisibleClassName(material)}',
            source,
        )
        self.assertIn('return "material";', source)
        self.assertIn('return "textureMap";', source)
        self.assertIn("remap.Get().Backpatch();", source)
        self.assertIn("AddMaterialClone(*library, current[i]);", source)
        self.assertIn('payload.value("backup_dir", "")', source)
        self.assertIn('payload.value("file_path", "")', source)
        self.assertIn("one or more material library backups failed", source)
        self.assertNotIn("RunMAXScript", source)

    def test_native_registry_backup_schema_matches_native_public_args(self) -> None:
        tools = extract_tools(Path("maxmcp/tools/materials.py"))
        backup = next(
            tool for tool in tools
            if tool["name"] == "backup_material_library"
        )

        self.assertEqual(
            set(backup["schema"]["properties"]),
            {"source", "backup_dir", "file_path", "prefix"},
        )


if __name__ == "__main__":
    unittest.main()
