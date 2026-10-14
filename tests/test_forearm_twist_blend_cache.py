"""Small AST-isolated blend-cache checks; native mathutils proof is separate.

These tests execute the production weight/blend functions with a float32 Matrix
stub. They cover cache lifetime inputs, validation and mutation isolation, not
Blender inversion or native correction equality. Run the native follow-math
suite and real-model paired profile before accepting the candidate.
"""

import ast
import math
from pathlib import Path
import struct
import unittest
from unittest.mock import Mock


class MatrixStub:
    constructions = 0

    def __init__(self, rows):
        type(self).constructions += 1
        self.rows = [[struct.unpack("=f", struct.pack("=f", value))[0]
                      for value in row] for row in rows]

    def __getitem__(self, index):
        return self.rows[index]

    def __iter__(self):
        return iter(self.rows)

    def copy(self):
        result = type(self).__new__(type(self))
        result.rows = [row.copy() for row in self.rows]
        return result

    @classmethod
    def Identity(cls, size):
        return cls(tuple(tuple(float(row == column) for column in range(size))
                         for row in range(size)))


def namespace():
    path = (Path(__file__).resolve().parents[1]
            / "addons/character_designer/forearm_twist_math.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {"TwistMathError", "_finite", "_weights", "blended_matrix"}
    subset = ast.Module(body=[node for node in tree.body
                             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                             and node.name in names], type_ignores=[])
    result = {"math": math, "Matrix": MatrixStub}
    exec(compile(subset, str(path), "exec"), result)
    return result


class BlendCacheTests(unittest.TestCase):
    def setUp(self):
        self.scope = namespace()
        self.blend = self.scope["blended_matrix"]
        self.check = self.scope["_weights"]
        self.transforms = {"lower": ((0.7, 0.1, 0.2, 0.3), (0.1, 0.8, 0.4, 0.2),
                                      (0.1, 0.2, 0.9, 0.5), (0.0, 0.0, 0.0, 1.0)),
                           "hand": ((0.2, 0.3, 0.4, 0.5), (0.6, 0.7, 0.8, 0.9),
                                    (0.5, 0.1, 0.3, 0.8), (0.0, 0.0, 0.0, 1.0)),
                           "other": ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0),
                                     (0.0, 0.0, 1.0, 0.0), (0.0, 0.0, 0.0, 1.0))}
        MatrixStub.constructions = 0

    def test_repeated_blend_retains_checks_but_constructs_once(self):
        checked = Mock(wraps=self.check)
        finite = Mock(wraps=self.scope["_finite"])
        self.scope["_weights"], self.scope["_finite"] = checked, finite
        cache = {}
        for _ in range(6):
            self.blend(self.transforms, {"lower": 0.4, "hand": 0.6},
                       _matrix_rows=self.transforms, _blend_cache=cache)
        self.assertEqual(checked.call_count, 6)
        self.assertEqual(finite.call_count, 6)
        self.assertEqual(MatrixStub.constructions, 1)

    def test_normalization_passes_and_order_have_distinct_exact_keys(self):
        raw = {"lower": 0.1281599137849305, "hand": 0.12181411510790636,
               "other": 0.7500359711071631}
        once = self.check(self.transforms, raw)
        twice = self.check(self.transforms, once)
        self.assertNotEqual(tuple(once.items()), tuple(twice.items()))
        cache = {}
        for weights in (raw, once, dict(reversed(tuple(raw.items())))):
            direct = self.blend(self.transforms, weights)
            for _ in range(2):
                cached = self.blend(self.transforms, weights, _matrix_rows=self.transforms,
                                    _blend_cache=cache)
                self.assertEqual(direct.rows, cached.rows)
            self.assertIn(tuple(self.check(self.transforms, weights).items()), cache)
        self.assertEqual(len(cache), 3)

    def test_returned_matrix_mutation_cannot_change_cached_value(self):
        weights, cache = {"lower": 0.4, "hand": 0.6}, {}
        expected = self.blend(self.transforms, weights)
        for value in (math.nan, math.inf, -93.0):
            current = self.blend(self.transforms, weights, _blend_cache=cache)
            self.assertEqual(current.rows, expected.rows)
            current[0][0] = value
        self.assertEqual(self.blend(self.transforms, weights, _blend_cache=cache).rows,
                         expected.rows)

    def test_previous_hit_does_not_bypass_invalid_weight_checks(self):
        cache = {}
        self.blend(self.transforms, {"lower": 0.4, "hand": 0.6}, _blend_cache=cache)
        for weights in ({"lower": math.nan}, {"lower": -0.1, "hand": 1.1},
                        {"lower": 0.2}, {"missing": 1.0}):
            with self.assertRaises(self.scope["TwistMathError"]):
                self.blend(self.transforms, weights, _blend_cache=cache)
        self.assertEqual(len(cache), 1)

    def test_nonfinite_blend_rejected_without_caching_and_weight_error_wins(self):
        transforms = {"lower": tuple(tuple(math.inf if row == column == 0 else value
                                            for column, value in enumerate(values))
                                     for row, values in enumerate(self.transforms["lower"]))}
        cache = {}
        for _ in range(2):
            with self.assertRaisesRegex(self.scope["TwistMathError"], "Blended deformation"):
                self.blend(transforms, {"lower": 1.0}, _blend_cache=cache)
        self.assertEqual(cache, {})
        with self.assertRaisesRegex(self.scope["TwistMathError"], "Bone weights"):
            self.blend(transforms, {"lower": math.nan}, _blend_cache=cache)

    def test_empty_weights_keep_identity_without_cache_or_finite_scan(self):
        self.scope["_finite"] = Mock(side_effect=AssertionError("Unexpected finite scan"))
        cache = {}
        result = self.blend({}, {}, _blend_cache=cache)
        self.assertEqual(result.rows, MatrixStub.Identity(4).rows)
        self.assertEqual(cache, {})

    def test_new_call_cache_uses_changed_current_rows(self):
        weights = {"lower": 0.4, "hand": 0.6}
        first = self.blend(self.transforms, weights, _blend_cache={})
        changed = {name: tuple(tuple(value + (0.31 if column == 3 and row < 3 else 0.0)
                                   for column, value in enumerate(values))
                             for row, values in enumerate(matrix))
                   for name, matrix in self.transforms.items()}
        second = self.blend(changed, weights, _matrix_rows=changed, _blend_cache={})
        self.assertNotEqual(first.rows, second.rows)
        self.assertEqual(second.rows, self.blend(changed, weights).rows)


if __name__ == "__main__":
    unittest.main()
