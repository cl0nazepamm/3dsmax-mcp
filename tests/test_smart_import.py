import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maxmcp.tools.smart_import import (
    _filter_mesh_lods,
    _grid_cells,
    _lod_mesh_asset_token,
    _match_textures_to_meshes,
    _mesh_stem_key,
    smart_import,
)
from maxmcp.tools.material_detection import _DEFAULT_CHANNEL_PATTERNS


class SmartImportHelperTests(unittest.TestCase):
    def test_grid_cells_lays_out_in_roughly_square_grid(self) -> None:
        self.assertEqual(_grid_cells(0, 100), [])
        self.assertEqual(_grid_cells(1, 100), [(0, 0)])
        # 4 cells -> 2 cols, 2 rows
        self.assertEqual(
            _grid_cells(4, 100),
            [(0, 0), (100, 0), (0, 100), (100, 100)],
        )
        # 5 cells -> 3 cols
        self.assertEqual(
            _grid_cells(5, 50),
            [(0, 0), (50, 0), (100, 0), (0, 50), (50, 50)],
        )

    def test_match_textures_to_meshes_picks_longest_prefix(self) -> None:
        # mesh stems: "Wood" and "WoodFloor"; WoodFloor must win on WoodFloor_BaseColor
        meshes = [Path("Wood.fbx"), Path("WoodFloor.fbx")]
        textures = [
            Path("WoodFloor_BaseColor.png"),
            Path("WoodFloor_Roughness.png"),
            Path("Wood_BaseColor.png"),
        ]
        groups = _match_textures_to_meshes(meshes, textures, _DEFAULT_CHANNEL_PATTERNS)

        self.assertIn(Path("WoodFloor.fbx"), groups)
        self.assertIn(Path("Wood.fbx"), groups)
        # WoodFloor group has both basecolor and roughness; Wood group has just basecolor
        self.assertEqual(
            set(groups[Path("WoodFloor.fbx")]["channels"].keys()),
            {"diffuse", "roughness"},
        )
        self.assertEqual(
            set(groups[Path("Wood.fbx")]["channels"].keys()),
            {"diffuse"},
        )
        self.assertEqual(
            groups[Path("Wood.fbx")]["channels"]["diffuse"],
            Path("Wood_BaseColor.png"),
        )

    def test_mesh_stem_key_normalizes_via_texture_tokens(self) -> None:
        # Tokenizer splits on underscore + non-alphanumeric only; camelCase stays
        # as a single token (matches how _detect_texture_channel sees filenames).
        self.assertEqual(_mesh_stem_key("WoodFloor"), "woodfloor")
        self.assertEqual(_mesh_stem_key("wood_floor"), "wood_floor")
        self.assertEqual(_mesh_stem_key("stone_01"), "stone_01")
        self.assertEqual(_mesh_stem_key(""), "")

    def test_lod_mesh_asset_token_from_lod_mesh(self) -> None:
        self.assertEqual(_lod_mesh_asset_token("wmfiaaldw_LOD0"), "wmfiaaldw")
        self.assertIsNone(_lod_mesh_asset_token("Var1_LOD0"))
        self.assertIsNone(_lod_mesh_asset_token("Stone"))

    def test_lod_filter_keeps_lod0_only(self) -> None:
        paths = [
            Path("a_LOD0.fbx"),
            Path("a_LOD1.fbx"),
            Path("plain.obj"),
        ]
        kept, skipped = _filter_mesh_lods(paths, "lod0")
        self.assertEqual([p.name for p in kept], ["a_LOD0.fbx", "plain.obj"])
        self.assertEqual([p.name for p in skipped], ["a_LOD1.fbx"])

    def test_match_shared_albedo_and_lod_normal(self) -> None:
        meshes = [Path("wmfiaaldw_LOD0.fbx")]
        textures = [
            Path("wmfiaaldw_4K_Albedo.jpg"),
            Path("wmfiaaldw_1K_Albedo.jpg"),
            Path("wmfiaaldw_4K_Roughness.jpg"),
            Path("wmfiaaldw_1K_Normal_LOD0.jpg"),
        ]
        groups = _match_textures_to_meshes(meshes, textures, _DEFAULT_CHANNEL_PATTERNS)
        channels = groups[Path("wmfiaaldw_LOD0.fbx")]["channels"]
        self.assertEqual(set(channels.keys()), {"diffuse", "roughness", "normal"})
        self.assertEqual(channels["diffuse"].name, "wmfiaaldw_4K_Albedo.jpg")
        self.assertEqual(groups[Path("wmfiaaldw_LOD0.fbx")]["name"], "wmfiaaldw")


