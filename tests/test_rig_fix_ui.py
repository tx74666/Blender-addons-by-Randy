"""Fix keeps placement separate from construction and reuses display-only actions."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '_test_rig_fix_package'


class Layout:
    def __init__(self, records=None, *, enabled=True):
        self.records = [] if records is None else records
        self.enabled = enabled

    def row(self, **kwargs):
        return Layout(self.records, enabled=self.enabled)

    box = row

    def label(self, **kwargs):
        self.records.append(('label', kwargs.get('text')))

    def operator(self, name, **kwargs):
        result = SimpleNamespace()
        self.records.append(('operator', name, self.enabled, result))
        return result

    def prop(self, owner, name, **kwargs):
        self.records.append(('prop', name, getattr(owner, name)))


def context(page='RIG', section='FIX', *, original=False, mode='POSE'):
    rig = SimpleNamespace(type='ARMATURE', is_editable=True, library=None,
                          override_library=None, data=SimpleNamespace(library=None),
                          original=original)
    return SimpleNamespace(object=rig, mode=mode,
                           window_manager=SimpleNamespace(character_designer=SimpleNamespace(
                               ui_page=page, rig_section=section)))


class RigFixUITests(unittest.TestCase):
    def setUp(self):
        package = ModuleType(PACKAGE)
        package.__path__ = [str(ROOT / 'addons/character_designer')]
        types, props = ModuleType('bpy.types'), ModuleType('bpy.props')
        types.Operator, types.Panel = type('Operator', (), {}), type('Panel', (), {})
        props.EnumProperty = lambda **kwargs: kwargs
        self.patcher = patch.dict(sys.modules, {
            PACKAGE: package, 'bpy': ModuleType('bpy'),
            'bpy.types': types, 'bpy.props': props,
        })
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.package = package
        self.constants = self.load('ui_constants')
        self.ui = self.load('rig_fix_ui')
        self.collection = object()
        self.display = SimpleNamespace(
            character_rig=Mock(side_effect=lambda ctx: ctx.object),
            groups=SimpleNamespace(body_collection=Mock(return_value=self.collection)),
            show_controls=Mock(),
        )
        self.original = SimpleNamespace(active=Mock(side_effect=lambda rig: rig.original))
        self.ground = SimpleNamespace(draw=Mock())
        self.rebuild = SimpleNamespace(rebuild=Mock(return_value={'sides': ('L', 'R')}))
        package.bone_display, package.body_original_mode = self.display, self.original
        package.character_ground = self.ground
        package.foot_controls_rebuild = self.rebuild
        package.body_region_ui = SimpleNamespace(draw=Mock())

    def load(self, name):
        spec = importlib.util.spec_from_file_location(
            PACKAGE + '.' + name, ROOT / 'addons/character_designer' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        setattr(self.package, name, module)
        spec.loader.exec_module(module)
        return module

    def test_legacy_fix_is_not_an_anatomy_tab_and_routes_to_body_without_mutation(self):
        self.assertEqual(tuple(item[0] for item in self.constants.UI_RIG_SECTION_ITEMS),
                         ('BODY', 'HAIR', 'SKIRT'))
        self.assertEqual(tuple(item[0] for item in self.constants.UI_RIG_SECTION_RNA_ITEMS),
                         ('BODY', 'HAIR', 'SKIRT', 'FIX'))
        ctx = context(section='FIX')
        self.assertEqual(self.constants.active_rig_section(ctx), 'BODY')
        self.assertEqual(ctx.window_manager.character_designer.rig_section, 'FIX')
        self.assertEqual(self.constants.active_rig_section(context(section='invalid')), 'BODY')
        self.assertEqual(self.constants.active_rig_section(context(page='CLOTHING')), 'SKIRT')

    def test_legacy_fix_panel_stays_hidden_on_every_page_without_changing_session(self):
        panel = self.ui.CHARACTERDESIGNER_PT_rig_fix
        for page, section, expected in (
                ('RIG', 'FIX', False), ('RIG', 'BODY', False), ('RIG', 'HAIR', False),
                ('RIG', 'SKIRT', False), ('MODELING', 'FIX', False),
                ('WEIGHT', 'FIX', False), ('ANIMATION', 'FIX', False),
                ('MISCELLANEOUS', 'FIX', False), ('CLOTHING', 'FIX', False)):
            with self.subTest(page=page, section=section):
                ctx = context(page, section)
                before = dict(vars(ctx.window_manager.character_designer))
                self.assertEqual(panel.poll(ctx), expected)
                self.assertEqual(vars(ctx.window_manager.character_designer), before)

    def test_body_adjustments_reuse_placement_and_display_without_a_foot_special_case(self):
        layout, ctx = Layout(), context()
        self.ui.draw_body_adjustments(layout, ctx)
        self.ground.draw.assert_called_once_with(layout, ctx)
        buttons = [row for row in layout.records if row[0] == 'operator']
        self.assertEqual([(row[1], row[2]) for row in buttons],
                         [('character_designer.fix_show_body_controls', True)])
        self.assertNotIn(('label', 'Moves meshes, rig and controls together.'), layout.records)
        self.assertIn(('label', 'Display only; keeps custom shapes.'), layout.records)
        self.display.show_controls.assert_not_called()
        self.rebuild.rebuild.assert_not_called()

    def test_original_mode_keeps_return_to_controls_explicit(self):
        layout, ctx = Layout(), context(original=True)
        self.ui.draw_body_adjustments(layout, ctx)
        self.assertFalse(next(row for row in layout.records
                              if row[:2] == ('operator', 'character_designer.fix_show_body_controls'))[2])
        self.assertIn(('label', 'Choose Controls in Bone Display first.'), layout.records)
        self.display.show_controls.assert_not_called()

    def test_absent_body_controls_cannot_be_generated_from_display_adjustments(self):
        self.display.groups.body_collection.return_value = None
        layout = Layout()
        self.ui.draw_body_adjustments(layout, context())
        self.assertFalse(next(row for row in layout.records
                              if row[:2] == ('operator', 'character_designer.fix_show_body_controls'))[2])
        self.assertIn(('label', 'Set up body controls first.'), layout.records)

    def test_display_error_remains_readable_without_creating_controls(self):
        self.display.character_rig.side_effect = ValueError('Choose one character rig.')
        layout = Layout()
        self.ui.draw_body_adjustments(layout, context())
        self.assertIn(('label', 'Choose one character rig.'), layout.records)
        self.assertFalse(any(row[:2] == ('operator', 'character_designer.fix_show_body_controls')
                             for row in layout.records))

    def test_show_controls_delegates_to_idempotent_body_display_backend(self):
        operator = self.ui.CHARACTERDESIGNER_OT_fix_show_body_controls()
        operator.report = Mock()
        ctx = context()
        self.assertEqual(operator.execute(ctx), {'FINISHED'})
        self.display.show_controls.assert_called_once_with(ctx, ctx.object, role='BODY', toggle=False)
        self.assertIn('UNDO', operator.bl_options)

    def test_show_controls_rejection_is_cancelled_with_the_backend_reason(self):
        operator = self.ui.CHARACTERDESIGNER_OT_fix_show_body_controls()
        operator.report = Mock()
        self.display.show_controls.side_effect = ValueError('Ownership needs review.')
        self.assertEqual(operator.execute(context()), {'CANCELLED'})
        operator.report.assert_called_once_with({'WARNING'}, 'Ownership needs review.')

    def test_show_controls_poll_is_local_pose_or_object_mode_outside_original(self):
        poll = self.ui.CHARACTERDESIGNER_OT_fix_show_body_controls.poll
        self.assertTrue(poll(context()))
        self.assertTrue(poll(context(mode='OBJECT')))
        self.assertFalse(poll(context(mode='EDIT_ARMATURE')))
        self.assertFalse(poll(context(original=True)))
        for owner, attr, value in (('rig', 'library', object()),
                                    ('rig', 'override_library', object()),
                                    ('rig', 'is_editable', False),
                                    ('rig', 'type', 'MESH'), ('data', 'library', object())):
            with self.subTest(owner=owner, attr=attr):
                ctx = context()
                setattr(ctx.object if owner == 'rig' else ctx.object.data, attr, value)
                self.assertFalse(poll(ctx))
        self.display.character_rig.assert_not_called()
        self.display.show_controls.assert_not_called()

    def foot_operator(self, side='BOTH'):
        operator = self.ui.CHARACTERDESIGNER_OT_fix_rebuild_foot_controls()
        operator.side, operator.report = side, Mock()
        return operator

    def test_foot_rebuild_defaults_to_both_and_routes_only_the_selected_side(self):
        prop = self.ui.CHARACTERDESIGNER_OT_fix_rebuild_foot_controls.__annotations__['side']
        self.assertEqual(prop['default'], 'BOTH')
        self.assertEqual(tuple(item[0] for item in prop['items']), ('BOTH', 'L', 'R'))
        for side, expected in (('BOTH', ('L', 'R')), ('L', ('L',)), ('R', ('R',))):
            with self.subTest(side=side):
                self.rebuild.rebuild.reset_mock()
                operator, ctx = self.foot_operator(side), context()
                self.assertEqual(operator.execute(ctx), {'FINISHED'})
                self.rebuild.rebuild.assert_called_once_with(ctx, ctx.object, sides=expected)
                self.assertEqual(operator.report.call_args.args[0], {'INFO'})
                self.display.show_controls.assert_not_called()

    def test_foot_rebuild_side_dialog_does_not_run_rebuild(self):
        operator, ctx = self.foot_operator(), context()
        ctx.window_manager.invoke_props_dialog = Mock(return_value={'RUNNING_MODAL'})
        self.assertEqual(operator.invoke(ctx, object()), {'RUNNING_MODAL'})
        ctx.window_manager.invoke_props_dialog.assert_called_once_with(operator, width=360)
        operator.layout = Layout()
        operator.draw(ctx)
        self.assertIn(('prop', 'side', 'BOTH'), operator.layout.records)
        self.assertIn(('label', 'Uses the current foot pose.'), operator.layout.records)
        self.rebuild.rebuild.assert_not_called()

    def test_foot_rebuild_preserves_core_guard_error_and_does_not_fall_back(self):
        for error in (ValueError('Leave Original mode first.'),
                      RuntimeError('Foot controls are animated.'),
                      ReferenceError('Control ownership needs review.')):
            with self.subTest(error=type(error).__name__):
                self.rebuild.rebuild.side_effect = error
                operator = self.foot_operator('R')
                self.assertEqual(operator.execute(context()), {'CANCELLED'})
                operator.report.assert_called_once_with({'WARNING'}, str(error))
                self.display.show_controls.assert_not_called()

    def test_foot_rebuild_poll_is_cheap_and_leaves_original_validation_to_core(self):
        poll = self.ui.CHARACTERDESIGNER_OT_fix_rebuild_foot_controls.poll
        self.assertTrue(poll(context()))
        self.assertTrue(poll(context(mode='OBJECT')))
        self.assertTrue(poll(context(original=True)))
        self.assertFalse(poll(context(mode='EDIT_ARMATURE')))
        for owner, attr, value in (('rig', 'library', object()),
                                    ('rig', 'override_library', object()),
                                    ('rig', 'is_editable', False),
                                    ('rig', 'type', 'MESH'), ('data', 'library', object())):
            with self.subTest(owner=owner, attr=attr):
                ctx = context()
                setattr(ctx.object if owner == 'rig' else ctx.object.data, attr, value)
                self.assertFalse(poll(ctx))
        self.display.character_rig.assert_not_called()
        self.rebuild.rebuild.assert_not_called()
        self.original.active.assert_not_called()


if __name__ == '__main__':
    unittest.main()
