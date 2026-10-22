"""Pure data-only cache contracts: no Blender, native proof or timing claim."""
import ast
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import math
from pathlib import Path
import struct
import sys
import types
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def load_scope():
    spec = importlib.util.spec_from_file_location('_validation_json_binary_under_test', SOURCE / 'validation_json.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def raw(value, label='padding'):
    return json.dumps({'data': value, label: 'x' * 4200}, ensure_ascii=True)


def exact(value):
    kind = type(value)
    if kind is float:
        return (kind.__name__, struct.pack('>d', value))
    if kind is dict:
        return ('dict', tuple((exact(key), exact(item)) for key, item in value.items()))
    if kind is list:
        return ('list', tuple(exact(item) for item in value))
    return (kind.__name__, value)


def getter(file, name, namespace):
    tree = ast.parse((SOURCE / file).read_bytes())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), file, 'exec'), namespace)
    return namespace[name]


class ValidationJsonBinaryCacheTests(unittest.TestCase):
    def setUp(self):
        self.scope = load_scope()

    def decode(self, text):
        with self.scope.read_scope():
            return self.scope.decode(text)

    def test_finite_types_bits_unicode_surrogates_and_order_are_exact(self):
        values = [None, False, True, 0, -(2 ** 3000), 2 ** 3000, 0.0, -0.0,
                  5e-324, -5e-324, sys.float_info.max, '中文é😀\ud800\udfff',
                  {'z': [1, 2], 'a': {}, 'm': 'null'}, [[], {}, []]]
        for value in values:
            with self.subTest(kind=type(value).__name__, preview=str(value)[:40]):
                text = raw(value)
                expected = exact(json.loads(text))
                self.assertEqual(exact(self.decode(text)), expected)
                self.assertEqual(exact(self.decode(text)), expected)
        self.assertTrue(self.scope._PACKED)
        self.assertTrue(all(type(key) is str and type(value) is bytes
                            for key, value in self.scope._PACKED.items()))

    def test_duplicate_key_retains_first_position_and_last_value(self):
        text = '{"z":0,"a":1,"z":2,"pad":"' + 'x' * 4200 + '"}'
        first, second = self.decode(text), self.decode(text)
        self.assertEqual(list(second), ['z', 'a', 'pad'])
        self.assertEqual(exact(second), exact(json.loads(text)))
        self.assertIsNot(first, second)

    def test_nonfinite_values_keep_json_shared_constants_and_are_not_packed(self):
        for token in ('NaN', 'Infinity', '-Infinity', '1e309', '-1e309'):
            with self.subTest(token=token):
                text = '{"data":[' + token + '],"pad":"' + 'x' * 4200 + '"}'
                first, second = self.decode(text), self.decode(text)
                self.assertEqual(first, second)
                if token == 'NaN':
                    self.assertIs(first['data'][0], second['data'][0])
                self.assertFalse(math.isfinite(second['data'][0]))
                self.assertNotIn(text, self.scope._PACKED)

    def test_each_scope_has_fresh_independent_mutable_containers(self):
        text = raw([{'same': []}, {'same': []}])
        first = self.decode(text)
        first['data'][0]['same'].append('mutated')
        self.assertEqual(first['data'][1]['same'], [])
        second = self.decode(text)
        self.assertEqual(second['data'], [{'same': []}, {'same': []}])
        self.assertIsNot(first['data'], second['data'])
        self.assertIsNot(second['data'][0], second['data'][1])
        with self.scope.read_scope():
            live = self.scope.decode(text)
            self.assertIs(live, self.scope.decode(text))

    def test_ordinary_reads_still_use_json_and_never_read_or_populate_binary(self):
        text = raw({'a': []})
        loads = json.loads
        with patch.object(json, 'loads', wraps=loads) as decoder:
            self.decode(text)
            self.assertEqual(decoder.call_count, 1)
            self.decode(text)
            self.assertEqual(decoder.call_count, 1)
            first, second = self.scope.decode(text), self.scope.decode(text)
            self.assertEqual(decoder.call_count, 3)
            first['data']['a'].append(9)
            self.assertEqual(second['data']['a'], [])

    def test_small_and_nonexact_raws_never_enter_binary_cache(self):
        class Text(str):
            pass
        text = raw([1])
        for value in ('{"small":1}', text.encode(), bytearray(text.encode()), Text(text)):
            with self.subTest(kind=type(value).__name__):
                self.assertEqual(self.decode(value), json.loads(value))
                self.assertFalse(self.scope._PACKED)

    def test_parse_errors_and_exception_positions_remain_standard_and_uncached(self):
        for value in ('{"broken":' + ' ' * 4200, '[NaN,' + ' ' * 4200, 1, [], {}, None):
            for _ in range(2):
                with self.subTest(kind=type(value).__name__):
                    try:
                        json.loads(value)
                    except (ValueError, TypeError) as expected:
                        with self.assertRaises(type(expected)) as actual:
                            self.decode(value)
                        self.assertEqual(actual.exception.args, expected.args)
                        if isinstance(expected, json.JSONDecodeError):
                            self.assertEqual(actual.exception.pos, expected.pos)
            self.assertFalse(self.scope._PACKED)

    def test_encode_failure_returns_original_parsed_result_without_caching(self):
        text = raw([{'a': 1}])
        for error in (ValueError, TypeError, OverflowError, RecursionError):
            with self.subTest(error=error):
                with patch.object(self.scope.marshal, 'dumps', side_effect=error('unsupported')):
                    self.assertEqual(self.decode(text), json.loads(text))
                self.assertFalse(self.scope._PACKED)

    def test_binary_load_failure_evicts_and_uses_original_json(self):
        text = raw([1, {'a': 2}])
        self.decode(text)
        self.assertIn(text, self.scope._PACKED)
        with patch.object(self.scope.marshal, 'loads', side_effect=ValueError('unsupported')):
            self.assertEqual(self.decode(text), json.loads(text))
        self.assertNotIn(text, self.scope._PACKED)

    def test_deep_json_success_still_succeeds_when_marshal_cannot_pack_it(self):
        # Platform scanner limits differ; derive the original outcome first.
        text = '[' * 1100 + '"' + 'x' * 4200 + '"' + ']' * 1100
        try:
            expected = json.loads(text)
        except RecursionError as expected_error:
            with self.assertRaises(RecursionError) as actual:
                self.decode(text)
            self.assertEqual(actual.exception.args, expected_error.args)
        else:
            actual = self.decode(text)
            for _ in range(1100):
                actual, expected = actual[0], expected[0]
            self.assertEqual(actual, expected)

    def test_integer_limit_change_cannot_reuse_a_previously_accepted_large_integer(self):
        limit = sys.get_int_max_str_digits()
        text = '{"data":' + '1' * 4500 + '}'
        try:
            sys.set_int_max_str_digits(0)
            self.decode(text)
            self.assertIn(text, self.scope._PACKED)
            sys.set_int_max_str_digits(4300)
            with self.assertRaises(ValueError) as expected:
                json.loads(text)
            with self.assertRaises(ValueError) as actual:
                self.decode(text)
            self.assertEqual(actual.exception.args, expected.exception.args)
            self.assertNotIn(text, self.scope._PACKED)
        finally:
            sys.set_int_max_str_digits(limit)

    def test_recursion_limit_change_discards_prior_binary_values(self):
        text = raw({'nested': [1, 2]})
        self.decode(text)
        prior = self.scope._PACKED[text]
        limit = sys.getrecursionlimit()
        try:
            sys.setrecursionlimit(limit + 10)
            self.decode(text)
            self.assertIsNot(self.scope._PACKED[text], prior)
        finally:
            sys.setrecursionlimit(limit)

    def test_decoder_replacement_invalidates_binary_data(self):
        text = raw([1])
        self.decode(text)
        with patch.object(json, 'loads', return_value={'custom': []}):
            self.assertEqual(self.decode(text), {'custom': []})
        self.assertEqual(self.decode(text), json.loads(text))

    def test_lru_entry_count_and_actual_byte_accounting_are_bounded(self):
        self.scope._MAX_PACKED_ENTRIES = 3
        self.scope._MAX_PACKED_BYTES = 30000
        texts = [raw([index]) for index in range(5)]
        for text in texts[:3]:
            self.decode(text)
        self.decode(texts[0])
        self.decode(texts[3])
        self.assertNotIn(texts[1], self.scope._PACKED)
        self.assertEqual(list(self.scope._PACKED), [texts[2], texts[0], texts[3]])
        self.assertEqual(self.scope._PACKED_BYTES, self.scope._packed_size())
        self.assertLessEqual(self.scope._PACKED_BYTES, 30000)
        self.scope._MAX_PACKED_ENTRIES = 16
        self.scope._MAX_PACKED_BYTES = 20000
        self.decode(texts[4])
        self.assertLessEqual(self.scope._PACKED_BYTES, 20000)
        huge = raw('y' * 30000)
        self.assertEqual(self.decode(huge), json.loads(huge))
        self.assertNotIn(huge, self.scope._PACKED)
        self.assertLessEqual(self.scope._PACKED_BYTES, 20000)
        self.assertEqual(self.scope._PACKED_BYTES, self.scope._packed_size())

    def test_exception_and_nested_scopes_keep_fresh_mutable_trees(self):
        text = raw([1])
        with self.scope.read_scope():
            outer = self.scope.decode(text)
            outer_cache = self.scope._ACTIVE.get()
            with self.assertRaisesRegex(ValueError, 'controlled'):
                with self.scope.read_scope():
                    inner = self.scope.decode(text)
                    self.assertIsNot(inner, outer)
                    inner['data'].append(99)
                    raise ValueError('controlled')
            self.assertIs(self.scope._ACTIVE.get(), outer_cache)
            self.assertIs(self.scope.decode(text), outer)
            self.assertEqual(outer['data'], [1])
        self.assertIsNone(self.scope._ACTIVE.get())
        self.assertEqual(outer_cache, {})

    def test_concurrent_contexts_share_only_bytes_and_keep_budget_consistent(self):
        self.scope._MAX_PACKED_BYTES = 50000
        def work(index):
            text = raw([index % 5])
            for _ in range(25):
                with self.scope.read_scope():
                    first = self.scope.decode(text)
                    self.assertIs(first, self.scope.decode(text))
                    first['data'].append(index)
                self.assertIsNone(self.scope._ACTIVE.get())
                self.assertEqual(self.decode(text)['data'], [index % 5])
        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(work, range(18)))
        self.assertEqual(self.scope._PACKED_BYTES, self.scope._packed_size())
        self.assertLessEqual(self.scope._PACKED_BYTES, 50000)

    def test_current_rna_raw_and_schema_are_checked_after_cross_scope_cache_hit(self):
        namespace = {'json': json, 'validation_json': self.scope, 'RECORD_KEY': 'record',
                     'VERSION': 1, 'ROOT_NAME': 'Root', '_error': ValueError}
        read = getter('root_control.py', 'get_record', namespace)
        payload = {'version': 1, 'master': 'Root', 'bones': {'MASTER': 'Root'},
                   'controls': ['Hand'], 'sources': ['Hips'],
                   'control_states': {'Hand': {}}, 'source_states': {'Hips': {}},
                   'bone_states': {'MASTER': {}}, 'widgets': {'MASTER': {}},
                   'constraints': [{}], 'padding': 'x' * 4200}
        rig = types.SimpleNamespace(data={'record': json.dumps(payload)})
        for _ in range(2):
            with self.scope.read_scope():
                self.assertEqual(read(rig)['version'], 1)
        payload['version'] = 999
        rig.data['record'] = json.dumps(payload)
        with self.scope.read_scope():
            with self.assertRaisesRegex(ValueError, 'recovery data is invalid'):
                read(rig)

    def test_native_ownership_is_rechecked_even_when_pure_json_was_packed(self):
        namespace = {'json': json, 'validation_json': self.scope,
                     'LimbIKError': ValueError, 'LEGACY_SOURCE_WIDGETS_VERSION': 1,
                     'SOURCE_WIDGETS_VERSION': 2, 'ARMATURE_ID_KEY': 'id',
                     'OWNER_KEY': 'owner', 'OWNER_VALUE': 'generated', 'SIDES': {'L', 'R'},
                     '_validate_shape_state': lambda *_args, **_kwargs: None}
        parse = getter('limb_ik.py', '_parse_source_widget_registry', namespace)
        data = type('ArmatureData', (dict,), {})({'id': 'A'})
        data.bones = {'source': {'owner': 'artist'}}
        rig = types.SimpleNamespace(data=data)
        text = json.dumps({'version': 2, 'armature_id': 'A', 'bones': {
            'source': {'kind': 'FINGER', 'side': 'L', 'original': {}, 'generated': {}}},
            'padding': 'x' * 4200})
        with self.scope.read_scope():
            parse(rig, text)
        self.assertIn(text, self.scope._PACKED)
        data['id'] = 'B'
        with self.scope.read_scope():
            with self.assertRaisesRegex(ValueError, 'another Armature ID'):
                parse(rig, text)
        data['id'] = 'A'
        data.bones['source']['owner'] = 'generated'
        with self.scope.read_scope():
            with self.assertRaisesRegex(ValueError, 'missing or generated'):
                parse(rig, text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
