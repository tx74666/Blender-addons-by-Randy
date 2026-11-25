"""Pure regressions for the retained-calibration Enable control.

Actual product definitions run with native-ID/point doubles. These tests prove
disabled routing, transaction rollback and draw semantics; they do not measure
Blender playback, validate native binding, or substitute for native geometry QA.
"""
from __future__ import annotations

import ast
from array import array
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import json
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/forearm_twist.py'
RECORD_KEY = 'character_designer_forearm_twist_v1'


class Owner(dict):
    sequence = 0

    def __init__(self, name='Body', *, records=None, forbid_data=False):
        super().__init__()
        Owner.sequence += 1
        self.pointer = Owner.sequence
        self.name, self.type, self.mode = name, 'MESH', 'OBJECT'
        self.active_shape_key_index = 2
        self.forbid_data = forbid_data
        self.data_reads = 0
        self._data = NS(shape_keys=NS(update_tag=Mock()), update=Mock())
        if records is not None:
            self[RECORD_KEY] = json.dumps(records, sort_keys=True)

    @property
    def data(self):
        self.data_reads += 1
        if self.forbid_data:
            raise AssertionError('Disabled calibration accessed mesh data')
        return self._data

    def as_pointer(self):
        return self.pointer


class Points:
    def __init__(self, values):
        self.values = list(values)

    def __len__(self):
        return len(self.values) // 3

    def foreach_get(self, name, output):
        assert name == 'co'
        output[:] = array('f', self.values)

    def foreach_set(self, name, values):
        assert name == 'co'
        self.values[:] = values


class Layout:
    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls

    def row(self, **kwargs):
        return Layout(self.calls)

    def box(self):
        return Layout(self.calls)

    def operator(self, name, **kwargs):
        self.calls.append(('operator', name, kwargs))
        return NS()

    def label(self, **kwargs):
        self.calls.append(('label', kwargs))

    def prop(self, *args, **kwargs):
        pass


def records(enabled=True):
    return {side: {'key': 'Owned.' + side, 'enabled': enabled,
                   'chain': ['upper.' + side, 'lower.' + side, 'hand.' + side],
                   'rings': [{'vertices': [index, index + 1], 'ratio': .25}],
                   'rest': 'captured-rest-' + side, 'paired': True,
                   'calibration': 'artist-data-' + side}
            for index, side in enumerate(('L', 'R'))}


class Harness:
    def __init__(self):
        self.error = type('ForearmTwistError', (ValueError,), {})
        self.pose_error = type('ForearmTwistPoseRangeError', (self.error,), {})
        self.graph = NS(scene=NS())
        self.graph_get = Mock(return_value=self.graph)
        self.keys = {side: NS(name='Owned.' + side, data=Points([1., 2., 3., 4., 5., 6.]),
                              value=1., mute=False) for side in ('L', 'R')}
        self.prepare = Mock(side_effect=lambda obj, **kwargs: deepcopy(self.ns['_records'](obj)))
        self.numeric = Mock()
        self.scope = Mock(side_effect=AssertionError('Disabled owner entered validation scope'))
        self.ns = dict(json=json, array=array, RECORD_KEY=RECORD_KEY,
                       _SESSION=None, _BUSY=False, _HISTORY_RECOVERY_PENDING=False,
                       _PLAYBACK_PREPARED={}, _CACHE={}, _OUTPUT_CACHE={}, _KEY_REFERENCES={}, _ERRORS={},
                       ForearmTwistError=self.error, ForearmTwistPoseRangeError=self.pose_error,
                       _prepare_runtime_records=self.prepare, _calculate_records=self.numeric,
                       _managed_key=Mock(side_effect=lambda obj, side, record, **kwargs: self.keys[side]),
                       _set_render_lock=Mock(), _redraw=Mock(),
                       _playback_token=Mock(return_value='active-token'),
                       validation_cache=NS(read_scope=self.scope),
                       bpy=NS(context=NS(evaluated_depsgraph_get=self.graph_get)))
        self.context = NS(scene=NS(), evaluated_depsgraph_get=self.graph_get)
        self.load('_records', '_write_records', '_calculate_object', 'update_runtime',
                  'toggle_paired_calibration', '_apply_runtime_records', '_target_only_change')

    def load(self, *names):
        tree = ast.parse(SOURCE.read_text(encoding='utf8'))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        assert {node.name for node in nodes} == set(names)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def scene(self, *owners):
        scene = NS(objects=owners)
        self.graph.scene = scene
        return scene

    def seed_caches(self, obj):
        for name in ('_PLAYBACK_PREPARED', '_CACHE', '_OUTPUT_CACHE'):
            self.ns[name][obj.as_pointer()] = 'stale'


