"""Focused owner admission for pose-only wrist edits on native local Actions.

The actual guard, browser hint policy and pose-data isolation check run against
native-ID doubles. Action layers/curves deliberately reject reads outside the
separate managed-Custom tests. No worklist or Action authoring is performed.
"""
import ast
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(os.environ.get('CD_WRIST_OWNER_TEST_SOURCE',
    Path(__file__).resolve().parents[1] / 'addons/character_designer'))


class ID(dict):
    def __init__(self, name='ID', **fields):
        super().__init__()
        self.name = name
        self.library = self.override_library = self.asset_data = None
        self.is_editable = True
        self.__dict__.update(fields)

    def __bool__(self):
        return True

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other

    __hash__ = object.__hash__


class Action(ID):
    is_action_layered = True
    use_fake_user = True

    def __init__(self, name='Native Action', **kwargs):
        super().__init__(name, **kwargs)
        self._layers = None

    @property
    def layers(self):
        if self._layers is None:
            raise AssertionError('Pose-only admission read Action layer/curve contents')
        return self._layers


def definitions(module, filename, functions=(), assignments=()):
    nodes = []
    for node in ast.parse((SOURCE / filename).read_bytes()).body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            nodes.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(part, ast.Name) and part.id in assignments
                for target in node.targets for part in ast.walk(target)):
            nodes.append(node)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE / filename), 'exec'), module.__dict__)


