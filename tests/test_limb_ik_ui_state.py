"""Fresh UI limb metadata never enters animation/Rest/recovery admission."""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


class LimbIKError(ValueError):
    pass


class Properties(dict):
    def get(self, key, default=None):
        if key.startswith(('character_designer_worklist', 'character_designer_hair')):
            raise AssertionError('UI entered historical authority metadata')
        return super().get(key, default)


class Bone(Properties):
    def __init__(self, name, **tags):
        super().__init__(tags)
        self.name = name

    def __getattr__(self, name):
        raise AssertionError('UI inspected native geometry/constraints: ' + name)


class Bones(list):
    def get(self, name):
        return next((bone for bone in self if bone.name == name), None)


class Data(Properties):
    pass


class Armature:
    type = 'ARMATURE'

    def __init__(self, data):
        self.data = data

    def as_pointer(self):
        return id(self)

    def __getattr__(self, name):
        raise AssertionError('UI inspected pose/animation/context: ' + name)


def limb_stub():
    limb = ModuleType('_limb_ui_test.limb_ik')
    for key, suffix in {
        'OWNER_KEY': 'owner', 'VERSION_KEY': 'limb_ik_version',
        'ROLE_KEY': 'limb_ik_role', 'KIND_KEY': 'limb_ik_kind',
        'SIDE_KEY': 'limb_ik_side', 'RIG_ID_KEY': 'limb_ik_rig_id',
        'ARMATURE_ID_KEY': 'limb_ik_armature_id', 'CHAIN_KEY': 'limb_ik_chain',
        'SCHEMA_KEY': 'limb_ik_schema',
        'TARGET_ROTATION_VERSION_KEY': 'limb_ik_target_rotation_version',
    }.items():
        setattr(limb, key, 'character_designer_' + suffix)
    limb.OWNER_VALUE = 'limb_ik'
    limb.RIG_VERSION = limb.TARGET_ROTATION_VERSION = limb.LEGACY_SCHEMA = 1
    limb.ENHANCED_SCHEMA, limb.ROLL_DECOUPLED_SCHEMA = 2, 3
    limb.LEGACY_DIRECT_PREROLL_SCHEMA, limb.DIRECT_PREROLL_SCHEMA = 4, 5
    limb.LimbIKError = LimbIKError
    limb.foot_controls = SimpleNamespace(_SUSPENDED=set())
    for name in ('_validate_inventory', '_validation_source_rest',
                 '_owned_constraint_records', '_capture_context'):
        setattr(limb, name, Mock(side_effect=AssertionError('Deep admission: ' + name)))
    return limb


