"""Fresh-source/mutation tests for scoped Basis and exact mirror dependencies.

These use collection doubles. Native mirror geometry/refusal parity is covered
separately by the Blender cache and real-model integration suites.
"""
from array import array
import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/forearm_twist_cache.py'
spec = importlib.util.spec_from_file_location('_scoped_basis_under_test', SOURCE)
cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cache)


class ID:
    def __init__(self, **values):
        self.__dict__.update(values)
        self.session_uid = id(self)

    def as_pointer(self):
        return id(self)


class Collection:
    def __init__(self, values, **fields):
        self.values, self.fields, self.reads = values, fields, 0

    def __len__(self):
        return len(self.values)

    def foreach_get(self, name, target):
        self.reads += 1
        rows = self.values if name == 'co' else self.fields[name]
        flat = [number for row in rows for number in row] if rows and isinstance(rows[0], (tuple, list)) else rows
        target[:] = array(target.typecode, flat)


def fixture():
    points = Collection([(0., -0., 1.), (2., 3., 4.)])
    basis = ID(data=points)
    mesh = ID(shape_keys=ID(reference_key=basis), vertices=Collection(points.values),
              edges=Collection([0], vertices=[(0, 1)]),
              loops=Collection([0, 1], vertex_index=[0, 1]),
              polygons=Collection([0], loop_start=[0], loop_total=[2]))
    matrix = tuple(tuple(float(row == col) for col in range(4)) for row in range(4))
    obj = ID(type='MESH', data=mesh, matrix_world=matrix)
    names = ('upper.L', 'lower.L', 'hand.L', 'upper.R', 'lower.R', 'hand.R', 'unrelated')
    bones = {name: NS(use_deform=True, head_local=(float(index),0.,0.), tail_local=(float(index),1.,0.))
             for index, name in enumerate(names)}
    arm = ID(type='ARMATURE', data=ID(bones=bones), matrix_world=matrix)
    return obj, arm, {'chain':list(names[:3])}, {'chain':list(names[3:6])}, points


class ScopedBasisTests(unittest.TestCase):
    def setUp(self):
        cache.clear()

    def test_fresh_without_scope_and_mutable_result_cannot_poison_proof(self):
        obj, _, _, _, points = fixture()
        first = cache.basis_coordinates(obj)
        first[0] = 999.
        self.assertEqual(cache.basis_coordinates(obj)[0], 0.)
        self.assertEqual(points.reads, 2)
        points.values[0] = (.25, -0., 1.)
        self.assertEqual(cache.basis_coordinates(obj)[0], .25)

    def test_one_native_read_shared_with_private_immutable_bytes(self):
        obj, _, _, _, points = fixture()
        with cache.read_scope():
            proof = cache.basis_bytes(obj)
            first = cache.basis_coordinates(obj)
            self.assertEqual(first.tobytes(), proof)
            first[0] = 999.
            self.assertEqual(cache.basis_coordinates(obj).tobytes(), proof)
            self.assertEqual(points.reads, 1)
        with cache.read_scope():
            self.assertEqual(cache.basis_bytes(obj), proof)
        self.assertEqual(points.reads, 2)
        self.assertNotEqual(proof, array('f', [0.,0.,1.,2.,3.,4.]).tobytes())

    def test_reference_block_replacement_invalidates_within_scope(self):
        obj, _, _, _, points = fixture()
        with cache.read_scope():
            old = cache.basis_bytes(obj)
            replacement = Collection([(.5, -0., 1.), (2.,3.,4.)])
            obj.data.shape_keys.reference_key = ID(data=replacement)
            self.assertNotEqual(cache.basis_bytes(obj), old)
            self.assertEqual(replacement.reads, 1)
        self.assertEqual(points.reads, 1)

    def test_nested_exception_restores_scope_and_fresh_next_call(self):
        obj, _, _, _, points = fixture()
        with cache.read_scope():
            proof = cache.basis_bytes(obj)
            with self.assertRaisesRegex(ValueError, 'nested'):
                with cache.read_scope():
                    self.assertEqual(cache.basis_bytes(obj), proof)
                    raise ValueError('nested')
            self.assertEqual(cache.basis_bytes(obj), proof)
            self.assertEqual(points.reads, 2)
        points.values[0] = (.75, -0., 1.)
        self.assertNotEqual(cache.basis_bytes(obj), proof)

    def test_mesh_vertices_without_shape_keys_are_fresh(self):
        obj, _, _, _, _ = fixture()
        obj.data.shape_keys = None
        first = cache.basis_bytes(obj)
        obj.data.vertices.values[0] = (.125,0.,1.)
        self.assertNotEqual(cache.basis_bytes(obj), first)

    def test_only_actual_chain_geometry_invalidates_mirror(self):
        obj, arm, left, right, _ = fixture()
        calls = []
        def calculate(*args):
            calls.append(1)
            return [(0,0)]
        def invoke():
            with cache.read_scope():
                return cache.mirror_pairs(obj,arm,left,right,calculate)
        self.assertEqual(invoke(), [(0,0)])
        arm.data.bones['unrelated'].head_local = (99.,99.,99.)
        invoke()
        self.assertEqual(len(calls), 1)
        arm.data.bones['lower.R'].head_local = (99.,0.,0.)
        invoke()
        self.assertEqual(len(calls), 2)
        arm.data.bones['hand.L'].use_deform = False
        invoke()
        self.assertEqual(len(calls), 3)
        left['chain'][0],left['chain'][1] = left['chain'][1],left['chain'][0]
        invoke()
        self.assertEqual(len(calls), 4)

    def test_same_count_basis_edit_outside_support_invalidates_mirror(self):
        obj, arm, left, right, points = fixture()
        calls = []
        def calculate(*args):
            calls.append(1)
            return [(0,0)]
        cache.mirror_pairs(obj,arm,left,right,calculate)
        points.values[1] = (9.,3.,4.)
        cache.mirror_pairs(obj,arm,left,right,calculate)
        self.assertEqual(len(calls), 2)

    def test_malformed_or_missing_chain_delegates_original_error_each_call(self):
        obj, arm, left, right, _ = fixture()
        calls = []
        def calculate(*args):
            calls.append(1)
            raise ValueError('original calculator validation order')
        for bad in [{'chain':[[],1,2]}, {'chain':['missing','lower.L','hand.L']}, None]:
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, 'original calculator validation order'):
                    cache.mirror_pairs(obj,arm,bad,right,calculate)
        self.assertEqual(len(calls), 6)


if __name__ == '__main__':
    unittest.main()
