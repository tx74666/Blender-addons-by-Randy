"""Case-A stale Base Pose preservation, with real validators and no Blender."""
import copy
import importlib.util
from pathlib import Path
import struct
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer'
spec = importlib.util.spec_from_file_location('_stale_base_pose_existing_fixture',
    ROOT / 'tests/test_animation_pose_test.py')
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


class NamedID(old.ID):
    def __bool__(self):
        return True

    @property
    def name_full(self):
        return self.name

    def __eq__(self, other):
        return self is other

    __hash__ = object.__hash__

    def pop(self, key, *default):
        result = super().pop(key, *default)
        if getattr(self, 'fail_after_pop', None) == key:
            self.fail_after_pop = None
            raise RuntimeError('after token removal')
        return result


def action_references(value):
    if isinstance(value, NamedAction):
        return [value]
    if isinstance(value, dict):
        return [ref for child in value.values() for ref in action_references(child)]
    if isinstance(value, (list, tuple)):
        return [ref for child in value for ref in action_references(child)]
    return []


class NamedAction(old.Action):
    def __bool__(self):
        return True

    def __init__(self, name):
        super().__init__(name)
        self.reference_users = 0
        self.outgoing_action_refs = {}
        self.fail_property = None

    @property
    def name_full(self):
        return self.name

    @property
    def users(self):
        return self.binding_users + self.reference_users + int(self.use_fake_user)

    def __eq__(self, other):
        return self is other

    __hash__ = object.__hash__

    def __setitem__(self, key, value):
        if self.fail_property == key:
            self.fail_property = None
            raise RuntimeError('archive property write')
        for ref in self.outgoing_action_refs.pop(key, []):
            ref.reference_users -= 1
        refs = action_references(value)
        self.outgoing_action_refs[key] = refs
        for ref in refs:
            ref.reference_users += 1
        super().__setitem__(key, value)

    def release_outgoing_refs(self):
        for refs in self.outgoing_action_refs.values():
            for ref in refs:
                ref.reference_users -= 1
        self.outgoing_action_refs.clear()


class Actions(old.Actions):
    fail_new = False
    fail_archive_property = None

    def new(self, name):
        if self.fail_new:
            self.fail_new = False
            raise RuntimeError('archive allocation')
        action = NamedAction(name)
        action.fail_property = self.fail_archive_property
        self.fail_archive_property = None
        self.append(action)
        return action

    def remove(self, action):
        action.release_outgoing_refs()
        super().remove(action)


class Animation(old.Animation):
    @property
    def action(self):
        return old.Animation.action.fget(self)

    @action.setter
    def action(self, value):
        old.Animation.action.fset(self, value)
        if value is None:
            self.action_slot = None


class NamedCollection(list):
    active = None

    def get(self, name):
        return next((value for value in self if value.name == name), None)

    def keys(self):
        return [value.name for value in self]

    def __getitem__(self, index):
        if isinstance(index, str):
            value = self.get(index)
            if value is None:
                raise KeyError(index)
            return value
        return super().__getitem__(index)


class ObjectMap(dict):
    active = None


