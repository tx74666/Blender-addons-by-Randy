"""Portable differential tests for the actual runtime's accepted-output proof.

The small float32 RNA/math doubles exercise cache decisions and transactional
ordering, not native Blender math compatibility. --baseline can additionally
run the same cases against a separately pinned pre-change runtime function.
Native full-coordinate/author proofs remain required before deployment.
"""
import argparse
import ast
import copy
import json
import math
from array import array
from pathlib import Path
import struct
import sys
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/forearm_twist.py'
BASELINE = None
RECORD_KEY = 'character_designer_forearm_twist_v1'


def f32(value):
    return struct.unpack('=f', struct.pack('=f', value))[0]


class Vector(tuple):
    def __new__(cls, values):
        return super().__new__(cls, map(f32, values))

    def __add__(self, other):
        return Vector(a + b for a, b in zip(self, other))

    def __sub__(self, other):
        return Vector(a - b for a, b in zip(self, other))

    def __mul__(self, scalar):
        return Vector(value * scalar for value in self)

    __rmul__ = __mul__

    def normalized(self):
        length = math.sqrt(sum(value * value for value in self))
        return self * (1.0 / length) if length else self


class Matrix:
    def __init__(self, rows=None, label=''):
        self.rows = tuple(tuple(map(f32, row)) for row in (rows or
                          ((1., 0., 0., 0.), (0., 1., 0., 0.),
                           (0., 0., 1., 0.), (0., 0., 0., 1.))))
        self.label = label

    def __iter__(self):
        return iter(self.rows)

    def copy(self):
        return Matrix(self.rows, self.label)

    def with_entry(self, row, column, value):
        rows = [list(values) for values in self.rows]
        rows[row][column] = value
        return Matrix(rows, self.label)

    def inverted(self):
        # Fixtures use translation-only object/rest transforms.
        return Matrix(tuple(tuple(-value if column == 3 and row < 3 else value
                                  for column, value in enumerate(values))
                            for row, values in enumerate(self.rows)), self.label)

    def __matmul__(self, other):
        if isinstance(other, Matrix):
            identity = Matrix().rows
            identity_bits = array('f', (v for row in identity for v in row)).tobytes()
            if array('f', (v for row in other.rows for v in row)).tobytes() == identity_bits:
                return self.copy()
            if array('f', (v for row in self.rows for v in row)).tobytes() == identity_bits:
                return other.copy()
            return Matrix(tuple(tuple(sum(self.rows[row][k] * other.rows[k][column]
                                          for k in range(4)) for column in range(4))
                                for row in range(4)), self.label)
        return Vector(sum(self.rows[row][k] * other[k] for k in range(3))
                      + self.rows[row][3] for row in range(3))


class Coordinates:
    def __init__(self, points):
        self.values = array('f', (value for point in points for value in point))
        self.reads = self.writes = 0

    def __len__(self):
        return len(self.values) // 3

    def __getitem__(self, index):
        collection = self

        class Coordinate:
            @property
            def co(self):
                return Vector(collection.values[3 * index:3 * index + 3])

            @co.setter
            def co(self, value):
                collection.values[3 * index:3 * index + 3] = array('f', value)
                collection.writes += 1

        return Coordinate()

    def foreach_get(self, field, destination):
        assert field == 'co'
        self.reads += 1
        destination[:] = self.values

    def foreach_set(self, field, values):
        assert field == 'co'
        self.values[:] = values
        self.writes += 1


class Key:
    def __init__(self, name, pointer, points, basis=None):
        self.name, self.pointer = name, pointer
        self.data = Coordinates(points)
        self.relative_key, self.vertex_group = basis, ''
        self.value, self.mute = .2, False

    def as_pointer(self):
        return self.pointer


class Mesh(dict):
    def as_pointer(self):
        return 42

    def evaluated_get(self, graph):
        return self


def load_product(path):
    names = {'_calculate_records', '_key_identity', 'ForearmTwistError',
             'ForearmTwistPoseRangeError', 'ForearmTwistRecaptureRequired'}
    tree = ast.parse(path.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in names]
    scope = {'array': array, 'RECORD_KEY': RECORD_KEY, 'MAX_ANGLE': math.radians(120),
             '_CACHE': {}, '_OUTPUT_CACHE': {}, '_ERRORS': {}}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), scope)
    return scope


