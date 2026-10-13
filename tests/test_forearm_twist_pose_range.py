"""Portable failure-path tests of actual Forearm Twist functions, not native QA."""
import ast
import copy
import json
import math
from array import array
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/forearm_twist.py'
RECORD_KEY = 'character_designer_forearm_twist_v1'


def product():
    names = {'ForearmTwistError', 'ForearmTwistPoseRangeError',
             'ForearmTwistRecaptureRequired', '_target_only_change',
             '_apply_runtime_records', '_calculate_records', 'update_runtime'}
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
             and node.name in names]
    namespace = {'array': array, 'RECORD_KEY': RECORD_KEY, 'MAX_ANGLE': math.radians(120),
                 '_CACHE': {}, '_OUTPUT_CACHE': {}, '_KEY_REFERENCES': {}, '_ERRORS': {},
                 '_BUSY': False}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


class Coordinates:
    def __init__(self, values=(1.0, 2.0, 3.0)):
        self.values = list(values)

    def __len__(self):
        return len(self.values) // 3

    def foreach_get(self, name, destination):
        destination[:] = array('f', self.values)

    def foreach_set(self, name, values):
        self.values[:] = values


class Mesh(dict):
    def __init__(self, records):
        super().__init__({RECORD_KEY: json.dumps(records, indent=2)})
        self.name, self.type, self.mode = 'Cosha', 'MESH', 'OBJECT'
        self.active_shape_key_index = 7
        self.removed, self.updates = [], []
        basis = NS(data=Coordinates((0.0, 0.0, 0.0)))
        self.keys = {side: NS(name=record['key'], data=Coordinates(), value=.25,
                             mute=False, relative_key=basis, vertex_group='')
                     for side, record in records.items()}
        self.data = NS(shape_keys=NS(reference_key=basis,
                                    update_tag=lambda **kwargs: self.updates.append(('keys', kwargs))),
                       update=lambda: self.updates.append(('mesh', {})))
        self.matrix_world = Matrix()

    def as_pointer(self):
        return 42

    def shape_key_remove(self, key):
        self.removed.append(key)

    def evaluated_get(self, depsgraph):
        return self


class Matrix:
    def __iter__(self):
        return iter(((1.0, 0.0), (0.0, 1.0)))

    def inverted(self):
        return self

    def __matmul__(self, other):
        return other


class Vector(tuple):
    def __sub__(self, other):
        return Vector(a - b for a, b in zip(self, other))

    def normalized(self):
        return self


def records():
    return {side: {'key': 'Twist.' + side, 'target': 'hand.' + side,
                   'rest': 'rest.' + side, 'armature': 'Rig',
                   'chain': ['upper.' + side, 'lower.' + side, 'hand.' + side],
                   'vertices': [0], 'positions': [.5], 'enabled': True,
                   'range_start': 0, 'range_end': 1,
                   'rings': [{'side': side, 'position': 0, 'ratio': 0},
                             {'side': side, 'position': 1, 'ratio': 1}]}
            for side in ('L', 'R')}


