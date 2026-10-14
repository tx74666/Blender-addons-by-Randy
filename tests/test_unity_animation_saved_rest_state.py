"""Immutable saved JSON proof preserves the eager native comparison contract."""

import ast
import copy
from dataclasses import dataclass, FrozenInstanceError
import math
from pathlib import Path
import sys
from types import MappingProxyType, ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer' / 'unity_animation.py'


def load_contract():
    names = {'_SavedRestState', '_compile_saved_rest_state', '_matches_saved_rest_state',
             '_matches_rest_state', '_same_rest_bone'}
    constants = {'_SAVED_REST_FIELDS', '_SAVED_REST_VECTOR_LENGTHS'}
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'), filename=str(SOURCE))
    nodes = [node for node in tree.body if
             isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
             or isinstance(node, ast.Assign) and any(isinstance(item, ast.Name) and item.id in constants
                                                    for item in node.targets)]
    module = ModuleType('_saved_rest_native_contract')
    module.__dict__.update(dataclass=dataclass, MappingProxyType=MappingProxyType, math=math)
    sys.modules[module.__name__] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), module.__dict__)
    return module


def rest():
    return {'bone': {'parent': None, 'head': [1.0, 0.0, 0.0], 'tail': [1.0, 1.0, 0.0],
                     'matrix': [float(row == column) for row in range(4) for column in range(4)],
                     'use_connect': True, 'use_deform': True, 'use_inherit_rotation': True,
                     'inherit_scale': 'FULL', 'use_local_location': False,
                     'use_relative_parent': False}}


class SavedRestStateTests(unittest.TestCase):
    def setUp(self):
        self.ua = load_contract()
        self.current = rest()
        self.ua._rest_state = Mock(side_effect=lambda _data: copy.deepcopy(self.current))
        self.data = object()

    def compare(self, saved):
        proof = self.ua._compile_saved_rest_state(saved)
        self.assertIsNotNone(proof)
        ordinary = self.ua._matches_rest_state(self.data, saved)
        compiled = self.ua._matches_rest_state(self.data, proof)
        self.assertEqual(compiled, ordinary)
        return compiled

    def test_ordinary_snapshot_detaches_and_has_no_native_read(self):
        saved = rest()
        proof = self.ua._compile_saved_rest_state(saved)
        self.ua._rest_state.assert_not_called()
        saved['bone']['head'][0] = 99.0
        saved['bone']['inherit_scale'] = 'NONE'
        self.assertTrue(self.ua._matches_rest_state(self.data, proof))
        self.assertFalse(self.ua._matches_rest_state(self.data, saved))
        with self.assertRaises(TypeError):
            proof.rows['other'] = {}
        with self.assertRaises(TypeError):
            proof.rows['bone']['head'] = ()
        with self.assertRaises(TypeError):
            proof.rows['bone']['head'][0] = 2.0
        with self.assertRaises(FrozenInstanceError):
            proof.names = frozenset()

    def test_each_match_reads_current_snapshot_eagerly(self):
        proof = self.ua._compile_saved_rest_state(rest())
        self.assertTrue(self.ua._matches_rest_state(self.data, proof))
        self.current['bone']['tail'][1] += .01
        self.assertFalse(self.ua._matches_rest_state(self.data, proof))
        self.assertEqual(self.ua._rest_state.call_count, 2)
        self.ua._rest_state.side_effect = RuntimeError('native read failed')
        with self.assertRaisesRegex(RuntimeError, 'native read failed'):
            self.ua._matches_rest_state(self.data, proof)

    def test_bool_and_int_numeric_values_keep_legacy_equality(self):
        saved = rest()
        saved['bone']['head'][0] = True
        saved['bone']['matrix'][0] = True
        saved['bone']['use_connect'] = 1
        saved['bone']['use_local_location'] = 0.0
        self.assertTrue(self.compare(saved))

    def test_scalar_comparisons_keep_legacy_value_semantics(self):
        for field in ('parent', 'use_connect', 'use_deform', 'use_inherit_rotation',
                      'inherit_scale', 'use_local_location', 'use_relative_parent'):
            for value in (None, True, False, 0, 1, 1.25, 'FULL', 'other'):
                with self.subTest(field=field, value=value):
                    saved = rest()
                    saved['bone'][field] = value
                    self.compare(saved)

    def test_numeric_tolerance_and_field_order_match_ordinary_path(self):
        for field in ('head', 'tail', 'matrix'):
            for delta in (0.0, 5e-7, 1e-6, 2e-6):
                with self.subTest(field=field, delta=delta):
                    saved = rest()
                    saved['bone'][field][1] += delta
                    self.compare(saved)

    def test_inventory_and_field_changes_are_checked_fresh(self):
        proof = self.ua._compile_saved_rest_state(rest())
        self.current['new'] = copy.deepcopy(self.current['bone'])
        self.assertFalse(self.ua._matches_rest_state(self.data, proof))
        del self.current['new']
        self.current['bone']['extra'] = 3
        self.assertFalse(self.ua._matches_rest_state(self.data, proof))

    def test_empty_native_inventory_keeps_ordinary_result(self):
        self.current = {}
        self.assertTrue(self.compare({}))

    def test_special_reference_values_and_mutable_scalars_fall_back(self):
        for field in ('parent', 'use_connect', 'head', 'tail', 'matrix'):
            for value in ({'$id': 'native_reference'}, ['mutable'], {'mutable': True}):
                with self.subTest(field=field, value=value):
                    saved = rest()
                    saved['bone'][field] = value
                    self.assertIsNone(self.ua._compile_saved_rest_state(saved))
        self.ua._rest_state.assert_not_called()

    def test_schema_or_numeric_malformed_values_fall_back_without_exception(self):
        variants = (None, [], {'bone': None}, {'bone': {'head': []}})
        for saved in variants:
            with self.subTest(saved=saved):
                self.assertIsNone(self.ua._compile_saved_rest_state(saved))
        for vector in ([], [1, 2], [1, 2, 'bad'], [1, 2, float('nan')],
                       [1, 2, float('inf')], [1, 2, 10 ** 1000]):
            saved = rest()
            saved['bone']['head'] = vector
            self.assertIsNone(self.ua._compile_saved_rest_state(saved))
        self.ua._rest_state.assert_not_called()

    def test_nonfinite_scalar_falls_back_to_unchanged_dictionary_path(self):
        saved = rest()
        saved['bone']['use_connect'] = float('nan')
        self.assertIsNone(self.ua._compile_saved_rest_state(saved))
        self.assertFalse(self.ua._matches_rest_state(self.data, saved))

    def test_huge_numeric_fallback_preserves_later_legacy_overflow(self):
        saved = rest()
        saved['bone']['head'][0] = 10 ** 1000
        self.assertIsNone(self.ua._compile_saved_rest_state(saved))
        self.ua._rest_state.assert_not_called()
        with self.assertRaises(OverflowError):
            self.ua._matches_rest_state(self.data, saved)
        self.ua._rest_state.assert_called_once_with(self.data)

    def test_native_nonfinite_arithmetic_keeps_legacy_comparison_behavior(self):
        saved = rest()
        self.current['bone']['head'][0] = float('nan')
        self.compare(saved)


