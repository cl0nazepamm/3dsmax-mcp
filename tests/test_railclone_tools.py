import json
import unittest
from unittest.mock import patch

from maxmcp.tools.railclone import get_railclone_style_graph


class RailCloneToolTests(unittest.TestCase):
    def test_get_railclone_style_graph_parses_graph_payload(self) -> None:
        payload = "\n".join(
            [
                "HDR|RailClone001|RailClone_Pro|StyleA|6|0",
                "META|baseCount|1",
                "META|segmentCount|1",
                "META|parameterCount|1",
                "BA|1|base-1|0|Spline|Rectangle001|true|0.0|100.0|Path base",
                "SG|1|seg-1|Segment A|Box001|1|1|true|true|false|false|1|[0,0,0]|[0,0,0]|[100,100,100]|1",
                "PA|1|param-1|Size|1|float|true|10.5|0.0|20.0||Scale amount|false|0",
                "WARN|STYLE_DESC_EMPTY|0|0",
            ]
        )
        with patch("maxmcp.tools.railclone.client.send_command", return_value={"result": payload}):
            result = json.loads(get_railclone_style_graph("RailClone001"))

        self.assertEqual(result["name"], "RailClone001")
        self.assertEqual(result["baseCount"], 1)
        self.assertEqual(result["segmentCount"], 1)
        self.assertEqual(result["parameterCount"], 1)
        self.assertEqual(result["bases"][0]["node"], "Rectangle001")
        self.assertEqual(result["segments"][0]["sliceSourceIndex"], 1)
        self.assertEqual(result["parameters"][0]["typeLabel"], "float")
        self.assertTrue(any(edge["type"] == "base_to_segment" for edge in result["graph"]["edges"]))

    def test_get_railclone_style_graph_returns_error_when_missing(self) -> None:
        with patch("maxmcp.tools.railclone.client.send_command", return_value={"result": "__ERROR__|Object not found: MissingRC"}):
            result = json.loads(get_railclone_style_graph("MissingRC"))
        self.assertEqual(result["error"], "Object not found: MissingRC")


if __name__ == "__main__":
    unittest.main()

