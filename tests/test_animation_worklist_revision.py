"""Current-Rig revision transactions with exact-ID fakes, no Blender runtime."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_worklist_revision.py'


class ID(dict):
    __hash__ = object.__hash__
    __eq__ = object.__eq__
    def __init__(self, name, **fields):
        super().__init__()
        self.name = name
        self.library = None
        self.override_library = None
        self.__dict__.update(fields)
    def __bool__(self):
        return True
    @property
    def name_full(self):
        return self.name
    def as_pointer(self):
        return id(self)


class Armature(ID):
    pass


class Action(ID):
    def __init__(self, name, db):
        super().__init__(name, db=db, use_fake_user=False, asset_data=None, curves=[],
                         slots=[NS(handle=1, target_id_type='OBJECT')], dependencies=[], frame_range=(1., 31.))
    @property
    def users(self):
        return self.db.references_to(self) + int(self.use_fake_user)
    def copy(self):
        action = self.db.actions.new(self.name + '.copy')
        action.update(self)
        action.curves = copy.deepcopy(self.curves)
        action.slots = copy.deepcopy(self.slots)
        action.dependencies = list(self.dependencies)
        action.use_fake_user = self.use_fake_user
        return action


class Actions(list):
    def __init__(self, db):
        self.db = db
    def new(self, name):
        action = Action(name, self.db)
        self.append(action)
        return action
    def remove(self, value, *, do_unlink=False):
        if do_unlink or value.users:
            raise ValueError('foreign Action users')
        for i, item in enumerate(self):
            if item is value:
                del self[i]; return
        raise ValueError('missing Action')


class Libraries(list):
    def __init__(self, db):
        self.db = db
    def remove(self, value, *, do_unlink=False):
        if do_unlink or any(action.library is value for action in self.db.actions):
            raise ValueError('foreign Library users')
        for i, item in enumerate(self):
            if item is value:
                del self[i]; return


class Rows(list):
    def __init__(self, defaults):
        self.defaults = defaults
    def add(self):
        values = dict(self.defaults)
        props = [NS(identifier=key, is_readonly=False, is_array=False, type='POINTER' if isinstance(value, ID) else 'STRING')
                 for key, value in values.items()]
        row = NS(**values, bl_rna=NS(properties=props))
        self.append(row)
        return row


class DB:
    @staticmethod
    def ids(value):
        if isinstance(value, ID):
            yield value
        elif hasattr(value, 'items'):
            for item in value.values():
                yield from DB.ids(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from DB.ids(item)
    def __init__(self):
        self.actions, self.libraries = Actions(self), Libraries(self)
        self.rows, self.rig = [], None
    def references_to(self, value):
        count = sum(item is value for action in self.actions for item in self.ids(dict(action)))
        count += sum(getattr(row, key, None) is value for row in self.rows for key in ('custom_action', 'source_action'))
        if self.rig is not None:
            count += self.rig.animation_data.action is value
        return count
    def user_map(self):
        result = {}
        if self.rig is not None:
            result[self.rig.data] = {self.rig}
        for action in self.actions:
            for value in list(self.ids(dict(action))) + action.dependencies:
                result.setdefault(value, set()).add(action)
        return result


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = DB()
        self.bpy = NS(types=NS(ID=ID, Armature=Armature, Action=Action),
                      data=self.db, path=NS(abspath=lambda value: value))
        patcher = patch.dict(sys.modules, {'bpy': self.bpy}); patcher.start(); self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location('_current_revision_test', SOURCE)
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        self.data = Armature('Current102')
        self.pb = ID('thigh.L', location=[3., 4., 5.], rotation_euler=[0., 0., 0.],
                     rotation_quaternion=[1., 0., 0., 0.], rotation_axis_angle=[0., 0., 1., 0.],
                     scale=[1., 1., 1.], rotation_mode='QUATERNION', constraints={},
                     lock_location=[False]*3, lock_rotation=[False]*3, lock_scale=[False]*3,
                     lock_rotation_w=False, lock_rotations_4d=False)
        self.pb['ik_fk'] = .7
        self.pb.path_from_id = lambda: 'pose.bones["thigh.L"]'
        self.ad = NS(action=None, action_slot=None, use_nla=False, action_blend_type='REPLACE', action_influence=1.)
        self.rig = ID('Rig', data=self.data, animation_data=self.ad, type='ARMATURE',
                      pose=NS(bones={'thigh.L': self.pb}), select_set=Mock(), hide_set=Mock())
        self.rig.animation_data_create = lambda: self.ad
        self.rig.path_resolve = self.resolve
        self.rig['old_session'] = 'immutable historical record'
        self.rig['old_refs'] = {'id_data': self.data, 'id_rig': self.rig}
        self.db.rig = self.rig
        self.context = NS(scene=ID('Scene', tool_settings=NS(use_keyframe_insert_auto=True)),
            view_layer=NS(name='ViewLayer', objects=NS(active=self.rig), update=Mock()),
            selected_objects=[], window_manager=NS(character_designer_animation=NS(target=None)))
        self.defaults = dict(current_rig_revision='', item_id='', work_item_id='', clip_key='', name='', rig=self.rig,
            custom_action=None, source_action=None, custom_slot=1, source_slot=1, source_file='', source_hash='',
            source_data=None, custom_data=None, new_animation=False, new_action_id='', manifest_path='', link_identity='',
            side='CUSTOM', source_baseline_receipt='stale-receipt', last_synced_receipt='stale-sync', scan_state='CHANGED',
            scan_reason='stale', sync_selected=True, b_edited=True, composition_changed=False,
            effective_inputs_changed=True, export_needed=True, composition_json='', composition_sha256='', application_role='')
        self.rows = Rows(self.defaults); self.db.rows = self.rows
        self.old_actions = []
        for name in ('Walk_N', 'Idle'):
            action = self.db.actions.new(name + ' Custom')
            action.curves = [NS(data_path='pose.bones["thigh.L"].location', array_index=0, points=[(1., 2.), (31., 7.)]),
                             NS(data_path='pose.bones["thigh.L"]["ik_fk"]', array_index=0, points=[(1., 1.)])]
            action['old_motion_data'] = self.data
            action['author_note'] = name + ' user edits'
            self.old_actions.append(action)
            row = self.rows.add(); row.item_id = name; row.name = name
            row.custom_action = action
        self.saved = NS(rig=self.rig, items=self.rows, active_index=0, status='before', has_error=False,
                        workspace_id='x', model_sha256='historic-model')
        self.api = NS(worklist=NS(state=lambda c: self.saved, _idle=Mock(), _slot=self.slot,
                        _item=lambda saved, item_id: next(row for row in saved.items if row.item_id == item_id),
                        _linked_baseline=self.baseline, _check_baseline=self.check_baseline),
            target=NS(SESSION='old_session', REFS='old_refs', _action_motion_keys=lambda: ('old_motion_data',),
                      _data_reference_keys=lambda owner, data: {key for key, value in owner.items() if any(x is data for x in DB.ids(value))},
                      _capture=self.capture, _validate_capture=Mock(), _apply_capture=self.apply),
            ua=NS(active_preview=lambda rig: None, _rest_state=lambda data: {'current': 102},
                  _set_playing=Mock(), _set_frame=Mock()),
            pose=NS(_admission=Mock(), active=lambda rig: False, _structure=lambda rig: 'current graph'),
            adoption=NS(verified_revision=lambda rig: True, recovery_owners=self.owners),
            body=NS(_hash=self.props_hash), original=NS(active=lambda rig: False),
            composition=NS(active=lambda rig: False),
            fingerprint=NS(fingerprint_action=self.fingerprint, _selected_curves=lambda action, handle: action.curves))
        self.m._modules = lambda: self.api
        self.base_actions = list(self.db.actions)
        self.base_rows = [self.m._row(row) for row in self.rows]
        self.before_owners = self.owners(self.rig)
        self.apply_calls = []
    def resolve(self, path):
        if path == 'pose.bones["thigh.L"].location': return self.pb.location
        if path == 'pose.bones["thigh.L"]["ik_fk"]': return self.pb['ik_fk']
        if path == 'location': return [0., 0., 0.]
        raise ValueError(path)
    def slot(self, action, handle=None):
        slots = [slot for slot in action.slots if handle is None or slot.handle == handle]
        if len(slots) != 1: raise ValueError('slot changed')
        return slots[0]
    def fingerprint(self, action, handle):
        values = [(f.data_path, f.array_index, f.points) for f in action.curves]
        return {'fingerprint_known': True, 'action_fingerprint': hashlib.sha256(repr(values).encode()).hexdigest()}
    def props_hash(self, value):
        def plain(v):
            if isinstance(v, ID): return [type(v).__name__, v.name_full]
            if hasattr(v, 'items'): return {k:plain(x) for k,x in v.items()}
            if isinstance(v, (list,tuple)): return [plain(x) for x in v]
            return v
        return hashlib.sha256(json.dumps(plain(value), sort_keys=True).encode()).hexdigest()
    def owners(self, rig):
        return {'owners': sorted(action.name for action in self.db.user_map().get(self.data, ()) if isinstance(action, Action)),
                'raw': rig['old_session'], 'refs': self.props_hash(rig['old_refs'])}
    def capture(self, context, rig):
        fields = ('location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale','rotation_mode')
        entry = dict(target=rig, data=self.data,
            snapshot={'pose':{'thigh.L':{key:copy.deepcopy(getattr(self.pb,key)) for key in fields}},
                      'autokey':context.scene.tool_settings.use_keyframe_insert_auto},
            properties={'thigh.L':dict(self.pb)}, locks={'thigh.L':{key:copy.deepcopy(getattr(self.pb,key)) for key in
                ('lock_location','lock_rotation','lock_scale','lock_rotation_w','lock_rotations_4d')}}, constraints={'thigh.L':{}})
        return dict(targets=[entry], metadata=[], context={}, action=rig.animation_data.action)
    def apply(self, context, rig, captured):
        self.apply_calls.append(captured)
        self.ad.action = captured['action']
        # Simulate the existing restore helper's final frame_set overwriting
        # an unkeyed raw Pose override when a keyed current Action is bound.
        self.pb.location = [99., 99., 99.] if self.ad.action else [0.,0.,0.]
    def baseline(self, saved, temporary, item_id, name, *, _ownership):
        path = str(Path(self.temp.name)/(item_id+'.blend'))
        Path(path).write_bytes(b'owned-source')
        _ownership.update(path=path, input_action=temporary)
        library = ID('Library', filepath=path, users=0); self.db.libraries.append(library)
        source = temporary.copy(); source.library=library; source.use_fake_user=False
        _ownership.update(action=source, library=library, load_result=NS(actions=[source]))
        return source, path, hashlib.sha256(b'owned-source').hexdigest()
    def check_baseline(self, row):
        self.assertEqual(Path(row.source_file).read_bytes(), b'owned-source')
        self.assertIsNotNone(row.source_action.library)
    def assert_original(self):
        self.assertEqual(self.rig['old_session'], 'immutable historical record')
        self.assertIs(self.rig['old_refs']['id_data'], self.data)
        self.assertEqual(self.owners(self.rig), self.before_owners)
        self.assertEqual([self.m._row(row) for row in self.rows], self.base_rows)
        for action in self.old_actions:
            self.assertIs(action['old_motion_data'], self.data)
    def test_batch_preserves_old_rows_and_native_data_owners(self):
        item = self.m.create(self.context, 'Walk_N')
        self.assertEqual([row.name for row in self.rows], ['Walk_N','Idle'])
        self.assertEqual(len({row.item_id for row in self.rows}), 2)
        self.assertEqual(len({row.current_rig_revision for row in self.rows}), 2)
        self.assertEqual(self.owners(self.rig), self.before_owners)
        value = self.m.validate(self.context, item.item_id)
        self.assertEqual(value['old_rows'], self.base_rows)
        self.assertTrue(all(not isinstance(v, Armature) for v in item.custom_action[self.m.CHECKPOINT][self.m.REFS].values()))
        self.assertNotIn('old_motion_data',item.custom_action)
        self.assertEqual(item.source_baseline_receipt,''); self.assertEqual(item.last_synced_receipt,'')
        self.assertFalse(item.sync_selected)
        for old,new in zip(self.old_actions,self.rows):
            self.assertEqual(self.fingerprint(old,1),self.fingerprint(new.custom_action,1))
    def test_ui_scan_state_is_mutable_but_identity_is_not(self):
        item = self.m.create(self.context,'Idle')
        item.name='My Idle'; item.scan_state='CHANGED'; item.scan_reason='edited'; item.sync_selected=True; item.b_edited=True
        self.m.validate(self.context,item.item_id)
        item.custom_slot=7
        with self.assertRaises(ValueError): self.m.validate(self.context,item.item_id)
    def test_row_scalar_RNA_without_is_array_and_actual_array(self):
        scalar=NS(identifier='current_rig_revision',is_readonly=False,type='STRING')
        pointer=NS(identifier='rig',is_readonly=False,type='POINTER')
        array=NS(identifier='coordinates',is_readonly=False,type='FLOAT',is_array=True)
        item=NS(current_rig_revision='',rig=self.rig,coordinates=(1.,2.,3.),
                bl_rna=NS(properties=[scalar,pointer,array]))
        self.assertFalse(hasattr(scalar,'is_array'))
        self.assertFalse(hasattr(pointer,'is_array'))
        result=self.m._row(item)
        self.assertEqual(result,dict(current_rig_revision='',rig=self.rig,coordinates=[1.,2.,3.]))
        self.assertIs(result['rig'],self.rig)
        self.assertIsInstance(result['coordinates'],list)
    def test_history_reference_cannot_be_resolved_from_a_new_armature(self):
        refs={}; encoded=self.m._encode(self.data,refs,self.rig['old_refs'],self.rig)
        self.assertEqual(encoded,{'$history_ref':'id_data'}); self.assertEqual(refs,{})
        with self.assertRaises(ValueError):
            self.m._decode(encoded,refs,{'id_data':Armature('replacement')},self.rig)
        with self.assertRaises(ValueError):
            self.m._encode(Armature('not-history'),{},self.rig['old_refs'],self.rig)
    def test_missing_and_uncovered_paths_reject_before_creation(self):
        for path,index in [('location',0),('pose.bones["missing"].location',0),('pose.bones["thigh.L"].location',5)]:
            self.old_actions[0].curves[0].data_path=path; self.old_actions[0].curves[0].array_index=index
            with self.assertRaises(ValueError): self.m.create(self.context,'Walk_N')
            self.assertEqual(len(self.db.actions),2)
    def test_unknown_ID_property_or_native_marker_dependency_is_preserved(self):
        camera=ID('Author camera')
        self.old_actions[0]['author_object']=camera
        with self.assertRaisesRegex(ValueError,'unknown Action ID'): self.m.create(self.context,'Walk_N')
        self.assertIs(self.old_actions[0]['author_object'],camera)
        del self.old_actions[0]['author_object']
        self.old_actions[0].dependencies=[camera]
        with self.assertRaisesRegex(ValueError,'native ID dependencies'): self.m.create(self.context,'Walk_N')
        self.assertEqual(self.old_actions[0].dependencies,[camera]); self.assertEqual(len(self.db.actions),2)
    def test_failed_partial_library_load_removes_only_receipted_IDs(self):
        real=self.baseline
        foreign=[]
        def fail(*args,**kwargs):
            real(*args,**kwargs)
            foreign.append(self.db.actions.new('Foreign handler Action'))
            raise ValueError('partial load failure')
        self.api.worklist._linked_baseline=fail
        with self.assertRaisesRegex(ValueError,'partial load failure'): self.m.create(self.context,'Walk_N')
        self.assert_original(); self.assertIn(foreign[0],self.db.actions)
        self.assertEqual(len(self.db.actions),3); self.assertFalse(list(Path(self.temp.name).glob('*.blend')))
    def test_failed_first_activation_restores_rows_and_keeps_foreign_ID(self):
        foreign=[]
        def frame(*args):
            self.pb.location=[50.,50.,50.]
            foreign.append(self.db.actions.new('Foreign frame Action'))
            raise ValueError('frame fault')
        self.api.ua._set_frame=frame
        with self.assertRaisesRegex(ValueError,'frame fault'): self.m.create(self.context,'Walk_N',activate_new=True)
        self.assert_original(); self.assertEqual(self.pb.location,[3.,4.,5.])
        self.assertIn(foreign[0],self.db.actions); self.assertEqual(len(self.db.actions),3)
        self.assertFalse(list(Path(self.temp.name).glob('*.blend')))
    def test_keyed_current_action_failure_replays_unkeyed_pose_override(self):
        item=self.m.create(self.context,'Walk_N')
        self.ad.action=item.custom_action; self.ad.action_slot=item.custom_action.slots[0]
        self.pb.location=[12.,13.,14.]; self.pb['ik_fk']=.23
        self.api.ua._set_frame=Mock(side_effect=ValueError('frame fault'))
        with self.assertRaisesRegex(ValueError,'frame fault'): self.m.activate(self.context,item.item_id)
        self.assertIs(self.ad.action,item.custom_action)
        self.assertEqual(self.pb.location,[12.,13.,14.]); self.assertEqual(self.pb['ik_fk'],.23)
        self.assertEqual(self.owners(self.rig),self.before_owners)
    def test_foreign_source_user_preserves_receipted_cache_and_library(self):
        real=self.baseline
        retained={}
        def fail(*args,**kwargs):
            source,path,_=real(*args,**kwargs)
            holder=self.db.actions.new('Foreign source holder')
            holder['keep_source']=source
            retained.update(source=source,path=path,library=source.library,holder=holder)
            raise ValueError('after-load fault')
        self.api.worklist._linked_baseline=fail
        with self.assertRaisesRegex(RuntimeError,'rollback needs review'):
            self.m.create(self.context,'Walk_N')
        self.assert_original()
        self.assertIs(retained['holder']['keep_source'],retained['source'])
        self.assertIn(retained['source'],self.db.actions)
        self.assertIn(retained['library'],self.db.libraries)
        self.assertEqual(Path(retained['path']).read_bytes(),b'owned-source')
    def test_remove_action_resolves_native_pointer_and_restores_fake_on_failure(self):
        action=self.db.actions.new('Owned')
        action.use_fake_user=True
        alias=NS(as_pointer=action.as_pointer)
        with patch.object(self.db.actions,'remove',side_effect=ValueError('native remove fault')):
            with self.assertRaisesRegex(ValueError,'native remove fault'):
                self.m._remove_action(alias)
        self.assertTrue(action.use_fake_user)
        self.m._remove_action(alias)
        self.assertNotIn(action,self.db.actions)
    def test_foreign_library_user_prevents_native_remove(self):
        library=ID('Owned library',users=1)
        self.db.libraries.append(library)
        with patch.object(self.db.libraries,'remove') as remove:
            with self.assertRaisesRegex(ValueError,'foreign user'):
                self.m._remove_library(library)
        remove.assert_not_called()
        self.assertIn(library,self.db.libraries)
    def test_restore_helper_failure_still_reasserts_raw_and_restores_ui(self):
        item=self.m.create(self.context,'Walk_N')
        self.ad.action=item.custom_action
        self.pb.location=[12.,13.,14.]
        self.context.scene.tool_settings.use_keyframe_insert_auto=True
        before=(self.saved.active_index,item.side,self.saved.status,self.saved.has_error,
                self.context.window_manager.character_designer_animation.target)
        self.api.ua._set_frame=Mock(side_effect=ValueError('activation frame fault'))
        def fail_restore(context,rig,captured):
            self.assertFalse(context.scene.tool_settings.use_keyframe_insert_auto)
            self.assertFalse(captured['targets'][0]['snapshot']['autokey'])
            self.ad.action=captured['action']
            self.pb.location=[99.,99.,99.]
            raise ValueError('rollback frame fault')
        self.api.target._apply_capture=fail_restore
        with self.assertRaisesRegex(RuntimeError,'rollback needs review') as caught:
            self.m.activate(self.context,item.item_id,'SOURCE')
        self.assertEqual(str(caught.exception.__cause__),'activation frame fault')
        self.assertEqual(self.pb.location,[12.,13.,14.])
        self.assertTrue(self.context.scene.tool_settings.use_keyframe_insert_auto)
        self.assertEqual((self.saved.active_index,item.side,self.saved.status,self.saved.has_error,
            self.context.window_manager.character_designer_animation.target),before)
    def test_native_dependency_detection_uses_pointer_not_wrapper_identity(self):
        action=self.old_actions[0]
        alias=ID('Different RNA wrapper')
        alias.as_pointer=action.as_pointer
        camera=ID('Camera')
        self.db.user_map=lambda:{camera:{alias}}
        with self.assertRaisesRegex(ValueError,'native ID dependencies: Camera'):
            self.m._no_native_dependencies(action)


if __name__ == '__main__':
    unittest.main()
