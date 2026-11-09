"""Offline provider regressions; --pid runs acceptance in an explicitly chosen
disposable Max session with FStorm and the native bridge. No render/scene reset.
The live check creates uniquely named lights and deletes only those test nodes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from maxmcp.tools.lighting import _lighting as l
from maxmcp.tools.lighting import _fstorm_lighting as fs


# Selected native PB2 descriptors captured on FStorm 2.0.0Z / Max 2027.2.
# Includes the plugin's duplicate targeted descriptor, which must resolve once.
AREA = [('enabled', 0, 'bool'), ('visible', 1, 'bool'), ('gi_visible', 21, 'bool'),
        ('cast_shadows', 24, 'bool'), ('targeted', 2, 'bool'), ('double_sided', 16, 'bool'),
        ('affect_diffuse', 22, 'bool'), ('affect_glossy', 23, 'bool'),
        ('size_x', 4, 'worldUnits'), ('size_y', 5, 'worldUnits'), ('shape', 6, 'int'),
        ('power', 7, 'float'), ('color_type', 11, 'int'), ('color', 8, 'color'),
        ('temperature', 12, 'float'), ('directional', 9, 'bool'), ('ies_enabled', 15, 'bool')]
SUN = [('enabled', 0, 'bool'), ('power', 1, 'float'), ('size', 4, 'float'),
       ('model', 50, 'int'), ('sun_color', 14, 'color'), ('targeted', 6, 'bool'),
       ('affect_diffuse', 15, 'bool'), ('visible', 17, 'bool'), ('affect_glossy', 16, 'bool'),
       ('targeted', 6, 'bool'), ('hour', 8, 'float'), ('month', 9, 'float'),
       ('latitude', 10, 'float'), ('north_direction', 11, 'float')]
CONTEXT = {'context_token': 'test', 'meters_per_unit': .001}


def schema(*, class_ref):
    sun = tuple(class_ref['class_id']) == fs.FSTORM_SUN
    return {'identity': {'label': 'FStormSunLight' if sun else 'FStormLight'},
            'schema_token': 'test', 'properties': [
                {'name': name, 'property_ref': l.prop(pid), 'type': typ}
                for name, pid, typ in (SUN if sun else AREA)]}


def specs():
    return [
        {'kind': 'area', 'shape': 'rectangle', 'size': {'width': 2, 'height': 1},
         'position': [1, 2, 3], 'orientation': {'direction': [0, 0, -1]},
         'output': {'value': 10, 'unit': 'renderer'}, 'color': {'kelvin': 3200},
         'fstorm': {'visible': False, 'double_sided': True}},
        {'kind': 'area', 'shape': 'disk', 'size': {'radius': .25},
         'orientation': {'direction': [0, 0, -1]}, 'output': {'value': 2, 'unit': 'renderer'}},
        {'kind': 'area', 'shape': 'sphere', 'size': {'radius': .5},
         'output': {'value': 3, 'unit': 'renderer'}, 'color': {'rgb': [.2, .4, .6]}},
        {'kind': 'directional', 'output': {'value': 1, 'unit': 'renderer'},
         'fstorm': {'solar': {'hour': 15, 'month': 8, 'latitude': 45, 'north_direction': 10}, 'sun_size': 2}},
    ]


class ProviderTests(unittest.TestCase):
    def compile(self, values, family='fstorm'):
        with patch.object(l.api, 'all_properties', side_effect=schema):
            return l.compile_lights([l.LightSpec.model_validate(v) for v in values], CONTEXT, family, 'm')

    def test_sizes_units_solar_and_unique_bindings(self):
        plan = self.compile(specs())
        self.assertEqual(len(plan.payload['creates']), 4)
        for i, expected in enumerate(({'size_x': 1000, 'size_y': 500}, {'size_x': 250},
                                      {'size_x': 500}, {'hour': 15, 'model': 1, 'targeted': False})):
            key = f'light_{i}'
            names = {p['property_ref']['param_id']: p['name'] for p in plan.schemas[key]['properties']}
            edits = [e for e in plan.payload['edits'] if e['owner_ref']['created'] == key]
            values = {names[e['property_ref']['param_id']]: e['value'] for e in edits}
            self.assertEqual(len(edits), len(values), 'Duplicate property writes must not reach native patch')
            for name, value in expected.items():
                self.assertEqual(values[name], value)
        self.assertEqual(plan.payload['creates'][0]['matrix'][3], [1000, 2000, 3000])

    def test_unsupported_inputs_fail_before_native_commit(self):
        cases = []
        for change in ({'output': {'value': 1, 'unit': 'lm'}}, {'output': {'value': 0, 'unit': 'renderer'}},
                       {'color': {'kelvin': 25000}}, {'fstorm': {'solar': {'hour': 9}}},
                       {'shape': 'cylinder', 'size': {'radius': 1, 'length': 2}}):
            cases.append({**specs()[0], **change})
        for change in ({'orientation': {'direction': [0, 0, -1]}}, {'cast_shadows': False},
                       {'color': {'kelvin': 6500}}, {'body': 'moon'},
                       {'fstorm': {'solar': {'hour': 25}}}, {'fstorm': {'solar': {}}},
                       {'fstorm': {'solar': {'hour': 9}, 'double_sided': True}},
                       {'fstorm': {'solar': {'hour': 9}, 'sun_model': 'physical'}, 'color': {'rgb': [1, 1, 1]}}):
            cases.append({**specs()[3], **change})
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.compile([value])
        with self.assertRaisesRegex(ValueError, 'FStorm controls'):
            self.compile([specs()[0]], 'corona')

    def test_other_directional_providers_still_require_orientation(self):
        base = {'kind': 'directional', 'output': {'value': 1, 'unit': 'renderer'}}
        with self.assertRaises(ValueError):
            l.LightSpec.model_validate(base)
        self.assertIsNotNone(l.LightSpec.model_validate({**base, 'orientation': {'direction': [1, 0, -1]}}))

    def test_schema_conflict_is_not_hidden_by_duplicate_handling(self):
        plan = self.compile([specs()[3]])
        props = plan.schemas['light_0']['properties']
        bad = deepcopy(next(p for p in props if p['name'] == 'targeted'))
        bad['property_ref'] = l.prop(999)
        props.append(bad)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            plan.set('light_0', 'targeted', False)
        next(p for p in props if p['name'] == 'power')['type'] = 'filename'
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            plan.set('light_0', 'power', 1.0)

    def test_targeted_solar_edits_refuse_before_assignment(self):
        assignments = []
        with self.assertRaisesRegex(ValueError, 'untargeted'):
            fs.apply_controls(fs.FStormControls(solar={'hour': 9}), sun=True, targeted=True,
                              assign=lambda *args: assignments.append(args))
        self.assertEqual(assignments, [])


def live(pid):
    os.environ['MCP_MAX_PID'] = str(pid)
    from maxmcp.server import client
    check = unittest.TestCase()
    prefix = 'MCP_FStorm_Test_' + uuid4().hex + '_'
    values = specs()
    for i, value in enumerate(values):
        value['name'] = prefix + str(i)
    context = l.api.native('native:lighting_context', {})
    scale = 1 / context['meters_per_unit']
    created = None

    def state(ref):
        return l.inspect_one(ref['owner_ref'], 'fstorm')

    def request(entry, changes, token=None):
        return {'light_ref': entry['light_ref'], 'expected_light': token or state(entry['light_ref'])['light_token'], 'changes': changes}

    def size_matches(actual, expected):
        check.assertEqual(set(actual), set(expected))
        for name, value in expected.items():
            check.assertAlmostEqual(actual[name], value, delta=max(1e-6, abs(value)*1e-6))

    try:
        created = l.create(values, requested='fstorm', unit='m')
        check.assertEqual(len(created['lights']), 4)
        entries = created['lights']
        for entry in entries:
            check.assertTrue(entry['state']['decoded'], entry)
        size_matches(entries[0]['state']['size'], {'width': 2*scale, 'height': scale})
        check.assertEqual(entries[0]['state']['color'], {'kelvin': 3200})
        check.assertFalse(entries[0]['state']['fstorm']['visible'])
        size_matches(entries[1]['state']['size'], {'radius': .25*scale})
        size_matches(entries[2]['state']['size'], {'radius': .5*scale})
        check.assertEqual(entries[3]['state']['direction_source'], 'solar')
        check.assertEqual(entries[3]['state']['fstorm']['sun_model'], 'physical')

        # Independent geometric check via Max's bounds, not the provider's decoder.
        for i, (width, height) in enumerate(((2, 1), (.5, .5), (1, 1))):
            handle = int(entries[i]['light_ref']['node_ref']['handle'])
            code = f'''(local n = getAnimByHandle {handle}
local b = nodeGetBoundingBox n n.transform
if abs ((b[2].x-b[1].x) - {width*scale}) > 0.01 do throw "width mismatch"
if abs ((b[2].y-b[1].y) - {height*scale}) > 0.01 do throw "height mismatch"
"BOUNDS_PASS")'''
            check.assertEqual(client.send_command(code)['result'], 'BOUNDS_PASS')

        old_token = entries[0]['state']['light_token']
        edits = [request(entries[0], {'output': {'value': 5, 'unit': 'renderer'},
                                     'size': {'width': 1, 'height': .5}, 'color': {'rgb': [.8, .6, .4]},
                                     'fstorm': {'visible': True, 'affect_glossy': False}}),
                 request(entries[1], {'enabled': False, 'cast_shadows': False}),
                 request(entries[2], {'fstorm': {'gi_visible': False, 'affect_diffuse': False}}),
                 request(entries[3], {'fstorm': {'solar': {'hour': 9}, 'sun_model': 'legacy'}, 'color': {'rgb': [1, .9, .7]}})]
        result = l.edit(edits, unit='m')['lights']
        size_matches(result[0]['size'], {'width': scale, 'height': .5*scale})
        check.assertEqual(result[0]['output']['value'], 5)
        check.assertTrue(result[0]['fstorm']['visible'])
        check.assertFalse(result[0]['fstorm']['affect_glossy'])
        for actual, expected in zip(result[0]['color']['rgb'], [.8, .6, .4]):
            check.assertAlmostEqual(actual, expected, places=5)
        check.assertFalse(result[1]['enabled'])
        check.assertFalse(result[1]['cast_shadows'])
        check.assertFalse(result[2]['fstorm']['gi_visible'])
        check.assertFalse(result[2]['fstorm']['affect_diffuse'])
        check.assertEqual(result[3]['fstorm']['solar'], {'hour': 9, 'month': 8, 'latitude': 45, 'north_direction': 10})
        check.assertEqual(result[3]['fstorm']['sun_model'], 'legacy')
        with check.assertRaisesRegex(ValueError, 'STALE_LIGHT'):
            l.edit([request(entries[0], {'enabled': False}, old_token)])
        # A bad second edit must prevent the valid first edit from committing.
        with check.assertRaisesRegex(ValueError, 'untargeted|no Kelvin'):
            l.edit([request(entries[0], {'enabled': False}), request(entries[3], {'color': {'kelvin': 5000}})])
        check.assertTrue(state(entries[0]['light_ref'])['enabled'])

        # Existing targeted suns retain target ownership; no implicit mode change.
        sun_handle = int(entries[3]['light_ref']['node_ref']['handle'])
        check.assertEqual(client.send_command(f'(local n = getAnimByHandle {sun_handle}; n.targeted = true; "TARGETED_PASS")')['result'], 'TARGETED_PASS')
        with check.assertRaisesRegex(ValueError, 'untargeted'):
            l.edit([request(entries[3], {'fstorm': {'solar': {'hour': 12}}})])
        check.assertTrue(state(entries[3]['light_ref'])['targeted'])
        check.assertEqual(l.api.native('native:lighting_context', {})['renderer'], context['renderer'])
    finally:
        # Only this run's UUID names; no reset, save, render, or unrelated deletion.
        cleanup = f'''(local nodes = for n in objects where matchPattern n.name pattern:"{prefix}*" collect n
for n in nodes where isValidNode n do (local t = try(n.target)catch(undefined); if isValidNode t do delete t; if isValidNode n do delete n)
"CLEANUP_PASS")'''
        check.assertEqual(client.send_command(cleanup)['result'], 'CLEANUP_PASS')
    print('LIVE ALL PASS: shape bounds, units, colors, visibility, solar partial edits, stale/batch/target guards and cleanup.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, help='Run live acceptance on this DISPOSABLE Max process only.')
    args = parser.parse_args()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ProviderTests))
    if not result.wasSuccessful():
        sys.exit(1)
    if args.pid:
        live(args.pid)