class RuntimeRecordTransactionTests(unittest.TestCase):
    def fixture(self, failure_name='ForearmTwistPoseRangeError'):
        ns, old = product(), records()
        obj, updated = Mesh(old), copy.deepcopy(old)
        updated['L']['target'] = 'CTRL_hand_IK.L'
        ns['_records'] = lambda mesh: json.loads(mesh[RECORD_KEY])
        ns['_managed_key'] = lambda mesh, side, record, **kwargs: mesh.keys.get(side)
        ns['_write_records'] = lambda mesh, value: mesh.__setitem__(RECORD_KEY, json.dumps(value))
        ns['_new_output_key'] = lambda mesh, record: NS(name=record['key'], mute=False)
        old_ref, other_ref = object(), object()
        ns['_KEY_REFERENCES'].update({(42, 'L'): old_ref, (99, 'L'): other_ref})
        ns['_CACHE'][42], ns['_OUTPUT_CACHE'][42] = 'stale', 'stale output'
        error = ns[failure_name]('visible pose or structural error')

        def fail(mesh, depsgraph):
            for key in mesh.keys.values():
                key.data.values[:] = [9.0, 9.0, 9.0]
                key.value, key.mute = 1.0, True
            mesh.active_shape_key_index = 123
            ns['_KEY_REFERENCES'][(42, 'L')] = object()
            raise error

        ns['_calculate_records'] = fail
        return ns, obj, updated, error, {(42, 'L'): old_ref, (99, 'L'): other_ref}

    def run_failure(self, mutate=None, failure_name='ForearmTwistPoseRangeError', keep=False):
        ns, obj, updated, error, references = self.fixture(failure_name)
        original_json = obj[RECORD_KEY]
        if mutate:
            mutate(updated)
        with self.assertRaises(type(error)) as raised:
            ns['_apply_runtime_records'](obj, updated, object())
        self.assertIs(raised.exception, error)
        if keep:
            self.assertEqual(json.loads(obj[RECORD_KEY]), updated)
        else:
            self.assertEqual(obj[RECORD_KEY], original_json)
        for key in obj.keys.values():
            self.assertEqual(key.data.values, [1.0, 2.0, 3.0])
            self.assertEqual((key.value, key.mute), (.25, False))
        self.assertEqual(obj.active_shape_key_index, 7)
        self.assertEqual(ns['_KEY_REFERENCES'], references)
        self.assertNotIn(42, ns['_CACHE'])
        self.assertNotIn(42, ns['_OUTPUT_CACHE'])
        return obj

    def test_valid_target_only_pose_error_retains_metadata_but_restores_output(self):
        self.run_failure(keep=True)

    def test_non_target_changes_roll_back_exact_json(self):
        for field, value in [('rest', 'changed'), ('vertices', [1]), ('enabled', False),
                             ('range_start', 1), ('rings', [])]:
            with self.subTest(field=field):
                self.run_failure(lambda data: data['L'].__setitem__(field, value))

    def test_new_record_field_is_not_target_only(self):
        self.run_failure(lambda data: data['L'].__setitem__('paired', True))

    def test_removed_record_field_is_not_target_only(self):
        self.run_failure(lambda data: data['L'].pop('range_end'))

    def test_missing_side_rolls_back(self):
        self.run_failure(lambda data: data.pop('R'))

    def test_added_side_removes_new_key_and_rolls_back(self):
        obj = self.run_failure(lambda data: data.__setitem__('new', dict(data['R'])))
        self.assertEqual(len(obj.removed), 1)

    def test_changed_key_identity_rolls_back(self):
        self.run_failure(lambda data: data['L'].__setitem__('key', 'Replacement key'))

    def test_generic_failure_never_retains_target(self):
        self.run_failure(failure_name='ForearmTwistError')

    def test_runtime_keeps_error_visible_and_mutes_restored_keys(self):
        ns, obj, updated, error, _references = self.fixture()
        ns['_calculate_object'] = lambda mesh, graph: ns['_apply_runtime_records'](mesh, updated, graph)
        ns['update_runtime'](NS(objects=[obj]), object())
        self.assertEqual(ns['_ERRORS'][obj.name], str(error))
        self.assertEqual(json.loads(obj[RECORD_KEY]), updated)
        self.assertTrue(all(key.mute for key in obj.keys.values()))
        self.assertTrue(all(key.data.values == [1.0, 2.0, 3.0] for key in obj.keys.values()))
        self.assertFalse(ns['_BUSY'])


