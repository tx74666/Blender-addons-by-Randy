"""Pure reader parity/omission checks; stubs do not claim native performance."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
PACKAGE = '_cd_wrist_content_reader_tests'
package = ModuleType(PACKAGE)
package.__path__ = [str(BASE / 'addons/character_designer')]
sys.modules[PACKAGE] = package


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


fp = load(PACKAGE + '.animation_worklist_fingerprint', BASE / 'addons/character_designer/animation_worklist_fingerprint.py')
content = load(PACKAGE + '.limb_ik_wrist_content', BASE / 'addons/character_designer/limb_ik_wrist_content.py')
fixtures = load('_cd_wrist_content_fixture_stubs', BASE / 'tests/test_animation_worklist_fingerprint.py')
ENUMS = {'interpolation': ('CONSTANT', 'LINEAR', 'BEZIER'), 'easing': ('AUTO', 'EASE_IN'),
         'type': ('KEYFRAME', 'BREAKDOWN'), 'handle_left_type': ('FREE', 'AUTO'),
         'handle_right_type': ('FREE', 'AUTO')}


class Points(list):
    def foreach_get(self, name, target):
        values = []
        for point in self:
            prop = next(p for p in point.bl_rna.properties if p.identifier == name)
            value = getattr(point, name)
            if prop.type == 'ENUM':
                value = next(p.value for p in prop.enum_items if p.identifier == value)
            values.extend(content._flatten(value) if prop.is_array else (value,))
        target[:] = type(target)(target.typecode, values)


def fixture():
    action = fixtures.action([fixtures.curve('pose.bones["Wrist"].rotation_euler', i) for i in range(3)])
    for curve in action.layers[0].strips[0].channelbags[0].fcurves:
        curve.keyframe_points = Points(fixtures.key(float(i), i * .13) for i in range(20))
        for point in curve.keyframe_points:
            point.bl_rna.identifier = 'Keyframe'
            for prop in point.bl_rna.properties:
                prop.array_length = len(getattr(point, prop.identifier)) if prop.is_array else 0
                prop.is_enum_flag = False
                if prop.identifier in ENUMS:
                    prop.type = 'ENUM'
                    prop.enum_items = [SimpleNamespace(identifier=v, value=i * 3 + 1)
                                       for i, v in enumerate(ENUMS[prop.identifier])]
    return action


def curves(action):
    return action.layers[0].strips[0].channelbags[0].fcurves


class ContentReaderTests(unittest.TestCase):
    def bulk(self):
        # Explicit pure transport double; not a native capability assertion.
        return patch.object(content._Writer, '_native_points', return_value=True)

    def test_bulk_scalar_exact_and_legacy_reader_still_known(self):
        action = fixture()
        self.assertTrue(fp.fingerprint_action(action, 4)['fingerprint_known'])
        with self.bulk():
            diagnostics = {}
            fast = content.action_digest(action, 4, diagnostics=diagnostics)
            slow = content.action_digest(action, 4, force_scalar=True)
        self.assertEqual(fast, slow)
        self.assertGreater(diagnostics['bulk_fields'], 0)
        self.assertEqual(diagnostics['points'], 60)

    def test_optional_numeric_acceleration_and_missing_numpy_keep_identical_proofs(self):
        action = fixture()
        curves(action)[1].keyframe_points[8].co = (-0., -0.)
        with self.bulk():
            typed = content.action_digest(action, 4)
            legacy = content.legacy_action_hash(action, 4)
            with patch.dict(sys.modules, {'numpy': None}):
                self.assertEqual(typed, content.action_digest(action, 4))
                self.assertEqual(legacy, content.legacy_action_hash(action, 4))
                curves(action)[1].keyframe_points[8].co = (8., float('inf'))
                with self.assertRaises(ValueError):
                    content.action_digest(action, 4)

    def test_numeric_acceleration_preserves_finite_bits_and_rejects_each_nonfinite(self):
        from array import array
        import math
        values = array('d', [0., -0., 5e-324, -5e-324, sys.float_info.max, -sys.float_info.max])
        expected = array('d', values)
        expected[1] = 0.
        content._finite_numbers(values)
        self.assertEqual(values.tobytes(), expected.tobytes())
        for number in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(number=number), self.assertRaises(ValueError):
                content._finite_numbers(array('d', [1., number, 2.]))

    def test_legacy_bytes_match_original_for_bulk_and_scalar(self):
        action = fixture()
        expected = fp.fingerprint_action(action, 4)['action_fingerprint']
        with self.bulk():
            self.assertEqual(expected, content.legacy_action_hash(action, 4))
            self.assertEqual(expected, content.legacy_action_hash(action, 4, force_scalar=True))

    def test_legacy_bytes_preserve_every_changed_field(self):
        action = fixture()
        changes = {'co': (8., .123), 'handle_left': (7.5, .456), 'handle_right': (8.5, .789),
                   'interpolation': 'LINEAR', 'easing': 'EASE_IN', 'type': 'BREAKDOWN',
                   'handle_left_type': 'AUTO', 'handle_right_type': 'AUTO',
                   'amplitude': 1.25, 'back': 1.25, 'period': 1.25}
        for name, value in changes.items():
            with self.subTest(name=name), self.bulk():
                changed = copy.deepcopy(action)
                setattr(curves(changed)[1].keyframe_points[8], name, value)
                original = fp.fingerprint_action(changed, 4)
                self.assertTrue(original['fingerprint_known'])
                self.assertEqual(original['action_fingerprint'], content.legacy_action_hash(changed, 4))

    def test_legacy_complex_schema_and_modifier_keep_original_guards(self):
        action = fixture()
        for point in curves(action)[0].keyframe_points:
            point.flags = {'A', 'B'}
            point.bl_rna.properties.append(SimpleNamespace(identifier='flags', type='ENUM',
                is_array=False, is_enum_flag=True))
        curves(action)[1].modifiers.append(fixtures.RNA(strength=.7, points=[fixtures.RNA(frame=2., value=.5)],
            kinds={'points':'COLLECTION'}))
        with self.bulk():
            self.assertEqual(fp.fingerprint_action(action,4)['action_fingerprint'],content.legacy_action_hash(action,4))

    def test_legacy_native_transport_failure_has_exact_original_fallback(self):
        action = fixture()
        expected = fp.fingerprint_action(action,4)['action_fingerprint']
        with self.bulk(), patch.object(Points, 'foreach_get', side_effect=TypeError('unsupported')):
            self.assertEqual(expected,content.legacy_action_hash(action,4))

    def test_every_key_motion_field_mutation_is_seen(self):
        action = fixture()
        baseline = content.action_digest(action, 4)
        changes = {'co': (8., .123), 'handle_left': (7.5, .456), 'handle_right': (8.5, .789),
                   'interpolation': 'LINEAR', 'easing': 'EASE_IN', 'type': 'BREAKDOWN',
                   'handle_left_type': 'AUTO', 'handle_right_type': 'AUTO',
                   'amplitude': 1.25, 'back': 1.25, 'period': 1.25}
        for name, value in changes.items():
            with self.subTest(name=name), self.bulk():
                changed = copy.deepcopy(action)
                setattr(curves(changed)[1].keyframe_points[8], name, value)
                fast = content.action_digest(changed, 4)
                self.assertNotEqual(baseline, fast)
                self.assertEqual(fast, content.action_digest(changed, 4, force_scalar=True))

    def test_ignored_ui_and_negative_zero_keep_motion_semantics(self):
        action = fixture()
        baseline = content.action_digest(action, 4)
        point = curves(action)[0].keyframe_points[0]
        point.co = (-0., -0.)
        point.select_left_handle = True
        point.select_control_point = True
        with self.bulk():
            self.assertEqual(baseline, content.action_digest(action, 4))

    def test_native_transport_failure_uses_exact_scalar_encoding(self):
        action = fixture()
        expected = content.action_digest(action, 4, force_scalar=True)
        with self.bulk(), patch.object(Points, 'foreach_get', side_effect=TypeError('unsupported')):
            self.assertEqual(expected, content.action_digest(action, 4))

    def test_unknown_interior_enum_code_refuses(self):
        action = fixture()
        original = Points.foreach_get
        def corrupted(points, name, target):
            original(points, name, target)
            if name == 'interpolation':
                target[8] = 999
        with self.bulk(), patch.object(Points, 'foreach_get', corrupted):
            with self.assertRaisesRegex(fp.FingerprintUnknown, 'enum code'):
                content.action_digest(action, 4)

    def test_nonfinite_interior_coordinate_refuses(self):
        action = fixture()
        curves(action)[0].keyframe_points[8].co = (8., float('nan'))
        with self.bulk(), self.assertRaisesRegex(fp.FingerprintUnknown, 'non-finite'):
            content.action_digest(action, 4)

    def test_new_pointer_and_unsupported_RNA_are_never_omitted(self):
        for kind in ('POINTER', 'FUTURE_KIND'):
            action = fixture()
            for point in curves(action)[0].keyframe_points:
                point.future = object()
                point.bl_rna.properties.append(SimpleNamespace(identifier='future', type=kind, is_array=False))
            with self.subTest(kind=kind), self.bulk(), self.assertRaises(fp.FingerprintUnknown):
                content.action_digest(action, 4)

    def test_enum_flags_keep_every_identifier(self):
        action = fixture()
        for curve in curves(action):
            for point in curve.keyframe_points:
                point.flags = {'A'}
                point.bl_rna.properties.append(SimpleNamespace(identifier='flags', type='ENUM',
                    is_array=False, is_enum_flag=True))
        baseline = content.action_digest(action, 4)
        curves(action)[1].keyframe_points[8].flags.add('B')
        with self.bulk():
            changed = content.action_digest(action, 4)
            self.assertNotEqual(baseline, changed)
            self.assertEqual(changed, content.action_digest(action, 4, force_scalar=True))

    def test_unaffected_omits_only_exact_selected_slot_channels(self):
        action = fixture()
        foreign = SimpleNamespace(slot_handle=7, fcurves=copy.deepcopy(curves(action)))
        action.layers[0].strips[0].channelbags.append(foreign)
        action.slots.append(SimpleNamespace(handle=7, target_id_type='OBJECT'))
        affected = {('Wrist', 'rotation_euler', 0)}
        baseline = content.unaffected_digest(action, 4, affected)
        curves(action)[0].keyframe_points[8].co = (8., 33.)
        action.frame_range = (-100., 100.)  # Derived from an allowed wrist insertion.
        self.assertEqual(baseline, content.unaffected_digest(action, 4, affected))
        foreign.fcurves[0].keyframe_points[8].co = (8., 66.)
        self.assertNotEqual(baseline, content.unaffected_digest(action, 4, affected))

    def test_unaffected_layer_settings_and_other_components_remain_guarded(self):
        action = fixture()
        affected = {('Wrist', 'rotation_euler', 0)}
        baseline = content.unaffected_digest(action, 4, affected)
        action.layers[0].influence = .5
        self.assertNotEqual(baseline, content.unaffected_digest(action, 4, affected))
        action.layers[0].influence = 1.
        curves(action)[1].keyframe_points[8].period = 2.
        self.assertNotEqual(baseline, content.unaffected_digest(action, 4, affected))

    def test_mixed_non_native_RNA_schema_refuses(self):
        action = fixture()
        point = curves(action)[0].keyframe_points[8]
        point.future = 3.
        point.bl_rna.properties.append(SimpleNamespace(identifier='future', type='FLOAT', is_array=False))
        with self.assertRaisesRegex(fp.FingerprintUnknown, 'schema changed'):
            content.action_digest(action, 4)

    def test_invalid_slot_and_driver_still_refuse(self):
        action = fixture()
        with self.assertRaises(fp.FingerprintUnknown):
            content.action_digest(action, 999)
        curves(action)[0].driver = object()
        with self.assertRaises(fp.FingerprintUnknown):
            content.action_digest(action, 4)


if __name__ == '__main__':
    unittest.main()
