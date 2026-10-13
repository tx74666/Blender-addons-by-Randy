"""Actual UI dispatch and refusal guards; substitutes do not prove native Rigging."""

from contextlib import ExitStack
import ast
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


_helper_spec = importlib.util.spec_from_file_location(
    '_prepared_rigging_ui_helpers', Path(__file__).with_name('test_animation_worklist_ui.py'))
_helpers = importlib.util.module_from_spec(_helper_spec)
_helper_spec.loader.exec_module(_helpers)


class PreparedRiggingUI(unittest.TestCase):
    def setUp(self):
        # Reuse the existing fake Blender bootstrap, then execute the complete
        # canonical UI module. No native module or backend is imported here.
        _helpers.WorklistUI.setUp(self)
        self.main = SimpleNamespace(name='CoshaRig', type='ARMATURE', library=None,
            animation_data=SimpleNamespace(action=None),
            get=Mock(side_effect=lambda key: 'owned snapshot' if
                key == 'character_designer_worklist_main_session_v1' else
                'sealed rigging session' if key == 'character_designer_rigging_session_v1' else None))
        self.context.scene.character_designer_setup = SimpleNamespace(rig=self.main)
        self.context.scene.objects = {self.main.name: self.main}
        self.state.rig = self.main
        self.context.window_manager.fileselect_add = Mock()
        self.backend.attach_prepared_rigging_work = Mock()
        self.backend.validate_rigging_entry = Mock()
        self.backend.adopt_main_rig = Mock()
        for item in self.state.items:
            item.rig = self.main
        self.item = self.state.items[0]
        self.item.custom_action = SimpleNamespace(name='Walk Custom')
        self.item.source_action = SimpleNamespace(name='Walk Source')
        self.main.animation_data.action = self.item.custom_action
        self.session = dict(rig=self.main, scene=self.context.scene,
            base_action=self.item.custom_action, base_slot=4,
            input_action=SimpleNamespace(name='Walk Controls'), input_slot=7,
            work_item_id='prepared-work-id', source_packet_sha256='a' * 64,
            source_read_only=False, enabled=True, edit_role='RIGGING')
        self.native = ModuleType(self.ui.__package__ + '.unity_animation_rigging')
        self.native.current = Mock(return_value=self.session)
        self.native.display_state = Mock(return_value=None)
        self.native.activate, self.native.key_current = Mock(), Mock()
        # Composition owns a separate session/ID fixture. Isolate its sibling
        # renderer while retaining the actual Worklist drawing and guards.
        composition = ModuleType(self.ui.__package__ + '.animation_composition_ui')
        composition.draw_composition = Mock()
        modules = patch.dict(sys.modules, {self.native.__name__: self.native,
                                          composition.__name__: composition})
        modules.start()
        self.addCleanup(modules.stop)
        setattr(sys.modules[self.ui.__package__], 'unity_animation_rigging', self.native)
        # Prime Worklist drawing before measuring poll/draw data-file I/O.
        self.ui.draw_worklist(_helpers.Layout(), self.context)

    def attach_operator(self, path='D:/Prepared/Cosha.workspace.json'):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_add_prepared_animation()
        operator.filepath = path
        return operator

    def cached_state(self):
        return (self.state.workspace_path, tuple(self.state.catalog), tuple(self.state.items),
                self.state.rig, self.state.active_index)

    def no_file_access(self):
        stack = ExitStack()
        for method in ('open', 'read_text', 'read_bytes', 'stat', 'exists', 'is_file', 'is_dir'):
            stack.enter_context(patch.object(Path, method,
                side_effect=AssertionError('UI attempted disk I/O: ' + method)))
        stack.enter_context(patch('builtins.open', side_effect=AssertionError('UI attempted open')))
        return stack

    def test_only_cached_same_adopted_main_rig_is_eligible(self):
        self.assertTrue(self.attach_operator().poll(self.context))
        cases = (
            ('missing rig', None, self.main, 'owned snapshot'),
            ('different Main Rig', self.main, SimpleNamespace(), 'owned snapshot'),
            ('missing Main Rig', self.main, None, 'owned snapshot'),
            ('restored or unadopted', self.main, self.main, None),
        )
        for label, rig, main, record in cases:
            with self.subTest(label=label):
                self.state.rig = rig
                self.context.scene.character_designer_setup.rig = main
                self.main.get.side_effect = None
                self.main.get.return_value = record
                self.assertFalse(self.attach_operator().poll(self.context))
                layout = _helpers.Layout()
                self.ui.draw_worklist(layout, self.context)
                self.assertNotIn(self.attach_operator().bl_idname,
                    [value[0] for kind, value, _ in layout.records if kind == 'operator'])
        self.backend.attach_prepared_rigging_work.assert_not_called()
        self.backend.validate_rigging_entry.assert_not_called()

        self.state.rig = self.context.scene.character_designer_setup.rig = self.main
        self.main.get.return_value = 'owned snapshot'
        self.context.scene.objects.clear()
        self.assertFalse(self.attach_operator().poll(self.context))
        self.context.scene.objects[self.main.name] = self.main
        self.main.library = object()
        self.assertFalse(self.attach_operator().poll(self.context))

    def test_poll_and_draw_have_no_file_access_or_backend_authentication(self):
        before = self.cached_state()
        with self.no_file_access():
            self.assertTrue(self.attach_operator().poll(self.context))
            layout = _helpers.Layout()
            self.ui.draw_worklist(layout, self.context)
        buttons = [value[0] for kind, value, _ in layout.records if kind == 'operator']
        self.assertEqual(buttons.count(self.attach_operator().bl_idname), 1)
        self.assertIn('character_designer.worklist_add_ready', buttons)
        self.assertIn('character_designer.worklist_sync_changed', buttons)
        self.assertIn(self.ui.CHARACTERDESIGNER_OT_worklist_add_prepared_animation,
                      self.ui.WORKLIST_CLASSES)
        self.backend.attach_prepared_rigging_work.assert_not_called()
        self.backend.validate_rigging_entry.assert_not_called()
        self.assertEqual(self.cached_state(), before)

    def test_busy_owning_context_disables_entry_without_reading_files(self):
        self.animation._link_idle.return_value = False
        with self.no_file_access():
            self.assertFalse(self.attach_operator().poll(self.context))
            layout = _helpers.Layout()
            self.ui.draw_worklist(layout, self.context)
        button = [(value, enabled) for kind, value, enabled in layout.records
                  if kind == 'operator' and value[0] == self.attach_operator().bl_idname]
        self.assertEqual(len(button), 1)
        self.assertFalse(button[0][1])
        self.backend.attach_prepared_rigging_work.assert_not_called()

    def test_file_selector_preserves_explicit_path_without_guessing_workspace(self):
        for path in ('', 'D:/NewPrepare/character_animation_workspace.json',
                     'D:/Animations/Cosha.workspace.json'):
            with self.subTest(path=path), self.no_file_access():
                operator = self.attach_operator(path)
                self.assertEqual(operator.invoke(self.context, None), {'RUNNING_MODAL'})
                self.assertEqual(operator.filepath, path)
                self.context.window_manager.fileselect_add.assert_called_with(operator)
        self.backend.attach_prepared_rigging_work.assert_not_called()
        self.backend.connect.assert_not_called()

    def test_execute_dispatches_exact_file_with_omitted_work_id_and_keeps_cached_old_rows(self):
        # Backend transaction/admission has its own tests. This verifies the UI
        # neither selects a row from the old catalog nor swaps cached ownership.
        self.state.items.extend(SimpleNamespace(item_id='old-' + str(i)) for i in range(6))
        self.assertEqual(len(self.state.items), 9)
        before = self.cached_state()
        for path in ('D:/NewPrepare/character_animation_workspace.json',
                     'D:/Animations/Cosha.workspace.json'):
            with self.subTest(path=path):
                operator = self.attach_operator(path)
                self.assertEqual(operator.execute(self.context), {'FINISHED'})
                self.backend.attach_prepared_rigging_work.assert_called_with(self.context, path)
                self.assertEqual(self.cached_state(), before)
        for method in ('connect', 'refresh', 'adopt_main_rig', 'add', 'activate', 'remove'):
            getattr(self.backend, method).assert_not_called()

    def test_direct_execute_rechecks_same_main_rig_and_idle_before_backend(self):
        for stale_rig, idle in ((None, True), (self.main, False)):
            with self.subTest(stale_rig=stale_rig, idle=idle):
                self.state.rig = stale_rig
                self.animation._link_idle.return_value = idle
                operator = self.attach_operator()
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertTrue(self.state.has_error)
                self.assertIn('same adopted Main Rig', self.state.status)
        self.backend.attach_prepared_rigging_work.assert_not_called()

    def test_backend_ordinary_ambiguous_and_stale_refusals_retain_feedback_and_old_ownership(self):
        before = self.cached_state()
        for message in ('Choose a workspace JSON containing prepared Rigging work.',
                        'Expected exactly one request-matched prepared Rigging work item.',
                        'The prepared packet or Link changed after Prepare.'):
            with self.subTest(message=message):
                self.backend.attach_prepared_rigging_work.side_effect = ValueError(message)
                operator = self.attach_operator()
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertEqual(self.state.status, message)
                self.assertTrue(self.state.has_error)
                self.assertEqual(operator.last_report, ({'ERROR'}, message))
                self.assertEqual(self.cached_state(), before)
        self.backend.connect.assert_not_called()
        self.backend.adopt_main_rig.assert_not_called()

    def assert_rigging_order(self, role):
        events = []
        self.backend.validate_rigging_entry.side_effect = lambda *args: events.append(('validate', args))
        self.native.activate.side_effect = lambda *args, **kwargs: events.append(('activate', args, kwargs))
        self.native.key_current.side_effect = lambda *args: events.append(('key', args))
        if role is None:
            operator = self.ui.CHARACTERDESIGNER_OT_worklist_save_controls()
        else:
            operator = self.ui.CHARACTERDESIGNER_OT_worklist_rigging_edit()
            operator.edit_role = role
        operator.item_id = self.item.item_id
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.assertEqual(events[0], ('validate', (self.context, self.item, self.session)))
        if role is None:
            self.assertEqual(events[1], ('key', (self.context, self.session)))
            self.native.activate.assert_not_called()
        else:
            self.assertEqual(events[1], ('activate', (self.context, self.session),
                                        {'edit_role': role, 'enabled': True}))
            self.native.key_current.assert_not_called()
        self.assertEqual(len(events), 2)
        self.assertFalse(self.state.has_error)

    def test_edit_base_reauthenticates_before_activation(self):
        self.assert_rigging_order('BASE')

    def test_edit_controls_reauthenticates_before_activation(self):
        self.assert_rigging_order('RIGGING')

    def test_save_controls_reauthenticates_before_keying(self):
        self.assert_rigging_order(None)

    def test_reacquired_row_wrapper_is_passed_to_backend_by_current_stable_item_id(self):
        # RNA collection access can return a fresh Python row wrapper. The
        # backend owns native pointer/WorkID validation; UI must pass the current
        # wrapper rather than bind editing to an earlier Python row instance.
        current_row = SimpleNamespace(**vars(self.item), as_pointer=lambda: 1234)
        self.state.items[0] = current_row
        self.assertIsNot(current_row, self.item)
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_save_controls()
        operator.item_id = self.item.item_id
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.backend.validate_rigging_entry.assert_called_once_with(
            self.context, current_row, self.session)
        self.native.key_current.assert_called_once_with(self.context, self.session)

    def test_stale_link_packet_capture_slot_or_work_id_refusal_prevents_mutation(self):
        for message in ('Link belongs to another target.', 'The full packet hash changed.',
                        'Custom capture differs from the prepared packet.',
                        'Base or Custom slot changed.', 'Session WorkID differs from the row.'):
            for role in ('BASE', 'RIGGING', None):
                with self.subTest(message=message, role=role):
                    self.backend.validate_rigging_entry.side_effect = ValueError(message)
                    operator = (self.ui.CHARACTERDESIGNER_OT_worklist_save_controls()
                                if role is None else self.ui.CHARACTERDESIGNER_OT_worklist_rigging_edit())
                    operator.item_id = self.item.item_id
                    operator.edit_role = role
                    self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                    self.assertEqual(operator.last_report, ({'ERROR'}, message))
                    self.assertEqual(self.state.status, message)
                    self.assertTrue(self.state.has_error)
        self.native.activate.assert_not_called()
        self.native.key_current.assert_not_called()

    def test_source_foreign_session_and_stale_selection_fail_before_reauthentication(self):
        operator = self.ui.CHARACTERDESIGNER_OT_worklist_save_controls()
        operator.item_id = self.item.item_id
        cases = (
            dict(side='SOURCE'),
            dict(item_id='other-row'),
            dict(source_read_only=True),
            dict(scene=SimpleNamespace()),
            dict(base_action=SimpleNamespace(name='Other Custom')),
        )
        for changes in cases:
            with self.subTest(changes=changes):
                original_item = self.item.side, operator.item_id
                original_session = self.session.copy()
                self.item.side = changes.get('side', self.item.side)
                operator.item_id = changes.get('item_id', operator.item_id)
                self.session.update({key: value for key, value in changes.items()
                                     if key not in {'side', 'item_id'}})
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.item.side, operator.item_id = original_item
                self.session.clear()
                self.session.update(original_session)
        self.backend.validate_rigging_entry.assert_not_called()
        self.native.activate.assert_not_called()
        self.native.key_current.assert_not_called()

    def test_edit_and_save_poll_use_only_cached_display(self):
        self.native.display_state.return_value = self.session
        with self.no_file_access():
            self.assertTrue(self.ui.CHARACTERDESIGNER_OT_worklist_rigging_edit.poll(self.context))
            self.assertTrue(self.ui.CHARACTERDESIGNER_OT_worklist_save_controls.poll(self.context))
        self.backend.validate_rigging_entry.assert_not_called()
        self.native.current.assert_not_called()