class PoseRangeClassificationTests(unittest.TestCase):
    def fixture(self, invalid=None):
        ns, data = product(), records()
        obj, visited = Mesh(data), []
        bones = {name: NS(matrix_local=Matrix(), head_local=Vector((0, 0, 0)),
                          tail_local=Vector((0, 1, 0)))
                 for record in data.values() for name in record['chain']}
        arm = NS(name='Rig', data=NS(bones=bones), matrix_world=Matrix(),
                 pose=NS(bones={name: NS(matrix=Matrix()) for name in bones}))
        arm.evaluated_get = lambda depsgraph: arm
        ns.update(_records=lambda mesh: json.loads(mesh[RECORD_KEY]), _check_mesh=lambda mesh: arm,
                  _topology=lambda mesh: 'valid topology',
                  _current_topology_record=lambda mesh, record, **kwargs: record,
                  _managed_key=lambda mesh, side, record: mesh.keys[side],
                  _input_mix=lambda *args: {0: Vector((0, 0, 0))},
                  _weights=lambda *args: {0: {}},
                  twist_angle=lambda *args: math.radians(121))

        def validate(stage, side):
            visited.append((stage, side))
            if invalid == (stage, side):
                raise ns['ForearmTwistError']('invalid ' + stage + ' ' + side)

        def rest(armature, chain):
            side = chain[0][-1]
            validate('rest', side)
            return 'rest.' + side

        def knots(rings):
            validate('profile', rings[0]['side'])
            return [(0., 0.), (1., 1.)]

        def record_range(record):
            validate('range', record['rings'][0]['side'])
            return 0, 1

        def compile_sampler(rings, **kwargs):
            validate('sampler', rings[0]['side'])
            return lambda position: (0., 0.)

        ns['_rest_signature'] = rest
        ns['profile'] = NS(profile_knots=knots, record_range=record_range, compile_sampler=compile_sampler)
        return ns, obj, visited

    def test_both_sides_validate_before_typed_pose_failure_without_output_commit(self):
        ns, obj, visited = self.fixture()
        with self.assertRaises(ns['ForearmTwistPoseRangeError']):
            ns['_calculate_records'](obj, object())
        self.assertEqual(visited, [(stage, side) for side in ('L', 'R')
                                   for stage in ('rest', 'profile', 'range', 'sampler')])
        self.assertEqual(obj.updates, [])
        self.assertTrue(all(key.data.values == [1.0, 2.0, 3.0] for key in obj.keys.values()))
        self.assertTrue(all((key.value, key.mute) == (.25, False) for key in obj.keys.values()))

    def test_later_structural_failure_is_not_classified_as_only_pose_range(self):
        for stage in ('rest', 'profile', 'range', 'sampler'):
            with self.subTest(stage=stage):
                ns, obj, _visited = self.fixture((stage, 'R'))
                with self.assertRaises(ns['ForearmTwistError']) as raised:
                    ns['_calculate_records'](obj, object())
                self.assertNotIsInstance(raised.exception, ns['ForearmTwistPoseRangeError'])
                self.assertIn('invalid ' + stage + ' R', str(raised.exception))
                self.assertEqual(obj.updates, [])

    def test_later_output_failure_also_prevents_target_retention(self):
        ns, obj, visited = self.fixture()
        angles = iter((math.radians(121), 0.))
        ns['twist_angle'] = lambda *args: next(angles)
        compile_sampler = ns['profile'].compile_sampler

        def nonzero_sampler(rings, **kwargs):
            compile_sampler(rings, **kwargs)
            return lambda position: (.5, 1.)

        def invalid_inverse(*args, **kwargs):
            raise ns['ForearmTwistError']('invalid inverse skinning')

        ns['profile'].compile_sampler = nonzero_sampler
        ns['corrected_vertex'] = invalid_inverse
        with self.assertRaises(ns['ForearmTwistError']) as raised:
            ns['_calculate_records'](obj, object())
        self.assertNotIsInstance(raised.exception, ns['ForearmTwistPoseRangeError'])
        self.assertEqual(str(raised.exception), 'invalid inverse skinning')
        self.assertIn(('sampler', 'R'), visited)
        self.assertEqual(obj.updates, [])

    def test_disabled_side_keeps_existing_profile_skip_but_checks_rest(self):
        ns, obj, visited = self.fixture()
        data = json.loads(obj[RECORD_KEY])
        data['R']['enabled'], data['R']['rings'] = False, []
        obj[RECORD_KEY] = json.dumps(data)
        with self.assertRaises(ns['ForearmTwistPoseRangeError']):
            ns['_calculate_records'](obj, object())
        self.assertIn(('rest', 'R'), visited)
        self.assertNotIn(('profile', 'R'), visited)
        self.assertEqual(obj.updates, [])


if __name__ == '__main__':
    unittest.main()