class Vector:
    def __init__(self, values):
        self.values = values

    def __sub__(self, other):
        return SimpleNamespace(length=math.sqrt(sum((x-y) ** 2 for x, y in zip(self.values, other.values))))


class Bone:
    def __init__(self):
        self.parent = None
        self.use_deform = self.use_inherit_rotation = self.use_local_location = True
        self.use_relative_parent = False
        self.inherit_scale = 'FULL'
        self.head_local, self.tail_local = Vector((0, 0, 0)), Vector((0, 1, 0))
        self.matrix_reads = 0
        self.matrix = tuple(tuple(float(row == column) for column in range(4)) for row in range(4))

    @property
    def matrix_local(self):
        self.matrix_reads += 1
        return self.matrix


class NativeMatrixReadTests(unittest.TestCase):
    def setUp(self):
        self.ua = load_contract()
        self.original, self.motion = Bone(), Bone()

    def test_same_bone_reads_two_current_matrices_once(self):
        self.assertTrue(self.ua._same_rest_bone(self.original, self.motion))
        self.assertEqual((self.original.matrix_reads, self.motion.matrix_reads), (1, 1))

    def test_parent_and_geometry_guards_precede_matrix_reads(self):
        self.motion.parent = SimpleNamespace(name='new parent')
        self.assertFalse(self.ua._same_rest_bone(self.original, self.motion))
        self.assertEqual((self.original.matrix_reads, self.motion.matrix_reads), (0, 0))
        self.motion.parent = None
        self.motion.tail_local = Vector((.01, 1, 0))
        self.assertFalse(self.ua._same_rest_bone(self.original, self.motion))
        self.assertEqual((self.original.matrix_reads, self.motion.matrix_reads), (0, 0))


if __name__ == '__main__':
    unittest.main()
