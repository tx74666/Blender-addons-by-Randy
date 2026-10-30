"""Pinned .91 blend comparison with float32 Matrix doubles; no Blender.

The trusted runtime input is a mapping of native 4x4 float row tuples. These
tests check scalar products/sum bits before Matrix rounding, final float32 bits,
guards and cache isolation. Native mathutils/whole-call timing remains separate.
"""
import ast
import hashlib
import math
from pathlib import Path
import random
import struct
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/forearm_twist_math.py'
APPROVED_MODULE_SHA = 'd2dc9a3c2305530dec2e3527a087d2f96179e540b4527259f8e348dd3eeae6a0'
APPROVED_BLEND_SHA = '7c980b329d51ffdaabbcc46fdb8d6c13ffaa97b92280f38a9716a8aac589ce15'
# ast.dump changed its optional-field formatting in 3.13. Both hashes below
# were calculated from the approved file, excluding only blended_matrix.
UNCHANGED_AST_SHAS = {
    'ccba226ee5188a9a203166379883f6b688dfa529c1574b57d0d63320ddd7fc99',
    'b082293a768843cb596285d21feece7838e56c47ce192e24135b92cb30474818',
}
# Verbatim LF source plus final newline from approved .91 (same math as .89).
REFERENCE = '''def blended_matrix(transforms, weights, *, _matrix_rows=None, _blend_cache=None):
    """Actual linear skinning matrix for normalized deform-bone weights."""
    weights = _weights(transforms, weights)
    if not weights:
        return Matrix.Identity(4)
    # A trusted runtime caller can provide a dictionary local to one synchronous
    # calculation with fixed native transforms/rows. Key only AFTER the existing
    # checks/normalization: desired and actual paths normalize a different number
    # of times and must retain their exact, ordered floating-point expressions.
    # Never share a cached mutable Matrix with a caller.
    cache_key = tuple(weights.items()) if _blend_cache is not None else None
    cached = _blend_cache.get(cache_key) if _blend_cache is not None else None
    # Runtime callers may snapshot native matrix rows once for their current
    # synchronous calculation. Keep the weight checks, scalar multiply/sum
    # order and final Matrix rounding identical to direct mathutils access.
    source = transforms if _matrix_rows is None else _matrix_rows
    result = (cached.copy() if cached is not None else
              Matrix(tuple(tuple(sum(weight * source[name][row][column]
                                     for name, weight in weights.items())
                               for column in range(4)) for row in range(4))))
    if not _finite_matrix(result):
        raise TwistMathError("Blended deformation contains non-finite values")
    if _blend_cache is not None and cached is None:
        _blend_cache[cache_key] = result.copy()
    return result
'''


