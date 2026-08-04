import json
import unittest
from unittest.mock import patch

from maxmcp.tools._query_scene_core import scene_info_summary_from_snapshot, run_filter


class SceneReadsTests(unittest.TestCase):
    def test_scene_info_summary_mapping(self) -> None:
        summary = scene_info_summary_from_snapshot({
            "objectCount": 12,
            "classCounts": {"Box": 3},
            "layers": ["0"],
            "hiddenCount": 1,
            "frozenCount": 0,
            "materials": {"M1": 2},
        })
        self.assertEqual(summary["totalObjects"], 12)
        self.assertNotIn("materials", summary)

    def test_filter_unfiltered_uses_scene_snapshot(self) -> None:
        snapshot = {
            "objectCount": 4,
            "classCounts": {"Sphere": 4},
            "layers": ["0"],
            "hiddenCount": 0,
            "frozenCount": 0,
        }
        mock_client = unittest.mock.MagicMock()
        mock_client.native_available = True
        with patch("maxmcp.tools._query_scene_core.fetch_scene_snapshot", return_value=snapshot) as mocked:
            payload = json.loads(run_filter(mock_client))
        mocked.assert_called_once()
        self.assertEqual(payload["totalObjects"], 4)


if __name__ == "__main__":
    unittest.main()
