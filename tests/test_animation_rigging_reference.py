"""Actual-function reference ownership tests with fake RNA, not Blender QA.

No solver, operator, imported model, or native matrix proof is simulated as a
pass. The mapping boundary is an explicit fake, while file pins, owner guards,
strip transaction, serialization, lifecycle and export isolation use actual AST.
"""
import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


def load_functions(filename, names, namespace):
    tree = ast.parse((SOURCE / filename).read_text(encoding='utf-8'))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in selected} != set(names):
        raise AssertionError('Actual reference API missing: ' + str(set(names) - {n.name for n in selected}))
    for node in selected:
        node.decorator_list = [d for d in node.decorator_list
                               if isinstance(d, ast.Name) and d.id == 'contextmanager']
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE / filename), 'exec'), namespace)


class ID(dict):
    def __init__(self, name, **fields):
        super().__init__()
        self.name, self.library = name, None
        self.__dict__.update(fields)

    def __bool__(self):
        return True

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    __hash__ = object.__hash__

    def __deepcopy__(self, memo):
        return self

    def as_pointer(self):
        return id(self)

    def __setitem__(self, key, value):
        if getattr(self, 'fail_session_once', False) and key == 'session':
            self.fail_session_once = False
            raise RuntimeError('injected persistent reference store failure')
        super().__setitem__(key, value)


class Action(ID):
    def __init__(self, name, slot, motion):
        super().__init__(name, slots=[NS(handle=slot)], motion=motion, use_fake_user=True, foreign_users=0)

    @property
    def users(self):
        return int(self.use_fake_user) + self.foreign_users


class AD(NS):
    @property
    def action_slot_handle(self):
        return self.action_slot.handle if self.action_slot is not None else 0


class Actions(list):
    pass


class Tracks(list):
    def get(self, name):
        return next((row for row in self if row.name == name), None)


class Strip:
    def __init__(self, action, slot, fields):
        self.action, self.action_slot = action, slot
        self.__dict__.update(fields)

    @property
    def action_slot_handle(self):
        return self.action_slot.handle