class DisabledRoutingTests(unittest.TestCase):
    def test_disabled_frames_skip_evaluation_preparation_and_native_data(self):
        h = Harness()
        obj = Owner(records=records(False), forbid_data=True)
        before = dict(obj)
        h.seed_caches(obj)
        calculate = h.ns['_calculate_object'] = Mock()
        prepared = {}
        for _ in range(4):
            h.ns['update_runtime'](h.scene(obj), _playback=True, _playback_prepared=prepared)
        h.graph_get.assert_not_called()
        calculate.assert_not_called()
        h.prepare.assert_not_called()
        h.numeric.assert_not_called()
        self.assertEqual(obj.data_reads, 0)
        self.assertEqual(dict(obj), before)
        self.assertEqual(prepared, {})
        self.assertNotIn(obj.as_pointer(), h.ns['_PLAYBACK_PREPARED'])
        self.assertFalse(h.ns['_BUSY'])

    def test_direct_object_calculation_skips_scope_and_binding_validation(self):
        h = Harness()
        obj = Owner(records=records(False), forbid_data=True)
        h.seed_caches(obj)
        h.ns['_calculate_object'](obj, object(), _playback_scene=object())
        h.scope.assert_not_called()
        h.prepare.assert_not_called()
        h.numeric.assert_not_called()
        self.assertEqual(obj.data_reads, 0)
        self.assertNotIn(obj.as_pointer(), h.ns['_PLAYBACK_PREPARED'])

    def test_direct_numeric_entry_skips_keys_vertices_weights_and_matrices(self):
        h = Harness()
        h.load('_calculate_records')
        for saved in ({}, records(False)):
            with self.subTest(saved=saved):
                obj = Owner(records=saved, forbid_data=True)
                h.ns['_calculate_records'](obj, object())
                self.assertEqual(obj.data_reads, 0)

    def test_mixed_owners_preserve_enabled_playback_and_admission(self):
        h = Harness()
        off = Owner('Off', records=records(False), forbid_data=True)
        active = Owner('Active', records=records())
        h.seed_caches(off)
        calculate = h.ns['_calculate_object'] = Mock()
        prepared = {}
        scene = h.scene(off, active)
        h.ns['update_runtime'](scene, _playback=True, _playback_prepared=prepared)
        calculate.assert_called_once_with(active, h.graph, _playback_scene=scene)
        h.graph_get.assert_called_once_with()
        self.assertEqual(prepared, {active.as_pointer(): 'active-token'})
        self.assertNotIn(off.as_pointer(), h.ns['_PLAYBACK_PREPARED'])
        self.assertEqual(off.data_reads, 0)

    def test_one_enabled_side_and_legacy_default_still_receive_full_runtime(self):
        for mixed in (dict(records(False), R=records()['R']),
                      {'L': {key: value for key, value in records()['L'].items() if key != 'enabled'}}):
            with self.subTest(saved=mixed):
                h = Harness()
                obj = Owner(records=mixed)
                calculate = h.ns['_calculate_object'] = Mock()
                h.ns['update_runtime'](h.scene(obj))
                calculate.assert_called_once_with(obj, h.graph)

    def test_saved_flags_are_reread_across_external_off_on_off_changes(self):
        h = Harness()
        obj = Owner(records=records(False), forbid_data=True)
        calculate = h.ns['_calculate_object'] = Mock()
        scene = h.scene(obj)
        h.ns['update_runtime'](scene)
        obj[RECORD_KEY] = json.dumps(records(True))
        h.ns['update_runtime'](scene)
        obj[RECORD_KEY] = json.dumps(records(False))
        h.ns['update_runtime'](scene)
        calculate.assert_called_once_with(obj, h.graph)
        self.assertEqual(obj.data_reads, 0)

    def test_malformed_saved_records_remain_errors_and_do_not_request_graph(self):
        h = Harness()
        obj = Owner()
        obj[RECORD_KEY] = 'broken-json'
        h.ns['_calculate_object'] = Mock()
        h.ns['update_runtime'](h.scene(obj))
        self.assertIn(obj.name, h.ns['_ERRORS'])
        self.assertEqual(obj[RECORD_KEY], 'broken-json')
        h.graph_get.assert_not_called()
        h.ns['_calculate_object'].assert_not_called()
        self.assertFalse(h.ns['_BUSY'])

    def test_enabled_owner_failure_still_mutes_only_authenticated_outputs(self):
        h = Harness()
        obj = Owner(records=records())
        before = dict(obj)
        h.seed_caches(obj)
        h.ns['_calculate_object'] = Mock(side_effect=ValueError('binding changed'))
        h.ns['update_runtime'](h.scene(obj))
        self.assertEqual(h.ns['_ERRORS'][obj.name], 'binding changed')
        self.assertTrue(all(key.mute for key in h.keys.values()))
        self.assertEqual(dict(obj), before)
        self.assertNotIn(obj.as_pointer(), h.ns['_PLAYBACK_PREPARED'])
        self.assertNotIn(obj.as_pointer(), h.ns['_CACHE'])
        self.assertFalse(h.ns['_BUSY'])


