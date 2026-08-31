import json
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

from maxmcp.tools.scene_qa import scene_qa
from scripts.gen_tool_registry import extract_tools


class SceneQAToolTests(unittest.TestCase):
    def test_scan_uses_read_only_native_route(self) -> None:
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.scene_qa.client.send_command", return_value={"result": '{"issues":[]}'}) as send,
        ):
            result = scene_qa(checks=["name_collisions", "invalid_transforms"])

        self.assertEqual(json.loads(result)["issues"], [])
        payload = json.loads(send.call_args.args[0])
        self.assertEqual(send.call_args.kwargs["cmd_type"], "native:scene_qa_scan")
        self.assertEqual(payload["checks"], ["name_collisions", "invalid_transforms"])
        self.assertNotIn("far_origin_threshold", payload)

    def test_fix_passes_stale_guard_and_targets_to_mutating_route(self) -> None:
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.scene_qa.client.send_command", return_value={"result": '{"applied":2}'}) as send,
        ):
            scene_qa(
                action="fix",
                scope="targets",
                handles=[101, 102],
                refs=[{"path": "/Rig/Camera"}],
                fixes=["name_collisions"],
                expected_scene_seq=77,
            )

        payload = json.loads(send.call_args.args[0])
        self.assertEqual(send.call_args.kwargs["cmd_type"], "native:scene_qa_fix")
        self.assertEqual(payload["handles"], [101, 102])
        self.assertEqual(payload["refs"], [{"path": "/Rig/Camera"}])
        self.assertEqual(payload["expected_scene_seq"], 77)

    def test_fix_dry_run_never_uses_mutating_route(self) -> None:
        with (
            patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=True),
            patch("maxmcp.tools.scene_qa.client.send_command", return_value={"result": "{}"}) as send,
        ):
            scene_qa(action="fix", fixes=["empty_names"], dry_run=True)

        self.assertEqual(send.call_args.kwargs["cmd_type"], "native:scene_qa_scan")
        self.assertTrue(json.loads(send.call_args.args[0])["dry_run"])

    def test_rejects_mesh_scope_and_bad_target_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "scope must be"):
            scene_qa(scope="mesh")
        with self.assertRaisesRegex(ValueError, "requires refs"):
            scene_qa(scope="targets")

    def test_requires_native_bridge(self) -> None:
        with patch("maxmcp.max_client.MaxClient.native_available", new_callable=PropertyMock, return_value=False):
            self.assertIn("Native bridge is required", scene_qa())

    def test_native_contract_stays_scene_graph_only(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "src"
            / "handlers"
            / "scene_qa_handlers.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("SceneQAScan", source)
        self.assertIn("SceneQAFix", source)
        self.assertIn("expected_scene_seq", source)
        self.assertIn('"SCENE_CONFLICT"', source)
        self.assertIn('{"mesh_checks_included", false}', source)
        self.assertNotIn("#include <mesh.h>", source)
        self.assertNotIn("#include <mnmesh.h>", source)
        self.assertNotIn("MNMesh", source)

    def test_standalone_chat_exposes_read_only_scan_contract(self) -> None:
        module = (
            Path(__file__).resolve().parents[1]
            / "maxmcp"
            / "tools"
            / "scene_qa.py"
        )
        tool = next(item for item in extract_tools(module) if item["name"] == "scene_qa")

        self.assertEqual(tool["cmdType"], "native:scene_qa_scan")
        properties = tool["schema"]["properties"]
        self.assertIn("checks", properties)
        self.assertNotIn("fixes", properties)
        self.assertNotIn("action", properties)


if __name__ == "__main__":
    unittest.main()
