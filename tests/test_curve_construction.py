"""Geometric invariants and construction persistence, with no live Max calls."""
import copy
import json
import math
import unittest
from unittest.mock import patch

from maxmcp.helpers.curves import (build_model, make_curve, curve_qa, dot, sub,
                                  cross, length, unit, transport)
from maxmcp.helpers.geometry_qa import analyze_triangles
from maxmcp.tools import curve_model as model, curve_edit
from maxmcp.max_client import MaxClient
from maxmcp.helpers import curve_runtime


PARAMS={"width":4.,"depth":2.,"radius":.4,"height":20.,"bow":4.}
RECIPE={
    "curves":{
        "section":{"kind":"rounded_rectangle","width":"width","depth":"depth","radius":"radius"},
        "rail":{"kind":"spline","plane":"xz","points":[[0,0],["bow","height/2"],[0,"height"]]},
    },
    "output":{"kind":"sweep","profile":"section","path":"rail","up":[0,1,0],
              "path_samples":24,"profile_samples":32,"scale":[1,.7],"twist":15},
}
A="-".join(["AA"]*32)
B="-".join(["BB"]*32)


def fixture(params=None,token=A):
    params=dict(PARAMS if params is None else params)
    built=build_model(RECIPE,params)
    data={"schema":model.SCHEMA,"version":model.VERSION,"definition":copy.deepcopy(RECIPE),
          "kind":"sweep","origin":[0,0,10],"alignment":None,"topology":model._topology(built),
          "history":[{"parameters":params,"fingerprint":token}]}
    identity={"name":"Armrest","handle":42,"fingerprint":token,"modifiers_above":1,"instance_count":1}
    return identity,data,model.encode(data)


def topology_report(built):
    vertices=list(built["vertices"]); triangles=[]
    for face in built["faces"]:
        if len(face)<=4:
            triangles.extend([face[0],face[i],face[i+1]] for i in range(1,len(face)-1))
        else:
            # These fixtures have convex caps. A center fan avoids artificial
            # zero-area triangles between collinear points on a straight rim.
            vertices.append([sum(vertices[i-1][a] for i in face)/len(face) for a in range(3)])
            triangles.extend([len(vertices),a,b] for a,b in zip(face,face[1:]+face[:1]))
    return analyze_triangles(vertices,triangles)


