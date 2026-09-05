"""Construction math/security and persistent loft parameter contracts."""
import copy
import json
import math
import unittest
from unittest.mock import patch

from maxmcp.helpers.geometry_qa import analyze_triangles
from maxmcp.helpers.loft import build_loft, coordinate, validate_parameters
from maxmcp.tools import loft, mesh_ops


PARAMETERS = {"width": 10.0, "depth": 4.0, "height": 2.0}
SECTIONS = [
    [["-width/2", "-depth/2", 0], ["width/2", "-depth/2", 0],
     ["width/2", "depth/2", 0], ["-width/2", "depth/2", 0]],
    [["-width/2", "-depth/2", "height"], ["width/2", "-depth/2", "height"],
     ["width/2", "depth/2", "height"], ["-width/2", "depth/2", "height"]],
]
TOKEN_A = "-".join(["AA"] * 32)
TOKEN_B = "-".join(["BB"] * 32)
TOKEN_C = "-".join(["CC"] * 32)


def fixture():
    _, _, definition = build_loft(SECTIONS, PARAMETERS, caps=True)
    data = {"schema": loft.SCHEMA, "version": loft.VERSION, "origin": [0, 0, 1],
            "definition": definition,
            "history": [{"parameters": dict(PARAMETERS), "fingerprint": TOKEN_A}]}
    identity = {"name": "Chair", "handle": 42, "counts": {"vertices": 8, "edges": 12, "faces": 6},
                "modifiers_above": 2, "instance_count": 1,
                "cage_fingerprint": TOKEN_A, "mesh_token": "CURRENT_MESH"}
    return identity, data, json.dumps(data)


