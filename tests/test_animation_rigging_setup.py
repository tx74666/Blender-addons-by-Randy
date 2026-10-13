"""Pure boundaries of the actual fresh-import wrapper; no Blender import/run."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_rigging_setup.py'


class ID:
    def __init__(self, **fields):
        self.__dict__.update(fields)
        self.properties = {}

    def get(self, name, default=None):
        return self.properties.get(name, default)

    def as_pointer(self):
        return id(self)


class Matrix:
    def __init__(self):
        self.values = [[1., 0., 0., 0.], [0., 1., 0., 0.],
                       [0., 0., 1., 0.], [0., 0., 0., 1.]]

    def __iter__(self):
        return iter(self.values)

    def __matmul__(self, value):
        return value

    def copy(self):
        return copy.deepcopy(self)


class NativeCollection(dict):
    def __iter__(self):
        return iter(self.values())


class Operator:
    def __init__(self, fixture):
        self.fixture = fixture

    def poll(self):
        return True

    def __call__(self, *args):
        f = self.fixture
        f.calls.append(('public_build', args))
        f.public_settings = f.settings_snapshot()
        if f.cancel:
            f.settings.last_message = 'strict native gate rejected this chain'
            return {'CANCELLED'}
        if f.change_rest:
            f.rig.rest['RUpper']['head'] = [99., 0., 0.]
        f.inventory = {'schema': 3, 'rigs': {('ARM', side):
            {'chain': list(f.chains[side]), 'target': ID(name='Target.' + side)} for side in ('R', 'L')}}
        f.rig.animation_data = types.SimpleNamespace(action=None, drivers=[object()],
            nla_tracks=[], use_tweak_mode=False)
        f.settings.last_message = 'both exact arms built'
        return {'FINISHED'}


class Fixture:
    def __init__(self, directory):
        self.calls, self.cancel, self.change_rest, self.bad_binding = [], False, False, False
        self.chains = {'R': ('RUpper', 'RMid', 'RTip'), 'L': ('LUpper', 'LMid', 'LTip')}
        native = ['Hips', *self.chains['R'], *self.chains['L']]
        bones = {}
        for name in native:
            bones[name] = ID(name=name, parent=None, use_deform=True, matrix_local=Matrix())
        for chain in self.chains.values():
            bones[chain[0]].parent = bones['Hips']
            bones[chain[1]].parent = bones[chain[0]]
            bones[chain[2]].parent = bones[chain[1]]
        data = ID(bones=bones, users=1, library=None, override_library=None)
        self.collection, self.old_scene = ID(name='new collection'), ID(name='original scene')
        self.rig = ID(name='Cosha', type='ARMATURE', data=data, library=None,
            override_library=None, matrix_world=Matrix(), animation_data=None,
            users_collection=[self.collection], rest={name: {'head': [0., 0., 0.],
                'parent': bones[name].parent.name if bones[name].parent else None} for name in native},
            pose=types.SimpleNamespace(bones=NativeCollection(
                {name: ID(name=name, constraints=[], matrix=Matrix()) for name in native})))
        self.scene = ID(name='fresh scene', objects={self.rig.name: self.rig},
            unit_settings=types.SimpleNamespace(scale_length=1.))
        self.context = types.SimpleNamespace(window=object(), scene=self.scene,
            view_layer=types.SimpleNamespace(objects={self.rig.name: self.rig}),
            object=self.rig, mode='OBJECT')
        self.inventory = {'rigs': {}}
        self.model = directory / 'actual_prepared.fbx'
        self.model.write_bytes(b'owned fake model bytes; no FBX importer runs')
        packet_bones = [{'name': name, 'path': name,
            'parent': native.index(bones[name].parent.name) if bones[name].parent else -1} for name in native]
        bindings = []
        for side in ('R', 'L'):
            row = dict(zip(('rootPath', 'midPath', 'tipPath'), self.chains[side]))
            row.update(rigWeight=0., constraintWeight=1., targetPositionWeight=1.,
                targetRotationWeight=1., hintWeight=1., maintainTargetPositionOffset=False,
                maintainTargetRotationOffset=False)
            bindings.append(row)
        self.packet = {'bones': packet_bones, 'rigging': {'bindings': bindings}}
        path = directory / 'actual_prepared.cdanim.json'
        path.write_text(json.dumps(self.packet), encoding='utf-8')
        self.packet.update(_path=str(path), _sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.mapping = types.SimpleNamespace(indices={name: index for index, name in enumerate(native)},
            controls=None, conversion=Matrix(), rest_world={name: bones[name].matrix_local for name in native})
        self.settings = types.SimpleNamespace(armature=ID(name='old setup buffer rig'),
            analysis_json='old analysis', direct_preroll_json='old preroll', pole_direction_armature_token='old token',
            build_method='DIRECT_PREROLL', pole_distance_ratio=1.2, last_level='INFO', last_message='old status',
            right_arm_upper='old R root', right_arm_lower='old R mid', right_arm_end='old R tip',
            left_arm_upper='old L root', left_arm_lower='old L mid', left_arm_end='old L tip',
            right_arm_pole_direction=(1., 2., 3.), left_arm_pole_direction=(4., 5., 6.))
        self.modules = {}
        def module(name, **fields):
            m = types.ModuleType('rigging_setup_test.' + name)
            m.__dict__.update(fields)
            self.modules[m.__name__] = m
            return m
        module('animation_rigging_contract', validate_capture=self.validate_capture)
        module('limb_ik', ARMATURE_ID_KEY='native_arm_owner', ROLL_DECOUPLED_SCHEMA=3,
            WIDGET_COLLECTION_NAME='owned widgets', WIDGET_NAMES={'HAND': 'owned shape'},
            DEFAULT_POLE_DIRECTIONS={('ARM', 'R'): (0., 1., 0.), ('ARM', 'L'): (0., 1., 0.)},
            _validate_inventory=lambda rig: self.inventory, _settings=lambda context: self.settings,
            analyze_armature=self.analyze)
        self.modes = {'R': 'IK', 'L': 'IK'}
        module('limb_ik_fk', switch_limb=self.switch, mode_for_rig=lambda rig, row: self.modes[row['chain'][0][0]])
        module('unity_animation', _rest_state=lambda data: copy.deepcopy(self.rig.rest), _mapping=self.remap)
        module('unity_animation_rigging', _binding_plan=self.binding_plan)
        self.bpy = types.ModuleType('bpy')
        self.bpy.data = types.SimpleNamespace(scenes=[self.old_scene, self.scene],
            collections={}, objects={}, meshes={})
        self.old_scene.objects = {}
        self.bpy.ops = types.SimpleNamespace(character_designer=types.SimpleNamespace(limb_ik_build_arm=Operator(self)))
        self.modules['bpy'] = self.bpy
        package = types.ModuleType('rigging_setup_test')
        package.__path__ = []
        self.modules[package.__name__] = package
        self.sut = types.ModuleType('rigging_setup_test.animation_rigging_setup')
        self.sut.__package__ = package.__name__
        exec(compile(SOURCE.read_bytes(), str(SOURCE), 'exec'), self.sut.__dict__)

    def settings_snapshot(self):
        return {key: value for key, value in vars(self.settings).items()}

    def validate_capture(self, capture, *, packet):
        self.calls.append(('contract', packet is self.packet))
        return copy.deepcopy(capture)

    def analyze(self, rig):
        self.calls.append(('analyze', rig is self.rig))
        return {'version': 1, 'digest': 'authenticated-current-rest', 'limbs': {'guessed': 'deliberately wrong'}}

    def switch(self, context, rig, key, mode, **kwargs):
        self.calls.append(('switch', key, mode, kwargs['keyframe'], tuple(kwargs['desired_pose'])))
        self.modes[key[1]] = mode

    def remap(self, rig, packet, scale):
        self.calls.append(('remap', scale))
        return types.SimpleNamespace(indices=dict(self.mapping.indices), conversion=self.mapping.conversion,
            rest_world=dict(self.mapping.rest_world), controls=object())

    def binding_plan(self, rig, packet, capture, mapping):
        self.calls.append(('native_binding_admission',))
        if self.bad_binding:
            raise ValueError('unsupported separate solver Target')
        return [{'native_side': side, 'chain': list(self.chains[side]), 'target': 'Target.' + side,
            'pole': 'Pole.' + side, 'auto_align': True, 'auto_rotation_space': 'PARENT_DELTA'} for side in ('R', 'L')]

    def run(self, **overrides):
        args = {'existing_ids': {self.old_scene}, 'model_file': str(self.model),
                'model_sha256': hashlib.sha256(self.model.read_bytes()).hexdigest()}
        args.update(overrides)
        with patch.dict(sys.modules, self.modules):
            return self.sut.prepare_fresh_arm_controls(self.context, self.rig, self.packet, self.mapping, **args)


class FreshSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.f = Fixture(Path(self.temp.name))

    def test_exact_packet_chains_override_detector_and_restore_original_buffer(self):
        before = self.f.settings_snapshot()
        result = self.f.run()
        self.assertEqual(result['arm_chains'], {side: list(names) for side, names in self.f.chains.items()})
        self.assertEqual(self.f.public_settings['right_arm_upper'], 'RUpper')
        self.assertEqual(self.f.public_settings['left_arm_end'], 'LTip')
        self.assertEqual(self.f.public_settings['build_method'], 'ROLL_DECOUPLED')
        self.assertEqual(self.f.settings_snapshot(), before)
        self.assertEqual([row[0] for row in self.f.calls],
            ['contract', 'analyze', 'public_build', 'switch', 'switch', 'remap', 'native_binding_admission'])
        self.assertTrue(all(row[3] is False for row in self.f.calls if row[0] == 'switch'))
        self.assertIsNone(self.f.rig.animation_data.action)

    def test_original_rig_is_rejected_before_any_public_setup(self):
        with self.assertRaisesRegex(ValueError, 'new local model Rig'):
            self.f.run(existing_ids={self.f.old_scene, self.f.rig})
        self.assertEqual(self.f.calls, [])

    def test_partial_weight_is_rejected_before_mutating_the_setup_buffer(self):
        before = self.f.settings_snapshot()
        self.f.packet['rigging']['bindings'][0]['hintWeight'] = .5
        with self.assertRaisesRegex(ValueError, 'Partial arm weights'):
            self.f.run()
        self.assertEqual(self.f.settings_snapshot(), before)
        self.assertNotIn('public_build', [row[0] for row in self.f.calls])

    def test_packet_index_swap_is_rejected_before_public_build(self):
        self.f.mapping.indices['RUpper'], self.f.mapping.indices['RMid'] = (
            self.f.mapping.indices['RMid'], self.f.mapping.indices['RUpper'])
        with self.assertRaisesRegex(ValueError, 'native bone index'):
            self.f.run()
        self.assertNotIn('public_build', [row[0] for row in self.f.calls])

    def test_old_global_widget_is_not_reused_or_changed(self):
        shape = ID(name='owned shape', marker='original unchanged')
        self.f.bpy.data.objects[shape.name] = shape
        with self.assertRaisesRegex(ValueError, 'global Body widget'):
            self.f.run()
        self.assertEqual(shape.marker, 'original unchanged')
        self.assertNotIn('public_build', [row[0] for row in self.f.calls])

    def test_cancelled_public_build_preserves_failure_and_restores_setup_buffer(self):
        before = self.f.settings_snapshot()
        self.f.cancel = True
        with self.assertRaisesRegex(ValueError, 'strict native gate rejected'):
            self.f.run()
        self.assertEqual(self.f.settings_snapshot(), before)
        self.assertNotIn('native_binding_admission', [row[0] for row in self.f.calls])

    def test_rest_change_fails_without_repair_or_success_report(self):
        before = self.f.settings_snapshot()
        self.f.change_rest = True
        with self.assertRaisesRegex(ValueError, 'changed original source Rest'):
            self.f.run()
        self.assertEqual(self.f.settings_snapshot(), before)
        self.assertEqual(self.f.rig.rest['RUpper']['head'], [99., 0., 0.])
        self.assertNotIn('native_binding_admission', [row[0] for row in self.f.calls])

    def test_native_binding_rejection_is_propagated_without_a_fallback(self):
        before = self.f.settings_snapshot()
        self.f.bad_binding = True
        with self.assertRaisesRegex(ValueError, 'unsupported separate solver Target'):
            self.f.run()
        self.assertEqual(self.f.settings_snapshot(), before)


if __name__ == '__main__':
    unittest.main()
