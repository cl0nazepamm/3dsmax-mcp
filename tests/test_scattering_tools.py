import unittest
from unittest.mock import patch

from src.tools import scattering


class ScatterForestPackTests(unittest.TestCase):
    def test_scatter_forest_pack_generates_expected_maxscript(self) -> None:
        with patch.object(
            scattering.client,
            "send_command",
            return_value={"result": '{"name":"FP_Scatter"}'},
        ) as mocked_send:
            result = scattering.scatter_forest_pack(
                surfaces=["GroundA", "GroundB"],
                geometry=["TreeA", "TreeB"],
                probabilities=[0.7, 0.3],
                density=250,
                seed=77,
                scale_min=85.0,
                scale_max=130.0,
                z_rotation_min=-90.0,
                z_rotation_max=90.0,
                source_width_cm=2.5,
                source_height_cm=6.5,
                icon_size_cm=45.0,
                density_units_x_cm=800.0,
                density_units_y_cm=200.0,
                facing_mode=1,
                name='FP "Scatter"',
            )

        self.assertEqual(result, '{"name":"FP_Scatter"}')
        mocked_send.assert_called_once()
        maxscript = mocked_send.call_args.args[0]

        self.assertIn('Forest_Pro name:"FP \\"Scatter\\""', maxscript)
        self.assertIn('local surfaceNames = #("GroundA", "GroundB")', maxscript)
        self.assertIn('local geometryNames = #("TreeA", "TreeB")', maxscript)
        self.assertIn("local probValues = #(0.700000, 0.300000)", maxscript)
        self.assertIn("fp.arnodelist = areaNodeList", maxscript)
        self.assertIn("fp.arnamelist = areaNameList", maxscript)
        self.assertIn("fp.pf_aractivelist = areaActiveList", maxscript)
        self.assertIn("fp.arprojectlist = areaProjectList", maxscript)
        self.assertIn("fp.maxdensity = 250", maxscript)
        self.assertIn("fp.seed = 77", maxscript)
        self.assertIn('local sourceWidthWU = units.decodeValue "2.5cm"', maxscript)
        self.assertIn('local sourceHeightWU = units.decodeValue "6.5cm"', maxscript)
        self.assertIn('local iconSizeWU = units.decodeValue "45.0cm"', maxscript)
        self.assertIn('local densityUnitsXWU = units.decodeValue "800.0cm"', maxscript)
        self.assertIn('local densityUnitsYWU = units.decodeValue "200.0cm"', maxscript)
        self.assertIn("fp.widthlist = #(sourceWidthWU)", maxscript)
        self.assertIn("fp.heightlist = #(sourceHeightWU)", maxscript)
        self.assertIn("fp.units_x = densityUnitsXWU", maxscript)
        self.assertIn("fp.units_y = densityUnitsYWU", maxscript)
        self.assertIn("fp.iconSize = iconSizeWU", maxscript)
        self.assertIn("fp.direction = 1", maxscript)
        self.assertIn("fp.scalexmin = 85.0", maxscript)
        self.assertIn("fp.scalexmax = 130.0", maxscript)
        self.assertIn("fp.zrotmin = -90.0", maxscript)
        self.assertIn("fp.zrotmax = 90.0", maxscript)

    def test_scatter_forest_pack_uses_equal_default_weights(self) -> None:
        with patch.object(
            scattering.client,
            "send_command",
            return_value={"result": "{}"},
        ) as mocked_send:
            scattering.scatter_forest_pack(
                surfaces=["Ground"],
                geometry=["TreeA", "TreeB", "TreeC"],
            )

        maxscript = mocked_send.call_args.args[0]
        self.assertIn("local probValues = #(1.000000, 1.000000, 1.000000)", maxscript)

    def test_scatter_forest_pack_validates_probability_count(self) -> None:
        with self.assertRaises(ValueError):
            scattering.scatter_forest_pack(
                surfaces=["Ground"],
                geometry=["TreeA", "TreeB"],
                probabilities=[1.0],
            )

    def test_scatter_forest_pack_requires_inputs(self) -> None:
        with self.assertRaises(ValueError):
            scattering.scatter_forest_pack(surfaces=[], geometry=["TreeA"])
        with self.assertRaises(ValueError):
            scattering.scatter_forest_pack(surfaces=["Ground"], geometry=[])

    def test_scatter_forest_pack_validates_facing_mode(self) -> None:
        with self.assertRaises(ValueError):
            scattering.scatter_forest_pack(
                surfaces=["Ground"],
                geometry=["TreeA"],
                facing_mode=3,
            )


if __name__ == "__main__":
    unittest.main()