class LoftMathTests(unittest.TestCase):
    def test_capped_quads_have_consistent_winding_and_positive_volume(self):
        vertices, faces, definition = build_loft(SECTIONS, PARAMETERS, caps=True)
        self.assertEqual(len(vertices), 8)
        self.assertEqual(len(faces), 6)
        self.assertTrue(all(len(face) == 4 for face in faces))
        triangles = [[f[0], f[i], f[i + 1]] for f in faces for i in range(1, len(f) - 1)]
        report = analyze_triangles(vertices, triangles)
        self.assertTrue(all(i["count"] == 0 for i in report["issues"].values()))
        volume = 0
        for face in triangles:
            a, b, c = [vertices[i - 1] for i in face]
            volume += (a[0] * (b[1] * c[2] - b[2] * c[1])
                       + a[1] * (b[2] * c[0] - b[0] * c[2])
                       + a[2] * (b[0] * c[1] - b[1] * c[0])) / 6
        self.assertAlmostEqual(volume, 80)
        wider, same_faces, same_definition = build_loft(parameters={**PARAMETERS, "width": 20}, **definition)
        self.assertEqual(faces, same_faces)
        self.assertEqual(definition, same_definition)
        self.assertEqual(wider[0], [-10, -2, 0])
        _, reverse_faces, _ = build_loft(SECTIONS, PARAMETERS, caps=True, reverse=True)
        self.assertEqual(reverse_faces, [list(reversed(f)) for f in faces])

    def test_open_strip_and_closed_path_share_only_intended_edges(self):
        vertices, faces, _ = build_loft([[[0, 0, 0], [1, 0, 0]], [[0, 1, 1], [1, 1, 1]]], profile_closed=False)
        self.assertEqual(len(faces), 1)
        self.assertEqual(len(vertices), 4)
        rings = []
        for i in range(12):
            u = i * 2 * math.pi / 12
            rings.append([[(5 + math.cos(v)) * math.cos(u), (5 + math.cos(v)) * math.sin(u), math.sin(v)]
                          for v in [j * 2 * math.pi / 8 for j in range(8)]])
        vertices, faces, _ = build_loft(rings, close_path=True)
        triangles = [[f[0], f[i], f[i + 1]] for f in faces for i in range(1, len(f) - 1)]
        report = analyze_triangles(vertices, triangles)
        self.assertEqual(report["issues"]["boundary_edges"]["count"], 0)
        self.assertEqual(report["issues"]["winding_conflicts"]["count"], 0)
        self.assertEqual(len(faces), 96)

    def test_arithmetic_math_and_bounded_evaluation(self):
        self.assertAlmostEqual(coordinate("r*cos(pi/4)", {"r": 10}), math.sqrt(50))
        self.assertEqual(coordinate("max(abs(-4), sqrt(9))+min(2,3)", {}), 6)
        self.assertEqual(coordinate("2**3 + 7%4", {}), 11)
        for expr in ("__import__('os').system('whoami')", "(1).__class__", "[x for x in [1]]",
                     "sin(x=2)", "pi[0]", "lambda: 1", "unknown+1", "1/0", "sqrt(-1)",
                     "2**10000000", "1e300", "True", "'123'", "min()", "abs(1,2)",
                     "2**-1000000", "(-1)**0.5", "1+" * 200 + "1"):
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                coordinate(expr, {})
        for parameters in ({"pi": 3}, {"sqrt": 2}, {"__class__": 1}, {"x": True}, {"x": float("nan")}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                validate_parameters(parameters)

    def test_correspondence_and_closure_errors_fail_before_creation(self):
        bad = [
            (SECTIONS[:1], {}),
            ([SECTIONS[0], SECTIONS[1][:-1]], {}),
            ([SECTIONS[0], SECTIONS[0]], {}),
            ([SECTIONS[0] + [SECTIONS[0][0]], SECTIONS[1] + [SECTIONS[1][0]]], {}),
            (SECTIONS, {"caps": True, "profile_closed": False}),
            ([*SECTIONS, SECTIONS[0]], {"close_path": True}),
            (SECTIONS, {"profile_closed": "true"}),
        ]
        for sections, options in bad:
            with self.subTest(options=options), self.assertRaises(ValueError):
                build_loft(sections, PARAMETERS, **options)


class LoftPersistenceTests(unittest.TestCase):
    def test_reads_are_compact_unless_full_definition_is_requested(self):
        identity, data, raw = fixture()
        with patch.object(loft, "_read", return_value=(identity, data, raw)):
            compact = loft.loft_mesh(action="read", handle=42)
            full = loft.loft_mesh(action="read", handle=42, include_definition=True)
        self.assertNotIn("definition", compact)
        self.assertEqual(compact["parameters"], PARAMETERS)
        self.assertEqual(compact["counts"], identity["counts"])
        self.assertTrue(compact["cage_matches_definition"])
        self.assertEqual(full["definition"], data["definition"])
        self.assertEqual({k: v for k, v in full.items() if k != "definition"}, compact)
        with patch.object(loft, "_read") as read, self.assertRaises(ValueError):
            loft.loft_mesh(action="read", handle=42, include_definition="true")
        read.assert_not_called()

    def test_create_attaches_definition_inside_existing_mesh_transaction(self):
        identity, stored, raw = fixture()
        with patch.object(mesh_ops, "_create_mesh", return_value={"handle": 42}) as create, \
                patch.object(loft, "_read", return_value=(identity, stored, raw)):
            result = loft.loft_mesh(name="Chair", sections=SECTIONS, parameters=PARAMETERS, caps=True)
        self.assertEqual(result["parameters"], PARAMETERS)
        self.assertEqual(result["modifiers_above"], 2)
        self.assertEqual(create.call_args.args[2][-1], [5, 6, 7, 8])
        # Definition installation is passed to the shared creation hold rather
        # than issuing a second nontransactional tool write after creation.
        self.assertIn("setAppData obj", create.call_args.kwargs["before_accept"])

    def test_undo_reconciles_retained_state_and_unknown_cage_blocks(self):
        identity, data, _ = fixture()
        data["history"].append({"parameters": {**PARAMETERS, "width": 20}, "fingerprint": TOKEN_B})
        self.assertEqual(loft._public(identity, data, include_definition=True)["parameters"]["width"], 10)
        identity["cage_fingerprint"] = TOKEN_B
        self.assertEqual(loft._public(identity, data, include_definition=True)["parameters"]["width"], 20)
        identity["cage_fingerprint"] = TOKEN_C
        result = loft._public(identity, data, include_definition=True)
        self.assertIsNone(result["parameters"])
        self.assertFalse(result["cage_matches_definition"])
        with patch.object(loft, "_read", return_value=(identity, data, json.dumps(data))), \
                patch.object(loft, "_run") as run, self.assertRaisesRegex(RuntimeError, "cage changed"):
            loft.loft_mesh(action="update", name="Chair", parameters={"width": 12})
        run.assert_not_called()

    def test_unknown_parameters_instances_and_invalid_geometry_never_write(self):
        for overrides, changes, exception in [({}, {"unknown": 1}, ValueError),
                                              ({"instance_count": 2}, {"width": 12}, RuntimeError),
                                              ({}, {"width": 0}, ValueError)]:
            identity, data, raw = fixture()
            identity.update(overrides)
            with self.subTest(changes=changes, overrides=overrides), \
                    patch.object(loft, "_read", return_value=(identity, data, raw)), \
                    patch.object(loft, "_run") as run, self.assertRaises(exception):
                loft.loft_mesh(action="update", name="Chair", parameters=changes)
            run.assert_not_called()

    def test_update_preserves_definition_and_verifies_new_persisted_parameters(self):
        before, data, raw = fixture()
        after = {**before, "cage_fingerprint": TOKEN_B}
        updated = copy.deepcopy(data)
        updated["history"].append({"parameters": {**PARAMETERS, "width": 14}, "fingerprint": TOKEN_B})
        with patch.object(loft, "_read", side_effect=[(before, data, raw), (after, updated, json.dumps(updated))]), \
                patch.object(loft, "_run", return_value=TOKEN_B):
            result = loft.loft_mesh(action="update", handle=42, parameters={"width": 14})
        self.assertEqual(result["parameters"]["width"], 14)
        self.assertEqual(result["changed_parameters"]["width"], {"before": 10, "after": 14})
        self.assertEqual(result["modifiers_above"], 2)
        self.assertEqual(result["counts"], before["counts"])
        self.assertEqual(updated["definition"], data["definition"])
        self.assertEqual(updated["origin"], data["origin"])

    def test_unsupported_corrupt_or_oversized_persistence_is_rejected(self):
        _, data, raw = fixture()
        self.assertEqual(loft._parse_definition(raw), data)
        for changed in ({**data, "version": 99}, {**data, "history": []},
                        {**data, "origin": [0, 1]},
                        {**data, "history": [{"parameters": None, "fingerprint": TOKEN_A}]},
                        {**data, "history": [{"parameters": PARAMETERS, "fingerprint": "oops"}]}):
            with self.subTest(data=changed), self.assertRaises(ValueError):
                loft._parse_definition(json.dumps(changed))
        with self.assertRaises(ValueError):
            loft._parse_definition(" " * (loft.MAX_DEFINITION_BYTES + 1))

    def test_identical_parameters_are_noop_and_ambiguous_geometry_is_not_guessed(self):
        identity, data, raw = fixture()
        with patch.object(loft, "_read", return_value=(identity, data, raw)), \
                patch.object(loft, "_run") as run:
            result = loft.loft_mesh(action="update", name="Chair", parameters={"width": 10})
        self.assertTrue(result["unchanged"])
        run.assert_not_called()
        data["history"].append({"parameters": {**PARAMETERS, "width": 11}, "fingerprint": TOKEN_A})
        self.assertIsNone(loft._active_revision(data, TOKEN_A))

    def test_update_does_not_claim_verified_success_if_readback_changed(self):
        for changed_geometry in (True, False):
            identity, data, raw = fixture()
            after = {**identity, "cage_fingerprint": TOKEN_C if changed_geometry else TOKEN_B}
            stale_data = copy.deepcopy(data)
            stale_data["history"].append({"parameters": PARAMETERS, "fingerprint": TOKEN_B})
            with self.subTest(changed_geometry=changed_geometry), \
                    patch.object(loft, "_read", side_effect=[(identity, data, raw), (after, stale_data, json.dumps(stale_data))]), \
                    patch.object(loft, "_run", return_value=TOKEN_B), self.assertRaisesRegex(RuntimeError, "committed"):
                loft.loft_mesh(action="update", handle=42, parameters={"width": 14})

    def test_definition_limit_includes_fingerprint_expansion(self):
        data = {"fingerprint": loft._TOKEN_MARKER}
        encoded = json.dumps(data, separators=(",", ":"))
        full_size = len(encoded) + 95 - len(loft._TOKEN_MARKER)
        with patch.object(loft, "MAX_DEFINITION_BYTES", full_size - 1), self.assertRaises(ValueError):
            loft._json(data)
        with patch.object(loft, "MAX_DEFINITION_BYTES", full_size):
            self.assertEqual(loft._json(data), encoded)


if __name__ == "__main__":
    unittest.main()
