import json
import unittest
from unittest.mock import patch
from maxmcp.tools import viewport, mesh_ops


class Bridge:
    native_available=True
    def __init__(self,*replies): self.replies=list(replies); self.calls=[]
    def send_command(self,command,**kwargs):
        self.calls.append((command,kwargs))
        result=self.replies.pop(0)
        if isinstance(result,Exception): raise result
        return {"result":result if isinstance(result,str) else json.dumps(result)}


class AgentViewportTests(unittest.TestCase):
    def test_pending_vfb_never_returns_previous_render_or_stops_launch(self):
        bridge=Bridge({"render":{"mode":"vray_vfb","session_state":"starting"}})
        with patch.object(viewport,"client",bridge), patch.object(viewport,"capture_screen") as capture:
            result=viewport.agent_viewport(action="stop_capture")
        capture.assert_not_called()
        self.assertEqual(len(bridge.calls),1)
        self.assertIsNone(result["capture"])
        self.assertFalse(result["stopped"])

    def test_vfb_capture_precedes_stop_and_retains_partial_status(self):
        bridge=Bridge({"render":{"mode":"vray_vfb","capture_target":"vray_vfb","session_state":"running"}},
                      {"render":{"mode":"shaded"}})
        def capture(**kwargs):
            self.assertEqual(len(bridge.calls),1)
            self.assertEqual(kwargs,{"enabled":True,"target":"vray_vfb","crop":[2,3,100,80]})
            return {"file":"partial.jpg"}
        with patch.object(viewport,"client",bridge), patch.object(viewport,"capture_screen",side_effect=capture):
            result=viewport.agent_viewport(action="stop_capture",crop=[2,3,100,80])
        self.assertTrue(result["stopped"])
        self.assertTrue(result["captured_before_stop"])
        self.assertIsNone(result["converged"])
        self.assertEqual(json.loads(bridge.calls[1][0])["mode"],"shaded")

    def test_failed_capture_preserves_running_preview(self):
        bridge=Bridge({"render":{"mode":"vray_vfb","capture_target":"vray_vfb"}})
        with patch.object(viewport,"client",bridge), patch.object(viewport,"capture_screen",side_effect=RuntimeError("covered")):
            with self.assertRaisesRegex(RuntimeError,"covered"):
                viewport.agent_viewport(action="stop_capture")
        self.assertEqual(len(bridge.calls),1)

    def test_render_modes_route_to_owned_panel(self):
        bridge=Bridge(*[{"render":{"mode":mode}} for mode in ("vray_ipr","activeshade","shaded")])
        with patch.object(viewport,"client",bridge):
            for mode in ("vray_ipr","activeshade","shaded"):
                result=viewport.agent_viewport(action="render",mode=mode)
                self.assertEqual(result["render"]["mode"],mode)
        for command, kwargs in bridge.calls:
            self.assertEqual(kwargs["cmd_type"],"native:agent_viewport")
            self.assertEqual(json.loads(command)["source"],"agent")

    def test_invalid_render_requests_do_not_reach_max(self):
        bridge=Bridge()
        with patch.object(viewport,"client",bridge):
            for args in ({"action":"render"},{"action":"render","mode":"production"},
                         {"action":"status","mode":"vray_ipr"},{"renderer_source":"unknown"},
                         {"action":"render","mode":"vray_ipr","renderer_source":"production"}):
                with self.subTest(args=args), self.assertRaises(ValueError): viewport.agent_viewport(**args)
        self.assertEqual(bridge.calls,[])

    def test_unavailable_explicit_agent_capture_never_uses_active_view(self):
        bridge=Bridge(RuntimeError("Unknown command type"))
        with patch.object(viewport,"client",bridge), self.assertRaisesRegex(RuntimeError,"Unknown command"):
            viewport.capture_viewport(source="agent")
        self.assertEqual(len(bridge.calls),1)
        self.assertEqual(bridge.calls[0][1]["cmd_type"],"native:agent_viewport")

    def test_closed_owned_panel_does_not_redirect_navigation(self):
        bridge=Bridge(RuntimeError("Agent viewport is unavailable"))
        with patch.object(viewport,"client",bridge), self.assertRaisesRegex(RuntimeError,"unavailable"):
            viewport.set_viewport()
        self.assertEqual(len(bridge.calls),1)

    def test_auto_uses_legacy_only_when_no_panel_is_owned(self):
        bridge=Bridge({"handled":False},"OK|view_front|55")
        with patch.object(viewport,"client",bridge): result=viewport.set_viewport(view="front")
        self.assertEqual(result["actual_view"],"view_front")
        self.assertEqual(len(bridge.calls),2)

    def test_old_bridge_compatibility_is_limited_to_missing_route(self):
        bridge=Bridge(RuntimeError("Unknown command type"),"OK|view_front|55")
        with patch.object(viewport,"client",bridge): viewport.set_viewport(view="front")
        self.assertEqual(len(bridge.calls),2)

    def test_bad_navigation_is_rejected_before_dispatch(self):
        bridge=Bridge()
        with patch.object(viewport,"client",bridge):
            for args in ({"action":"pick","x":1.1},{"factor":0},{"pitch":float("nan")},{"width":True},{"action":"isolate"}):
                with self.subTest(args=args), self.assertRaises(ValueError): viewport.agent_viewport(**args)
        self.assertEqual(bridge.calls,[])

    def test_parked_open_and_restore_use_only_owned_native_route(self):
        bridge=Bridge({"owned":True,"capture_ready":False,"window_state":"minimized"},
            {"owned":True,"capture_ready":True,"window_state":"visible"})
        with patch.object(viewport,"client",bridge):
            parked=viewport.agent_viewport(action="open",start_minimized=True)
            ready=viewport.agent_viewport(action="restore")
        self.assertFalse(parked["capture_ready"])
        self.assertTrue(ready["capture_ready"])
        self.assertTrue(json.loads(bridge.calls[0][0])["start_minimized"])
        self.assertEqual(json.loads(bridge.calls[1][0])["action"],"restore")
        self.assertTrue(all(call[1]["cmd_type"]=="native:agent_viewport" for call in bridge.calls))

    def test_parked_agent_blocks_mesh_inspection_before_capture(self):
        bridge=Bridge({"owner":"agent","owned":True,"available":True,
            "capture_ready":False,"window_state":"minimized","next_action":"restore"})
        with patch.object(mesh_ops,"client",bridge), self.assertRaisesRegex(RuntimeError,"minimized.*restore"):
            mesh_ops.inspect_mesh(name="Chair",capture=True)
        self.assertEqual(len(bridge.calls),1)

    def test_projection_keeps_view_token_and_world_points(self):
        points=[[1,2,3],[-5.5,0,1]]
        reply={"width":1000,"height":740,"view_token":"VIEW",
            "projections":[{"pixel":[12,34],"depth":10,"in_front":True,"in_frame":True},
                           {"pixel":None,"depth":-4,"in_front":False,"in_frame":False}]}
        bridge=Bridge(reply)
        with patch.object(viewport,"client",bridge):
            result=viewport.agent_viewport(action="project",points=points,expected_view="VIEW")
        payload=json.loads(bridge.calls[0][0])
        self.assertEqual(payload["points"],points)
        self.assertEqual(payload["expected_view"],"VIEW")
        self.assertEqual(result,reply)

    def test_invalid_projection_and_lifecycle_arguments_never_dispatch(self):
        bridge=Bridge()
        invalid=({"action":"project"},{"action":"project","points":[]},
            {"action":"project","points":[[0,0,0]]*2001},
            {"action":"project","points":[[0,1]]},
            {"action":"project","points":[[0,0,float("nan")]]},
            {"action":"project","points":[[True,0,0]]},
            {"action":"project","points":[[0,0,1e13]]},
            {"action":"status","points":[[0,0,0]]},
            {"action":"restore","start_minimized":True},
            {"action":"open","start_minimized":1})
        with patch.object(viewport,"client",bridge):
            for args in invalid:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    viewport.agent_viewport(**args)
        self.assertEqual(bridge.calls,[])

    def test_mesh_labels_use_agent_capture_without_user_overlay(self):
        bridge=Bridge({"owner":"agent","view_token":"VIEW"},
            "META|42|8,12,6|0|MESH\nMATCH|1\nC|2|[1,2,3]|1,2,3,4|[0,0,1]",
            {"file":"agent.png","agent_viewport":{"view_token":"VIEW"}})
        with patch.object(mesh_ops,"client",bridge): result=mesh_ops.inspect_mesh(name="Chair",capture=True)
        self.assertEqual(result["capture"]["file"],"agent.png")
        self.assertNotIn("registerRedrawViewsCallback",bridge.calls[1][0])
        capture=json.loads(bridge.calls[2][0])
        self.assertEqual(capture["expected_view"],"VIEW")
        self.assertEqual(capture["labels"],[{"point":[1.0,2.0,3.0],"text":"F2"}])

    def test_closed_agent_blocks_mesh_capture_before_overlay_registration(self):
        bridge=Bridge({"owned":True,"available":False})
        with patch.object(mesh_ops,"client",bridge), self.assertRaisesRegex(RuntimeError,"closed"):
            mesh_ops.inspect_mesh(name="Chair",capture=True)
        self.assertEqual(len(bridge.calls),1)
