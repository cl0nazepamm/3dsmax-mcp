import json
import unittest
from pathlib import Path
from unittest.mock import patch

from maxmcp.tools.scene_patch import resolve_node_refs, scene_patch


class ScenePatchToolTests(unittest.TestCase):
    @patch("maxmcp.tools.scene_patch.client")
    def test_resolve_node_refs_forwards_canonical_selectors(self, client) -> None:
        client.native_available = True
        client.send_command.return_value = {
            "result": '{"refs":[],"sceneSeq":7,"journal":true}'
        }

        result = resolve_node_refs(
            [{"handle": "123", "name": "Camera"}, {"path": "/Rig/Camera"}]
        )

        self.assertIn('"sceneSeq":7', result)
        payload = json.loads(client.send_command.call_args.args[0])
        self.assertEqual(
            payload,
            {
                "refs": [
                    {"handle": "123", "name": "Camera"},
                    {"path": "/Rig/Camera"},
                ]
            },
        )
        self.assertEqual(
            client.send_command.call_args.kwargs["cmd_type"],
            "native:resolve_node_refs",
        )

    @patch("maxmcp.tools.scene_patch.client")
    def test_scene_patch_forwards_guard_dry_run_and_label(self, client) -> None:
        client.native_available = True
        client.send_command.return_value = {
            "result": '{"status":"preflight","dryRun":true}'
        }
        operations = [
            {
                "op": "set_flags",
                "target": {"handle": 123},
                "hidden": True,
            }
        ]

        result = scene_patch(
            operations,
            expected_scene_seq=42,
            dry_run=True,
            label="QA cleanup",
        )

        self.assertIn('"dryRun":true', result)
        payload = json.loads(client.send_command.call_args.args[0])
        self.assertEqual(payload["operations"], operations)
        self.assertEqual(payload["expected_scene_seq"], 42)
        self.assertIs(payload["dry_run"], True)
        self.assertEqual(payload["label"], "QA cleanup")
        self.assertEqual(
            client.send_command.call_args.kwargs["cmd_type"],
            "native:scene_patch",
        )

    @patch("maxmcp.tools.scene_patch.client")
    def test_scene_patch_requires_native_atomicity(self, client) -> None:
        client.native_available = False
        with self.assertRaisesRegex(RuntimeError, "require the native"):
            scene_patch([{"op": "rename", "target": {"name": "A"}, "name": "B"}])
        client.send_command.assert_not_called()

    def test_scene_patch_rejects_negative_sequence_locally(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            scene_patch(
                [{"op": "rename", "target": {"name": "A"}, "name": "B"}],
                expected_scene_seq=-1,
            )


class ScenePatchNativeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.handler = (cls.root / "native/src/handlers/scene_patch_handlers.cpp").read_text(
            encoding="utf-8"
        )
        cls.node_ref = (cls.root / "native/include/mcp_bridge/node_ref.h").read_text(
            encoding="utf-8"
        )
        cls.journal = (cls.root / "native/src/scene_journal.cpp").read_text(
            encoding="utf-8"
        )

    def test_dispatch_and_build_registration_are_present(self) -> None:
        dispatcher = (cls_path := self.root / "native/src/command_dispatcher.cpp").read_text(
            encoding="utf-8"
        )
        cmake = (self.root / "native/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('cmd_type == "native:resolve_node_refs"', dispatcher, cls_path)
        self.assertIn('cmd_type == "native:scene_patch"', dispatcher, cls_path)
        self.assertIn("handlerOwnsTransaction", dispatcher)
        self.assertIn("src/handlers/scene_patch_handlers.cpp", cmake)

    def test_node_ref_contract_has_strict_handle_name_path_resolution(self) -> None:
        self.assertIn('ref.contains("handle")', self.node_ref)
        self.assertIn('ref.contains("name")', self.node_ref)
        self.assertIn('ref.contains("path")', self.node_ref)
        self.assertIn("NODE_REF_MISMATCH", self.node_ref)
        self.assertIn("Ambiguous NodeRef name", self.node_ref)
        self.assertIn("EncodePathSegment", self.node_ref)

    def test_patch_has_guard_preflight_and_strict_native_hold(self) -> None:
        self.assertIn('payload.contains("expected_scene_seq")', self.handler)
        self.assertIn("ThrowSceneConflict", self.handler)
        self.assertIn("BuildPlan", self.handler)
        self.assertIn("theHold.Begin()", self.handler)
        self.assertIn("theHold.Accept", self.handler)
        self.assertIn("theHold.Cancel", self.handler)
        self.assertIn("PATCH_APPLY_FAILED", self.handler)
        self.assertIn("one_native_hold", self.handler)

    def test_node_flags_have_explicit_undo_and_redo_records(self) -> None:
        self.assertIn("class NodeFlagsRestore final : public RestoreObj", self.handler)
        self.assertIn("ApplyFlagsSnapshot(node_, before_)", self.handler)
        self.assertIn("ApplyFlagsSnapshot(node_, after_)", self.handler)
        self.assertIn("theHold.Put(new NodeFlagsRestore", self.handler)

    def test_stale_guard_ignores_activity_only_selection(self) -> None:
        self.assertIn("unsigned long long g_mutationSeq", self.journal)
        self.assertIn('AppendEvent("selection_changed", nodes, false)', self.journal)
        self.assertIn(
            'AppendEvent("subobject_selection_changed", nodes, false)',
            self.journal,
        )
        self.assertIn("SceneJournal::CurrentMutationSeq()", self.handler)
        self.assertIn("SceneJournal::MutationChangesSince", self.handler)
        self.assertIn('result["activitySeq"]', self.handler)

    def test_parent_key_and_casefolded_rename_collision_are_guarded(self) -> None:
        self.assertIn('if (!raw.contains("parent"))', self.handler)
        self.assertIn("pass null to detach to scene root", self.handler)
        self.assertIn("FoldName(simulatedNames[node])", self.handler)
        self.assertIn("FoldName(finalName)", self.handler)


if __name__ == "__main__":
    unittest.main()
