"""Bounded old/new channel-capture comparison; no bpy installation required.

The reference below is copied verbatim (LF, final newline) from the approved
0.77.87 projection, not from the implementation under test. Protocol fakes
exercise branch/state/error behavior; the companion Blender script checks
actual mathutils results on four disposable bones.
"""

from collections import Counter
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
APPROVED_MODULE_SHA256 = 'acf81b37e8d89bdaff0a04b8a3a2a244584e6b5d5551c58efb1946b1ece5b3c7'
APPROVED_CAPTURE_SHA256 = 'fa048fdbd7d4e26c9f8d7949a6ea359d74b660417982c58141a47acde53c816f'
APPROVED_CAPTURE_SOURCE = '''def _capture_channels(target, clone, plan, keyed, channels, previous_q, previous_e):
    needs_translation = False
    for name in keyed:
        pb = clone.pose.bones[name]
        location, rotation, scale = pb.matrix_basis.decompose()
        if _difference(pb.matrix_basis, Matrix.LocRotScale(location, rotation, scale)) > SHEAR_TOLERANCE:
            raise _error(f"'{name}' requires shear; the original rig was left untouched.")
        if target.data.bones[name].use_connect and location.length > 1.0e-7:
            needs_translation = True
        if name in previous_q and rotation.dot(previous_q[name]) < 0:
            rotation.negate()
        previous_q[name] = rotation.copy()
        mode = target.pose.bones[name].rotation_mode
        if mode == 'QUATERNION':
            rotation_path, components = 'rotation_quaternion', rotation
        elif mode == 'AXIS_ANGLE':
            axis, angle = rotation.to_axis_angle()
            rotation_path, components = 'rotation_axis_angle', (angle, *axis)
        else:
            euler = rotation.to_euler(mode, previous_e[name]) if name in previous_e else rotation.to_euler(mode)
            previous_e[name] = euler.copy()
            rotation_path, components = 'rotation_euler', euler
        for prop, values in (('location', location), (rotation_path, components), ('scale', scale)):
            path = target.pose.bones[name].path_from_id(prop)
            for index, value in enumerate(values):
                if (path, index) in plan.driver_keys:
                    continue  # Root scale continues to use its owned uniform-scale driver.
                if not math.isfinite(value):
                    raise _error(f"'{name}' produced a non-finite animation channel.")
                channels.setdefault((path, index), []).append(float(value))
    for (name, prop), value in plan.switches.items():
        path = target.pose.bones[name].path_from_id() + '[' + json.dumps(prop) + ']'
        channels.setdefault((path, 0), []).append(float(value))
    return needs_translation
'''


def reference_capture(controls, matrix):
    assert hashlib.sha256(APPROVED_CAPTURE_SOURCE.encode()).hexdigest() == APPROVED_CAPTURE_SHA256
    namespace = dict(Matrix=matrix, json=json, math=math, _error=controls._error,
                     _difference=controls._difference, SHEAR_TOLERANCE=controls.SHEAR_TOLERANCE)
    exec(compile(APPROVED_CAPTURE_SOURCE, '<approved087-capture>', 'exec'), namespace)
    return namespace['_capture_channels']


@contextmanager
def load_controls(matrix):
    name = '_channel_capture_' + uuid.uuid4().hex
    package = ModuleType(name)
    package.__path__ = [str(SOURCE)]
    ua = ModuleType(name + '.unity_animation')
    ua.UnityAnimationError = type('UnityAnimationError', (ValueError,), {})
    mathutils = ModuleType('mathutils')
    mathutils.Matrix = matrix
    spec = importlib.util.spec_from_file_location(name + '.unity_animation_controls',
                                                SOURCE / 'unity_animation_controls.py')
    controls = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: package, ua.__name__: ua, spec.name: controls,
                                 'mathutils': mathutils, 'bpy': ModuleType('bpy')}):
        spec.loader.exec_module(controls)
        yield controls


class Vector(list):
    @property
    def length(self):
        return math.sqrt(sum(value * value for value in self))

    def copy(self):
        return type(self)(self)


class Quaternion(Vector):
    def dot(self, other):
        return sum(a * b for a, b in zip(self, other))

    def negate(self):
        self[:] = [-value for value in self]

    def to_axis_angle(self):
        length = Vector(self[1:]).length
        return (Vector(value / length for value in self[1:]) if length else Vector((1, 0, 0)),
                2 * math.atan2(length, self[0]))

    def to_euler(self, mode, previous=None):
        # A deterministic protocol model, not a replacement for native rotations.
        values = [self[1 + 'XYZ'.index(axis)] for axis in mode]
        if previous is not None:
            values = [value + round((old - value) / math.tau) * math.tau
                      for value, old in zip(values, previous)]
        return Vector(values)


