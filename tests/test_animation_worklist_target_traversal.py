"""Pure differential checks against the pinned approved087 tree walkers."""

from array import array
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import CodeType, ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
APPROVED_MODULE_SHA256 = '659d9f0fd33deeed83000a57e616415fbb9a971917586f48404b81e6a6b1e3dd'
REFERENCE = {
    '_decode': (
        '7d27d1c3e6f0ae6c99ea791a4ed0fa647592088d2593a4b226ddc9c00db68fad',
        '''def _decode(value, references):
    if isinstance(value, dict):
        if set(value) == {'$id'}:
            target = references.get(value['$id'])
            if target is None:
                raise ValueError('A saved Main Rig recovery data block was removed.')
            return target
        return {key: _decode(entry, references) for key, entry in value.items()}
    if isinstance(value, list):
        return [_decode(entry, references) for entry in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('A saved Main Rig recovery value is not finite.')
    return value
'''),
    '_encoded_reference_keys': (
        '54768e84bd70d8ae1e5949bce4253fd3546a399c9c72644ab3c59b133a0eff64',
        '''def _encoded_reference_keys(value):
    if isinstance(value, dict):
        if set(value) == {'$id'}:
            return {value['$id']}
        return set().union(*(_encoded_reference_keys(entry) for entry in value.values()))
    if isinstance(value, list):
        return set().union(*(_encoded_reference_keys(entry) for entry in value))
    return set()
'''),
}


def references():
    namespace = {'math': math}
    for digest, source in REFERENCE.values():
        assert hashlib.sha256(source.encode()).hexdigest() == digest
        exec(compile(source, '<approved087-walkers>', 'exec'), namespace)
    return namespace


class ID(dict):
    pass


def count_calls(function, *args):
    codes = {function.__code__}
    codes.update(value for value in function.__code__.co_consts
                 if isinstance(value, CodeType) and value.co_name == 'collect')
    count = 0

    def profile(frame, event, _arg):
        nonlocal count
        if event == 'call' and frame.f_code in codes:
            count += 1

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        result = function(*args)
    finally:
        sys.setprofile(previous)
    return result, count