class LimbDisplayTests(unittest.TestCase):
    def setUp(self):
        self.limb = limb_stub()
        package = ModuleType('_limb_ui_test')
        package.__path__ = []
        package.limb_ik = self.limb
        self.modules = patch.dict(sys.modules, {
            package.__name__: package, self.limb.__name__: self.limb,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        path = Path(__file__).resolve().parents[1] / 'addons/character_designer/limb_ik_ui_state.py'
        spec = importlib.util.spec_from_file_location('_limb_ui_test.limb_ik_ui_state', path)
        self.ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ui)
        data = Data({self.limb.ARMATURE_ID_KEY: 'artist', self.limb.SCHEMA_KEY: 5,
                     self.limb.TARGET_ROTATION_VERSION_KEY: 1})
        data.bones = Bones()
        self.rig = Armature(data)
        self.arm = self.add_pair('ARM', 'L')

    def add_pair(self, kind, side):
        limb, bones = self.limb, self.rig.data.bones
        names = tuple(f'{kind}.{side}.{i}' for i in range(3))
        bones.extend(Bone(name) for name in names)
        result = []
        for role in ('HAND_IK' if kind == 'ARM' else 'FOOT_IK', 'POLE'):
            bone = Bone(f'{role}.{side}', **{
                limb.OWNER_KEY: limb.OWNER_VALUE, limb.VERSION_KEY: 1,
                limb.ARMATURE_ID_KEY: 'artist', limb.ROLE_KEY: role,
                limb.KIND_KEY: kind, limb.SIDE_KEY: side,
                limb.RIG_ID_KEY: kind + side, limb.CHAIN_KEY: json.dumps(names),
            })
            bones.append(bone)
            result.append(bone)
        return result

    def read(self, **kwargs):
        return self.ui.inventory(self.rig, **kwargs)

    def foot_record(self, direction='NATURAL'):
        leg = self.add_pair('LEG', 'L')
        record = dict(version=1, side='L', rig_id='LEGL',
                      chain=json.loads(leg[0][self.limb.CHAIN_KEY]),
                      target=leg[0].name, rotation_direction=direction,
                      bone_states={'unused': 'not part of the UI DTO'})
        self.rig.data[self.ui._FOOT_RECORD] = json.dumps(dict(version=1, legs={'L': record}))
        return leg, record

    def test_current_four_pairs_are_explicitly_presentation_only(self):
        for key in (('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R')):
            self.add_pair(*key)
        result = self.read()
        self.assertTrue(result['presentation_only'])
        self.assertEqual(len(result['rigs']), 4)
        self.assertIs(result['rigs']['ARM', 'L']['target'], self.arm[0])
        self.assertIsInstance(result['rigs']['ARM', 'L']['chain'], tuple)
        self.assertNotIn('records', result)

    def test_fresh_rename_and_result_mutation_do_not_leave_cached_state(self):
        first = self.read()
        self.arm[0].name = 'Renamed wrist'
        first['rigs'].clear()
        second = self.read()
        self.assertEqual(second['rigs']['ARM', 'L']['target'].name, 'Renamed wrist')
        source = self.rig.data.bones.get('ARM.L.0')
        source.name = 'Renamed upper'
        with self.assertRaises(LimbIKError):
            self.read()
        for bone in self.arm:
            bone[self.limb.CHAIN_KEY] = json.dumps(['Renamed upper', 'ARM.L.1', 'ARM.L.2'])
        self.assertEqual(self.read()['rigs']['ARM', 'L']['chain'][0], 'Renamed upper')

    def test_fresh_target_removal_and_owner_drift_fail_closed(self):
        self.read()
        self.rig.data.bones.remove(self.arm[0])
        with self.assertRaises(LimbIKError):
            self.read()
        self.rig.data.bones.append(self.arm[0])
        self.arm[0][self.limb.OWNER_KEY] = 'foreign'
        with self.assertRaises(LimbIKError):
            self.read()

    def test_non_owned_bones_and_unrelated_helpers_are_ignored(self):
        self.rig.data.bones.extend((Bone('foreign', **{self.limb.ROLE_KEY: 'POLE'}),
                                    Bone('master', **{self.limb.OWNER_KEY: 'limb_ik',
                                                     self.limb.ROLE_KEY: 'MASTER'})))
        self.assertEqual(len(self.read()['rigs']), 1)

    def test_duplicate_control_and_duplicate_cross_limb_identity_fail_closed(self):
        duplicate = Bone('duplicate', **dict(self.arm[0]))
        self.rig.data.bones.append(duplicate)
        with self.assertRaises(LimbIKError):
            self.read()
        self.rig.data.bones.remove(duplicate)
        other = self.add_pair('ARM', 'R')
        for bone in other:
            bone[self.limb.RIG_ID_KEY] = 'ARML'
        with self.assertRaises(LimbIKError):
            self.read()

    def test_mismatched_pair_and_role_tags_fail_closed(self):
        for key, value in ((self.limb.RIG_ID_KEY, 'other'),
                           (self.limb.ARMATURE_ID_KEY, 'other'),
                           (self.limb.VERSION_KEY, True),
                           (self.limb.KIND_KEY, 'LEG'),
                           (self.limb.SIDE_KEY, '?')):
            with self.subTest(key=key):
                old = self.arm[0][key]
                self.arm[0][key] = value
                with self.assertRaises(LimbIKError):
                    self.read()
                self.arm[0][key] = old

    def test_chain_malformed_oversized_missing_and_mismatched_fail_closed(self):
        original = self.arm[0][self.limb.CHAIN_KEY]
        for raw in (None, '[]', '[', 'x' * (self.ui._MAX_CHAIN_JSON + 1),
                    json.dumps(['ARM.L.0', 'ARM.L.1', 'missing']),
                    json.dumps(['ARM.L.0', 'ARM.L.0', 'ARM.L.2']),
                    json.dumps([True, 'ARM.L.1', 'ARM.L.2']),
                    json.dumps(['ARM.L.2', 'ARM.L.1', 'ARM.L.0'])):
            with self.subTest(raw=raw):
                self.arm[0][self.limb.CHAIN_KEY] = raw
                with self.assertRaises(LimbIKError):
                    self.read()
        self.arm[0][self.limb.CHAIN_KEY] = original
        self.rig.data.bones.remove(self.rig.data.bones.get('ARM.L.1'))
        with self.assertRaises(LimbIKError):
            self.read()

    def test_data_replacement_and_schema_drift_are_read_fresh(self):
        self.read()
        self.rig.data[self.limb.SCHEMA_KEY] = True
        with self.assertRaises(LimbIKError):
            self.read()
        data = Data()
        data.bones = Bones()
        self.rig.data = data
        result = self.read()
        self.assertEqual(result['armature_id'], '')
        self.assertEqual(result['rigs'], {})

    def test_daily_does_not_read_foot_metadata_or_deep_authority(self):
        self.rig.data[self.ui._FOOT_RECORD] = object()
        original = self.rig.data.get
        def guarded(key, default=None):
            if key == self.ui._FOOT_RECORD:
                raise AssertionError('Daily display read Advanced Foot recovery')
            return original(key, default)
        self.rig.data.get = guarded
        self.read()
        for name in ('_validate_inventory', '_validation_source_rest',
                     '_owned_constraint_records', '_capture_context'):
            getattr(self.limb, name).assert_not_called()

    def test_advanced_foot_is_a_fresh_minimal_projection(self):
        _leg, record = self.foot_record()
        first = self.read(include_foot=True)['rigs']['LEG', 'L']['foot_controls']
        self.assertEqual(first['rotation_direction'], 'NATURAL')
        self.assertTrue(first['presentation_only'])
        self.assertNotIn('bone_states', first)
        record['rotation_direction'] = 'LEGACY'
        self.rig.data[self.ui._FOOT_RECORD] = json.dumps(dict(version=1, legs={'L': record}))
        second = self.read(include_foot=True)['rigs']['LEG', 'L']['foot_controls']
        self.assertEqual(second['rotation_direction'], 'LEGACY')
        self.rig.data.pop(self.ui._FOOT_RECORD)
        self.assertNotIn('foot_controls', self.read(include_foot=True)['rigs']['LEG', 'L'])

    def test_bad_advanced_foot_metadata_fails_closed(self):
        _leg, record = self.foot_record()
        for field, value in (('version', True), ('side', 'R'), ('rig_id', 'other'),
                             ('chain', []), ('target', 'missing'),
                             ('rotation_direction', 'unknown')):
            with self.subTest(field=field):
                bad = dict(record, **{field: value})
                self.rig.data[self.ui._FOOT_RECORD] = json.dumps(dict(version=1, legs={'L': bad}))
                with self.assertRaises(LimbIKError):
                    self.read(include_foot=True)
        for raw in ('[]', '[', 'x' * (self.ui._MAX_FOOT_JSON + 1),
                    json.dumps(dict(version=2, legs={})),
                    json.dumps(dict(version=1, legs={'?': record}))):
            with self.subTest(raw=raw[:40]):
                self.rig.data[self.ui._FOOT_RECORD] = raw
                with self.assertRaises(LimbIKError):
                    self.read(include_foot=True)

    def test_suspended_foot_matches_existing_visibility_without_caching(self):
        self.foot_record()
        suspended = self.limb.foot_controls._SUSPENDED
        suspended.add((self.rig.as_pointer(), 'L'))
        self.assertNotIn('foot_controls', self.read(include_foot=True)['rigs']['LEG', 'L'])
        suspended.clear()
        self.assertIn('foot_controls', self.read(include_foot=True)['rigs']['LEG', 'L'])

    def test_chain_length_is_rejected_before_json_decode(self):
        self.arm[0][self.limb.CHAIN_KEY] = 'x' * (self.ui._MAX_CHAIN_JSON + 1)
        with patch.object(self.ui.json, 'loads', side_effect=AssertionError('Oversized JSON decoded')):
            with self.assertRaises(LimbIKError):
                self.read()


if __name__ == '__main__':
    unittest.main()