class Basis:
    def __init__(self, location=(0, 0, 0), rotation=(1, 0, 0, 0), scale=(1, 1, 1), shear=0):
        self.location, self.rotation, self.scale = Vector(location), Quaternion(rotation), Vector(scale)
        values = list(location) + list(rotation) + list(scale) + [0, 0, shear, 0, 0, 1]
        self.rows = [values[index:index + 4] for index in range(0, 16, 4)]

    def __getitem__(self, index):
        return self.rows[index]

    def decompose(self):
        return self.location.copy(), self.rotation.copy(), self.scale.copy()

    @staticmethod
    def LocRotScale(location, rotation, scale):
        return Basis(location, rotation, scale)


class Lookup(dict):
    def __init__(self, stats, key, *args):
        super().__init__(*args)
        self.stats, self.key = stats, key

    def __getitem__(self, name):
        self.stats[self.key] += 1
        return super().__getitem__(name)


class PoseBone:
    def __init__(self, name, mode, stats):
        self.prefix, self.mode, self.stats = 'pose.bones[' + json.dumps(name) + ']', mode, stats
        self.matrix_basis = Basis()

    @property
    def rotation_mode(self):
        self.stats['rotation_mode'] += 1
        return self.mode

    def path_from_id(self, prop=None):
        self.stats['path_from_id'] += 1
        return self.prefix + ('.' + prop if prop else '')


class Bone:
    def __init__(self, connected, stats):
        self.connected, self.stats = connected, stats

    @property
    def use_connect(self):
        self.stats['use_connect'] += 1
        return self.connected


def fixture(modes=('QUATERNION', 'AXIS_ANGLE', 'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX')):
    stats = Counter()
    names = tuple('Bone' + str(index) for index in range(len(modes)))
    pose = {name: PoseBone(name, mode, stats) for name, mode in zip(names, modes)}
    target = NS(pose=NS(bones=Lookup(stats, 'pose_lookup', pose)),
                data=NS(bones=Lookup(stats, 'bone_lookup',
                                    {name: Bone(index == 0, stats) for index, name in enumerate(names)})))
    clone = NS(pose=NS(bones={name: NS(matrix_basis=Basis()) for name in names}))
    switch = '切替"\\値'
    plan = NS(driver_keys={(pose[names[0]].prefix + '.scale', 0)},
              switches={(names[0], switch): 0.75})
    return target, clone, plan, names, stats


def capture_series(controls, reference=False, samples=5, modes=None, fault=None):
    target, clone, plan, keyed, stats = fixture() if modes is None else fixture(modes)
    capture = reference_capture(controls, Basis) if reference else controls._capture_channels
    compiled = None if reference else controls._compile_channel_capture(target, plan, keyed)
    channels, previous_q, previous_e, translated = {}, {}, {}, []
    for sample in range(samples):
        for index, name in enumerate(keyed):
            sign = -1 if sample % 2 else 1
            location = (0.02 * sample, 0.01 * index, 0)
            if fault == 'nan' and sample == 2 and index == 0:
                location = (float('nan'), 0, 0)
            rotation = tuple(sign * value for value in (0.8, 0.2 + sample * 0.02, 0.3, 0.4))
            clone.pose.bones[name].matrix_basis = Basis(
                location, rotation, shear=0.001 if fault == 'shear' and sample == 2 and index == 0 else 0)
        options = {} if reference else {'capture_plan': compiled}
        translated.append(capture(target, clone, plan, keyed, channels, previous_q, previous_e, **options))
    return channels, previous_q, previous_e, translated, dict(stats)


