"""One exact rig pair owns one checkpoint across nested Body operations."""
import ast
from contextlib import contextmanager
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/body_setup.py'
PACKAGE = '_body_atomic_join_fixture'
RESTORED_MARKER = '_character_designer_body_rollback_restored'


class BodyAtomicJoinTests(unittest.TestCase):
    def setUp(self):
        self.events, self.values, self.object_values = [], {}, {}
        package = ModuleType(PACKAGE)
        package.__path__ = []
        display, sync = ModuleType(PACKAGE + '.bone_display'), ModuleType(PACKAGE + '.bone_display_sync')
        package.bone_display, package.bone_display_sync = display, sync
        self.patcher = patch.dict(sys.modules, {PACKAGE: package,
            display.__name__: display, sync.__name__: sync})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.context = object()
        self.first = SimpleNamespace(name='First', data=object())
        self.second = SimpleNamespace(name='Second', data=object())
        self.values.update(First='original First', Second='original Second')
        self.object_values.update(First='original Object First', Second='original Object Second')
        display._affected = lambda context, rig: (rig,)
        display._checkpoint = lambda rigs: ('display', rigs[0].name)
        display.restore_view = lambda rig: self.events.append(('display_begin', rig.name))
        display._rollback = lambda checkpoint: self.events.append(('display_restore', checkpoint[1]))
        sync._BUSY = False
        sync.completed = lambda rig: self.events.append(('completed', rig.name))

        def capture(context, rig):
            self.events.append(('capture', rig.name))
            return {'name': rig.name, 'context': None, 'value': self.values[rig.name],
                    'object_inputs': [{'name': rig.name, 'value': self.object_values[rig.name]}]}

        def restore_object_inputs(context, inputs):
            changed = False
            for state in inputs:
                self.events.append(('object_restore', state['name']))
                changed |= self.object_values[state['name']] != state['value']
                self.object_values[state['name']] = state['value']
            return changed

        def restore(context, rig, checkpoint):
            self.events.append(('restore', rig.name))
            self.values[rig.name] = checkpoint['value']
            # The native transaction restores context before its final Object
            # input synchronization; the outer display rollback runs later.
            self.namespace['limb_ik']._restore_context(context, rig, checkpoint['context'])
            restore_object_inputs(context, checkpoint['object_inputs'])

        def discard(checkpoint):
            self.events.append(('discard', checkpoint['name']))

        transaction = SimpleNamespace(capture=capture, restore=restore, discard=discard,
            assert_original_ids=lambda checkpoint: None, restore_object_inputs=restore_object_inputs)

        @contextmanager
        def paused():
            self.events.append(('pause',))
            try:
                yield
            finally:
                self.events.append(('unpause',))

        self.namespace = {'__name__': PACKAGE + '.body_setup', '__package__': PACKAGE,
            '_ATOMIC_STACK': [], '_runtime_paused': paused,
            'body_setup_transaction': transaction,
            'limb_ik': SimpleNamespace(_restore_context=lambda *args: self.events.append(('context_restore',)))}
        nodes = [node for node in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                 if isinstance(node, ast.FunctionDef) and node.name in {'_atomic', 'preserve_object_inputs'}]
        self.assertEqual({node.name for node in nodes}, {'_atomic', 'preserve_object_inputs'})
        self.assertEqual(len(nodes), 2)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.namespace)
        self.atomic = self.namespace['_atomic']
        self.preserve = self.namespace['preserve_object_inputs']
        self.transaction, self.sync, self.display = transaction, sync, display

    def count(self, event):
        return sum(row[0] == event for row in self.events)

    def assert_released(self):
        self.assertEqual(self.namespace['_ATOMIC_STACK'], [])
        self.assertFalse(self.sync._BUSY)

    def test_same_rig_nested_success_uses_one_checkpoint_and_one_discard(self):
        def outer():
            return self.atomic(self.context, self.first, lambda: 'selected part built')
        self.assertEqual(self.atomic(self.context, self.first, outer), 'selected part built')
        self.assertEqual(self.count('capture'), 1)
        self.assertEqual(self.count('restore'), 0)
        self.assertEqual(self.count('discard'), 1)
        self.assertEqual(self.count('pause'), 1)
        self.assert_released()

    def test_nested_failure_cleans_deferred_style_token_after_single_outer_rollback(self):
        def inner():
            self.values['First'] = 'partly released'
            raise ValueError('surface guard refused')

        try:
            with self.assertRaisesRegex(ValueError, 'surface guard refused'):
                self.atomic(self.context, self.first,
                            lambda: self.atomic(self.context, self.first, inner))
        finally:
            self.events.append(('style_cleanup', 'selected token only'))
        self.assertEqual(self.values['First'], 'original First')
        self.assertEqual((self.count('capture'), self.count('restore'), self.count('discard')), (1, 1, 1))
        labels = [row[0] for row in self.events]
        self.assertLess(labels.index('restore'), labels.index('display_restore'))
        self.assertLess(labels.index('display_restore'), labels.index('discard'))
        self.assertLess(labels.index('discard'), labels.index('style_cleanup'))
        self.assert_released()

    def test_caught_nested_failure_cannot_commit_partial_outer_state(self):
        def inner():
            self.values['First'] = 'partial'
            raise ValueError('nested write failed')

        def outer():
            try:
                self.atomic(self.context, self.first, inner)
            except ValueError:
                pass
            return 'must not commit'

        with self.assertRaisesRegex(RuntimeError, 'nested Body operation failed') as captured:
            self.atomic(self.context, self.first, outer)
        self.assertIsInstance(captured.exception.__cause__, ValueError)
        self.assertEqual(self.values['First'], 'original First')
        self.assertEqual(self.count('restore'), 1)
        self.assert_released()

    def test_another_rig_owns_its_own_checkpoint(self):
        def outer():
            return self.atomic(self.context, self.second, lambda: 'other rig result')
        self.assertEqual(self.atomic(self.context, self.first, outer), 'other rig result')
        self.assertEqual(self.count('capture'), 2)
        self.assertEqual(self.count('discard'), 2)
        self.assertEqual(self.count('restore'), 0)
        self.assert_released()

    def test_cleanup_error_cannot_leave_an_active_join_for_the_next_click(self):
        real = self.transaction.discard
        self.transaction.discard = lambda checkpoint: (_ for _ in ()).throw(RuntimeError('cleanup failed'))
        with self.assertRaisesRegex(RuntimeError, 'cleanup failed'):
            self.atomic(self.context, self.first, lambda: None)
        self.assert_released()
        self.transaction.discard = real
        self.atomic(self.context, self.first, lambda: None)
        self.assertEqual(self.count('capture'), 2)
        self.assert_released()

    def test_recovered_error_is_marked_only_after_the_complete_outer_restore(self):
        refusal = ValueError('surface guard refused')
        real_restore, real_display = self.transaction.restore, self.display._rollback

        def restore(*args):
            self.assertFalse(getattr(refusal, RESTORED_MARKER, False))
            real_restore(*args)
            self.assertFalse(getattr(refusal, RESTORED_MARKER, False))

        def display_restore(*args):
            self.assertFalse(getattr(refusal, RESTORED_MARKER, False))
            real_display(*args)
            self.assertFalse(getattr(refusal, RESTORED_MARKER, False))

        def inner():
            self.values['First'] = 'partly released'
            raise refusal

        def outer():
            try:
                self.atomic(self.context, self.first, inner)
            except ValueError as error:
                self.assertIs(error, refusal)
                self.assertFalse(getattr(error, RESTORED_MARKER, False))
                self.assertEqual(self.count('restore'), 0)
                raise

        with patch.object(self.transaction, 'restore', side_effect=restore), \
             patch.object(self.display, '_rollback', side_effect=display_restore):
            with self.assertRaises(ValueError) as caught:
                self.atomic(self.context, self.first, outer)
        self.assertIs(caught.exception, refusal)
        self.assertIs(getattr(caught.exception, RESTORED_MARKER), True)
        self.assertEqual(str(caught.exception), 'surface guard refused')
        self.assertEqual(self.values['First'], 'original First')
        self.assertEqual((self.count('capture'), self.count('restore'), self.count('discard')), (1, 1, 1))
        self.assert_released()

    def test_failed_native_or_display_recovery_never_claims_a_complete_restore(self):
        for owner, method in ((self.transaction, 'restore'), (self.display, '_rollback')):
            with self.subTest(stage=method):
                refusal = ValueError('surface guard refused')

                def reject():
                    self.values['First'] = 'partial'
                    raise refusal

                with patch.object(owner, method, side_effect=RuntimeError('recovery failed')):
                    with self.assertRaisesRegex(RuntimeError, 'In-place recovery also failed') as caught:
                        self.atomic(self.context, self.first, reject)
                self.assertIs(caught.exception.__cause__, refusal)
                self.assertFalse(getattr(caught.exception, RESTORED_MARKER, False))
                self.assertFalse(getattr(refusal, RESTORED_MARKER, False))
                self.assert_released()

    def test_cleanup_failure_does_not_inherit_the_recovered_errors_marker(self):
        refusal = ValueError('surface guard refused')

        def reject():
            self.values['First'] = 'partial'
            raise refusal

        with patch.object(self.transaction, 'discard', side_effect=RuntimeError('cleanup failed')):
            with self.assertRaisesRegex(RuntimeError, 'cleanup failed') as caught:
                self.atomic(self.context, self.first, reject)
        # Complete recovery happened, but the subsequently raised cleanup error
        # is a different failure and must not tell the UI that all work finished.
        self.assertIs(getattr(refusal, RESTORED_MARKER), True)
        self.assertIs(caught.exception.__context__, refusal)
        self.assertFalse(getattr(caught.exception, RESTORED_MARKER, False))
        self.assertEqual(self.values['First'], 'original First')
        self.assert_released()

    def test_successful_calls_do_not_mark_values_or_keep_global_failure_state(self):
        untouched = ValueError('a later preflight refusal')
        self.assertIs(self.atomic(self.context, self.first, lambda: untouched), untouched)
        self.assertFalse(getattr(untouched, RESTORED_MARKER, False))
        self.assertEqual(self.count('restore'), 0)
        self.assert_released()

    def test_success_restores_object_inputs_after_context_and_only_for_exact_active_rig_pair(self):
        self.assertFalse(self.preserve(self.context, self.first))
        self.assertEqual(self.count('object_restore'), 0)

        def restore_context(context, rig, checkpoint):
            self.events.append(('context_restore',))
            self.object_values[rig.name] = 'context resampled Object input'

        def operation():
            self.object_values['First'] = 'driver resampled Object input'
            self.object_values['Second'] = 'unrelated rig edited separately'
            self.assertFalse(self.preserve(self.context, self.second))
            impostor = SimpleNamespace(name='First', data=self.first.data)
            self.assertFalse(self.preserve(self.context, impostor))
            original_data = self.first.data
            try:
                self.first.data = object()
                self.assertFalse(self.preserve(self.context, self.first))
            finally:
                self.first.data = original_data
            self.assertEqual(self.count('object_restore'), 0)
            return 'controls actually committed'

        with patch.object(self.namespace['limb_ik'], '_restore_context', side_effect=restore_context):
            self.assertEqual(self.atomic(self.context, self.first, operation), 'controls actually committed')
        self.assertEqual(self.object_values['First'], 'original Object First')
        self.assertEqual(self.object_values['Second'], 'unrelated rig edited separately')
        labels = [row[0] for row in self.events]
        self.assertLess(labels.index('context_restore'), labels.index('object_restore'))
        self.assertLess(labels.index('object_restore'), labels.index('discard'))
        self.assertEqual((self.count('capture'), self.count('restore'), self.count('object_restore')), (1, 0, 1))
        self.assertFalse(self.preserve(self.context, self.first))
        self.assertEqual(self.count('object_restore'), 1)
        self.assert_released()

    def test_failed_operation_resynchronizes_object_inputs_after_native_context_and_display_recovery(self):
        refusal = ValueError('surface guard refused')
        actual_display = self.display._rollback
        actual_inputs = self.transaction.restore_object_inputs

        def restore_context(context, rig, checkpoint):
            self.events.append(('context_restore',))
            self.object_values[rig.name] = 'native context resampled Object input'

        def display_restore(checkpoint):
            actual_display(checkpoint)
            self.object_values[checkpoint[1]] = 'display recovery resampled Object input'

        def restore_inputs(context, inputs):
            self.assertFalse(getattr(refusal, RESTORED_MARKER, False))
            result = actual_inputs(context, inputs)
            self.assertFalse(getattr(refusal, RESTORED_MARKER, False))
            return result

        def reject():
            self.values['First'] = 'partly rebuilt'
            self.object_values['First'] = 'driver resampled Object input'
            raise refusal

        with patch.object(self.namespace['limb_ik'], '_restore_context', side_effect=restore_context), \
             patch.object(self.display, '_rollback', side_effect=display_restore), \
             patch.object(self.transaction, 'restore_object_inputs', side_effect=restore_inputs):
            with self.assertRaises(ValueError) as caught:
                self.atomic(self.context, self.first, reject)
        self.assertIs(caught.exception, refusal)
        self.assertIs(getattr(refusal, RESTORED_MARKER), True)
        self.assertEqual(self.values['First'], 'original First')
        self.assertEqual(self.object_values['First'], 'original Object First')
        self.assertEqual(self.object_values['Second'], 'original Object Second')
        labels = [row[0] for row in self.events]
        positions = [index for index, label in enumerate(labels) if label == 'object_restore']
        self.assertEqual(len(positions), 2)
        self.assertLess(labels.index('restore'), labels.index('context_restore'))
        self.assertLess(labels.index('context_restore'), positions[0])
        self.assertLess(positions[0], labels.index('display_restore'))
        self.assertLess(labels.index('display_restore'), positions[1])
        self.assertLess(positions[1], labels.index('discard'))
        self.assert_released()


if __name__ == '__main__':
    unittest.main()
