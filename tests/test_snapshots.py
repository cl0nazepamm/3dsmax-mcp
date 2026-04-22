import json
import unittest
from unittest.mock import patch

from src.tools import snapshots


def _force_fallback():
    """Make client.send_command raise RuntimeError so get_scene_delta's
    native path takes its `except RuntimeError: pass` branch and falls
    through to the Python diff logic these tests exercise."""
    return patch.object(
        snapshots.client,
        "send_command",
        side_effect=RuntimeError("test: force Python fallback"),
    )


class SceneDeltaTests(unittest.TestCase):
    def test_first_call_captures_baseline(self) -> None:
        state = {
            "Box001": {"c": "Box", "p": [0.0, 0.0, 0.0], "m": "", "n": 0, "h": False},
        }
        with _force_fallback(), \
             patch.object(snapshots, "_capture_scene_state", return_value=state):
            snapshots._previous_snapshot = None
            result = json.loads(snapshots.get_scene_delta())

        self.assertEqual(result, {"baseline": True, "objectCount": 1})

    def test_capture_true_resets_baseline(self) -> None:
        state = {
            "Sphere001": {"c": "Sphere", "p": [1.0, 2.0, 3.0], "m": "", "n": 0, "h": False},
        }
        with _force_fallback(), \
             patch.object(snapshots, "_capture_scene_state", return_value=state):
            snapshots._previous_snapshot = {"Old": {"c": "Box", "p": [0, 0, 0], "m": "", "n": 0, "h": False}}
            result = json.loads(snapshots.get_scene_delta(capture=True))

        self.assertEqual(result, {"baseline": True, "objectCount": 1})
        self.assertEqual(snapshots._previous_snapshot, state)

    def test_diff_reports_added_removed_and_modified_objects(self) -> None:
        previous = {
            "Box001": {"c": "Box", "p": [0.0, 0.0, 0.0], "m": "", "n": 0, "h": False},
            "Box002": {"c": "Box", "p": [5.0, 0.0, 0.0], "m": "MatA", "n": 1, "h": False},
        }
        current = {
            "Box002": {"c": "Box", "p": [6.2, 0.0, 0.0], "m": "MatB", "n": 2, "h": True},
            "Sphere001": {"c": "Sphere", "p": [1.0, 1.0, 1.0], "m": "", "n": 0, "h": False},
        }
        with _force_fallback(), \
             patch.object(snapshots, "_capture_scene_state", return_value=current):
            snapshots._previous_snapshot = previous
            result = json.loads(snapshots.get_scene_delta())

        self.assertEqual(result["added"], [{"name": "Sphere001", "class": "Sphere"}])
        self.assertEqual(result["removed"], [{"name": "Box001", "class": "Box"}])
        self.assertEqual(result["counts"], {"added": 1, "removed": 1, "modified": 1, "total": 2})
        self.assertEqual(
            result["modified"],
            [{
                "name": "Box002",
                "position": {"from": [5.0, 0.0, 0.0], "to": [6.2, 0.0, 0.0]},
                "material": {"from": "MatA", "to": "MatB"},
                "modifierCount": {"from": 1, "to": 2},
                "hidden": {"from": False, "to": True},
            }],
        )

    def test_diff_ignores_small_position_noise(self) -> None:
        previous = {
            "Box001": {"c": "Box", "p": [1.04, 2.0, 3.0], "m": "", "n": 0, "h": False},
        }
        current = {
            "Box001": {"c": "Box", "p": [1.03, 2.0, 3.0], "m": "", "n": 0, "h": False},
        }
        with _force_fallback(), \
             patch.object(snapshots, "_capture_scene_state", return_value=current):
            snapshots._previous_snapshot = previous
            result = json.loads(snapshots.get_scene_delta())

        self.assertEqual(result["modified"], [])
        self.assertEqual(result["counts"]["modified"], 0)


if __name__ == "__main__":
    unittest.main()
