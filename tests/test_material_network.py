import json
import unittest
from unittest.mock import patch

from maxmcp.tools.material_network import inspect_material_network, replicate_material


class MaterialNetworkToolTests(unittest.TestCase):
    def test_inspect_material_network_uses_native_graph_handler(self) -> None:
        payload = {
            "ok": True,
            "root": {"name": "BodyMat", "class": "PhysicalMaterial"},
            "nodes": [],
            "issues": [],
        }
        with patch("maxmcp.tools.material_network.client") as client:
            client.native_available = True
            client.send_command.return_value = {"result": json.dumps(payload)}

            result = json.loads(inspect_material_network("CC_Base_Body", depth=4, scope="all_slots"))

        sent = json.loads(client.send_command.call_args.args[0])
        self.assertEqual(client.send_command.call_args.kwargs["cmd_type"], "native:inspect_material_network")
        self.assertEqual(sent["name"], "CC_Base_Body")
        self.assertEqual(sent["depth"], 4)
        self.assertEqual(sent["scope"], "all_slots")
        self.assertEqual(result["root"]["name"], "BodyMat")

    def test_replicate_material_defaults_to_preview_native_handler(self) -> None:
        native = {
            "ok": True,
            "preview": True,
            "plannedFiles": [{"oldPath": "C:/old/diffuse.1001.jpg", "newPath": "D:\\tex\\diffuse.1001.jpg"}],
        }
        with patch("maxmcp.tools.material_network.client") as client:
            client.native_available = True
            client.send_command.return_value = {"result": json.dumps(native)}

            result = json.loads(
                replicate_material(
                    source="CC_Base_Body",
                    target="CC_Base_Body_New",
                    texture_folder="D:/tex",
                )
            )

        sent = json.loads(client.send_command.call_args.args[0])
        self.assertEqual(client.send_command.call_args.kwargs["cmd_type"], "native:replicate_material")
        self.assertEqual(sent["source"], "CC_Base_Body")
        self.assertEqual(sent["targets"], ["CC_Base_Body_New"])
        self.assertTrue(sent["preview"])
        self.assertFalse(sent["include_values"])
        self.assertEqual(result["plannedFiles"][0]["newPath"], "D:\\tex\\diffuse.1001.jpg")

    def test_replicate_material_apply_uses_apply_handler(self) -> None:
        with patch("maxmcp.tools.material_network.client") as client:
            client.native_available = True
            client.send_command.return_value = {"result": '{"ok":true,"preview":false,"status":"applied"}'}

            result = json.loads(
                replicate_material(
                    source="Mat_A",
                    target=["Obj_A", "Obj_B"],
                    mode="clone",
                    preview=False,
                    material_name="Mat_A_Copy",
                )
            )

        sent = json.loads(client.send_command.call_args.args[0])
        self.assertEqual(client.send_command.call_args.kwargs["cmd_type"], "native:replicate_material")
        self.assertFalse(sent["preview"])
        self.assertEqual(sent["mode"], "clone")
        self.assertEqual(sent["material_name"], "Mat_A_Copy")
        self.assertEqual(result["status"], "applied")

    def test_native_unavailable_returns_clear_error(self) -> None:
        with patch("maxmcp.tools.material_network.client") as client:
            client.native_available = False
            result = json.loads(inspect_material_network("Mat_A"))

        self.assertFalse(result["ok"])
        self.assertIn("native C++ bridge", result["error"])


    def test_replicate_material_blocks_apply_when_texture_folder_missing(self) -> None:
        with patch("maxmcp.tools.material_network.client") as client:
            client.native_available = True

            result = json.loads(
                replicate_material(
                    source="Sedna_Body",
                    target="CC_Base_Body",
                    mode="clone_and_remap",
                    preview=False,
                    texture_folder="",
                )
            )

        client.send_command.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertIn("texture_folder", result["errors"][0])

    def test_inspect_material_network_compact_mode(self) -> None:
        payload = {
            "ok": True,
            "query": "Anya_Body",
            "root": {"name": "Anya_Body"},
            "nodes": [
                {
                    "id": "n1",
                    "class": "Image tiles",
                    "files": [{"path": "C:\\tex\\a.jpg", "exists": True, "param": "ImageFilenames_list"}],
                }
            ],
            "issues": [],
            "warnings": [{"code": "CIRCULAR_REF"}],
            "hints": {"replicateReady": True},
        }
        with patch("maxmcp.tools.material_network.client") as client:
            client.native_available = True
            client.send_command.return_value = {"result": json.dumps(payload)}

            result = json.loads(inspect_material_network("Anya_Body", compact=True))

        self.assertNotIn("nodes", result)
        self.assertEqual(len(result["fileManifest"]), 1)
        self.assertEqual(result["warnings"][0]["code"], "CIRCULAR_REF")


if __name__ == "__main__":
    unittest.main()
