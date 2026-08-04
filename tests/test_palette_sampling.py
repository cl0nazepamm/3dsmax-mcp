import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maxmcp.helpers.palette_sampling import (
    collect_one_sample_per_subfolder,
    is_preview_asset,
    normalize_overflow_mode,
    normalize_sample_mode,
    split_palette_and_library,
)


def _tripback_message(result):
    return result["message"] if isinstance(result, dict) else result


class PaletteSamplingHelperTests(unittest.TestCase):
    def test_normalize_sample_mode_aliases(self) -> None:
        self.assertEqual(normalize_sample_mode("random-per-subfolder"), "random_per_subfolder")
        self.assertEqual(normalize_overflow_mode("palette-then-library"), "palette_then_library")

    def test_is_preview_asset(self) -> None:
        self.assertTrue(is_preview_asset(Path("oak_preview.jpg")))
        self.assertTrue(is_preview_asset(Path("oak_previewclose.jpg")))
        self.assertFalse(is_preview_asset(Path("oak_basecolor.jpg")))

    def test_split_palette_and_library(self) -> None:
        items = list(range(5))
        palette, library = split_palette_and_library(items, max_slots=2, overflow_mode="palette_then_library")
        self.assertEqual(palette, [0, 1])
        self.assertEqual(library, [2, 3, 4])

    def test_collect_one_sample_per_subfolder_picks_first_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wood = root / "wood_pack"
            stone = root / "stone_pack"
            wood.mkdir()
            stone.mkdir()
            for name in ("oak_basecolor.png", "oak_roughness.png"):
                (wood / name).write_bytes(b"x")
            for name in ("slate_basecolor.png", "slate_roughness.png"):
                (stone / name).write_bytes(b"x")
            (wood / "oak_preview.jpg").write_bytes(b"x")

            picked, meta, _, _ = collect_one_sample_per_subfolder(
                str(root),
                recursive=True,
                pick_random=False,
                random_seed=None,
                slot_content="pbr_material",
            )

        self.assertEqual(meta["subfolder_count"], 2)
        self.assertEqual(len(picked), 2)
        names = sorted(group["name"] for group in picked)
        self.assertEqual(names, ["oak", "slate"])


class MaterialEditorPaletteSamplingTests(unittest.TestCase):
    def test_palette_laydown_random_per_subfolder_builds_one_group_per_child(self) -> None:
        from maxmcp.tools.palette_laydown import palette_laydown

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_a = root / "pack_a"
            pack_b = root / "pack_b"
            pack_a.mkdir()
            pack_b.mkdir()
            (pack_a / "alpha_basecolor.png").write_bytes(b"x")
            (pack_a / "alpha_roughness.png").write_bytes(b"x")
            (pack_b / "beta_basecolor.png").write_bytes(b"x")
            (pack_b / "beta_roughness.png").write_bytes(b"x")

            with patch(
                "maxmcp.tools.material_ops.client.send_command",
                return_value={"result": "Loaded 2 grouped PBR material(s)"},
            ) as send:
                result = palette_laydown(
                    tmp,
                    slot_content="pbr_material",
                    sample_mode="random_per_subfolder",
                    random_seed=7,
                    include_displacement=False,
                )

        self.assertEqual(_tripback_message(result), "Loaded 2 grouped PBR material(s)")
        self.assertEqual(result["subfolder_count"], 2)
        self.assertEqual(result["sample_mode"], "random_per_subfolder")
        send.assert_called_once()
        maxscript = send.call_args.args[0]
        self.assertIn('"tex_alpha"', maxscript)
        self.assertIn('"tex_beta"', maxscript)

    def test_palette_laydown_overflow_puts_extra_groups_in_library(self) -> None:
        from maxmcp.tools.palette_laydown import palette_laydown

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label in ("alpha", "beta", "gamma"):
                folder = root / label
                folder.mkdir()
                (folder / f"{label}_basecolor.png").write_bytes(b"x")
                (folder / f"{label}_roughness.png").write_bytes(b"x")

            with patch(
                "maxmcp.tools.material_ops.client.send_command",
                return_value={"result": "Loaded 2 grouped PBR material(s) | Library: 1 material(s)"},
            ) as send:
                result = palette_laydown(
                    tmp,
                    slot_content="pbr_material",
                    sample_mode="one_per_subfolder",
                    overflow_mode="palette_then_library",
                    max_slots=2,
                )

        self.assertEqual(result["palette_count"], 2)
        self.assertEqual(result["library_count"], 1)
        maxscript = send.call_args.args[0]
        self.assertIn("append currentMaterialLibrary lib_3", maxscript)

    def test_palette_laydown_rejects_unknown_sample_mode(self) -> None:
        from maxmcp.tools.palette_laydown import palette_laydown

        with tempfile.TemporaryDirectory() as tmp:
            with patch("maxmcp.tools.material_ops.client.send_command") as send:
                result = palette_laydown(tmp, sample_mode="every_other_tuesday")

        self.assertIn("Unsupported sample_mode", result)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
