"""Offline tests for boolean_operation / draw_spline / edit_vertices: input
normalization, enum mapping, validation errors, and bridge-reply parsing
against a canned fake client."""

import unittest

import maxmcp.tools.booleans as booleans
import maxmcp.tools.poly_edit as poly_edit
import maxmcp.tools.splines as splines


def unwrap(tool):
    return getattr(tool, "fn", None) or getattr(tool, "__wrapped__", None) or tool


boolean_operation = unwrap(booleans.boolean_operation)
draw_spline = unwrap(splines.draw_spline)
edit_vertices = unwrap(poly_edit.edit_vertices)


class FakeClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.scripts = []

    def send_command(self, script, cmd_type=None, **kwargs):
        self.scripts.append(script)
        return {"result": self.replies.pop(0) if self.replies else ""}


class Patched(unittest.TestCase):
    module = None

    def use(self, *replies):
        fake = FakeClient(*replies)
        self._saved = self.module.client
        self.module.client = fake
        self.addCleanup(setattr, self.module, "client", self._saved)
        return fake


class TestBooleanValidation(Patched):
    module = booleans

    def test_op_mapping(self):
        self.assertEqual(booleans._op_enum("Subtract"), "#subtraction")
        self.assertEqual(booleans._op_enum("union"), "#union")
        self.assertIsNone(booleans._op_enum("dissolve"))

    def test_apply_requires_operands(self):
        r = boolean_operation(action="apply", name="Base")
        self.assertEqual(r["status"], "error")

    def test_self_operand_rejected(self):
        r = boolean_operation(action="apply", name="Base", operands=["base"])
        self.assertIn("itself", r["error"])

    def test_bad_operation_rejected(self):
        r = boolean_operation(action="apply", name="Base", operands=["Cut"], operation="dissolve")
        self.assertEqual(r["status"], "error")
        self.assertIn("valid", r["details"])

    def test_apply_parses_reply(self):
        fake = self.use("OK|Boolean|true|3|144|")
        r = boolean_operation(action="apply", name="Base", operands=["A", "B"], operation="subtract")
        self.assertEqual(r["operands_total"], 3)
        self.assertEqual(r["tris"], 144)
        self.assertEqual(r["consumed"], ["A", "B"])
        self.assertIn("#subtraction", fake.scripts[0])

    def test_apply_reports_failures(self):
        self.use("OK|Boolean|false|2|100|B, ")
        r = boolean_operation(action="apply", name="Base", operands=["A", "B"])
        self.assertEqual(r["failed"], ["B"])
        self.assertEqual(r["appended"], ["A"])

    def test_list_parses_operands(self):
        self.use(
            "OPER|1|[Base Object]|modified|union|none|false\n"
            "OPER|2|hole|single|subtraction|cookie|false\n"
            "INFO|Boolean|0|false|112\n"
        )
        r = boolean_operation(action="list", name="Base")
        self.assertEqual(r["method"], "mesh")
        self.assertEqual(len(r["operands"]), 2)
        self.assertEqual(r["operands"][1]["operation"], "subtraction")

    def test_set_operand_needs_an_edit(self):
        r = boolean_operation(action="set_operand", name="Base", operand_index=2)
        self.assertEqual(r["status"], "error")

    def test_set_operand_index_required(self):
        r = boolean_operation(action="set_operand", name="Base", rename="x")
        self.assertIn("operand_index", r["error"])


