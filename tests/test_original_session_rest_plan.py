"""Pure differential contracts against the frozen 0.77.82 Original validator.

Only exact AST definitions are loaded; no Blender/add-on import or native scene
is needed. Native acceptance, live RNA and playback timing remain separate.
"""
import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, dataclass
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'dist/character_designer-0.77.82.zip'
ARCHIVE_SHA = '6b0b919487cccdba82608402d932a3e6b848e0f27a7a13c7de614b96fa83b703'
CURRENT = ROOT / 'addons/character_designer/body_original_mode.py'
FUNCTIONS = {'active', '_valid_rest_entry', '_compile_session_rest_plan',
             '_materialize_session_rest_plan', '_validate_session_rest'}
CONSTANTS = {'SESSION', '_REST_FIELDS', '_REST_RELATIONS',
             '_REST_LENGTH_EPSILON', '_REST_MATRIX_EPSILON'}


class NativeReads:
    def __init__(self):
        self.current = {}
        self.reads = 0

    def native_rest(self, _rig):
        self.reads += 1
        return deepcopy(self.current)


def definitions(source, name, poses):
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == '_SessionRestPlan':
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id in CONSTANTS for target in node.targets):
            selected.append(node)
    module = types.ModuleType(name)
    module.__dict__.update(json=json, math=math, lru_cache=lru_cache,
                           dataclass=dataclass, poses=poses)
    sys.modules[name] = module
    exec(compile(ast.Module(body=selected, type_ignores=[]), name, 'exec'), module.__dict__)
    return module


class Bones:
    def __init__(self, names):
        self.names = list(names)

    def keys(self):
        return self.names[:]


class Rig(dict):
    type = 'ARMATURE'

    def __init__(self, raw, names):
        super().__init__(character_designer_body_original_mode_v1=raw)
        self.data = types.SimpleNamespace(bones=Bones(names))