class OriginalInputsGunUI(unittest.TestCase):
    """Run the real input UI and selection adapter, with no native rig simulation."""

    def setUp(self):
        _helpers.WorklistUI.setUp(self)
        package_name = self.ui.__package__
        self.worklist_ui = self.ui
        name = package_name + '.animation_original_inputs_ui'
        spec = importlib.util.spec_from_file_location(name,
            _helpers.SOURCE / 'animation_original_inputs_ui.py')
        self.ui = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.ui
        spec.loader.exec_module(self.ui)

        class PoseBones(list):
            def __getitem__(self, key):
                return next(pb for pb in self if pb.name == key) if isinstance(key, str) else super().__getitem__(key)

        self.bones = PoseBones(SimpleNamespace(name=name, select=True,
            bone=SimpleNamespace(name=name, collections=[])) for name in ('Target.L', 'Target.R', 'Hint.R'))
        self.metadata = {'character_designer_worklist_main_session_v1': 'adopted',
                         self.ui.PAIR_KEY: 'saved pair',
                         'character_designer_composition_weapon_v1': 'owned gun'}
        self.rig = SimpleNamespace(name='CoshaRig', type='ARMATURE', library=None, mode='POSE',
            data=SimpleNamespace(library=None, bones=SimpleNamespace(active=None)),
            pose=SimpleNamespace(bones=self.bones), get=self.metadata.get)
        self.context.scene.character_designer_setup = SimpleNamespace(rig=self.rig)
        self.context.scene.objects = {self.rig.name: self.rig}
        self.context.active_object = self.rig
        self.session = dict(rig=self.rig, scene=self.context.scene, owner='session owner',
            native_bindings=[dict(native_side='L', target='Target.L'), dict(native_side='R', target='Target.R')],
            capture=dict(bindings=[dict(constraintWeight=1., targetPositionWeight=1.),
                                   dict(constraintWeight=1., targetPositionWeight=1.)]),
            base_action=SimpleNamespace(name='Source'), input_action=SimpleNamespace(name='Custom'),
            enabled=True, tweak_enabled=True, edit_role='RIGGING', legacy_side='SOURCE')
        self.inputs = ModuleType(package_name + '.unity_animation_original_inputs')
        self.inputs.current = Mock(return_value=self.session)
        self.inputs.display_state = Mock(return_value=self.session)
        self.inputs.edit_controls, self.inputs.key_current = Mock(), Mock()
        self.weapon = ModuleType(package_name + '.animation_composition_weapon')
        self.gun = {'rigging_session_owner': 'session owner'}
        self.weapon.validate_owned = Mock(return_value=(self.gun, []))
        composition = ModuleType(package_name + '.animation_composition')
        source = _helpers.SOURCE / 'animation_composition.py'
        tree = ast.parse(source.read_text(encoding='utf-8-sig'))
        selection = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == '_pose_selection_owner']
        self.assertEqual(len(selection), 1)
        exec(compile(ast.Module(body=selection, type_ignores=[]), str(source), 'exec'), vars(composition))
        for module in (self.inputs, self.weapon, composition):
            sys.modules[module.__name__] = module
            setattr(sys.modules[package_name], module.__name__.rsplit('.', 1)[1], module)

    def edit(self, focus=True):
        operator = self.ui.CHARACTERDESIGNER_OT_original_inputs_edit()
        operator.focus_gun_motion = focus
        return operator

    def test_gun_selects_only_right_target_after_existing_edit_and_keeps_undo(self):
        for legacy in (False, True):
            with self.subTest(legacy_bone_selection=legacy):
                if legacy:
                    for pb in self.bones:
                        del pb.select
                        pb.bone.select = True
                def activate(*_args):
                    self.assertTrue(all((pb.bone if legacy else pb).select for pb in self.bones))
                    self.assertIsNone(self.rig.data.bones.active)
                self.inputs.edit_controls.side_effect = activate
                operator = self.edit()
                self.assertEqual(operator.execute(self.context), {'FINISHED'})
                self.assertEqual([(pb.bone if legacy else pb).select for pb in self.bones], [False, True, False])
                self.assertIs(self.rig.data.bones.active, self.bones['Target.R'].bone)
                self.assertIn('UNDO', operator.bl_options)
                self.inputs.edit_controls.assert_called_with(self.context, self.rig)
                self.inputs.key_current.assert_not_called()
                self.rig.data.bones.active = None

    def test_gun_refusals_precede_activation_and_leave_selection_unchanged(self):
        cases = ('inactive rig', 'object mode', 'foreign owner', 'non-string owner', 'no session',
                 'duplicate right', 'zero constraint', 'zero position', 'hidden collection', 'unknown selection API')
        for case in cases:
            with self.subTest(case=case), ExitStack() as stack:
                if case == 'inactive rig': stack.enter_context(patch.object(self.context, 'active_object', None))
                elif case == 'object mode': stack.enter_context(patch.object(self.rig, 'mode', 'OBJECT'))
                elif case in ('foreign owner', 'non-string owner'):
                    stack.enter_context(patch.dict(self.gun, rigging_session_owner='other' if case == 'foreign owner' else None))
                elif case == 'no session': stack.enter_context(patch.object(self.inputs.current, 'return_value', None))
                elif case == 'duplicate right':
                    stack.enter_context(patch.dict(self.session['native_bindings'][0], native_side='R'))
                elif case in ('zero constraint', 'zero position'):
                    field = 'constraintWeight' if case == 'zero constraint' else 'targetPositionWeight'
                    stack.enter_context(patch.dict(self.session['capture']['bindings'][1], {field: 0.}))
                elif case == 'hidden collection':
                    stack.enter_context(patch.object(self.bones['Target.R'].bone, 'collections',
                        [SimpleNamespace(is_visible_effectively=False)]))
                else:
                    del self.bones[0].select
                    stack.callback(setattr, self.bones[0], 'select', True)
                operator = self.edit()
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertEqual(operator.last_report[0], {'ERROR'})
                self.inputs.edit_controls.assert_not_called()
                self.inputs.key_current.assert_not_called()
                self.assertIsNone(self.rig.data.bones.active)
                self.assertTrue(all(getattr(pb, 'select', True) for pb in self.bones))

    def test_backend_reauthentication_failure_does_not_select_gun(self):
        self.inputs.edit_controls.side_effect = ValueError('Prepared input identity changed.')
        operator = self.edit()
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.assertEqual(operator.last_report, ({'ERROR'}, 'Prepared input identity changed.'))
        self.assertTrue(all(pb.select for pb in self.bones))
        self.assertIsNone(self.rig.data.bones.active)

    def test_selection_failure_after_activation_finishes_with_warning_to_preserve_undo(self):
        class ReadOnlySelection:
            name = 'Target.L'
            bone = SimpleNamespace(collections=[])
            @property
            def select(self): return True
            @select.setter
            def select(self, _value): raise RuntimeError('Selection became unavailable.')
        self.bones[0] = ReadOnlySelection()
        operator = self.edit()
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.inputs.edit_controls.assert_called_once_with(self.context, self.rig)
        self.assertEqual(operator.last_report[0], {'WARNING'})
        self.assertIn('Inputs are open.', operator.last_report[1])
        self.assertIn('UNDO', operator.bl_options)

    def test_generic_edit_and_key_keep_the_existing_dispatch_without_gun_validation(self):
        self.assertEqual(self.edit(False).execute(self.context), {'FINISHED'})
        operator = self.ui.CHARACTERDESIGNER_OT_original_inputs_save()
        self.assertTrue(operator.poll(self.context))
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.inputs.edit_controls.assert_called_once_with(self.context, self.rig)
        self.inputs.key_current.assert_called_once_with(self.context, self.rig)
        self.weapon.validate_owned.assert_not_called()
        self.assertTrue(all(pb.select for pb in self.bones))

    def test_draw_reuses_edit_and_save_and_does_not_authenticate_or_read_files(self):
        for live, weapon, editable in ((True, True, True), (True, True, False), (True, False, True), (False, True, False)):
            with self.subTest(live=live, weapon=weapon, editable=editable):
                self.inputs.display_state.return_value = self.session if live else None
                self.session['tweak_enabled'] = editable
                self.metadata['character_designer_composition_weapon_v1'] = 'owned gun' if weapon else None
                layout = _helpers.Layout()
                with PreparedRiggingUI.no_file_access(self):
                    self.ui.draw_original_inputs(layout, self.context)
                buttons = [(value, enabled) for kind, value, enabled in layout.records if kind == 'operator']
                gun = [(value, enabled) for value, enabled in buttons if value[1].get('text') == 'Gun Motion']
                keys = [(value, enabled) for value, enabled in buttons if value[1].get('text') == 'Key Gun Motion']
                self.assertEqual(len(gun), int(live and weapon))
                self.assertEqual(len(keys), int(live and weapon))
                if gun:
                    self.assertEqual(gun[0][0][0], self.ui.CHARACTERDESIGNER_OT_original_inputs_edit.bl_idname)
                    self.assertTrue(gun[0][0][2].focus_gun_motion)
                    self.assertEqual(keys[0][0][0], self.ui.CHARACTERDESIGNER_OT_original_inputs_save.bl_idname)
                    self.assertEqual(keys[0][1], editable)
                generic = [value for value, _enabled in buttons if value[0] == self.edit().bl_idname
                           and value[1].get('text') != 'Gun Motion']
                self.assertEqual(len(generic), 1)
                self.assertFalse(generic[0][2].focus_gun_motion)
        self.weapon.validate_owned.assert_not_called()
        self.inputs.current.assert_not_called()
        self.inputs.edit_controls.assert_not_called()
        self.inputs.key_current.assert_not_called()


if __name__ == '__main__':
    unittest.main()
