"""Portable publishing tests for the actual runtime via the existing Fixture.

The declared legacy oracle is Basis copy followed by ordered point RNA stores.
Mocks model float32 storage/clamping and per-instance point bus targets, not
native Blender notification delivery or a measured playback improvement.
Optional --baseline adds the same actual pre-change runtime, without making
external evidence a default dependency or skipping portable expectations.
"""
import argparse
from array import array
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
from types import SimpleNamespace as NS
import unittest

import test_forearm_side_output_reuse as old

BASELINE = None
BASELINE_SHA = 'ecf0e5f7c20fc70815d873ab1b38d837a3a4f33ce1ae76804291d0f5aca640f3'
MAX_FLOAT = struct.unpack('=f', struct.pack('=I', 0x7f7fffff))[0]
SUBNORMAL = struct.unpack('=f', struct.pack('=I', 1))[0]


def rna_triplet(values):
    """Declared old point-store model: conversion, float-property clamp, f32."""
    result = []
    for value in values:
        value = float(value)
        if value < -MAX_FLOAT:
            value = -MAX_FLOAT
        elif value > MAX_FLOAT:
            value = MAX_FLOAT
        result.append(value)
    if len(result) != 3:
        raise ValueError('RNA co expects three components')
    return array('f', result)


def legacy_bytes(basis, output):
    """Original small publisher oracle, independent of candidate selection."""
    values = array('f', basis)
    count = len(values) // 3
    for index, coordinate in output:
        index = int(index)
        if index < 0:
            index += count
        if not 0 <= index < count:
            raise IndexError('RNA point index out of range')
        values[3 * index:3 * index + 3] = rna_triplet(coordinate)
    return values.tobytes()


class PublishFailure(RuntimeError):
    pass


class PropertyKey:
    """Opaque mock of path_resolve('co', False), not an instance/type tuple."""
    def __init__(self, collection, index):
        self.collection, self.index = collection, index


class BasisSeed(old.Vector):
    def __add__(self, other):
        return self.finish(old.Vector.__add__(self, other))


class Point:
    def __init__(self, collection, index, physical):
        self.collection, self.index, self.physical = collection, index, physical

    @property
    def co(self):
        data = self.collection
        if data.basis:
            if 0 <= self.physical < len(data):
                value = BasisSeed(data.values[3 * self.physical:3 * self.physical + 3])
            else:
                value = BasisSeed((0.0, 0.0, 0.0))
            def finish(default):
                fixture = data.fixture
                token = (fixture.current_side, self.index)
                choices = fixture.output_overrides.get(token)
                coordinate = choices.pop(0) if choices else default
                fixture.generated[fixture.current_side].append((self.index, coordinate))
                return coordinate
            value.finish = finish
            return value
        return old.Vector(data.values[3 * self.physical:3 * self.physical + 3])

    @co.setter
    def co(self, value):
        data = self.collection
        converted = rna_triplet(value)
        data.values[3 * self.physical:3 * self.physical + 3] = converted
        data.writes += 1
        data.co_sets += 1
        data.fixture.timeline.append(('co', data.side, self.physical))
        data.fixture.touches.append((data.side, self.physical))

    def path_resolve(self, name, coerce):
        if name != 'co' or coerce is not False:
            raise AssertionError('Expected exact point property RNA resolution')
        fixture = self.collection.fixture
        fixture.timeline.append(('resolve', self.collection.side, self.physical))
        if fixture.resolve_failure:
            raise PublishFailure('property resolution failed')
        return PropertyKey(self.collection, self.physical)


class Coordinates(old.Coordinates):
    def __init__(self, fixture, side, values, basis=False):
        self.fixture, self.side, self.basis = fixture, side, basis
        self.values = array('f', values)
        self.reads = self.writes = self.co_sets = self.bulk_calls = 0
        self.bulk_inputs = []
        self.fail_bulk = False

    def __getitem__(self, index):
        physical = int(index)
        if physical < 0:
            physical += len(self)
        permitted_seed = self.basis and (self.fixture.current_side, index) in self.fixture.output_overrides
        if not 0 <= physical < len(self) and not permitted_seed:
            raise IndexError('RNA point index out of range')
        return Point(self, index, physical)

    def foreach_set(self, field, values):
        if field != 'co':
            raise AssertionError('Expected coordinate bulk store')
        self.bulk_calls += 1
        self.bulk_inputs.append(values)
        self.fixture.timeline.append(('bulk', self.side))
        if self.fail_bulk:
            raise PublishFailure('foreach failed')
        self.values[:] = array('f', values)
        self.writes += 1