class SmartImportToolTests(unittest.TestCase):
    def test_smart_import_returns_error_when_folder_missing(self) -> None:
        result = smart_import("Z:/does/not/exist/abcxyz")
        self.assertIn("not found", result.lower())

    def test_smart_import_returns_no_meshes_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "readme.txt").write_text("nothing to import", encoding="utf-8")
            result = smart_import(tmp)
        self.assertIn("No mesh files found", result)

    def test_smart_import_builds_max_merge_and_pbr_material_per_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cube.max").write_bytes(b"x")
            (root / "Stone.obj").write_bytes(b"x")
            (root / "Stone_BaseColor.png").write_bytes(b"x")
            (root / "Lamp.usd").write_bytes(b"x")
            (root / "Lamp.mtlx").write_bytes(b"x")

            with patch(
                "maxmcp.tools.smart_import.client.send_command",
                return_value={"result": "smart_import: 3 imported"},
            ) as send:
                result = smart_import(tmp, grid_spacing=250.0)

        self.assertEqual(result["message"], "smart_import: 3 imported")
        self.assertEqual(result["material_renderer"], "openpbr")
        self.assertIn("OpenPBRMaterial", result["supported_material_classes"])
        self.assertIn("octane", result["supported_material_classes"])
        send.assert_called_once()
        ms = send.call_args.args[0]

        # .max merged, not imported
        self.assertIn("mergeMaxFile", ms)
        self.assertIn("Cube.max", ms)
        # other types imported
        self.assertIn('importFile @"', ms)
        self.assertIn("Stone.obj", ms)
        # USD imports its sibling .mtlx
        self.assertIn("Lamp.usd", ms)
        self.assertIn("Lamp.mtlx", ms)
        # Per-mesh dedup check via name prefix
        self.assertIn("mcp_sceneHasNamePrefix", ms)
        # Grid placement helper present
        self.assertIn("mcp_placeOnGrid", ms)
        # PBR material wired for Stone (matched its BaseColor texture)
        self.assertIn('mcp_createOpenPbrPreferred "Stone"', ms)
        # USD/MaterialX path does NOT wire textures via palette logic
        self.assertNotIn('mcp_createOpenPbrPreferred "Lamp"', ms)
        # Material is assigned to imported nodes (Stone has matched textures)
        self.assertIn("n.material = mat_", ms)

    def test_smart_import_skips_textures_for_usd_without_mtlx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Lamp.usd").write_bytes(b"x")
            (root / "Lamp_BaseColor.png").write_bytes(b"x")

            with patch(
                "maxmcp.tools.smart_import.client.send_command",
                return_value={"result": "ok"},
            ) as send:
                smart_import(tmp)

        ms = send.call_args.args[0]
        self.assertIn("Lamp.usd", ms)
        # USD must NOT trigger palette-style material build, even with matching texture name
        self.assertNotIn('mcp_createOpenPbrPreferred "Lamp"', ms)
        # No sibling .mtlx -> the .mtlx filename is not referenced anywhere
        self.assertNotIn("Lamp.mtlx", ms)

    def test_smart_import_rejects_unknown_material_class(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Cube.max").write_bytes(b"x")
            result = smart_import(tmp, material_class="NoSuchRenderer")
        self.assertEqual(result["status"], "error")
        self.assertIn("Unsupported material_class", result["error"])
        self.assertIn("VRayMtl", result["supported_material_classes"])
        self.assertIn("octane", result["supported_material_classes"])

    def test_smart_import_filters_meshes_by_name_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "OakWood.fbx").write_bytes(b"x")
            (root / "StoneWall.fbx").write_bytes(b"x")
            (root / "OakWood_BaseColor.png").write_bytes(b"x")

            with patch(
                "maxmcp.tools.smart_import.client.send_command",
                return_value={"result": "smart_import: 1 imported"},
            ) as send:
                result = smart_import(tmp, name_pattern="*wood*")

        self.assertEqual(result["message"], "smart_import: 1 imported")
        self.assertEqual(result["name_filtered_out"], 1)
        ms = send.call_args.args[0]
        self.assertIn("OakWood.fbx", ms)
        self.assertNotIn("StoneWall.fbx", ms)

    def test_smart_import_octane_uses_std_surface_mtl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Stone.obj").write_bytes(b"x")
            (root / "Stone_BaseColor.png").write_bytes(b"x")

            with patch(
                "maxmcp.tools.smart_import.client.send_command",
                return_value={"result": "smart_import: 1 imported"},
            ) as send:
                result = smart_import(tmp, material_class="octane")

        self.assertEqual(result["material_renderer"], "octane_standard")
        ms = send.call_args.args[0]
        self.assertIn("Std_Surface_Mtl", ms)

    def test_match_colocated_mesh_and_tex_subfolder(self) -> None:
        meshes = [Path("container/mesh.fbx")]
        textures = [
            Path("container/tex/albedo.png"),
            Path("container/tex/roughness.png"),
            Path("container/tex/normal.png"),
        ]
        groups = _match_textures_to_meshes(meshes, textures, _DEFAULT_CHANNEL_PATTERNS)
        self.assertIn(Path("container/mesh.fbx"), groups)
        channels = set(groups[Path("container/mesh.fbx")]["channels"].keys())
        self.assertIn("diffuse", channels)
        self.assertIn("roughness", channels)
        self.assertIn("normal", channels)
        self.assertEqual(groups[Path("container/mesh.fbx")]["name"], "container")

    def test_fuzzy_stem_matches_containermesh_to_container_textures(self) -> None:
        meshes = [Path("container/containermesh.fbx")]
        textures = [
            Path("container/container_albedo.png"),
            Path("container/container_roughness.png"),
        ]
        groups = _match_textures_to_meshes(meshes, textures, _DEFAULT_CHANNEL_PATTERNS)
        self.assertIn("diffuse", groups[Path("container/containermesh.fbx")]["channels"])

    def test_plant_var_mesh_matches_shared_atlas_textures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "3dplant_flowering plant_xikkdhjja"
            (root / "Textures" / "Atlas").mkdir(parents=True)
            (root / "Var1").mkdir()
            mesh = root / "Var1" / "Var1_LOD0.fbx"
            mesh.touch()
            textures = [
                root / "Textures" / "Atlas" / "xikkdhjja_4K_Albedo.jpg",
                root / "Textures" / "Atlas" / "xikkdhjja_4K_Roughness.jpg",
                root / "Textures" / "Atlas" / "xikkdhjja_4K_Normal.jpg",
            ]
            for tex in textures:
                tex.touch()
            groups = _match_textures_to_meshes([mesh], textures, _DEFAULT_CHANNEL_PATTERNS)
            self.assertIn(mesh, groups)
            self.assertEqual(groups[mesh]["name"], "xikkdhjja")
            self.assertIn("diffuse", groups[mesh]["channels"])

    def test_shared_bundle_textures_apply_to_all_variant_meshes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "3dplant_flowering plant_xikkdhjja"
            (root / "Textures" / "Atlas").mkdir(parents=True)
            meshes = []
            for i in range(1, 4):
                (root / f"Var{i}").mkdir()
                mesh = root / f"Var{i}" / f"Var{i}_LOD0.fbx"
                mesh.touch()
                meshes.append(mesh)
            textures = [root / "Textures" / "Atlas" / "xikkdhjja_4K_Albedo.jpg"]
            textures[0].touch()
            groups = _match_textures_to_meshes(meshes, textures, _DEFAULT_CHANNEL_PATTERNS)
            self.assertEqual(len(groups), 3)
            for mesh in meshes:
                self.assertIn("diffuse", groups[mesh]["channels"])

    def test_smart_import_recursive_picks_up_subfolders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "sub"
            sub.mkdir()
            (sub / "Nested.obj").write_bytes(b"x")
            (sub / "Nested_BaseColor.png").write_bytes(b"x")

            with patch(
                "maxmcp.tools.smart_import.client.send_command",
                return_value={"result": "ok"},
            ) as send:
                # Non-recursive should find nothing
                result = smart_import(tmp, recursive=False)
            self.assertIn("No mesh files found", result)

            with patch(
                "maxmcp.tools.smart_import.client.send_command",
                return_value={"result": "ok"},
            ) as send:
                # Default recursive=True should find nested meshes.
                smart_import(tmp)
            ms = send.call_args.args[0]
            self.assertIn("Nested.obj", ms)
            self.assertIn('mcp_createOpenPbrPreferred "Nested"', ms)


if __name__ == "__main__":
    unittest.main()
