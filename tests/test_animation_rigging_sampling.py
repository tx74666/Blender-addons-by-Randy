"""Portable tests of actual control-curve sampling, not native Blender QA.

AST-loaded production functions validate and sample fake FCurves. Native session
admission and DTO assembly are explicit stubs; the quaternion substitute only
normalizes four scalars. These tests prove output/guard preservation and bounded
curve scans, not Blender timing, IK evaluation, or publication acceptance.
"""
import ast
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import uuid


SOURCE = (Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
          / 'unity_animation_rigging.py')


class Quaternion:
    def __init__(self, values):
        self.w, self.x, self.y, self.z = values

    def __iter__(self):
        return iter((self.w, self.x, self.y, self.z))

    def normalize(self):
        length = math.sqrt(sum(value * value for value in self))
        self.w, self.x, self.y, self.z = (value / length for value in self)


class Curve:
    """Linear interpolation through finite, deliberately distinct input keys."""
    def __init__(self, path, index, offset):
        self.data_path, self.array_index = path, index
        self.mute = self.lock = self.is_driver = False
        self.modifiers, self.sampled_points, self.driver = [], [], None
        self.evaluation_override = None
        self.evaluations = []
        self.keyframe_points = [NS(co=[float(frame), offset + frame * .002],
            handle_left=[frame - .25, offset + frame * .002 - .0005],
            handle_right=[frame + .25, offset + frame * .002 + .0005])
            for frame in range(1, 60)]

    def evaluate(self, frame):
        self.evaluations.append(frame)
        if self.evaluation_override is not None:
            return self.evaluation_override
        points = self.keyframe_points
        if frame <= points[0].co[0]:
            return points[0].co[1]
        for left, right in zip(points, points[1:]):
            if frame <= right.co[0]:
                phase = (frame - left.co[0]) / (right.co[0] - left.co[0])
                return left.co[1] + phase * (right.co[1] - left.co[1])
        return points[-1].co[1]


class Action:
    def __init__(self, name, curves=()):
        self.name = name
        self.slots = [NS(handle=7)]
        self.layers = [NS(strips=[NS()])]
        self.bag = NS(fcurves=list(curves))


