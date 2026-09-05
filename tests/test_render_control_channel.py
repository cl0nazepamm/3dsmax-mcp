import json
import unittest
from unittest.mock import patch

from maxmcp.max_client import MaxClient


class RenderControlChannelTests(unittest.TestCase):
    def test_control_bypasses_locked_render_connection_and_stays_on_same_max(self):
        client=MaxClient(transport="auto")
        client._selected_pipe_name="rendering-max"
        client._pipe_lock.acquire()
        seen=[]
        def send(control, request, timeout):
            self.assertIsNot(control,client)
            self.assertFalse(control._pipe_lock.locked())
            self.assertEqual(control._resolve_pipe_name(),"rendering-max")
            self.assertEqual(control.transport,"pipe")
            seen.append(json.loads(request)["type"])
            return b'{"success":true,"result":"{}","meta":{"transport":"namedpipe"}}'
        try:
            with patch.object(MaxClient,"_send_via_pipe",send), patch.object(MaxClient,"_send_via_tcp",side_effect=AssertionError("no fallback")):
                client.send_command("{}",cmd_type="native:render_cancel")
                client.send_command("{}",cmd_type="native:capture_screen")
                client.send_command("{}",cmd_type="native:render_cancel_capture")
        finally:
            client._pipe_lock.release()
        self.assertEqual(seen,["native:render_cancel","native:capture_screen","native:render_cancel_capture"])

    def test_control_failure_never_retargets_or_falls_back(self):
        client=MaxClient(transport="auto")
        with patch.object(client,"_resolve_pipe_name",return_value="chosen-max"), \
             patch.object(MaxClient,"_send_via_pipe",side_effect=ConnectionError("gone")), \
             patch.object(MaxClient,"_send_via_tcp",side_effect=AssertionError("no fallback")):
            with self.assertRaisesRegex(ConnectionError,"gone"):
                client.send_command("{}",cmd_type="native:render_cancel")

    def test_idle_control_follows_current_claim_and_normal_writes_keep_normal_channel(self):
        client=MaxClient(transport="pipe")
        client._selected_pipe_name="old-max"
        seen=[]
        def send(bridge, request, timeout):
            seen.append((bridge is client,bridge._resolve_pipe_name()))
            return b'{"success":true,"result":"{}"}'
        with patch.object(client,"_resolve_pipe_name",return_value="new-max"), patch.object(MaxClient,"_send_via_pipe",send):
            client.send_command("{}",cmd_type="native:render_cancel")
            client.send_command("{}",cmd_type="native:create_object")
        self.assertEqual(seen,[(False,"new-max"),(True,"new-max")])