class Fixture:
    def __init__(self, path=SOURCE, support=2):
        self.ns = load_product(path)
        self.calls, self.guard_calls, self.updates = [], [], []
        self.invalid = None
        points = [(i + .125, i * .25, -.5) for i in range(2 * support + 1)]
        self.obj = Mesh()
        self.obj.name, self.obj.type, self.obj.mode = 'Body', 'MESH', 'OBJECT'
        self.obj.session_uid, self.obj.topology = 100, 'topology'
        self.obj.matrix_world = Matrix()
        basis = Key('Basis', 10, points)
        self.keys = {side: Key('Twist.' + side, 20 + index, points, basis)
                     for index, side in enumerate(('L', 'R'))}
        native_keys = NS(reference_key=basis, session_uid=200, as_pointer=lambda: 9,
                         update_tag=lambda **kwargs: self.updates.append(('keys', kwargs)))
        self.obj.data = NS(shape_keys=native_keys, update=lambda: self.updates.append(('mesh', {})))
        names = [role + '.' + side for side in ('L', 'R') for role in ('upper', 'lower', 'hand')]
        names += ['other.L', 'other.R']
        self.bones = {name: NS(matrix_local=Matrix(label=name),
                               head_local=Vector((0., 0., 0.)),
                               tail_local=Vector((0., 1., 0.))) for name in names}
        self.arm = NS(name='Rig', data=NS(bones=self.bones), matrix_world=Matrix(),
                      pose=NS(bones={name: NS(matrix=Matrix(label=name)) for name in names}))
        self.arm.evaluated_get = lambda graph: self.arm
        records = {side: {'key': 'Twist.' + side, 'armature': 'Rig',
                         'chain': [role + '.' + side for role in ('upper', 'lower', 'hand')],
                         'vertices': list(range(support)) if side == 'L' else list(range(support, 2 * support)),
                         'positions': [(i + .5) / support for i in range(support)],
                         'range_start': 0, 'range_end': 1,
                         'rings': [{'side': side, 'position': 0., 'ratio': 0.},
                                   {'side': side, 'position': 1., 'ratio': 1.}]}
                   for side in ('L', 'R')}
        for record in records.values():
            record['rest'] = self.rest_values(record['chain'])
        self.obj[RECORD_KEY] = json.dumps(records)
        self.base = {i: Vector(point) for i, point in enumerate(points)}
        self.weights = {i: {'lower.' + side: .4, 'hand.' + side: .5, 'other.' + side: .1}
                        for side, indices in (('L', range(support)), ('R', range(support, 2 * support)))
                        for i in indices}
        self.ns.update(_records=lambda obj: json.loads(obj[RECORD_KEY]),
                       _check_mesh=lambda obj: self.arm, _topology=lambda mesh: self.obj.topology,
                       _current_topology_record=lambda mesh, record, **kwargs: record,
                       _managed_key=lambda obj, side, record: self.keys[side],
                       _input_mix=lambda obj, indices, *args: {i: self.base[i] for i in indices},
                       _weights=lambda obj, arm, indices: {i: dict(self.weights[i]) for i in indices},
                       _rest_signature=self.rest, twist_angle=self.angle,
                       corrected_vertex=self.correct,
                       profile=NS(profile_knots=self.knots, record_range=self.record_range,
                                  compile_sampler=self.sampler),
                       validation_cache=NS(basis_coordinates=self.basis_coordinates))

    def basis_coordinates(self, obj):
        result = array('f', [0.]) * len(obj.data.shape_keys.reference_key.data.values)
        obj.data.shape_keys.reference_key.data.foreach_get('co', result)
        return result

    def rest_values(self, chain):
        return [[name, [v for row in self.bones[name].matrix_local for v in row]] for name in chain]

    def guard(self, stage, side):
        self.guard_calls.append((stage, side))
        if self.invalid == (stage, side):
            raise self.ns['ForearmTwistError']('invalid ' + stage + ' ' + side)

    def rest(self, arm, chain):
        self.guard('rest', chain[0][-1])
        return self.rest_values(chain)

    def angle(self, lower, hand, axis):
        side = hand.label[-1]
        self.guard('angle', side)
        if not all(math.isfinite(v) for mat in (lower, hand) for row in mat for v in row):
            raise self.ns['ForearmTwistError']('invalid matrix')
        return hand.rows[1][3] - lower.rows[1][3]

    def knots(self, rings):
        self.guard('profile', rings[0]['side'])
        return [(ring['position'], ring['ratio']) for ring in rings]

    def record_range(self, record):
        self.guard('range', record['rings'][0]['side'])
        return record['range_start'], record['range_end']

    def sampler(self, rings, **kwargs):
        side = rings[0]['side']
        self.guard('sampler', side)

        def sample(position):
            self.guard('sample', side)
            if not math.isfinite(position):
                raise self.ns['ForearmTwistError']('invalid position')
            return position, 1.

        return sample

    def correct(self, point, transforms, weights, lower, hand, axis, pivot, ratio, **kwargs):
        side = lower[-1]
        self.calls.append(side)
        self.guard('correct', side)
        if not all(math.isfinite(v) for v in point) or not all(math.isfinite(v) for v in weights.values()):
            raise self.ns['ForearmTwistError']('invalid point or weights')
        if not all(math.isfinite(v) for name in weights for row in transforms[name] for v in row):
            raise self.ns['ForearmTwistError']('invalid weighted matrix')
        # Preserve order in the differential oracle, including double weights.
        offset = sum(weight * (transforms[name].rows[0][3] + .125)
                     for name, weight in weights.items())
        return point + Vector((offset + ratio * kwargs['angle'] + pivot[0], axis[1] * .125, 0.))

    def run(self, cold=False):
        if cold:
            self.ns['_CACHE'].clear()
            self.ns['_OUTPUT_CACHE'].clear()
        self.ns['_calculate_records'](self.obj, object())

    def pose(self, side, angle):
        name = 'hand.' + side
        self.arm.pose.bones[name].matrix = self.arm.pose.bones[name].matrix.with_entry(1, 3, angle)

    def write_records(self, change):
        records = json.loads(self.obj[RECORD_KEY])
        change(records)
        self.obj[RECORD_KEY] = json.dumps(records)

    def snapshot(self):
        return {side: (key.data.values.tobytes(), struct.pack('=d', key.value), key.mute)
                for side, key in self.keys.items()}

    def reset_counts(self):
        self.calls.clear()
        self.guard_calls.clear()
        self.updates.clear()
        for key in self.keys.values():
            key.data.writes = 0


