"""Mesh input/readback contracts and deterministic evaluated geometry checks."""
import base64
import unittest
from unittest.mock import patch

from maxmcp.tools import mesh_ops, selection


class FakeClient:
    native_available = True

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def send_command(self, script, **kwargs):
        self.calls.append(script)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return {"result":reply}


class MeshToolTests(unittest.TestCase):
    def test_invalid_component_filters_never_reach_max(self):
        bad = [
            {"indices":[]}, {"indices":[True]}, {"indices":[0]}, {"indices":[1.5]},
            {"indices":[1],"current":True}, {"all":"true"},
            {"near":[0,0,0]}, {"near":[0,0,0],"radius":float("nan")},
            {"radius":1}, {"bbox":[1,0,0,0,1,1]}, {"normal":[0,0,0]},
            {"normal":[0,0,1],"angle":181}, {"boundary":True}, {"sharp":30},
            {"typo":True},
        ]
        fake = FakeClient()
        with patch.object(mesh_ops,"client",fake):
            for selector in bad:
                with self.subTest(selector=selector), self.assertRaises(ValueError):
                    mesh_ops.inspect_mesh(name="Chair",level="face",selection=selector)
        self.assertEqual(fake.calls,[])

    def test_entire_batch_validates_before_sending(self):
        invalid = [
            {"op":"chamfer","level":"face","amount":1},
            {"op":"move","offset":[1,2,float("inf")]},
            {"op":"extrude","amount":1,"mode":"typo"},
            {"op":"inset","amount":-1},
            {"op":"connect","level":"edge","segments":0},
            {"op":"connect","level":"edge","pinch":101},
            {"op":"relax","amount":1.5},
            {"op":"delete","arbitrary_code":"delete objects"},
            {"op":"bevel","amount":True},
        ]
        fake=FakeClient()
        with patch.object(mesh_ops,"client",fake):
            for op in invalid:
                with self.subTest(op=op), self.assertRaises(ValueError):
                    mesh_ops.mesh_edit(name="Chair",operations=[{"op":"select","selection":{"all":True}},op])
        self.assertEqual(fake.calls,[])

    def test_polygon_input_rejects_bad_ids_and_nonfinite_coordinates(self):
        fake=FakeClient()
        verts=[[0,0,0],[1,0,0],[0,1,0]]
        with patch.object(mesh_ops,"client",fake):
            for faces in ([[1,2,4]],[[1,1,3]],[[0,1,2]],[[1,2]],[[1,2,True]],[[1,2,[3]]]):
                with self.subTest(faces=faces), self.assertRaises(ValueError):
                    mesh_ops.create_mesh("Chair",verts,faces)
            with self.assertRaises(ValueError):
                mesh_ops.create_mesh("Chair",[[float("nan"),0,0],*verts[1:]],[[1,2,3]])
        self.assertEqual(fake.calls,[])

    def test_inspection_reports_precision_canonical_name_and_truncation(self):
        name="Chair | ö"
        encoded=base64.b64encode(name.encode()).decode()
        fake=FakeClient(f"NAME|{encoded}\nMETA|42|8,12,6|1|TOKEN\nMATCH|8\nSELECTED|1,2|8\nC|1|[1234.56787,-2,3]|1|[0,0,0]\nC|2|[5,6,7]|2|[0,0,0]")
        with patch.object(mesh_ops,"client",fake):
            result=mesh_ops.inspect_mesh(handle=42,level="vertex",limit=2)
        self.assertEqual(result["name"],name)
        self.assertEqual(result["selected_count"],8)
        self.assertEqual(result["selected"],[1,2])
        self.assertEqual(result["components"][0]["center"],[1234.56787,-2,3])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["modifiers_above"],1)

    def test_edit_failure_is_surfaced_without_retry(self):
        fake=FakeClient("__ERROR__|Stale mesh token: inspect_mesh again")
        with patch.object(mesh_ops,"client",fake), self.assertRaisesRegex(RuntimeError,"Stale mesh"):
            mesh_ops.mesh_edit(name="Chair",expected_mesh="old",operations=[{"op":"select"}])
        self.assertEqual(len(fake.calls),1)

    def test_capture_failure_always_unregisters_overlay(self):
        fake=FakeClient('{"owned":false}',"META|42|8,12,6|0|TOKEN",RuntimeError("Capture failed"),"OK")
        with patch.object(mesh_ops,"client",fake), self.assertRaisesRegex(RuntimeError,"Capture failed"):
            mesh_ops.inspect_mesh(name="Chair",capture=True)
        self.assertEqual(len(fake.calls),4)
        self.assertIn("unregisterRedrawViewsCallback",fake.calls[-1])

    def test_explicit_empty_object_selection_clears_selection(self):
        fake=FakeClient("Selected 0 objects")
        with patch.object(selection,"client",fake):
            self.assertEqual(selection.select_objects(names=[]),"Selected 0 objects")
        self.assertIn("clearSelection()",fake.calls[0])