class EnableTransactionTests(unittest.TestCase):
    def test_disable_retains_both_calibrations_and_output_coordinates(self):
        h = Harness()
        saved = records()
        obj = Owner(records=saved, forbid_data=True)
        coordinates = {side: list(key.data.values) for side, key in h.keys.items()}
        h.seed_caches(obj)
        h.ns['toggle_paired_calibration'](h.context, obj)
        expected = deepcopy(saved)
        for item in expected.values():
            item['enabled'] = False
        self.assertEqual(h.ns['_records'](obj), expected)
        self.assertTrue(all(key.mute for key in h.keys.values()))
        self.assertEqual({side: key.data.values for side, key in h.keys.items()}, coordinates)
        for name in ('_PLAYBACK_PREPARED', '_CACHE', '_OUTPUT_CACHE'):
            self.assertNotIn(obj.as_pointer(), h.ns[name])
        self.assertEqual(obj.data_reads, 0)
        h.prepare.assert_not_called()
        h.graph_get.assert_not_called()
        h.numeric.assert_not_called()
        self.assertFalse(h.ns['_BUSY'])

    def test_disable_ownership_refusal_is_atomic_for_both_sides(self):
        h = Harness()
        obj = Owner(records=records(), forbid_data=True)
        before = dict(obj)
        h.ns['_managed_key'].side_effect = [h.keys['L'], ValueError('R ownership changed')]
        with self.assertRaisesRegex(ValueError, 'ownership'):
            h.ns['toggle_paired_calibration'](h.context, obj)
        self.assertEqual(dict(obj), before)
        self.assertFalse(any(key.mute for key in h.keys.values()))
        self.assertFalse(h.ns['_BUSY'])

    def test_enable_preparation_failure_keeps_off_without_evaluation_or_writes(self):
        h = Harness()
        obj = Owner(records=records(False), forbid_data=True)
        before = dict(obj)
        for key in h.keys.values():
            key.mute = True
        h.prepare.side_effect = ValueError('strict binding refusal')
        with self.assertRaisesRegex(ValueError, 'strict binding'):
            h.ns['toggle_paired_calibration'](h.context, obj)
        self.assertEqual(dict(obj), before)
        self.assertTrue(all(key.mute for key in h.keys.values()))
        self.assertEqual(obj.data_reads, 0)
        h.graph_get.assert_not_called()
        h.numeric.assert_not_called()

    def test_enable_refuses_renamed_owned_key_before_preparation_without_repair(self):
        for renamed_side in ('L', 'R'):
            with self.subTest(renamed_side=renamed_side):
                h = Harness()
                obj = Owner(records=records(False), forbid_data=True)
                for key in h.keys.values():
                    key.mute = True
                h.keys[renamed_side].name = 'Artist renamed.' + renamed_side
                before = dict(obj)
                authors = {side: (key.name, key.value, key.mute, list(key.data.values))
                           for side, key in h.keys.items()}
                references = {(obj.as_pointer(), side): 'native-' + side for side in h.keys}
                h.ns['_KEY_REFERENCES'].update(references)
                with self.assertRaises(h.error):
                    h.ns['toggle_paired_calibration'](h.context, obj)
                self.assertEqual(dict(obj), before)
                self.assertEqual({side: (key.name, key.value, key.mute, list(key.data.values))
                                  for side, key in h.keys.items()}, authors)
                self.assertEqual(h.ns['_KEY_REFERENCES'], references)
                self.assertTrue(h.ns['_managed_key'].called)
                self.assertTrue(all(call.kwargs.get('repair_name') is False
                                    for call in h.ns['_managed_key'].call_args_list))
                h.prepare.assert_not_called()
                h.graph_get.assert_not_called()
                h.numeric.assert_not_called()
                self.assertEqual(obj.data_reads, 0)
                self.assertFalse(h.ns['_BUSY'])

    def test_enable_numeric_failure_rolls_back_records_mutes_points_and_references(self):
        h = Harness()
        obj = Owner(records=records(False))
        for key in h.keys.values():
            key.mute = True
        before = dict(obj)
        points = {side: list(key.data.values) for side, key in h.keys.items()}
        references = {(obj.as_pointer(), side): 'native-' + side for side in h.keys}
        h.ns['_KEY_REFERENCES'].update(references)
        h.seed_caches(obj)

        def partial_publish(owner, graph):
            self.assertTrue(all(record['enabled'] for record in h.ns['_records'](owner).values()))
            h.keys['L'].data.values[0] = 100.
            h.keys['L'].mute = False
            h.ns['_KEY_REFERENCES'][(obj.as_pointer(), 'L')] = 'changed'
            raise h.pose_error('unsupported pose')

        h.numeric.side_effect = partial_publish
        with self.assertRaisesRegex(h.pose_error, 'unsupported pose'):
            h.ns['toggle_paired_calibration'](h.context, obj)
        self.assertEqual(dict(obj), before)
        self.assertEqual({side: key.data.values for side, key in h.keys.items()}, points)
        self.assertTrue(all(key.mute for key in h.keys.values()))
        self.assertEqual(h.ns['_KEY_REFERENCES'], references)
        self.assertEqual(obj.active_shape_key_index, 2)
        for name in ('_PLAYBACK_PREPARED', '_CACHE', '_OUTPUT_CACHE'):
            self.assertNotIn(obj.as_pointer(), h.ns[name])

    def test_off_cross_frame_on_invalidates_old_cache_before_fresh_publication(self):
        h = Harness()
        obj = Owner(records=records())
        saved = h.ns['_records'](obj)
        h.ns['toggle_paired_calibration'](h.context, obj)
        calculate = h.ns['_calculate_object'] = Mock()
        for _ in range(3):
            h.ns['update_runtime'](h.scene(obj))
        calculate.assert_not_called()
        h.seed_caches(obj)

        def publish(owner, graph):
            self.assertEqual(h.ns['_records'](owner), saved)
            for name in ('_PLAYBACK_PREPARED', '_CACHE', '_OUTPUT_CACHE'):
                self.assertNotIn(obj.as_pointer(), h.ns[name])
            for key in h.keys.values():
                key.mute = False

        h.numeric.side_effect = publish
        h.ns['toggle_paired_calibration'](h.context, obj)
        h.prepare.assert_called_once_with(obj)
        h.numeric.assert_called_once_with(obj, h.graph)
        self.assertFalse(any(key.mute for key in h.keys.values()))
        self.assertEqual(h.ns['_records'](obj), saved)