class CurveMathTests(unittest.TestCase):
    def test_plane_uses_named_local_axes_and_world_origin(self):
        c=make_curve({"kind":"polyline","points":[[0,0],[2,3]],
                      "plane":{"origin":[10,20,30],"x_axis":[0,1,0],"normal":[1,0,0]}},{})
        self.assertEqual(c.knots()[0]["pos"],[10,20,30])
        self.assertEqual(c.knots()[1]["pos"],[10,22,33])
        with self.assertRaisesRegex(ValueError,"perpendicular"):
            make_curve({"kind":"polyline","points":[[0,0],[1,1]],"plane":{"normal":[1,0,0]}},{})

    def test_circle_is_closed_with_tangent_continuity_and_approximately_correct_length(self):
        c=make_curve({"kind":"circle","radius":10},{})
        q=curve_qa(c,.001)
        self.assertEqual(len(c.knots()),4)
        self.assertTrue(q["closed"])
        self.assertEqual(q["tangent_breaks"],[])
        self.assertEqual(q["sampled_intersections"],0)
        self.assertAlmostEqual(q["length"],20*math.pi,delta=.02)
        points=c.samples(32,.001)
        self.assertNotEqual(points[0],points[-1])
        self.assertTrue(all(abs(length(p)-10)<.004 for p in points))

    def test_radius_and_tangent_arcs_join_without_manual_handle_coordinates(self):
        c=make_curve({"kind":"path","start":[0,0],"segments":[
            {"kind":"line","to":[5,0]},
            {"kind":"tangent_arc","to":[10,5]},
            {"kind":"line","to":[10,10]}]}, {})
        self.assertLess(length(sub(c.knots()[-1]["pos"],[10,10,0])),1e-8)
        self.assertEqual(curve_qa(c)["tangent_breaks"],[])
        a=make_curve({"kind":"path","start":[0,0],"segments":[{"kind":"arc","to":[10,0],"radius":5}]},{})
        self.assertAlmostEqual(curve_qa(a,.001)["length"],5*math.pi,delta=.01)
        with self.assertRaisesRegex(ValueError,"too small"):
            make_curve({"kind":"path","start":[0,0],"segments":[{"kind":"arc","to":[10,0],"radius":2}]},{})

    def test_bezier_tangents_are_directions_of_travel(self):
        c=make_curve({"kind":"path","start":[0,0],"segments":[
            {"kind":"bezier","to":[10,10],"start_tangent":[1,0],"end_tangent":[0,1],"start_length":3,"end_length":4}]},{})
        s=c.segments[0]
        self.assertEqual(s.out,[3,0,0])
        self.assertEqual(s.incoming,[10,6,0])

    def test_rounded_profiles_and_offsets_keep_the_requested_dimensions(self):
        c=make_curve({"kind":"rounded_rectangle","width":10,"depth":6,"radius":1},{})
        q=curve_qa(c)
        self.assertEqual(q["bounds"],[[-5,-3,0],[5,3,0]])
        self.assertEqual(q["tangent_breaks"],[])
        for points in ([[0,0],[10,0],[10,6],[0,6]],[[0,6],[10,6],[10,0],[0,0]]):
            c=make_curve({"kind":"polyline","points":points,"closed":True,"offset":1},{})
            self.assertEqual(curve_qa(c)["bounds"],[[-1,-1,0],[11,7,0]])
        with self.assertRaisesRegex(ValueError,"collapsed"):
            make_curve({"kind":"polyline","points":[[0,0],[10,0],[10,6],[0,6]],"closed":True,"offset":-4},{})

    def test_oversized_fillets_are_rejected_instead_of_silently_reduced(self):
        with self.assertRaisesRegex(ValueError,"overlap"):
            make_curve({"kind":"polyline","points":[[0,0],[4,0],[4,4],[0,4]],"closed":True,"fillet":3},{})

    def test_qa_catches_crossings_and_intentional_corners(self):
        c=make_curve({"kind":"polyline","points":[[0,0],[4,4],[0,4],[4,0]],"closed":True},{})
        q=curve_qa(c)
        self.assertGreater(q["sampled_intersections"],0)
        self.assertEqual(len(q["tangent_breaks"]),4)
        c=make_curve({"kind":"polyline","points":[[0,0,0],[1,0,0],[1,1,0],[1,1,1]]},{})
        self.assertFalse(curve_qa(c)["planar"])
        self.assertIn("not checked",curve_qa(c)["intersection_check"])

    def test_unknown_fields_nonfinite_values_and_expression_execution_are_rejected(self):
        for spec in ({"kind":"circle","radius":1,"fillet":2},
                     {"kind":"circle","radius":float("nan")},
                     {"kind":"circle","radius":"__import__('os')"},
                     {"kind":"spline","points":[[0,0],[1,1]],"tension":1},
                     {"kind":"polyline","points":[[0,0],[1,1]],"closed":"false"}):
            with self.subTest(spec=spec),self.assertRaises(ValueError): make_curve(spec,{})