class TrackedKey(old.Key):
    def __setattr__(self, name, value):
        if name in ('value', 'mute') and '_fixture' in self.__dict__:
            self._fixture.timeline.append((name, self._side, value))
        object.__setattr__(self, name, value)


class Fixture(old.Fixture):
    def __init__(self, path=old.SOURCE, support=2, api=True):
        self.current_side = 'L'
        self.timeline, self.touches, self.publish_calls = [], [], []
        self.generated = {'L': [], 'R': []}
        self.output_overrides = {}
        self.resolve_failure = False
        self.publisher_failure = False
        super().__init__(path=path, support=support)
        basis = self.obj.data.shape_keys.reference_key
        basis.data = Coordinates(self, 'Basis', basis.data.values, basis=True)
        for side, original in tuple(self.keys.items()):
            key = TrackedKey.__new__(TrackedKey)
            key.__dict__.update(original.__dict__)
            key._fixture, key._side = self, side
            key.data = Coordinates(self, side, original.data.values)
            self.keys[side] = key
        if api:
            self.ns.update(Vector=old.Vector, bpy=NS(msgbus=NS(publish_rna=self.publish)))
        native_keys = self.obj.data.shape_keys
        def key_tag(**kwargs):
            self.updates.append(('keys', kwargs))
            self.timeline.append(('tag', 'keys', kwargs))
        def mesh_update():
            self.updates.append(('mesh', {}))
            self.timeline.append(('tag', 'mesh', {}))
        native_keys.update_tag = key_tag
        self.obj.data.update = mesh_update

    def basis_coordinates(self, obj):
        self.basis_buffer = super().basis_coordinates(obj)
        return self.basis_buffer

    def correct(self, *args, **kwargs):
        self.current_side = args[3][-1]
        return super().correct(*args, **kwargs)

    def publish(self, *, key):
        if not isinstance(key, PropertyKey):
            raise AssertionError('Publisher requires the resolved per-instance property key')
        self.publish_calls.append(key)
        self.timeline.append(('publish', key.collection.side, key.index))
        if self.publisher_failure:
            raise PublishFailure('publisher failed')
        self.touches.append((key.collection.side, key.index))

    def reset_counts(self):
        super().reset_counts()
        self.timeline.clear(); self.touches.clear(); self.publish_calls.clear()
        self.generated = {'L': [], 'R': []}
        for key in self.keys.values():
            key.data.bulk_calls = key.data.co_sets = 0
            key.data.bulk_inputs.clear()


class OpaqueCoordinate:
    def __init__(self, values):
        self.values, self.iterations = values, 0

    def __len__(self):
        return len(self.values)

    def __iter__(self):
        self.iterations += 1
        return iter(self.values)