class ChannelCaptureTests(unittest.TestCase):
    def setUp(self):
        self.environment = load_controls(Basis)
        self.controls = self.environment.__enter__()
        self.addCleanup(self.environment.__exit__, None, None, None)

    def test_approved_reference_pin(self):
        self.assertEqual(hashlib.sha256(APPROVED_CAPTURE_SOURCE.encode()).hexdigest(),
                         APPROVED_CAPTURE_SHA256)

    def test_all_modes_sign_continuity_translation_driver_and_unicode_match(self):
        expected = capture_series(self.controls, reference=True)
        actual = capture_series(self.controls)
        self.assertEqual(actual[:4], expected[:4])
        channels, previous_q, previous_e, translated, _stats = actual
        self.assertEqual(translated, [False, True, True, True, True])
        self.assertNotIn(('pose.bones["Bone0"].scale', 0), channels)
        self.assertEqual(len(previous_e), 6)
        self.assertGreater(previous_q['Bone0'][0], 0)
        self.assertTrue(all(value > 0 for value in channels[('pose.bones["Bone0"].rotation_quaternion', 0)]))
        switch_key = ('pose.bones["Bone0"][' + json.dumps('切替"\\値') + ']', 0)
        self.assertEqual(channels[switch_key], [0.75] * 5)

    def test_nan_and_shear_refuse_like_approved_reference(self):
        for fault, text in (('nan', 'non-finite'), ('shear', 'requires shear')):
            messages = []
            for reference in (True, False):
                with self.assertRaisesRegex(ValueError, text) as caught:
                    capture_series(self.controls, reference=reference, fault=fault)
                messages.append(str(caught.exception))
            self.assertEqual(*messages)

    def test_each_euler_mode_keeps_previous_sample_across_wrap(self):
        for mode in ('XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'):
            target, clone, plan, names, _stats = fixture((mode,))
            clone.pose.bones[names[0]].matrix_basis = Basis(rotation=(1, -3.1, -3.1, -3.1))
            expected, actual = {}, {}
            previous_old = {names[0]: Vector((3.1, 3.1, 3.1))}
            previous_new = {names[0]: Vector((3.1, 3.1, 3.1))}
            old = reference_capture(self.controls, Basis)
            old(target, clone, plan, names, expected, {}, previous_old)
            compiled = self.controls._compile_channel_capture(target, plan, names)
            self.controls._capture_channels(target, clone, plan, names, actual, {}, previous_new,
                                            capture_plan=compiled)
            self.assertEqual(actual, expected)
            self.assertEqual(previous_new, previous_old)
            self.assertTrue(all(value > 3.1 for value in previous_new[names[0]]))

    def test_owned_driver_skip_precedes_finite_channel_check(self):
        target, clone, plan, names, _stats = fixture(('QUATERNION',))
        clone.pose.bones[names[0]].matrix_basis = Basis(scale=(float('inf'), 1, 1))
        expected, actual = {}, {}
        reference_capture(self.controls, Basis)(target, clone, plan, names, expected, {}, {})
        compiled = self.controls._compile_channel_capture(target, plan, names)
        self.controls._capture_channels(target, clone, plan, names, actual, {}, {},
                                        capture_plan=compiled)
        self.assertEqual(actual, expected)
        self.assertTrue(all(math.isfinite(value) for values in actual.values() for value in values))

    def test_each_compile_reads_new_mode_path_connectivity_and_driver_plan(self):
        target, clone, plan, names, _stats = fixture(('QUATERNION',))
        first = self.controls._compile_channel_capture(target, plan, names)
        target.pose.bones[names[0]].mode = 'ZYX'
        target.pose.bones[names[0]].prefix = 'pose.bones["Renamed"]'
        target.data.bones[names[0]].connected = False
        plan.driver_keys = {('pose.bones["Renamed"].location', 1)}
        second = self.controls._compile_channel_capture(target, plan, names)
        self.assertNotEqual(first, second)
        self.assertEqual(second[0][0][1:3], (False, 'ZYX'))
        channels = {}
        translated = self.controls._capture_channels(target, clone, plan, names, channels, {}, {},
                                                      capture_plan=second)
        self.assertFalse(translated)
        self.assertIn(('pose.bones["Renamed"].rotation_euler', 0), channels)
        self.assertNotIn(('pose.bones["Renamed"].location', 1), channels)
        self.assertFalse(any('rotation_quaternion' in key[0] for key in channels))

    def test_legacy_call_without_compiled_metadata_is_compatible(self):
        target, clone, plan, names, _stats = fixture()
        expected, actual = {}, {}
        old = reference_capture(self.controls, Basis)
        self.assertEqual(old(target, clone, plan, names, expected, {}, {}),
                         self.controls._capture_channels(target, clone, plan, names, actual, {}, {}))
        self.assertEqual(actual, expected)

    def test_bounded_lookup_counts_drop_without_sample_output_changes(self):
        samples, bones, switches = 5, 8, 1
        old = capture_series(self.controls, reference=True, samples=samples)
        new = capture_series(self.controls, samples=samples)
        self.assertEqual(new[:4], old[:4])
        self.assertEqual(old[4]['path_from_id'], (3 * bones + switches) * samples)
        self.assertEqual(new[4]['path_from_id'], 3 * bones + switches)
        for key in ('rotation_mode', 'use_connect', 'bone_lookup'):
            self.assertEqual(old[4][key], bones * samples)
            self.assertEqual(new[4][key], bones)
        self.assertEqual(old[4]['pose_lookup'], (4 * bones + switches) * samples)
        self.assertEqual(new[4]['pose_lookup'], bones + switches)


if __name__ == '__main__':
    unittest.main()
