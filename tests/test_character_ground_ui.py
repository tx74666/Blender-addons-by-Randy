"""Placement UI never evaluates geometry while drawing or polling."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch


def load_subject():
    props, types, maths = ModuleType('bpy.props'), ModuleType('bpy.types'), ModuleType('mathutils')
    props.FloatProperty = lambda **kwargs: kwargs
    types.Operator, types.PropertyGroup = type('Operator', (), {}), type('PropertyGroup', (), {})
    maths.Matrix, maths.Vector = Mock(), Mock()
    path = Path(__file__).resolve().parents[1] / 'addons/character_designer/character_ground.py'
    spec = importlib.util.spec_from_file_location('_character_ground_ui_subject', path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'bpy': ModuleType('bpy'), 'bpy.props': props,
                                 'bpy.types': types, 'mathutils': maths}):
        spec.loader.exec_module(module)
    return module


class Layout:
    def __init__(self):
        self.buttons, self.props, self.labels = [], [], []

    def row(self, **kwargs):
        return self

    box = row

    def prop(self, owner, name, **kwargs):
        self.props.append((name, getattr(owner, name)))

    def label(self, **kwargs):
        self.labels.append(kwargs.get('text'))

    def operator(self, name, **kwargs):
        self.buttons.append((name, kwargs.get('text')))
        return SimpleNamespace()


def context(mode='POSE'):
    return SimpleNamespace(object=SimpleNamespace(type='ARMATURE', is_editable=True,
                           library=None, override_library=None), mode=mode,
                           scene=SimpleNamespace(character_designer_ground=SimpleNamespace(distance=-.15)))


class CharacterPlacementUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = load_subject()

    def test_draw_only_exposes_automatic_move_to_floor_without_evaluation(self):
        layout = Layout()
        with patch.object(self.ui, '_prepare', side_effect=AssertionError('draw evaluated geometry')):
            self.ui.draw(layout, context())
        self.assertEqual(layout.props, [])
        self.assertEqual(layout.buttons, [('character_designer.ground_character', 'Move to Floor')])
        self.assertEqual(layout.labels, [])
        self.assertEqual(self.ui.CHARACTERDESIGNER_OT_ground_character.bl_label, 'Move to Floor')

    def test_poll_rejects_edit_mode_linked_and_overridden_objects_without_evaluation(self):
        with patch.object(self.ui, '_prepare', side_effect=AssertionError('poll evaluated geometry')):
            self.assertTrue(self.ui.CHARACTERDESIGNER_OT_move_character.poll(context()))
            self.assertFalse(self.ui.CHARACTERDESIGNER_OT_move_character.poll(context('EDIT_ARMATURE')))
            for name in ('library', 'override_library'):
                ctx = context()
                setattr(ctx.object, name, object())
                self.assertFalse(self.ui.CHARACTERDESIGNER_OT_move_character.poll(ctx))

    def test_absent_persistent_settings_do_not_draw_an_unusable_operator(self):
        ctx = context()
        del ctx.scene.character_designer_ground
        layout = Layout()
        self.ui.draw(layout, ctx)
        self.assertEqual(layout.buttons, [])

    def test_distance_property_is_persistent_length_with_signed_default(self):
        prop = self.ui.CharacterDesignerGroundState.__annotations__['distance']
        self.assertEqual(prop['unit'], 'LENGTH')
        self.assertEqual(prop['subtype'], 'DISTANCE')
        self.assertEqual(prop['default'], 0.)
        self.assertNotIn('SKIP_SAVE', prop.get('options', set()))


if __name__ == '__main__':
    unittest.main()
