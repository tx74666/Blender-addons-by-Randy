"""Rest comparison guards without importing Blender or claiming native timing.

The small vector stand-in checks control flow against the frozen expression;
mathutils rounding and real RNA behavior still require native validation.
"""

import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_animation.py'
tree = ast.parse(SOURCE.read_text(encoding='utf-8-sig'), filename=str(SOURCE))
node = next(node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == '_same_rest_bone')
namespace = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
compare = namespace['_same_rest_bone']
FLAGS = ('use_deform', 'use_inherit_rotation', 'inherit_scale',
         'use_local_location', 'use_relative_parent')


def baseline(original, preview):
    if (original.parent.name if original.parent else None) != (preview.parent.name if preview.parent else None):
        return False
    if any(getattr(original, field) != getattr(preview, field) for field in FLAGS):
        return False
    if (original.head_local - preview.head_local).length > 1e-6 or (original.tail_local - preview.tail_local).length > 1e-6:
        return False
    return max(abs(original.matrix_local[i][j] - preview.matrix_local[i][j])
               for i in range(4) for j in range(4)) <= 1e-6


class Vector:
    def __init__(self, *values):
        self.values = values

    def __sub__(self, other):
        return Vector(*(a - b for a, b in zip(self.values, other.values)))

    @property
    def length(self):
        return math.sqrt(sum(value * value for value in self.values))


class Matrix:
    def __init__(self, label, events):
        self.label, self.events = label, events
        self.values = [[float(i == j) for j in range(4)] for i in range(4)]

    def __getitem__(self, row):
        matrix = self

        class Row:
            def __getitem__(self, column):
                matrix.events.append(('scalar', matrix.label, row, column))
                return matrix.values[row][column]

        return Row()


class Bone:
    def __init__(self, label, events):
        self.label, self.events, self.errors = label, events, {}
        self.values = dict(parent=SimpleNamespace(name='Root'),
                           head_local=Vector(0., 0., 0.), tail_local=Vector(0., 1., 0.),
                           matrix_local=Matrix(label, events), use_connect=True,
                           **dict(zip(FLAGS, (True, True, 'FULL', True, False))))

    def __getattr__(self, field):
        if field not in self.values:
            raise AttributeError(field)
        self.events.append(('field', self.label, field))
        if field in self.errors:
            raise ValueError(self.errors[field])
        return self.values[field]


def run(function, configure=lambda original, preview: None):
    events = []
    original, preview = Bone('original', events), Bone('preview', events)
    configure(original, preview)
    try:
        result = function(original, preview)
    except Exception as error:
        result = (type(error), str(error))
    return result, events, original, preview


def property_order(events):
    """Keep first matrix reads; repeated reads carry no new static property."""
    result, matrices = [], set()
    for event in events:
        if event[0] != 'field':
            continue
        if event[2] == 'matrix_local':
            if event[1] in matrices:
                continue
            matrices.add(event[1])
        result.append(event)
    return result


