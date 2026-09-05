"""Adversarial projection geometry and guarded image-to-cage read sequences."""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from maxmcp.helpers.component_pick import polygon_distance, segment_distance, surface_distance
from maxmcp.tools import component_pick as tool


class Fixture:
    def __init__(self, components, positions, *, hit=True, details=False, modifiers=0, truncated=False):
        self.components = components
        self.positions = positions
        self.details = details
        self.native_calls = []
        self.mesh_calls = []
        self.hit = {"handle": 42, "name": "Chair", "point": [10, 0, 1], "normal": [0, 0, 1]} if hit else None
        self.base = {"name": "Chair", "handle": 42, "mesh_token": "MESH",
            "counts": {"vertices": len(positions), "edges": len(components), "faces": len(components)},
            "modifiers_above": modifiers, "instance_count": 1, "matched": len(components),
            "truncated": truncated}

    def native(self, **kwargs):
        self.native_calls.append(kwargs)
        reply = {"width": 1000, "height": 500, "view_token": "VIEW"}
        if kwargs["action"] == "pick":
            reply["hit"] = self.hit
        elif kwargs["action"] == "project":
            reply["pixels"] = [p[:2] if p[2] >= 0 else None for p in kwargs["points"]]
            if self.details:
                reply["projections"] = [{"pixel": p[:2], "depth": p[2], "in_front": p[2] >= 0,
                    "in_frame": True} for p in kwargs["points"]]
        return reply

    def inspect(self, **kwargs):
        self.mesh_calls.append(kwargs)
        if kwargs["level"] == "vertex" and (kwargs.get("selection") or {}).get("indices"):
            ids = kwargs["selection"]["indices"]
            return {**self.base, "matched": len(ids), "truncated": False,
                "components": [{"id": i, "center": self.positions[i], "vertices": [i]} for i in ids]}
        return {**self.base, "components": self.components}

    def pick(self, **kwargs):
        with patch.object(tool, "agent_viewport", self.native), patch.object(tool, "inspect_mesh", self.inspect):
            return tool.pick_component(x=10/999, y=0, expected_view="VIEW", **kwargs)


class DistanceTests(unittest.TestCase):
    def test_segment_uses_extent_and_handles_degenerate_points(self):
        self.assertEqual(segment_distance([10, 1], [0, 0], [1000, 0]), (1.0, [10.0, 0.0]))
        self.assertEqual(segment_distance([3, 4], [0, 0], [0, 0]), (5.0, [0.0, 0.0]))
        self.assertEqual(segment_distance([-2, 0], [0, 0], [10, 0]), (2.0, [0.0, 0.0]))

    def test_concave_polygon_does_not_fill_its_notch(self):
        polygon = [[0, 0], [4, 0], [4, 1], [1, 1], [1, 4], [0, 4]]
        self.assertEqual(polygon_distance([0.5, 3], polygon), (0.0, [0.5, 3]))
        self.assertEqual(polygon_distance([2, 2], polygon)[0], 1.0)
        self.assertEqual(polygon_distance([1, 2], polygon)[0], 0.0)
        self.assertEqual(polygon_distance([0.5, 3], list(reversed(polygon)))[0], 0.0)

    def test_world_face_distance_uses_plane_and_concave_boundary(self):
        face = [[0, 0, 0], [4, 0, 0], [4, 1, 0], [1, 1, 0], [1, 4, 0], [0, 4, 0]]
        distance, closest, approximate = surface_distance([0.5, 3, 2], face, "face")
        self.assertEqual((distance, closest, approximate), (2.0, [0.5, 3.0, 0.0], False))
        distance, closest, approximate = surface_distance([2, 2, 3], face, "face")
        self.assertAlmostEqual(distance, 10 ** 0.5)
        self.assertIn(closest, ([2.0, 1.0, 0.0], [1.0, 2.0, 0.0]))
        self.assertFalse(approximate)
        face[2][2] = 1
        self.assertTrue(surface_distance([0.5, 3, 2], face, "face")[2])


