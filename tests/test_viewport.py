import json
import os
import tempfile
import unittest
from unittest.mock import patch

from maxmcp.tools import viewport


class FakeClient:
    native_available = True

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[tuple[tuple, dict]] = []

    def send_command(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class ViewportCaptureTests(unittest.TestCase):
    def test_capture_viewport_returns_file_metadata_by_default(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"png bytes")
            tmp_path = tmp.name
        self.addCleanup(lambda: os.path.exists(tmp_path) and os.remove(tmp_path))

        fake_client = FakeClient(
            {
                "result": json.dumps({
                    "file": tmp_path,
                    "width": 1600,
                    "height": 900,
                })
            }
        )
        with patch.object(viewport, "client", fake_client):
            result = viewport.capture_viewport()

        self.assertEqual(result["type"], "image_file")
        self.assertEqual(result["file"], tmp_path)
        self.assertEqual(result["mime_type"], "image/png")
        self.assertEqual(result["size_bytes"], len(b"png bytes"))
        self.assertNotIn("data", result)
        self.assertEqual(
            fake_client.calls,
            [
                (
                    (json.dumps({"max_width": viewport.DEFAULT_MAX_WIDTH, "max_height": 0}),),
                    {"cmd_type": "native:capture_viewport"},
                )
            ],
        )

    def test_capture_viewport_return_image_is_ignored_and_hints_at_file(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(b"png bytes")
            tmp_path = tmp.name
        self.addCleanup(lambda: os.path.exists(tmp_path) and os.remove(tmp_path))

        fake_client = FakeClient(
            {
                "result": json.dumps({
                    "file": tmp_path,
                    "width": 1600,
                    "height": 900,
                })
            }
        )
        with patch.object(viewport, "client", fake_client):
            result = viewport.capture_viewport(return_image=True)

        self.assertEqual(result["type"], "image_file")
        self.assertEqual(result["file"], tmp_path)
        self.assertNotIn("data", result)
        self.assertIn("deprecated", result["hint"]["message"])


if __name__ == "__main__":
    unittest.main()