def rest():
    return {name: {'matrix': [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
                             [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
                   'length': 1.0, 'parent': None if index == 0 else 'Root',
                   'connected': False, 'inherit_scale': 'FULL',
                   'inherit_rotation': True, 'local_location': True}
            for index, name in enumerate(('Root', 'Arm'))}


class OriginalSessionRestPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = ARCHIVE.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA:
            raise AssertionError('Frozen .82 reference archive pin changed.')
        with zipfile.ZipFile(ARCHIVE) as archive:
            member = next(name for name in archive.namelist() if name.endswith('/body_original_mode.py'))
            cls.reference_source = archive.read(member).decode('utf-8-sig')
        cls.current_source = CURRENT.read_text(encoding='utf-8-sig')

    def setUp(self):
        self.poses = NativeReads()
        self.poses.current = rest()
        self.saved = {'version': 1, 'bones': ['Arm', 'Control', 'Root'], 'rest': rest()}
        self.rig = Rig(json.dumps(self.saved), self.saved['bones'])
        self.reference = definitions(self.reference_source, '_original_rest_reference', self.poses)
        self.candidate = definitions(self.current_source, '_original_rest_candidate', self.poses)
        self.candidate._compile_session_rest_plan.cache_clear()

    def outcome(self, function, *, saved=None, current=None):
        try:
            return ('return', function(self.rig, saved=deepcopy(saved), current=deepcopy(current)))
        except BaseException as error:
            return ('error', type(error).__name__, str(error))

    def equal(self, *, saved=None, current=None):
        expected = self.outcome(self.reference._validate_session_rest, saved=saved, current=current)
        actual = self.outcome(self.candidate._validate_session_rest, saved=saved, current=current)
        self.assertEqual(actual, expected)
        return actual

    def prime(self):
        self.assertEqual(self.candidate._validate_session_rest(self.rig), self.poses.current)

    def test_warm_plan_still_reads_native_rest_every_call(self):
        loads = json.loads
        with patch.object(json, 'loads', wraps=loads) as decoder:
            for _ in range(10):
                self.prime()
            self.assertEqual(decoder.call_count, 1)
        self.assertEqual(self.poses.reads, 10)
        self.assertEqual(self.candidate._compile_session_rest_plan.cache_info().hits, 9)

    def test_plan_is_recursively_immutable_and_materialized_copies_are_owned(self):
        plan = self.candidate._compile_session_rest_plan(self.rig[self.candidate.SESSION])
        def immutable(value):
            self.assertNotIsInstance(value, (dict, list, set))
            if isinstance(value, tuple):
                for entry in value:
                    immutable(entry)
        immutable(plan.bones)
        immutable(plan.rest)
        with self.assertRaises(FrozenInstanceError):
            plan.bones = ()
        first = self.candidate._materialize_session_rest_plan(plan)
        first['rest']['Root']['matrix'][0][0] = float('nan')
        first['bones'].clear()
        self.assertEqual(self.candidate._materialize_session_rest_plan(plan), self.saved)
        self.prime()

    def test_live_geometry_changes_are_rejected_after_warm_plan(self):
        self.prime()
        for field in ('matrix', 'length'):
            with self.subTest(field=field):
                self.poses.current = rest()
                if field == 'matrix':
                    self.poses.current['Arm']['matrix'][0][3] = .1
                else:
                    self.poses.current['Arm']['length'] = 1.1
                self.assertEqual(self.equal()[0], 'error')

    def test_live_bone_inventory_changes_are_rejected_after_warm_plan(self):
        self.prime()
        for names in (['Arm', 'Root'], ['Arm', 'Control', 'Root', 'New'], ['Arm', 'Renamed', 'Root']):
            with self.subTest(names=names):
                self.rig.data.bones.names = names
                result = self.equal()
                self.assertEqual(result[0], 'error')
                self.assertIn('bone inventory changed', result[-1])

    def test_native_ownership_changes_are_rejected_after_warm_plan(self):
        self.prime()
        del self.poses.current['Arm']
        result = self.equal()
        self.assertIn('native bone ownership changed', result[-1])

    def test_live_nonfinite_or_malformed_data_still_checks_current(self):
        self.prime()
        for value in (float('nan'), float('inf'), True, 'bad'):
            with self.subTest(value=value):
                self.poses.current = rest()
                self.poses.current['Arm']['matrix'][0][0] = value
                self.equal()

    def test_new_exact_raw_text_invalidates_plan(self):
        self.prime()
        changed = deepcopy(self.saved)
        changed['rest']['Arm']['length'] = 2.0
        self.rig[self.candidate.SESSION] = json.dumps(changed)
        self.assertEqual(self.equal()[0], 'error')
        self.poses.current['Arm']['length'] = 2.0
        self.assertEqual(self.equal()[0], 'return')
        changed['rest']['Root']['matrix'][0][0] = float('nan')
        self.rig[self.candidate.SESSION] = json.dumps(changed)
        self.assertEqual(self.equal()[0], 'error')

    def test_explicit_saved_mutation_never_uses_raw_plan(self):
        self.prime()
        explicit = deepcopy(self.saved)
        self.assertEqual(self.equal(saved=explicit)[0], 'return')
        explicit['rest']['Arm']['matrix'][0][0] = float('nan')
        self.assertEqual(self.equal(saved=explicit)[0], 'error')
        self.assertEqual(self.candidate._compile_session_rest_plan.cache_info().hits, 0)

    def test_explicit_current_keeps_full_validation_and_no_native_read(self):
        self.prime()
        before = self.poses.reads
        explicit = rest()
        self.assertEqual(self.equal(current=explicit)[0], 'return')
        explicit['Arm']['matrix'][0][0] = True
        self.assertEqual(self.equal(current=explicit)[0], 'error')
        self.assertEqual(self.poses.reads, before)
        self.assertEqual(self.candidate._compile_session_rest_plan.cache_info().hits, 0)

    def test_saved_schema_and_type_differential(self):
        cases = []
        for field, value in [('length', -1), ('length', True), ('length', 10 ** 1000),
                             ('parent', 0), ('inherit_scale', ''), ('connected', 0),
                             ('matrix', []), ('matrix', [[0] * 3] * 4)]:
            saved = deepcopy(self.saved)
            saved['rest']['Arm'][field] = value
            cases.append(saved)
        for scalar in (float('nan'), float('inf'), True, '0'):
            saved = deepcopy(self.saved)
            saved['rest']['Arm']['matrix'][0][0] = scalar
            cases.append(saved)
        for saved in cases:
            with self.subTest(saved=saved):
                self.rig[self.candidate.SESSION] = json.dumps(saved)
                self.assertEqual(self.equal()[0], 'error')

    def test_mixed_failures_keep_original_error_order(self):
        bad = deepcopy(self.saved)
        bad['rest']['Arm']['length'] = float('nan')
        self.rig[self.candidate.SESSION] = json.dumps(bad)
        self.rig.data.bones.names.append('Unexpected')
        result = self.equal()
        self.assertIn('bone inventory changed', result[-1])
        bad['version'] = True
        self.rig[self.candidate.SESSION] = json.dumps(bad)
        self.assertIn('schema is unsupported', self.equal()[-1])

    def test_numeric_thresholds_and_relation_order_match_reference(self):
        self.prime()
        for field, values in [('matrix', (0.0, 1e-7, 2e-6, 2.00001e-6, -2.00001e-6)),
                              ('length', (0.0, 1e-7, 1e-6, 1.00001e-6))]:
            for value in values:
                with self.subTest(field=field, value=value):
                    self.poses.current = rest()
                    if field == 'matrix':
                        self.poses.current['Arm']['matrix'][0][3] = value
                    else:
                        self.poses.current['Arm']['length'] += value
                    self.equal()
        for field, value in [('parent', None), ('connected', True), ('inherit_scale', 'NONE'),
                             ('inherit_rotation', False), ('local_location', False)]:
            with self.subTest(field=field):
                self.poses.current = rest()
                self.poses.current['Arm'][field] = value
                self.assertEqual(self.equal()[0], 'error')

    def test_signed_zero_survives_materialization_without_rejecting_equal_native(self):
        self.saved['rest']['Root']['matrix'][0][1] = -0.0
        self.rig[self.candidate.SESSION] = json.dumps(self.saved)
        self.assertEqual(self.equal()[0], 'return')
        plan = self.candidate._compile_session_rest_plan(self.rig[self.candidate.SESSION])
        copy = self.candidate._materialize_session_rest_plan(plan)
        self.assertEqual(copy['rest']['Root']['matrix'][0][1].hex(), '-0x0.0p+0')

    def test_explicit_tuple_rows_retain_old_supported_semantics(self):
        saved, current = deepcopy(self.saved), rest()
        for entry in saved['rest'].values():
            entry['matrix'] = tuple(tuple(row) for row in entry['matrix'])
        self.assertEqual(self.equal(saved=saved, current=current)[0], 'return')
        self.assertEqual(self.candidate._compile_session_rest_plan.cache_info().misses, 0)

    def test_malformed_or_nonstring_raw_uses_original_exception_mapping(self):
        for raw in ('{', '[]', '{}', 'null', 1, [], json.dumps(self.saved).encode('utf-8')):
            with self.subTest(raw=raw):
                self.rig[self.candidate.SESSION] = raw
                self.equal()


if __name__ == '__main__':
    unittest.main(verbosity=2)
