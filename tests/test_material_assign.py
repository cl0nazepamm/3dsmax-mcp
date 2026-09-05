import unittest
from pathlib import Path
from unittest.mock import patch

from maxmcp.helpers.material_assign import existing_material_script, existing_material_result
from maxmcp.tools.material_ops import assign_material
from scripts.gen_tool_registry import extract_tools


class ExistingMaterialTests(unittest.TestCase):
    def test_direct_native_probe_does_not_advertise_python_source_mode(self):
        tool = next(t for t in extract_tools(Path('maxmcp/tools/material_ops.py')) if t['name'] == 'assign_material')
        self.assertNotIn('source_name', tool['schema']['properties'])
        self.assertEqual(tool['schema']['required'], ['material_class'])

    def test_existing_material_uses_source_identity_and_single_assignment(self):
        with patch('maxmcp.tools.material_ops.client.send_command', return_value={'result': 'OK|12|99|14,15,'}) as send:
            result = assign_material(names=['Seat'], handles=[15], source_name='Frame', source_handle=12)
        script = send.call_args.args[0]
        self.assertIn('getAnimByHandle 12', script)
        self.assertIn('Source handle/name mismatch', script)
        self.assertLess(script.index('Target node is no longer valid'), script.index('theHold.Begin()'))
        self.assertIn('node.material = material', script)
        self.assertIn('if ownsHold and theHold.Holding() do theHold.Cancel()', script)
        self.assertEqual(result['assigned_handles'], [14, 15])
        self.assertEqual(result['material_handle'], 99)

    def test_creation_arguments_cannot_be_silently_ignored(self):
        with patch('maxmcp.tools.material_ops.client.send_command') as send:
            with self.assertRaises(ValueError):
                assign_material(names=['Seat'], source_name='Frame', material_class='OpenPBR_Material')
            send.assert_not_called()

    def test_creation_route_is_unchanged(self):
        with patch('maxmcp.tools.material_ops.client.send_command', return_value={'result': 'created'}) as send, patch('maxmcp.max_client.MaxClient.native_available', True):
            self.assertEqual(assign_material(names=['Seat'], material_class='OpenPBR_Material'), 'created')
            self.assertEqual(send.call_args.kwargs['cmd_type'], 'native:assign_material')

    def test_rejects_empty_targets_and_bad_identity(self):
        for names, handles, source_name, source_handle in [([], [], 'Frame', 0), (['Seat'], [-1], 'Frame', 0), (['Seat'], [], '', True), (['Seat'], [], '', 0), (['Seat\0'], [], 'Frame', 0)]:
            with self.subTest(names=names, handles=handles, source_handle=source_handle):
                with self.assertRaises(ValueError):
                    existing_material_script(names, handles, source_name, source_handle)

    def test_names_are_data_not_script(self):
        name='Oak "A"\\ test\nö'
        script = existing_material_script([name], [], name, 0)
        self.assertNotIn(name, script)
        self.assertIn('FromBase64String', script)

    def test_busy_retains_retryable_error(self):
        result = existing_material_result('__BUSY__')
        self.assertEqual(result['code'], 'USER_BUSY')
        self.assertTrue(result['retryable'])

    def test_incomplete_or_failed_readback_never_reports_success(self):
        for raw in ['', '__ERROR__|Source object has no material', 'OK|12|99|', 'OK|0|99|1,', 'OK|12|99|bad,']:
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                existing_material_result(raw)
