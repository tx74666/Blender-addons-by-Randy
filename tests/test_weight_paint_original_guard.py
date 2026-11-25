"""Weight-paint sessions exclude Original transactions without changing their proof."""
import ast
from contextlib import nullcontext
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/body_original_mode.py'


class Rig(dict):
    type = 'ARMATURE'
    name = 'Character'
    override_library = None


class Layout:
    def __init__(self, parent=None):
        self.parent = parent
        self.enabled = True
        self.rows = []
        self.labels = []
        self.buttons = []

    def row(self, **_options):
        child = Layout(self)
        self.rows.append(child)
        return child

    def operator(self, identifier, **options):
        button = NS(identifier=identifier, **options)
        self.buttons.append(button)
        return button

    def label(self, **options):
        self.labels.append(options)

    @property
    def effective_enabled(self):
        return self.enabled and (self.parent is None or self.parent.effective_enabled)


class WeightPaintOriginalGuardTests(unittest.TestCase):
    def setUp(self):
        prefix = '_weight_paint_original_guard_' + uuid.uuid4().hex
        package = ModuleType(prefix)
        package.__path__ = []
        bpy = ModuleType('bpy')
        bpy.types = NS(Operator=object)
        props = ModuleType('bpy.props')
        props.EnumProperty = Mock(return_value=None)
        self.rig = Rig(artist_marker='unchanged')
        self.rig.data = NS(collections_all={'Original': object()})
        self.context = NS(mode='OBJECT', scene={},
                          view_layer=NS(objects={self.rig.name: self.rig}), screen=NS(areas=[]))
        self.display = NS(character_rig=Mock(return_value=self.rig),
                          view_mode=Mock(return_value=None), _editable=Mock(),
                          restore_view=Mock(), show_controls=Mock(), ensure_controls_selectable=Mock(),
                          _apply_pending_physics=Mock())
        self.groups = NS(body_collection=Mock(return_value=object()))
        self.paint = NS(active=Mock(return_value=False), owns_rig=Mock(return_value=False),
                        phase=Mock(return_value=None), show_painting=Mock(return_value='PAINT'),
                        show_controls=Mock(return_value='POSE_PREVIEW'), native_painting=Mock(return_value=False),
                        last_finish_status=Mock(return_value=None),
                        controls_display_needs_repair=Mock(return_value=False))
        self.ui = NS(active_ui_page=lambda context: getattr(context, 'page', 'RIG'), UI_PAGE_WEIGHT='WEIGHT')
        self.sync = NS(last_error=Mock(return_value=''))
        self.owner = NS(pose_capability=Mock(return_value=object()),
                        run_return=Mock(return_value=False))
        self.refresh = Mock()
        self.forearm = NS(_SESSION=None,
                          defer_runtime=Mock(return_value=nullcontext(self.refresh)))
        modules = {'bpy': bpy, 'bpy.props': props, prefix: package,
                   prefix + '.bone_display': self.display,
                   prefix + '.bone_collections': self.groups,
                   prefix + '.limb_ik': NS(), prefix + '.limb_ik_fk': NS(),
                   prefix + '.control_pose_assets': NS(),
                   prefix + '.skirt_original_mode': NS(),
                   prefix + '.weight_paint_entry': self.paint,
                   prefix + '.body_original_owner': self.owner,
                   prefix + '.forearm_twist': self.forearm}
        modules[prefix + '.ui_constants'] = self.ui
        modules[prefix + '.bone_display_sync'] = self.sync
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        name = prefix + '.body_original_mode'
        spec = importlib.util.spec_from_file_location(name, SOURCE)
        self.original = importlib.util.module_from_spec(spec)
        self.prefix = prefix
        sys.modules[name] = self.original
        spec.loader.exec_module(self.original)

    def operator(self, action='ORIGINAL'):
        operator = self.original.CHARACTERDESIGNER_OT_body_original_mode()
        operator.action = action
        operator.report = Mock()
        return operator

    def test_services_refuse_all_paint_phases_before_capabilities_or_scene_services(self):
        self.paint.owns_rig.return_value = True
        self.rig[self.original.SESSION] = 'an existing session must not be parsed or replaced'
        before = dict(self.rig)
        for mode in ('PAINT_WEIGHT', 'POSE', 'OBJECT'):
            self.context.mode = mode
            for service, args in ((self.original._require, ()),
                                  (self.original.enter, ()),
                                  (self.original.leave, ()),
                                  (self.original.ensure_dress_editable, ()),
                                  (self.original.show_group, ('BODY',))):
                with self.subTest(mode=mode, service=service.__name__):
                    with self.assertRaisesRegex(ValueError, 'Controls'):
                        service(self.context, self.rig, *args)
                    self.paint.owns_rig.assert_called_with(self.rig)
                    self.assertEqual(dict(self.rig), before)
        self.owner.pose_capability.assert_not_called()
        self.owner.run_return.assert_not_called()
        self.forearm.defer_runtime.assert_not_called()
        self.display._editable.assert_not_called()
        self.display.restore_view.assert_not_called()
        self.display._apply_pending_physics.assert_not_called()

    def test_f3_execute_cannot_bypass_guard_even_for_controls_without_original_session(self):
        self.paint.owns_rig.return_value = True
        self.display.view_mode.return_value = 'ORIGINAL'
        for action in ('ORIGINAL', 'CONTROLS', 'TOGGLE'):
            with self.subTest(action=action):
                operator = self.operator(action)
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertIn('Controls', operator.report.call_args.args[1])
        self.display.restore_view.assert_not_called()
        self.owner.pose_capability.assert_not_called()
        self.owner.run_return.assert_not_called()

    def test_active_paint_uses_weight_controls_on_either_page(self):
        self.paint.owns_rig.return_value = self.paint.active.return_value = True
        for mode in ('PAINT_WEIGHT', 'POSE', 'OBJECT'):
            with self.subTest(mode=mode):
                self.context.mode = mode
                layout = Layout()
                self.assertFalse(self.original.draw(layout, self.context))
                row, = layout.rows
                self.assertTrue(row.effective_enabled)
                painting, controls = row.rows
                self.assertEqual(painting.buttons[0].text, 'Original Bones')
                self.assertEqual(controls.buttons[0].text, 'Controls')
                self.assertEqual(controls.buttons[0].identifier, 'character_designer.preview_weight_pose')

    def test_no_paint_session_keeps_buttons_available_and_no_new_hint(self):
        layout = Layout()
        self.assertFalse(self.original.draw(layout, self.context))
        self.assertTrue(layout.rows[0].effective_enabled)
        self.assertEqual(layout.labels, [])

    def test_another_rigs_paint_session_does_not_block_this_rigs_strict_preflight(self):
        another_rig = Rig(artist_marker='other')
        self.paint.owns_rig.side_effect = lambda rig: rig is another_rig
        self.assertIsNone(self.original._require(self.context, self.rig))
        self.display._editable.assert_called_once_with(self.rig, _original_pose=None)

    def test_same_rig_is_protected_from_a_different_scene_or_view_layer(self):
        self.paint.active.return_value = False
        self.paint.owns_rig.return_value = True
        elsewhere = NS(mode='OBJECT', scene={'different_scene': True},
                       view_layer=NS(objects={self.rig.name: self.rig}))
        with self.assertRaisesRegex(ValueError, 'Controls'):
            self.original.enter(elsewhere, self.rig)
        self.owner.pose_capability.assert_not_called()
        self.forearm.defer_runtime.assert_not_called()
        layout = Layout()
        self.original.draw(layout, elsewhere)
        self.assertFalse(layout.rows[0].effective_enabled)
        self.assertIn('Scene and View Layer', layout.labels[0]['text'])

    def test_inactive_paint_keeps_enter_and_return_strict_errors(self):
        technical = 'Main Rig motion Rest proof failed: original strict rejection'
        with patch.object(self.original, '_enter', side_effect=ValueError(technical)):
            with self.assertRaisesRegex(ValueError, technical):
                self.original.enter(self.context, self.rig)
        self.owner.pose_capability.assert_called_once_with(self.context, self.rig, entering=True)
        self.display._apply_pending_physics.assert_not_called()
        self.owner.run_return.side_effect = ValueError(technical)
        with self.assertRaisesRegex(ValueError, technical):
            self.original.leave(self.context, self.rig)
        self.owner.run_return.assert_called_once()
        self.display._apply_pending_physics.assert_not_called()

    def test_missing_recovery_refs_report_explains_scope_and_keeps_original_detail(self):
        technical = 'Main Rig motion Rest proof failed: A saved Main Rig recovery data block was removed.'
        operator = self.operator()
        with patch.object(self.original, 'enter', side_effect=ValueError(technical)):
            self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        level, message = operator.report.call_args.args
        self.assertEqual(level, {'WARNING'})
        self.assertIn('missing saved Main Rig recovery references', message)
        self.assertIn('Original is currently unavailable', message)
        self.assertIn('Original Bones on the Weight page', message)
        self.assertTrue(message.endswith(technical))

    def test_unrelated_strict_report_stays_unchanged(self):
        technical = 'Original native bone ownership changed; keep the current edit.'
        operator = self.operator()
        with patch.object(self.original, 'enter', side_effect=ValueError(technical)):
            self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        operator.report.assert_called_once_with({'WARNING'}, technical)

    def test_weight_original_uses_formal_service_and_owned_paint_never_bypasses_admission(self):
        self.context.page = 'WEIGHT'
        self.rig['character_designer_worklist_main_session_v1'] = 'missing historical references'
        before = dict(self.rig)
        with patch.object(self.original, 'enter') as enter:
            self.assertEqual(self.operator('ORIGINAL').execute(self.context), {'FINISHED'})
            enter.assert_called_once_with(self.context, self.rig)
            for phase in ('POSE_PREVIEW', 'PAINT'):
                with self.subTest(phase=phase):
                    self.paint.owns_rig.return_value = True
                    self.paint.phase.return_value = phase
                    operator = self.operator('ORIGINAL')
                    self.assertFalse(operator.poll(self.context))
                    self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                    self.assertIn('Controls', operator.report.call_args.args[1])
                    self.assertEqual(enter.call_count, 1)
        self.assertEqual(dict(self.rig), before)
        self.paint.show_painting.assert_not_called()
        self.paint.show_controls.assert_not_called()

    def test_formal_weight_controls_refuse_owned_session_in_both_paint_phases(self):
        self.context.page = 'WEIGHT'
        self.paint.owns_rig.return_value = True
        for phase, mode in (('PAINT', 'PAINT_WEIGHT'), ('POSE_PREVIEW', 'POSE')):
            with self.subTest(phase=phase, mode=mode):
                self.context.mode, self.paint.phase.return_value = mode, phase
                operator = self.operator('CONTROLS')
                self.assertFalse(operator.poll(self.context))
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertIn('Controls', operator.report.call_args.args[1])
        self.paint.show_controls.assert_not_called()
        self.display.show_controls.assert_not_called()
        self.display.ensure_controls_selectable.assert_not_called()
        self.owner.pose_capability.assert_not_called()
        self.owner.run_return.assert_not_called()

    def test_weight_idle_controls_only_repairs_public_selection_with_193_policy(self):
        self.context.page = 'WEIGHT'
        operator = self.operator('CONTROLS')
        self.assertEqual(operator.execute(self.context), {'FINISHED'})
        self.display.ensure_controls_selectable.assert_called_once_with(self.context, self.rig)
        self.display.show_controls.assert_not_called()
        self.paint.show_controls.assert_not_called()
        self.paint.show_painting.assert_not_called()
        self.owner.run_return.assert_not_called()

    def test_native_orphan_painting_cannot_become_formal_original_or_controls(self):
        self.context.page, self.context.mode = 'WEIGHT', 'PAINT_WEIGHT'
        self.paint.native_painting.return_value = True
        for action in ('ORIGINAL', 'CONTROLS', 'TOGGLE'):
            with self.subTest(action=action):
                operator = self.operator(action)
                self.assertFalse(operator.poll(self.context))
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertIn('native painting', operator.report.call_args.args[1])
        self.display.show_controls.assert_not_called()
        self.display.ensure_controls_selectable.assert_not_called()
        self.paint.show_controls.assert_not_called()
        self.paint.show_painting.assert_not_called()
        self.assertNotIn(self.original.SESSION, self.rig)

    def test_weight_page_returns_formal_original_through_same_guarded_service(self):
        self.context.page = 'WEIGHT'
        self.rig[self.original.SESSION] = 'unparsed existing formal session'
        with patch.object(self.original, 'leave') as leave, \
                patch.object(self.original, 'ensure_dress_editable') as ensure:
            self.assertEqual(self.operator('ORIGINAL').execute(self.context), {'FINISHED'})
            ensure.assert_called_once_with(self.context, self.rig)
            for action in ('CONTROLS', 'TOGGLE'):
                self.assertEqual(self.operator(action).execute(self.context), {'FINISHED'})
            self.assertEqual(leave.call_count, 2)
        self.paint.show_painting.assert_not_called()
        self.paint.show_controls.assert_not_called()
        layout = Layout()
        self.assertTrue(self.original.draw(layout, self.context))
        self.assertTrue(layout.rows[0].effective_enabled)
        original = layout.rows[0].rows[0].buttons[0]
        self.assertTrue(original.depress)
        self.assertEqual(original.identifier, 'character_designer.body_original_mode')
        self.assertEqual(original.action, 'ORIGINAL')
        self.assertEqual(layout.rows[0].rows[1].buttons[0].action, 'CONTROLS')

    def test_weight_button_stages_and_manual_mode_reentry_remain_distinct(self):
        self.context.page = 'WEIGHT'
        self.paint.owns_rig.return_value = self.paint.active.return_value = True
        for phase, mode, painting_enabled, preview_enabled in (
                ('PAINT', 'PAINT_WEIGHT', False, True), ('POSE_PREVIEW', 'POSE', True, True),
                ('PAINT', 'OBJECT', True, True), ('POSE_PREVIEW', 'OBJECT', True, True)):
            with self.subTest(phase=phase, mode=mode):
                self.paint.phase.return_value, self.context.mode = phase, mode
                layout = Layout()
                self.assertFalse(self.original.draw(layout, self.context))
                self.assertTrue(layout.rows[0].effective_enabled)
                painting, preview = layout.rows[0].rows
                self.assertEqual(painting.effective_enabled, painting_enabled)
                self.assertEqual(preview.effective_enabled, preview_enabled)
                self.assertEqual(painting.buttons[0].identifier, 'character_designer.resume_weight_paint')
                self.assertEqual(preview.buttons[0].identifier, 'character_designer.preview_weight_pose')
                self.assertEqual(painting.buttons[0].depress, phase == 'PAINT' and mode == 'PAINT_WEIGHT')
                self.assertEqual(preview.buttons[0].text, 'Controls')

    def test_weight_cross_scene_rig_ownership_never_falls_through_to_formal_controls(self):
        self.context.page = 'WEIGHT'
        self.paint.owns_rig.return_value = True
        self.paint.active.return_value = False
        operator = self.operator('CONTROLS')
        self.assertFalse(operator.poll(self.context))
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        self.display.show_controls.assert_not_called()
        self.display.ensure_controls_selectable.assert_not_called()
        self.owner.run_return.assert_not_called()
        layout = Layout()
        self.original.draw(layout, self.context)
        self.assertFalse(layout.rows[0].effective_enabled)
        self.assertEqual(len(layout.rows), 1)
        self.assertTrue(any('Scene and View Layer' in item['text'] for item in layout.labels))

    def test_idle_highlight_follows_pose_mode_and_formal_return_stays_available(self):
        self.context.page = 'WEIGHT'
        for mode in ('OBJECT', 'POSE'):
            self.context.mode = mode
            layout = Layout()
            self.original.draw(layout, self.context)
            row, = layout.rows
            self.assertEqual(row.rows[1].buttons[0].depress, mode == 'POSE')
        self.rig[self.original.SESSION] = 'existing formal session'
        layout = Layout()
        self.original.draw(layout, self.context)
        painting, controls = layout.rows[0].rows
        self.assertTrue(painting.buttons[0].depress)
        self.assertEqual(painting.buttons[0].action, 'ORIGINAL')
        self.assertTrue(controls.effective_enabled)
        self.assertEqual(controls.buttons[0].action, 'CONTROLS')

    def display_classes(self):
        """Execute the real two panel/operator definitions with isolated providers."""
        source = SOURCE.with_name('bone_display.py')
        tree = ast.parse(source.read_text(encoding='utf-8'))
        selected = ast.Module(body=[node for node in tree.body if isinstance(node, ast.ClassDef) and
                                   node.name in {'CHARACTERDESIGNER_OT_bone_display', 'CHARACTERDESIGNER_PT_bone_display'}],
                              type_ignores=[])
        namespace = dict(__name__=self.prefix + '.display_fixture', __package__=self.prefix,
                         Operator=object, Panel=object, EnumProperty=lambda **_: None,
                         SIDEBAR_CATEGORY='Character Designer', UI_PAGE_WEIGHT='WEIGHT', UI_PAGE_RIG='RIG',
                         active_ui_page=self.ui.active_ui_page, character_rig=self.display.character_rig,
                         view_mode=self.display.view_mode, restore_view=Mock(), show_native=Mock(), show_controls=Mock(),
                         _control_collections=Mock(side_effect=AssertionError('Do not resolve display presets during painting')),
                         _controls_visible=Mock())
        exec(compile(selected, str(source), 'exec'), namespace)
        return namespace

    def test_owned_paint_session_blocks_every_explicit_display_preset_before_mutation(self):
        self.paint.owns_rig.return_value = True
        namespace = self.display_classes()
        for action in ('ALL', 'BODY', 'HAIR', 'DRESS', 'ORIGINAL', 'HAIR_BONES', 'DRESS_BONES', 'RESTORE'):
            with self.subTest(action=action):
                operator = namespace['CHARACTERDESIGNER_OT_bone_display']()
                operator.action, operator.report = action, Mock()
                self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                self.assertIn('Controls', operator.report.call_args.args[1])
        for name in ('restore_view', 'show_native', 'show_controls'):
            namespace[name].assert_not_called()

    def test_owned_paint_display_presets_are_disabled_without_formal_group_resolution(self):
        self.context.page, self.context.mode = 'WEIGHT', 'PAINT_WEIGHT'
        self.paint.owns_rig.return_value = self.paint.active.return_value = True
        self.paint.phase.return_value = 'PAINT'
        self.display.view_mode.return_value = 'ORIGINAL'
        namespace = self.display_classes()
        panel = namespace['CHARACTERDESIGNER_PT_bone_display']()
        panel.layout = Layout()
        with patch.object(self.original, '_native_groups', side_effect=AssertionError('No formal Original lookup')):
            panel.draw(self.context)
        namespace['_control_collections'].assert_not_called()
        def presets(layout):
            return [(layout, button) for button in layout.buttons if button.identifier == 'character_designer.bone_display'] + [
                entry for row in layout.rows for entry in presets(row)]
        found = presets(panel.layout)
        self.assertEqual(len(found), 4)
        self.assertTrue(all(not row.effective_enabled for row, _button in found))


if __name__ == '__main__':
    unittest.main()
