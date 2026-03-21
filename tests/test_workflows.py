import json
import unittest
from unittest.mock import patch

from src.tools.workflows import (
    add_modifier_verified,
    assign_material_verified,
    create_object_verified,
    inspect_active_target,
    set_object_property_verified,
    set_modifier_state_verified,
    set_material_verified,
    transform_object_verified,
)


class WorkflowToolTests(unittest.TestCase):
    def test_inspect_active_target_prefers_single_selection(self) -> None:
        with (
            patch("src.tools.snapshots.get_selection_snapshot", return_value='{"selected":1,"objects":[{"name":"Box001"}]}'),
            patch("src.tools.inspect.inspect_object", return_value='{"name":"Box001","class":"Box"}'),
            patch("src.tools.bridge.get_bridge_status", return_value='{"connected":true}'),
        ):
            result = json.loads(inspect_active_target())

        self.assertEqual(result["mode"], "single_selection")
        self.assertEqual(result["target"]["name"], "Box001")
        self.assertEqual(result["bridge"]["connected"], True)

    def test_inspect_active_target_falls_back_to_scene_summary(self) -> None:
        with (
            patch("src.tools.snapshots.get_selection_snapshot", return_value='{"selected":0,"objects":[]}'),
            patch("src.tools.snapshots.get_scene_snapshot", return_value='{"objectCount":2,"roots":["A","B"]}'),
            patch("src.tools.session_context.get_session_context", return_value='{"scene":{"objectCount":2}}'),
        ):
            result = json.loads(inspect_active_target(detail="summary"))

        self.assertEqual(result["mode"], "scene_summary")
        self.assertEqual(result["scene"]["objectCount"], 2)

    def test_create_object_verified_returns_delta_and_object(self) -> None:
        with (
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"added":[{"name":"NewBox"}]}']),
            patch("src.tools.objects.create_object", return_value="NewBox"),
            patch("src.tools.selection.select_objects", return_value="Selected 1 of 1 objects"),
            patch("src.tools.inspect.inspect_object", return_value='{"name":"NewBox","class":"Box"}'),
        ):
            result = json.loads(create_object_verified("Box", name="NewBox"))

        self.assertEqual(result["created"], "NewBox")
        self.assertEqual(result["delta"]["added"][0]["name"], "NewBox")
        self.assertEqual(result["object"]["class"], "Box")

    def test_assign_material_verified_returns_slots_and_objects(self) -> None:
        with (
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"modified":[{"name":"Box001"}]}']),
            patch("src.tools.material_ops.assign_material", return_value="assigned"),
            patch("src.tools.inspect.inspect_object", return_value='{"name":"Box001","material":{"name":"Mat"}}'),
            patch("src.tools.material_ops.get_material_slots", return_value='{"class":"ai_standard_surface","mapSlots":["base_color_shader"]}'),
        ):
            result = json.loads(assign_material_verified(["Box001"], "ai_standard_surface", "Mat"))

        self.assertEqual(result["assignResult"], "assigned")
        self.assertEqual(result["objects"][0]["name"], "Box001")
        self.assertEqual(result["materialSlots"]["class"], "ai_standard_surface")

    def test_set_material_verified_returns_delta_and_slots(self) -> None:
        with (
            patch("src.tools.workflows.client") as mocked_client,
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"modified":[{"name":"Box001","material":{"from":"A","to":"B"}}]}']),
            patch("src.tools.material_ops.set_material_properties", return_value="set"),
            patch("src.tools.inspect.inspect_object", return_value='{"name":"Box001","material":{"name":"Mat"}}'),
            patch("src.tools.material_ops.get_material_slots", side_effect=[
                '{"class":"ai_standard_surface","numericSlots":[{"name":"metalness","value":"0.0"}]}',
                '{"class":"ai_standard_surface","numericSlots":[{"name":"metalness","value":"1.0"}]}',
            ]),
        ):
            mocked_client.native_available = False
            result = json.loads(set_material_verified("Box001", {"metalness": "1.0"}))

        self.assertEqual(result["setResult"], "set")
        self.assertEqual(result["delta"]["modified"][0]["name"], "Box001")
        self.assertEqual(result["materialSlots"]["class"], "ai_standard_surface")
        self.assertEqual(result["slotChanges"]["metalness"]["before"], "0.0")
        self.assertEqual(result["slotChanges"]["metalness"]["after"], "1.0")

    def test_add_modifier_verified_returns_modifier_details(self) -> None:
        with (
            patch("src.tools.workflows.client") as mocked_client,
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"modified":[{"name":"Box001","modifierCount":{"from":0,"to":1}}]}']),
            patch("src.tools.modifiers.add_modifier", return_value="added"),
            patch("src.tools.inspect.inspect_object", return_value='{"name":"Box001","modifiers":[{"name":"Bend","class":"Bend"},{"name":"TurboSmooth","class":"TurboSmooth"}]}'),
            patch("src.tools.inspect.inspect_modifier_properties", return_value='{"target":"modifier","class":"Bend"}') as mocked_inspect_modifier,
        ):
            mocked_client.native_available = False
            result = json.loads(add_modifier_verified("Box001", "Bend", "angle:15"))

        mocked_inspect_modifier.assert_called_once_with("Box001", modifier_index=1)
        self.assertEqual(result["addResult"], "added")
        self.assertEqual(result["object"]["name"], "Box001")
        self.assertEqual(result["modifier"]["class"], "Bend")

    def test_transform_object_verified_returns_delta_and_object(self) -> None:
        with (
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"modified":[{"name":"Box001","position":{"from":[0,0,0],"to":[10,0,0]}}]}']),
            patch("src.tools.transform.transform_object", return_value="transformed"),
            patch("src.tools.inspect.inspect_object", return_value='{"name":"Box001","position":[10,0,0]}'),
        ):
            result = json.loads(transform_object_verified("Box001", move=[10, 0, 0]))

        self.assertEqual(result["transformResult"], "transformed")
        self.assertEqual(result["delta"]["modified"][0]["name"], "Box001")
        self.assertEqual(result["object"]["position"], [10, 0, 0])

    def test_set_modifier_state_verified_returns_modifier_details(self) -> None:
        with (
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"modified":[]}']),
            patch("src.tools.modifiers.set_modifier_state", return_value="state-set"),
            patch("src.tools.inspect.inspect_object", side_effect=[
                '{"name":"Box001","modifiers":[{"name":"Shell","class":"Shell","enabled":true,"enabledInViews":true,"enabledInRenders":true}]}',
                '{"name":"Box001","modifiers":[{"name":"Shell","class":"Shell","enabled":true,"enabledInViews":false,"enabledInRenders":true}]}',
            ]),
            patch("src.tools.inspect.inspect_modifier_properties", return_value='{"target":"modifier","class":"Shell"}') as mocked_inspect_modifier,
        ):
            result = json.loads(set_modifier_state_verified("Box001", modifier_name="Shell", enabled=False))

        mocked_inspect_modifier.assert_called_once_with("Box001", modifier_index=1)
        self.assertEqual(result["stateResult"], "state-set")
        self.assertEqual(result["modifier"]["class"], "Shell")
        self.assertEqual(result["modifierStateChanges"]["enabledInViews"]["before"], True)
        self.assertEqual(result["modifierStateChanges"]["enabledInViews"]["after"], False)

    def test_set_object_property_verified_returns_before_and_after(self) -> None:
        with (
            patch("src.tools.snapshots.get_scene_delta", side_effect=['{"baseline":true}', '{"modified":[{"name":"Box001","position":{"from":[0,0,0],"to":[5,0,0]}}]}']),
            patch("src.tools.objects.set_object_property", return_value="set"),
            patch("src.tools.inspect.inspect_object", side_effect=[
                '{"name":"Box001","position":[0,0,0]}',
                '{"name":"Box001","position":[5,0,0]}',
            ]),
        ):
            result = json.loads(set_object_property_verified("Box001", "pos", "[5,0,0]"))

        self.assertEqual(result["setResult"], "set")
        self.assertEqual(result["property"], "pos")
        self.assertEqual(result["objectBefore"]["position"], [0, 0, 0])
        self.assertEqual(result["object"]["position"], [5, 0, 0])


if __name__ == "__main__":
    unittest.main()
