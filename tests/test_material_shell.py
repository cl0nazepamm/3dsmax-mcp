import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import PropertyMock, patch

from scripts.gen_tool_registry import extract_tools
from src.max_client import MaxBridgeError
from src.tools.material_ops import create_shell_material
from src.tools.material_shell import build_shell_wrap_maxscript


class ShellMaterialTests(unittest.TestCase):
    def test_standalone_chat_schema_only_exposes_native_wrap_mode(self) -> None:
        tools = extract_tools(Path("src/tools/material_ops.py"))
        shell = next(
            tool for tool in tools
            if tool["name"] == "create_shell_material"
        )

        self.assertEqual(
            shell["schema"]["required"],
            ["shell_name", "render_material"],
        )
        properties = shell["schema"]["properties"]
        self.assertNotIn("texture_folder", properties)
        self.assertNotIn("render_material_class", properties)
        self.assertNotIn("export_material_class", properties)

    def test_wrap_existing_materials(self) -> None:
        ms = build_shell_wrap_maxscript(
            "MyShell",
            render_material="OctaneMat",
            export_material="ExportMat",
            assign_to=["Box001"],
        )
        self.assertIn("mcp_findMaterialByName", ms)
        self.assertIn('"OctaneMat"', ms)
        self.assertIn('"ExportMat"', ms)
        self.assertIn("Shell_Material()", ms)
        self.assertIn("shell.originalMaterial = renderMat", ms)
        self.assertIn("shell.bakedMaterial = exportMat", ms)
        self.assertNotIn("ai_multiply", ms)

    def test_create_shell_wraps_by_name(self) -> None:
        with (
            patch(
                "src.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=False,
            ),
            patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": '{"status":"success","workflow":"shell_wrap"}'},
            ) as send,
        ):
            result = create_shell_material(
                "PlantShell",
                render_material="xikkdhjja",
                export_material="xikkdhjja_export",
            )

        self.assertIn("success", result)
        send.assert_called_once()
        ms = send.call_args.args[0]
        self.assertIn("shell_wrap", ms)
        self.assertIn("mcp_findMaterialByName", ms)

    def test_create_shell_uses_native_for_existing_materials(self) -> None:
        with (
            patch(
                "src.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": '{"status":"success","workflow":"shell_wrap"}'},
            ) as send,
        ):
            result = create_shell_material(
                "PlantShell",
                render_material="RenderMat",
                export_material="ExportMat",
                assign_to=["Box001"],
            )

        self.assertIn("success", result)
        payload = json.loads(send.call_args.args[0])
        self.assertEqual(send.call_args.kwargs["cmd_type"], "native:create_shell_material")
        self.assertEqual(payload["shell_name"], "PlantShell")
        self.assertEqual(payload["render_material"], "RenderMat")
        self.assertEqual(payload["export_material"], "ExportMat")
        self.assertEqual(payload["assign_to"], ["Box001"])

    def test_create_shell_falls_back_when_native_route_is_missing(self) -> None:
        error = "Unknown command type: native:create_shell_material"
        with (
            patch(
                "src.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "src.tools.material_ops.client.send_command",
                side_effect=[
                    MaxBridgeError(error, {"success": False, "error": error}),
                    {"result": '{"status":"success","workflow":"shell_wrap"}'},
                ],
            ) as send,
        ):
            result = create_shell_material(
                "PlantShell",
                render_material="RenderMat",
            )

        self.assertIn("success", result)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(
            send.call_args_list[0].kwargs["cmd_type"],
            "native:create_shell_material",
        )
        self.assertNotIn("cmd_type", send.call_args_list[1].kwargs)
        self.assertIn("mcp_findMaterialByName", send.call_args_list[1].args[0])

    def test_create_shell_propagates_native_handler_error(self) -> None:
        error = "Render material not found"
        bridge_error = MaxBridgeError(
            error,
            {"success": False, "error": error},
        )
        with (
            patch(
                "src.max_client.MaxClient.native_available",
                new_callable=PropertyMock,
                return_value=True,
            ),
            patch(
                "src.tools.material_ops.client.send_command",
                side_effect=bridge_error,
            ),
            self.assertRaises(MaxBridgeError) as raised,
        ):
            create_shell_material(
                "PlantShell",
                render_material="MissingMat",
            )

        self.assertIs(raised.exception, bridge_error)

    def test_create_shell_builds_any_renderer_from_textures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "asset_basecolor.png").write_bytes(b"x")
            (root / "asset_roughness.png").write_bytes(b"x")

            with (
                patch(
                    "src.max_client.MaxClient.native_available",
                    new_callable=PropertyMock,
                    return_value=True,
                ),
                patch(
                    "src.tools.material_ops.client.send_command",
                    return_value={"result": '{"status":"success"}'},
                ) as send,
            ):
                create_shell_material(
                    "DualShell",
                    texture_folder=tmp,
                    render_material_class="octane",
                    export_material_class="OpenPBRMaterial",
                )

        ms = send.call_args.args[0]
        self.assertIn("Std_Surface_Mtl", ms)
        self.assertIn("OpenPBRMaterial", ms)
        self.assertIn("shell.originalMaterial = renderMat", ms)
        self.assertIn("shell.bakedMaterial = exportMat", ms)

    def test_create_shell_requires_render_or_textures(self) -> None:
        result = create_shell_material("EmptyShell")
        self.assertIn("render_material is required", result)

    def test_native_shell_keeps_unassigned_material_and_sets_slots_explicitly(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "src"
            / "handlers"
            / "material_handlers.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("if (!setRender || !setViewport)", source)
        self.assertIn("GetMaterialLibrary().Add(shellMaterial)", source)
        self.assertIn("MaxScriptVisibleClassName(renderMaterial)", source)
        self.assertNotIn("Export material not found:", source)


if __name__ == "__main__":
    unittest.main()
