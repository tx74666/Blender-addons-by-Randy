"""Differential Rest proof against the frozen 0.77.40 implementation.

Only native-like stand-ins run here. Real RNA, mathutils rounding, frame outputs
and timing must also be verified in the isolated Blender validation.
"""

import ast
from dataclasses import dataclass
import math
from pathlib import Path
import struct
import sys
from types import MappingProxyType, ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
BASELINE = Path(__file__).resolve().parent / 'fixtures' / 'motion_rest_proof_07740.py'
FLAGS = ('use_deform', 'use_inherit_rotation', 'inherit_scale',
         'use_local_location', 'use_relative_parent')


def float32(value):
    return struct.unpack('f', struct.pack('f', value))[0]


class Vector:
    def __init__(self, values):
        self.values = tuple(float32(value) for value in values)

    def __iter__(self):
        return iter(self.values)

    def __sub__(self, other):
        return Vector(a - b for a, b in zip(self.values, other.values))

    @property
    def length(self):
        return math.sqrt(sum(value * value for value in self.values))


class Bone:
    def __init__(self, name='bone', *, connected=True, parent=None):
        self.values = dict(name=name, parent=parent, use_connect=connected,
                           head_local=Vector((0., 0., 0.)),
                           tail_local=Vector((0., 1., 0.)),
                           matrix_local=[[float(row == column) for column in range(4)]
                                         for row in range(4)],
                           **dict(zip(FLAGS, (True, True, 'FULL', True, False))))
        self.events, self.errors, self.owners = [], {}, {}

    def __getattr__(self, field):
        if field not in self.values:
            raise AttributeError(field)
        self.events.append(field)
        if field in self.errors:
            raise RuntimeError(self.errors[field])
        return self.values[field]

    def get(self, key):
        self.events.append(('owner', key))
        return self.owners.get(key)


class Bones(dict):
    def __iter__(self):
        return iter(self.values())


def load_ua(path, name):
    wanted = {'_rest_state', '_same_rest_bone', '_same_current_rest_bone',
              '_SavedRestState', '_compile_saved_rest_state', '_matches_saved_rest_state',
              '_matches_current_rest_state', '_matches_rest_state', '_read_matched_rest_state'}
    constants = {'_SAVED_REST_FIELDS', '_SAVED_REST_VECTOR_LENGTHS'}
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    nodes = [node for node in tree.body if
             isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in wanted
             or isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id in constants
                                                    for item in node.targets)]
    module = ModuleType(name)
    module.__dict__.update(dataclass=dataclass, MappingProxyType=MappingProxyType,
                           math=math, Vector=Vector)
    sys.modules[name] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), module.__dict__)
    return module


def load_target(path, name, package):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    node = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == '_prove_motion_data')
    module = ModuleType(name)
    module.__package__ = package
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), module.__dict__)
    return module


def outcome(callback):
    try:
        return ('return', callback())
    except Exception as error:
        return ('raise', type(error), str(error))


