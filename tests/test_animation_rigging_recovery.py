"""Pure fault injection for actual Rigging display transactions, not native QA.

Only recovery functions are AST-loaded. Fake native IDs model ownership and
links; no Blender operators, solver, frame evaluation or external process runs.
"""
import ast
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


def functions(path, names, namespace):
    nodes = [node for node in ast.parse(path.read_text(encoding='utf-8')).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('The recovery API changed: ' + str(names))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


class Group(dict):
    def to_dict(self):
        return dict(self)


def property_value(value):
    if isinstance(value, dict) and not isinstance(value, Native):
        return Group({key: property_value(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return [property_value(item) for item in value]
    return value


class Native(dict):
    """An ID reference is identity-based, even when its properties are empty."""
    def __init__(self, name, **fields):
        super().__init__()
        self.name, self.removed, self.library = name, False, None
        self.__dict__.update(fields)

    def __setitem__(self, key, value):
        fault = getattr(self, 'write_fault', None)
        if fault is not None and fault(key, value):
            self.write_fault = None
            raise RuntimeError('injected second session store failure')
        super().__setitem__(key, property_value(value))

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    __hash__ = object.__hash__

    def __bool__(self):
        return True

    def __deepcopy__(self, _memo):
        return self

    def as_pointer(self):
        return id(self)


class Armature(Native):
    pass


class Action(Native):
    @property
    def users(self):
        return int(self.use_fake_user)


class Links:
    def __init__(self, root):
        self.root, self.members, self.fail_unlink = root, [], False

    def __contains__(self, value):
        return value in self.members

    def __iter__(self):
        return iter(self.members)

    def get(self, name):
        return next((value for value in self.members if value.name == name), None)

    def link(self, value):
        if value not in self.members:
            self.members.append(value)
            value.parents.add(self.root)

    def unlink(self, value):
        if self.fail_unlink:
            self.fail_unlink = False
            raise RuntimeError('injected Scene unlink failure')
        self.members.remove(value)
        value.parents.remove(self.root)


class Collection(Native):
    def __init__(self, name, world):
        super().__init__(name, objects=[], parents=set())
        self.world, self.children = world, Links(self)

    @property
    def users(self):
        record = self.world.rig.get(self.world.weapon.RECORD)
        return len(self.parents) + int(record is not None and record.get('collection') is self)


class Table(list):
    def get(self, name):
        return next((value for value in self if value.name == name), None)

    def remove(self, value, **_kwargs):
        value.removed = True
        super().remove(value)
        if isinstance(value, Collection):
            for root in tuple(value.parents):
                root.children.unlink(value)
        else:
            for collection in tuple(getattr(self, 'collections', ())):
                if value in collection.objects:
                    collection.objects.remove(value)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        package_name = '_rigging_recovery_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = []
        self.weapon = ModuleType(package_name + '.animation_composition_weapon')
        self.weapon.RECORD, self.weapon.SCHEMA = 'owned_weapon', 'cdesigner.weapon-context/1'
        self.weapon.OWNER = 'weapon_owner'
        self.rig = Native('Original Rig', type='ARMATURE', data=Armature('Original bones'),
                          matrix_world=[1.], pose_state={'target': 'current unkeyed drag'})
        self.root = Collection('Scene root', self)
        self.scene = Native('Artist Scene', collection=self.root,
                            unit_settings=SimpleNamespace(scale_length=1.))
        self.base = Action('Base Custom', use_fake_user=True)
        self.inputs = Action('Target inputs', use_fake_user=True)
        self.context = SimpleNamespace(scene=self.scene)
        self.rig.animation_data = SimpleNamespace(use_tweak_mode=False, use_nla=False,
            action=self.base, action_slot_handle=4, nla_tracks=Table())
        objects, collections = Table([self.rig]), Table()
        objects.collections = collections
        data = SimpleNamespace(objects=objects, collections=collections,
            actions=Table([self.base, self.inputs]), scenes=Table([self.scene]))
        data.user_map = self.user_map
        self.bpy = SimpleNamespace(types=SimpleNamespace(ID=Native, Object=Native,
            Collection=Collection, Scene=Native, Action=Action, Armature=Armature), data=data)
        self.modules = patch.dict(sys.modules, {package_name: package,
            self.weapon.__name__: self.weapon, 'bpy': self.bpy})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.composition = SimpleNamespace()
        serializer = {'bpy': self.bpy}
        functions(SOURCE / 'animation_composition.py', {'_encode', '_decode'}, serializer)
        self.composition._encode, self.composition._decode = serializer['_encode'], serializer['_decode']
        copy_namespace = {}
        functions(SOURCE / 'bone_collections.py', {'_copy_property'}, copy_namespace)
        self.copy_property = copy_namespace['_copy_property']
        self.composition._prior = lambda _context, rig: {'pose': deepcopy(rig.pose_state)}
        self.composition._restore_prior = lambda _context, rig, prior: setattr(rig, 'pose_state', deepcopy(prior['pose']))
        self.composition._slot = lambda _action, slot: SimpleNamespace(handle=slot)
        self.composition.active = lambda _rig: False
        self.composition._tweak = lambda *_args, **_kwargs: None
        self.display = {'groups': ['Original', 'Body'], 'color': 'original'}
        bone_display = SimpleNamespace(_snapshot=lambda _rig: deepcopy(self.display),
                                      _restore=lambda _rig, saved: self.display.update(saved))
        names = {'_require', '_digest', '_uid', '_detached', '_store', 'current',
            'restore_owned', '_capture_owned', '_weapon_token', '_detach_weapon',
            '_return_weapon', '_consume_token', '_finish_owned_state',
            '_restore_owned_state', 'snapshot_base_refs', 'import_inputs'}
        self.n = {'__package__': package_name, 'bpy': self.bpy, 'json': json,
            'uuid': uuid, 'hashlib': hashlib, 'deepcopy': deepcopy,
            'contextmanager': contextmanager, 'composition': self.composition,
            'bone_display': bone_display,
            'bone_collections': SimpleNamespace(_copy_property=self.copy_property),
            'SESSION_KEY': 'owned_session', '_REFS_KEY': 'owned_refs',
            'CAPTURE_KEY': 'original_capture', '_INPUT_REF': 'input_ref',
            '_INPUT_META': 'input_owner', '_INPUT_BASE': 'input_base',
            '_CACHE': {}, '_BUSY': set(), '_SUSPENDED': {}, '_HANDLER_ERRORS': {},
            '_OWNED_TOKENS': {}, '_SIDES': ('Right', 'Left'),
            '_FIELDS': (('targetPosition', 3), ('targetRotation', 4), ('hintPosition', 3)),
            '_property': lambda side, field: side + '_' + field,
            '_validate': lambda _session: None, '_motion': lambda _action, _slot: 'f' * 64,
            '_tracks': lambda _session: {}, '_remove_handlers_if_idle': lambda: None,
            '_restore_live_pose': lambda rig, prior: setattr(rig, 'pose_state', deepcopy(prior['pose'])),
            '_rows': lambda matrix: list(matrix), '_binding_plan': lambda *_args: [],
            '_curves': lambda *_args: None}
        functions(SOURCE / 'unity_animation_rigging.py', names, self.n)
        self.n['activate'] = self.activate
        self.weapon.__dict__.update(bpy=self.bpy, _require=self.n['_require'],
            re=__import__('re'), _ASSET_TABLES=(), _remove_unused_assets=lambda _assets: None,
            _host=lambda _name: SimpleNamespace(current=self.n['current']))
        functions(SOURCE / 'animation_composition_weapon.py',
            {'validate_owned', 'validate_removal', 'remove_context', 'remove_rigging_context'}, self.weapon.__dict__)
        self.capture = {'newActionId': '2' * 32, 'previewEnabled': True,
                        'sampleRate': 2., 'weaponPreview': {'display': 'fixture'}}
        self.session = dict(version=1, owner=str(uuid.uuid4()), rig=self.rig, scene=self.scene,
            capture=self.capture, base_action=self.base, base_slot=4, input_action=self.inputs,
            input_slot=7, new_action_id='2' * 32, work_item_id='1' * 32,
            source_packet_sha256='a' * 64, source_read_only=False,
            prior={'pose': {'target': 'entry pose'}}, display=deepcopy(self.display),
            tracks={}, properties_before={}, enabled=True, edit_role='RIGGING',
            tweak_enabled=False, committed=True, _time=.5, _left_parent=[1.])
        self.old_record = self.install_display(self.session, 'Old display')
        self.n['_store'](self.rig, self.session)

    def user_map(self):
        users = {}
        record = self.rig.get(self.weapon.RECORD)
        if record is not None:
            users[record['collection']] = {self.rig, self.root}
            for obj in record['objects'].values():
                users[obj] = {self.rig, record['collection']}
        if getattr(self, 'external_user', False) and record is not None:
            users[next(iter(record['objects'].values()))].add(self.base)
        return users

    def install_display(self, session, name):
        owner = str(uuid.uuid4())
        collection = Collection(name, self)
        obj = Native(name + ' mesh', users_collection={collection})
        obj[self.weapon.OWNER] = collection[self.weapon.OWNER] = owner
        collection.objects.append(obj)
        self.bpy.data.objects.append(obj)
        self.bpy.data.collections.append(collection)
        self.root.children.link(collection)
        self.rig[self.weapon.RECORD] = {'schema': self.weapon.SCHEMA, 'owner': owner,
            'collection': collection, 'scene': self.scene, 'scene_root': self.root,
            'objects': {'mesh': obj}, 'assets': {}, 'identity': name,
            'rigging_session_owner': session['owner']}
        session['weapon_preview'] = {'display_owner': owner, 'identity': name,
            'record_key': self.weapon.RECORD, 'rigging_session_owner': session['owner']}
        return self.copy_property(self.rig[self.weapon.RECORD])

    def activate(self, _context, session, *, edit_role, enabled):
        # Solver/display formulas are outside this test. Restore state ordering
        # is exercised by the actual transaction around this boundary.
        session.update(enabled=True, edit_role=edit_role, tweak_enabled=enabled)
        self.rig.pose_state = {'target': 'activation evaluation'}
        self.n['_store'](self.rig, session)

    def detach(self):
        token = self.n['_capture_owned'](self.context, self.rig)
        self.n['restore_owned'](self.context, self.rig)
        return token

    def assert_record_ids(self, expected):
        actual = self.rig[self.weapon.RECORD]
        self.assertIs(actual['collection'], expected['collection'])
        self.assertIs(actual['scene'], expected['scene'])
        self.assertIs(actual['objects']['mesh'], expected['objects']['mesh'])
        self.assertEqual(self.copy_property(actual), expected)

    def test_failed_worklist_rollback_restores_same_ids_session_and_live_pose(self):
        original_pose = deepcopy(self.rig.pose_state)
        token = self.detach()
        self.assertEqual(token['status'], 'detached')
        self.assertNotIn(self.weapon.RECORD, self.rig)
        self.assertEqual(self.old_record['collection'].users, 0)
        self.assertFalse(self.old_record['objects']['mesh'].removed)
        self.n['_restore_owned_state'](self.context, self.rig, token)
        self.assert_record_ids(self.old_record)
        recovered = self.n['current'](self.rig)
        self.assertIs(recovered['base_action'], self.base)
        self.assertIs(recovered['input_action'], self.inputs)
        self.assertEqual((recovered['base_slot'], recovered['input_slot']), (4, 7))
        self.assertEqual((recovered['enabled'], recovered['edit_role'], recovered['tweak_enabled']),
                         (True, 'RIGGING', False))
        self.assertEqual(self.rig.pose_state, original_pose)
        self.assertEqual(recovered['_time'], .5)
        self.assertEqual(token['status'], 'restored')
        self.assertEqual(self.n['_OWNED_TOKENS'], {})

    def test_normal_close_foreign_weapon_user_rejects_before_tweak_or_state_changes(self):
        self.external_user = True
        self.session['tweak_enabled'] = True
        self.session['tracks'] = {'RIGGING': 'track'}
        self.session['original_inputs'] = {'fixture': 'Original20 preflight precedes Forearm scope'}
        self.n['_store'](self.rig, self.session)
        self.rig.animation_data.use_tweak_mode = True
        self.n['_tracks'] = lambda _session: {'RIGGING': 'track'}
        calls = []
        self.composition._tweak = lambda *_args, **_kwargs: calls.append('tweak')
        self.composition._restore_prior = lambda *_args: calls.append('restore prior')
        self.n['bone_display']._restore = lambda *_args: calls.append('restore display')
        # Capture individual ID properties; an ID itself remains an identity reference.
        properties = {key: self.copy_property(value) for key, value in self.rig.items()}
        with self.assertRaisesRegex(ValueError, 'external object'):
            self.n['restore_owned'](self.context, self.rig)
        self.assertEqual(calls, [])
        self.assertTrue(self.rig.animation_data.use_tweak_mode)
        self.assertEqual({key: self.copy_property(value) for key, value in self.rig.items()}, properties)
        self.assert_record_ids(self.old_record)
        self.assertFalse(self.old_record['objects']['mesh'].removed or self.old_record['collection'].removed)
        self.assertEqual(self.n['_BUSY'], set())
        self.assertEqual(self.n['_SUSPENDED'], {})

    def test_normal_close_collection_guard_also_precedes_tweak_exit(self):
        self.session['tweak_enabled'] = True
        self.session['tracks'] = {'RIGGING': 'track'}
        self.n['_store'](self.rig, self.session)
        self.rig.animation_data.use_tweak_mode = True
        self.n['_tracks'] = lambda _session: {'RIGGING': 'track'}
        original_users = self.bpy.data.user_map
        def external_collection_user():
            result = original_users()
            result[self.old_record['collection']].add(self.base)
            return result
        self.bpy.data.user_map = external_collection_user
        calls = []
        self.composition._tweak = lambda *_args, **_kwargs: calls.append('tweak')
        with self.assertRaisesRegex(ValueError, 'collection gained an external user'):
            self.n['restore_owned'](self.context, self.rig)
        self.assertEqual(calls, [])
        self.assertTrue(self.rig.animation_data.use_tweak_mode)
        self.assert_record_ids(self.old_record)

    def scene_base_users(self, *, extra_references=0):
        obj = self.old_record['objects']['mesh']
        self.scene.view_layers = [SimpleNamespace(objects=Table([obj]))]
        self.scene.user_of_id = lambda _obj: 1 + extra_references
        original_users = self.bpy.data.user_map
        def with_scene():
            result = original_users()
            if obj in result: result[obj].add(self.scene)
            return result
        self.bpy.data.user_map = with_scene

    def test_scene_base_only_normal_close_retains_drafts_and_restores_pose(self):
        self.scene_base_users()
        self.n['restore_owned'](self.context, self.rig)
        self.assertNotIn(self.weapon.RECORD, self.rig)
        self.assertNotIn(self.n['SESSION_KEY'], self.rig)
        self.assertTrue(self.old_record['objects']['mesh'].removed and self.old_record['collection'].removed)
        self.assertFalse(self.rig.removed or self.base.removed or self.inputs.removed)
        self.assertEqual(self.rig.pose_state, self.session['prior']['pose'])

    def test_extra_scene_reference_rejects_close_before_tweak_exit(self):
        self.scene_base_users(extra_references=1)
        self.session['tweak_enabled'] = True
        self.session['tracks'] = {'RIGGING': 'track'}
        self.n['_store'](self.rig, self.session)
        self.rig.animation_data.use_tweak_mode = True
        self.n['_tracks'] = lambda _session: {'RIGGING': 'track'}
        calls = []
        self.composition._tweak = lambda *_args, **_kwargs: calls.append('tweak')
        with self.assertRaisesRegex(ValueError, 'owning Scene has an extra weapon reference'):
            self.n['restore_owned'](self.context, self.rig)
        self.assertEqual(calls, [])
        self.assertTrue(self.rig.animation_data.use_tweak_mode)
        self.assert_record_ids(self.old_record)

    def test_pending_owner_token_keeps_existing_detach_path(self):
        self.external_user = True
        token = self.n['_capture_owned'](self.context, self.rig)
        self.n['restore_owned'](self.context, self.rig)
        self.assertEqual(token['status'], 'detached')
        self.assertNotIn(self.weapon.RECORD, self.rig)
        self.assertFalse(self.old_record['objects']['mesh'].removed or self.old_record['collection'].removed)

    def test_another_owner_pending_token_does_not_bypass_preflight(self):
        self.external_user = True
        token = self.n['_capture_owned'](self.context, self.rig)
        token['session']['owner'] = str(uuid.uuid4())
        with self.assertRaisesRegex(ValueError, 'external object'):
            self.n['restore_owned'](self.context, self.rig)
        self.assertEqual(token['status'], 'pending')
        self.assert_record_ids(self.old_record)
        self.assertFalse(self.old_record['objects']['mesh'].removed or self.old_record['collection'].removed)

    def test_commit_releases_only_old_geometry_and_keeps_new_native_ids(self):
        token = self.detach()
        new_session = dict(self.session, owner=str(uuid.uuid4()))
        new_record = self.install_display(new_session, 'New display')
        self.n['_store'](self.rig, new_session)
        self.n['_finish_owned_state'](self.context, self.rig, token)
        self.assertTrue(self.old_record['collection'].removed)
        self.assertTrue(self.old_record['objects']['mesh'].removed)
        self.assertFalse(new_record['collection'].removed)
        self.assertFalse(new_record['objects']['mesh'].removed)
        self.assert_record_ids(new_record)
        self.assertIs(self.n['current'](self.rig)['rig'], self.rig)
        self.assertFalse(self.rig.removed or self.base.removed or self.inputs.removed)
        self.assertEqual(token['status'], 'finished')
        self.assertEqual(self.n['_OWNED_TOKENS'], {})

    def test_detach_link_failure_restores_original_record_and_pending_token(self):
        token = self.n['_capture_owned'](self.context, self.rig)
        self.root.children.fail_unlink = True
        with self.assertRaisesRegex(RuntimeError, 'unlink failure'):
            self.n['restore_owned'](self.context, self.rig)
        self.assert_record_ids(self.old_record)
        self.assertIs(self.root.children.get(self.old_record['collection'].name), self.old_record['collection'])
        self.assertEqual(token['status'], 'pending')
        self.assertFalse(self.old_record['objects']['mesh'].removed)

    def test_failed_commit_guard_retains_old_and_restores_new_record(self):
        token = self.detach()
        new_session = dict(self.session, owner=str(uuid.uuid4()))
        new_record = self.install_display(new_session, 'New display')
        self.external_user = True
        with self.assertRaisesRegex(ValueError, 'external object'):
            self.n['_finish_owned_state'](self.context, self.rig, token)
        self.assert_record_ids(new_record)
        self.assertEqual(token['status'], 'detached')
        self.assertEqual(self.old_record['collection'].users, 0)
        self.assertFalse(self.old_record['objects']['mesh'].removed)
        self.assertFalse(new_record['objects']['mesh'].removed)

    def test_second_store_import_failure_removes_created_geometry_and_session(self):
        self.weapon.remove_context(self.context, self.rig)
        self.rig.pop(self.n['SESSION_KEY'])
        self.rig.pop(self.n['_REFS_KEY'])
        self.n['_CACHE'].clear()
        self.base['packet_sha'] = 'a' * 64
        self.n['ua'] = SimpleNamespace(PACKAGE_HASH_KEY='packet_sha',
            native_action_frame_rate=lambda _action: 2.,
            _mapping=lambda *_args: SimpleNamespace(conversion=[1.]), _rest_state=lambda _data: {})
        self.n['contract'] = SimpleNamespace(validate_capture=lambda value, **_kwargs: deepcopy(value))
        self.n['_new_input_action'] = lambda *_args: (self.inputs, 7, {'new_action_id': '2' * 32})
        created = []

        def add(_context, _rig, _preview, _packet, _mapping, *, session):
            created.append(self.install_display(session, 'Failed import display'))
            return session['weapon_preview']

        self.weapon.add_rigging_context = add
        actual_store, calls = self.n['_store'], []
        self.rig.write_fault = lambda key, value: (
            key == self.n['SESSION_KEY'] and '"weapon_preview"' in value)

        def fail_second(rig, session):
            calls.append('weapon_preview' in session)
            return actual_store(rig, session)

        self.n['_store'] = fail_second
        with self.assertRaisesRegex(RuntimeError, 'second session store'):
            self.n['import_inputs'](self.context, self.rig,
                {'rigging': self.capture, '_sha256': 'a' * 64}, base_action=self.base,
                base_slot=4, work_item_id='1' * 32)
        self.assertEqual(calls, [False, True])
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0]['collection'].removed)
        self.assertTrue(created[0]['objects']['mesh'].removed)
        self.assertNotIn(self.weapon.RECORD, self.rig)
        self.assertNotIn(self.n['SESSION_KEY'], self.rig)
        self.assertNotIn(self.n['_REFS_KEY'], self.rig)
        self.assertNotIn(self.n['CAPTURE_KEY'], self.base)
        self.assertNotIn(self.n['_INPUT_REF'], self.base)
        self.assertFalse(self.base.removed or self.rig.removed)

    def test_snapshot_tuple_shape_and_native_references_restore_after_write_failure(self):
        self.rig['Right_targetPosition'] = [1., 2., 3.]
        self.base[self.n['_INPUT_REF']] = self.inputs
        self.n['_SUSPENDED'][self.rig.as_pointer()] = 1
        for failure in (False, True):
            with self.subTest(write_failure=failure):
                try:
                    with self.n['snapshot_base_refs'](self.rig, self.base):
                        self.assertNotIn(self.weapon.RECORD, self.rig)
                        self.assertNotIn(self.n['SESSION_KEY'], self.rig)
                        self.assertNotIn(self.n['_INPUT_REF'], self.base)
                        if failure:
                            raise RuntimeError('injected snapshot write failure')
                except RuntimeError as error:
                    self.assertTrue(failure)
                    self.assertEqual(str(error), 'injected snapshot write failure')
                self.assert_record_ids(self.old_record)
                self.assertIs(self.base[self.n['_INPUT_REF']], self.inputs)
                self.assertEqual(self.rig['Right_targetPosition'], [1., 2., 3.])
                self.assertIs(self.n['current'](self.rig)['base_action'], self.base)
                self.assertFalse(self.old_record['objects']['mesh'].removed)

    def test_copied_unregistered_weapon_token_cannot_detach_old_display(self):
        token = self.n['_capture_owned'](self.context, self.rig)
        forged = dict(token)
        with self.assertRaisesRegex(ValueError, 'not this native transaction'):
            self.n['_detach_weapon'](self.rig, forged)
        self.assert_record_ids(self.old_record)
        self.assertEqual(token['status'], 'pending')


class PreviewWeightSemanticsTests(unittest.TestCase):
    """Actual callback/apply/activate/base_only AST, with no native solver."""
    def setUp(self):
        class Matrix:
            def __init__(self, translation=(0., 0., 0.), rotation=(1., 0., 0., 0.)):
                self.translation, self.rotation = tuple(translation), tuple(rotation)

            def copy(self):
                return Matrix(self.translation, self.rotation)

            def __matmul__(self, other):
                return other

            def inverted(self):
                return self

            def to_quaternion(self):
                return self.rotation

            @staticmethod
            def LocRotScale(position, rotation, _scale):
                return Matrix(position, rotation)

        self.Matrix, self.calls = Matrix, []
        self.scene = Native('Scene', tool_settings=SimpleNamespace(use_keyframe_insert_auto=False),
            frame_current=1, frame_subframe=0.)
        self.rig = Armature('Rig', matrix_world=Matrix(), animation_data=SimpleNamespace(
            use_tweak_mode=False, use_nla=True, action=None, action_slot_handle=1))
        self.rig.animation_data_create = lambda: self.rig.animation_data
        self.rig['session'] = 'owned'
        self.scene.objects = [self.rig]
        self.base = Action('Base', use_fake_user=True)
        self.base['unity_start_frame'] = 1.
        bones, entries, rows = {}, {}, []
        for side in ('R', 'L'):
            names = [side + role for role in ('Upper', 'Mid', 'Tip')]
            for name in names:
                bones[name] = SimpleNamespace(matrix=Matrix((8., 9., 10.)))
            bones[side + 'Pole'] = SimpleNamespace(matrix=Matrix())
            entries['ARM', side] = {'chain': names, 'side': side}
            rows.append({'native_side': side, 'chain': names, 'pole': side + 'Pole'})
        self.rig.pose = SimpleNamespace(bones=bones)
        self.inventory = {'rigs': entries}
        self.modes = {'R': 'FK', 'L': 'FK'}
        bindings = [dict(rigWeight=0., constraintWeight=1., targetPositionWeight=1.,
            targetRotationWeight=1., hintWeight=1.) for _ in range(2)]
        self.session = {'rig': self.rig, 'scene': self.scene, 'capture':
            {'previewEnabled': True, 'bindings': bindings}, 'native_bindings': rows,
            'enabled': True, 'tracks': {'owned': True}, 'edit_role': 'BASE',
            'tweak_enabled': False, 'source_read_only': False, 'conversion': [],
            'base_action': self.base, 'base_slot': 1}
        self.context = SimpleNamespace(scene=self.scene)
        tracks = {key: SimpleNamespace(mute=False) for key in ('BASE', 'RIGGING')}

        def switched(_context, _rig, key, mode, **kwargs):
            self.calls.append(('switch', key[1], mode, kwargs['keyframe']))
            self.modes[key[1]] = mode

        @contextmanager
        def owned_context(_session):
            yield self.context

        def prior(_context, _rig):
            return {'snapshot': {'frame': 1.}, 'modes': dict(self.modes),
                    'use_nla': self.rig.animation_data.use_nla, 'action': self.rig.animation_data.action}

        def restored(_context, _rig, state):
            self.modes.update(state['modes'])
            self.rig.animation_data.use_nla = state['use_nla']
            self.rig.animation_data.action = state['action']

        def frame(_scene, _value):
            self.modes.update(R='FK', L='FK')

        self.n = {'contextmanager': contextmanager, 'deepcopy': deepcopy,
            'Matrix': Matrix, 'Vector': tuple, 'Quaternion': tuple,
            'bpy': SimpleNamespace(app=SimpleNamespace(handlers=SimpleNamespace(persistent=lambda f: f))),
            'SESSION_KEY': 'session', '_BUSY': set(), '_SUSPENDED': {}, '_HANDLER_ERRORS': {},
            '_require': lambda ok, text: None if ok else (_ for _ in ()).throw(ValueError(text)),
            '_validate': lambda _session: None, '_inventory': lambda _session: self.inventory,
            '_time': lambda *_args: .5, '_matrix': lambda _values: Matrix(),
            '_rows': lambda _matrix: [0.] * 16, '_sample': lambda *_args: [
                {'targetPosition': [1., 2., 3.], 'targetRotation': [0., 0., 0., 1.],
                 'hintPosition': [4., 5., 6.]} for _ in range(2)],
            '_parent_at': lambda *_args: Matrix(), '_tip_from_unity': lambda _s, _i, value: value,
            '_write_target': lambda _ctx, _s, index, _entry, desired:
                self.calls.append(('target', index, desired.translation, desired.rotation)),
            '_restrict_inputs': lambda _session: self.calls.append(('restrict',)),
            'limb_ik_fk': SimpleNamespace(mode_for_rig=lambda _r, entry: self.modes[entry['side']],
                switch_limb=switched, _update=lambda *_args: self.calls.append(('update',))),
            'bone_collections': SimpleNamespace(sync_limb_display=lambda *_args, **_kwargs: None),
            'current': lambda _rig: self.session, '_context': owned_context,
            '_handler_error': lambda _rig, _session, error: (_ for _ in ()).throw(error),
            '_tracks': lambda _session: tracks, '_detached': deepcopy,
            '_store': lambda _rig, _session: self.calls.append(('store',)),
            '_handlers': lambda: self.calls.append(('install_handlers',)),
            '_remove_handlers_if_idle': lambda: self.calls.append(('remove_idle_handlers',)),
            'bone_display': SimpleNamespace(_snapshot=lambda _r: {}, _restore=lambda *_args: None),
            'composition': SimpleNamespace(_prior=prior, _restore_prior=restored,
                _slot=lambda *_args: SimpleNamespace(handle=1)),
            'ua': SimpleNamespace(_set_frame=frame, native_action_frame_rate=lambda _a: 60.),
            '_restore_live_pose': lambda *_args: self.calls.append(('restore_live_pose',))}
        functions(SOURCE / 'unity_animation_rigging.py',
                  ('_apply', '_frame', 'activate', 'base_only'), self.n)

    def test_raw_zero_weight_applies_enabled_preview_without_editing_saved_weights(self):
        stored = deepcopy(self.session['capture'])
        self.n['_frame'](self.scene)
        self.assertEqual([call for call in self.calls if call[0] == 'switch'],
                         [('switch', 'R', 'IK', False), ('switch', 'L', 'IK', False)])
        self.assertEqual(len([call for call in self.calls if call[0] == 'target']), 2)
        self.assertEqual(self.session['capture'], stored)

    def test_disabled_saved_view_is_not_applied_by_frame_callback(self):
        self.session['enabled'] = False
        stored = deepcopy(self.session['capture'])
        self.n['_frame'](self.scene)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.modes, {'R': 'FK', 'L': 'FK'})
        self.assertEqual(self.session['capture'], stored)

    def test_base_only_nested_and_exception_suspends_preview_without_weight_rewrite(self):
        stored = deepcopy(self.session['capture'])
        with self.assertRaisesRegex(RuntimeError, 'owned sample failed'):
            with self.n['base_only'](self.context, self.rig, self.base):
                with self.n['base_only'](self.context, self.rig, self.base):
                    self.assertEqual(self.n['_SUSPENDED'][self.rig.as_pointer()], 2)
                    self.n['_frame'](self.scene)
                    self.assertFalse(any(call[0] in {'switch', 'target'} for call in self.calls))
                    self.assertIs(self.rig.animation_data.action, self.base)
                    raise RuntimeError('owned sample failed')
        self.assertEqual(self.n['_SUSPENDED'], {})
        self.assertEqual(self.n['_BUSY'], set())
        self.assertTrue(self.rig.animation_data.use_nla)
        self.assertEqual(self.session['capture'], stored)

    def test_constraint_and_channel_weights_remain_independent_of_global_preview(self):
        binding = self.session['capture']['bindings'][0]
        binding.update(constraintWeight=0., targetPositionWeight=0., targetRotationWeight=0., hintWeight=0.)
        self.n['_frame'](self.scene)
        self.assertEqual([call for call in self.calls if call[0] == 'switch'], [('switch', 'L', 'IK', False)])
        right = next(call for call in self.calls if call[:2] == ('target', 0))
        self.assertEqual(right[2:], ((8., 9., 10.), (1., 0., 0., 0.)))
        self.assertEqual(len([call for call in self.calls if call[0] == 'update']), 1)
        self.assertEqual(binding['rigWeight'], 0.)

    def test_apply_auto_align_readback_uses_final_pole_and_settled_right_parent(self):
        # Actual _apply with a same-axis Auto Align solver boundary: the Pole
        # changes the natural hand orientation, and Save's readback composes
        # the stored Target offset with that current natural orientation.
        # This is a behavioral dependency regression, not native solver QA.
        for original, hint_weight in ((False, 1.), (True, 1.), (True, 0.)):
            with self.subTest(original=original, hint_weight=hint_weight):
                self.setUp()
                with self.display_handoff_fixture(original=original):
                    values = [dict(targetPosition=[1., 2., 3.],
                        targetRotation=[0., 0., math.sin(angle / 2), math.cos(angle / 2)],
                        hintPosition=[pole, 5., 6.]) for angle, pole in ((.2, .9), (.4, -.6))]
                    for binding in self.session['capture']['bindings']:
                        binding['hintWeight'] = hint_weight
                    self.n['_sample'] = lambda *_args: deepcopy(values)
                    offsets, parent_reads = {}, []

                    def read_angle(side):
                        pole = self.rig.pose.bones[side + 'Pole'].matrix.translation[0]
                        return offsets[side] + pole

                    def quaternion(angle):
                        return (math.cos(angle / 2), 0., 0., math.sin(angle / 2))

                    def angle(matrix):
                        return 2 * math.atan2(matrix.rotation[3], matrix.rotation[0])

                    class Parent:
                        def __init__(self, rotation):
                            self.rotation = rotation

                        def __matmul__(_self, matrix):
                            return self.Matrix(matrix.translation, quaternion(_self.rotation + angle(matrix)))

                    def parent_at(_session, index, field, _time):
                        if index == 1 and field == 'targetParentRoot':
                            current = read_angle('R')
                            parent_reads.append(current)
                            return Parent(current)
                        return self.Matrix()

                    def write_target(_ctx, _session, index, entry, desired):
                        side = entry['side']
                        natural = self.rig.pose.bones[side + 'Pole'].matrix.translation[0]
                        offsets[side] = angle(desired) - natural
                        self.calls.append(('target', index, desired.translation, desired.rotation))

                    self.n.update(_parent_at=parent_at, _write_target=write_target)
                    stored = deepcopy(self.session['capture'])
                    self.n['_apply'](self.context, self.session)
                    # The Save decoder removes Left's CURRENT RightHand parent.
                    decoded_local = (read_angle('R'), read_angle('L') - read_angle('R'))
                    for actual, expected in zip(decoded_local, (.2, .4)):
                        self.assertAlmostEqual(actual, expected, places=12)
                    self.assertTrue(parent_reads)
                    for parent in parent_reads:
                        self.assertAlmostEqual(parent, .2, places=12)
                    self.assertEqual([call[1] for call in self.calls if call[0] == 'target'], [0, 1])
                    self.assertEqual(self.session['capture'], stored)

    def test_activate_enabled_false_turns_off_tweak_not_authenticated_preview_choice(self):
        stored = deepcopy(self.session['capture'])
        result = self.n['activate'](self.context, self.session, edit_role='BASE', enabled=False)
        self.assertTrue(result['enabled'])
        self.assertFalse(result['tweak_enabled'])
        self.assertEqual(result['edit_role'], 'BASE')
        self.assertEqual(len([call for call in self.calls if call[0] == 'switch']), 2)
        self.assertEqual(self.session['capture'], stored)


    def test_original20_forearm_refresh_is_final_after_nested_apply_and_failed_activation_recovery(self):
        # Actual C5 activate/apply and actual official deferred/runtime functions.
        # Geometry evaluation is an explicit fake observation boundary, not a
        # native Forearm solver or asset acceptance claim.
        forearm = ModuleType('rigging_forearm_test.forearm_twist')
        forearm.__dict__.update(contextmanager=contextmanager, _BUSY=False,
            RECORD_KEY='forearm_record', _ERRORS={}, _CACHE={},
            bpy=SimpleNamespace(context=SimpleNamespace(evaluated_depsgraph_get=lambda: object())),
            _calculate_object=lambda _obj, _graph: self.calls.append(('forearm_final', tuple(sorted(self.modes.items())))))
        functions(SOURCE / 'forearm_twist.py', ('defer_runtime', 'update_runtime'), forearm.__dict__)
        parent = ModuleType('rigging_forearm_test'); parent.__path__ = []; parent.forearm_twist = forearm
        self.n.update(__package__='rigging_forearm_test', __name__='rigging_forearm_test.unity_animation_rigging')
        self.session['original_inputs'] = {}
        self.rig.type = 'ARMATURE'
        sleeve = Native('Sleeve', type='MESH', data=SimpleNamespace(shape_keys=None))
        sleeve['forearm_record'] = 'authenticated-observation-boundary'
        self.scene.objects.append(sleeve)
        original_switch = self.n['limb_ik_fk'].switch_limb
        original_write = self.n['_write_target']
        def switch_with_callback(*args, **kwargs):
            original_switch(*args, **kwargs)
            forearm.update_runtime(self.scene)
        def write_with_callback(*args):
            original_write(*args)
            forearm.update_runtime(self.scene)
        self.n['limb_ik_fk'].switch_limb = switch_with_callback
        self.n['_write_target'] = write_with_callback
        modules = {'rigging_forearm_test': parent, 'rigging_forearm_test.forearm_twist': forearm}
        stored = deepcopy(self.session['capture'])
        with patch.dict(sys.modules, modules):
            self.n['activate'](self.context, self.session, edit_role='BASE', enabled=False)
            refreshes = [call for call in self.calls if call[0] == 'forearm_final']
            self.assertEqual(refreshes, [('forearm_final', (('L', 'IK'), ('R', 'IK')))])
            self.assertGreater(self.calls.index(refreshes[0]),
                max(index for index, call in enumerate(self.calls) if call[0] in {'switch', 'target', 'update'}))
            self.assertFalse(forearm._BUSY)
            self.assertEqual(self.n['_BUSY'], set())
            self.assertEqual(self.session['capture'], stored)
            # Enter the next transaction from FK, then fail its second Target.
            # The actual activation rollback restores the prior mode before the
            # outer official defer scope publishes anything from this call.
            self.modes.update(R='FK', L='FK'); self.calls.clear()
            prior_restore = self.n['composition']._restore_prior
            def restore_with_witness(*args):
                prior_restore(*args)
                self.calls.append(('restored_prior',))
            def fail_second_target(*args):
                write_with_callback(*args)
                if args[2] == 1: raise RuntimeError('injected owned second Target failure')
            self.n['composition']._restore_prior = restore_with_witness
            self.n['_write_target'] = fail_second_target
            with self.assertRaisesRegex(RuntimeError, 'owned second Target failure'):
                self.n['activate'](self.context, self.session, edit_role='BASE', enabled=False)
            refreshes = [call for call in self.calls if call[0] == 'forearm_final']
            self.assertEqual(refreshes, [('forearm_final', (('L', 'FK'), ('R', 'FK')))])
            self.assertLess(self.calls.index(('restored_prior',)), self.calls.index(refreshes[0]))
            self.assertEqual(self.modes, {'R': 'FK', 'L': 'FK'})
            self.assertFalse(forearm._BUSY)
            self.assertEqual(self.n['_BUSY'], set())
            self.assertEqual(self.session['capture'], stored)



    @contextmanager
    def display_handoff_fixture(self, *, original):
        """Observe the official display seam; no native display/solver claim."""
        forearm = ModuleType('rigging_display_test.forearm_twist')
        @contextmanager
        def deferred(_context):
            self.calls.append(('defer_enter',))
            try:
                yield
            finally:
                self.calls.append(('defer_exit',))
        forearm.defer_runtime = deferred
        parent = ModuleType('rigging_display_test'); parent.__path__ = []
        parent.forearm_twist = forearm
        self.n.update(__package__='rigging_display_test', __name__='rigging_display_test.native')
        if original:
            self.session['original_inputs'] = {}
        else:
            self.session.pop('original_inputs', None)
        # The reference matcher temporarily leaves these owned display states
        # in FK. A successful handoff must see the final native modes instead.
        self.display_modes = {'R': 'FK', 'L': 'FK', 'leg': 'artist leg display'}
        self.hide_flags = {'R': True, 'L': False, 'leg': True}
        self.inventory['rigs']['LEG', 'L'] = {'side': 'leg'}
        self.modes['leg'] = 'FK'
        def sync_display(rig, inventory, *, keys, force_flags):
            self.assertIs(rig, self.rig)
            self.assertIs(inventory, self.inventory)
            self.calls.append(('display', keys, force_flags, self.modes['R'], self.modes['L']))
            for kind, side in keys:
                self.assertEqual(kind, 'ARM')
                self.display_modes[side] = self.modes[side]
                if force_flags:
                    self.hide_flags[side] = False
        self.n['bone_collections'].sync_limb_display = sync_display
        for row in self.session['native_bindings']:
            row['target'] = row['native_side'] + 'Target'
            for name in (row['target'], row['pole']):
                self.rig.pose.bones[name] = SimpleNamespace(
                    matrix=self.Matrix(), matrix_basis=self.Matrix((7., 8., 9.)))
        functions(SOURCE / 'unity_animation_rigging.py', ('_restore_preview_mode',), self.n)
        modules = {'rigging_display_test': parent, 'rigging_display_test.forearm_twist': forearm}
        with patch.dict(sys.modules, modules):
            yield

    def test_apply_hands_off_owned_arm_display_after_final_pose_preserving_original_hide_flags(self):
        for original in (True, False):
            with self.subTest(original=original):
                self.setUp()
                with self.display_handoff_fixture(original=original):
                    flags, capture = dict(self.hide_flags), deepcopy(self.session['capture'])
                    self.n['_apply'](self.context, self.session)
                    displays = [call for call in self.calls if call[0] == 'display']
                    self.assertEqual(displays, [('display', (('ARM', 'R'), ('ARM', 'L')), not original, 'IK', 'IK')])
                    self.assertGreater(self.calls.index(displays[0]),
                        max(i for i, call in enumerate(self.calls) if call[0] in {'switch', 'target', 'update'}))
                    self.assertLess(self.calls.index(displays[0]), self.calls.index(('restrict',)))
                    self.assertEqual(self.display_modes, {'R': 'IK', 'L': 'IK', 'leg': 'artist leg display'})
                    self.assertEqual(self.hide_flags, flags if original else {'R': False, 'L': False, 'leg': True})
                    self.assertEqual(self.session['capture'], capture)

    def test_mode_restoration_hands_off_original_display_after_restoring_unkeyed_controls(self):
        for original in (True, False):
            with self.subTest(original=original):
                self.setUp()
                with self.display_handoff_fixture(original=original):
                    self.modes.update(R='BLEND', L='FK')
                    flags = dict(self.hide_flags)
                    switch = self.n['limb_ik_fk'].switch_limb
                    def matching(*args, **kwargs):
                        switch(*args, **kwargs)
                        for row in self.session['native_bindings']:
                            if row['native_side'] == args[2][1]:
                                for name in (row['target'], row['pole']):
                                    self.rig.pose.bones[name].matrix_basis = self.Matrix((0., 0., 0.))
                    self.n['limb_ik_fk'].switch_limb = matching
                    sync = self.n['bone_collections'].sync_limb_display
                    def final_display(*args, **kwargs):
                        for row in self.session['native_bindings']:
                            for name in (row['target'], row['pole']):
                                self.assertEqual(self.rig.pose.bones[name].matrix_basis.translation, (7., 8., 9.))
                        sync(*args, **kwargs)
                    self.n['bone_collections'].sync_limb_display = final_display
                    self.n['_restore_preview_mode'](self.context, self.session)
                    displays = [call for call in self.calls if call[0] == 'display']
                    self.assertEqual(displays, [('display', (('ARM', 'R'), ('ARM', 'L')), False, 'IK', 'IK')] if original else [])
                    if original:
                        self.assertGreater(self.calls.index(displays[0]),
                            max(i for i, call in enumerate(self.calls) if call[0] in {'switch', 'update'}))
                        self.assertLess(self.calls.index(displays[0]), self.calls.index(('defer_exit',)))
                    self.assertEqual(self.hide_flags, flags)
                    self.assertEqual(self.display_modes['leg'], 'artist leg display')

    def test_settled_or_disabled_mode_restoration_does_not_flush_or_handoff_display(self):
        with self.display_handoff_fixture(original=True):
            self.modes.update(R='IK', L='IK')
            self.n['_restore_preview_mode'](self.context, self.session)
            self.assertEqual(self.calls, [])
            self.modes.update(R='FK', L='FK')
            for binding in self.session['capture']['bindings']:
                binding['constraintWeight'] = 0.
            self.n['_restore_preview_mode'](self.context, self.session)
            self.assertEqual(self.calls, [])

    def test_actual_depsgraph_noop_and_changed_parent_coalesce_final_forearm_refresh(self):
        # Real callback and official defer/update code; geometry is a fake
        # observation boundary, never a native Forearm or model proof.
        functions(SOURCE / 'unity_animation_rigging.py',
                  ('_restore_preview_mode', '_depsgraph'), self.n)
        forearm = ModuleType('rigging_depsgraph_test.forearm_twist')
        forearm.__dict__.update(contextmanager=contextmanager, _BUSY=False,
            RECORD_KEY='forearm_record', _ERRORS={}, _CACHE={},
            bpy=SimpleNamespace(context=SimpleNamespace(evaluated_depsgraph_get=lambda: object())),
            _calculate_object=lambda _obj, _graph: self.calls.append(('forearm_final', tuple(sorted(self.modes.items())))))
        functions(SOURCE / 'forearm_twist.py', ('defer_runtime', 'update_runtime'), forearm.__dict__)
        parent = ModuleType('rigging_depsgraph_test'); parent.__path__ = []; parent.forearm_twist = forearm
        self.n.update(__package__='rigging_depsgraph_test', __name__='rigging_depsgraph_test.unity_animation_rigging')
        self.session.update(original_inputs={}, _time=.5, _left_parent=[0.] * 16)
        self.rig.type = 'ARMATURE'
        sleeve = Native('Sleeve', type='MESH', data=SimpleNamespace(shape_keys=None))
        sleeve['forearm_record'] = 'authenticated-observation-boundary'
        self.scene.objects.append(sleeve)
        for row in self.session['native_bindings']:
            row['target'] = row['native_side'] + 'Target'
            for name in (row['target'], row['pole']):
                self.rig.pose.bones[name] = SimpleNamespace(matrix=self.Matrix(), matrix_basis=self.Matrix())
        parent_x, parent_after_switch = [0.], [None]
        self.n['_rows'] = lambda matrix: list(matrix.translation) + [0.] * 13
        self.n['_matrix'] = lambda values: self.Matrix(values[:3])
        self.n['_parent_at'] = lambda *_args: self.Matrix((parent_x[0], 0., 0.))
        self.n['_unity_from_tip'] = lambda _session, _index, value: value
        self.n['_read_target'] = lambda *_args: self.Matrix()
        original_switch, original_write = self.n['limb_ik_fk'].switch_limb, self.n['_write_target']
        def switch_with_callback(*args, **kwargs):
            original_switch(*args, **kwargs)
            if parent_after_switch[0] is not None:
                parent_x[0] = parent_after_switch[0]
            forearm.update_runtime(self.scene)
        def write_with_callback(*args):
            original_write(*args)
            forearm.update_runtime(self.scene)
        self.n['limb_ik_fk'].switch_limb = switch_with_callback
        self.n['_write_target'] = write_with_callback
        modules = {'rigging_depsgraph_test': parent, 'rigging_depsgraph_test.forearm_twist': forearm}
        stored = deepcopy(self.session['capture'])
        with patch.dict(sys.modules, modules):
            self.modes.update(R='IK', L='IK')
            self.n['_restore_preview_mode'](self.context, self.session)
            self.n['_depsgraph'](self.scene)
            self.assertEqual(self.calls, [])
            # The existing <=1e-7 parent threshold remains a stable no-op.
            parent_x[0] = .5e-7
            self.n['_depsgraph'](self.scene)
            self.assertEqual(self.calls, [])
            for modes, parent_value, after_switch, targets in (
                    ({'R': 'BLEND', 'L': 'IK'}, 0., None, 0),
                    ({'R': 'FK', 'L': 'FK'}, 1., 2., 1),
                    ({'R': 'IK', 'L': 'IK'}, 1., None, 1)):
                with self.subTest(modes=modes, parent=parent_value):
                    self.calls.clear(); self.modes.update(modes)
                    self.session['_left_parent'] = [0.] * 16
                    parent_x[0], parent_after_switch[0] = parent_value, after_switch
                    self.n['_depsgraph'](self.scene)
                    refreshes = [call for call in self.calls if call[0] == 'forearm_final']
                    self.assertEqual(refreshes, [('forearm_final', (('L', 'IK'), ('R', 'IK')))])
                    self.assertEqual(len([call for call in self.calls if call[0] == 'target']), targets)
                    self.assertEqual(len([call for call in self.calls if call[0] == 'switch']),
                                     sum(mode != 'IK' for mode in modes.values()))
                    self.assertGreater(self.calls.index(refreshes[0]),
                        max(index for index, call in enumerate(self.calls) if call[0] in {'switch', 'target', 'update'}))
                    if targets:
                        self.assertEqual(self.session['_left_parent'], [parent_x[0], 0., 0.] + [0.] * 13)
                    self.assertFalse(forearm._BUSY)
                    self.assertEqual(forearm._ERRORS, {})
                    self.assertEqual(self.n['_BUSY'], set())
                    self.assertEqual(self.session['capture'], stored)
                    # A second callback over the settled state does no work.
                    self.calls.clear()
                    self.n['_depsgraph'](self.scene)
                    self.assertEqual(self.calls, [])
            # The ordinary callback retains its existing mode/parent behavior
            # without loading or flushing the Original20 Forearm transaction.
            self.session.pop('original_inputs')
            self.n['limb_ik_fk'].switch_limb = original_switch
            self.n['_write_target'] = original_write
            self.session['_left_parent'] = [0.] * 16
            self.modes.update(R='FK', L='FK')
            parent_x[0], parent_after_switch[0] = 1., None
            self.n['_depsgraph'](self.scene)
            self.assertEqual(len([call for call in self.calls if call[0] == 'target']), 1)
            self.assertFalse(any(call[0] == 'forearm_final' for call in self.calls))
            self.assertEqual(self.n['_BUSY'], set())
            self.assertFalse(forearm._BUSY)
            self.assertEqual(self.session['capture'], stored)


class LifecycleTimers:
    def __init__(self):
        self.pending, self.registrations = [], []

    def register(self, callback, **options):
        self.pending.append(callback)
        self.registrations.append((callback, options))

    def unregister(self, callback):
        self.pending.remove(callback)

    def is_registered(self, callback):
        return callback in self.pending

    def run(self):
        callback = self.pending.pop(0)
        return callback()


class LifecycleWorld:
    """Fault only the admission seam; execute the actual lifecycle functions."""
    def __init__(self):
        self.rig = Native('Artist Rig', matrix_world=[[1., 0.], [0., 1.]])
        self.rig['session'] = json.dumps({'payload': {'enabled': True}})
        self.scene, self.base, self.inputs = (Native(name) for name in ('Scene', 'Body', 'Inputs'))
        self.base['motion'], self.inputs['motion'] = 'untouched Body curves', 'untouched twenty curves'
        self.objects = Table([self.rig])
        self.timers = LifecycleTimers()
        self.handlers = SimpleNamespace(**{name: [] for name in
            ('load_post', 'undo_post', 'redo_post', 'frame_change_post', 'depsgraph_update_post')},
            persistent=lambda callback: callback)
        self.current_calls, self.file_calls = [], []
        self.admission_failures, self.file_failures = [], []
        self.n = {'__name__': 'lifecycle_test.native', 'json': json, 'SESSION_KEY': 'session',
            'bpy': SimpleNamespace(data=SimpleNamespace(objects=self.objects),
                                   app=SimpleNamespace(handlers=self.handlers, timers=self.timers)),
            '_CACHE': {}, '_HANDLER_ERRORS': {}, '_BUSY': set(), '_SUSPENDED': {}, '_OWNED_TOKENS': {},
            '_REACQUIRE_PENDING': {}, '_REACQUIRE_TIMER': None, '_REACQUIRE_GENERATION': 0,
            'current': self.current, '_validate_preview_reference': self.files}
        # These callbacks may be registered but must never execute during admission.
        exec('def _frame(*args):\n    raise AssertionError("frame evaluation during admission")\n'
             'def _depsgraph(*args):\n    raise AssertionError("solver during admission")\n', self.n)
        functions(SOURCE / 'unity_animation_rigging.py',
            {'_require', 'display_state', '_handlers', '_remove_handlers_if_idle', '_cancel_reacquire',
             '_deferred_reacquire', '_reacquire', '_registration_reacquire', 'register', 'unregister'}, self.n)
        self.before = self.artist_state()

    def artist_state(self):
        return deepcopy((dict(self.rig), self.rig.matrix_world, dict(self.base), dict(self.inputs), dict(self.scene)))

    def current(self, rig):
        self.current_calls.append(rig)
        if self.admission_failures:
            error = self.admission_failures.pop(0)
            if error:
                raise ValueError(error)
        session = {'rig': rig, 'scene': self.scene, 'base_action': self.base, 'input_action': self.inputs,
            'edit_role': 'RIGGING', 'source_read_only': False, 'enabled': True, 'tweak_enabled': True,
            'original_inputs': {}}
        self.n['_CACHE'][rig.as_pointer()] = session
        return session

    def files(self, session, *, files):
        if files is not True:
            raise AssertionError('Deferred admission skipped the actual reference-file check')
        self.file_calls.append(session)
        if self.file_failures:
            error = self.file_failures.pop(0)
            if error:
                raise ValueError(error)


class LifecycleAdmissionTests(unittest.TestCase):
    def assert_artist_unchanged(self, world):
        self.assertEqual(world.artist_state(), world.before)

    def assert_callbacks(self, world, expected):
        self.assertEqual(world.handlers.frame_change_post, [world.n['_frame']] if expected else [])
        self.assertEqual(world.handlers.depsgraph_update_post, [world.n['_depsgraph']] if expected else [])

    def test_restricted_registration_defers_full_admission_with_fresh_ids_once(self):
        w = LifecycleWorld()
        w.n['bpy'].data = SimpleNamespace()  # Blender's _RestrictData has no objects.
        w.n['register']()
        self.assertEqual(w.current_calls, [])
        self.assertEqual(w.file_calls, [])
        self.assertEqual(w.n['_CACHE'], {})
        self.assert_callbacks(w, False)
        self.assertEqual(len(w.timers.pending), 1)
        self.assertEqual(w.timers.registrations[0][1], {'first_interval': 0., 'persistent': False})
        fresh = Native('Reacquired Rig', matrix_world=w.rig.matrix_world)
        fresh['session'] = w.rig['session']
        w.n['bpy'].data = SimpleNamespace(objects=Table([fresh]))
        self.assertIsNone(w.timers.run())
        self.assertEqual(w.current_calls, [fresh])
        self.assertEqual(len(w.file_calls), 1)
        self.assertIs(w.file_calls[0]['rig'], fresh)
        self.assertIsNone(w.n['_REACQUIRE_TIMER'])
        self.assertEqual(w.timers.pending, [])
        self.assert_callbacks(w, True)
        self.assert_artist_unchanged(w)

    def test_restricted_registration_reload_and_unregister_cancel_stale_timer(self):
        w = LifecycleWorld()
        w.n['bpy'].data = SimpleNamespace()
        w.n['register']()
        stale = w.timers.pending[0]
        functions(SOURCE / 'unity_animation_rigging.py',
            {'_registration_reacquire', 'register', 'unregister'}, w.n)
        w.n['register']()
        latest = w.timers.pending[0]
        self.assertEqual(len(w.timers.pending), 1)
        self.assertIsNot(stale, latest)
        self.assertIsNone(stale())
        w.n['unregister']()
        self.assertIsNone(latest())
        self.assertEqual(w.current_calls, [])
        self.assertEqual(w.file_calls, [])
        self.assertEqual(w.timers.pending, [])
        self.assertIsNone(w.n['_REACQUIRE_TIMER'])
        self.assertTrue(all(not getattr(w.handlers, name) for name in
            ('load_post', 'undo_post', 'redo_post', 'frame_change_post', 'depsgraph_update_post')))
        self.assert_artist_unchanged(w)

    def test_restricted_registration_timer_error_propagates_for_owned_cleanup(self):
        w = LifecycleWorld()
        w.n['bpy'].data = SimpleNamespace()
        def unavailable(*_args, **_kwargs):
            raise RuntimeError('timer unavailable')
        w.timers.register = unavailable
        with self.assertRaisesRegex(RuntimeError, 'timer unavailable'):
            w.n['register']()
        self.assertIsNone(w.n['_REACQUIRE_TIMER'])
        w.n['unregister']()
        self.assertEqual(w.current_calls, [])
        self.assertEqual(w.file_calls, [])
        self.assertEqual(w.timers.pending, [])
        self.assert_artist_unchanged(w)

    def test_synchronous_admission_needs_no_timer_and_registration_is_idempotent(self):
        w = LifecycleWorld()
        w.n['register']()
        w.n['register']()
        self.assertEqual(len(w.current_calls), 2)
        self.assertEqual(len(w.file_calls), 2)
        self.assertEqual(w.timers.registrations, [])
        self.assertEqual(w.n['_HANDLER_ERRORS'], {})
        for name in ('load_post', 'undo_post', 'redo_post'):
            self.assertEqual(getattr(w.handlers, name), [w.n['_reacquire']])
        self.assert_callbacks(w, True)
        self.assertIs(w.n['display_state'](w.rig)['rig'], w.rig)
        self.assert_artist_unchanged(w)

    def test_transient_transform_failure_retries_full_admission_once_without_artist_writes(self):
        w = LifecycleWorld()
        w.admission_failures = ['authenticated character object transform']
        w.n['register']()
        pointer = w.rig.as_pointer()
        self.assertEqual(w.n['_HANDLER_ERRORS'][pointer], 'authenticated character object transform')
        self.assertNotIn(pointer, w.n['_CACHE'])
        self.assertIsNone(w.n['display_state'](w.rig))
        self.assert_callbacks(w, False)
        callback, options = w.timers.registrations[0]
        self.assertEqual(options, {'first_interval': 0., 'persistent': False})
        self.assertIsNone(callback.__closure__)
        self.assertEqual(callback.__defaults__, (w.n['_REACQUIRE_GENERATION'],))
        self.assertEqual(w.n['_REACQUIRE_PENDING'][pointer], (w.rig.name, w.rig['session']))
        # A separate public read can fill the cache, but may not expose an active
        # UI or clear the lifecycle error before reference files are admitted.
        w.current(w.rig)
        self.assertIsNone(w.n['display_state'](w.rig))
        self.assertIsNone(w.timers.run())
        self.assertEqual(w.current_calls, [w.rig] * 3)
        self.assertEqual(len(w.file_calls), 1)
        self.assertEqual(w.n['_HANDLER_ERRORS'], {})
        self.assertEqual(w.n['_REACQUIRE_PENDING'], {})
        self.assertIsNone(w.n['_REACQUIRE_TIMER'])
        self.assertEqual(w.timers.pending, [])
        self.assertEqual(len(w.timers.registrations), 1)
        self.assert_callbacks(w, True)
        self.assertTrue(w.n['display_state'](w.rig)['original_controls_only'])
        self.assert_artist_unchanged(w)

    def test_permanent_identity_or_reference_file_failure_is_closed_after_one_retry(self):
        for boundary in ('current', 'reference_files'):
            with self.subTest(boundary=boundary):
                w = LifecycleWorld()
                failures = w.admission_failures if boundary == 'current' else w.file_failures
                failures.extend(['first strict rejection', 'still invalid'])
                w.n['register']()
                pointer = w.rig.as_pointer()
                self.assertNotIn(pointer, w.n['_CACHE'])
                self.assertIsNone(w.timers.run())
                self.assertNotIn(pointer, w.n['_CACHE'])
                self.assertEqual(w.n['_HANDLER_ERRORS'][pointer],
                    'first strict rejection; deferred lifecycle admission: still invalid')
                self.assertIsNone(w.n['display_state'](w.rig))
                self.assertEqual(len(w.current_calls), 2)
                self.assertEqual(len(w.file_calls), 2 if boundary == 'reference_files' else 0)
                self.assertEqual(len(w.timers.registrations), 1)
                self.assertEqual(w.timers.pending, [])
                self.assert_callbacks(w, False)
                self.assert_artist_unchanged(w)

    def test_deferred_admission_does_not_accept_replaced_id_or_changed_persistent_owner(self):
        for change in ('replacement', 'payload', 'removed'):
            with self.subTest(change=change):
                w = LifecycleWorld()
                w.admission_failures = ['transient transform']
                w.n['register']()
                pointer = w.rig.as_pointer()
                if change == 'replacement':
                    replacement = Native(w.rig.name)
                    replacement['session'] = w.rig['session']
                    w.objects[:] = [replacement]
                elif change == 'payload':
                    w.rig['session'] = json.dumps({'payload': {'enabled': True, 'owner': 'different'}})
                else:
                    w.objects.clear()
                self.assertIsNone(w.timers.run())
                self.assertEqual(w.current_calls, [w.rig])
                self.assertEqual(w.file_calls, [])
                self.assertEqual(w.n['_HANDLER_ERRORS'][pointer], 'transient transform')
                self.assertEqual(w.n['_CACHE'], {})
                self.assert_callbacks(w, False)

    def test_stale_retry_preserves_successful_activate_payload_cache_and_cleared_error(self):
        w = LifecycleWorld()
        w.admission_failures = ['transient transform']
        w.n['register']()
        pointer = w.rig.as_pointer()
        # Model only the successful Activate commit boundary: a new stored
        # role/Tweak payload, authenticated cache and cleared lifecycle error.
        w.rig['session'] = json.dumps({'payload': {'enabled': True, 'edit_role': 'RIGGING', 'tweak_enabled': True}})
        session = w.current(w.rig)
        w.n['_HANDLER_ERRORS'].pop(pointer)
        w.n['_handlers']()
        after_activate = w.artist_state()
        self.assertIsNone(w.timers.run())
        self.assertIs(w.n['_CACHE'][pointer], session)
        self.assertEqual(w.n['_HANDLER_ERRORS'], {})
        self.assertEqual(len(w.current_calls), 2)
        self.assertEqual(w.file_calls, [])
        self.assertEqual(w.artist_state(), after_activate)
        self.assert_callbacks(w, True)
        self.assertIs(w.n['display_state'](w.rig)['rig'], w.rig)

    def test_stale_retry_preserves_successful_close_without_recreating_error_or_cache(self):
        w = LifecycleWorld()
        w.admission_failures = ['transient transform']
        w.n['register']()
        pointer = w.rig.as_pointer()
        # Successful Close removes the persistent session and its runtime state.
        w.rig.pop('session')
        w.n['_CACHE'].pop(pointer, None)
        w.n['_HANDLER_ERRORS'].pop(pointer)
        w.n['_remove_handlers_if_idle']()
        after_close = w.artist_state()
        self.assertIsNone(w.timers.run())
        self.assertEqual(w.n['_CACHE'], {})
        self.assertEqual(w.n['_HANDLER_ERRORS'], {})
        self.assertEqual(w.current_calls, [w.rig])
        self.assertEqual(w.file_calls, [])
        self.assertEqual(w.artist_state(), after_close)
        self.assert_callbacks(w, False)
        self.assertIsNone(w.n['display_state'](w.rig))

    def test_new_lifecycle_and_unregister_cancel_and_invalidate_old_callbacks(self):
        w = LifecycleWorld()
        w.admission_failures = ['first boundary', 'second boundary']
        w.n['register']()
        old = w.timers.pending[0]
        w.n['_reacquire']()
        new = w.timers.pending[0]
        self.assertIsNot(old, new)
        self.assertNotIn(old, w.timers.pending)
        self.assertIsNone(old())
        self.assertEqual(len(w.current_calls), 2)
        self.assertIs(w.n['_REACQUIRE_TIMER'], new)
        w.n['unregister']()
        w.n['unregister']()
        self.assertIsNone(new())
        self.assertEqual(len(w.current_calls), 2)
        self.assertEqual(w.timers.pending, [])
        self.assertEqual(w.n['_REACQUIRE_PENDING'], {})
        self.assertEqual(w.n['_HANDLER_ERRORS'], {})
        self.assertEqual(w.n['_CACHE'], {})
        for name in ('load_post', 'undo_post', 'redo_post', 'frame_change_post', 'depsgraph_update_post'):
            self.assertEqual(getattr(w.handlers, name), [])
        self.assert_artist_unchanged(w)

    def test_in_place_reload_cancels_old_timer_and_replaces_only_owned_callbacks(self):
        w = LifecycleWorld()
        w.n['register']()
        w.admission_failures = ['boundary before reload']
        w.n['_reacquire']()
        old_timer = w.timers.pending[0]
        foreign = lambda: None
        foreign.__name__, foreign.__module__ = '_reacquire', 'another_addon'
        w.handlers.undo_post.append(foreign)
        # Re-execute the actual lifecycle function definitions in the same module
        # namespace, as importlib.reload does, leaving old function registrations.
        functions(SOURCE / 'unity_animation_rigging.py',
            {'_reacquire', '_cancel_reacquire', '_deferred_reacquire', '_handlers', 'register', 'unregister'}, w.n)
        exec('def _frame(*args):\n    raise AssertionError("frame evaluation during reload")\n'
             'def _depsgraph(*args):\n    raise AssertionError("solver during reload")\n', w.n)
        w.n['register']()
        self.assertIsNone(old_timer())
        self.assertEqual(len(w.current_calls), 3)
        self.assertEqual(w.timers.pending, [])
        self.assertEqual(w.handlers.undo_post, [foreign, w.n['_reacquire']])
        self.assert_callbacks(w, True)
        w.n['unregister']()
        self.assertEqual(w.handlers.undo_post, [foreign])
        self.assert_callbacks(w, False)
        self.assert_artist_unchanged(w)

    def test_one_failed_rig_does_not_remove_other_admitted_rigs_callbacks(self):
        w = LifecycleWorld()
        second = Native('Second Rig')
        second['session'] = w.rig['session']
        w.objects.append(second)
        w.admission_failures = ['first Rig invalid', None, 'first Rig remains invalid']
        w.n['register']()
        self.assert_callbacks(w, True)
        self.assertIs(w.n['display_state'](second)['rig'], second)
        self.assertIsNone(w.timers.run())
        self.assert_callbacks(w, True)
        self.assertIsNone(w.n['display_state'](w.rig))
        self.assertIs(w.n['display_state'](second)['rig'], second)
        self.assertEqual(len(w.timers.registrations), 1)
        self.assert_artist_unchanged(w)

    def test_reload_then_direct_unregister_removes_old_callbacks_and_timer(self):
        w = LifecycleWorld()
        w.n['register']()
        w.admission_failures = ['before reload']
        w.n['_reacquire']()
        old_timer = w.timers.pending[0]
        functions(SOURCE / 'unity_animation_rigging.py',
            {'_reacquire', '_cancel_reacquire', '_deferred_reacquire', 'unregister'}, w.n)
        exec('def _frame(*args):\n    raise AssertionError("old frame callback survived")\n'
             'def _depsgraph(*args):\n    raise AssertionError("old solver callback survived")\n', w.n)
        w.n['unregister']()
        self.assertIsNone(old_timer())
        self.assertEqual(len(w.current_calls), 2)
        self.assertEqual(w.timers.pending, [])
        for name in ('load_post', 'undo_post', 'redo_post', 'frame_change_post', 'depsgraph_update_post'):
            self.assertEqual(getattr(w.handlers, name), [])
        self.assert_artist_unchanged(w)

    def test_nontext_persistent_record_is_not_retained_by_deferred_callback(self):
        w = LifecycleWorld()
        w.rig['session'] = {'invalid_native_reference': w.rig}
        w.admission_failures = ['persistent session must be JSON text']
        w.n['register']()
        self.assertEqual(w.n['_REACQUIRE_PENDING'], {})
        self.assertEqual(w.timers.registrations, [])
        self.assertEqual(w.n['_CACHE'], {})
        self.assertIn('must be JSON text', w.n['_HANDLER_ERRORS'][w.rig.as_pointer()])
        self.assert_callbacks(w, False)

    def test_timer_registration_failure_retains_diagnostic_without_active_cache(self):
        w = LifecycleWorld()
        w.admission_failures = ['strict transform rejection']
        def fail_register(*_args, **_kwargs):
            raise RuntimeError('timer unavailable')
        w.timers.register = fail_register
        w.n['register']()
        self.assertEqual(w.n['_CACHE'], {})
        self.assertEqual(w.n['_REACQUIRE_PENDING'], {})
        self.assertIsNone(w.n['_REACQUIRE_TIMER'])
        self.assertEqual(w.n['_HANDLER_ERRORS'][w.rig.as_pointer()],
            'strict transform rejection; cannot schedule lifecycle admission: timer unavailable')
        self.assert_callbacks(w, False)
        self.assert_artist_unchanged(w)


if __name__ == '__main__':
    unittest.main()