class TestSplineNormalization(Patched):
    module = splines

    def test_list_of_triples(self):
        pts, err = splines._normalize_points([[0, 0, 0], [1, 2, 3]], "smooth")
        self.assertEqual(err, "")
        self.assertEqual(pts[1]["pos"], [1.0, 2.0, 3.0])

    def test_flat_list(self):
        pts, err = splines._normalize_points([0, 0, 0, 1, 2, 3], "corner")
        self.assertEqual(err, "")
        self.assertEqual(len(pts), 2)

    def test_flat_list_bad_length(self):
        _, err = splines._normalize_points([0, 0, 0, 1], "corner")
        self.assertIn("divisible by 3", err)

    def test_json_string(self):
        pts, err = splines._normalize_points("[[0,0,0],[5,5,5]]", "smooth")
        self.assertEqual(err, "")
        self.assertEqual(len(pts), 2)

    def test_dict_points_with_handles(self):
        pts, err = splines._normalize_points(
            [{"pos": [0, 0, 0], "type": "bezier", "in": [-1, 0, 0], "out": [1, 0, 0]}], "smooth")
        self.assertEqual(err, "")
        self.assertEqual(pts[0]["in"], [-1.0, 0.0, 0.0])

    def test_bezier_without_handles_rejected(self):
        _, err = splines._normalize_points([{"pos": [0, 0, 0], "type": "bezier"}], "smooth")
        self.assertIn("in_vec", err)

    def test_parse_p3(self):
        self.assertEqual(splines._parse_p3("[1.5,-2,3e2]"), [1.5, -2.0, 300.0])
        self.assertEqual(splines._parse_p3("garbage"), [0.0, 0.0, 0.0])

    def test_create_needs_two_points(self):
        r = draw_spline(action="create", name="S", points=[[0, 0, 0]])
        self.assertEqual(r["status"], "error")

    def test_create_parses_summary(self):
        fake = self.use("OK|S|1|4|189.09|[760,900,-10]|[840,900,30]")
        r = draw_spline(action="create", name="S",
                        points=[[0, 0, 0], [10, 0, 0], [10, 10, 0]], closed=True)
        self.assertEqual(r["knots"], 4)
        self.assertAlmostEqual(r["length"], 189.09)
        self.assertIn("close ss 1", fake.scripts[0])

    def test_set_knots_requires_knots(self):
        r = draw_spline(action="set_knots", name="S")
        self.assertEqual(r["status"], "error")

    def test_unknown_action(self):
        r = draw_spline(action="warp", name="S")
        self.assertIn("unknown action", r["error"])


class TestPolyEditValidation(Patched):
    module = poly_edit

    def test_positions_normalization(self):
        ps, err = poly_edit._normalize_positions([[0, 0, 0], [1, 1, 1]])
        self.assertEqual(err, "")
        self.assertEqual(len(ps), 2)
        ps, err = poly_edit._normalize_positions([0, 0, 0, 1, 1, 1])
        self.assertEqual(err, "")
        self.assertEqual(ps[1], [1.0, 1.0, 1.0])

    def test_move_needs_offset(self):
        r = edit_vertices(action="move", name="M")
        self.assertEqual(r["status"], "error")

    def test_set_needs_parallel_lists(self):
        r = edit_vertices(action="set", name="M", indices=[1, 2], positions=[[0, 0, 0]])
        self.assertIn("parallel", r["error"])

    def test_conform_validates_axes(self):
        r = edit_vertices(action="conform", name="M", target="T", axes="xq")
        self.assertIn("axes", r["error"])

    def test_conform_validates_axis_token(self):
        r = edit_vertices(action="conform", name="M", target="T", axis="diag")
        self.assertEqual(r["status"], "error")

    def test_get_parses_verts_and_meta(self):
        self.use("V|1|[780,780,20]\nV|25|[800,800,50]\nMETA|49|2|2|0\n")
        r = edit_vertices(action="get", name="M", indices=[1, 25])
        self.assertEqual(r["total_verts"], 49)
        self.assertEqual(r["verts"][1]["pos"], [800.0, 800.0, 50.0])

    def test_conform_parses_reply(self):
        self.use("OK|48|1|mesh|false")
        r = edit_vertices(action="conform", name="M", target="T", axis="-z")
        self.assertEqual(r["conformed"], 48)
        self.assertEqual(r["mode"], "mesh")
        self.assertIn("note", r)


