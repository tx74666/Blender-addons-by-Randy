"""Original Pose means the same guarded pose transfer on every sidebar page."""
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


class Layout:
    def __init__(self, parent=None):
        self.parent, self.enabled = parent, True
        self.rows, self.labels, self.buttons = [], [], []

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


class OriginalPoseEntryTests(unittest.TestCase):
    def setUp(self):
        prefix = '_original_pose_entry_' + uuid.uuid4().hex
        package = ModuleType(prefix)
        package.__path__ = [str(SOURCE.parent)]
        bpy, props = ModuleType('bpy'), ModuleType('bpy.props')
        bpy.types = NS(Operator=object)
        props.EnumProperty = Mock(return_value=None)
        self.rig = Rig(artist_marker='keep current work')
        self.rig.data = NS(collections_all={'Original': object()})
        self.context = NS(mode='POSE', page='WEIGHT')
        self.display = NS(character_rig=Mock(return_value=self.rig),
                          view_mode=Mock(return_value=None), restore_view=Mock(), show_controls=Mock(),
                          ensure_controls_selectable=Mock())
        self.groups = NS(body_collection=Mock(return_value=object()))
        self.paint = NS(owns_rig=Mock(return_value=False), active=Mock(return_value=False),
                        phase=Mock(return_value=None), controls_display_needs_repair=Mock(return_value=False),
                        native_painting=Mock(return_value=False), last_finish_status=Mock(return_value=None),
                        show_painting=Mock(), show_controls=Mock())
        modules = {'bpy': bpy, 'bpy.props': props, prefix: package,
                   prefix + '.bone_display': self.display, prefix + '.bone_collections': self.groups,
                   prefix + '.limb_ik': NS(), prefix + '.limb_ik_fk': NS(),
                   prefix + '.control_pose_assets': NS(), prefix + '.skirt_original_mode': NS(),
                   prefix + '.weight_paint_entry': self.paint,
                   prefix + '.ui_constants': NS(active_ui_page=lambda context: context.page, UI_PAGE_WEIGHT='WEIGHT')}
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        name = prefix + '.body_original_mode'
        spec = importlib.util.spec_from_file_location(name, SOURCE)
        self.original = importlib.util.module_from_spec(spec)
        sys.modules[name] = self.original
        spec.loader.exec_module(self.original)
        self.enter = patch.object(self.original, 'enter', return_value={'bones': 10}).start()
        self.leave = patch.object(self.original, 'leave', return_value=True).start()
        self.upgrade = patch.object(self.original, 'ensure_dress_editable').start()
        self.legacy_weight = patch.object(self.original, '_weight_execute').start()
        self.addCleanup(patch.stopall)

    def operator(self, action):
        operator = self.original.CHARACTERDESIGNER_OT_body_original_mode()
        operator.action, operator.report = action, Mock()
        return operator

    def formal_buttons(self, layout):
        return [button for row in layout.rows[0].rows for button in row.buttons]

    def painting(self, phase, *, local=True, mode='POSE'):
        self.paint.owns_rig.return_value = True
        self.paint.active.return_value = local
        self.paint.phase.return_value = phase
        self.context.mode = mode

    def assert_no_paint_transfer(self):
        self.legacy_weight.assert_not_called()
        self.paint.show_painting.assert_not_called()
        self.paint.show_controls.assert_not_called()

    def test_original_dispatches_formal_pose_transfer_on_every_page(self):
        for page in ('RIG', 'WEIGHT', 'ANIMATION'):
            with self.subTest(page=page):
                self.context.page = page
                operator = self.operator('ORIGINAL')
                self.assertEqual(operator.execute(self.context), {'FINISHED'})
                self.enter.assert_called_with(self.context, self.rig)
                self.leave.assert_not_called()
                self.assert_no_paint_transfer()
        self.assertEqual(self.enter.call_count, 3)

    def test_controls_returns_active_formal_session_on_every_page(self):
        self.rig[self.original.SESSION] = 'retained recovery record'
        before = dict(self.rig)
        for page in ('RIG', 'WEIGHT'):
            self.context.page = page
            self.assertEqual(self.operator('CONTROLS').execute(self.context), {'FINISHED'})
            self.leave.assert_called_with(self.context, self.rig)
            self.display.show_controls.assert_not_called()
            self.display.restore_view.assert_not_called()
            self.assert_no_paint_transfer()
            self.assertEqual(dict(self.rig), before)

    def test_idle_controls_actually_shows_normal_controls(self):
        for page in ('RIG', 'WEIGHT'):
            self.context.page = page
            self.assertEqual(self.operator('CONTROLS').execute(self.context), {'FINISHED'})
            self.display.ensure_controls_selectable.assert_called_with(self.context, self.rig)
            self.leave.assert_not_called()
            self.display.restore_view.assert_not_called()
            self.assert_no_paint_transfer()

    def test_controls_restores_existing_display_view_before_idle_display(self):
        self.display.view_mode.return_value = 'HAIR'
        self.assertEqual(self.operator('CONTROLS').execute(self.context), {'FINISHED'})
        self.display.restore_view.assert_called_once_with(self.rig)
        self.display.ensure_controls_selectable.assert_called_once_with(self.context, self.rig)
        self.display.show_controls.assert_not_called()
        self.leave.assert_not_called()

    def test_toggle_keeps_formal_entry_and_return_semantics(self):
        self.assertEqual(self.operator('TOGGLE').execute(self.context), {'FINISHED'})
        self.enter.assert_called_once_with(self.context, self.rig)
        self.rig[self.original.SESSION] = 'current original recovery'
        self.assertEqual(self.operator('TOGGLE').execute(self.context), {'FINISHED'})
        self.leave.assert_called_once_with(self.context, self.rig)
        self.assert_no_paint_transfer()

    def test_formal_poll_never_accepts_native_paint_or_edit_mode(self):
        for page in ('RIG', 'WEIGHT', 'ANIMATION'):
            self.context.page = page
            for mode in ('OBJECT', 'POSE', 'PAINT_WEIGHT', 'EDIT_ARMATURE'):
                with self.subTest(page=page, mode=mode):
                    self.context.mode = mode
                    self.assertEqual(self.original.CHARACTERDESIGNER_OT_body_original_mode.poll(self.context),
                                     mode in {'OBJECT', 'POSE'})

    def test_painting_blocks_f3_formal_actions_before_any_transfer(self):
        self.paint.owns_rig.return_value = True
        self.assertFalse(self.original.CHARACTERDESIGNER_OT_body_original_mode.poll(self.context))
        before = dict(self.rig)
        for page in ('RIG', 'WEIGHT'):
            self.context.page = page
            for action in ('ORIGINAL', 'CONTROLS', 'TOGGLE'):
                with self.subTest(page=page, action=action):
                    operator = self.operator(action)
                    self.assertEqual(operator.execute(self.context), {'CANCELLED'})
                    self.assertIn('Controls', operator.report.call_args.args[1])
        self.enter.assert_not_called()
        self.leave.assert_not_called()
        self.display.restore_view.assert_not_called()
        self.display.show_controls.assert_not_called()
        self.assert_no_paint_transfer()
        self.assertEqual(dict(self.rig), before)

    def test_direct_unsupported_mode_controls_never_changes_display(self):
        for mode in ('PAINT_WEIGHT', 'EDIT_ARMATURE'):
            self.context.mode = mode
            for action in ('ORIGINAL', 'CONTROLS', 'TOGGLE'):
                self.assertEqual(self.operator(action).execute(self.context), {'CANCELLED'})
        self.enter.assert_not_called()
        self.leave.assert_not_called()
        self.display.ensure_controls_selectable.assert_not_called()
        self.display.restore_view.assert_not_called()

    def test_rig_keeps_its_guarded_direct_pose_editor(self):
        self.context.page = 'RIG'
        self.display.view_mode.return_value = 'ORIGINAL'
        layout = Layout()
        self.assertFalse(self.original.draw(layout, self.context))
        buttons = self.formal_buttons(layout)
        self.assertEqual([b.text for b in buttons], ['Original Bones', 'Controls'])
        self.assertEqual([b.action for b in buttons], ['ORIGINAL', 'CONTROLS'])
        self.assertTrue(all(not hasattr(b, 'icon') for b in buttons))
        self.assertEqual([b.depress for b in buttons], [True, False])

    def test_weight_idle_original_bones_is_the_native_paint_entry(self):
        layout = Layout()
        self.original.draw(layout, self.context)
        row, = layout.rows
        paint, controls = row.rows
        self.assertEqual([r.buttons[0].text for r in row.rows], ['Original Bones', 'Controls'])
        self.assertTrue(all(not hasattr(r.buttons[0], 'icon') for r in row.rows))
        self.assertEqual(paint.buttons[0].identifier, 'character_designer.paint_weights')
        self.assertTrue(paint.effective_enabled)
        self.assertEqual(controls.buttons[0].action, 'CONTROLS')

    def test_old_preview_and_paused_paint_can_resume_or_finish(self):
        for phase, mode in [('POSE_PREVIEW','POSE'), ('PAINT','POSE'), ('PAINT','PAINT_WEIGHT')]:
            with self.subTest(phase=phase, mode=mode):
                self.painting(phase, mode=mode)
                layout = Layout()
                self.original.draw(layout, self.context)
                row, = layout.rows
                paint, controls = row.rows
                self.assertEqual(paint.effective_enabled, mode != 'PAINT_WEIGHT')
                self.assertEqual(paint.buttons[0].depress, mode == 'PAINT_WEIGHT')
                self.assertEqual(controls.buttons[0].depress, phase == 'POSE_PREVIEW')
                self.assertEqual(paint.buttons[0].identifier, 'character_designer.resume_weight_paint')
                self.assertEqual(controls.buttons[0].identifier, 'character_designer.preview_weight_pose')
                self.assertTrue(controls.effective_enabled)

    def test_original_edit_shows_its_active_state_and_return_on_both_pages(self):
        self.rig[self.original.SESSION] = 'retained'
        for page in ('WEIGHT', 'RIG'):
            with self.subTest(page=page):
                self.context.page = page
                layout = Layout()
                self.assertTrue(self.original.draw(layout, self.context))
                original, controls = self.formal_buttons(layout)
                self.assertTrue(original.depress)
                self.assertFalse(controls.depress)
                self.assertEqual(controls.action, 'CONTROLS')

    def test_temporary_display_must_return_before_painting(self):
        self.display.view_mode.return_value = 'HAIR'
        layout = Layout()
        self.original.draw(layout, self.context)
        paint, controls = layout.rows[0].rows
        self.assertFalse(paint.effective_enabled)
        self.assertTrue(controls.effective_enabled)
        self.assertEqual(controls.buttons[0].action, 'CONTROLS')

    def test_cross_scene_paint_owner_disables_both_routes(self):
        self.painting('PAINT', local=False)
        layout = Layout()
        self.original.draw(layout, self.context)
        self.assertFalse(layout.rows[0].effective_enabled)
        self.assertIn('Return to the painting Scene and View Layer.', [label['text'] for label in layout.labels])
        self.paint.phase.assert_not_called()

    def test_original_admission_failure_stays_a_failure_and_never_starts_painting(self):
        self.enter.side_effect = ValueError('Original owner proof rejected current references.')
        operator = self.operator('ORIGINAL')
        before = dict(self.rig)
        self.assertEqual(operator.execute(self.context), {'CANCELLED'})
        operator.report.assert_called_once_with({'WARNING'}, 'Original owner proof rejected current references.')
        self.assertEqual(dict(self.rig), before)
        self.assert_no_paint_transfer()


if __name__ == '__main__':
    unittest.main()
