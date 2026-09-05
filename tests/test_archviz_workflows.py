"""Contracts exercised by the live pavilion modeling workflow."""
import json
import unittest
from unittest.mock import patch

from maxmcp.tools import clone, viewport, booleans


class FakeClient:
    native_available = True

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def send_command(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return {"result": self.replies.pop(0)}


class ArchvizWorkflowTests(unittest.TestCase):
    def test_bad_array_inputs_do_not_mutate(self):
        fake = FakeClient()
        with patch.object(clone, "client", fake):
            for args in [{"count": 0}, {"count": 1.5}, {"count": True},
                         {"count": 201}, {"mode": "typo"},
                         {"offset": [1, 2]}, {"offset": [float("nan"), 0, 0]}]:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    clone.clone_objects(names=["Beam"], **args)
        self.assertEqual(fake.calls, [])

    def test_array_returns_actual_handles_and_two_bridge_calls(self):
        nodes = [{"name": "Beam001", "handle": 12001, "class": "Box"},
                 {"name": "Beam002", "handle": 12002, "class": "Box"}]
        fake = FakeClient("12001,12002,", json.dumps({"nodes": nodes}))
        with patch.object(clone, "client", fake):
            result = json.loads(clone.clone_objects(["Beam", "Beam"], count=2, offset=[60, 0, 0]))
        self.assertEqual(result["cloned"], ["Beam001", "Beam002"])
        self.assertEqual([n["handle"] for n in result["nodes"]], [12001, 12002])
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("getAnimByHandle", fake.calls[1][0])

    def test_array_failure_is_not_retried(self):
        fake = FakeClient("__ERROR__|Sources must resolve uniquely: Missing")
        with patch.object(clone, "client", fake), self.assertRaisesRegex(RuntimeError, "Missing"):
            clone.clone_objects(["Missing"], count=2)
        self.assertEqual(len(fake.calls), 1)

    def test_single_clone_deduplicates_sources(self):
        fake = FakeClient('{"cloned":[],"nodes":[]}')
        with patch.object(clone, "client", fake):
            clone.clone_objects(["Beam", "Beam"])
        self.assertEqual(json.loads(fake.calls[0][0])["names"], ["Beam"])

    def test_bad_view_inputs_do_not_mutate(self):
        fake = FakeClient()
        cases = [{"view": "typo"}, {"eye": [1, 2, 3]},
                 {"eye": [1, 2, 3], "target": [1, 2, 3]},
                 {"view": "front", "eye": [0, 1, 2], "target": [0, 0, 0]},
                 {"fov": float("inf")}, {"padding": 0.9}, {"shading": "unknown"}]
        with patch.object(viewport, "client", fake):
            for args in cases:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    viewport.set_viewport(**args)
        self.assertEqual(fake.calls, [])

    def test_viewport_reports_actual_readback(self):
        fake = FakeClient("OK|view_persp_user|48.0")
        with patch.object(viewport, "client", fake):
            result = viewport.set_viewport(frame_names=["Pavilion", "Pavilion"], fov=48, source="active")
        self.assertEqual(result["actual_view"], "view_persp_user")
        self.assertEqual(result["framed"], ["Pavilion"])
        self.assertEqual(result["fov"], 48)

    def test_orthographic_zoom_is_not_reported_as_camera_fov(self):
        fake = FakeClient("OK|view_front|212.91")
        with patch.object(viewport,"client",fake):
            result=viewport.set_viewport(view="front",source="active")
        self.assertEqual(result["actual_view"],"view_front")
        self.assertIsNone(result["fov"])

    def test_cutter_segments_and_finite_coordinates(self):
        for segments in [3, 257, 12.5, True]:
            _, error = booleans._normalize_cutters(
                [{"name": "arch", "size": 10, "segments": segments}], {}, "#subtraction")
            self.assertIn("segments", error)
        for bad in [float("nan"), float("inf")]:
            _, error = booleans._normalize_cutters(
                [{"name": "arch", "size": 10, "pos": [bad, 0, 0]}], {}, "#subtraction")
            self.assertIn("finite", error)
        cutters, error = booleans._normalize_cutters(
            [{"name": "arch", "shape": "cylinder", "size": [210,210,80]}], {}, "#subtraction")
        self.assertEqual(error, "")
        self.assertEqual(cutters[0]["segments"], 64)

    def test_cutter_expansion_limit_before_allocation(self):
        _, error = booleans._normalize_cutters(
            [{"name": "arch", "size": 10}],
            {"count": 1000000000, "spacing": 10}, "#subtraction")
        self.assertIn("cap", error)


if __name__ == "__main__":
    unittest.main()