class GeometryQATests(unittest.TestCase):
    """Adversarial indexed meshes check QA meaning, not MAXScript spelling."""

    tetra_vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]]
    tetra_faces = [[1, 3, 2], [1, 2, 4], [2, 3, 4], [3, 1, 4]]

    def analyze(self, vertices=None, faces=None, **kwargs):
        from maxmcp.helpers.geometry_qa import analyze_triangles
        return analyze_triangles(
            self.tetra_vertices if vertices is None else vertices,
            self.tetra_faces if faces is None else faces, **kwargs)

    def test_closed_tetrahedron_and_reversed_neighbor(self):
        result = self.analyze()
        self.assertTrue(all(i["count"] == 0 for i in result["issues"].values()))
        self.assertEqual(result["counts"]["edge_connected_components"], 1)
        self.assertEqual(result["counts"]["unique_edges"], 6)
        flipped = [list(reversed(self.tetra_faces[0])), *self.tetra_faces[1:]]
        result = self.analyze(faces=flipped)
        self.assertEqual(result["issues"]["winding_conflicts"]["count"], 3)
        self.assertEqual(result["issues"]["boundary_edges"]["count"], 0)
        # A consistently inverted shell has no local winding conflict: the
        # tool must not invent an outward-normal certification.
        reversed_shell = [list(reversed(face)) for face in self.tetra_faces]
        self.assertEqual(self.analyze(faces=reversed_shell)["issues"]["winding_conflicts"]["count"], 0)

    def test_open_shell_reports_complete_counts_with_bounded_spatial_samples(self):
        result = self.analyze(faces=self.tetra_faces[1:], limit=1)
        boundary = result["issues"]["boundary_edges"]
        self.assertEqual(boundary["count"], 3)
        self.assertEqual(len(boundary["samples"]), 1)
        self.assertTrue(boundary["truncated"])
        row = boundary["samples"][0]
        a, b = (self.tetra_vertices[v - 1] for v in row["vertices"])
        self.assertEqual(row["center"], [(x + y) / 2 for x, y in zip(a, b)])

    def test_non_manifold_edge_is_not_misreported_as_winding(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1]]
        result = self.analyze(vertices, [[1, 2, 3], [2, 1, 4], [1, 2, 5]])
        self.assertEqual(result["issues"]["non_manifold_edges"]["count"], 1)
        self.assertEqual(result["issues"]["non_manifold_edges"]["samples"][0]["vertices"], [1, 2])
        self.assertEqual(result["issues"]["winding_conflicts"]["count"], 0)
        self.assertEqual(result["issues"]["boundary_edges"]["count"], 6)

    def test_degenerate_duplicate_and_isolated_findings(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 0], [9, 9, 9]]
        faces = [[1, 2, 3], [3, 2, 1], [1, 2, 4], [1, 1, 2]]
        result = self.analyze(vertices, faces)
        self.assertEqual(result["issues"]["degenerate_faces"]["count"], 2)
        self.assertEqual(result["issues"]["duplicate_faces"]["count"], 1)
        self.assertEqual(result["issues"]["duplicate_faces"]["samples"][0]["other_face"], 1)
        self.assertEqual(result["issues"]["isolated_vertices"]["samples"], [{"vertex": 5, "point": [9, 9, 9]}])
        edge = next(e for e in result["issues"]["non_manifold_edges"]["samples"] if e["vertices"] == [1, 2])
        self.assertEqual(edge["face_count"], 4)  # Not five from the repeated index.

    def test_components_use_edges_and_do_not_weld_coincident_positions(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]]
        result = self.analyze(vertices, [[1, 2, 3], [1, 4, 5]])
        self.assertEqual(result["components"]["count"], 2)  # Touch only at a vertex.
        doubled_vertices = self.tetra_vertices + self.tetra_vertices
        doubled_faces = self.tetra_faces + [[v + 4 for v in face] for face in self.tetra_faces]
        result = self.analyze(doubled_vertices, doubled_faces)
        self.assertEqual(result["components"]["count"], 2)
        self.assertEqual(result["issues"]["duplicate_faces"]["count"], 0)

    def test_area_tolerance_is_translation_independent_and_scale_relative(self):
        original = self.analyze()
        translated = [[v * 1e-5 + 2 for v in point] for point in self.tetra_vertices]
        result = self.analyze(translated)
        self.assertEqual(result["issues"]["degenerate_faces"]["count"], 0)
        self.assertAlmostEqual(result["area_epsilon"] / original["area_epsilon"], 1e-10, places=16)
        result = self.analyze(area_epsilon=1.0)
        self.assertEqual(result["issues"]["degenerate_faces"]["count"], 4)

    def test_empty_and_invalid_input_never_look_clean(self):
        result = self.analyze([], [])
        self.assertEqual(result["counts"]["triangles"], 0)
        self.assertIsNone(result["bounds"])
        for vertices, faces in [([[float("nan"), 0, 0]], []), ([[0, 0, 0]], [[1, 1, 2]]),
                                ([[0, 0, 0]], [[True, 1, 1]])]:
            with self.subTest(vertices=vertices, faces=faces), self.assertRaises(ValueError):
                self.analyze(vertices, faces)

    def test_qa_bridge_readback_and_cleanup(self):
        import re
        from pathlib import Path
        from maxmcp.tools import geometry_qa
        name = "Chair | ö"
        encoded = base64.b64encode(name.encode()).decode()
        raw = f"NAME|{encoded}\nMETA|42|3|1|2|160\nV|0,0,0\nV|1,0,0\nV|0,1,0\nF|1,2,3\n"
        paths = []

        def respond(script):
            path = Path(re.search(r'qaFile = createFile "([^"]+)"', script).group(1))
            path.write_bytes(raw.encode("ascii"))
            paths.append(path)
            return {"result": "OK"}

        with patch.object(geometry_qa.client, "send_command", side_effect=respond):
            result = geometry_qa.geometry_qa(handle=42, limit=2)
        self.assertEqual(result["name"], name)
        self.assertEqual(result["handle"], 42)
        self.assertEqual(result["modifiers_above"], 2)
        self.assertEqual(result["time_ticks"], 160)
        self.assertEqual(result["scope"], "evaluated_triangle_mesh")
        self.assertEqual(result["issues"]["boundary_edges"]["count"], 3)
        self.assertEqual(len(result["snapshot_token"]), 24)
        self.assertTrue(result["complete"])
        self.assertFalse(paths[0].exists())
        for broken in (raw.replace("|3|1|", "|4|1|"), raw.replace("F|1,2,3\n", "")):
            with self.subTest(raw=broken), self.assertRaises(RuntimeError):
                geometry_qa._parse_snapshot(broken.encode("ascii"))

    def test_qa_invalid_limits_and_bridge_failures_do_not_retry(self):
        from maxmcp.tools import geometry_qa
        fake = FakeClient()
        with patch.object(geometry_qa, "client", fake):
            for args in ({"limit": 0}, {"max_faces": 500001}, {"area_epsilon": -1},
                         {"area_epsilon": float("inf")}, {"handle": -2}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    geometry_qa.geometry_qa(**args)
        self.assertEqual(fake.calls, [])
        fake = FakeClient("__ERROR__|Geometry QA limit exceeded")
        with patch.object(geometry_qa, "client", fake), self.assertRaisesRegex(RuntimeError, "limit exceeded"):
            geometry_qa.geometry_qa(name="DenseChair")
        self.assertEqual(len(fake.calls), 1)