def f32(value):
    try:
        return struct.unpack('=f', struct.pack('=f', value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def bits(rows, width):
    return b''.join(struct.pack('=' + width, value) for row in rows for value in row)


class Matrix:
    def __init__(self, rows):
        self.raw = tuple(tuple(row) for row in rows)
        self.rows = [[f32(value) for value in row] for row in self.raw]

    def __iter__(self):
        return iter(self.rows)

    def __getitem__(self, index):
        return self.rows[index]

    def copy(self):
        # Native Matrix.copy copies the already rounded float32 coordinates.
        return Matrix(self.rows)

    @classmethod
    def Identity(cls, size):
        return cls(tuple(tuple(float(row == column) for column in range(size))
                         for row in range(size)))


class Reads(dict):
    def __init__(self, values):
        super().__init__(values)
        self.reads = []

    def __getitem__(self, key):
        self.reads.append(key)
        return super().__getitem__(key)


def scopes():
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'TwistMathError', '_finite', '_finite_matrix', '_weights', 'blended_matrix'}
    subset = ast.Module(body=[node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names], type_ignores=[])
    candidate = dict(math=math, Matrix=Matrix, Vector=type('NativeVectorUnavailable', (), {}))
    exec(compile(subset, str(SOURCE), 'exec'), candidate)
    reference = candidate.copy()
    assert hashlib.sha256(REFERENCE.encode()).hexdigest() == APPROVED_BLEND_SHA
    exec(compile(REFERENCE, 'approved091.blended_matrix', 'exec'), reference)
    return reference, candidate


def outcome(scope, rows, weights, *, trusted=True, cache=None):
    try:
        result = scope['blended_matrix'](rows, weights,
            _matrix_rows=rows if trusted else None, _blend_cache=cache)
        return ('value', bits(result.raw, 'd'), bits(result.rows, 'f'))
    except Exception as error:
        return ('error', type(error).__name__, str(error))


class MatrixProductsTests(unittest.TestCase):
    def setUp(self):
        self.reference, self.candidate = scopes()

    def compare(self, rows, weights, **options):
        expected = outcome(self.reference, rows, weights, **options)
        actual = outcome(self.candidate, rows, weights, **options)
        self.assertEqual(actual, expected)
        return actual

    def test_only_blended_matrix_changes_and_reference_pin_is_independent(self):
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        tree.body = [node for node in tree.body
                     if not (isinstance(node, ast.FunctionDef) and node.name == 'blended_matrix')]
        self.assertIn(hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest(),
                      UNCHANGED_AST_SHAS)
        self.assertEqual(hashlib.sha256(REFERENCE.encode()).hexdigest(), APPROVED_BLEND_SHA)

    def test_random_ordered_products_match_double_and_float32_bits(self):
        rng = random.Random(0x91A)
        for count in (1, 2, 3, 4, 6):
            for _sample in range(100):
                raw = [rng.uniform(.000001, 1.) for _ in range(count)]
                total = sum(raw)
                weights = {str(i): value / total for i, value in enumerate(raw)}
                rows = {str(i): tuple(tuple(f32(rng.uniform(-1.e18, 1.e18)) for _ in range(4))
                                      for _ in range(4)) for i in range(count)}
                self.compare(rows, weights)
                self.compare(rows, dict(reversed(tuple(weights.items()))))

    def test_compensated_sum_cancellation_is_not_replaced_with_scalar_adds(self):
        rows = {str(i): tuple(tuple(value if row == column == 0 else float(row == column)
                                   for column in range(4)) for row in range(4))
                for i, value in enumerate((3.e16, 3., -3.e16))}
        weights = {str(i): 1. / 3. for i in range(3)}
        self.compare(rows, weights)
        result = self.candidate['blended_matrix'](rows, weights, _matrix_rows=rows)
        self.assertEqual(result.raw[0][0], sum((1.e16, 1., -1.e16)))
        # Python 3.12+ returns 1.0 here; a naive += loop returns 0.0.

    def test_signed_zero_subnormal_overflow_and_nonfinite_outcomes_match(self):
        values = (0., -0., 2.**-149, -2.**-149, 2.**-1074, -2.**-1074,
                  3.4028234663852886e38, -3.4028234663852886e38,
                  1.e308, -1.e308, math.inf, -math.inf, math.nan)
        for count in (1, 2, 3, 4, 6):
            weights = {str(i): 1. / count for i in range(count)}
            for value in values:
                rows = {str(i): tuple(tuple(value if row == column else -0. for column in range(4))
                                      for row in range(4)) for i in range(count)}
                self.compare(rows, weights)
                self.compare(rows, weights, trusted=False)

    def test_all_nonfinite_components_reject_without_cache_and_weights_are_checked_first(self):
        base = tuple(tuple(float(row == column) for column in range(4)) for row in range(4))
        for row in range(4):
            for column in range(4):
                for bad in (math.nan, math.inf, -math.inf):
                    values = [list(part) for part in base]
                    values[row][column] = bad
                    rows = {'a': tuple(map(tuple, values))}
                    for scope in (self.reference, self.candidate):
                        cache = {}
                        result = outcome(scope, rows, {'a': 1.}, cache=cache)
                        self.assertEqual(result, ('error', 'TwistMathError', 'Blended deformation contains non-finite values'))
                        self.assertEqual(cache, {})
        rows = Reads({'a': base})
        for weights in ({'a': math.nan}, {'a': math.inf}, {'a': -1.}, {'a': .2}, {'missing': 1.}):
            self.compare(rows, weights)
        self.assertEqual(rows.reads, [])

    def test_trusted_small_miss_reduces_source_reads_and_fallback_order_is_unchanged(self):
        base = tuple(tuple(float(row == column) for column in range(4)) for row in range(4))
        for count in (1, 2, 3, 4, 6):
            weights = {str(i): 1. / count for i in range(count)}
            for trusted in (False, True):
                before, after = Reads({str(i): base for i in range(count)}), Reads({str(i): base for i in range(count)})
                self.assertEqual(outcome(self.reference, before, weights, trusted=trusted),
                                 outcome(self.candidate, after, weights, trusted=trusted))
                self.assertEqual(len(before.reads), 16 * count)
                if trusted and count <= 4:
                    self.assertEqual(after.reads, list(weights))
                else:
                    self.assertEqual(after.reads, before.reads)

    def test_cache_copy_finite_checks_and_changed_next_call_remain_exact(self):
        rows = {'a': tuple(tuple(float(row == column) for column in range(4)) for row in range(4))}
        weights = {'a': 1.}
        for scope in (self.reference, self.candidate):
            cache = {}
            expected = scope['blended_matrix'](rows, weights, _matrix_rows=rows, _blend_cache=cache)
            returned = scope['blended_matrix'](rows, weights, _matrix_rows=rows, _blend_cache=cache)
            returned[0][0] = 93.
            self.assertEqual(bits(scope['blended_matrix'](rows, weights, _matrix_rows=rows,
                _blend_cache=cache).rows, 'f'), bits(expected.rows, 'f'))
            next(iter(cache.values()))[0][0] = math.nan
            self.assertEqual(outcome(scope, rows, weights, cache=cache),
                             ('error', 'TwistMathError', 'Blended deformation contains non-finite values'))
            with self.assertRaisesRegex(scope['TwistMathError'], 'Bone weights'):
                scope['blended_matrix'](rows, {'a': math.nan}, _matrix_rows=rows, _blend_cache=cache)
        changed = {'a': tuple(tuple(2. * value for value in row) for row in rows['a'])}
        self.compare(changed, weights, cache={})

    def test_normalization_passes_order_and_empty_weight_path_remain_distinct(self):
        rows = {name: tuple(tuple((index + 1.) * float(row == column) for column in range(4))
                            for row in range(4)) for index, name in enumerate(('a', 'b', 'c'))}
        raw = {'a': .1281599137849305, 'b': .12181411510790636, 'c': .7500359711071631}
        once = self.reference['_weights'](rows, raw)
        twice = self.reference['_weights'](rows, once)
        self.assertNotEqual(tuple(once.items()), tuple(twice.items()))
        for weights in (raw, once, dict(reversed(tuple(raw.items())))):
            self.compare(rows, weights)
        for scope in (self.reference, self.candidate):
            checked = Mock(wraps=scope['_weights'])
            finite = Mock(wraps=scope['_finite_matrix'])
            scope['_weights'], scope['_finite_matrix'] = checked, finite
            cache = {}
            result = scope['blended_matrix']({}, {}, _matrix_rows={}, _blend_cache=cache)
            self.assertEqual(bits(result.rows, 'f'), bits(Matrix.Identity(4).rows, 'f'))
            checked.assert_called_once()
            finite.assert_not_called()
            self.assertEqual(cache, {})


if __name__ == '__main__':
    unittest.main()
