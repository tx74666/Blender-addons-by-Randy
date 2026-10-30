"""Real source resolver with an ephemeral native scope; no Blender import."""
import ast
import gc
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch
import weakref


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
PACKAGE = '_source_rest_scope_fixture'


class LimbIKError(ValueError):
    pass


class Rig:
    __hash__ = None

    def __eq__(self, other):
        raise AssertionError('A read-scope identity must not compare rig values.')


def load_scope():
    spec = importlib.util.spec_from_file_location('_source_rest_native_scope', SOURCE / 'validation_native.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SourceRestScopeTests(unittest.TestCase):
    def setUp(self):
        self.scope = load_scope()
        self.adopted, self.result = True, None
        self.adoption_error = None
        self.view_error = None
        self.adoption_calls, self.view_calls = [], []
        package = ModuleType(PACKAGE)
        package.__path__ = []
        target = ModuleType(PACKAGE + '.animation_worklist_target')

        def is_adopted(armature):
            # Store only scalar identities: the fixture must not retain rigs.
            self.adoption_calls.append(id(armature))
            if self.adoption_error is not None:
                raise self.adoption_error
            return self.adopted

        def control_rest_view(armature):
            self.view_calls.append(id(armature))
            if self.view_error is not None:
                raise self.view_error
            return self.result

        target.is_adopted = is_adopted
        target.control_rest_view = control_rest_view
        package.animation_worklist_target = target
        module_patch = patch.dict(sys.modules, {PACKAGE: package, target.__name__: target})
        module_patch.start()
        self.addCleanup(module_patch.stop)
        tree = ast.parse((SOURCE / 'limb_ik.py').read_text(encoding='utf8'))
        resolver = next(node for node in tree.body
                        if isinstance(node, ast.FunctionDef) and node.name == '_validation_source_rest')
        namespace = {'__name__': PACKAGE + '.limb_ik', '__package__': PACKAGE,
                     'validation_native': self.scope, 'LimbIKError': LimbIKError}
        # Keep the real relative import and the entire current function body.
        exec(compile(ast.Module(body=[resolver], type_ignores=[]), 'limb_ik.py', 'exec'), namespace)
        self.resolve = namespace['_validation_source_rest']

    def six_queries(self, rig):
        return [self.resolve(rig) for _ in range(6)]

    def test_six_validator_queries_resolve_one_successful_none_in_scope(self):
        rig = Rig()
        with self.scope.read_scope():
            self.assertEqual(self.six_queries(rig), [None] * 6)
            self.assertTrue(self.scope.source_rest_is_none(rig))
        self.assertEqual(self.adoption_calls, [id(rig)])
        self.assertEqual(self.view_calls, [id(rig)])
        self.assertIsNone(self.scope._ACTIVE.get())

    def test_outside_scope_all_six_queries_remain_fresh(self):
        rig = Rig()
        self.assertEqual(self.six_queries(rig), [None] * 6)
        self.assertEqual(self.adoption_calls, [id(rig)] * 6)
        self.assertEqual(self.view_calls, [id(rig)] * 6)
        self.assertFalse(self.scope.source_rest_is_none(rig))

    def test_unadopted_none_is_scoped_without_reading_a_view(self):
        self.adopted = False
        rig = Rig()
        with self.scope.read_scope():
            self.assertEqual(self.six_queries(rig), [None] * 6)
        self.assertEqual(self.adoption_calls, [id(rig)])
        self.assertEqual(self.view_calls, [])
        self.six_queries(rig)
        self.assertEqual(len(self.adoption_calls), 7)

    def test_independent_scopes_and_changed_adoption_are_fresh(self):
        rig = Rig()
        for _ in range(2):
            with self.scope.read_scope():
                self.six_queries(rig)
        self.assertEqual(len(self.view_calls), 2)
        self.view_error = ValueError('saved reference changed')
        with self.scope.read_scope(), self.assertRaisesRegex(LimbIKError, 'saved reference changed'):
            self.resolve(rig)
        self.assertEqual(len(self.view_calls), 3)

    def test_nested_scope_is_fresh_and_restores_outer_hit(self):
        rig = Rig()
        with self.scope.read_scope():
            self.six_queries(rig)
            outer = self.scope._ACTIVE.get()
            with self.scope.read_scope():
                self.assertFalse(self.scope.source_rest_is_none(rig))
                self.six_queries(rig)
                inner = self.scope._ACTIVE.get()
                self.assertIsNot(inner, outer)
            self.assertEqual(inner, {})
            self.assertIs(self.scope._ACTIVE.get(), outer)
            self.six_queries(rig)
        self.assertEqual(outer, {})
        self.assertEqual(len(self.view_calls), 2)

    def test_nested_exception_clears_inner_and_preserves_outer_hit(self):
        rig = Rig()
        with self.scope.read_scope():
            self.resolve(rig)
            outer = self.scope._ACTIVE.get()
            with self.assertRaisesRegex(RuntimeError, 'inner boundary'):
                with self.scope.read_scope():
                    self.resolve(rig)
                    inner = self.scope._ACTIVE.get()
                    raise RuntimeError('inner boundary')
            self.assertEqual(inner, {})
            self.assertIs(self.scope._ACTIVE.get(), outer)
            self.assertIsNone(self.resolve(rig))
        self.assertEqual(len(self.view_calls), 2)

    def test_non_none_view_is_not_memoized(self):
        rig, view = Rig(), object()
        self.result = view
        with self.scope.read_scope():
            for result in self.six_queries(rig):
                self.assertIs(result, view)
            self.assertFalse(self.scope.source_rest_is_none(rig))
        self.assertEqual(len(self.view_calls), 6)
        self.assertEqual(len(self.adoption_calls), 6)

    def test_explicit_view_always_passes_through_even_after_none_hit(self):
        rig = Rig()
        with self.scope.read_scope():
            self.resolve(rig)
            self.adoption_error = AssertionError('Explicit view must not resolve adoption.')
            for explicit in (object(), False, 0, '', []):
                self.assertIs(self.resolve(rig, explicit), explicit)
        self.assertEqual(len(self.adoption_calls), 1)
        self.assertEqual(len(self.view_calls), 1)
        self.assertIs(self.resolve(rig, False), False)

    def test_view_value_error_preserves_wrapping_cause_and_is_not_cached(self):
        rig = Rig()
        self.view_error = ValueError('artist Rest changed')
        with self.scope.read_scope():
            for _ in range(2):
                with self.assertRaises(LimbIKError) as caught:
                    self.resolve(rig)
                self.assertEqual(str(caught.exception), 'Main Rig motion Rest proof failed: artist Rest changed')
                self.assertIs(caught.exception.__cause__, self.view_error)
                self.assertFalse(self.scope.source_rest_is_none(rig))
        self.assertEqual(len(self.view_calls), 2)

    def test_adoption_error_remains_unwrapped_and_is_not_cached(self):
        rig = Rig()
        self.adoption_error = ValueError('adoption lookup failed')
        with self.scope.read_scope():
            for _ in range(2):
                with self.assertRaises(ValueError) as caught:
                    self.resolve(rig)
                self.assertIs(caught.exception, self.adoption_error)
                self.assertIs(type(caught.exception), ValueError)
        self.assertEqual(len(self.adoption_calls), 2)
        self.assertEqual(self.view_calls, [])

    def test_other_view_exceptions_remain_unwrapped_and_are_not_cached(self):
        rig = Rig()
        self.view_error = TypeError('malformed native reference')
        with self.scope.read_scope():
            for _ in range(2):
                with self.assertRaises(TypeError) as caught:
                    self.resolve(rig)
                self.assertIs(caught.exception, self.view_error)
        self.assertEqual(len(self.view_calls), 2)

    def test_failure_then_success_can_be_remembered(self):
        rig = Rig()
        with self.scope.read_scope():
            self.view_error = ValueError('first refusal')
            with self.assertRaises(LimbIKError):
                self.resolve(rig)
            self.view_error = None
            self.six_queries(rig)
        self.assertEqual(len(self.view_calls), 2)

    def test_unhashable_rigs_are_separate_identity_entries(self):
        first, second = Rig(), Rig()
        with self.scope.read_scope():
            self.six_queries(first)
            self.assertFalse(self.scope.source_rest_is_none(second))
            self.six_queries(second)
            self.six_queries(first)
        self.assertEqual(self.view_calls, [id(first), id(second)])

    def test_none_identity_is_not_mistaken_for_a_cache_miss(self):
        self.scope.remember_source_rest_none(None)
        self.assertFalse(self.scope.source_rest_is_none(None))
        with self.scope.read_scope():
            self.assertFalse(self.scope.source_rest_is_none(None))
            self.scope.remember_source_rest_none(None)
            self.assertTrue(self.scope.source_rest_is_none(None))
        self.assertFalse(self.scope.source_rest_is_none(None))

    def test_scope_strong_reference_is_released_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                try:
                    with self.scope.read_scope():
                        rig = Rig()
                        borrowed = weakref.ref(rig)
                        self.resolve(rig)
                        cache = self.scope._ACTIVE.get()
                        del rig
                        gc.collect()
                        self.assertIsNotNone(borrowed())
                        if fail:
                            raise RuntimeError('end failed validation')
                except RuntimeError:
                    pass
                gc.collect()
                self.assertIsNone(borrowed())
                self.assertEqual(cache, {})
                self.assertIsNone(self.scope._ACTIVE.get())


if __name__ == '__main__':
    unittest.main()
