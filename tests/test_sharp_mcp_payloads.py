import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from maxmcp.tools.material_replace import batch_replace_materials
from maxmcp.tools.modifiers import collapse_modifier_stack
from maxmcp.tools.objects import delete_objects


class SharpMCPPayloadTests(unittest.TestCase):
    def test_delete_objects_forwards_handles_and_dry_run(self) -> None:
        mock_client = MagicMock()
        mock_client.native_available = True
        mock_client.send_command.return_value = {"result": "{}"}

        with patch("maxmcp.tools.objects.client", mock_client):
            delete_objects(names=["Box"], handles=[123], dry_run=True)

        payload = json.loads(mock_client.send_command.call_args.args[0])
        self.assertEqual(payload, {"names": ["Box"], "handles": [123], "dry_run": True})
        self.assertEqual(mock_client.send_command.call_args.kwargs["cmd_type"], "native:delete_objects")

    def test_native_delete_preflights_and_deduplicates_before_mutation(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native/src/handlers/object_handlers.cpp"
        ).read_text(encoding="utf-8")
        start = source.index("std::string NativeHandlers::DeleteObjects")
        end = source.index("// ── native:transform_object", start)
        handler = source[start:end]

        self.assertIn("std::vector<INode*> targets", handler)
        self.assertIn("seen.insert(node).second", handler)
        self.assertLess(handler.index("CollectNodesByExactName"), handler.index("ip->DeleteNode(node)"))

    def test_collapse_modifier_stack_forwards_handle_and_dry_run(self) -> None:
        mock_client = MagicMock()
        mock_client.native_available = True
        mock_client.send_command.return_value = {"result": "{}"}

        with patch("maxmcp.tools.modifiers.client", mock_client):
            collapse_modifier_stack(handle=456, to_index=2, dry_run=True)

        payload = json.loads(mock_client.send_command.call_args.args[0])
        self.assertEqual(payload, {"name": "", "to_index": 2, "dry_run": True, "handle": 456})
        self.assertEqual(mock_client.send_command.call_args.kwargs["cmd_type"], "native:collapse_modifier_stack")

    def test_batch_replace_materials_maps_dry_run_to_preview(self) -> None:
        mock_client = MagicMock()
        mock_client.native_available = True
        mock_client.send_command.return_value = {"result": "{}"}

        with patch("maxmcp.tools.material_replace.client", mock_client):
            batch_replace_materials(
                replacements=[{"source": "A", "target": "B"}],
                dry_run=True,
            )

        payload = json.loads(mock_client.send_command.call_args.args[0])
        self.assertEqual(payload["replacements"], [{"source": "A", "target": "B"}])
        self.assertEqual(payload["preview"], True)
        self.assertEqual(payload["dry_run"], True)
        self.assertEqual(mock_client.send_command.call_args.kwargs["cmd_type"], "native:batch_replace_materials")


if __name__ == "__main__":
    unittest.main()