class World:
    def __init__(self, directory):
        self.directory = directory
        self.model = directory / 'real-model.fbx'
        self.model.write_bytes(b'actual test model bytes, no FBX import')
        self.model_sha = hashlib.sha256(self.model.read_bytes()).hexdigest()
        self.packet_path = directory / 'reference.cdanim.json'
        self.native_names = ['Hips', 'upper.R', 'lower.R', 'hand.R', 'upper.L', 'lower.L', 'hand.L']
        self.matrix = [[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 1., 0.], [0., 0., 0., 1.]]
        self.scene = ID('Scene', render=NS(fps=24, fps_base=1.), frame_start=1, frame_end=49, frame_current=5,
                        frame_subframe=.25, unit_settings=NS(scale_length=1.),
                        tool_settings=NS(use_keyframe_insert_auto=False))
        self.rig = ID('Rig', type='ARMATURE', data=ID('Data', bones={name: None for name in self.native_names + ['CTRL.R', 'CTRL.L']}),
                      matrix_world=self.matrix, pose='live combined pose')
        self.rig['character_designer_animation_source_model_sha256'] = self.model_sha
        self.context = NS(scene=self.scene)
        self.base, self.input, self.ref = Action('Formal WalkN', 4, 'base-keys'), Action('Controls', 7, 'twenty-local-keys'), Action('Readonly WalkRight', 9, 'reference-keys')
        bones = [{'name': 'CoshaRig', 'path': 'CoshaRig', 'parent': -1, 'restSource': 'hierarchy', 'weighted': False}]
        for name in self.native_names:
            bones.append({'name': name, 'path': 'CoshaRig/' + name, 'parent': 0,
                          'restSource': 'bindpose', 'weighted': True})
        self.packet = {'bones': bones, 'sampleRate': 30., 'duration': 4., 'targetGuid': 'b' * 32,
                       'rigging': {'this_data_is_never_imported': True}, 'weaponPreview': {'never_imported': True}}
        self.write_packet()
        self.work_id, self.draft_id = uuid.uuid4().hex, uuid.uuid4().hex
        self.capture = {'bindings': [{'rootPath': 'CoshaRig/upper.R'}, {'rootPath': 'CoshaRig/upper.L'}],
                        'targetGuid': 'a' * 32, 'duration': 2., 'sampleRate': 60., 'previewEnabled': True,
                        'frames': [{'time': value, 'controls': [{'targetPosition': [value, 0., 0.],
                            'targetRotation': [0., 0., 0., 1.], 'hintPosition': [0., value, 0.]}] * 2}
                            for value in (0., 1., 2.)], 'newActionId': self.draft_id}
        self.base['packet_sha'] = 'a' * 64
        self.base['unity_start_frame'] = 1.
        self.input['input_base'] = self.base
        self.input['owner'] = json.dumps({'capture_sha256': self.digest(self.capture),
            'new_action_id': self.draft_id, 'source_packet_sha256': 'a' * 64,
            'work_item_id': self.work_id, 'source_read_only': False})
        self.ref.update(target=self.rig, scene=self.scene, version=2, packet=str(self.packet_path),
            packet_sha=self.packet_sha, native_rate=30., unity_sample_rate=30., unity_duration=4., unity_start_frame=1.)
        self.fields = ('frame_start', 'frame_end', 'action_frame_start', 'action_frame_end', 'scale', 'repeat',
                       'blend_type', 'influence', 'extrapolation', 'mute', 'blend_in', 'blend_out',
                       'use_auto_blend', 'use_animated_influence', 'use_animated_time',
                       'use_animated_time_cyclic', 'use_reverse', 'use_sync_length')
        values = dict(frame_start=1., frame_end=49., action_frame_start=1., action_frame_end=121.,
                      scale=.4, repeat=1., blend_type='REPLACE', influence=1., extrapolation='HOLD',
                      mute=False, blend_in=0., blend_out=0.)
        values.update({key: False for key in self.fields if key.startswith('use_')})
        self.base_track = ID('Owned Base', strips=[Strip(self.base, self.base.slots[0], dict(values))], mute=False, is_solo=False)
        self.input_track = ID('Owned Inputs', strips=[Strip(self.input, self.input.slots[0], dict(values))], mute=False, is_solo=False)
        self.rig.animation_data = AD(nla_tracks=Tracks([self.base_track, self.input_track]),
            action=None, action_slot=None, use_nla=True, use_tweak_mode=False)
        self.rig.animation_data_create = lambda: self.rig.animation_data
        self.session = {'version': 1, 'owner': 'owned', 'rig': self.rig, 'scene': self.scene,
            'data_name': 'Data', 'rest_sha256': self.digest({'rest': 'unchanged'}), 'object_matrix': sum(self.matrix, []),
            'base_action': self.base, 'base_slot': 4, 'input_action': self.input, 'input_slot': 7,
            'input_owner': json.loads(self.input['owner']), 'capture': self.capture,
            'new_action_id': self.draft_id, 'source_packet_sha256': 'a' * 64, 'work_item_id': self.work_id,
            'source_read_only': False, 'enabled': True, 'edit_role': 'BASE', 'tweak_enabled': False,
            'scene_clock': [24, 1.], 'scene_start': 1., 'conversion': sum(self.matrix, []),
            'native_bindings': [{'native_side': 'R'}, {'native_side': 'L'}], 'properties_before': {}, 'tracks': {
                role: {'name': track.name, 'action': action, 'slot': slot, 'fields': deepcopy(values)}
                for role, track, action, slot in (('BASE', self.base_track, self.base, 4), ('RIGGING', self.input_track, self.input, 7))}}
        self.mapping_calls, self.apply_calls = 0, 0
        self.association = {'model_file': str(self.model), 'model_sha256': self.model_sha,
                            'identity': {'sourcePackageSha256': 'a' * 64, 'targetGuid': 'a' * 32}}
        self.links = ModuleType('reference_test.animation_link')
        self.links.get_link = lambda rig: self.association
        self.bpy = NS(types=NS(Action=Action, ID=ID, Armature=type('Armature', (ID,), {})),
                      data=NS(objects=[self.rig], actions=Actions([self.base, self.input, self.ref]), armatures=[self.rig.data]))
        self.timers = set()
        self.bpy.app = NS(timers=NS(is_registered=lambda callback: callback in self.timers,
            register=lambda callback, **_kwargs: self.timers.add(callback),
            unregister=self.timers.remove))
        self.ua = NS(TARGET_KEY='target', SCENE_KEY='scene', VERSION_KEY='version', PACKAGE_KEY='packet', PACKAGE_HASH_KEY='packet_sha',
            NATIVE_TIMEBASE_KEY='native_rate', ORIGINAL_DATA_KEY='original_data', PREVIEW_DATA_KEY='preview_data', ACTIVE_KEY='active',
            _rest_state=lambda data: {'rest': 'unchanged'}, load_package=self.load_packet,
            native_action_frame_rate=self.native_rate, _mapping=self.mapping, _set_frame=self.set_frame,
            active_preview=lambda rig: rig.get('active'), _import_package_action=self.import_reference,
            restore_preview=self.restore_reference, _same_rest_bone=lambda before, after: True,
            _swap_armature_data=self.swap_data, _discard_preview_data=self.discard_data)
        self.composition = NS(_slot=lambda action, slot: next((s for s in action.slots if s.handle == slot), None),
            _plain=lambda value: value, _STRIP_FIELDS=self.fields, _prior=self.prior, _restore_prior=self.restore_prior,
            _track=self.track)
        serialize = {'bpy': self.bpy}
        load_functions('animation_composition.py', {'_encode', '_decode'}, serialize)
        self.composition._encode, self.composition._decode = serialize['_encode'], serialize['_decode']
        self.contract = NS(capture_digest=self.digest, build_export=lambda capture, samples, **kwargs: dict(kwargs, frames=samples))
        self.ns = {'__package__': 'reference_test', 'bpy': self.bpy, 'hashlib': hashlib, 'json': json,
            'math': __import__('math'), 'Path': Path, 'contextmanager': contextmanager, 'deepcopy': deepcopy, 'uuid': uuid,
            'ua': self.ua, 'composition': self.composition, 'contract': self.contract,
            'SESSION_KEY': 'session', '_REFS_KEY': 'refs', 'CAPTURE_KEY': 'capture', '_INPUT_REF': 'input_ref',
            '_PREVIEW_REF_META': 'reference_seal',
            '_INPUT_META': 'owner', '_INPUT_BASE': 'input_base', '_CACHE': {}, '_BUSY': set(), '_SUSPENDED': {},
            '_HANDLER_ERRORS': {}, '_OWNED_TOKENS': {}, '_SIDES': ('Right', 'Left'),
            '_REACQUIRE_PENDING': {}, '_REACQUIRE_TIMER': None, '_REACQUIRE_GENERATION': 0,
            '_FIELDS': (('targetPosition', 3), ('targetRotation', 4), ('hintPosition', 3)),
            '_motion': lambda action, slot: action.motion,
            '_curves': lambda action, slot: {index: None for index in range(20)},
            '_inventory': lambda session: {'rigs': {('ARM', side): {} for side in ('R', 'L')}},
            '_binding_plan': lambda rig, packet, capture, mapping: deepcopy(self.session['native_bindings']),
            '_sample': lambda session, time, **_kwargs: [{'targetPosition': [time, 0., 0.], 'targetRotation': [0., 0., 0., 1.], 'hintPosition': [0., time, 0.]}] * 2,
            '_apply': self.apply, '_restore_live_pose': lambda rig, prior: setattr(rig, 'pose', prior['pose']),
            '_restore_preview_mode': lambda context, session: None, '_restrict_inputs': lambda session: None,
            'limb_ik_fk': NS(mode_for_rig=lambda rig, entry: 'FK'),
            'bone_display': NS(_snapshot=lambda rig: {}, _restore=lambda rig, snapshot: None),
            '_handlers': lambda: None, '_remove_handlers_if_idle': lambda: None}
        names = {'_require', '_digest', '_uid', '_property', '_rows', '_detached', '_store', 'current', '_validate',
            '_tracks', '_reference_file_sha', '_preview_reference_seal', '_validate_preview_reference', '_reference_model_identity', '_reference_admission',
            '_preview_base_timing', '_assign_preview_base', 'set_preview_reference', 'capture_inputs',
            'prepare_preview_reference', '_time', 'base_only', '_reacquire', '_cancel_reacquire',
            '_deferred_reacquire', '_make_tracks', 'activate',
            '_restore_owned_state', '_consume_token'}
        load_functions('unity_animation_rigging.py', names, self.ns)
        self.ns['_store'](self.rig, self.session)
        parent = ModuleType('reference_test')
        parent.__path__ = []
        parent.animation_link = self.links
        with patch.dict(sys.modules, {'reference_test': parent, 'reference_test.animation_link': self.links}):
            self.ns['_reference_admission'](self.context, self.session, self.ref, 9,
                str(self.packet_path), self.packet_sha, str(self.model), self.model_sha, _creating=True)
        self.mapping_calls = 0

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def write_packet(self):
        self.packet_path.write_text(json.dumps(self.packet), encoding='utf-8')
        self.packet_sha = hashlib.sha256(self.packet_path.read_bytes()).hexdigest()

    def load_packet(self, path):
        packet = json.loads(Path(path).read_text(encoding='utf-8'))
        packet.update(_path=str(Path(path).resolve()), _sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())
        return packet

    def native_rate(self, action):
        if action is self.base:
            return 60.
        packet = self.load_packet(action['packet'])
        if packet['_sha256'] != action['packet_sha'] or packet['duration'] != action['unity_duration'] or packet['sampleRate'] != action['native_rate']:
            raise ValueError('actual packet clock changed')
        return action['native_rate']

    def mapping(self, rig, packet, scale):
        self.mapping_calls += 1
        if getattr(self, 'mapping_failure', False):
            raise ValueError('actual Rest admission rejected')
        return NS(indices={name: index + 1 for index, name in enumerate(self.native_names)},
                  rest_world={name: self.matrix for name in self.native_names}, conversion=self.matrix,
                  controls=NS(control_names={'CTRL.R', 'CTRL.L'}))

    def set_frame(self, scene, frame):
        scene.frame_current, scene.frame_subframe = int(frame), frame - int(frame)

    def apply(self, context, session):
        self.apply_calls += 1
        self.rig.pose = 'new reference evaluated pose'
        if getattr(self, 'apply_failure', False):
            raise ValueError('injected preview evaluation failure')

    def track(self, ad, name, action, slot, start, end, **kwargs):
        fields = deepcopy(self.session['tracks']['RIGGING']['fields'])
        fields.update(action_frame_start=start, action_frame_end=end,
                      scale=1. / kwargs['speed'], frame_start=kwargs['scene_start'])
        fields['frame_end'] = fields['frame_start'] + (end - start) * fields['scale']
        track = ID(name, strips=[Strip(action, action.slots[0], fields)], mute=False, is_solo=False)
        ad.nla_tracks.append(track)
        return track

    def import_reference(self, context, rig, packet, *, start_frame, native_timebase):
        if native_timebase is not True or start_frame != 1.:
            raise AssertionError('native import clock was not requested')
        action = Action('Imported Reference', 11, 'reference-keys')
        action.update(self.ref)
        action.pop('reference_seal', None)
        if getattr(self, 'foreign_target', False):
            action['target'] = ID('Foreign Target')
        self.bpy.data.actions.append(action)
        rig.animation_data.action, rig.animation_data.action_slot = action, action.slots[0]
        rig['active'] = action
        if getattr(self, 'restore_failure', False):
            self.original_data = rig.data
            data = ID('Temporary Reference', bones=rig.data.bones, users=0, use_fake_user=False)
            self.bpy.data.armatures.append(data)
            action['original_data'], action['preview_data'] = rig.data, data
            rig.data = data
        return NS(action=action)

    def restore_reference(self, context, rig):
        if getattr(self, 'restore_failure', False):
            raise ValueError('injected public restore failure')
        action = rig.pop('active')
        rig.animation_data.action, rig.animation_data.action_slot = self.base, self.base.slots[0]
        if getattr(self, 'final_tamper', False):
            self.packet_path.write_text('{}')
        return self.base

    def swap_data(self, context, rig, data):
        rig.data = data

    def discard_data(self, action, data):
        action.pop('preview_data', None)
        if data.users == 0:
            self.bpy.data.armatures.remove(data)

    def prior(self, context, rig):
        ad = rig.animation_data
        return {'pose': rig.pose, 'snapshot': {'frame': context.scene.frame_current + context.scene.frame_subframe},
            'action': ad.action, 'slot': ad.action_slot, 'use_nla': ad.use_nla,
            'mutes': [(track, track.mute) for track in ad.nla_tracks]}

    def restore_prior(self, context, rig, prior):
        ad = rig.animation_data
        ad.action, ad.action_slot, ad.use_nla = prior['action'], prior['slot'], prior['use_nla']
        for track, mute in prior['mutes']:
            track.mute = mute
        self.set_frame(context.scene, prior['snapshot']['frame'])

    def set_reference(self, session=None):
        parent = ModuleType('reference_test')
        parent.__path__ = []
        parent.animation_link = self.links
        with patch.dict(sys.modules, {'reference_test': parent, 'reference_test.animation_link': self.links}):
            return self.ns['set_preview_reference'](self.context, session or self.session, self.ref, 9,
                reference_packet_file=str(self.packet_path), reference_packet_sha256=self.packet_sha,
                model_file=str(self.model), model_sha256=self.model_sha)

    def prepare(self):
        parent = ModuleType('reference_test')
        parent.__path__ = []
        parent.animation_link = self.links
        with patch.dict(sys.modules, {'reference_test': parent, 'reference_test.animation_link': self.links}):
            return self.ns['prepare_preview_reference'](self.context, self.session,
                str(self.packet_path), self.packet_sha, model_file=str(self.model), model_sha256=self.model_sha)

    def dto(self, session=None):
        return self.ns['capture_inputs'](self.context, session or self.session,
            source_packet_sha256='a' * 64, work_item_id=self.work_id, source_action=self.base, source_action_slot=4)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.world = World(Path(self.temp.name))

    def test_reference_changes_only_preview_track_and_preserves_twenty_input_export(self):
        w = self.world
        before, owner = w.dto(), deepcopy(w.session['input_owner'])
        w.set_reference()
        self.assertIs(w.base_track.strips[0].action, w.ref)
        self.assertIs(w.input_track.strips[0].action, w.input)
        self.assertIs(w.session['base_action'], w.base)
        self.assertIs(w.input['input_base'], w.base)
        self.assertEqual(w.dto(), before)
        self.assertEqual(w.session['input_owner'], owner)
        self.assertEqual((w.mapping_calls, w.apply_calls), (1, 1))

    def test_normalized_clock_uses_full_reference_duration_and_inclusive_end(self):
        w = self.world
        w.set_reference()
        strip = w.base_track.strips[0]
        self.assertEqual((strip.action_frame_start, strip.action_frame_end), (1., 121.))
        self.assertEqual((strip.frame_start, strip.frame_end, strip.scale), (1., 49., .4))
        for phase in (0., .5, 1.):
            source = strip.action_frame_start + (strip.frame_start + phase * 48. - strip.frame_start) / strip.scale
            self.assertEqual(source, 1. + phase * 120.)
        w.set_frame(w.scene, 49.)
        self.assertEqual(w.ns['_time'](w.context, w.session), 2.)

    def test_restore_original_keeps_identity_and_controls_exact(self):
        w = self.world
        before = w.dto()
        w.set_reference()
        w.ns['set_preview_reference'](w.context, w.session)
        self.assertNotIn('preview_reference', w.session)
        self.assertIs(w.base_track.strips[0].action, w.base)
        self.assertEqual(w.dto(), before)

    def test_real_mapping_rejection_has_no_strip_or_carrier_side_effect(self):
        w = self.world
        w.mapping_failure = True
        with self.assertRaisesRegex(ValueError, 'Rest admission'):
            w.set_reference()
        self.assertIs(w.base_track.strips[0].action, w.base)
        self.assertNotIn('preview_reference', w.session)
        self.assertEqual(w.apply_calls, 0)

    def test_packet_model_and_action_ownership_are_required(self):
        for bad in ('file', 'model_owner', 'rig_pointer', 'slot', 'carrier'):
            with self.subTest(bad=bad):
                w = World(Path(self.temp.name))
                if bad == 'file':
                    w.packet_path.write_text('{}')
                elif bad == 'model_owner':
                    w.association['model_sha256'] = 'f' * 64
                elif bad == 'rig_pointer':
                    w.ref['target'] = ID('Foreign Rig')
                elif bad == 'slot':
                    w.ref.slots[0].handle = 10
                else:
                    w.ref['input_base'] = w.base
                with self.assertRaises((ValueError, KeyError)):
                    w.set_reference()
                self.assertIs(w.base_track.strips[0].action, w.base)

    def test_actual_weighted_root_contract_is_required(self):
        w = self.world
        self.assertIs(w.packet['bones'][0]['weighted'], False)
        self.assertNotIn('skinWeighted', w.packet['bones'][0])
        w.set_reference()
        for bad in (True, None):
            with self.subTest(weighted=bad):
                w = World(Path(self.temp.name))
                if bad is None:
                    w.packet['bones'][0].pop('weighted')
                    w.packet['bones'][0]['skinWeighted'] = False
                else:
                    w.packet['bones'][0]['weighted'] = bad
                w.write_packet()
                w.ref['packet_sha'] = w.packet_sha
                with self.assertRaisesRegex(ValueError, 'explicit source root'):
                    w.set_reference()

    def test_reference_is_readonly_and_base_tweak_is_rejected(self):
        w = self.world
        w.set_reference()
        with self.assertRaisesRegex(ValueError, 'read-only'):
            w.ns['activate'](w.context, w.session, edit_role='BASE', enabled=True)
        w.ref.motion = 'edited reference keys'
        with self.assertRaisesRegex(ValueError, 'curves changed'):
            w.ns['current'](w.rig)
        w.ref.motion = 'reference-keys'
        w.session['tweak_enabled'] = True
        with self.assertRaisesRegex(ValueError, 'read-only BASE'):
            w.ns['_validate_preview_reference'](w.session)

    def test_reference_cannot_be_edited_between_public_prepare_and_first_set(self):
        w = self.world
        w.ref.motion = 'edited before first switch'
        with self.assertRaisesRegex(ValueError, 'original public-import'):
            w.set_reference()
        self.assertIs(w.base_track.strips[0].action, w.base)

    def test_solver_failure_restores_same_strip_owner_pose_and_dto(self):
        w = self.world
        dto, before = w.dto(), deepcopy(w.session['tracks']['BASE']['fields'])
        w.apply_failure = True
        with self.assertRaisesRegex(ValueError, 'evaluation failure'):
            w.set_reference()
        self.assertIs(w.base_track.strips[0].action, w.base)
        self.assertEqual(w.session['tracks']['BASE']['fields'], before)
        self.assertEqual(w.rig.pose, 'live combined pose')
        self.assertEqual(w.dto(), dto)
        self.assertNotIn(w.rig.as_pointer(), w.ns['_BUSY'])

    def test_store_failure_restores_persisted_original_owner(self):
        w = self.world
        dto = w.dto()
        w.rig.fail_session_once = True
        with self.assertRaisesRegex(RuntimeError, 'store failure'):
            w.set_reference()
        current = w.ns['current'](w.rig)
        self.assertNotIn('preview_reference', current)
        self.assertIs(w.base_track.strips[0].action, w.base)
        self.assertEqual(w.dto(current), dto)

    def test_base_only_still_binds_original_while_reference_preview_recovers(self):
        w = self.world
        w.set_reference()
        dto = w.dto()
        with w.ns['base_only'](w.context, w.rig, action=w.base):
            self.assertIs(w.rig.animation_data.action, w.base)
            self.assertEqual(w.rig.animation_data.action_slot.handle, 4)
            self.assertFalse(w.rig.animation_data.use_nla)
            self.assertTrue(w.base_track.mute and w.input_track.mute)
            with self.assertRaisesRegex(ValueError, 'exact bound base'):
                with w.ns['base_only'](w.context, w.rig, action=w.ref):
                    pass
        self.assertIs(w.base_track.strips[0].action, w.ref)
        self.assertIsNone(w.rig.animation_data.action)
        self.assertFalse(w.base_track.mute or w.input_track.mute)
        self.assertEqual(w.dto(), dto)

    def test_load_undo_reacquire_uses_persistent_native_ids_without_solver(self):
        w = self.world
        w.set_reference()
        w.ns['_CACHE'].clear()
        before = w.apply_calls
        w.ns['_reacquire']()
        self.assertFalse(w.ns['_HANDLER_ERRORS'])
        current = w.ns['current'](w.rig)
        self.assertIs(current['preview_reference']['action'], w.ref)
        self.assertIs(current['base_action'], w.base)
        self.assertEqual(w.apply_calls, before)
        self.assertEqual(w.dto(current), w.dto())

    def test_saved_changed_reference_packet_fails_lifecycle_without_repair(self):
        w = self.world
        w.set_reference()
        w.packet_path.write_text('{}')
        w.ns['_reacquire']()
        self.assertIn(w.rig.as_pointer(), w.ns['_HANDLER_ERRORS'])
        self.assertIs(w.base_track.strips[0].action, w.ref)
        self.assertIn('preview_reference', w.ns['current'](w.rig))

    def test_public_prepare_restores_original_stack_and_does_not_select_reference(self):
        w = self.world
        dto, count = w.dto(), len(w.bpy.data.actions)
        action, slot = w.prepare()
        self.assertEqual((action.name, slot), ('Imported Reference', 11))
        self.assertEqual(len(w.bpy.data.actions), count + 1)
        self.assertNotIn('preview_reference', w.session)
        self.assertIs(w.base_track.strips[0].action, w.base)
        self.assertIsNone(w.rig.animation_data.action)
        self.assertIsNone(w.ua.active_preview(w.rig))
        self.assertEqual(w.dto(), dto)

    def test_public_prepare_restore_failure_cleans_only_created_action_and_data(self):
        w = self.world
        dto, actions, original = w.dto(), list(w.bpy.data.actions), w.rig.data
        w.restore_failure = True
        with self.assertRaisesRegex(ValueError, 'public restore failure'):
            w.prepare()
        self.assertEqual(w.bpy.data.actions, actions)
        self.assertEqual(w.bpy.data.armatures, [original])
        self.assertIs(w.rig.data, original)
        self.assertIsNone(w.ua.active_preview(w.rig))
        self.assertIsNone(w.rig.animation_data.action)
        self.assertEqual(w.dto(), dto)

    def test_public_prepare_final_file_tamper_retains_original_owner_and_stack(self):
        w = self.world
        dto, actions = w.dto(), list(w.bpy.data.actions)
        w.final_tamper = True
        with self.assertRaises((ValueError, KeyError)):
            w.prepare()
        self.assertEqual(w.bpy.data.actions, actions)
        self.assertIs(w.base_track.strips[0].action, w.base)
        self.assertFalse(w.base_track.mute or w.input_track.mute)
        self.assertEqual(w.dto(), dto)

    def test_public_prepare_foreign_target_is_not_accepted(self):
        w = self.world
        actions = list(w.bpy.data.actions)
        w.foreign_target = True
        with self.assertRaisesRegex(ValueError, 'foreign Action'):
            w.prepare()
        self.assertEqual(w.bpy.data.actions, actions)
        self.assertIsNone(w.rig.animation_data.action)
        self.assertIsNone(w.ua.active_preview(w.rig))

    def test_transaction_restore_recreates_base_track_from_persisted_reference(self):
        w = self.world
        w.set_reference()
        token = {'session': w.ns['_detached'](w.session), 'status': 'pending',
                 'input_motion': w.input.motion, 'source_packet_sha256': 'a' * 64,
                 'live_prior': w.prior(w.context, w.rig), 'live': {}}
        w.rig.pop('session')
        w.rig.pop('refs')
        w.ns['_CACHE'].clear()
        w.rig.animation_data.nla_tracks.clear()
        w.ns['_restore_owned_state'](w.context, w.rig, token)
        current = w.ns['current'](w.rig)
        base = w.rig.animation_data.nla_tracks.get(current['tracks']['BASE']['name'])
        self.assertIs(base.strips[0].action, w.ref)
        self.assertIs(current['base_action'], w.base)
        self.assertEqual(current['input_slot'], 7)
        self.assertEqual(token['status'], 'restored')

    def test_transaction_restore_uses_current_start_for_reference_and_input_tracks(self):
        w = self.world
        w.set_reference()
        token = {'session': w.ns['_detached'](w.session), 'status': 'pending',
                 'input_motion': w.input.motion, 'source_packet_sha256': 'a' * 64,
                 'live_prior': w.prior(w.context, w.rig), 'live': {}}
        w.rig.pop('session')
        w.rig.pop('refs')
        w.ns['_CACHE'].clear()
        w.rig.animation_data.nla_tracks.clear()
        w.scene.frame_start = 17
        w.ns['_restore_owned_state'](w.context, w.rig, token)
        current = w.ns['current'](w.rig)
        self.assertEqual(current['scene_start'], 17)
        for role in ('BASE', 'RIGGING'):
            strip = w.rig.animation_data.nla_tracks.get(current['tracks'][role]['name']).strips[0]
            self.assertEqual((strip.frame_start, strip.frame_end), (17., 65.))
        self.assertIs(current['tracks']['BASE']['action'], w.ref)

    def test_lifecycle_decodes_replaced_native_reference_id_without_old_cache(self):
        w = self.world
        w.set_reference()
        fresh = Action(w.ref.name, 9, w.ref.motion)
        fresh.update(w.ref)
        for key, value in list(w.rig['refs'].items()):
            if value is w.ref:
                w.rig['refs'][key] = fresh
        w.base_track.strips[0].action, w.base_track.strips[0].action_slot = fresh, fresh.slots[0]
        w.ns['_CACHE'].clear()
        w.ns['_reacquire']()
        self.assertFalse(w.ns['_HANDLER_ERRORS'])
        self.assertIs(w.ns['current'](w.rig)['preview_reference']['action'], fresh)


if __name__ == '__main__':
    unittest.main()
