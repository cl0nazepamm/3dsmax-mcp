"""Tests for path_filter helper."""

import unittest
from pathlib import Path

from maxmcp.helpers.path_filter import (
    filter_by_name_pattern,
    matches_name_pattern,
    normalize_name_pattern,
)


class PathFilterTests(unittest.TestCase):
    def test_normalize_treats_empty_and_star_as_disabled(self) -> None:
        self.assertIsNone(normalize_name_pattern(""))
        self.assertIsNone(normalize_name_pattern("*"))
        self.assertEqual(normalize_name_pattern("*wood*"), "*wood*")

    def test_filter_by_stem_glob(self) -> None:
        paths = [Path("OakWood_LOD0.fbx"), Path("StoneWall.fbx"), Path("wood_plank.obj")]
        kept, skipped = filter_by_name_pattern(paths, "*wood*", key=lambda p: p.stem)
        self.assertEqual([p.name for p in kept], ["OakWood_LOD0.fbx", "wood_plank.obj"])
        self.assertEqual(skipped, 1)

    def test_filter_disabled_returns_all(self) -> None:
        paths = [Path("a.fbx"), Path("b.fbx")]
        kept, skipped = filter_by_name_pattern(paths, "", key=lambda p: p.stem)
        self.assertEqual(kept, paths)
        self.assertEqual(skipped, 0)

    def test_matches_is_case_insensitive(self) -> None:
        self.assertTrue(matches_name_pattern("OakWood", "*wood*"))

    def test_exclude_pattern_drops_matches(self) -> None:
        paths = [Path("oak.fbx"), Path("oak_broken.fbx"), Path("birch.fbx")]
        kept, skipped = filter_by_name_pattern(
            paths, "", key=lambda p: p.stem, exclude="*_broken*",
        )
        self.assertEqual([p.name for p in kept], ["oak.fbx", "birch.fbx"])
        self.assertEqual(skipped, 1)

    def test_exclude_wins_over_include(self) -> None:
        paths = [Path("oak_LOD0.fbx"), Path("oak_lowpoly.fbx"), Path("stone.fbx")]
        kept, skipped = filter_by_name_pattern(
            paths, "oak_*", key=lambda p: p.stem, exclude="*lowpoly*",
        )
        # included by name_pattern but removed by exclude
        self.assertEqual([p.name for p in kept], ["oak_LOD0.fbx"])
        self.assertEqual(skipped, 2)

    def test_comma_separated_includes(self) -> None:
        paths = [Path("oak.fbx"), Path("birch.fbx"), Path("stone.fbx")]
        kept, _ = filter_by_name_pattern(paths, "oak*,birch*", key=lambda p: p.stem)
        self.assertEqual([p.name for p in kept], ["oak.fbx", "birch.fbx"])

    def test_comma_separated_excludes(self) -> None:
        paths = [Path("a.fbx"), Path("debris1.fbx"), Path("lowpoly_a.fbx")]
        kept, skipped = filter_by_name_pattern(
            paths, "", key=lambda p: p.stem, exclude="*debris*,*lowpoly*",
        )
        self.assertEqual([p.name for p in kept], ["a.fbx"])
        self.assertEqual(skipped, 2)

    def test_no_include_no_exclude_returns_all(self) -> None:
        paths = [Path("a.fbx"), Path("b.fbx")]
        kept, skipped = filter_by_name_pattern(paths, "", key=lambda p: p.stem, exclude="")
        self.assertEqual(kept, paths)
        self.assertEqual(skipped, 0)


if __name__ == "__main__":
    unittest.main()
