import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools.material_ops import create_material_from_textures


class OpenPBRMaterialTests(unittest.TestCase):
    def test_create_material_from_textures_defaults_to_openpbr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "asset_basecolor.png"
            tex.write_bytes(b"fake")

            with (
                patch(
                    "src.tools.material_ops._build_openpbr_maxscript",
                    return_value='("openpbr")',
                ) as build_openpbr,
                patch("src.tools.material_ops.client.send_command", return_value={"result": "ok"}) as send,
            ):
                result = create_material_from_textures(tmp)

        build_openpbr.assert_called_once()
        send.assert_called_once()
        self.assertEqual(result, "ok")

    def test_create_material_from_textures_accepts_explicit_openpbr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "asset_roughness.png"
            tex.write_bytes(b"fake")

            with patch(
                "src.tools.material_ops._build_openpbr_maxscript",
                return_value='("openpbr")',
            ) as build_openpbr, patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "ok"},
            ):
                create_material_from_textures(tmp, material_class="OpenPBR_Material")

        build_openpbr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
