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

    def test_main_panel_only_rebuilds_the_selected_side(self):
        _, records, button = self.draw('BOUND', region='RIGHT_LEG')
        self.assertEqual(button[2:4], ('Rebuild Controls', True))
        self.assertEqual((button[4].region, button[4].action), ('RIGHT_LEG', 'REBUILD'))
        self.assertEqual(len([row for row in records if row[0] == 'operator']), 1)
        self.assertIn(('prop', 'region', 'RIGHT_LEG'), records)
        self.assertIn(('label', 'Controls Ready'), records)

    def test_main_panel_omits_routine_help_and_stale_animation_paragraphs(self):
        hint = 'Release only these generated controls; keep native bones and other body regions.'
        warning = 'Saved animations retain earlier body proportions; check target compatibility before reuse.'
        _, records, _button = self.draw('BOUND', reason=hint + ' ' + warning)
        labels = [row[1] for row in records if row[0] == 'label']
        self.assertEqual(labels, ['Body Part', 'Controls Ready'])

    def test_advanced_release_uses_the_same_selected_part(self):
        ctx, layout = context('RIGHT_LEG'), Layout()
        service = SimpleNamespace(state=Mock(return_value={'status': 'BOUND'}))
        with patch.object(self.ui, '_service', return_value=service):
            self.ui.draw_advanced(layout, ctx)
        release = next(row for row in layout.records if row[0] == 'operator')
        self.assertEqual(release[2:4], ('Release to Edit', True))
        self.assertEqual((release[4].region, release[4].action), ('RIGHT_LEG', 'RELEASE'))
        service.state.assert_called_once_with(ctx.object, 'RIGHT_LEG')

    def test_advanced_does_not_offer_release_for_unavailable_or_foot_only_parts(self):
        for status in ('RELEASED', 'MISSING', 'MIXED', 'ERROR'):
            with self.subTest(status=status):
                layout = Layout()
                service = SimpleNamespace(state=Mock(return_value={'status': status}))
                with patch.object(self.ui, '_service', return_value=service):
                    self.ui.draw_advanced(layout, context())
                self.assertEqual(layout.records, [])
        for region in ('FEET', 'LEFT_FOOT', 'RIGHT_FOOT'):
            with self.subTest(region=region):
                layout = Layout()
                with patch.object(self.ui, '_state', side_effect=AssertionError('Foot teardown must stay unavailable')):
                    self.ui.draw_advanced(layout, context(region))
                self.assertEqual(layout.records, [])

    def test_released_rebuild_targets_only_the_selected_part(self):
        _, records, button = self.draw('RELEASED', region='LEFT_ARM')
        self.assertEqual(button[2:4], ('Rebuild Controls', True))
        self.assertEqual((button[4].region, button[4].action), ('LEFT_ARM', 'BIND'))
        self.assertIn(('label', 'Released'), records)

    def test_empty_part_has_no_enabled_release_or_implicit_build(self):
        _, records, button = self.draw('MISSING')
        self.assertFalse(button[3])
        self.assertEqual(button[4].action, 'REBUILD')
        self.assertIn(('label', 'Not Built'), records)
        self.assertEqual(len([row for row in records if row[0] == 'operator']), 1)

    def test_mixed_parts_require_explicit_side_selection(self):
        _, records, button = self.draw('MIXED')
        self.assertFalse(button[3])
        self.assertIn(('label', 'Different States'), records)

    def test_edit_mode_shows_released_state_but_blocks_rebuild(self):
        _, records, button = self.draw('RELEASED', mode='EDIT_ARMATURE')
        self.assertFalse(button[3])
        self.assertIn(('label', 'Released'), records)

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

    def test_recovered_operations_keep_an_undo_step_without_reporting_success(self):
        for action, method, label in (('RELEASE', 'release', 'Release'),
                                      ('BIND', 'bind', 'Control generation'),
                                      ('REBUILD', 'rebuild', 'Rebuild')):
            with self.subTest(action=action):
                ctx, operator = context(), self.operator(action=action)
                refusal = ValueError('The surface guard refused this change.')
                refusal._character_designer_body_rollback_restored = True
                service = SimpleNamespace(**{method: Mock(side_effect=refusal)})
                with patch.object(self.ui, '_service', return_value=service):
                    self.assertEqual(operator.execute(ctx), {'FINISHED'})
                getattr(service, method).assert_called_once_with(ctx, ctx.object, 'LEGS')
                operator.report.assert_called_once_with({'WARNING'},
                    label + ' was not applied; the previous settings were restored. '
                    'The surface guard refused this change.')

    def test_recovered_foot_only_failure_keeps_undo_without_entering_limb_service(self):
        ctx, operator = context('RIGHT_FOOT'), self.operator(action='REBUILD', region='RIGHT_FOOT')
        refusal = RuntimeError('The foot surface guard refused this change.')
        refusal._character_designer_body_rollback_restored = True
        with patch.object(self.ui, '_rebuild_feet', side_effect=refusal) as rebuild, \
             patch.object(self.ui, '_service', side_effect=AssertionError('Foot failure entered limb service')):
            self.assertEqual(operator.execute(ctx), {'FINISHED'})
        rebuild.assert_called_once_with(ctx, ctx.object, ('R',))
        operator.report.assert_called_once_with({'WARNING'},
            'Rebuild was not applied; the previous settings were restored. '
            'The foot surface guard refused this change.')

    def test_only_an_explicit_completed_recovery_marker_changes_failure_result(self):
        for marker in (None, False, 1, 'restored'):
            with self.subTest(marker=marker):
                ctx, operator = context(), self.operator(action='REBUILD')
                refusal = ValueError('The same guard reason.')
                if marker is not None:
                    refusal._character_designer_body_rollback_restored = marker
                service = SimpleNamespace(rebuild=Mock(side_effect=refusal))
                with patch.object(self.ui, '_service', return_value=service):
                    self.assertEqual(operator.execute(ctx), {'CANCELLED'})
                operator.report.assert_called_once_with({'WARNING'}, 'The same guard reason.')

    def test_a_recovered_failure_does_not_change_the_next_preflight_refusal(self):
        ctx, operator = context(), self.operator(action='REBUILD')
        restored, preflight = ValueError('First guard refusal.'), ValueError('A control is still animated.')
        restored._character_designer_body_rollback_restored = True
        service = SimpleNamespace(rebuild=Mock(side_effect=(restored, preflight)))
        with patch.object(self.ui, '_service', return_value=service):
            self.assertEqual(operator.execute(ctx), {'FINISHED'})
            operator.report.reset_mock()
            self.assertEqual(operator.execute(ctx), {'CANCELLED'})
        operator.report.assert_called_once_with({'WARNING'}, 'A control is still animated.')
        self.assertEqual(service.rebuild.call_count, 2)

    def test_existing_limb_rebuild_routes_only_to_selected_part(self):
        ctx = context('LEFT_ARM')
        operator = self.operator(action='REBUILD', region='LEFT_ARM')
        service = SimpleNamespace(release=Mock(), bind=Mock(), rebuild=Mock())
        with patch.object(self.ui, '_service', return_value=service):
            self.assertEqual(operator.execute(ctx), {'FINISHED'})
        service.rebuild.assert_called_once_with(ctx, ctx.object, 'LEFT_ARM')
        service.release.assert_not_called()
        service.bind.assert_not_called()

    def test_feet_append_to_the_existing_shared_part_selector(self):
        parts = tuple(item[0] for item in self.ui.REGION_ITEMS)
        self.assertEqual(parts[:6], ('LEGS', 'LEFT_LEG', 'RIGHT_LEG', 'ARMS', 'LEFT_ARM', 'RIGHT_ARM'))
        self.assertEqual(parts[6:], ('FEET', 'LEFT_FOOT', 'RIGHT_FOOT'))

    def test_foot_only_rebuild_routes_selected_sides_without_limb_release(self):
        service = SimpleNamespace(release=Mock(), bind=Mock(), rebuild=Mock())
        for region, sides in (('FEET', ('L', 'R')), ('LEFT_FOOT', ('L',)), ('RIGHT_FOOT', ('R',))):
            with self.subTest(region=region):
                ctx, operator = context(region), self.operator(action='REBUILD', region=region)
                with patch.object(self.ui, '_service', return_value=service), \
                     patch.object(self.ui, '_rebuild_feet') as rebuild:
                    self.assertEqual(operator.execute(ctx), {'FINISHED'})
                rebuild.assert_called_once_with(ctx, ctx.object, sides)
        service.release.assert_not_called()
        service.bind.assert_not_called()
        service.rebuild.assert_not_called()

    def test_foot_only_status_never_offers_limb_release_or_implicit_generation(self):
        for records, expected_status, enabled in (({'L': {}, 'R': {}}, 'Controls Ready', True),
                                                  ({'L': {}}, 'Different States', False),
                                                  ({}, 'Not Built', False)):
            with self.subTest(status=expected_status):
                ctx, layout = context('FEET'), Layout()
                with patch.object(self.ui, '_foot_records', return_value=records), \
                     patch.object(self.ui, '_service', side_effect=AssertionError('Foot draw entered limb service')):
                    self.ui.draw(layout, ctx)
                buttons = [row for row in layout.records if row[0] == 'operator']
                self.assertEqual(len(buttons), 1)
                self.assertEqual(buttons[0][2:4], ('Rebuild Controls', enabled))
                self.assertEqual((buttons[0][4].region, buttons[0][4].action), ('FEET', 'REBUILD'))
                self.assertIn(('label', expected_status), layout.records)

    def test_foot_only_release_or_bind_refuses_without_mutating_a_limb(self):
        service = SimpleNamespace(release=Mock(), bind=Mock(), rebuild=Mock())
        for action in ('RELEASE', 'BIND'):
            with self.subTest(action=action):
                operator = self.operator(action=action, region='LEFT_FOOT')
                with patch.object(self.ui, '_service', return_value=service), \
                     patch.object(self.ui, '_rebuild_feet') as rebuild:
                    self.assertEqual(operator.execute(context('LEFT_FOOT')), {'CANCELLED'})
                rebuild.assert_not_called()
        service.release.assert_not_called()
        service.bind.assert_not_called()
        service.rebuild.assert_not_called()

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
