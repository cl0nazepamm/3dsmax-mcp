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
    def test_cropped_capture_refuses_old_bridge_without_returning_fullscreen(self):
        fake_client=FakeClient({"result":json.dumps({"file":"unexpected-desktop.jpg"})})
        with patch.object(viewport,"client",fake_client), self.assertRaisesRegex(RuntimeError,"updated native bridge"):
            viewport.capture_screen(enabled=True,target="vray_vfb",crop=[10,20,300,200])
        self.assertEqual(len(fake_client.calls),1)

    def test_screen_crop_preserves_native_metadata(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg",delete=False) as tmp:
            tmp.write(b"pixels"); path=tmp.name
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        native={"file":path,"width":300,"height":200,"capture_contract":"desktop_crop_v1",
                "target":"vray_vfb","screen_rect":[-1200,100,300,200],"visible_pixels_only":True}
        fake_client=FakeClient({"result":json.dumps(native)})
        with patch.object(viewport,"client",fake_client):
            result=viewport.capture_screen(enabled=True,target="vray_vfb",crop=[10,20,300,200])
        self.assertEqual(result["screen_rect"],[-1200,100,300,200])
        self.assertTrue(result["visible_pixels_only"])
        payload=json.loads(fake_client.calls[0][0][0])
        self.assertEqual(payload["crop"],[10,20,300,200])
        self.assertEqual(payload["target"],"vray_vfb")

    def test_bad_crop_and_disabled_capture_never_dispatch(self):
        fake_client=FakeClient({})
        with patch.object(viewport,"client",fake_client):
            for crop in ([],[1,2,3],[0,0,0,10],[-1,0,10,10],[True,0,10,10],[0,0,1.5,10],[0,0,10,999999]):
                with self.subTest(crop=crop), self.assertRaises(ValueError):
                    viewport.capture_screen(enabled=True,crop=crop)
            with self.assertRaises(ValueError): viewport.capture_screen(target="vray_vfb")
        self.assertEqual(fake_client.calls,[])

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
