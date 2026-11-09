"""Offline regressions and an optional disposable Max batch acceptance script.

Run: python scripts/live/test_fstorm_materials.py
Emit: python scripts/live/test_fstorm_materials.py --emit-maxscript local/fstorm-smoke.ms
Then run that .ms with 3dsmaxbatch.exe (no scene input, no render).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from maxmcp.tools.materials._material_tripback import PBR_RENDERER_REGISTRY
from maxmcp.core.maxscript import safe_string
from maxmcp.tools.materials._material_detection import _renderer_from_material_class
from maxmcp.tools.materials._pbr_material_builder import (
    groups_need_uberbitmap_osl, pbr_helpers_preamble_lines,
    pbr_per_group_lines, pbr_renderer_setup_lines,
)


def group_script(channels, renderer="fstorm", displacement=True, name="FStorm test"):
    group = {"name": name, "channels": channels, "aliases": {}}
    return "\n".join(pbr_per_group_lines(
        group, idx=1, mat_var="testMat", mat_name=safe_string(name),
        renderer=renderer, include_displacement=displacement,
    ))


class FStormTests(unittest.TestCase):
    def test_aliases_and_other_renderer_registry(self):
        for row in PBR_RENDERER_REGISTRY:
            for alias in row["ask_as"]:
                with self.subTest(alias=alias):
                    self.assertEqual(_renderer_from_material_class(alias), row["renderer"])
        for alias in ("FStormBitmap", "FStormPhysical", "FStormMixMat", "FStormPBR_Unknown"):
            self.assertIsNone(_renderer_from_material_class(alias))

    def test_native_graph_and_priority(self):
        code = group_script({k: Path(f"{k}.png") for k in (
            "diffuse", "ao", "roughness", "glossiness", "normal", "bump", "metallic", "orm", "ior")})
        self.assertIn("FStormMix", code)
        self.assertIn("mix_mod = 3", code)
        self.assertIn("refl_glossiness_invert = 1", code)
        self.assertIn("bump(normal takes priority", code)
        self.assertIn("metallic(use FStormPBR)", code)
        self.assertIn("orm(unpack", code)
        for absent in ("Normal_Bump", "Bitmaptexture", "Output name", "CompositeTexturemap", "OSLMap"):
            self.assertNotIn(absent, code)

    def test_pbr_slots_gloss_and_displacement(self):
        code = group_script({k: Path(f"{k}.png") for k in (
            "glossiness", "translucency", "metallic", "displacement")}, "fstorm_pbr", False)
        self.assertIn("FStormPBR", code)
        self.assertIn("g1_glossiness.inverted = true", code)
        self.assertIn("testMat.translucence_texture =", code)
        self.assertIn("testMat.translucence_tex_on = true", code)
        self.assertIn("testMat.metalness_tex =", code)
        self.assertNotIn("testMat.displacement_on = true", code)
        self.assertNotIn("local g1_displacement", code)

    def test_color_data_and_empty_bitmap_policy(self):
        code = group_script({"diffuse": Path("a.png"), "normal": Path("n.png"), "roughness": Path("r.png")})
        self.assertIn('"a" false false', code)
        self.assertIn('"n" true true', code)
        self.assertIn('"r" true false', code)
        setup = "\n".join(pbr_renderer_setup_lines("fstorm", needs_uberbitmap_osl=False))
        self.assertIn('if path != "" do tex.filename = path', setup)
        self.assertIn("doesFileExist path", setup)
        self.assertFalse(groups_need_uberbitmap_osl([{"channels": {"orm": Path("x.png")}}], "fstorm"))

    def test_paths_and_names_are_escaped(self):
        code = group_script({"diffuse": Path('C:/texture folder/quote"name.png')}, name='A "quoted" material')
        self.assertIn('name:"A \\"quoted\\" material"', code)
        self.assertIn('quote\\"name.png', code)

    def test_all_renderer_builders_still_generate(self):
        for row in PBR_RENDERER_REGISTRY:
            with self.subTest(renderer=row["renderer"]):
                code = group_script({"diffuse": Path("sample.png")}, row["renderer"])
                self.assertIn("local testMat", code)


def emit_maxscript(path: Path):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fixture = path.with_suffix(".png")
    report = path.with_suffix(".txt")
    q = lambda p: '"' + safe_string(p.as_posix()) + '"'
    lines = [
        "(", f"local report = createFile {q(report)}", "try (",
        f"local fixture = bitmap 4 4 color:(color 128 128 255) filename:{q(fixture)}",
        "save fixture", "close fixture",
        'fn check condition label = (if not condition do throw ("FAIL: " + label))',
        *pbr_helpers_preamble_lines(),
        *pbr_renderer_setup_lines("fstorm", needs_uberbitmap_osl=False),
        'local blank = mcp_fstormBitmap "" "Empty" false false',
        'check (classOf blank == FStormBitmap and blank.filename == "") "blank bitmap"',
    ]
    cases = [
        ("fstorm", True, ["diffuse", "ao", "roughness", "glossiness", "normal", "bump", "metallic", "opacity", "emission", "translucency", "specular", "displacement", "orm", "ior"], [
            '(classOf testMat == FStorm)',
            '(classOf testMat.diffuse_tex == FStormMix)',
            '(testMat.diffuse_tex.texture1.input_gamma and not testMat.diffuse_tex.texture2.input_gamma)',
            '(testMat.refl_glossiness_invert == 1 and not testMat.reflection_glossy_tex.input_gamma)',
            '(testMat.bump_texture.normal_map and not testMat.bump_texture.input_gamma)',
            '(testMat.displacement_on and testMat.displacement_texture_on)',
            '(testMat.emission_enabled and testMat.emission_power == 1)',
            '(findString skippedList "metallic(use FStormPBR)" != undefined)',
        ]),
        ("fstorm_pbr", True, ["diffuse", "ao", "glossiness", "normal", "metallic", "opacity", "emission", "translucency", "specular", "displacement"], [
            '(classOf testMat == FStormPBR)',
            '(testMat.roughness_tex.inverted and not testMat.roughness_tex.input_gamma)',
            '(testMat.metalness_tex != undefined and not testMat.metalness_tex.input_gamma)',
            '(testMat.translucence_texture != undefined and testMat.translucence_tex_on)',
            '(testMat.bump_tex.normal_map and testMat.displacement_on)',
            '(skippedList == "")',
        ]),
        ("fstorm", False, ["glossiness", "bump", "displacement"], [
            '(testMat.refl_glossiness_invert == 0)',
            '(not testMat.bump_texture.normal_map)',
            '(not testMat.displacement_on and testMat.displacement_texture == undefined)',
        ]),
        ("fstorm_pbr", False, ["roughness", "glossiness"], [
            '(not testMat.roughness_tex.inverted)',
            '(findString skippedList "glossiness(roughness takes priority)" != undefined)',
        ]),
    ]
    for i, (renderer, displacement, channels, checks) in enumerate(cases):
        lines.extend(["(", group_script({k: fixture for k in channels}, renderer, displacement)])
        for j, condition in enumerate(checks):
            lines.append(f'check {condition} "case {i + 1}, assertion {j + 1}"')
        lines.extend([f'format "PASS case {i + 1}: {renderer}\\n" to:report', ")"])
    lines.extend([
        # Test the documented API; never use the historical empty-path workaround.
        f'local reloadMap = mcp_fstormBitmap {q(fixture)} "Reload" false false',
        'local oldPath = reloadMap.filename',
        'reloadMap.FStormBitmap.reloadBitmap()',
        'check (reloadMap.filename == oldPath) "reload preserved filename"',
        'local fb = FStormFrontBack()',
        'fb.texture1 = reloadMap', 'fb.texture2 = undefined',
        'local first = fb.texture1', 'local second = fb.texture2',
        'fb.texture1 = second', 'fb.texture2 = first',
        'check (fb.texture1 == undefined and fb.texture2 == reloadMap) "FrontBack swap preserves blank input"',
        'format "PASS bitmap reload and FrontBack readback\\nALL PASS\\n" to:report',
        ') catch (format "FAIL: %\\n" (getCurrentException()) to:report)',
        'close report', ')',
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {path}; results will be in {report}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emit-maxscript", type=Path)
    args = parser.parse_args()
    if args.emit_maxscript:
        emit_maxscript(args.emit_maxscript)
    else:
        unittest.main(argv=[sys.argv[0]])