class OwnerAdmissionTests(unittest.TestCase):
    def fixture(self):
        package_name = '_unkeyed_wrist_' + uuid.uuid4().hex
        package = ModuleType(package_name)
        package.__path__ = [str(SOURCE)]
        browser = ModuleType(package_name + '.animation_action_browser')
        definitions(browser, 'animation_action_browser.py', ('_owned_hint', '_foreign_hint'),
                    ('PREFIX', 'ROLE_KEY', 'LABEL_KEY', 'CLIP_KEY', 'OWNER_KEY', 'DATA_KEY'))
        package.animation_action_browser = browser
        rig = ID('Artist Rig', type='ARMATURE', data=ID('Artist Rig Data'))
        action = Action()
        slot = NS(handle=17, id_data=action, target_id_type='OBJECT')
        rig.animation_data = NS(action=action, action_slot=slot, use_tweak_mode=False,
            use_nla=False, nla_tracks=[], action_blend_type='REPLACE', action_influence=1.)
        saved = NS(items=[], retired_items=[], workspace_path='', active_index=-1)
        context = NS(object=rig, mode='POSE', screen=NS(is_animation_playing=False),
            scene=NS(objects={rig.name: rig}, frame_current=12, frame_subframe=.375,
                     tool_settings=NS(use_keyframe_insert_auto=False),
                     keying_sets=NS(active=None), keying_sets_all=NS(active=None)),
            view_layer=NS(objects={rig.name: rig}))
        bpy = NS(data=NS(objects=[rig], scenes=[context.scene]), types=NS(Action=Action))
        pose = ModuleType(package_name + '.animation_pose_test')
        pose.bpy = bpy
        definitions(pose, 'animation_pose_test.py', ('_pose_owner',), ('SESSION_KEY', 'REFS_KEY'))
        pose.active, pose.natural_active = Mock(return_value=False), Mock(return_value=False)
        worklist = ModuleType(package_name + '.animation_worklist')
        worklist.__package__ = package_name
        worklist.links = NS(ACTION_KEY='managed-action-link')
        worklist._idle = Mock()
        worklist.state = Mock(return_value=saved)
        worklist.current = Mock(return_value=None)
        definitions(worklist, 'animation_worklist.py', ('_rigging_module',))
        revision = NS(TOKEN='current-token', CHECKPOINT='current-checkpoint',
                      RECORD='retained-record', REFS='retained-refs')
        target = ModuleType(package_name + '.animation_worklist_target')
        definitions(target, 'animation_worklist_target.py', assignments=('ACTION_RIG_KEY', 'ACTION_MOTION_KEY'))
        api = NS(bpy=bpy, worklist=worklist, pose=pose, revision=revision,
            original=NS(active=Mock(return_value=False)), rest=NS(is_rest=Mock(return_value=False)),
            composition=NS(active=Mock(return_value=False)), ua=NS(active_preview=Mock(return_value=None)),
            forearm=NS(_SESSION=None))
        modules = {package_name: package, browser.__name__: browser, target.__name__: target,
                   package_name + '.unity_animation_rigging': ModuleType(package_name + '.unity_animation_rigging')}
        module_patch = patch.dict(sys.modules, modules)
        module_patch.start()
        self.addCleanup(module_patch.stop)
        path = SOURCE / 'limb_ik_wrist_owner_guard.py'
        spec = importlib.util.spec_from_file_location(package_name + '.guard', path)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        guard._modules = Mock(return_value=api)
        return NS(rig=rig, data=rig.data, action=action, slot=slot, saved=saved,
                  context=context, api=api, browser=browser, guard=guard)

    def witness(self, f):
        return (dict(f.rig), dict(f.action), tuple(f.saved.items), tuple(f.saved.retired_items),
                f.saved.workspace_path, f.saved.active_index, f.rig.animation_data.action,
                f.rig.animation_data.action_slot)

    def test_native_local_action_off_has_pose_only_owner_without_content_reads_or_writes(self):
        f = self.fixture()
        before = self.witness(f)
        owner = f.guard.prepare(f.context, f.rig)
        self.assertTrue(owner['unkeyed_local'])
        self.assertIs(owner['rig'], f.rig)
        self.assertIs(owner['data'], f.data)
        self.assertIs(owner['action'], f.action)
        self.assertEqual(owner['slot'], f.slot.handle)
        self.assertFalse(owner['autokey'])
        self.assertIsNone(owner['row'])
        self.assertIsNone(owner['source'])
        self.assertIsNone(owner['checkpoint'])
        self.assertIsNone(owner['bag'])
        self.assertEqual(owner['expected_action_users'], 0)
        self.assertIs(f.guard.validate(f.context, owner), owner)
        self.assertEqual(self.witness(f), before)

    def test_own_work_action_can_autokey_only_in_the_current_object_slot(self):
        f = self.fixture()
        f.action.update({f.browser.ROLE_KEY: 'WORK', f.browser.OWNER_KEY: f.rig.name,
                         f.browser.DATA_KEY: f.data.name})
        f.slot.target_id_type = 'UNSPECIFIED'
        owner = f.guard.prepare(f.context, f.rig)
        self.assertTrue(owner['unkeyed_local'])
        self.assertFalse(owner['autokey'])
        f.context.scene.tool_settings.use_keyframe_insert_auto = True
        bag = NS(slot_handle=f.slot.handle)
        f.action._layers = [NS(strips=[NS(type='KEYFRAME', channelbags=[bag])])]
        f.action.users = 2
        keyed = f.guard.prepare(f.context, f.rig)
        self.assertIs(keyed['bag'], bag)
        self.assertTrue(keyed['autokey'])
        self.assertEqual(keyed['expected_action_users'], 2)
        self.assertIs(f.guard.validate(f.context, keyed), keyed)

    def test_local_keying_allows_metadata_and_other_slots_but_rejects_shared_native_slot(self):
        f = self.fixture()
        slot = f.slot.handle
        f.action.users = 4
        user = ID('Other rig', animation_data=NS(action=f.action, action_slot_handle=slot + 1, nla_tracks=[]))
        f.api.bpy.data.user_map = Mock(return_value={f.action: {user, f.rig, ID('metadata')}})
        f.guard._local_key_owner(f.rig, f.action, slot, f.api)
        user.animation_data.action_slot_handle = slot
        with self.assertRaisesRegex(ValueError, 'Another animation owner'):
            f.guard._local_key_owner(f.rig, f.action, slot, f.api)
        user.animation_data.action = None
        nested = NS(action=f.action, action_slot_handle=slot)
        user.animation_data.nla_tracks = [NS(strips=[NS(action=None, strips=[nested])])]
        with self.assertRaisesRegex(ValueError, 'NLA'):
            f.guard._local_key_owner(f.rig, f.action, slot, f.api)

    def test_native_action_never_falls_back_with_active_or_retained_managed_selection(self):
        for kind in ('row', 'items'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'row':
                    f.api.worklist.current.return_value = NS(rig=f.rig, side='SOURCE')
                else:
                    f.saved.items.append(object())
                before = self.witness(f)
                with self.assertRaises(ValueError):
                    f.guard.prepare(f.context, f.rig)
                self.assertEqual(self.witness(f), before)

    def test_connected_empty_list_keeps_native_work_editable_with_and_without_autokey(self):
        for auto in (False, True):
            for classified in (False, True):
                with self.subTest(auto=auto, classified=classified):
                    f = self.fixture()
                    f.saved.workspace_path = 'connected/workspace.json'
                    f.context.scene.tool_settings.use_keyframe_insert_auto = auto
                    f.action.users = 2
                    f.action._layers = [NS(strips=[NS(type='KEYFRAME',
                        channelbags=[NS(slot_handle=f.slot.handle)])])]
                    f.rig['character_designer_worklist_main_session_v1'] = 'historical session, no refs'
                    f.rig[f.api.worklist.links.ACTION_KEY] = Action('Different linked Action')
                    if classified:
                        f.action.update({f.browser.ROLE_KEY: 'WORK', f.browser.OWNER_KEY: f.rig.name,
                            f.browser.DATA_KEY: f.data.name, f.api.revision.TOKEN: uuid.uuid4().hex,
                            f.api.revision.CHECKPOINT: Action('Historical checkpoint')})
                    before = self.witness(f)
                    owner = f.guard.prepare(f.context, f.rig)
                    self.assertTrue(owner['local_action'])
                    self.assertEqual(owner['unkeyed_local'], not auto)
                    self.assertIsNone(owner['checkpoint'])
                    self.assertIs(f.guard.validate(f.context, owner), owner)
                    self.assertEqual(self.witness(f), before)

    def test_connected_list_respects_other_scene_rows_other_rigs_and_orphaned_managed_actions(self):
        for kind in ('active-source', 'active-custom', 'retired-source', 'retired-custom',
                     'foreign-link', 'revision-token', 'revision-checkpoint', 'motion-rest', 'motion-rig',
                     'wrong-motion-rig', 'unrelated'):
            with self.subTest(kind=kind):
                f = self.fixture()
                f.saved.workspace_path = 'existing-workspace'
                other_rig = ID('Other', type='ARMATURE', data=ID('Other data'))
                f.api.bpy.data.objects.append(other_rig)
                row = NS(rig=other_rig, custom_action=Action('Other Custom'), custom_slot=32,
                         source_action=Action('Other Source'), source_slot=33)
                other = NS(items=[], retired_items=[])
                f.api.bpy.data.scenes.append(NS(character_designer_animation_worklist=other))
                if kind.startswith(('active-', 'retired-')) or kind == 'unrelated':
                    (other.retired_items if kind.startswith('retired') else other.items).append(row)
                    if kind != 'unrelated':
                        setattr(row, 'source_action' if kind.endswith('source') else 'custom_action', f.action)
                elif kind == 'foreign-link':
                    other_rig[f.api.worklist.links.ACTION_KEY] = f.action
                elif kind in ('motion-rest', 'motion-rig', 'wrong-motion-rig'):
                    if kind == 'motion-rest':
                        f.action['character_designer_worklist_motion_rest_v1'] = 'retained'
                    else:
                        f.action['character_designer_worklist_motion_rig_v1'] = (
                            f.rig if kind == 'motion-rig' else other_rig)
                    if kind == 'wrong-motion-rig':
                        f.action.update({f.browser.ROLE_KEY: 'WORK', f.browser.OWNER_KEY: f.rig.name,
                                         f.browser.DATA_KEY: f.data.name})
                else:
                    key = f.api.revision.TOKEN if kind == 'revision-token' else f.api.revision.CHECKPOINT
                    f.action[key] = 'retained'
                before = self.witness(f)
                if kind == 'unrelated':
                    self.assertTrue(f.guard.prepare(f.context, f.rig)['local_action'])
                else:
                    with self.assertRaises(ValueError):
                        f.guard.prepare(f.context, f.rig)
                self.assertEqual(self.witness(f), before)

    def test_source_asset_override_archived_input_checkpoint_and_foreign_hints_refuse(self):
        for kind in ('source', 'asset', 'override', 'not_editable', 'ARCHIVED', 'INPUT', 'CHECKPOINT',
                     'SOURCE', 'foreign_owner', 'foreign_data', 'record', 'refs', 'pose_session', 'pose_refs',
                     'rigging_capture', 'rigging_input'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind in ('source', 'asset', 'override', 'not_editable'):
                    setattr(f.action, {'source': 'library', 'asset': 'asset_data', 'override': 'override_library',
                                       'not_editable': 'is_editable'}[kind], False if kind == 'not_editable' else object())
                elif kind in ('ARCHIVED', 'INPUT', 'CHECKPOINT', 'SOURCE'):
                    f.action[f.browser.ROLE_KEY] = kind
                elif kind in ('foreign_owner', 'foreign_data'):
                    f.action[f.browser.OWNER_KEY] = f.rig.name
                    f.action[f.browser.DATA_KEY] = f.data.name
                    f.action[f.browser.OWNER_KEY if kind == 'foreign_owner' else f.browser.DATA_KEY] = 'another'
                else:
                    key = {'record': f.api.revision.RECORD, 'refs': f.api.revision.REFS,
                           'pose_session': f.api.pose.SESSION_KEY, 'pose_refs': f.api.pose.REFS_KEY,
                           'rigging_capture': 'character_designer_rigging_capture_v1',
                           'rigging_input': 'character_designer_rigging_input_action_v1'}[kind]
                    f.action[key] = 'retained-author-data'
                before = self.witness(f)
                with self.assertRaises(ValueError):
                    f.guard.prepare(f.context, f.rig)
                self.assertEqual(self.witness(f), before)

    def test_retained_link_to_current_action_refuses_but_different_history_stays_passive(self):
        for current_link in (True, False):
            with self.subTest(current_link=current_link):
                f = self.fixture()
                historical = Action('Retained historical Action')
                f.rig[f.api.worklist.links.ACTION_KEY] = f.action if current_link else historical
                f.rig['retained-passive-refs'] = {'rig': f.rig, 'data': f.data, 'action': historical}
                # Passive ID references do not create another posed Object.
                f.data.users = 4
                f.action.users = 7
                before = self.witness(f)
                if current_link:
                    with self.assertRaises(ValueError):
                        f.guard.prepare(f.context, f.rig)
                else:
                    owner = f.guard.prepare(f.context, f.rig)
                    self.assertTrue(owner['unkeyed_local'])
                    self.assertIsNone(owner['source'])
                    self.assertIsNone(owner['checkpoint'])
                    self.assertIs(f.guard.validate(f.context, owner), owner)
                self.assertEqual(self.witness(f), before)

    def test_actual_shared_rig_data_and_invalid_native_slots_refuse(self):
        for kind in ('shared_data', 'no_slot', 'foreign_action', 'non_object_slot'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'shared_data':
                    f.api.bpy.data.objects.append(ID('Other Rig', type='ARMATURE', data=f.data))
                elif kind == 'no_slot':
                    f.rig.animation_data.action_slot = None
                elif kind == 'foreign_action':
                    f.slot.id_data = Action('Other Action')
                else:
                    f.slot.target_id_type = 'MESH'
                with self.assertRaises(ValueError):
                    f.guard.prepare(f.context, f.rig)

    def test_related_retired_rig_source_and_custom_refuse_but_unrelated_retired_row_is_kept(self):
        for kind in ('rig', 'source_action', 'custom_action', 'unrelated'):
            with self.subTest(kind=kind):
                f = self.fixture()
                row = NS(rig=object(), source_action=object(), custom_action=object())
                if kind != 'unrelated':
                    setattr(row, kind, f.rig if kind == 'rig' else f.action)
                f.saved.retired_items.append(row)
                before = self.witness(f)
                if kind == 'unrelated':
                    self.assertTrue(f.guard.prepare(f.context, f.rig)['unkeyed_local'])
                else:
                    with self.assertRaises(ValueError):
                        f.guard.prepare(f.context, f.rig)
                self.assertEqual(self.witness(f), before)

    def test_existing_nla_preview_rest_original_and_calibration_protections_remain(self):
        for kind in ('tweak', 'nla', 'weighted', 'Original', 'Base', 'Natural', 'Rest',
                     'Composition', 'Unity', 'Rigging', 'Forearm', 'playing', 'idle'):
            with self.subTest(kind=kind):
                f = self.fixture()
                if kind == 'tweak':
                    f.rig.animation_data.use_tweak_mode = True
                elif kind == 'nla':
                    f.rig.animation_data.use_nla = True
                    f.rig.animation_data.nla_tracks = [NS(mute=False, strips=[object()])]
                elif kind == 'weighted':
                    f.rig.animation_data.action_influence = .5
                elif kind == 'Rigging':
                    f.rig['character_designer_rigging_session_v1'] = 'retained'
                elif kind == 'Forearm':
                    f.api.forearm._SESSION = {'armature': f.rig}
                elif kind == 'playing':
                    f.context.screen.is_animation_playing = True
                elif kind == 'idle':
                    f.api.worklist._idle.side_effect = ValueError('export is running')
                else:
                    callbacks = {'Original': f.api.original.active, 'Base': f.api.pose.active,
                        'Natural': f.api.pose.natural_active, 'Rest': f.api.rest.is_rest,
                        'Composition': f.api.composition.active, 'Unity': f.api.ua.active_preview}
                    callbacks[kind].return_value = object() if kind == 'Unity' else True
                before = self.witness(f)
                with self.assertRaises(ValueError):
                    f.guard.prepare(f.context, f.rig)
                self.assertEqual(self.witness(f), before)

    def test_native_owner_revalidates_binding_data_frame_key_policy_and_workflow(self):
        for kind in ('action', 'slot', 'data', 'frame', 'autokey', 'workspace', 'role'):
            with self.subTest(kind=kind):
                f = self.fixture()
                owner = f.guard.prepare(f.context, f.rig)
                if kind == 'action':
                    f.rig.animation_data.action = Action('Other Action')
                    f.slot.id_data = f.rig.animation_data.action
                elif kind == 'slot':
                    f.rig.animation_data.action_slot = NS(handle=18, id_data=f.action, target_id_type='OBJECT')
                elif kind == 'data':
                    f.rig.data = ID('Replacement Rig Data')
                elif kind == 'frame':
                    f.context.scene.frame_subframe = .75
                elif kind == 'autokey':
                    f.context.scene.tool_settings.use_keyframe_insert_auto = True
                elif kind == 'workspace':
                    f.saved.workspace_path = 'new-managed-workspace'
                else:
                    f.action[f.browser.ROLE_KEY] = 'ARCHIVED'
                before = self.witness(f)
                with self.assertRaises(ValueError):
                    f.guard.validate(f.context, owner)
                self.assertEqual(self.witness(f), before)

    def test_unwritten_action_metadata_change_is_preserved_without_history_restore(self):
        f = self.fixture()
        owner = f.guard.prepare(f.context, f.rig)
        f.action['artist-note'] = 'new current note'
        self.assertIs(f.guard.validate(f.context, owner), owner)
        self.assertEqual(f.action['artist-note'], 'new current note')

    def test_no_action_autokey_off_path_remains_separate(self):
        f = self.fixture()
        f.rig.animation_data.action = None
        owner = f.guard.prepare(f.context, f.rig)
        self.assertIsNone(owner['action'])
        self.assertIsNone(owner['slot'])
        self.assertFalse(owner.get('unkeyed_local', False))
        self.assertIs(f.guard.validate(f.context, owner), owner)
        f.context.scene.tool_settings.use_keyframe_insert_auto = True
        with self.assertRaises(ValueError):
            f.guard.prepare(f.context, f.rig)

    def test_managed_custom_off_and_autokey_on_keep_their_own_source_slot_and_bag(self):
        for autokey in (False, True):
            with self.subTest(autokey=autokey):
                f = self.fixture()
                source = Action('Linked Source', library=object())
                row = NS(rig=f.rig, side='CUSTOM', custom_action=f.action, custom_slot=f.slot.handle,
                    source_action=source, source_slot=9, item_id='managed-row', new_animation=False,
                    composition_json='', current_rig_revision='')
                f.saved.items.append(row)
                f.api.worklist.current.return_value = row
                f.context.scene.tool_settings.use_keyframe_insert_auto = autokey
                bag = NS(slot_handle=f.slot.handle)
                f.action._layers = [NS(strips=[NS(type='KEYFRAME', channelbags=[bag])], influence=1., mute=False)]
                before = self.witness(f)
                owner = f.guard.prepare(f.context, f.rig)
                self.assertFalse(owner.get('unkeyed_local', False))
                self.assertIs(owner['row'], row)
                self.assertIs(owner['source'], source)
                self.assertIs(owner['bag'], bag)
                self.assertEqual(owner['source_slot'], 9)
                self.assertEqual(owner['expected_action_users'], 3 if autokey else 0)
                self.assertIs(f.guard.validate(f.context, owner), owner)
                self.assertEqual(self.witness(f), before)
                row.side = 'SOURCE'
                with self.assertRaises(ValueError):
                    f.guard.prepare(f.context, f.rig)


if __name__ == '__main__':
    unittest.main()
