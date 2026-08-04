"""Tests for material_class tripback helpers."""

import unittest

from maxmcp.helpers.material_tripback import (
    PBR_RENDERER_REGISTRY,
    material_class_hint,
    supported_pbr_renderer_keys,
    unsupported_material_class_result,
    wrap_material_tool_result,
)


class MaterialTripbackTests(unittest.TestCase):
    def test_registry_covers_all_pbr_renderer_families(self) -> None:
        renderers = supported_pbr_renderer_keys()
        self.assertEqual(
            renderers,
            [
                "openpbr",
                "physical",
                "arnold",
                "redshift",
                "vray",
                "materialx",
                "octane_standard",
                "octane_pbr",
                "octane_universal",
            ],
        )
        self.assertEqual(len(PBR_RENDERER_REGISTRY), 9)

    def test_hint_lists_primary_material_classes(self) -> None:
        hint = material_class_hint(tool="smart_import")
        supported = hint["supported_material_classes"]
        self.assertIn("OpenPBRMaterial", supported)
        self.assertIn("PhysicalMaterial", supported)
        self.assertIn("ai_standard_surface", supported)
        self.assertIn("RS_Standard_Material", supported)
        self.assertIn("VRayMtl", supported)
        self.assertIn("MaterialX", supported)
        self.assertIn("octane", supported)
        self.assertIn("octane_pbr", supported)
        self.assertIn("octane_universal", supported)
        self.assertNotIn("Shell_Material", supported)

    def test_unsupported_result_includes_full_renderer_list(self) -> None:
        result = unsupported_material_class_result("NoSuchRenderer", tool="smart_import")
        self.assertEqual(result["status"], "error")
        self.assertEqual(len(result["supported_material_classes"]), 9)
        self.assertIn("hint", result)
        self.assertIn("Redshift", result["hint"]["summary"])

    def test_wrap_includes_renderer_label_and_supported_list(self) -> None:
        result = wrap_material_tool_result(
            "smart_import: 2 imported",
            material_class="VRayMtl",
            renderer="vray",
            tool="smart_import",
        )
        self.assertEqual(result["material_renderer"], "vray")
        self.assertIn("V-Ray", result["material_renderer_label"])
        self.assertEqual(len(result["supported_material_classes"]), 9)
        self.assertIn("summary", result["hint"])


if __name__ == "__main__":
    unittest.main()