class BulkPublishTests(unittest.TestCase):
    def assert_oracle(self, fixture):
        basis = fixture.obj.data.shape_keys.reference_key.data.values
        for side, key in fixture.keys.items():
            self.assertEqual(key.data.values.tobytes(), legacy_bytes(basis, fixture.generated[side]))
            self.assertEqual(key.value, 1.0)
        self.assertEqual(fixture.updates, [('keys', {}), ('mesh', {}), ('keys', {'refresh': {'TIME'}})])

    def warmed(self):
        fixture = Fixture()
        fixture.run()
        fixture.reset_counts()
        return fixture

    def caches(self, fixture):
        return copy.deepcopy((fixture.ns['_CACHE'], fixture.ns['_OUTPUT_CACHE']))

    def assert_cache(self, fixture, before):
        self.assertEqual((fixture.ns['_CACHE'], fixture.ns['_OUTPUT_CACHE']), before)

    def configure_bits(self, fixture):
        fixture.write_records(lambda data: data['L'].update(vertices=[0, 1, 0], positions=[.2, .5, .8]))
        basis = fixture.obj.data.shape_keys.reference_key.data.values
        basis[-3:] = array('f', (-0.0, SUBNORMAL, -SUBNORMAL))
        fixture.output_overrides[('L', 0)] = [(1.0, 2.0, 3.0), [-0.0, SUBNORMAL, -SUBNORMAL]]
        fixture.output_overrides[('L', 1)] = [old.Vector((MAX_FLOAT, -MAX_FLOAT, 0.0))]

    def test_fast_f32_bits_duplicate_order_outside_basis_and_no_alias(self):
        fixture = Fixture()
        self.configure_bits(fixture)
        basis_before = fixture.obj.data.shape_keys.reference_key.data.values.tobytes()
        fixture.run()
        self.assert_oracle(fixture)
        self.assertEqual(fixture.basis_buffer.tobytes(), basis_before)
        buffers = []
        for side, key in fixture.keys.items():
            self.assertEqual(key.data.bulk_calls, 1)
            self.assertEqual(key.data.co_sets, 1)
            packed, = key.data.bulk_inputs
            self.assertIs(type(packed), array)
            self.assertEqual(packed.typecode, 'f')
            self.assertIsNot(packed, fixture.basis_buffer)
            self.assertEqual(key.data.values.tobytes()[-12:], basis_before[-12:])
            buffers.append(packed)
        self.assertIsNot(buffers[0], buffers[1])
        self.assertEqual(fixture.keys['L'].data.values.tobytes()[:12], array('f', (-0.0, SUBNORMAL, -SUBNORMAL)).tobytes())
        if BASELINE is not None:
            reference = Fixture(BASELINE)
            self.configure_bits(reference)
            reference.run()
            self.assertEqual(fixture.snapshot(), reference.snapshot())

    def test_point_targets_and_three_explicit_notifications_keep_order(self):
        fixture = Fixture(support=3)
        fixture.run()
        self.assert_oracle(fixture)
        self.assertEqual(fixture.touches, [('L', 0), ('L', 1), ('L', 2), ('R', 3), ('R', 4), ('R', 5)])
        self.assertEqual(len(fixture.publish_calls), 4)
        for side in ('L', 'R'):
            self.assertEqual(fixture.keys[side].data.co_sets, 1)
            positions = [i for i, event in enumerate(fixture.timeline) if len(event) > 1 and event[1] == side]
            own = [fixture.timeline[i] for i in positions]
            self.assertEqual([event[0] for event in own], ['resolve', 'resolve', 'value', 'mute', 'bulk', 'publish', 'publish', 'co'])
        self.assertEqual([row[1] for row in fixture.timeline if row[0] == 'tag'], ['keys', 'mesh', 'keys'])

    def test_none_empty_disabled_and_unchanged_add_no_point_events(self):
        fixture = Fixture()
        fixture.write_records(lambda data: data['R'].__setitem__('enabled', False))
        fixture.run()
        right = fixture.keys['R']
        self.assertEqual(right.data.values, fixture.obj.data.shape_keys.reference_key.data.values)
        self.assertEqual((right.data.bulk_calls, right.data.co_sets), (1, 0))
        self.assertTrue(right.mute)
        self.assertFalse(any(side == 'R' for side, _index in fixture.touches))
        fixture.reset_counts(); fixture.run()
        self.assertEqual(fixture.timeline, [])
        self.assertEqual(fixture.updates, [])
        fixture.pose('L', .3); fixture.run()
        self.assertEqual((right.data.bulk_calls, right.data.co_sets), (0, 0))
        self.assertFalse(any(side == 'R' for side, _index in fixture.touches))

    def test_missing_bpy_msgbus_or_vector_keeps_complete_legacy_path(self):
        for absent in ('bpy', 'msgbus', 'Vector'):
            with self.subTest(absent=absent):
                fixture = Fixture()
                if absent == 'msgbus':
                    fixture.ns['bpy'] = NS()
                else:
                    fixture.ns.pop(absent)
                fixture.run()
                self.assert_oracle(fixture)
                self.assertEqual(fixture.publish_calls, [])
                self.assertFalse(any(row[0] == 'resolve' for row in fixture.timeline))
                for key in fixture.keys.values():
                    self.assertEqual((key.data.bulk_calls, key.data.co_sets), (1, 2))
                    self.assertIs(key.data.bulk_inputs[0], fixture.basis_buffer)

    def test_unsupported_owned_nonfloat_and_nonfinite_use_legacy_clamp(self):
        def owned():
            value = old.Vector((1.0, 2.0, 3.0)); value.owner = object(); return value
        def wrapped():
            value = old.Vector((1.0, 2.0, 3.0)); value.is_wrapped = True; return value
        cases = [('opaque', lambda: OpaqueCoordinate((1.0, 2.0, 3.0))), ('owned', owned), ('wrapped', wrapped),
                 ('int', lambda: (1, 2.0, 3.0)), ('nan', lambda: (math.nan, 2.0, 3.0)),
                 ('inf', lambda: (math.inf, 2.0, 3.0)), ('negative_inf', lambda: (-math.inf, 2.0, 3.0)),
                 ('finite_overflow', lambda: (MAX_FLOAT * 2, 2.0, 3.0))]
        for name, make in cases:
            with self.subTest(name=name):
                fixture = Fixture()
                coordinate = make()
                fixture.output_overrides[('L', 0)] = [coordinate]
                fixture.run()
                if name == 'opaque':
                    self.assertEqual(coordinate.iterations, 1)
                left = fixture.keys['L'].data
                self.assertEqual((left.bulk_calls, left.co_sets), (1, 2))
                self.assertIs(left.bulk_inputs[0], fixture.basis_buffer)
                self.assertFalse(any(key.collection.side == 'L' for key in fixture.publish_calls))
                self.assert_oracle(fixture)

    def test_negative_and_oob_indexes_keep_original_boundary_behavior(self):
        for index in (-1, 5):
            with self.subTest(index=index):
                fixture = Fixture()
                fixture.write_records(lambda data: data['L'].update(vertices=[index], positions=[.5]))
                fixture.base[index] = old.Vector((.1, .2, .3))
                fixture.weights[index] = dict(fixture.weights[0])
                fixture.output_overrides[('L', index)] = [(4.0, 5.0, 6.0)]
                before = self.caches(fixture)
                if index < 0:
                    fixture.run()
                    self.assert_oracle(fixture)
                    self.assertEqual(fixture.keys['L'].data.values[-3:], array('f', (4.0, 5.0, 6.0)))
                else:
                    with self.assertRaisesRegex(IndexError, 'RNA point index out of range'):
                        fixture.run()
                    self.assert_cache(fixture, before)
                    self.assertEqual(fixture.updates, [])
                left = fixture.keys['L'].data
                self.assertEqual(left.bulk_calls, 1)
                self.assertIs(left.bulk_inputs[0], fixture.basis_buffer)
                self.assertFalse(any(key.collection.side == 'L' for key in fixture.publish_calls))

    def test_math_failure_happens_before_any_publication(self):
        fixture = self.warmed()
        before, cache = fixture.snapshot(), self.caches(fixture)
        fixture.pose('R', .3); fixture.invalid = ('correct', 'R')
        with self.assertRaisesRegex(fixture.ns['ForearmTwistError'], 'invalid correct R'):
            fixture.run()
        self.assertEqual(fixture.snapshot(), before)
        self.assertEqual(fixture.timeline, [])
        self.assertEqual(fixture.updates, [])
        self.assert_cache(fixture, cache)

    def test_fast_foreach_failure_never_retries_legacy_or_commits_cache(self):
        fixture = self.warmed()
        cache = self.caches(fixture)
        fixture.pose('L', .3); fixture.keys['L'].data.fail_bulk = True
        with self.assertRaisesRegex(PublishFailure, 'foreach failed'):
            fixture.run()
        self.assertEqual(fixture.keys['L'].data.bulk_calls, 1)
        self.assertEqual(fixture.keys['L'].data.co_sets, 0)
        self.assertEqual(fixture.publish_calls, [])
        self.assertEqual(fixture.updates, [])
        self.assert_cache(fixture, cache)

    def test_publisher_failure_never_retries_legacy_or_commits_cache(self):
        fixture = self.warmed()
        cache = self.caches(fixture)
        fixture.pose('L', .3); fixture.publisher_failure = True
        with self.assertRaisesRegex(PublishFailure, 'publisher failed'):
            fixture.run()
        self.assertEqual(fixture.keys['L'].data.bulk_calls, 1)
        self.assertEqual(fixture.keys['L'].data.co_sets, 0)
        self.assertEqual(len(fixture.publish_calls), 1)
        self.assertEqual(fixture.updates, [])
        self.assert_cache(fixture, cache)

    def test_property_resolution_failure_precedes_key_rna_writes(self):
        fixture = self.warmed()
        before, cache = fixture.snapshot(), self.caches(fixture)
        fixture.pose('L', .3); fixture.resolve_failure = True
        with self.assertRaisesRegex(PublishFailure, 'property resolution failed'):
            fixture.run()
        self.assertEqual(fixture.snapshot(), before)
        self.assertEqual(fixture.keys['L'].data.bulk_calls, 0)
        self.assertEqual(fixture.keys['L'].data.co_sets, 0)
        self.assertTrue(all(row[0] == 'resolve' for row in fixture.timeline))
        self.assertEqual(fixture.updates, [])
        self.assert_cache(fixture, cache)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--baseline', type=Path)
    args, remaining = parser.parse_known_args()
    if args.baseline is not None:
        if hashlib.sha256(args.baseline.read_bytes()).hexdigest() != BASELINE_SHA:
            raise RuntimeError('Explicit baseline must be the fixed approved100 Forearm source.')
        BASELINE = args.baseline
    unittest.main(argv=[sys.argv[0], *remaining])