class ComponentPickTests(unittest.TestCase):
    def edge_fixture(self, **kwargs):
        return Fixture([
            {"id": 12, "vertices": [1, 2], "center": [500, 0, 1]},
            {"id": 3, "vertices": [3, 4], "center": [10, 10, 1]},
        ], {1: [0, 0, 1], 2: [1000, 0, 1], 3: [9, 10, 1], 4: [11, 10, 1]}, **kwargs)

    def test_long_edge_beats_nearby_center_using_installed_pixels_reply(self):
        fixture = self.edge_fixture()
        result = fixture.pick()
        self.assertEqual([c["id"] for c in result["candidates"]], [12, 3])
        self.assertEqual(result["candidates"][0]["distance_pixels"], 0)
        self.assertEqual(result["mesh_token"], "MESH")
        self.assertTrue(result["surface_matches_target"])
        self.assertTrue(result["ambiguous"])
        self.assertFalse(result["visibility_checked"])
        self.assertTrue(all(c["expected_view"] == "VIEW" for c in fixture.native_calls))
        self.assertEqual(fixture.mesh_calls[1]["selection"], {"indices": [1, 2, 3, 4]})
        self.assertEqual(fixture.mesh_calls[1]["name"], "Chair")
        self.assertEqual(fixture.mesh_calls[1]["handle"], 42)

    def test_vertex_positions_are_reused_without_reinspection(self):
        fixture = Fixture([{"id": 1, "vertices": [1], "center": [10, 0, 7]}], {1: [10, 0, 7]}, details=True)
        result = fixture.pick(level="vertex")
        self.assertEqual(len(fixture.mesh_calls), 1)
        self.assertEqual(result["candidates"][0]["vertex_depth_range"], [7, 7])

    def test_projected_face_interior_keeps_front_back_ambiguity(self):
        positions = {1: [0, -10, 1], 2: [20, -10, 1], 3: [20, 10, 1], 4: [0, 10, 1]}
        rows = [
            {"id": 1, "vertices": [1, 2, 3, 4], "center": [10, 0, 1], "normal": [0, 0, -1]},
            {"id": 2, "vertices": [4, 3, 2, 1], "center": [10, 0, 1], "normal": [0, 0, 1]},
        ]
        result = Fixture(rows, positions, modifiers=1).pick(level="face")
        self.assertEqual([c["id"] for c in result["candidates"]], [2, 1])
        self.assertTrue(all(c["distance_pixels"] == 0 for c in result["candidates"]))
        self.assertTrue(result["ambiguous"])
        self.assertTrue(any("Modifiers" in note for note in result["limitations"]))

    def test_auto_miss_requires_explicit_target_for_silhouette(self):
        fixture = self.edge_fixture(hit=False)
        result = fixture.pick()
        self.assertEqual(result["status"], "no_surface_hit")
        self.assertEqual(fixture.mesh_calls, [])
        result = fixture.pick(name="Chair")
        self.assertEqual(result["candidates"][0]["id"], 12)
        self.assertFalse(result["surface_matches_target"])

    def test_other_node_surface_does_not_claim_candidate_visibility(self):
        fixture = self.edge_fixture()
        fixture.hit["handle"] = 99
        result = fixture.pick(handle=42)
        self.assertFalse(result["surface_matches_target"])
        self.assertNotIn("surface_distance", result["candidates"][0])
        self.assertFalse(result["visibility_checked"])

    def test_surface_hit_prefers_front_rim_over_projected_underside(self):
        # The hidden underside aligns more closely in the image, but is much
        # farther from the actual surface hit than the cushion's front rim.
        positions = {1: [0, -18, 42.7], 2: [24, -18, 42.7],
                     3: [0, -25, 43.6], 4: [24, -25, 43.6]}
        fixture = Fixture([
            {"id": 90, "vertices": [1, 2], "center": [12, -18, 42.7]},
            {"id": 30, "vertices": [3, 4], "center": [12, -25, 43.6]},
        ], positions)
        fixture.hit["point"] = [12.18, -25.36, 45.45]
        original = fixture.native
        def native(**kwargs):
            reply = original(**kwargs)
            if kwargs["action"] == "project":
                reply["pixels"] = [[0, 0.55], [1000, 0.55], [0, 6], [1000, 6]]
            return reply
        fixture.native = native
        result = fixture.pick()
        self.assertEqual([row["id"] for row in result["candidates"]], [30, 90])
        front, underside = result["candidates"]
        self.assertGreater(front["distance_pixels"], underside["distance_pixels"])
        self.assertLess(front["surface_distance"], 1.9)
        self.assertGreater(underside["surface_distance"], 7.8)
        self.assertFalse(result["visibility_checked"])
        self.assertEqual([row["id"] for row in fixture.pick(prefer_surface=False)["candidates"]], [90, 30])

    def test_read_and_output_caps_never_report_complete_results(self):
        result = self.edge_fixture(truncated=True).pick(limit=1)
        self.assertTrue(result["inspection_truncated"])
        self.assertTrue(result["candidates_truncated"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["matched_candidates"], 2)
        with patch.object(tool, "_VERTEX_LIMIT", 2):
            result = self.edge_fixture().pick()
        self.assertTrue(result["truncated"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["scope"]["omitted_unprojectable_components"], 1)

    def test_behind_eye_vertices_are_not_joined_into_fake_visible_edges(self):
        fixture = self.edge_fixture(details=True)
        fixture.positions[2][2] = -1
        result = fixture.pick()
        self.assertEqual([c["id"] for c in result["candidates"]], [3])
        self.assertFalse(result["complete"])
        self.assertEqual(result["scope"]["omitted_unprojectable_components"], 1)

    def test_multiple_vertex_and_projection_chunks_keep_all_guards(self):
        positions = {i: [float(i), 0, 1] for i in range(1, 1003)}
        rows = [{"id": i, "vertices": [i*2-1, i*2], "center": [i*2-0.5, 0, 1]} for i in range(1, 502)]
        fixture = Fixture(rows, positions)
        fixture.pick()
        vertex_calls = [c for c in fixture.mesh_calls if c["level"] == "vertex"]
        project_calls = [c for c in fixture.native_calls if c["action"] == "project"]
        self.assertEqual([len(c["selection"]["indices"]) for c in vertex_calls], [1000, 2])
        self.assertEqual([len(c["points"]) for c in project_calls], [1000, 2])
        self.assertTrue(all(c["expected_view"] == "VIEW" for c in project_calls))

    def test_stale_mesh_stops_before_projection(self):
        fixture = self.edge_fixture()
        original = fixture.inspect
        def changed(**kwargs):
            reply = original(**kwargs)
            if kwargs["level"] == "vertex":
                reply["mesh_token"] = "CHANGED"
            return reply
        with patch.object(tool, "agent_viewport", fixture.native), patch.object(tool, "inspect_mesh", changed):
            with self.assertRaisesRegex(RuntimeError, "STALE_MESH"):
                tool.pick_component(0.01, 0, "VIEW")
        self.assertEqual([c["action"] for c in fixture.native_calls], ["pick"])

    def test_stale_projection_and_incomplete_vertex_readback_fail_closed(self):
        fixture = self.edge_fixture()
        original = fixture.native
        def changed(**kwargs):
            reply = original(**kwargs)
            if kwargs["action"] == "project":
                reply["view_token"] = "CHANGED"
            return reply
        with patch.object(tool, "agent_viewport", changed), patch.object(tool, "inspect_mesh", fixture.inspect):
            with self.assertRaisesRegex(RuntimeError, "STALE_VIEW"):
                tool.pick_component(0.01, 0, "VIEW")
        original_inspect = fixture.inspect
        def incomplete(**kwargs):
            reply = original_inspect(**kwargs)
            if kwargs["level"] == "vertex":
                reply["components"] = reply["components"][:-1]
            return reply
        with patch.object(tool, "agent_viewport", fixture.native), patch.object(tool, "inspect_mesh", incomplete):
            with self.assertRaisesRegex(RuntimeError, "Incomplete base vertex"):
                tool.pick_component(0.01, 0, "VIEW")

    def test_empty_filter_still_checks_view_after_mesh_read(self):
        fixture = Fixture([], {})
        result = fixture.pick()
        self.assertEqual(result["status"], "no_component_in_tolerance")
        self.assertEqual([c["action"] for c in fixture.native_calls], ["pick", "ray"])

    def test_validation_precedes_native_or_mesh_reads(self):
        invalid = [{"x": True}, {"x": 1.1}, {"y": float("nan")}, {"expected_view": ""},
            {"tolerance": -0.1}, {"tolerance": float("inf")}, {"limit": 0}, {"handle": True},
            {"level": "knot"}, {"selection": {"normal": [0, 0, 1]}}, {"prefer_surface": "yes"}]
        with patch.object(tool, "agent_viewport") as native, patch.object(tool, "inspect_mesh") as inspect:
            for args in invalid:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    tool.pick_component(**{"x": 0.5, "y": 0.5, "expected_view": "VIEW", **args})
        native.assert_not_called()
        inspect.assert_not_called()

    def test_full_profile_import_order_does_not_require_finished_peer_modules(self):
        env = {**os.environ, "MCP_TOOL_PROFILE": "full"}
        for module in ("mesh_ops", "viewport"):
            with self.subTest(module=module):
                result = subprocess.run([sys.executable, "-c", f"from maxmcp.tools import {module}"],
                    env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
