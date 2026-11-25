"""Selective immediate return-input restoration, with no saved departure pose."""
import copy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock

import test_animation_controller_edit as fixtures


FIELDS = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')


class Bone(SimpleNamespace):
    def __setattr__(self, name, value):
        if name != 'writes' and hasattr(self, 'writes'):
            self.writes.append(name)
        super().__setattr__(name, value)

    def __setitem__(self, name, value):
        self.writes.append('[' + name + ']')
        self.properties[name] = value


class ReturnInputs(unittest.TestCase):
    def setUp(self):
        fixtures.ControllerEdit.setUp(self)
        self.bone = Bone(name='Control', properties={'ik_fk': .25}, rotation_mode='XYZ', ik_stretch=.3)
        for block in (self.target, self.bone):
            block.location = (.007, 0., 0.)
            block.rotation_mode = 'XYZ'
            block.rotation_euler = (0., 0., 0.)
            block.rotation_quaternion = (1., 0., 0., 0.)
            block.rotation_axis_angle = (0., 0., 1., 0.)
            block.scale = (1., 1., 1.)
            block.ik_stretch = .3
        self.bone.writes = []
        self.target.pose.bones = {'Control': self.bone}
        self.controller._TRANSFORMS = FIELDS
        self.worker = ModuleType(self.service.__package__ + '.animation_controller_worker')
        self.worker._raw_packet = Mock(side_effect=lambda _rig: self.packet())
        sys.modules[self.worker.__name__] = self.worker
        setattr(sys.modules[self.service.__package__], 'animation_controller_worker', self.worker)
        self.service._return_identity = Mock(return_value=('same rig/data/binding/frame/graph',))
        source, home, target, bone = self.source, self.home, self.target, self.bone
        class Window:
            def __init__(self):
                self._scene = source
            @property
            def scene(self):
                return self._scene
            @scene.setter
            def scene(self, value):
                self._scene = value
                if value is home:
                    target.location = (0., 0., 0.)
                    bone.location = (0., 0., 0.)
                    bone['ik_fk'] = 0.
                    bone.ik_stretch = 0.
        self.context.window = Window()

    def packet(self):
        def block(value, properties):
            return dict(rotation_mode=value.rotation_mode, settings={'ik_stretch': value.ik_stretch},
                        properties=copy.deepcopy(properties), **{key: getattr(value, key) for key in FIELDS})
        return dict(object=block(self.target, {}), pose={'Control': block(self.bone, self.bone.properties)}, constraints=[])

    def test_immediate_held_transforms_numeric_controls_and_settings_survive_return(self):
        before = self.packet()
        self.service.return_to_character(self.context, self.home)
        self.assertIs(self.context.scene, self.home)
        self.assertEqual(self.packet(), before)
        self.assertNotIn('rotation_mode', self.bone.writes)
        self.assertNotIn('rotation_quaternion', self.bone.writes)
        self.assertNotIn('scale', self.bone.writes)

    def test_late_update_failure_restores_callers_context_and_immediate_inputs(self):
        before = self.packet()
        self.home.view_layer.update.side_effect = RuntimeError('Injected native update failure')
        with self.assertRaisesRegex(RuntimeError, 'native update failure'):
            self.service.return_to_character(self.context, self.home)
        self.assertIs(self.context.scene, self.source)
        self.assertEqual(self.packet(), before)

    def test_binding_identity_change_refuses_input_writes_and_restores_context(self):
        self.service._return_identity.side_effect = [('original',), ('changed',), ('changed',)]
        restore = self.service._restore_changed_inputs = Mock()
        with self.assertRaisesRegex(ValueError, 'binding, frame or ownership changed'):
            self.service.return_to_character(self.context, self.home)
        restore.assert_not_called()
        self.assertIs(self.context.scene, self.source)

    def test_constraint_change_refuses_before_any_selective_writes(self):
        before = self.packet()
        after = copy.deepcopy(before)
        after['constraints'] = [['other', 'constraint', {'influence': .5}]]
        after['pose']['Control']['location'] = (0., 0., 0.)
        self.bone.writes.clear()
        with self.assertRaisesRegex(ValueError, 'constraints or bone ownership changed'):
            self.service._restore_changed_inputs(self.target, before, after)
        self.assertEqual(self.bone.writes, [])


if __name__ == '__main__':
    unittest.main()
