import json
import unittest
from pathlib import Path
from unittest.mock import patch

from maxmcp.tools import render_automations as render


class RenderPreviewTests(unittest.TestCase):
    def test_native_diagnostics_do_not_route_python_status_to_cancellation(self):
        from scripts import gen_tool_registry
        source=Path(render.__file__)
        self.assertNotIn("render_automations",{tool["name"] for tool in gen_tool_registry.extract_tools(source)})

    def test_cancel_capture_uses_one_dedicated_route_with_job_and_crop(self):
        response={"capture":{"file":"partial.jpg"},"stopped":None,"cancellation":{"status":"cancelling"}}
        with patch.object(render.client,"send_command",return_value={"result":json.dumps(response)}) as send:
            actual=render.render_automations(action="cancel_capture",job_id="our-job",crop=[0,20,200,100])
        self.assertEqual(actual,response)
        self.assertEqual(send.call_count,1)
        self.assertEqual(send.call_args.kwargs["cmd_type"],"native:render_cancel_capture")
        self.assertEqual(json.loads(send.call_args.args[0])["job_id"],"our-job")

    def test_bad_requests_never_abort_or_capture(self):
        with patch.object(render.client,"send_command") as send:
            for args in ({},{"job_id":"ours","crop":[True,0,20,20]},
                         {"job_id":"ours","crop":[0,0,0,20]},
                         {"job_id":"ours","capture_target":"unknown"}):
                with self.subTest(args=args),self.assertRaises(ValueError):
                    render.render_automations(action="cancel_capture",**args)
        send.assert_not_called()

    def test_old_bridge_never_falls_back_to_global_cancel(self):
        with patch.object(render.client,"send_command",side_effect=RuntimeError("Unknown command")) as send:
            with self.assertRaisesRegex(RuntimeError,"Unknown command"):
                render.render_automations(action="cancel_capture",job_id="ours")
        self.assertEqual(send.call_count,1)
