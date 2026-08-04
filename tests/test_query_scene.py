import json
import unittest
from unittest.mock import MagicMock, patch

from maxmcp.tools._query_scene_core import (
    _previous_snapshot,
    dispatch_query_scene,
    run_delta,
    run_filter,
    run_overview,
)
from maxmcp.tools.query_scene import query_scene


class QuerySceneTests(unittest.TestCase):
    def test_dispatch_rejects_unknown_action(self) -> None:
        client = MagicMock()
        payload = json.loads(dispatch_query_scene(client, "nope"))
        self.assertIn("error", payload)

    def test_overview_delegates_to_run_overview(self) -> None:
        with patch("maxmcp.tools.query_scene.dispatch_query_scene", return_value='{"objectCount":1}') as mocked:
            result = query_scene(action="overview", max_roots=10)
        self.assertEqual(result, '{"objectCount":1}')
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.args[1], "overview")

    def test_run_overview_wrapper(self) -> None:
        with patch("maxmcp.tools._query_scene_core.fetch_scene_snapshot", return_value={"objectCount": 2}):
            client = MagicMock()
            client.native_available = True
            client.send_command.return_value = {"result": '{"objectCount":2}'}
            result = run_overview(client, max_roots=25)
        self.assertIn("objectCount", result)

    def test_run_filter_unfiltered_summary(self) -> None:
        snapshot = {
            "objectCount": 4,
            "classCounts": {"Sphere": 4},
            "layers": ["0"],
            "hiddenCount": 0,
            "frozenCount": 0,
        }
        mock_client = MagicMock()
        mock_client.native_available = True
        with patch("maxmcp.tools._query_scene_core.fetch_scene_snapshot", return_value=snapshot):
            payload = json.loads(run_filter(mock_client))
        self.assertEqual(payload["totalObjects"], 4)

    def test_run_delta_forwards_unchanged_since_to_native(self) -> None:
        client = MagicMock()
        client.native_available = True
        client.send_command.return_value = {
            "result": '{"journal":true,"unchanged":true,"currentSeq":42}',
        }

        result = json.loads(run_delta(client, capture=True, unchanged_since=41))

        payload = json.loads(client.send_command.call_args.args[0])
        self.assertEqual(payload, {"capture": True, "unchanged_since": 41})
        self.assertEqual(client.send_command.call_args.kwargs["cmd_type"], "native:scene_delta")
        self.assertTrue(result["unchanged"])


if __name__ == "__main__":
    unittest.main()
