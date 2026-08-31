import json
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from maxmcp.tools.keyframes import keyframe_tracks


class KeyframeToolTests(unittest.TestCase):
    def test_set_all_tracks_uses_compact_native_payload(self) -> None:
        payload = '{"keyed":3,"samplesOmitted":true}'
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": payload}) as mocked_send,
        ):
            result = keyframe_tracks(names=["Box001"], tracks="all", time=42, key_type="linear")

        self.assertEqual(json.loads(result)["keyed"], 3)
        args, kwargs = mocked_send.call_args
        sent = json.loads(args[0])
        self.assertEqual(kwargs["cmd_type"], "native:keyframe_tracks")
        self.assertEqual(sent["names"], ["Box001"])
        self.assertEqual(sent["tracks"], "all")
        self.assertEqual(sent["time"], 42)
        self.assertEqual(sent["key_type"], "linear")
        self.assertFalse(sent["budget"]["include_samples"])
        self.assertEqual(sent["budget"]["max_keys"], 50000)

    def test_track_paths_do_not_expand_to_all_tracks(self) -> None:
        payload = '{"styledKeys":1,"samplesOmitted":true}'
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": payload}) as mocked_send,
        ):
            keyframe_tracks(
                action="style",
                names=["RigRoot"],
                track_paths=["[#transform][#position][#x_position]"],
                key_type="step",
            )

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["track_paths"], ["[#transform][#position][#x_position]"])
        self.assertNotIn("tracks", sent)
        self.assertEqual(sent["key_type"], "step")

    def test_out_of_range_and_budget_pass_through(self) -> None:
        payload = '{"outOfRangeEdits":6,"samplesOmitted":true}'
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": payload}) as mocked_send,
        ):
            result = keyframe_tracks(
                action="ort",
                names=["Bip001"],
                before="cycle",
                after="pingPong",
                budget={"max_keys": 2500, "max_results": 5},
            )

        self.assertEqual(json.loads(result)["outOfRangeEdits"], 6)
        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["before"], "cycle")
        self.assertEqual(sent["after"], "pingPong")
        self.assertEqual(sent["budget"]["max_keys"], 2500)
        self.assertEqual(sent["budget"]["max_results"], 5)

    def test_value_passes_through_for_animation_safe_keys(self) -> None:
        payload = '{"keyed":1,"samplesOmitted":true}'
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": payload}) as mocked_send,
        ):
            keyframe_tracks(
                names=["Sphere001"],
                tracks="position",
                time=30,
                value=[80, 0, 60],
            )

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["value"], [80, 0, 60])
        self.assertNotIn("move", sent)

    def test_move_passes_through_for_animation_safe_keys(self) -> None:
        payload = '{"keyed":1,"samplesOmitted":true}'
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": payload}) as mocked_send,
        ):
            keyframe_tracks(
                names=["Sphere001"],
                tracks="position",
                time=30,
                move=[0, 0, 30],
            )

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["move"], [0, 0, 30])
        self.assertNotIn("value", sent)

    def test_list_and_loop_pass_through(self) -> None:
        payload = '{"action":"list","readOnly":true,"tracks":2}'
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": payload}) as mocked_send,
        ):
            keyframe_tracks(
                action="list",
                names=["Plane001", "Plane002"],
                from_time=1,
                to_time=100,
                tracks="all",
            )

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["action"], "list")
        self.assertEqual(sent["from_time"], 1)
        self.assertEqual(sent["to_time"], 100)

        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": '{"matched":6}'}) as mocked_send,
        ):
            keyframe_tracks(
                action="loop",
                names=["Plane001", "Plane002"],
                from_time=1,
                to_time=100,
                tracks="all",
            )

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["action"], "loop")
        self.assertEqual(sent["order"], "flat")

    def test_match_can_request_hierarchy_order(self) -> None:
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": "{}"}) as mocked_send,
        ):
            keyframe_tracks(
                action="match",
                names=["Plane001", "Plane002"],
                from_time=1,
                to_time=100,
                order="hierarchy",
            )

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["order"], "hierarchy")

    def test_timeline_management_uses_same_compact_native_route(self) -> None:
        payload = '{"action":"timeline","frameRate":24,"currentFrame":12,"range":{"start":0,"end":96}}'
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": payload}) as mocked_send,
        ):
            result = keyframe_tracks(
                action="timeline",
                frame_rate=24,
                current_frame=12,
                range_start=0,
                range_end=96,
            )

        self.assertEqual(json.loads(result)["frameRate"], 24)
        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(mocked_send.call_args.kwargs["cmd_type"], "native:keyframe_tracks")
        self.assertEqual(sent["frame_rate"], 24)
        self.assertEqual(sent["current_frame"], 12)
        self.assertEqual(sent["range_start"], 0)
        self.assertEqual(sent["range_end"], 96)

    def test_bounded_key_time_edits_pass_native_contract_fields(self) -> None:
        cases = [
            ("delete_keys", {}, {}),
            ("move_keys", {"time_offset": 8}, {"time_offset": 8}),
            ("scale_keys", {"time_scale": 0.5, "pivot_time": 10}, {"time_scale": 0.5, "pivot_time": 10}),
        ]
        for action, kwargs, expected in cases:
            with self.subTest(action=action):
                with (
                    patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
                    patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": "{}"}) as mocked_send,
                ):
                    keyframe_tracks(
                        action=action,
                        names=["RigRoot"],
                        tracks="position",
                        from_time=10,
                        to_time=40,
                        **kwargs,
                    )

                sent = json.loads(mocked_send.call_args.args[0])
                self.assertEqual(sent["action"], action)
                self.assertEqual(sent["from_time"], 10)
                self.assertEqual(sent["to_time"], 40)
                for key, value in expected.items():
                    self.assertEqual(sent[key], value)

    def test_resample_replace_and_step_pass_through(self) -> None:
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": "{}"}) as mocked_send,
        ):
            keyframe_tracks(
                action="bake",
                names=["CameraPath"],
                tracks="position",
                from_time=0,
                to_time=100,
                sample_step=2,
                replace_keys=True,
                key_type="linear",
            )

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["sample_step"], 2)
        self.assertTrue(sent["replace_keys"])
        self.assertEqual(sent["key_type"], "linear")

    def test_tangent_normalization_carries_explicit_bounds(self) -> None:
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.keyframes.client.send_command", return_value={"result": "{}"}) as mocked_send,
        ):
            keyframe_tracks(
                action="normalize_tangents",
                names=["CameraPath"],
                tracks="position",
                from_time=0,
                to_time=100,
            )

        sent = json.loads(mocked_send.call_args.args[0])
        self.assertEqual(sent["action"], "normalize_tangents")
        self.assertEqual(sent["from_time"], 0)
        self.assertEqual(sent["to_time"], 100)

    def test_native_mechanical_animation_contract_uses_sdk_apis(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "src"
            / "handlers"
            / "keyframe_handlers.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("ManageTimeline", source)
        self.assertIn("GetAnimRange()", source)
        self.assertIn("SetAnimRange", source)
        self.assertIn("SetFrameRate", source)
        self.assertIn("DeleteKeyByIndex", source)
        self.assertIn("key->time = edit.second", source)
        self.assertIn("SortKeys()", source)
        self.assertIn("BuildResamplePlan", source)
        self.assertIn("normalize_tangents", source)
        self.assertIn("refusing an unbounded edit", source)
        self.assertNotIn("ExecuteMAXScriptScript", source)

    def test_requires_native_bridge(self) -> None:
        with patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=False):
            result = keyframe_tracks(names=["Box001"])

        self.assertIn("Native bridge is required", result)


if __name__ == "__main__":
    unittest.main()
