import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.tools.palette_laydown import palette_laydown


class MaterialEditorPaletteTests(unittest.TestCase):
    def test_load_texture_folder_to_material_editor_creates_slot_preview_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "b_roughness.jpg").write_bytes(b"fake")
            (root / "a_basecolor.png").write_bytes(b"fake")
            (root / "notes.txt").write_text("skip", encoding="utf-8")

            with patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "Loaded 2"},
            ) as send:
                result = palette_laydown(
                    tmp,
                    start_slot=3,
                    max_slots=2,
                    open_editor=True,
                    material_prefix="pal_",
                )

        self.assertEqual(result, "Loaded 2")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn("MatEditor.Open()", maxscript)
        self.assertIn("medit.PutMtlToMtlEditor", maxscript)
        self.assertIn("mcp_createOpenPbrPreferred", maxscript)
        self.assertIn('mcp_createOpenPbrPreferred "pal_a_basecolor"', maxscript)
        self.assertIn('mcp_createOpenPbrPreferred "pal_b_roughness"', maxscript)
        self.assertIn("base_color_map", maxscript)
        self.assertIn("mcp_setFirstValue", maxscript)
        self.assertIn('"specular_color"', maxscript)
        self.assertIn("color 0 0 0", maxscript)
        self.assertNotIn("notes.txt", maxscript)
        self.assertIn("local slotIndex = 3", maxscript)

    def test_load_texture_folder_to_material_editor_does_not_call_max_without_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "notes.txt").write_text("skip", encoding="utf-8")

            with patch("src.tools.material_ops.client.send_command") as send:
                result = palette_laydown(tmp)

        self.assertIn("No image files found", result)
        send.assert_not_called()

    def test_load_texture_folder_to_material_editor_can_lay_down_bitmaps_directly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "wood.png"
            tex.write_bytes(b"fake")

            with patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "Loaded 1 bitmap"},
            ) as send:
                result = palette_laydown(tmp, slot_content="bitmap")

        self.assertEqual(result, "Loaded 1 bitmap")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn('Bitmaptexture name:"wood"', maxscript)
        self.assertIn("medit.PutMtlToMtlEditor tex_1 slotIndex", maxscript)
        self.assertIn("bitmap texture map", maxscript)
        self.assertNotIn('mcp_createOpenPbrPreferred "tex_wood"', maxscript)

    def test_load_texture_folder_to_material_editor_rejects_unknown_slot_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tex = Path(tmp) / "wood.png"
            tex.write_bytes(b"fake")

            with patch("src.tools.material_ops.client.send_command") as send:
                result = palette_laydown(tmp, slot_content="shader")

        self.assertIn("Unsupported slot_content", result)
        self.assertIn("material", result)
        self.assertIn("bitmap", result)
        send.assert_not_called()

    def test_load_texture_folder_to_material_editor_groups_full_pbr_sets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (
                "oak_base_color.png",
                "oak_ambient_occlusion.jpg",
                "oak_rough.tif",
                "oak_metalness.png",
                "oak_normal_gl.tga",
                "oak_height.exr",
                "tile_color.png",
                "tile_nrm.png",
            ):
                (root / name).write_bytes(b"fake")
            (root / "notes.txt").write_text("skip", encoding="utf-8")

            with patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "Loaded 2 grouped PBR material(s)"},
            ) as send:
                result = palette_laydown(
                    tmp,
                    slot_content="pbr_material",
                    material_class="PhysicalMaterial",
                    material_prefix="mat_",
                    max_slots=2,
                )

        self.assertEqual(result, "Loaded 2 grouped PBR material(s)")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn('PhysicalMaterial name:"mat_oak"', maxscript)
        self.assertIn('PhysicalMaterial name:"mat_tile"', maxscript)
        self.assertIn("CompositeTexturemap", maxscript)
        self.assertIn("diffuse(+ao)", maxscript)
        self.assertIn("roughness_map", maxscript)
        self.assertIn("metalness_map", maxscript)
        self.assertIn("Normal_Bump", maxscript)
        self.assertIn("displacement_map", maxscript)
        self.assertIn("color 255 255 255", maxscript)
        self.assertNotIn("notes.txt", maxscript)

    def test_grouped_pbr_consumes_normal_variants_and_common_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (
                "cloud_Albedo_Map.png",
                "cloud_Metallic.png",
                "cloud_Normal_OpenGL.png",
                "cloud_Roughness.png",
                "cloud_shoes_alb.png",
                "cloud_shoes_Metallic.png",
                "cloud_shoes_Norm.png",
                "cloud_shoes_Roughness.png",
            ):
                (root / name).write_bytes(b"fake")

            with patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "Loaded 2 grouped PBR material(s)"},
            ) as send:
                result = palette_laydown(
                    tmp,
                    slot_content="full_pbr",
                    material_class="OpenPBRMaterial",
                )

        self.assertEqual(result, "Loaded 2 grouped PBR material(s)")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn('mcp_createOpenPbrPreferred "tex_cloud"', maxscript)
        self.assertIn('mcp_createOpenPbrPreferred "tex_cloud_shoes"', maxscript)
        self.assertNotIn('name:"tex_cloud_opengl"', maxscript)
        self.assertNotIn('name:"tex_cloud_map"', maxscript)
        self.assertNotIn('name:"tex_cloud_shoes_norm"', maxscript)
        self.assertIn("normal->", maxscript)

    def test_load_texture_folder_to_material_editor_can_make_renderer_pbr_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("crate_d.png", "crate_r.png", "crate_m.png", "crate_n.png", "crate_spec.png"):
                (root / name).write_bytes(b"fake")

            with patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "Loaded Arnold"},
            ) as send:
                result = palette_laydown(
                    tmp,
                    slot_content="full_pbr",
                    material_class="ai_standard_surface",
                )

        self.assertEqual(result, "Loaded Arnold")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn('ai_standard_surface name:"tex_crate"', maxscript)
        self.assertIn("ai_image", maxscript)
        self.assertIn('color_space:"sRGB"', maxscript)
        self.assertIn('color_space:"Raw"', maxscript)
        self.assertIn("specular_roughness_shader", maxscript)
        self.assertIn("metalness_shader", maxscript)
        self.assertIn("ai_normal_map", maxscript)
        self.assertIn("specular_color_shader", maxscript)

    def test_load_texture_folder_to_material_editor_splits_packed_orm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "brick_basecolor.png").write_bytes(b"fake")
            (root / "brick_orm.png").write_bytes(b"fake")

            with patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "Loaded ORM"},
            ) as send:
                result = palette_laydown(tmp, slot_content="pbr")

        self.assertEqual(result, "Loaded ORM")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn("OSLMap()", maxscript)
        self.assertIn("MultiOutputChannelTexmapToTexmap", maxscript)
        self.assertIn("outputChannelIndex = 2", maxscript)
        self.assertIn("outputChannelIndex = 3", maxscript)
        self.assertIn("outputChannelIndex = 4", maxscript)
        self.assertIn("diffuse(+ao)", maxscript)

    def test_load_texture_folder_to_material_editor_can_skip_displacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("stone_basecolor.png", "stone_roughness.png", "stone_height.exr"):
                (root / name).write_bytes(b"fake")

            with patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "Loaded no displacement"},
            ) as send:
                result = palette_laydown(
                    tmp,
                    slot_content="full_pbr",
                    material_class="PhysicalMaterial",
                    include_displacement=False,
                )

        self.assertEqual(result, "Loaded no displacement")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn('PhysicalMaterial name:"tex_stone"', maxscript)
        self.assertIn("roughness_map", maxscript)
        self.assertNotIn("slot_1_displacement", maxscript)
        self.assertNotIn('displacement->', maxscript)

    def test_load_texture_folder_to_material_editor_can_make_vray_pbr_materials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("wood_basecolor.png", "wood_roughness.png", "wood_metalness.png", "wood_normal.png"):
                (root / name).write_bytes(b"fake")

            with patch(
                "src.tools.material_ops.client.send_command",
                return_value={"result": "Loaded V-Ray"},
            ) as send:
                result = palette_laydown(
                    tmp,
                    slot_content="full_pbr",
                    material_class="VRayMtl",
                )

        self.assertEqual(result, "Loaded V-Ray")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn('VRayMtl name:"tex_wood"', maxscript)
        self.assertIn("texmap_diffuse", maxscript)
        self.assertIn("texmap_roughness", maxscript)
        self.assertIn("brdf_useRoughness", maxscript)
        self.assertIn("texmap_metalness", maxscript)
        self.assertIn("VRayNormalMap", maxscript)


if __name__ == "__main__":
    unittest.main()