class CurrentSnapshotProofTests(unittest.TestCase):
    def setUp(self):
        self.packages, self.ua, self.target = [], {}, {}
        for variant, folder in (('baseline', BASELINE), ('candidate', SOURCE)):
            package_name = '_rest_snapshot_' + uuid.uuid4().hex
            package = ModuleType(package_name)
            package.__path__ = []
            limb = SimpleNamespace(OWNER_KEY='limb_owner')
            skirt = SimpleNamespace(OWNER_KEY='skirt_owner')
            ua_source = folder if folder.is_file() else folder / 'unity_animation.py'
            target_source = folder if folder.is_file() else folder / 'animation_worklist_target.py'
            ua = load_ua(ua_source, package_name + '.unity_animation')
            package.unity_animation, package.limb_ik, package.skirt_rig = ua, limb, skirt
            modules = patch.dict(sys.modules, {package_name: package})
            modules.start()
            self.addCleanup(modules.stop)
            self.addCleanup(sys.modules.pop, ua.__name__, None)
            self.ua[variant] = ua
            self.target[variant] = load_target(target_source,
                                                package_name + '.target', package_name)

    def prepare(self):
        original = Bone(parent=SimpleNamespace(name='root'))
        motion = Bone(connected=False, parent=SimpleNamespace(name='root'))
        first = SimpleNamespace(bones=Bones(bone=original))
        second = SimpleNamespace(bones=Bones(bone=motion))
        original_rest = self.ua['baseline']._rest_state(first)
        motion_rest = self.ua['baseline']._rest_state(second)
        original.events.clear()
        motion.events.clear()
        return first, second, original_rest, motion_rest, original, motion

    def compare_proof(self, configure=lambda *args: None, names=('bone',)):
        results = []
        fixtures = []
        for variant in ('baseline', 'candidate'):
            fixture = self.prepare()
            configure(*fixture)
            first, second, original_rest, motion_rest, original, motion = fixture
            result = outcome(lambda: self.target[variant]._prove_motion_data(
                first, second, original_rest, motion_rest, names))
            results.append(result)
            fixtures.append(fixture)
        self.assertEqual(results[1], results[0])
        return results[1], fixtures

    def test_fresh_full_reads_reused_only_within_one_proof(self):
        result, fixtures = self.compare_proof()
        self.assertEqual(result, ('return', frozenset({'bone'})))
        for bone in fixtures[1][-2:]:
            for field in ('parent', 'head_local', 'tail_local', 'matrix_local', 'use_connect', *FLAGS):
                self.assertEqual(bone.events.count(field), 1, field)
        for bone in fixtures[0][-2:]:
            self.assertEqual(bone.events.count('matrix_local'), 2)
            self.assertGreaterEqual(bone.events.count('use_connect'), 3)

    def test_original_rest_refusal_precedes_motion_native_error(self):
        def configure(first, second, saved_first, saved_second, original, motion):
            original.values['use_deform'] = False
            motion.errors['matrix_local'] = 'later motion native error'
        result, fixtures = self.compare_proof(configure)
        self.assertIn('owned motion-copy Rest changed', result[2])
        self.assertEqual(fixtures[1][-1].events, [])

    def test_motion_native_error_is_still_reported_after_original_full_read(self):
        def configure(first, second, saved_first, saved_second, original, motion):
            motion.errors['tail_local'] = 'motion native tail failure'
        result, fixtures = self.compare_proof(configure)
        self.assertEqual(result, ('raise', RuntimeError, 'motion native tail failure'))
        self.assertIn('use_relative_parent', fixtures[1][-2].events)

    def test_each_live_rest_field_change_is_rejected(self):
        fields = ('parent', 'head', 'tail', 'matrix', 'use_connect', *FLAGS)
        for side in ('original', 'motion'):
            for field in fields:
                with self.subTest(side=side, field=field):
                    def configure(first, second, saved_first, saved_second, original, motion):
                        bone = original if side == 'original' else motion
                        if field == 'parent':
                            bone.values[field] = SimpleNamespace(name='other root')
                        elif field in ('head', 'tail'):
                            bone.values[field + '_local'] = Vector((.01, 0., 0.))
                        elif field == 'matrix':
                            bone.values['matrix_local'][0][1] = .01
                        else:
                            bone.values[field] = 'NONE' if field == 'inherit_scale' else not bone.values[field]
                    result, _ = self.compare_proof(configure)
                    self.assertIn('owned motion-copy Rest changed', result[2])

    def test_matching_saved_records_cannot_hide_copy_structure_changes(self):
        for field in ('parent', 'head', 'tail', 'matrix', *FLAGS):
            with self.subTest(field=field):
                def configure(first, second, saved_first, saved_second, original, motion):
                    if field == 'parent':
                        motion.values[field] = SimpleNamespace(name='other root')
                    elif field in ('head', 'tail'):
                        motion.values[field + '_local'] = Vector((.01, 0., 0.))
                    elif field == 'matrix':
                        motion.values['matrix_local'][0][1] = .01
                    else:
                        motion.values[field] = 'NONE' if field == 'inherit_scale' else not motion.values[field]
                    saved_second.clear()
                    saved_second.update(self.ua['baseline']._rest_state(second))
                result, _ = self.compare_proof(configure)
                self.assertIn('geometry, parents or inheritance', result[2])

    def test_inventory_and_unmapped_connect_exceptions_stay_closed(self):
        result, _ = self.compare_proof(names=())
        self.assertIn('unapproved connected-bone flag', result[2])
        result, _ = self.compare_proof(names=('unknown',))
        self.assertIn('bone inventory', result[2])
        def configure(first, second, saved_first, saved_second, original, motion):
            second.bones['extra'] = Bone('extra', connected=False)
            saved_second.update(self.ua['baseline']._rest_state(second))
        result, _ = self.compare_proof(configure)
        self.assertIn('bone inventory', result[2])

    def test_live_owner_changes_refuse_on_next_call_without_proof_cache(self):
        for side in ('original', 'motion'):
            for key in ('limb_owner', 'skirt_owner'):
                with self.subTest(side=side, key=key):
                    first, second, saved_first, saved_second, original, motion = self.prepare()
                    prove = lambda: self.target['candidate']._prove_motion_data(
                        first, second, saved_first, saved_second, ('bone',))
                    self.assertEqual(prove(), frozenset({'bone'}))
                    bone = original if side == 'original' else motion
                    bone.owners[key] = 'generated owner'
                    result = outcome(prove)
                    self.assertIn('unapproved connected-bone flag', result[2])
                    del bone.owners[key]
                    self.assertEqual(prove(), frozenset({'bone'}))
                    self.assertEqual(original.events.count('matrix_local'), 3)
                    self.assertEqual(motion.events.count('matrix_local'), 3)

    def test_live_rest_changes_after_success_are_read_again(self):
        for side in ('original', 'motion'):
            with self.subTest(side=side):
                first, second, saved_first, saved_second, original, motion = self.prepare()
                prove = lambda: self.target['candidate']._prove_motion_data(
                    first, second, saved_first, saved_second, ('bone',))
                self.assertEqual(prove(), frozenset({'bone'}))
                bone = original if side == 'original' else motion
                bone.values['head_local'] = Vector((.01, 0., 0.))
                self.assertIn('owned motion-copy Rest changed', outcome(prove)[2])
                bone.values['head_local'] = Vector((0., 0., 0.))
                self.assertEqual(prove(), frozenset({'bone'}))
                self.assertEqual(original.events.count('matrix_local'), 3)
                self.assertEqual(motion.events.count('matrix_local'), 2 if side == 'original' else 3)

    def test_snapshot_geometry_matches_frozen_expression_at_boundaries(self):
        for field in ('head', 'tail', 'matrix'):
            for value in (0., math.nextafter(1e-6, 0.), 1e-6,
                          math.nextafter(1e-6, math.inf), 2e-6, math.nan, math.inf):
                with self.subTest(field=field, value=value):
                    first, second, _, _, original, motion = self.prepare()
                    if field == 'matrix':
                        motion.values['matrix_local'][0][1] = value
                    else:
                        motion.values[field + '_local'] = Vector((value, 0. if field == 'head' else 1., 0.))
                    expected = outcome(lambda: self.ua['baseline']._same_rest_bone(original, motion))
                    original_row = self.ua['candidate']._rest_state(first)['bone']
                    motion_row = self.ua['candidate']._rest_state(second)['bone']
                    actual = outcome(lambda: self.ua['candidate']._same_current_rest_bone(original_row, motion_row))
                    self.assertEqual(actual, expected)

    def test_multi_axis_head_delta_keeps_vector_norm_threshold(self):
        for delta, accepted in ((7e-7, True), (8e-7, False)):
            with self.subTest(delta=delta):
                first, second, _, _, original, motion = self.prepare()
                motion.values['head_local'] = Vector((delta, delta, 0.))
                expected = self.ua['baseline']._same_rest_bone(original, motion)
                original_row = self.ua['candidate']._rest_state(first)['bone']
                motion_row = self.ua['candidate']._rest_state(second)['bone']
                actual = self.ua['candidate']._same_current_rest_bone(original_row, motion_row)
                self.assertIs(expected, accepted)
                self.assertIs(actual, accepted)

    def test_matrix_nan_location_keeps_max_order_semantics(self):
        for index in range(16):
            with self.subTest(index=index):
                first, second, _, _, original, motion = self.prepare()
                motion.values['matrix_local'][index // 4][index % 4] = math.nan
                expected = outcome(lambda: self.ua['baseline']._same_rest_bone(original, motion))
                original_row = self.ua['candidate']._rest_state(first)['bone']
                motion_row = self.ua['candidate']._rest_state(second)['bone']
                actual = outcome(lambda: self.ua['candidate']._same_current_rest_bone(original_row, motion_row))
                self.assertEqual(actual, expected)

    def test_public_comparison_still_reads_eagerly_before_malformed_saved_value(self):
        for malformed in (None, [], {}, {'bone': {'head': []}}):
            outcomes = []
            for variant in ('baseline', 'candidate'):
                first, _, _, _, original, _ = self.prepare()
                original.errors['matrix_local'] = 'native error before malformed record'
                outcomes.append(outcome(lambda: self.ua[variant]._matches_rest_state(first, malformed)))
            self.assertEqual(outcomes[1], outcomes[0])
            self.assertEqual(outcomes[1], ('raise', RuntimeError, 'native error before malformed record'))

    def test_current_extra_field_and_compiled_proof_still_refuse(self):
        first, _, saved, _, _, _ = self.prepare()
        ua = self.ua['candidate']
        compiled = ua._compile_saved_rest_state(saved)
        current = ua._rest_state(first)
        self.assertTrue(ua._matches_current_rest_state(current, compiled))
        current['bone']['unexpected'] = 1
        self.assertFalse(ua._matches_current_rest_state(current, compiled))
        self.assertFalse(ua._matches_current_rest_state(current, saved))

    def test_empty_current_snapshot_is_success_not_none(self):
        data = SimpleNamespace(bones=Bones())
        self.assertEqual(self.ua['candidate']._read_matched_rest_state(data, {}), {})
        self.assertEqual(self.target['candidate']._prove_motion_data(data, data, {}, {}, ()), frozenset())


if __name__ == '__main__':
    unittest.main()