class SweepLoftTests(unittest.TestCase):
    def test_sweep_is_one_closed_consistently_wound_surface(self):
        built=build_model(RECIPE,PARAMS)
        self.assertEqual(len(built["vertices"]),24*32)
        q=topology_report(built)
        for issue in ("boundary_edges","non_manifold_edges","winding_conflicts","degenerate_faces"):
            self.assertEqual(q["issues"][issue]["count"],0,issue)
        # Every station lies in its normal plane even with changing taper/twist.
        for i in range(24):
            pts=built["vertices"][i*32:(i+1)*32]
            normal=unit(cross(sub(pts[8],pts[0]),sub(pts[16],pts[0])))
            self.assertTrue(all(abs(dot(sub(p,pts[0]),normal))<1e-7 for p in pts))

    def test_parameter_changes_preserve_cage_connectivity(self):
        a=build_model(RECIPE,PARAMS)
        b=build_model(RECIPE,{**PARAMS,"bow":6,"width":5})
        self.assertEqual(a["faces"],b["faces"])
        self.assertNotEqual(a["vertices"],b["vertices"])
        self.assertEqual(model._topology(a),model._topology(b))

    def test_parallel_transport_preserves_frame_and_rejects_reversal(self):
        a,b=[1,0,0],unit([1,1,1]); x=[0,1,0]
        moved=transport(x,a,b)
        self.assertAlmostEqual(length(moved),1)
        self.assertAlmostEqual(dot(moved,b),0)
        self.assertLess(length(sub(transport(moved,b,a),x)),1e-10)
        with self.assertRaisesRegex(ValueError,"reverses"):
            transport(x,a,[-1,0,0])

    def test_closed_sweep_seam_has_no_boundary(self):
        recipe={"curves":{"p":{"kind":"circle","radius":10},"s":{"kind":"circle","radius":1}},
                "output":{"kind":"sweep","path":"p","profile":"s","caps":False,"path_samples":48,"profile_samples":16}}
        q=topology_report(build_model(recipe))
        self.assertEqual(q["issues"]["boundary_edges"]["count"],0)
        self.assertEqual(q["issues"]["winding_conflicts"]["count"],0)
        recipe["output"]["scale"]=[1,2]
        with self.assertRaisesRegex(ValueError,"equal end scales"): build_model(recipe)

    def test_invalid_profiles_and_degenerate_sweep_options_fail(self):
        for change in ({"up":[0,0,0]},{"scale":[1,0]},{"profile_samples":True},{"path_samples":10000}):
            recipe=copy.deepcopy(RECIPE); recipe["output"].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError): build_model(recipe,PARAMS)
        recipe=copy.deepcopy(RECIPE)
        recipe["curves"]["section"]={"kind":"polyline","closed":True,"points":[[0,0],[4,4],[0,4],[4,0]]}
        with self.assertRaisesRegex(ValueError,"self-intersecting"): build_model(recipe,PARAMS)

    def test_loft_resamples_unmatched_sources_and_locks_auto_seams(self):
        recipe={"curves":{
            "a":{"kind":"circle","radius":3},
            "b":{"kind":"rounded_rectangle","width":"width","depth":4,"radius":.4,
                 "plane":{"origin":[0,0,10]}}},
            "output":{"kind":"loft","sections":["a","b"],"align":"auto","profile_samples":32}}
        a=build_model(recipe,{"width":6})
        self.assertEqual(len(a["vertices"]),64)
        b=build_model(recipe,{"width":8},alignment=a["alignment"])
        self.assertEqual(a["alignment"],b["alignment"])
        self.assertEqual(a["faces"],b["faces"])
        self.assertEqual(topology_report(b)["issues"]["boundary_edges"]["count"],0)

    def test_reversed_loft_sections_fail_without_mutation(self):
        recipe={"curves":{"a":{"kind":"circle","radius":2},
                          "b":{"kind":"circle","radius":2,"plane":{"origin":[0,0,5],"normal":[0,0,-1]}}},
                "output":{"kind":"loft","sections":["a","b"]}}
        with self.assertRaisesRegex(ValueError,"reverse winding"): build_model(recipe)