class TraversalTests(unittest.TestCase):
    def setUp(self):
        name = '_target_traversal_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        bpy = ModuleType('bpy')
        bpy.types = NS(ID=ID)
        spec = importlib.util.spec_from_file_location(
            name + '.animation_worklist_target', SOURCE / 'animation_worklist_target.py')
        self.target = importlib.util.module_from_spec(spec)
        modules = patch.dict(sys.modules, {name: package, spec.name: self.target, 'bpy': bpy})
        modules.start()
        self.addCleanup(modules.stop)
        spec.loader.exec_module(self.target)
        self.old = references()

    def compare(self, value, refs=None):
        for name in REFERENCE:
            args = (value, refs or {}) if name == '_decode' else (value,)
            expected = self.old[name](*args)
            actual = getattr(self.target, name)(*args)
            self.assertEqual(actual, expected)
        return self.target._decode(value, refs or {})

    def test_pinned_reference_hashes(self):
        for digest, source in REFERENCE.values():
            self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), digest)

    def test_deep_mixed_tree_preserves_values_identity_and_input(self):
        first, second = object(), object()
        leaf = {'matrix': [float(index) / 3 for index in range(16)],
                'refs': [{'$id': 'rig'}, {'$id': 12}, {'$id': 'rig'}],
                'literal': {'$id': 'not resolved', 'extra': True},
                'unicode': '切替"\\', 'null': None}
        tree = leaf
        for depth in range(80):
            tree = {'child': [tree, depth, False, None, 'text']}
        before = json.dumps(tree, ensure_ascii=False)
        result = self.compare(tree, {'rig': first, 12: second})
        for _depth in range(80):
            result = result['child'][0]
        self.assertIs(result['refs'][0], first)
        self.assertIs(result['refs'][1], second)
        self.assertIsNot(result, leaf)
        self.assertIsNot(result['matrix'], leaf['matrix'])
        self.assertEqual(json.dumps(tree, ensure_ascii=False), before)

    def test_primitive_arrays_tuples_bytes_and_float_subclass_remain_original_values(self):
        class Number(float):
            pass
        values = [array('f', [1, 2, 3]), (1, {'$id': 'literal tuple member'}),
                  bytearray(b'\x00\xff'), b'\x00\xff', {1, 2}, Number(1.25), object()]
        result = self.compare({'values': values})
        for actual, original in zip(result['values'], values):
            self.assertIs(actual, original)
        for value in (None, True, False, 0, -2, '文字', 1.25):
            self.assertEqual(self.compare(value), value)

    def test_id_values_include_nonstring_keys_and_falsy_resolved_values(self):
        for key, resolved in ((0, False), (12, 0), ('empty', ''), ('rig', object())):
            self.assertIs(self.compare({'$id': key}, {key: resolved}), resolved)
        self.assertEqual(self.target._encoded_reference_keys(
            [{'$id': 'a'}, {'$id': 'a'}, {'$id': None}]), {'a', None})

    def test_invalid_id_and_nonfinite_errors_match_and_keep_order(self):
        cases = [
            ({'$id': 'missing'}, {}),
            ({'$id': 'missing'}, {'missing': None}),
            ({'$id': []}, {}),
            ({'$id': {}}, {}),
            ({'first': {'$id': 'missing'}, 'later': float('nan')}, {}),
            ({'first': float('inf'), 'later': {'$id': 'missing'}}, {}),
            ([1.0, {'matrix': [float('-inf')]}], {}),
        ]
        for value, refs in cases:
            for name in REFERENCE:
                args = (value, refs) if name == '_decode' else (value,)
                outcomes = []
                for function in (self.old[name], getattr(self.target, name)):
                    try:
                        outcomes.append(('result', function(*args)))
                    except (ValueError, TypeError) as exc:
                        outcomes.append((type(exc), str(exc)))
                self.assertEqual(*outcomes)

    def test_each_call_resolves_current_map_and_nested_reference_changes(self):
        first, second = object(), object()
        value, refs = {'mixed': [1, {'$id': 'rig'}]}, {'rig': first}
        self.assertIs(self.target._decode(value, refs)['mixed'][1], first)
        refs['rig'] = second
        self.assertIs(self.target._decode(value, refs)['mixed'][1], second)
        value['mixed'].append({'$id': 'new'})
        self.assertEqual(self.target._encoded_reference_keys(value), {'rig', 'new'})
        del refs['rig']
        with self.assertRaisesRegex(ValueError, 'removed'):
            self.target._decode(value, refs)

    def test_container_aliases_are_detached_and_cycles_still_refuse(self):
        shared = [1, 2, {'$id': 'rig'}]
        result = self.compare([shared, shared], {'rig': object()})
        self.assertIsNot(result[0], result[1])
        cycle = []
        cycle.append(cycle)
        for name in REFERENCE:
            args = (cycle, {}) if name == '_decode' else (cycle,)
            for function in (self.old[name], getattr(self.target, name)):
                with self.assertRaises(RecursionError):
                    function(*args)

    def test_load_roundtrip_recovery_identity_and_tamper_refusal(self):
        rig, original, motion = ID(), ID(), ID()
        record = {'version': 1, 'rig': rig, 'scene': None, 'view_layer': 'Layer',
                  'workspace_identity': 'workspace', 'capture': {'targets': [
                      {'target': rig, 'data': original, 'rest': {'bone': list(range(16))}}]},
                  'packet_path': 'packet', 'packet_sha256': 'f' * 64,
                  'motion_data': motion, 'motion_rest': {'bone': list(range(16))},
                  'mapped_names': ['bone']}
        self.target._store(rig, record)
        loaded = self.target._load(rig)
        self.assertIs(loaded['rig'], rig)
        self.assertIs(loaded['capture']['targets'][0]['data'], original)
        self.assertIs(loaded['motion_data'], motion)
        encoded = json.loads(rig[self.target.SESSION])
        encoded['capture']['targets'][0]['rest']['bone'][0] = 99
        rig[self.target.SESSION] = json.dumps(encoded)
        with self.assertRaisesRegex(ValueError, 'changed or was truncated'):
            self.target._load(rig)

    def test_bounded_call_counts_exclude_primitive_rest_leaves(self):
        value = {'bones': [{'matrix': [float(index) / 10 for index in range(16)],
                            'head': [0.0, 1.0, 2.0], 'name': str(bone),
                            'rig': {'$id': 'rig'}} for bone in range(24)]}
        refs = {'rig': object()}
        for name in REFERENCE:
            args = (value, refs) if name == '_decode' else (value,)
            old_result, old_calls = count_calls(self.old[name], *args)
            new_result, new_calls = count_calls(getattr(self.target, name), *args)
            self.assertEqual(new_result, old_result)
            self.assertGreater(old_calls, new_calls * 4, (name, old_calls, new_calls))


if __name__ == '__main__':
    unittest.main()
