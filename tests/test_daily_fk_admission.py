"""Source motions with unsupported curves or under-sampled turns must stay unconverted."""
import importlib.util
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
PACKAGE = '_daily_fk_admission_tests'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + '.animation_controller_mapping', ROOT / 'animation_controller_mapping.py')
mapping = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mapping
spec.loader.exec_module(mapping)
NS = types.SimpleNamespace


def point(frame, value, mode, *, left=None, right=None):
    return NS(co=NS(x=frame, y=value), interpolation=mode, amplitude=0.,
              handle_left=NS(y=value if left is None else left), handle_right=NS(y=value if right is None else right))


def curve(path='B.location', index=0, keys=()):
    return NS(data_path=path, array_index=index, keyframe_points=list(keys), sampled_points=[], modifiers=[])


class DailyAdmissionTests(unittest.TestCase):
    def admit(self, curves):
        bone = NS(name='B', rotation_mode='QUATERNION', rotation_quaternion=(1., 0., 0., 0.),
                  location=(0.,)*3, scale=(1.,)*3, path_from_id=lambda field: 'B.' + field)
        plan = NS(names=('B',), source_rig=NS(pose=NS(bones=[bone])), source_action=None, source_slot=1)
        module = NS(_curves=lambda action, slot: curves)
        with patch.object(package, 'animation_export_inputs', module, create=True):
            return mapping._daily_source_curves(plan)

    def test_admits_linear_and_constant_plateaus(self):
        for mode in ('CONSTANT', 'BEZIER', 'SINE', 'LINEAR'):
            c = curve(keys=(point(0,2.,mode),point(1,2.,'LINEAR'),point(2,5.,'LINEAR')))
            self.assertEqual(self.admit([c])[0], [c])

    def test_rejects_animated_step_and_bezier_excursion(self):
        cases = [(point(1,0.,'CONSTANT'), point(2,1.,'LINEAR')),
                 (point(1,0.,'BEZIER',right=1.), point(2,0.,'LINEAR',left=1.)),
                 (point(1,0.,'SINE'), point(2,1.,'SINE'))]
        for keys in cases:
            with self.assertRaisesRegex(mapping.MappingNeeded, 'LINEAR'):
                self.admit([curve(keys=keys)])

    def test_refuses_nonexistent_or_indirect_channels_and_wrong_indices(self):
        for path, index in [('Missing.location',0), ('B["blink"]',0), ('B.rotation_euler',0), ('B.location',3)]:
            with self.assertRaisesRegex(mapping.MappingNeeded, 'unsupported source curve'):
                self.admit([curve(path,index,(point(0,0.,'LINEAR'),))])

    def rotation(self, prop, times, rows, anchors):
        if importlib.util.find_spec('numpy') is None:
            self.skipTest('The rotation sampler uses the NumPy bundled with Blender.')
        curves = [NS(evaluate=dict(zip(times,col)).__getitem__) for col in zip(*rows)]
        mapping._daily_rotation_arcs(NS(source_times=tuple(times)), {('B',prop):curves}, anchors)

    def test_quaternion_same_hemisphere_and_opposite_signs(self):
        times = [1.,2.]
        self.rotation('rotation_quaternion',times,[(1,0,0,0),(.8,.6,0,0)],set(times))
        for q in [(-.8,.6,0,0),(-1,0,0,0),(0,0,0,0)]:
            with self.assertRaisesRegex(mapping.MappingNeeded, 'quaternion'):
                self.rotation('rotation_quaternion',times,[(1,0,0,0),q],set(times))

    def test_euler_collinear_turn_promotes_original_subframes(self):
        times = [1.,1.25,1.5,1.75,2.]
        anchors = {1.,2.}
        self.rotation('rotation_euler',times,[(0,0,2*math.pi*(t-1)) for t in times],anchors)
        self.assertEqual(anchors, set(times))

    def test_refuses_single_frame_full_turn_without_intermediate_keys(self):
        with self.assertRaisesRegex(mapping.MappingNeeded, 'denser LINEAR'):
            self.rotation('rotation_euler',[1.,2.],[(0,0,0),(0,0,2*math.pi)],{1.,2.})

    def test_refuses_rotating_undefined_axis(self):
        with self.assertRaisesRegex(mapping.MappingNeeded, 'axis-angle'):
            self.rotation('rotation_axis_angle',[1.,2.],[(0,1,0,0),(1,0,0,0)],{1.,2.})


if __name__ == '__main__':
    unittest.main()
