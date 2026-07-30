"""Offline tests for boolean_operation / draw_spline / edit_vertices: input
normalization, enum mapping, validation errors, and bridge-reply parsing
against a canned fake client."""

import unittest

import src.tools.booleans as booleans
import src.tools.poly_edit as poly_edit
import src.tools.splines as splines


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


if __name__ == "__main__":
    unittest.main()
