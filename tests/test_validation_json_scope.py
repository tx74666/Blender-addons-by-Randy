"""Scoped decoding contracts and actual read-only getter integration, no bpy."""
import ast
import importlib.util
import json
from pathlib import Path
from queue import Queue
from threading import Thread
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer'


def load_scope():
    spec = importlib.util.spec_from_file_location('_validation_json_under_test', SOURCE / 'validation_json.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def function(file, name, namespace):
    tree = ast.parse((SOURCE / file).read_text(encoding='utf-8'))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, 'exec'), namespace)
    return namespace[name]


class ValidationJsonScopeTests(unittest.TestCase):
    def setUp(self):
        self.scope = load_scope()
        self.raw = '{"outer":{"values":[1,2]},"flag":true}'

    def test_ordinary_reads_are_fresh_and_mutations_do_not_escape(self):
        first = self.scope.decode(self.raw)
        first['outer']['values'].append(9)
        self.assertEqual(self.scope.decode(self.raw), json.loads(self.raw))
        self.assertIsNot(first, self.scope.decode(self.raw))

    def test_exact_current_raw_is_shared_only_inside_one_call(self):
        loads = json.loads
        with patch.object(json, 'loads', wraps=loads) as decoder:
            with self.scope.read_scope():
                first = self.scope.decode(self.raw)
                self.assertIs(self.scope.decode(self.raw), first)
                changed = self.raw.replace('[1,2]', '[3,4]')
                self.assertEqual(self.scope.decode(changed)['outer']['values'], [3, 4])
                self.assertEqual(decoder.call_count, 2)
            self.assertIsNot(self.scope.decode(self.raw), first)
            self.assertEqual(decoder.call_count, 3)

    def test_scope_exit_clears_cache_even_when_returned_record_remains_alive(self):
        with self.scope.read_scope():
            first = self.scope.decode(self.raw)
            cache = self.scope._ACTIVE.get()
        self.assertEqual(cache, {})
        self.assertIsNone(self.scope._ACTIVE.get())
        first['outer']['values'].clear()
        with self.scope.read_scope():
            self.assertEqual(self.scope.decode(self.raw)['outer']['values'], [1, 2])

    def test_decode_errors_are_never_cached_and_keep_standard_type_message(self):
        loads = json.loads
        with patch.object(json, 'loads', wraps=loads) as decoder:
            with self.scope.read_scope():
                for _ in range(2):
                    with self.assertRaises(json.JSONDecodeError) as error:
                        self.scope.decode('{')
                    try:
                        loads('{')
                    except json.JSONDecodeError as expected:
                        self.assertEqual(error.exception.args, expected.args)
                self.assertEqual(decoder.call_count, 2)
                self.assertEqual(self.scope._ACTIVE.get(), {})

    def test_exception_unwinds_scope_without_leaking_into_ordinary_readers(self):
        class Rejected(Exception):
            pass
        with self.assertRaises(Rejected):
            with self.scope.read_scope():
                self.scope.decode(self.raw)
                cache = self.scope._ACTIVE.get()
                raise Rejected('native validation refused')
        self.assertEqual(cache, {})
        self.assertIsNone(self.scope._ACTIVE.get())
        self.assertIsNot(self.scope.decode(self.raw), self.scope.decode(self.raw))

    def test_nested_success_and_failure_restore_the_exact_outer_scope(self):
        with self.scope.read_scope():
            outer = self.scope.decode(self.raw)
            outer_cache = self.scope._ACTIVE.get()
            for fail in (False, True):
                try:
                    with self.scope.read_scope():
                        inner = self.scope.decode(self.raw)
                        inner_cache = self.scope._ACTIVE.get()
                        self.assertIsNot(inner, outer)
                        if fail:
                            raise ValueError('inner refusal')
                except ValueError:
                    pass
                self.assertEqual(inner_cache, {})
                self.assertIs(self.scope._ACTIVE.get(), outer_cache)
                self.assertIs(self.scope.decode(self.raw), outer)

    def test_decorator_recreates_scope_for_every_invocation(self):
        @self.scope.read_scope()
        def read():
            first = self.scope.decode(self.raw)
            self.assertIs(first, self.scope.decode(self.raw))
            return first
        first, second = read(), read()
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertIsNone(self.scope._ACTIVE.get())

    def test_no_call_cache_is_shared_with_an_unrelated_thread(self):
        observations = Queue()
        def unrelated():
            observations.put((self.scope._ACTIVE.get(), self.scope.decode(self.raw),
                              self.scope.decode(self.raw)))
        with self.scope.read_scope():
            active = self.scope.decode(self.raw)
            worker = Thread(target=unrelated)
            worker.start()
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            cache, first, second = observations.get_nowait()
            self.assertIsNone(cache)
            self.assertIsNot(first, second)
            self.assertIsNot(first, active)

    def test_nonexact_string_types_keep_json_semantics_without_sharing(self):
        class Text(str):
            pass
        with self.scope.read_scope():
            for raw in (self.raw.encode(), bytearray(self.raw.encode()), Text(self.raw)):
                with self.subTest(kind=type(raw)):
                    self.assertEqual(self.scope.decode(raw), json.loads(raw))
                    self.assertIsNot(self.scope.decode(raw), self.scope.decode(raw))
            for raw in (1, [], {}, None):
                with self.subTest(raw=raw):
                    with self.assertRaises(TypeError) as actual:
                        self.scope.decode(raw)
                    with self.assertRaises(TypeError) as expected:
                        json.loads(raw)
                    self.assertEqual(actual.exception.args, expected.exception.args)

    def test_valid_scalar_null_and_whitespace_are_not_mistaken_for_missing(self):
        loads = json.loads
        with patch.object(json, 'loads', wraps=loads) as decoder:
            with self.scope.read_scope():
                for raw in ('null', 'false', '0', '[]', '{}', ' {} '):
                    self.scope.decode(raw)
                    self.scope.decode(raw)
                self.assertEqual(decoder.call_count, 6)

    def root_getter(self):
        ns = {'json': json, 'validation_json': self.scope, 'RECORD_KEY': 'record',
              'VERSION': 1, 'ROOT_NAME': 'Root', '_error': ValueError}
        getter = function('root_control.py', 'get_record', ns)
        payload = {'version': 1, 'master': 'Root', 'bones': {'MASTER': 'Root'},
                   'controls': ['Hand'], 'sources': ['Hips'],
                   'control_states': {'Hand': {}}, 'source_states': {'Hips': {}},
                   'bone_states': {'MASTER': {}}, 'widgets': {'MASTER': {}}, 'constraints': [{}]}
        rig = types.SimpleNamespace(data={'record': json.dumps(payload)})
        return getter, rig, payload

    def test_actual_getter_reads_rna_raw_again_and_rechecks_schema(self):
        getter, rig, payload = self.root_getter()
        with self.scope.read_scope():
            first = getter(rig)
            self.assertIs(first, getter(rig))
            payload['version'] = 999
            rig.data['record'] = json.dumps(payload)
            with self.assertRaisesRegex(ValueError, 'recovery data is invalid'):
                getter(rig)

    def test_actual_getter_outside_scope_retains_writer_owned_records(self):
        getter, rig, _payload = self.root_getter()
        with self.scope.read_scope():
            proof_record = getter(rig)
        writer = getter(rig)
        writer['source_states']['Hips']['new'] = True
        self.assertNotIn('new', proof_record['source_states']['Hips'])
        self.assertNotIn('new', getter(rig)['source_states']['Hips'])

    def test_cached_json_never_caches_actual_native_ownership_validation(self):
        ns = {'json': json, 'validation_json': self.scope,
              'LimbIKError': ValueError, 'LEGACY_SOURCE_WIDGETS_VERSION': 1,
              'SOURCE_WIDGETS_VERSION': 2, 'ARMATURE_ID_KEY': 'id',
              'OWNER_KEY': 'owner', 'OWNER_VALUE': 'generated', 'SIDES': {'L', 'R'},
              '_validate_shape_state': lambda *_args, **_kwargs: None}
        parse = function('limb_ik.py', '_parse_source_widget_registry', ns)
        data = type('ArmatureData', (dict,), {})({'id': 'A'})
        data.bones = {'source': {'owner': 'artist'}}
        rig = types.SimpleNamespace(data=data)
        raw = json.dumps({'version': 2, 'armature_id': 'A', 'bones': {
            'source': {'kind': 'FINGER', 'side': 'L', 'original': {}, 'generated': {}}}})
        with self.scope.read_scope():
            parse(rig, raw)
            data['id'] = 'B'
            with self.assertRaisesRegex(ValueError, 'another Armature ID'):
                parse(rig, raw)
            data['id'] = 'A'
            data.bones['source']['owner'] = 'generated'
            with self.assertRaisesRegex(ValueError, 'missing or generated'):
                parse(rig, raw)


if __name__ == '__main__':
    unittest.main(verbosity=2)
