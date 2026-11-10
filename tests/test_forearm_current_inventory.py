"""Existing paired outputs validate current native inputs, never old recovery."""
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


class ID(dict):
    __hash__ = object.__hash__
    __eq__ = object.__eq__

    def __init__(self, name, **fields):
        super().__init__()
        self.name = self.name_full = name
        self.library = self.override_library = None
        self.is_editable = True
        self.__dict__.update(fields)

    def __bool__(self):
        return True


class Object(ID):
    pass


class Armature(ID):
    pass


class Scene(ID):
    pass


class Items(dict):
    def __iter__(self):
        return iter(self.values())


class CurrentInventoryTests(unittest.TestCase):
    def setUp(self):
        name = '_forearm_current_' + uuid.uuid4().hex
        package = ModuleType(name)
        package.__path__ = [str(SOURCE)]
        self.data = Armature('Current data', bones=Items())
        for side in ('L', 'R'):
            for role in ('upper', 'lower', 'hand'):
                bone = ID(role + '.' + side, id_data=self.data,
                          matrix_local=((1., 0.), (0., float(len(self.data.bones) + 1))))
                self.data.bones[bone.name] = bone
        self.rig = Object('Current rig', type='ARMATURE', data=self.data)
        self.mesh = Object('Current body', type='MESH', data=ID('Mesh data'), rig=self.rig)
        self.scene = Scene('Current scene', objects=Items({self.rig.name: self.rig,
                                                         self.mesh.name: self.mesh}))
        self.bpy = ModuleType('bpy')
        self.bpy.types = NS(ID=ID, Object=Object, Armature=Armature, Scene=Scene)
        self.bpy.context = NS(scene=self.scene)
        self.bpy.data = NS(objects=Items(dict(self.scene.objects)))
        self.original = ModuleType(name + '.body_original_mode')
        self.original.active = Mock(return_value=False)
        self.ua = ModuleType(name + '.unity_animation')
        self.ua._compile_saved_rest_state = Mock(return_value=('immutable text proof',))
        self.forearm = ModuleType(name + '.forearm_twist')
        self.forearm.RECORD_KEY = 'forearm_records'
        self.forearm._records = lambda obj: json.loads(obj[self.forearm.RECORD_KEY])
        self.forearm._rest_signature = lambda rig, names: [
            [bone_name, [value for row in rig.data.bones[bone_name].matrix_local for value in row]]
            for bone_name in names]
        self.forearm._check_mesh = Mock(side_effect=lambda obj: obj.rig)
        self.records = {}
        for side in ('L', 'R'):
            chain = [role + '.' + side for role in ('upper', 'lower', 'hand')]
            self.records[side] = dict(chain=chain, armature=self.rig.name,
                rest=self.forearm._rest_signature(self.rig, chain), key='Output.' + side,
                enabled=True, paired=True)
        self.mesh[self.forearm.RECORD_KEY] = json.dumps(self.records)
        self.limb = ModuleType(name + '.limb_ik')
        for field in ('OWNER_KEY', 'ROLE_KEY', 'KIND_KEY', 'SIDE_KEY', 'RIG_ID_KEY',
                      'ARMATURE_ID_KEY', 'CHAIN_KEY', 'SCHEMA_KEY', 'TARGET_ROTATION_VERSION_KEY'):
            setattr(self.limb, field, field)
        self.limb.OWNER_VALUE, self.limb.LEGACY_SCHEMA = 'owned limb', 1
        self.data[self.limb.ARMATURE_ID_KEY] = 'current-armature-id'
        self.data[self.limb.SCHEMA_KEY] = 5
        self.data[self.limb.TARGET_ROTATION_VERSION_KEY] = 2
        self.limb.validation_json = NS(decode=json.loads)
        self.limb._owned = lambda bone, armature_id, role, rig_id: (
            bone.get(self.limb.OWNER_KEY) == self.limb.OWNER_VALUE
            and bone.get(self.limb.ARMATURE_ID_KEY) == armature_id
            and bone.get(self.limb.ROLE_KEY) == role and bone.get(self.limb.RIG_ID_KEY) == rig_id)
        self.limb._wrist_helper_name = lambda side: 'Wrist helper.' + side
        self.limb._validate_inventory = Mock(side_effect=AssertionError('Generic inventory is outside this route'))
        self.fk = ModuleType(name + '.limb_ik_fk')
        self.wrist = ModuleType(name + '.limb_ik_wrist_rotation')
        self.wrist._local_inventory = Mock(side_effect=self.local_inventory)
        self.rig.pose = NS(bones=Items())
        for bone in self.data.bones:
            self.rig.pose.bones[bone.name] = ID(bone.name, id_data=self.rig, bone=bone, constraints=[])
        self.routes = {}
        for side in ('L', 'R'):
            for role, name in (('HAND_IK', 'Target.' + side), ('POLE', 'Pole.' + side),
                               ('POLE_DISPLAY', 'Display.' + side)):
                bone = ID(name, id_data=self.data)
                bone.update({self.limb.OWNER_KEY: self.limb.OWNER_VALUE, self.limb.ROLE_KEY: role,
                    self.limb.KIND_KEY: 'ARM', self.limb.SIDE_KEY: side,
                    self.limb.ARMATURE_ID_KEY: 'current-armature-id', self.limb.RIG_ID_KEY: 'rig-' + side,
                    self.limb.CHAIN_KEY: json.dumps(self.records[side]['chain'])})
                self.data.bones[name] = bone
                self.rig.pose.bones[name] = ID(name, id_data=self.rig, bone=bone, constraints=[])
            entries = []
            for role, name in (('IK', 'lower.' + side), ('END_ROTATION', 'hand.' + side)):
                pose = self.rig.pose.bones[name]
                constraint = ID('Owned ' + role + side, id_data=self.rig)
                pose.constraints.append(constraint)
                entries.append((pose, constraint, {'role': role}))
            self.routes[side] = dict(chain=tuple(self.records[side]['chain']),
                target=self.data.bones['Target.' + side], pole=self.data.bones['Pole.' + side],
                display=self.data.bones['Display.' + side], entries=entries, auto_rotation_space='LOCAL')
        self.json_scope = ModuleType(name + '.validation_json')
        self.json_scope.read_scope = nullcontext
        self.native_scope = ModuleType(name + '.validation_native')
        self.native_scope.read_scope = nullcontext
        modules = {name: package, 'bpy': self.bpy}
        for module in (self.original, self.ua, self.forearm, self.limb, self.fk, self.wrist,
                       self.json_scope, self.native_scope):
            modules[module.__name__] = module
            setattr(package, module.__name__.rsplit('.', 1)[-1], module)
        mounted = patch.dict(sys.modules, modules)
        mounted.start()
        self.addCleanup(mounted.stop)
        self.target = self.load(name + '.animation_worklist_target', SOURCE / 'animation_worklist_target.py')
        setattr(package, 'animation_worklist_target', self.target)
        self.current = self.load(name + '.forearm_current_inventory', SOURCE / 'forearm_current_inventory.py')
        self.old_action = ID('Historical Action')
        self.store()

    @staticmethod
    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def store(self, *, extra=None, motion=None):
        capture = {'targets': [{'target': self.rig, 'data': self.data,
                    'rest': {'historical': 'text'}, 'action': self.old_action,
                    'action_fake_user': False, 'snapshot': {'had_action': True, 'slot': 7}}],
                   'metadata': [], 'context': {}}
        if extra:
            extra(capture)
        record = dict(version=1, rig=self.rig, scene=self.scene, view_layer='ViewLayer',
            workspace_identity='Historical workspace', capture=capture,
            packet_path='historical packet', packet_sha256='0' * 64,
            motion_data=self.data if motion is None else motion,
            motion_rest={'historical': 'text'}, mapped_names=['hand.L', 'hand.R'])
        self.target._store(self.rig, record)

    def remove_action_reference(self):
        encoded = json.loads(self.rig[self.target.SESSION])
        key = encoded['capture']['targets'][0]['action']['$id']
        self.rig[self.target.REFS].pop(key)
        return key

    def local_inventory(self, limb, match, rig, target):
        self.assertIs(limb, self.limb)
        self.assertIs(match, self.fk)
        self.assertIs(rig, self.rig)
        side = target.bone[self.limb.SIDE_KEY]
        self.assertIs(target, self.rig.pose.bones['Target.' + side])
        return ('ARM', side), self.routes[side]

    def tearDown(self):
        self.limb._validate_inventory.assert_not_called()

    def run_current(self, **kwargs):
        return self.current.validate_current_native(self.mesh, self.rig, scene=self.scene, **kwargs)

    def protected(self):
        return self.rig.get(self.target.SESSION), dict(self.rig.get(self.target.REFS, {})), self.mesh[self.forearm.RECORD_KEY]

    def test_healthy_and_unmanaged_read_the_same_complete_current_inventory(self):
        first = self.run_current()
        del self.rig[self.target.SESSION]
        second = self.run_current()
        self.assertEqual(first, second)
        self.assertEqual(self.wrist._local_inventory.call_count, 4)
        self.assertEqual(first['scope'], 'FOREARM_CURRENT_NATIVE')

    def test_all_refs_absent_current_inventory_passes_but_Main_still_refuses(self):
        del self.rig[self.target.REFS]
        before = self.protected()
        with self.assertRaisesRegex(ValueError, 'removed'):
            self.target.control_rest_view(self.rig)
        result = self.run_current(sides={'L'})
        self.assertEqual(set(result['rigs']), {('ARM', 'L'), ('ARM', 'R')})
        self.assertEqual(self.wrist._local_inventory.call_count, 2)
        self.assertEqual(self.protected(), before)
        self.assertNotIn('source_rest', result)
        self.assertFalse(hasattr(self.current, '_NativeIdentityRestView'))
        with self.assertRaisesRegex(ValueError, 'removed'):
            self.target.control_rest_view(self.rig)

    def test_original_never_uses_current_native_admission(self):
        self.remove_action_reference()
        self.original.active.return_value = True
        self.assertIsNone(self.run_current())
        self.wrist._local_inventory.assert_not_called()

    def test_any_historical_missing_reference_is_not_current_native_authority(self):
        for location in ('context', 'metadata', 'scene', 'rig', 'data', 'motion_data'):
            with self.subTest(location=location):
                self.store(extra=lambda capture: capture['context'].update(active=self.old_action))
                encoded = json.loads(self.rig[self.target.SESSION])
                node = {'context': encoded['capture']['context']['active'],
                        'metadata': encoded['capture']['context']['active'],
                        'scene': encoded['scene'], 'rig': encoded['rig'],
                        'data': encoded['capture']['targets'][0]['data'],
                        'motion_data': encoded['motion_data']}[location]
                del self.rig[self.target.REFS][node['$id']]
                self.assertIsNotNone(self.run_current())
                with self.assertRaisesRegex(ValueError, 'removed'):
                    self.target.control_rest_view(self.rig)
        self.assertEqual(self.wrist._local_inventory.call_count, 12)

    def test_aliased_historical_refs_are_not_repaired_or_borrowed(self):
        self.store(extra=lambda capture: capture['context'].update(active=self.old_action))
        encoded = json.loads(self.rig[self.target.SESSION])
        key = encoded['capture']['targets'][0]['action']['$id']
        encoded['capture']['context']['active'] = {'$id': key}
        self.rewrite(encoded)
        self.rig[self.target.REFS].pop(key)
        before = self.protected()
        self.assertIsNotNone(self.run_current())
        self.assertEqual(self.protected(), before)

    def rewrite(self, encoded, *, rehash=True):
        import hashlib
        if rehash:
            encoded['capture_sha256'] = hashlib.sha256(json.dumps(encoded['capture'],
                ensure_ascii=False, allow_nan=False, sort_keys=True,
                separators=(',', ':')).encode()).hexdigest()
        self.rig[self.target.SESSION] = json.dumps(encoded)

    def test_invalid_historical_text_is_never_decoded_by_the_current_reader(self):
        original = self.rig[self.target.SESSION]
        encoded = json.loads(original)
        encoded['capture']['context']['changed'] = True
        self.rewrite(encoded, rehash=False)
        self.assertIsNotNone(self.run_current())
        with self.assertRaisesRegex(ValueError, 'changed or was truncated'):
            self.target.control_rest_view(self.rig)
        encoded = json.loads(original)
        encoded['packet_path'] = float('nan')
        self.rewrite(encoded, rehash=False)
        self.assertIsNotNone(self.run_current())
        with self.assertRaisesRegex(ValueError, 'not finite'):
            self.target.control_rest_view(self.rig)
        self.assertEqual(self.wrist._local_inventory.call_count, 4)

    def test_enabled_state_is_kept_and_invalid_enabled_values_refuse(self):
        records = self.forearm._records(self.mesh)
        records['R']['enabled'] = False
        self.mesh[self.forearm.RECORD_KEY] = json.dumps(records)
        before = self.protected()
        self.assertIsNotNone(self.run_current())
        self.assertEqual(self.protected(), before)
        records['R']['enabled'] = 1
        self.mesh[self.forearm.RECORD_KEY] = json.dumps(records)
        with self.assertRaisesRegex(ValueError, 'enabled state'):
            self.run_current()

    def test_historical_core_IDs_never_replace_current_actual_binding(self):
        for field in ('rig', 'target', 'data', 'motion_data', 'scene'):
            with self.subTest(field=field):
                self.store()
                encoded = json.loads(self.rig[self.target.SESSION])
                node = {'rig': encoded['rig'], 'target': encoded['capture']['targets'][0]['target'],
                        'data': encoded['capture']['targets'][0]['data'],
                        'motion_data': encoded['motion_data'], 'scene': encoded['scene']}[field]
                self.rig[self.target.REFS][node['$id']] = ID('Foreign native ID')
                before = self.protected()
                self.assertIsNotNone(self.run_current())
                self.assertEqual(self.protected(), before)

    def test_separate_historical_motion_is_not_a_Rest_projection(self):
        self.store(motion=Armature('Separate motion', bones=self.data.bones))
        self.assertIsNotNone(self.run_current())
        self.assertTrue(all(call.args[2] is self.rig for call in self.wrist._local_inventory.call_args_list))

    def test_foreign_actual_binding_and_local_Scene_membership_refuse(self):
        self.bpy.data.objects['Other rig'] = Object('Other rig', type='ARMATURE', data=self.data)
        with self.assertRaisesRegex(ValueError, 'actually shares'):
            self.run_current()
        self.bpy.data.objects.pop('Other rig')
        self.mesh.rig = Object('Other modifier rig', type='ARMATURE', data=self.data)
        with self.assertRaisesRegex(ValueError, 'bound to another'):
            self.run_current()
        self.mesh.rig = self.rig
        self.scene.objects.pop(self.mesh.name)
        with self.assertRaisesRegex(ValueError, 'saved Scene'):
            self.run_current()

    def test_no_calibration_does_not_create_or_validate_one(self):
        self.mesh[self.forearm.RECORD_KEY] = '{}'
        self.assertIsNone(self.run_current())
        self.assertEqual(self.mesh[self.forearm.RECORD_KEY], '{}')
        self.wrist._local_inventory.assert_not_called()

    def test_both_existing_arm_rest_and_chain_bindings_are_required(self):
        valid = self.mesh[self.forearm.RECORD_KEY]
        for change in ('right_rest', 'rig_name', 'chain'):
            with self.subTest(change=change):
                records = json.loads(valid)
                if change == 'right_rest': records['R']['rest'][0][1][0] += .1
                if change == 'rig_name': records['L']['armature'] = 'Old rig'
                if change == 'chain': records['R']['chain'][1] = 'Missing lower'
                self.mesh[self.forearm.RECORD_KEY] = json.dumps(records)
                with self.assertRaises(ValueError):
                    self.run_current(sides={'L'})
                self.assertEqual(self.mesh[self.forearm.RECORD_KEY], json.dumps(records))
        self.wrist._local_inventory.assert_not_called()

    def test_local_inventory_failures_propagate_without_author_writes(self):
        before = self.protected()
        def reject(limb, match, rig, target):
            raise ValueError('Actual owned driver changed')
        self.wrist._local_inventory.side_effect = reject
        with self.assertRaisesRegex(ValueError, 'owned driver changed'):
            self.run_current()
        self.assertEqual(self.protected(), before)

    def test_target_is_resolved_from_unique_owned_metadata_and_chain_not_saved_alias(self):
        records = self.forearm._records(self.mesh)
        records['L']['target'] = 'hand.L'
        records['R']['target'] = 'hand.R'
        self.mesh[self.forearm.RECORD_KEY] = json.dumps(records)
        self.assertIsNotNone(self.run_current())
        duplicate = ID('Duplicate target', id_data=self.data)
        duplicate.update(self.data.bones['Target.L'])
        self.data.bones[duplicate.name] = duplicate
        with self.assertRaisesRegex(ValueError, 'unique owned Hand Target'):
            self.run_current()

    def test_empty_and_single_side_calibration_keep_the_existing_route(self):
        records = self.forearm._records(self.mesh)
        records.pop('R')
        self.mesh[self.forearm.RECORD_KEY] = json.dumps(records)
        before = self.protected()
        self.assertIsNone(self.run_current())
        self.assertEqual(self.protected(), before)
        self.wrist._local_inventory.assert_not_called()

    def test_paired_bare_native_FK_keeps_the_existing_resolver(self):
        for side in ('L', 'R'):
            self.data.bones['Target.' + side].pop(self.limb.ROLE_KEY)
        before = self.protected()
        self.assertIsNone(self.run_current())
        self.assertEqual(self.protected(), before)
        self.wrist._local_inventory.assert_not_called()

    def test_partial_missing_owned_Hand_Target_still_refuses(self):
        self.data.bones['Target.R'].pop(self.limb.ROLE_KEY)
        before = self.protected()
        with self.assertRaisesRegex(ValueError, 'unique owned Hand Target'):
            self.run_current()
        self.assertEqual(self.protected(), before)

    def test_explicit_callback_Scene_does_not_borrow_the_active_context_Scene(self):
        other = Scene('Unrelated context Scene', objects=Items())
        self.bpy.context.scene = other
        self.assertIsNotNone(self.run_current())
        self.assertIs(self.bpy.context.scene, other)
        with self.assertRaisesRegex(ValueError, 'saved Scene'):
            self.current.validate_current_native(self.mesh, self.rig)

    def test_extra_unverified_constraint_on_every_local_endpoint_refuses(self):
        for name in ('upper.L', 'lower.L', 'hand.L', 'Target.L', 'Pole.L', 'Display.L'):
            with self.subTest(name=name):
                pose = self.rig.pose.bones[name]
                extra = ID('Unverified extra relation', id_data=self.rig)
                pose.constraints.append(extra)
                before = self.protected()
                with self.assertRaisesRegex(ValueError, 'unverified constraint'):
                    self.run_current()
                self.assertEqual(self.protected(), before)
                pose.constraints.remove(extra)

    def test_inventory_cannot_change_core_or_calibration_during_admission(self):
        def mutate(limb, match, rig, target):
            result = self.local_inventory(limb, match, rig, target)
            self.mesh[self.forearm.RECORD_KEY] += ' '
            records = json.loads(self.mesh[self.forearm.RECORD_KEY])
            records['R']['enabled'] = False
            self.mesh[self.forearm.RECORD_KEY] = json.dumps(records)
            return result
        self.wrist._local_inventory.side_effect = mutate
        with self.assertRaisesRegex(ValueError, 'calibration changed'):
            self.run_current()


if __name__ == '__main__':
    unittest.main()