class RestBoneMatrixReadsTests(unittest.TestCase):
    def assert_oracle(self, configure=lambda original, preview: None):
        expected = run(baseline, configure)
        actual = run(compare, configure)
        self.assertEqual(actual[0], expected[0])
        self.assertEqual(property_order(actual[1]), property_order(expected[1]))
        if isinstance(actual[0], bool):
            self.assertEqual([event for event in actual[1] if event[0] == 'scalar'],
                             [event for event in expected[1] if event[0] == 'scalar'])
        return actual

    def test_all_matrix_cells_keep_row_major_scalar_order_and_tolerance(self):
        expected_order = [('scalar', side, row, column) for row in range(4)
                          for column in range(4) for side in ('original', 'preview')]
        for row in range(4):
            for column in range(4):
                for delta, accepted in ((5e-7, True), (2e-6, False)):
                    with self.subTest(row=row, column=column, delta=delta):
                        def configure(original, preview):
                            preview.values['matrix_local'].values[row][column] += delta
                        result, events, _, _ = self.assert_oracle(configure)
                        self.assertIs(result, accepted)
                        self.assertEqual([event for event in events if event[0] == 'scalar'], expected_order)

    def test_exact_matrix_boundary_and_nonfinite_expression_are_preserved(self):
        for value in (math.nextafter(1e-6, 0.), 1e-6,
                      math.nextafter(1e-6, math.inf), math.inf, math.nan):
            with self.subTest(value=value):
                result, _, _, _ = self.assert_oracle(
                    lambda original, preview: preview.values['matrix_local'].values[0].__setitem__(1, value))
                if math.isfinite(value):
                    self.assertIs(result, value <= 1e-6)

    def test_parent_and_each_inheritance_flag_refuse_before_matrix_read(self):
        changes = [('parent', SimpleNamespace(name='Other Root')),
                   ('parent', None), *[(field, 'NONE' if field == 'inherit_scale'
                                      else not value) for field, value in
                                     zip(FLAGS, (True, True, 'FULL', True, False))]]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                result, events, _, _ = self.assert_oracle(
                    lambda original, preview: preview.values.__setitem__(field, value))
                self.assertIs(result, False)
                self.assertFalse(any(event[0] == 'scalar' or event[-1] == 'matrix_local' for event in events))

    def test_head_and_same_axis_tail_changes_refuse_before_matrix_read(self):
        for field, value in (('head_local', Vector(2e-6, 0., 0.)),
                             ('tail_local', Vector(0., 1.01, 0.))):
            with self.subTest(field=field):
                result, events, _, _ = self.assert_oracle(
                    lambda original, preview: preview.values.__setitem__(field, value))
                self.assertIs(result, False)
                self.assertFalse(any(event[0] == 'scalar' or event[-1] == 'matrix_local' for event in events))

    def test_head_tail_boundary_keeps_existing_vector_operations(self):
        for delta in (math.nextafter(1e-6, 0.), 1e-6, math.nextafter(1e-6, math.inf)):
            for field in ('head_local', 'tail_local'):
                with self.subTest(field=field, delta=delta):
                    result, _, _, _ = self.assert_oracle(
                        lambda original, preview: preview.values.__setitem__(
                            field, Vector(delta, 0. if field == 'head_local' else 1., 0.)))
                    self.assertIs(result, delta <= 1e-6)

    def test_connected_flag_remains_the_separately_validated_exception(self):
        result, _, _, _ = self.assert_oracle(
            lambda original, preview: preview.values.__setitem__('use_connect', False))
        self.assertIs(result, True)

    def test_static_property_errors_keep_first_failure_and_order(self):
        fields = ('parent', *FLAGS, 'head_local', 'tail_local', 'matrix_local')
        for field in fields:
            for side in ('original', 'preview'):
                with self.subTest(field=field, side=side):
                    def configure(original, preview):
                        bone = original if side == 'original' else preview
                        bone.errors[field] = side + '.' + field
                        # A later static fault must not mask the first refusal.
                        if field != 'matrix_local':
                            preview.errors['matrix_local'] = 'later matrix fault'
                    result, _, _, _ = self.assert_oracle(configure)
                    self.assertEqual(result, (ValueError, side + '.' + field))

    def test_direct_mutation_is_seen_on_each_call_without_positive_cache(self):
        result, events, original, preview = run(compare)
        self.assertIs(result, True)
        for side in ('original', 'preview'):
            reads = events.count(('field', side, 'matrix_local'))
            # Current baseline reads 16 times; the planned local hoist reads once.
            self.assertGreaterEqual(reads, 1)
            self.assertLessEqual(reads, 16)
        preview.values['matrix_local'].values[2][3] = 2e-6
        events.clear()
        self.assertIs(compare(original, preview), False)
        self.assertIn(('field', 'preview', 'matrix_local'), events)
        preview.values['matrix_local'].values[2][3] = 0.
        events.clear()
        self.assertIs(compare(original, preview), True)
        self.assertIn(('field', 'original', 'matrix_local'), events)


if __name__ == '__main__':
    unittest.main()