class SideOutputReuseTests(unittest.TestCase):
    def pair(self):
        return Fixture(), Fixture(BASELINE or SOURCE)

    def equal_after(self, candidate, reference):
        candidate.run()
        reference.run(cold=True)
        self.assertEqual(candidate.snapshot(), reference.snapshot())

    def warmed(self):
        fixture = Fixture()
        fixture.run()
        fixture.reset_counts()
        return fixture

    def test_cold_and_whole_hit_match_full_recomputation(self):
        candidate, reference = self.pair()
        self.equal_after(candidate, reference)
        candidate.reset_counts()
        self.equal_after(candidate, reference)
        self.assertEqual(candidate.calls, [])
        self.assertEqual(candidate.updates, [])
        self.assertEqual([key.data.writes for key in candidate.keys.values()], [0, 0])

    def test_one_side_action_sequence_exact_and_sibling_not_written(self):
        candidate, reference = self.pair()
        self.equal_after(candidate, reference)
        previous_angle = 0.
        for frame in range(1, 31):
            angle = .5 * math.sin(frame * math.tau / 30)
            candidate.reset_counts()
            candidate.pose('L', angle)
            reference.pose('L', angle)
            self.equal_after(candidate, reference)
            changed = f32(angle) != previous_angle
            self.assertEqual(candidate.calls, ['L', 'L'] if changed else [])
            self.assertEqual(candidate.keys['R'].data.writes, 0)
            if changed:
                for stage in ('rest', 'angle', 'profile', 'range', 'sampler'):
                    self.assertIn((stage, 'R'), candidate.guard_calls)
                self.assertIn(('keys', {'refresh': {'TIME'}}), candidate.updates)
            previous_angle = f32(angle)

    def test_both_sides_action_sequence_exact(self):
        candidate, reference = self.pair()
        for frame in range(12):
            for side, sign in (('L', 1), ('R', -1)):
                candidate.pose(side, sign * .04 * frame)
                reference.pose(side, sign * .04 * frame)
            self.equal_after(candidate, reference)

    def test_every_positive_weight_dependency_is_scoped_to_its_side(self):
        fixture = self.warmed()
        for name in ('other.R', 'upper.R', 'lower.R'):
            fixture.reset_counts()
            bone = fixture.arm.pose.bones[name]
            bone.matrix = bone.matrix.with_entry(0, 3, bone.matrix.rows[0][3] + .125)
            fixture.run()
            self.assertEqual(fixture.calls, ['R', 'R'])
            self.assertEqual(fixture.keys['L'].data.writes, 0)

    def test_weight_order_and_adjacent_double_bits_each_force_recalculation(self):
        fixture = self.warmed()
        for values in (dict(reversed(tuple(fixture.weights[2].items()))),
                       {'lower.R': math.nextafter(.4, 1.), 'hand.R': .5, 'other.R': .1}):
            fixture.weights[2] = values
            fixture.reset_counts()
            fixture.run()
            self.assertEqual(fixture.calls, ['R', 'R'])

    def test_signed_zero_in_base_geometry_rest_and_frames_cannot_alias(self):
        mutations = [lambda f: f.base.__setitem__(2, Vector((f.base[2][0], -0., f.base[2][2]))),
                     lambda f: setattr(f.bones['hand.R'], 'head_local', Vector((-0., 0., 0.))),
                     lambda f: setattr(f.bones['upper.R'], 'matrix_local', Matrix().with_entry(0, 1, -0.)),
                     lambda f: setattr(f.obj, 'matrix_world', Matrix().with_entry(0, 1, -0.))]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                fixture = self.warmed()
                if mutate is mutations[0]:
                    fixture.base[2] = Vector((fixture.base[2][0], 0., fixture.base[2][2]))
                    fixture.run()
                    fixture.reset_counts()
                mutate(fixture)
                if mutate is mutations[2]:
                    # Keep this test a valid state while retaining new Rest bits.
                    fixture.write_records(lambda data: data['R'].__setitem__(
                        'rest', fixture.rest_values(data['R']['chain'])))
                fixture.run()
                self.assertIn('R', fixture.calls)

    def test_raw_axis_and_pivot_change_recalculates_unchanged_deformation(self):
        fixture = self.warmed()
        fixture.bones['lower.R'].tail_local = Vector((.001, 1., 0.))
        fixture.run()
        self.assertEqual(fixture.calls, ['R', 'R'])
        fixture.reset_counts()
        fixture.bones['hand.R'].head_local = Vector((.001, 0., 0.))
        fixture.run()
        self.assertEqual(fixture.calls, ['R', 'R'])

    def test_output_coordinate_value_and_mute_tamper_repairs_only_that_side(self):
        for mutate in (lambda key: key.data.__getitem__(4).__setattr__('co', Vector((90, 91, 92))),
                       lambda key: setattr(key, 'value', .25), lambda key: setattr(key, 'mute', True)):
            with self.subTest(mutate=mutate):
                candidate, reference = self.pair()
                self.equal_after(candidate, reference)
                candidate.reset_counts()
                mutate(candidate.keys['R'])
                mutate(reference.keys['R'])
                self.equal_after(candidate, reference)
                self.assertEqual(candidate.calls, ['R', 'R'])
                self.assertEqual(candidate.keys['L'].data.writes, 0)

    def test_new_native_key_lifetime_cannot_reuse_previous_full_bytes(self):
        fixture = self.warmed()
        fixture.keys['R'].pointer += 1000
        fixture.run()
        self.assertEqual(fixture.calls, ['R', 'R'])

    def test_no_native_lifetime_proof_never_fast_hits(self):
        fixture = Fixture()
        fixture.obj.session_uid = None
        fixture.run()
        fixture.reset_counts()
        fixture.run()
        self.assertEqual(fixture.calls, ['L', 'L', 'R', 'R'])

    def test_disabled_basis_reset_and_manual_tamper_are_preserved(self):
        candidate, reference = self.pair()
        for fixture in (candidate, reference):
            fixture.write_records(lambda data: data['R'].__setitem__('enabled', False))
        self.equal_after(candidate, reference)
        self.assertEqual(candidate.keys['R'].data.values, candidate.obj.data.shape_keys.reference_key.data.values)
        candidate.reset_counts()
        for fixture in (candidate, reference):
            fixture.pose('L', .2)
        self.equal_after(candidate, reference)
        self.assertEqual(candidate.keys['R'].data.writes, 0)
        self.assertNotIn(('angle', 'R'), candidate.guard_calls)
        for fixture in (candidate, reference):
            fixture.keys['R'].data[4].co = Vector((9, 9, 9))
        candidate.reset_counts()
        self.equal_after(candidate, reference)
        self.assertEqual(candidate.keys['R'].data.writes, 1)
        self.assertEqual(candidate.calls, [])

    def test_common_basis_topology_record_and_frames_invalidate_all_sides(self):
        changes = [lambda f: setattr(f.obj, 'topology', 'new topology'),
                   lambda f: setattr(f.obj.data.shape_keys.reference_key.data[4], 'co', Vector((8, 9, 10))),
                   lambda f: f.write_records(lambda data: data['L'].__setitem__('note', 'different raw record')),
                   lambda f: setattr(f.obj, 'matrix_world', Matrix().with_entry(0, 3, .5))]
        for change in changes:
            with self.subTest(change=change):
                candidate, reference = self.pair()
                self.equal_after(candidate, reference)
                candidate.reset_counts()
                change(candidate)
                change(reference)
                self.equal_after(candidate, reference)
                self.assertEqual(candidate.calls, ['L', 'L', 'R', 'R'])

    def test_support_even_beyond_zip_positions_is_part_of_proof(self):
        fixture = Fixture()
        fixture.write_records(lambda data: data['R'].__setitem__('positions', [.2]))
        fixture.run()
        fixture.reset_counts()
        fixture.base[3] = fixture.base[3] + Vector((.1, .2, .3))
        fixture.run()
        self.assertEqual(fixture.calls, ['R'])

    def test_changed_sibling_does_not_skip_unchanged_side_fresh_guards(self):
        for stage in ('rest', 'angle', 'profile', 'range', 'sampler'):
            with self.subTest(stage=stage):
                fixture = self.warmed()
                before, accepted = fixture.snapshot(), copy.deepcopy(fixture.ns['_CACHE'])
                fixture.pose('L', .3)
                fixture.invalid = (stage, 'R')
                with self.assertRaisesRegex(fixture.ns['ForearmTwistError'], 'invalid ' + stage + ' R'):
                    fixture.run()
                self.assertEqual(fixture.snapshot(), before)
                self.assertEqual(fixture.updates, [])
                self.assertEqual(fixture.ns['_CACHE'], accepted)
                self.assertTrue(all(key.data.writes == 0 for key in fixture.keys.values()))
                fixture.invalid = None
                fixture.reset_counts()
                fixture.run()
                self.assertEqual(fixture.calls, ['L', 'L'])

    def test_changed_side_inverse_failure_is_transactional(self):
        fixture = self.warmed()
        before = fixture.snapshot()
        fixture.pose('R', .3)
        fixture.invalid = ('correct', 'R')
        with self.assertRaisesRegex(fixture.ns['ForearmTwistError'], 'invalid correct R'):
            fixture.run()
        self.assertEqual(fixture.snapshot(), before)
        self.assertEqual(fixture.updates, [])

    def test_cold_and_warm_errors_match_full_calculation_class_message_and_guard_order(self):
        for warm in (False, True):
            for stage in ('rest', 'angle', 'profile', 'range', 'sampler'):
                with self.subTest(warm=warm, stage=stage):
                    candidate, reference = self.pair()
                    if warm:
                        self.equal_after(candidate, reference)
                    errors = []
                    for fixture in (candidate, reference):
                        fixture.reset_counts()
                        before = fixture.snapshot()
                        fixture.pose('L', .3)
                        fixture.invalid = (stage, 'R')
                        with self.assertRaises(fixture.ns['ForearmTwistError']) as error:
                            fixture.run(cold=fixture is reference)
                        errors.append((type(error.exception).__name__, str(error.exception)))
                        self.assertEqual(fixture.snapshot(), before)
                        self.assertEqual(fixture.updates, [])
                    self.assertEqual(errors[0], errors[1])
                    self.assertEqual([row for row in candidate.guard_calls if row[0] not in ('sample', 'correct')],
                                     [row for row in reference.guard_calls if row[0] not in ('sample', 'correct')])

    def test_managed_structure_is_checked_before_whole_hit(self):
        for field, value in (('relative_key', None), ('vertex_group', 'new mask')):
            with self.subTest(field=field):
                fixture = self.warmed()
                before = fixture.snapshot()
                setattr(fixture.keys['R'], field, value)
                with self.assertRaisesRegex(fixture.ns['ForearmTwistError'], 'managed corrective key'):
                    fixture.run()
                self.assertEqual(fixture.snapshot(), before)
                self.assertEqual(fixture.updates, [])

    def test_outside_range_still_checks_later_sibling_before_typed_failure(self):
        fixture = self.warmed()
        fixture.pose('L', 2.2)
        fixture.invalid = ('profile', 'R')
        with self.assertRaisesRegex(fixture.ns['ForearmTwistError'], 'invalid profile R') as error:
            fixture.run()
        self.assertNotIsInstance(error.exception, fixture.ns['ForearmTwistPoseRangeError'])
        fixture.invalid = None
        with self.assertRaises(fixture.ns['ForearmTwistPoseRangeError']):
            fixture.run()
        self.assertEqual(fixture.updates, [])

    def test_nonfinite_inputs_cannot_use_or_seed_a_fast_proof(self):
        for value in (math.nan, math.inf, -math.inf):
            for target in ('point', 'weight', 'matrix'):
                with self.subTest(value=value, target=target):
                    fixture = self.warmed()
                    before = fixture.snapshot()
                    if target == 'point':
                        fixture.base[2] = Vector((value, 1., 2.))
                    elif target == 'weight':
                        fixture.weights[2]['lower.R'] = value
                    else:
                        bone = fixture.arm.pose.bones['other.R']
                        bone.matrix = bone.matrix.with_entry(0, 3, value)
                    with self.assertRaises(fixture.ns['ForearmTwistError']):
                        fixture.run()
                    self.assertEqual(fixture.snapshot(), before)
                    self.assertEqual(fixture.updates, [])

    def test_rest_change_compensated_in_pose_does_not_hide_behind_same_transform(self):
        fixture = self.warmed()
        name = 'upper.R'
        fixture.bones[name].matrix_local = Matrix().with_entry(0, 3, .5)
        fixture.arm.pose.bones[name].matrix = Matrix(label=name).with_entry(0, 3, .5)
        with self.assertRaisesRegex(fixture.ns['ForearmTwistError'], 'rig is changing'):
            fixture.run()
        self.assertEqual(fixture.updates, [])

    def test_old_cache_schema_and_runtime_clears_require_cold_recalculation(self):
        fixture = self.warmed()
        fixture.ns['_CACHE'][42] = ('old raw json', 'topology', b'basis', (), (), ())
        fixture.run()
        self.assertEqual(fixture.calls, ['L', 'L', 'R', 'R'])
        for cache in ('_CACHE', '_OUTPUT_CACHE'):
            fixture.reset_counts()
            fixture.ns[cache].clear()
            fixture.run()
            self.assertEqual(fixture.calls, ['L', 'L', 'R', 'R'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--baseline', type=Path)
    arguments, remaining = parser.parse_known_args()
    BASELINE = arguments.baseline
    unittest.main(argv=[sys.argv[0], *remaining])
