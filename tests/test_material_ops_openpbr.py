import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maxmcp.tools.material_ops import create_material_from_textures


def _tripback_message(result):
    return result["message"] if isinstance(result, dict) else result


class OpenPBRMaterialTests(unittest.TestCase):
    def test_create_material_from_textures_defaults_to_openpbr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "asset_basecolor.png"
            tex.write_bytes(b"fake")

            with (
                patch(
                    "maxmcp.tools.material_ops._build_shared_pbr_maxscript",
                    return_value='("openpbr")',
                ) as build_pbr,
                patch("maxmcp.tools.material_ops.client.send_command", return_value={"result": "ok"}) as send,
            ):
                result = create_material_from_textures(tmp)

        build_pbr.assert_called_once()
        # renderer is the 3rd positional arg of _build_shared_pbr_maxscript.
        self.assertEqual(build_pbr.call_args.args[2], "openpbr")
        send.assert_called_once()
        self.assertEqual(_tripback_message(result), "ok")
        self.assertEqual(result["material_renderer"], "openpbr")

    def test_create_material_from_textures_accepts_explicit_openpbr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "asset_roughness.png"
            tex.write_bytes(b"fake")

            with patch(
                "maxmcp.tools.material_ops._build_shared_pbr_maxscript",
                return_value='("openpbr")',
            ) as build_pbr, patch(
                "maxmcp.tools.material_ops.client.send_command",
                return_value={"result": "ok"},
            ):
                create_material_from_textures(tmp, material_class="OpenPBR_Material")

        build_pbr.assert_called_once()
        self.assertEqual(build_pbr.call_args.args[2], "openpbr")

    def test_create_material_from_textures_openpbr_with_orm_uses_shared_builder(self) -> None:
        # Packed ORM folders select the openpbr renderer and now route through the
        # shared builder, which splits ORM into AO/roughness/metallic (the old
        # per-renderer OpenPBR builder dropped the packed ORM map silently).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "asset_basecolor.png").write_bytes(b"fake")
            (root / "asset_orm.png").write_bytes(b"fake")

            with patch("maxmcp.tools.material_ops._build_shared_pbr_maxscript", return_value="-- mock") as build_pbr, \
                 patch("maxmcp.tools.material_ops.client.send_command",
                       return_value={"result": '{"status":"success"}'}):
                create_material_from_textures(tmp, material_name="asset")

        build_pbr.assert_called_once()
        self.assertEqual(build_pbr.call_args.args[2], "openpbr")
        # The packed ORM map reaches the builder so it can be split downstream.
        matched = build_pbr.call_args.args[0]
        self.assertIn("orm", matched)

    def test_create_material_from_textures_octane_uses_shared_pbr_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "asset_basecolor.png"
            tex.write_bytes(b"fake")

            with patch(
                "maxmcp.tools.material_ops._build_shared_pbr_maxscript",
                return_value='("octane")',
            ) as build_pbr, patch(
                "maxmcp.tools.material_ops.client.send_command",
                return_value={"result": "ok"},
            ):
                result = create_material_from_textures(tmp, material_class="octane")

        build_pbr.assert_called_once()
        self.assertEqual(result["material_renderer"], "octane_standard")
        self.assertIn("ai_standard_surface", result["supported_material_classes"])


if __name__ == "__main__":
    unittest.main()