class StaleRecoveryTests(unittest.TestCase):
    def setUp(self):
        package_name = '_stale_pose_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = [str(SOURCE)]
        self.actions = Actions()
        self.bpy = NS(data=NS(actions=self.actions, objects=[]))
        modules = {package_name: package, 'bpy': self.bpy}
        for name, fields in {
            'unity_animation': dict(_set_playing=Mock(), active_preview=lambda rig: None),
            'animation_rest_pose': dict(PREVIEW_KEY='rest_marker'),
            'animation_composition': dict(active=lambda rig: False),
            'body_original_mode': dict(active=lambda rig: False),
            'forearm_twist': dict(_SESSION=None),
            'bone_display': dict(_snapshot=lambda rig: {'display': 'current'}),
            'limb_ik_fk': dict(_leg_pole_metadata=lambda rig: {'pole': 'current'}),
        }.items():
            module = ModuleType(package_name + '.' + name)
            module.__dict__.update(fields)
            modules[module.__name__] = module
            setattr(package, name, module)
        isolated = patch.dict(sys.modules, modules)
        isolated.start(); self.addCleanup(isolated.stop)
        product_spec = importlib.util.spec_from_file_location(package_name + '.animation_pose_test',
            SOURCE / 'animation_pose_test.py')
        self.product = importlib.util.module_from_spec(product_spec)
        sys.modules[product_spec.name] = self.product
        product_spec.loader.exec_module(self.product)
        collection = NS(name='Body')
        self.bone = NS(name='thigh.L', parent=None, length=1.0,
            matrix_local=[[1.,0.,0.,0.], [0.,1.,0.,0.], [0.,0.,1.,0.], [0.,0.,0.,1.]],
            use_connect=False, inherit_scale='FULL', use_inherit_rotation=True,
            use_local_location=True, collections=[collection])
        self.constraint = NS(name='ControlOld', type='COPY_ROTATION', target=None,
            subtarget='', mute=False)
        self.pb = NamedID(name='thigh.L', select=True, rotation_mode='XYZ',
            location=[.1,.2,.3], rotation_euler=[.0,.0,.0], rotation_quaternion=[1.,0.,0.,0.],
            rotation_axis_angle=[0.,0.,1.,0.], scale=[1.,1.,1.],
            constraints=NamedCollection([self.constraint]))
        bones = NamedCollection([self.bone]); bones.active = self.bone
        data = NamedID(name='metarig', library=None, override_library=None, is_editable=True,
            users=7, pose_position='POSE', bones=bones, collections_all=NamedCollection([collection]))
        self.ad = Animation(); self.ad.use_tweak_mode = False
        self.ad.drivers, self.ad.nla_tracks = [], []
        self.rig = NamedID(name='Main', type='ARMATURE', data=data,
            animation_data=self.ad, library=None, override_library=None, is_editable=True,
            hide_viewport=False, mode='POSE', pose=NS(bones=NamedCollection([self.pb])))
        self.rig.visible_get = lambda **kwargs: True
        self.rig.animation_data_create = lambda: self.ad
        self.mesh = NamedID(name='Artist Mesh', type='MESH', mode='OBJECT',
            weights=[(0, 'thigh.L', .25), (1, 'thigh.R', .75)],
            keys={'artist': [1., -0., 1e-20]})
        objects = ObjectMap({self.rig.name:self.rig, self.mesh.name:self.mesh})
        objects.active = self.rig
        self.context = NS(mode='POSE', object=self.rig, selected_objects=[self.rig, self.mesh],
            scene=NS(name_full='Scene', objects=objects, frame_current=19, frame_subframe=.5,
                tool_settings=NS(use_keyframe_insert_auto=True),
                character_designer_setup=NS(rig=self.rig),
                character_designer_animation_worklist=NS(rig=self.rig)),
            view_layer=NS(name='ViewLayer', objects=objects, update=Mock()))
        package.animation_rest_pose.get_rig = lambda context: self.rig
        self.bpy.data.objects[:] = [self.rig, self.mesh]
        self.original = self.actions.new('Walk current')
        self.original.slots.new(id_type='OBJECT', name='Main')
        self.original['artist_keys'] = [('untouched', 1.25, -0.0)]
        self.ad.action, self.ad.action_slot = self.original, self.original.slots[0]
        self.enter = patch.object(self.product, '_enter_pose', Mock())
        self.enter.start(); self.addCleanup(self.enter.stop)
        self.neutral = patch.object(self.product, '_neutral',
            Mock(side_effect=lambda context, rig: setattr(self.pb, 'location', [0.,0.,0.])))
        self.neutral.start(); self.addCleanup(self.neutral.stop)
        self.product.start_or_reset(self.context)
        self.draft = self.ad.action
        self.token = self.rig[self.product.TOKEN_KEY]
        self.old_raw = self.draft[self.product.SESSION_KEY]
        self.old_refs = dict(self.draft[self.product.REFS_KEY])
        self.old_name, self.old_fake = self.draft.name, self.draft.use_fake_user
        self.bone.length = 1.4
        self.bone.matrix_local[2][3] = -.2
        self.pb.location = [2.5, -0.0, 1e-20]
        self.pb['artist_unkeyed'] = 'kept'
        self.current_state, self.current_refs = self.product._capture(self.context, self.rig)
        self.author = self.author_snapshot()
        self.forbid_restore = patch.object(self.product, '_restore',
            Mock(side_effect=AssertionError('historical pose restoration forbidden')))
        self.restore_mock = self.forbid_restore.start(); self.addCleanup(self.forbid_restore.stop)
        self.forbid_retire = patch.object(self.product, '_retire',
            Mock(side_effect=AssertionError('carrier retirement forbidden')))
        self.retire_mock = self.forbid_retire.start(); self.addCleanup(self.forbid_retire.stop)

    def author_snapshot(self):
        state, refs = self.product._capture(self.context, self.rig)
        return dict(rest=copy.deepcopy(self.bone.matrix_local), length=self.bone.length,
            pose=state['pose'], pose_properties=dict(self.pb), constraints=state['constraints'], frame=state['frame'],
            subframe=state['subframe'], autokey=state['autokey'], mode=state['mode'],
            weights=copy.deepcopy(self.mesh.weights), keys=copy.deepcopy(self.mesh.keys),
            artist_keys=copy.deepcopy(self.original['artist_keys']),
            data=self.rig.data, active=self.context.object, selected=tuple(self.context.selected_objects))

    def assert_old_and_current_kept(self, bound=True):
        self.assertEqual(self.author_snapshot(), self.author)
        self.assertEqual(self.draft[self.product.SESSION_KEY], self.old_raw)
        self.assertEqual(dict(self.draft[self.product.REFS_KEY]), self.old_refs)
        self.assertEqual((self.draft.name,self.draft.use_fake_user),(self.old_name,self.old_fake))
        self.assertTrue(any(action is self.original for action in self.actions))
        self.assertTrue(any(action is self.draft for action in self.actions))
        if bound:
            self.assertIs(self.ad.action,self.draft)
            self.assertIs(self.ad.action_slot,self.draft.slots[0])
            self.assertEqual(self.rig[self.product.TOKEN_KEY],self.token)
        self.restore_mock.assert_not_called(); self.retire_mock.assert_not_called()

    def test_keyed_archive_keeps_carrier_original_refs_and_current_checkpoint(self):
        self.product._validate_restore(self.context,self.rig,self.current_state,self.current_refs)
        old.authored(self.draft)
        curve=self.draft.layers[0].strips[0].channelbags[0].fcurves[0]
        result=self.product.keep_stale_draft(self.context)
        archive=result['archive']; decoded=self.product.read_archive(archive)
        self.assertIs(result['draft'],self.draft); self.assertIsNot(archive,self.draft)
        self.assertEqual(archive.name,'Base Pose Recovery'); self.assertTrue(archive.use_fake_user)
        self.assertIs(self.draft.layers[0].strips[0].channelbags[0].fcurves[0],curve)
        self.assertEqual(decoded['original'],self.product._unpack(self.old_raw))
        self.assertEqual(decoded['original_refs'],self.old_refs)
        self.assertEqual(decoded['current'],self.current_state)
        self.assertEqual(decoded['current_refs'],self.current_refs)
        self.assertEqual(decoded['current_structure'],self.product._structure(self.rig))
        self.assertIs(decoded['current_refs']['previous_action'],self.draft)
        persisted=archive[self.product.ARCHIVE_REFS_KEY]
        self.assertNotIn('previous_action',persisted['current'])
        self.assertIs(persisted['draft'],self.draft)
        self.assertFalse(any(ref is archive for ref in action_references(persisted)))
        self.assertFalse(decoded['restoration_permission']); self.assertFalse(result['animation_restored'])
        self.assertIsNone(self.ad.action); self.assertNotIn(self.product.TOKEN_KEY,self.rig)
        self.assertGreaterEqual(self.draft.users,1)
        self.assert_old_and_current_kept(bound=False)

    def test_unkeyed_current_pose_is_archived_without_binding_old_Walk(self):
        self.assertFalse(self.product._has_content(self.draft))
        result=self.product.keep_stale_draft(self.context)
        saved=self.product.read_archive(result['archive'])
        location=saved['current']['pose']['thigh.L']['channels']['location']
        self.assertEqual(location,[2.5,-0.0,1e-20])
        self.assertEqual(struct.pack('<d',location[1]),struct.pack('<d',-0.0))
        self.assertIsNone(self.ad.action); self.assertIsNot(self.ad.action,self.original)
        self.assert_old_and_current_kept(bound=False)

    def test_default_strict_paths_still_reject_changed_Rest_and_graph(self):
        for changed in ('Rest','control graph'):
            if changed=='control graph':
                self.bone.length=1.0; self.bone.matrix_local[2][3]=0.0
                self.constraint.name='ControlNew'
            for operation in ('start_or_reset','finish','suspended_for_operation'):
                with self.subTest(changed=changed,operation=operation):
                    with self.assertRaisesRegex(ValueError,'Rest or control graph changed'):
                        if operation=='suspended_for_operation':
                            with self.product.suspended_for_operation(self.context,self.rig):
                                self.fail('stale suspension yielded')
                        else: getattr(self.product,operation)(self.context)
            self.constraint.name='ControlOld'
            self.bone.length=1.4; self.bone.matrix_local[2][3]=-.2
            self.assert_old_and_current_kept()
        self.assertEqual(len(self.actions),2)

    def test_authentication_rejects_token_payload_reference_and_Edit_mode(self):
        cases=[(self.rig,self.product.TOKEN_KEY,None),
               (self.draft,self.product.SESSION_KEY,self.old_raw+'changed')]
        for target,key,bad in cases:
            old_value=target[key]; target[key]=bad
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.product.keep_stale_draft(self.context)
            target[key]=old_value
            self.assert_old_and_current_kept()
        self.original.name='Renamed Walk'
        with self.assertRaisesRegex(ValueError,'ID reference changed'):
            self.product.keep_stale_draft(self.context)
        self.original.name='Walk current'
        self.context.view_layer.name='OtherLayer'
        with self.assertRaisesRegex(ValueError,'scene and view layer'):
            self.product.keep_stale_draft(self.context)
        self.context.view_layer.name='ViewLayer'
        self.context.mode='EDIT_MESH'
        with self.assertRaisesRegex(ValueError,'Edit operation'):
            self.product.keep_stale_draft(self.context)
        self.context.mode='POSE'
        self.assert_old_and_current_kept(); self.assertEqual(len(self.actions),2)

    def test_current_slot_foreign_binding_users_and_local_owner_are_rejected(self):
        slot=self.ad.action_slot; self.ad.action_slot=NS(identifier='foreign',handle=99)
        with self.assertRaisesRegex(ValueError,'binding changed'):
            self.product.keep_stale_draft(self.context)
        self.ad.action_slot=slot
        self.ad.action=self.original; self.ad.action_slot=self.original.slots[0]
        with self.assertRaisesRegex(ValueError,'binding changed'):
            self.product.keep_stale_draft(self.context)
        self.ad.action=self.draft; self.ad.action_slot=slot
        self.draft.binding_users+=1
        with self.assertRaisesRegex(ValueError,'Another user'):
            self.product.keep_stale_draft(self.context)
        self.draft.binding_users-=1
        self.draft.library=object()
        with self.assertRaisesRegex(ValueError,'linked, shared'):
            self.product.keep_stale_draft(self.context)
        self.draft.library=None
        second=NamedID(name='Other Rig',type='ARMATURE',data=self.rig.data)
        self.bpy.data.objects.append(second)
        with self.assertRaisesRegex(ValueError,'Another Object'):
            self.product.keep_stale_draft(self.context)
        self.bpy.data.objects.pop()
        self.assert_old_and_current_kept(); self.assertEqual(len(self.actions),2)

    def test_archive_reader_refuses_checksum_and_persisted_binding_tampering(self):
        archive=self.product.keep_stale_draft(self.context)['archive']
        raw=archive[self.product.ARCHIVE_KEY]
        archive[self.product.ARCHIVE_KEY]=raw+'changed'
        with self.assertRaises(ValueError): self.product.read_archive(archive)
        archive[self.product.ARCHIVE_KEY]=raw
        persisted=archive[self.product.ARCHIVE_REFS_KEY]
        persisted['current']['previous_action']=self.draft
        with self.assertRaisesRegex(ValueError,'current binding'):
            self.product.read_archive(archive)
        del persisted['current']['previous_action']
        self.draft.name='Renamed Draft'
        with self.assertRaisesRegex(ValueError,'reference was renamed'):
            self.product.read_archive(archive)
        self.draft.name=self.old_name
        self.assertFalse(self.product.read_archive(archive)['restoration_permission'])
        self.assert_old_and_current_kept(bound=False)

    def test_new_archive_allocation_or_write_failure_keeps_every_old_record(self):
        actual_read=self.product.read_archive
        def mismatched_readback(archive):
            saved=actual_read(archive)
            saved['original']=dict(saved['original'],structure='wrong-readback')
            return saved
        for edge in ('allocate','property','readback'):
            with self.subTest(edge=edge):
                if edge=='allocate': self.actions.fail_new=True
                elif edge=='property': self.actions.fail_archive_property=self.product.ARCHIVE_KEY
                if edge=='readback':
                    with patch.object(self.product,'read_archive',side_effect=mismatched_readback):
                        with self.assertRaisesRegex(ValueError,'read back exactly'):
                            self.product.keep_stale_draft(self.context)
                else:
                    with self.assertRaisesRegex(RuntimeError,'archive'):
                        self.product.keep_stale_draft(self.context)
                self.assert_old_and_current_kept(); self.assertEqual(len(self.actions),2)

    def test_failure_after_detach_and_token_removal_restores_binding_and_slot(self):
        self.rig.fail_after_pop=self.product.TOKEN_KEY
        with self.assertRaisesRegex(RuntimeError,'after token removal'):
            self.product.keep_stale_draft(self.context)
        self.assert_old_and_current_kept()
        self.assertEqual(len(self.actions),2)
        self.assertEqual(self.draft.users-int(self.draft.use_fake_user),1)


if __name__=='__main__':
    unittest.main()
