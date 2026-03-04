import json
import unittest
from unittest.mock import patch

from src.tools.controllers import inspect_controller, inspect_track_view


class ControllerToolTests(unittest.TestCase):
    def test_inspect_controller_returns_client_result(self) -> None:
        payload = '{"controller":"Position_XYZ","object":"Box001","param_path":"[#transform][#position]"}'
        with patch("src.tools.controllers.client.send_command", return_value={"result": payload}) as mocked_send:
            result = inspect_controller("Box001", "[#transform][#position]")

        self.assertEqual(json.loads(result)["controller"], "Position_XYZ")
        mocked_send.assert_called_once()

    def test_inspect_track_view_returns_client_result(self) -> None:
        payload = '{"object":"Box001","rootTrackCount":1,"tracks":[{"name":"transform","path":"[#transform]","controller":"PRS","childCount":1,"children":[{"name":"position","path":"[#transform][#position]","controller":"Position_XYZ","childCount":0,"children":[]}]}]}'
        with patch("src.tools.controllers.client.send_command", return_value={"result": payload}) as mocked_send:
            result = inspect_track_view("Box001", depth=3, filter="position", include_values=False)

        parsed = json.loads(result)
        self.assertEqual(parsed["object"], "Box001")
        self.assertEqual(parsed["tracks"][0]["controller"], "PRS")
        self.assertEqual(parsed["tracks"][0]["children"][0]["name"], "position")
        mocked_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