class ConstructionContractTests(unittest.TestCase):
    def setUp(self):
        self.guard=patch.object(MaxClient,"send_command",side_effect=AssertionError("Tests must never contact Max"))
        self.guard.start(); self.addCleanup(self.guard.stop)

    def test_preview_never_uses_transport(self):
        result=model.curve_model(definition=RECIPE,parameters=PARAMS)
        self.assertEqual(result["action"],"preview")
        self.assertEqual(result["counts"]["vertices"],768)

    def test_reads_are_compact_and_manual_edits_are_reported(self):
        identity,data,raw=fixture()
        with patch.object(model,"_read",return_value=(identity,data,raw)):
            result=model.curve_model(action="read",handle=42)
        self.assertNotIn("definition",result)
        self.assertEqual(result["parameters"],PARAMS)
        self.assertTrue(result["geometry_matches_recipe"])
        changed={**identity,"fingerprint":B}
        self.assertFalse(model._public(changed,data,raw,False)["geometry_matches_recipe"])

    def test_update_rejects_stale_manual_instanced_and_unknown_parameters_without_writes(self):
        for change,parameters,stale in (({}, {"width":5}, True),
                                       ({"fingerprint":B},{"width":5},False),
                                       ({"instance_count":2},{"width":5},False),
                                       ({},{"typo":5},False)):
            identity,data,raw=fixture(); identity.update(change)
            token="0"*64 if stale else model._model_token(identity,raw)
            with self.subTest(change=change,parameters=parameters),patch.object(model,"_read",return_value=(identity,data,raw)),patch.object(model,"run") as write:
                with self.assertRaises((ValueError,RuntimeError)):
                    model.curve_model(action="update",handle=42,parameters=parameters,expected_model=token)
                write.assert_not_called()

    def test_update_guards_and_recipe_share_the_geometry_transaction(self):
        before,data,raw=fixture(); after,new_data,new_raw=fixture({**PARAMS,"width":5},B)
        token=model._model_token(before,raw)
        with patch.object(model,"_read",side_effect=[(before,data,raw),(after,new_data,new_raw)]),patch.object(model,"run",return_value=B) as write:
            result=model.curve_model(action="update",handle=42,parameters={"width":5},expected_model=token)
        self.assertEqual(result["parameters"]["width"],5)
        script=write.call_args.args[0]
        self.assertLess(script.index('throw "STALE_MODEL'),script.index("theHold.Begin()"))
        self.assertLess(script.index("setAppData obj"),script.index("theHold.Accept"))
        self.assertIn("theHold.Cancel()",script)
        self.assertIn("recipe rollback failed",script)

    def test_noop_and_undo_aliases_do_not_guess_parameter_state(self):
        identity,data,raw=fixture()
        with patch.object(model,"_read",return_value=(identity,data,raw)),patch.object(model,"run") as write:
            result=model.curve_model(action="update",handle=42,parameters={"width":4},expected_model=model._model_token(identity,raw))
        self.assertTrue(result["unchanged"]); write.assert_not_called()
        data["history"].append({"fingerprint":A,"parameters":{**PARAMS,"width":5}})
        self.assertIsNone(model._active(data,A))

    def test_mutated_recipe_changes_model_token_even_if_geometry_is_identical(self):
        identity,data,raw=fixture()
        token=model._model_token(identity,raw)
        data["definition"]["output"]["twist"]=25
        self.assertNotEqual(token,model._model_token(identity,model.encode(data)))

    def test_topology_change_is_rejected_before_write(self):
        identity,data,raw=fixture()
        data["topology"]="0"*64; raw=model.encode(data)
        with patch.object(model,"_read",return_value=(identity,data,raw)),patch.object(model,"run") as write:
            with self.assertRaisesRegex(ValueError,"alters topology"):
                model.curve_model(action="update",handle=42,parameters={"width":5},expected_model=model._model_token(identity,raw))
        write.assert_not_called()

    def test_edit_batch_validation_precedes_transport(self):
        for edits in ([{"op":"set","knot":1,"pos":[0,0,0]},{"op":"set","knot":1,"pos":[1,1,1]}],
                      [{"op":"insert","segment":1,"param":0}],
                      [{"op":"insert","segment":1},{"op":"set","knot":2,"pos":[1,1,1]}],
                      [{"op":"set","knot":1,"in_vec":[0,0,0],"type":"smooth"}],
                      [{"op":"set","knot":1,"pos":[float("nan"),0,0]}]):
            with self.subTest(edits=edits),self.assertRaises(ValueError): curve_edit.edit_curve(A,edits,name="Curve")

    def test_edit_preflights_all_ids_and_saves_handles_before_moving(self):
        data={"name":"Curve","handle":10,"curve_token":B,"splines":[]}
        with patch.object(curve_edit,"run",return_value="10|"+B) as write,patch.object(curve_edit,"read_curve",return_value=data):
            curve_edit.edit_curve(A,[{"op":"set","knot":1,"pos":[1,2,3]},{"op":"set","knot":2,"out_vec":[3,4,5]}],name="Curve")
        script=write.call_args.args[0]
        self.assertLess(script.index('if 2 > numKnots'),script.index('theHold.Begin()'))
        self.assertLess(script.index('local oldIn'),script.index('setKnotPoint'))
        self.assertIn('theHold.Cancel()',script)
        self.assertIn('includeTransform:true',script)
        self.assertIn('in coordsys local',script)
        self.assertIn('* inverseTM',script)
        self.assertIn('updateShape shape',script)
        self.assertNotIn('update obj',script)

    def test_spline_api_dispatches_on_node_with_local_cage_coordinates(self):
        curve=make_curve({"kind":"circle","radius":5},{})
        for create in (True,False):
            script=model._curve_write(curve,[20,30,40],create)
            self.assertIn('local shape = obj',script)
            self.assertNotIn('obj.baseobject',script)
            self.assertIn('in coordsys local',script)
            self.assertIn('updateShape shape',script)
            self.assertNotIn('update obj',script)
        functions=curve_runtime.CURVE_FUNCTIONS
        self.assertIn('numSplines obj >',functions)
        self.assertNotIn('numSplines obj.baseobject',functions)
        self.assertIn('in coordsys local',functions)
        reply='NAME|Q3VydmU=\nMETA|10|'+A+'|'+B+'|1\nSPL|1|false\nKNOT|1|1|#bezier|#curve|[1,2,3]|[0,2,3]|[2,2,3]'
        with patch.object(curve_runtime,'run',return_value=reply) as read:
            result=curve_runtime.read_curve('Curve')
        self.assertEqual(result['splines'][0]['knots'][0]['pos'],[1,2,3])
        self.assertIn('in coordsys local',read.call_args.args[0])
        self.assertIn('(getKnotPoint shape s k)*tm',read.call_args.args[0])


if __name__=="__main__": unittest.main()
