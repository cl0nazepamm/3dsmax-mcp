import json
import unittest
from unittest.mock import patch

from src.tools import organize


class OrganizeToolTests(unittest.TestCase):
    def test_manage_layers_forwards_advanced_properties(self) -> None:
        with patch.object(
            organize.client,
            "send_command",
            return_value={"result": '{"ok":true}'},
        ) as mocked_send:
            result = organize.manage_layers(
                action="set_properties",
                name="LayerA",
                xRayMtl=True,
                backCull=True,
                allEdges=True,
                vertTicks=True,
                trajectory=True,
                primaryVisibility=False,
                secondaryVisibility=False,
            )

        self.assertEqual(result, '{"ok":true}')
        mocked_send.assert_called_once()

        payload = json.loads(mocked_send.call_args.args[0])
        cmd_type = mocked_send.call_args.kwargs.get("cmd_type")
        self.assertEqual(cmd_type, "native:manage_layers")
        self.assertEqual(payload["action"], "set_properties")
        self.assertEqual(payload["name"], "LayerA")
        self.assertEqual(payload["xRayMtl"], True)
        self.assertEqual(payload["backCull"], True)
        self.assertEqual(payload["allEdges"], True)
        self.assertEqual(payload["vertTicks"], True)
        self.assertEqual(payload["trajectory"], True)
        self.assertEqual(payload["primaryVisibility"], False)
        self.assertEqual(payload["secondaryVisibility"], False)

    def test_manage_groups_uses_native_command_type(self) -> None:
        with patch.object(
            organize.client,
            "send_command",
            return_value={"result": "{}"},
        ) as mocked_send:
            organize.manage_groups(action="detach", names=["A"])

        payload = json.loads(mocked_send.call_args.args[0])
        cmd_type = mocked_send.call_args.kwargs.get("cmd_type")
        self.assertEqual(cmd_type, "native:manage_groups")
        self.assertEqual(payload["action"], "detach")
        self.assertEqual(payload["names"], ["A"])

    def test_manage_selection_sets_uses_native_command_type(self) -> None:
        with patch.object(
            organize.client,
            "send_command",
            return_value={"result": "{}"},
        ) as mocked_send:
            organize.manage_selection_sets(action="replace", name="SetA", names=["ObjA"])

        payload = json.loads(mocked_send.call_args.args[0])
        cmd_type = mocked_send.call_args.kwargs.get("cmd_type")
        self.assertEqual(cmd_type, "native:manage_selection_sets")
        self.assertEqual(payload["action"], "replace")
        self.assertEqual(payload["name"], "SetA")
        self.assertEqual(payload["names"], ["ObjA"])


if __name__ == "__main__":
    unittest.main()