class SamplingTests(unittest.TestCase):
    def setUp(self):
        names = {'_require', '_uid', '_property', '_channels', '_curves',
                 '_sample', 'capture_inputs'}
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        functions = [node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual({node.name for node in functions}, names)
        constants = {target.id: ast.literal_eval(node.value)
                     for node in tree.body if isinstance(node, ast.Assign)
                     for target in node.targets if isinstance(target, ast.Name)
                     and target.id in {'_SIDES', '_FIELDS'}}
        self.scans, self.admissions, self.exports = [], [], []
        self.ns = dict(constants, json=json, math=math, uuid=uuid,
                       Quaternion=Quaternion, bpy=NS(types=NS(Action=Action)),
                       composition=NS(_bag=lambda action, slot: action.bag),
                       contract=NS(build_export=self.build_export),
                       _validate=self.admit)
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
        self.actual_curves = self.ns['_curves']
        self.ns['_curves'] = self.scan
        channels = sorted(self.ns['_channels']())
        self.curves = [Curve(path, index, (number + 1) * .01)
                       for number, (path, index) in enumerate(channels)]
        self.inputs = Action('Custom Inputs', self.curves)
        self.base = Action('Read-only Body')
        self.session = dict(input_action=self.inputs, input_slot=7,
            base_action=self.base, base_slot=7, source_packet_sha256='a' * 64,
            work_item_id=uuid.UUID(int=1).hex, new_action_id=uuid.UUID(int=2).hex,
            capture={'sampleRate': 60., 'frames': [{'time': frame / 60.}
                                                  for frame in range(59)]})

    def scan(self, action, slot):
        self.scans.append((action, slot))
        return self.actual_curves(action, slot)

    def admit(self, session):
        # Model only the one curve check inside native _validate. All native
        # ownership, file, Rest and scene admission stays outside this test.
        self.admissions.append(session)
        self.ns['_curves'](session['input_action'], session['input_slot'])

    def build_export(self, capture, samples, **identity):
        self.exports.append((capture, samples, identity))
        return samples

    def capture(self):
        return self.ns['capture_inputs'](None, self.session,
            source_packet_sha256=self.session['source_packet_sha256'],
            work_item_id=self.session['work_item_id'], source_action=self.base,
            source_action_slot=7)

    def position_curve(self):
        return next(curve for curve in self.inputs.bag.fcurves
                    if 'right_targetPosition' in curve.data_path
                    and curve.array_index == 0)

    def test_export_matches_individual_sampling_for_all_59_by_20_values(self):
        expected = [{'time': row['time'], 'controls': self.ns['_sample'](self.session, row['time'])}
                    for row in self.session['capture']['frames']]
        self.assertEqual(len(self.scans), 59)
        self.scans.clear()
        for curve in self.curves:
            curve.evaluations.clear()
        actual = self.capture()
        self.assertEqual(actual, expected)
        self.assertEqual(len(self.admissions), 1)
        self.assertEqual(len(self.scans), 2)  # One admission + one sampling map.
        self.assertEqual(len(actual), 59)
        for row in actual:
            self.assertEqual(sum(len(values) for side in row['controls']
                                 for values in side.values()), 20)
        for curve in self.curves:
            self.assertEqual(len(curve.evaluations), 59)

    def test_next_export_reads_changed_keys_and_replaced_native_curve(self):
        first = self.capture()
        curve = self.position_curve()
        curve.keyframe_points[0].co[1] += .25
        second = self.capture()
        self.assertAlmostEqual(second[0]['controls'][0]['targetPosition'][0]
                               - first[0]['controls'][0]['targetPosition'][0], .25)
        replacement = Curve(curve.data_path, curve.array_index, 2.)
        self.inputs.bag.fcurves[self.inputs.bag.fcurves.index(curve)] = replacement
        third = self.capture()
        self.assertEqual(third[0]['controls'][0]['targetPosition'][0], 2.002)
        self.assertEqual(len(replacement.evaluations), 59)
        self.assertEqual(len(self.scans), 6)

    def test_invalid_key_or_curve_is_rejected_before_export(self):
        mutations = {
            'key value': lambda curve: curve.keyframe_points[0].co.__setitem__(1, math.nan),
            'left handle': lambda curve: curve.keyframe_points[0].handle_left.__setitem__(1, math.inf),
            'right handle': lambda curve: curve.keyframe_points[0].handle_right.__setitem__(0, -math.inf),
            'empty keys': lambda curve: curve.keyframe_points.clear(),
            'modifier': lambda curve: curve.modifiers.append(NS()),
            'foreign channel': lambda curve: setattr(curve, 'data_path', 'foreign'),
            'duplicate channel': lambda curve: self.inputs.bag.fcurves.append(curve),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                self.setUp()
                mutate(self.position_curve())
                with self.assertRaises(ValueError):
                    self.capture()
                self.assertFalse(self.exports)
                self.assertTrue(all(not curve.evaluations for curve in self.curves))

    def test_nonfinite_evaluated_value_still_rejected_with_valid_keys(self):
        self.position_curve().evaluation_override = math.inf
        with self.assertRaisesRegex(ValueError, 'non-finite or have a zero rotation'):
            self.capture()
        self.assertFalse(self.exports)

    def test_zero_evaluated_rotation_still_rejected_with_valid_keys(self):
        for curve in self.curves:
            if 'right_targetRotation' in curve.data_path:
                curve.evaluation_override = 0.
        with self.assertRaisesRegex(ValueError, 'non-finite or have a zero rotation'):
            self.capture()
        self.assertFalse(self.exports)

    def test_preview_default_revalidates_after_a_previous_success(self):
        self.ns['_sample'](self.session, 0.)
        self.position_curve().modifiers.append(NS())
        with self.assertRaisesRegex(ValueError, 'unmodified finite keyframe curves'):
            self.ns['_sample'](self.session, .5)
        self.assertEqual(len(self.scans), 2)


if __name__ == '__main__':
    unittest.main()
