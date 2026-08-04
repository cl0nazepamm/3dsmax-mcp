import json
import unittest
from unittest.mock import patch

from maxmcp.tools.tyflow import (
    SHAPE_3D_IDS,
    create_tyflow,
    create_tyflow_preset,
    get_tyflow_info,
    get_tyflow_particle_count,
    get_tyflow_particles,
    list_tyflow_operator_types,
    modify_tyflow_operator,
    set_tyflow_shape,
)


class TyFlowToolTests(unittest.TestCase):
    def test_shape_id_mapping_contains_sphere_fix(self) -> None:
        self.assertEqual(SHAPE_3D_IDS["sphere"], 4)
        self.assertEqual(SHAPE_3D_IDS["pyramid"], 5)

    def test_list_operator_types_parses_json(self) -> None:
        with patch("maxmcp.tools.tyflow.client.send_command", return_value={
            "result": '{"available":["Birth","Shape"],"unavailable":["Fake Op"]}'
        }):
            result = json.loads(list_tyflow_operator_types())

        self.assertEqual(result["available"][0], "Birth")
        self.assertEqual(result["unavailable"][0], "Fake Op")

    def test_create_tyflow_returns_select_result(self) -> None:
        with (
            patch("maxmcp.tools.tyflow.client.send_command", return_value={
                "result": '{"name":"Flow001","event":"Emit","operatorCount":1,"operators":[]}'
            }),
            patch("maxmcp.tools.selection.select_objects", return_value="Selected 1 of 1 objects"),
        ):
            result = json.loads(create_tyflow(name="Flow001", operators=[{"type": "Birth"}]))

        self.assertEqual(result["name"], "Flow001")
        self.assertEqual(result["selectResult"], "Selected 1 of 1 objects")

    def test_modify_operator_requires_properties(self) -> None:
        result = json.loads(modify_tyflow_operator("Flow001", "Emit", "Birth", {}))
        self.assertIn("error", result)

    def test_set_shape_rejects_unknown_shape(self) -> None:
        result = json.loads(set_tyflow_shape("Flow001", shape="unknown_shape"))
        self.assertIn("error", result)

    def test_particle_reads_validate_limits(self) -> None:
        with self.assertRaises(ValueError):
            get_tyflow_particles("Flow001", max_particles=0)

    def test_particle_count_parses_response(self) -> None:
        with patch("maxmcp.tools.tyflow.client.send_command", return_value={"result": '{"name":"Flow001","particleCount":42}'}):
            result = json.loads(get_tyflow_particle_count("Flow001"))

        self.assertEqual(result["particleCount"], 42)

    def test_create_preset_delegates_to_create(self) -> None:
        with patch("maxmcp.tools.tyflow.create_tyflow", return_value='{"name":"ty_rain"}') as mocked_create:
            result = json.loads(create_tyflow_preset("rain"))

        self.assertEqual(result["name"], "ty_rain")
        mocked_create.assert_called_once()

    def test_get_tyflow_info_parses_deep_readback(self) -> None:
        readback = (
            "FLOW|Flow001|tyFlow|123\n"
            "META|eventSubAnimCount|1\n"
            "FP|physXGravityEnabled|true\n"
            "EV|Emit\n"
            "EP|Emit|enabled|true\n"
            "OP|Emit|Birth|tyBirth|2\n"
            "PR|Emit|Birth|birthTotal|1000\n"
            "PR|Emit|Birth|birthMode|0\n"
            "WARN|PR_TRUNCATED|Emit|Birth|20|10\n"
        )
        with patch("maxmcp.tools.tyflow.client.send_command", return_value={"result": readback}):
            result = json.loads(get_tyflow_info("Flow001", include_operator_properties=True))

        self.assertEqual(result["name"], "Flow001")
        self.assertEqual(result["particleCount"], 123)
        self.assertEqual(result["eventSubAnimCount"], 1)
        self.assertEqual(result["flowProperties"][0]["name"], "physXGravityEnabled")
        self.assertEqual(result["events"][0]["name"], "Emit")
        self.assertEqual(result["events"][0]["properties"][0]["name"], "enabled")
        self.assertEqual(result["events"][0]["operators"][0]["class"], "tyBirth")
        self.assertEqual(result["events"][0]["operators"][0]["properties"][0]["name"], "birthTotal")
        self.assertEqual(result["warnings"][0], ["PR_TRUNCATED", "Emit", "Birth", "20", "10"])

    def test_get_tyflow_info_handles_missing_object(self) -> None:
        with patch("maxmcp.tools.tyflow.client.send_command", return_value={"result": "__ERROR__|Object not found: Flow001"}):
            result = json.loads(get_tyflow_info("Flow001"))
        self.assertEqual(result["error"], "Object not found: Flow001")


if __name__ == "__main__":
    unittest.main()