class EnableDrawTests(unittest.TestCase):
    def test_fixed_label_and_checked_state_leave_errors_visible(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf8'))
        panel = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                     and node.name == 'CHARACTERDESIGNER_PT_forearm_twist')
        draw = next(node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == 'draw')
        for enabled, error in ((True, False), (False, False), (True, True), (False, True)):
            with self.subTest(enabled=enabled, error=error):
                obj = Owner(records=records(enabled), forbid_data=True)
                layout = Layout()
                ns = dict(_SESSION=None, context_mesh=lambda context: obj,
                          _records=lambda owner: json.loads(owner[RECORD_KEY]),
                          _ERRORS={obj.name: 'Binding changed'} if error else {})
                exec(compile(ast.Module(body=[draw], type_ignores=[]), str(SOURCE), 'exec'), ns)
                ns['draw'](NS(layout=layout), NS(window_manager=NS(
                    character_designer_forearm_twist=NS(show_capture=False))))
                controls = [call[2] for call in layout.calls
                            if call[:2] == ('operator', 'character_designer.forearm_twist_toggle')]
                self.assertEqual(controls, [dict(text='Enable',
                    icon='CHECKBOX_HLT' if enabled else 'CHECKBOX_DEHLT', emboss=False)])
                self.assertEqual(any(call[0] == 'label' and call[1].get('text') == 'Binding changed'
                                     for call in layout.calls), error)
                self.assertEqual(obj.data_reads, 0)


if __name__ == '__main__':
    unittest.main()