class TestInlineCutters(Patched):
    module = booleans

    def test_repeat_expansion_names_and_offsets(self):
        inst, err = booleans._normalize_cutters(
            [{"name": "vent", "size": [10, 4, 30], "pos": [100, 0, 0]}],
            {"count": 3, "axis": "-x", "spacing": 12},
            "#subtraction",
        )
        self.assertEqual(err, "")
        self.assertEqual([i["name"] for i in inst], ["vent_1", "vent_2", "vent_3"])
        self.assertEqual([i["pos"][0] for i in inst], [100.0, 88.0, 76.0])

    def test_single_count_keeps_bare_name_and_scalar_size(self):
        inst, err = booleans._normalize_cutters(
            [{"name": "hole", "shape": "cylinder", "size": 20}], {}, "#subtraction"
        )
        self.assertEqual(err, "")
        self.assertEqual(inst[0]["name"], "hole")
        self.assertEqual(inst[0]["size"], [20.0, 20.0, 20.0])

    def test_bad_shape_rejected(self):
        _, err = booleans._normalize_cutters([{"name": "x", "shape": "torus", "size": 5}], {}, "#union")
        self.assertIn("shape", err)

    def test_missing_name_rejected(self):
        _, err = booleans._normalize_cutters([{"size": 5}], {}, "#union")
        self.assertIn("name", err)

    def test_zero_size_rejected(self):
        _, err = booleans._normalize_cutters([{"name": "x", "size": [5, 0, 5]}], {}, "#union")
        self.assertIn("> 0", err)

    def test_spacing_required_for_multi(self):
        _, err = booleans._normalize_cutters(
            [{"name": "x", "size": 5}], {"count": 2, "axis": "x"}, "#union"
        )
        self.assertIn("spacing", err)

    def test_instance_cap(self):
        _, err = booleans._normalize_cutters(
            [{"name": "x", "size": 5}], {"count": 201, "axis": "x", "spacing": 1}, "#union"
        )
        self.assertIn("cap", err)

    def test_repeat_requires_cutters(self):
        r = boolean_operation(action="apply", name="Base", repeat={"count": 2})
        self.assertEqual(r["status"], "error")
        self.assertIn("repeat requires cutters", r["error"])

    def test_cutter_named_base_rejected(self):
        r = boolean_operation(
            action="apply", name="Base", cutters=[{"name": "base", "size": 5}]
        )
        self.assertEqual(r["status"], "error")
        self.assertIn("base object", r["error"])

    def test_duplicate_names_rejected(self):
        self.use("")
        r = boolean_operation(
            action="apply", name="Base", operands=["A"], cutters=[{"name": "a", "size": 5}]
        )
        self.assertEqual(r["status"], "error")
        self.assertIn("unique", r["error"])

    def test_apply_cutters_only_script_and_reply(self):
        fake = self.use("OK|Boolean|true|2|321|")
        r = boolean_operation(
            action="apply",
            name="Base",
            cutters=[{"name": "hole", "shape": "cylinder", "size": [10, 10, 60]}],
        )
        self.assertEqual(r["cutters_created"], ["hole"])
        self.assertIn("hole", r["appended"])
        self.assertIn("hole", r["consumed"])
        script = fake.scripts[0]
        self.assertIn("cutDefs", script)
        self.assertIn("Cylinder radius:", script)
        self.assertIn("findItem madeCutters", script)

    def test_apply_mixed_operands_and_cutters(self):
        fake = self.use("OK|Boolean|false|3|500|")
        r = boolean_operation(
            action="apply",
            name="Base",
            operands=["Cap"],
            operation="union",
            cutters=[{"name": "slot", "size": [4, 40, 30], "operation": "subtract"}],
        )
        self.assertEqual(sorted(r["appended"]), ["Cap", "slot"])
        script = fake.scripts[0]
        self.assertIn('"Cap"', script)
        self.assertIn('"slot"', script)
        self.assertIn("#subtraction", script)
        self.assertIn("#union", script)

    def test_no_cutter_block_without_cutters(self):
        fake = self.use("OK|Boolean|true|1|100|")
        boolean_operation(action="apply", name="Base", operands=["A"])
        self.assertNotIn("cutDefs", fake.scripts[0])


if __name__ == "__main__":
    unittest.main()
