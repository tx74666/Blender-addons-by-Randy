"""Regional UI routes the selected part and keeps absent or mixed parts disabled."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


def load_subject():
    bpy = ModuleType('bpy')
    props = ModuleType('bpy.props')
    props.EnumProperty = lambda **kwargs: kwargs
    types = ModuleType('bpy.types')
    types.Operator = type('Operator', (), {})
    types.PropertyGroup = type('PropertyGroup', (), {})
    path = Path(__file__).resolve().parents[1] / 'addons/character_designer/body_region_ui.py'
    spec = importlib.util.spec_from_file_location('_test_body_region_ui_subject', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'bpy': bpy, 'bpy.props': props, 'bpy.types': types}):
        spec.loader.exec_module(module)
    return module


class Layout:
    def __init__(self, records=None, enabled=True):
        self.records = records if records is not None else []
        self.enabled = enabled

    def row(self, **kwargs):
        return Layout(self.records, self.enabled)

    box = column = row

    def prop(self, owner, name, **kwargs):
        self.records.append(('prop', name, getattr(owner, name)))

    def label(self, **kwargs):
        self.records.append(('label', kwargs.get('text', '')))

    def operator(self, name, **kwargs):
        operator = SimpleNamespace()
        self.records.append(('operator', name, kwargs.get('text'), self.enabled, operator))
        return operator


def context(region='LEGS', mode='POSE'):
    data = SimpleNamespace(library=None, users=1, is_editable=True)
    rig = SimpleNamespace(type='ARMATURE', is_editable=True, library=None,
                          override_library=None, data=data)
    return SimpleNamespace(object=rig, mode=mode, scene=SimpleNamespace(
        character_designer_body_region=SimpleNamespace(region=region)))


class RegionalControlsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = load_subject()

    def draw(self, status, *, region='LEGS', mode='POSE', reason=''):
        ctx = context(region, mode)
        service = SimpleNamespace(state=Mock(return_value={'status': status, 'reason': reason}))
        layout = Layout()
        with patch.object(self.ui, '_service', return_value=service):
            self.ui.draw(layout, ctx)
        service.state.assert_called_once_with(ctx.object, region)
        button = next(row for row in layout.records if row[0] == 'operator')
        return ctx, layout.records, button

    def test_bound_release_targets_the_selected_side(self):
        _, records, button = self.draw('BOUND', region='RIGHT_LEG')
        self.assertEqual(button[2:4], ('Release Controls', True))
        self.assertEqual((button[4].region, button[4].action), ('RIGHT_LEG', 'RELEASE'))
        self.assertIn(('label', 'Right Leg: Controls Ready'), records)

    def test_released_rebuild_targets_only_the_selected_part(self):
        _, records, button = self.draw('RELEASED', region='LEFT_ARM')
        self.assertEqual(button[2:4], ('Rebuild Controls', True))
        self.assertEqual((button[4].region, button[4].action), ('LEFT_ARM', 'BIND'))
        self.assertIn(('label', 'Adjust original bones, then rebuild.'), records)

    def test_empty_part_has_no_enabled_release_or_implicit_build(self):
        _, records, button = self.draw('MISSING')
        self.assertFalse(button[3])
        self.assertEqual(button[4].action, 'RELEASE')
        self.assertIn(('label', 'Both Legs: Not Built'), records)
        self.assertIn(('label', 'No controls to release for this part.'), records)

    def test_mixed_parts_require_explicit_side_selection(self):
        _, records, button = self.draw('MIXED')
        self.assertFalse(button[3])
        self.assertIn(('label', 'Select the left or right side separately.'), records)

    def test_edit_mode_shows_released_state_but_blocks_rebuild(self):
        _, records, button = self.draw('RELEASED', mode='EDIT_ARMATURE')
        self.assertFalse(button[3])
        self.assertIn(('label', 'Finish bone editing to rebuild.'), records)

    def test_operator_rejects_linked_override_or_shared_armature(self):
        for target, attr, value in (('rig', 'library', object()),
                                    ('rig', 'override_library', object()),
                                    ('rig', 'is_editable', False),
                                    ('data', 'library', object()),
                                    ('data', 'users', 2),
                                    ('data', 'is_editable', False)):
            with self.subTest(target=target, attr=attr):
                ctx = context()
                setattr(ctx.object if target == 'rig' else ctx.object.data, attr, value)
                self.assertFalse(self.ui.CHARACTERDESIGNER_OT_body_region.poll(ctx))

    def operator(self, *, action='RELEASE', region='LEGS'):
        operator = self.ui.CHARACTERDESIGNER_OT_body_region()
        operator.action = action
        operator.region = region
        operator.report = Mock()
        return operator

    def test_release_and_bind_route_to_separate_backend_operations(self):
        for action, method in (('RELEASE', 'release'), ('BIND', 'bind')):
            with self.subTest(action=action):
                ctx = context('RIGHT_ARM')
                service = SimpleNamespace(release=Mock(), bind=Mock())
                operator = self.operator(action=action, region='RIGHT_ARM')
                with patch.object(self.ui, '_service', return_value=service):
                    self.assertEqual(operator.execute(ctx), {'FINISHED'})
                getattr(service, method).assert_called_once_with(ctx, ctx.object, 'RIGHT_ARM')
                getattr(service, 'bind' if method == 'release' else 'release').assert_not_called()

    def test_rejected_operation_is_cancelled_with_backend_reason(self):
        ctx = context()
        operator = self.operator()
        service = SimpleNamespace(release=Mock(side_effect=ValueError('A control is still animated.')))
        with patch.object(self.ui, '_service', return_value=service):
            self.assertEqual(operator.execute(ctx), {'CANCELLED'})
        operator.report.assert_called_once_with({'WARNING'}, 'A control is still animated.')

    def test_draw_validation_error_stays_readable_and_disabled(self):
        ctx = context()
        service = SimpleNamespace(state=Mock(side_effect=ValueError('Region record needs review.')))
        layout = Layout()
        with patch.object(self.ui, '_service', return_value=service):
            self.ui.draw(layout, ctx)
        self.assertIn(('label', 'Region record needs review.'), layout.records)
        self.assertFalse(next(row for row in layout.records if row[0] == 'operator')[3])


if __name__ == '__main__':
    unittest.main()
