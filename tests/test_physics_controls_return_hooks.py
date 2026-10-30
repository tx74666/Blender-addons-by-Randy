"""Controls returns finish ownership before applying explicit physics choices.

Exercise the real wrapper/display definitions with an ordered transaction model;
the native Original and physics backends retain their own independent suites.
"""
import ast
from contextlib import contextmanager
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch
import uuid


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


def definitions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('Missing exact product definitions.')
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)


class ID(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


class PhysicsControlsReturnHookTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.context = NS(scene=object())
        self.rig = ID(data=ID())
        self.package = ModuleType('_physics_return_hooks_' + uuid.uuid4().hex)
        self.package.__path__ = []
        modules = patch.dict(sys.modules, {self.package.__name__: self.package})
        modules.start()
        self.addCleanup(modules.stop)
        self.apply_result = {'applied': ['Dress'], 'deferred': [], 'failed': []}

        def apply(context, rig, **options):
            self.assertIs(context, self.context)
            self.assertIs(rig, self.rig)
            self.assertEqual(options, {'reconcile': True})
            self.events.append('apply-physics')
            return self.apply_result

        self.package.animation_physics = NS(apply_pending_skirt_modes=apply)
        self.display_ns = {'__package__': self.package.__name__}
        definitions(SOURCE/'bone_display.py', {'_apply_pending_physics'}, self.display_ns)
        self.helper = self.display_ns['_apply_pending_physics']

    def load_leave(self, *, run_result=True, transfer_error=None,
                   finalizer_error=None, owner_error=None):
        capability = object()

        @contextmanager
        def deferred(context, **options):
            self.assertIs(context, self.context)
            self.assertEqual(options, {'flush_on_exit': False})
            self.events.append('defer-enter')
            try:
                yield object()
            finally:
                self.events.append('defer-exit')

        def transfer(context, rig, refresh, *, finalize, _original_pose):
            self.assertIs(context, self.context)
            self.assertIs(rig, self.rig)
            self.assertIs(_original_pose, capability)
            self.events.append('transfer')
            if transfer_error is not None:
                raise transfer_error
            finalize()
            return run_result

        def owner_return(context, rig, callback):
            self.events.append('owner-enter')
            if owner_error is not None:
                raise owner_error

            def finalize():
                self.events.append('finalize')
                if finalizer_error is not None:
                    raise finalizer_error

            returned = callback(finalize, _original_pose=capability)
            self.events.append('owner-finished')
            return returned

        self.package.forearm_twist = NS(defer_runtime=deferred)
        self.package.body_original_owner = NS(run_return=owner_return)
        namespace = {'__package__': self.package.__name__, '_leave': transfer,
                     'display': NS(_apply_pending_physics=self.helper)}
        definitions(SOURCE/'body_original_mode.py', {'leave'}, namespace)
        return namespace['leave']

    def load_restore(self, *, capability=False, restore_failure=False,
                     preflight_failure=False, view=True):
        view_key, refs_key = 'saved-view', 'saved-refs'
        if view:
            self.rig.data[view_key] = json.dumps({'rigs': {'Main': {'display': 'artist'}}})
            self.rig.data[refs_key] = {'Main': self.rig}
        else:
            self.rig.data[refs_key] = {'Main': self.rig}
        self.groups = NS(_FRAME_CACHE={'unrelated': 'cache'},
                         _frame_visibility=lambda *args, **kwargs: self.events.append('frame-visibility'))
        self.capability = object() if capability else None
        self.package.body_original_owner = NS(_display_pose_capability=lambda cap, rig: capability)
        restore_count = 0

        def restore(rig, state):
            nonlocal restore_count
            restore_count += 1
            self.events.append('restore')
            if restore_failure and restore_count == 1:
                raise ValueError('Native display restore refused')

        def preflight(rig, state):
            self.events.append('check-restore')
            if preflight_failure:
                raise ValueError('Saved display ownership changed')

        namespace = {'__package__': self.package.__name__, 'json': json,
                     'VIEW_KEY': view_key, 'REFS_KEY': refs_key,
                     '_editable': lambda *args, **kwargs: self.events.append('editable'),
                     '_check_object_restore': preflight,
                     '_snapshot': lambda rig: {'display': 'original'}, '_restore': restore,
                     '_completed': lambda rig: self.events.append('display-completed'),
                     '_apply_pending_physics': self.helper,
                     'groups': self.groups, 'bpy': NS(context=self.context)}
        definitions(SOURCE/'bone_display.py', {'restore_view'}, namespace)
        return namespace['restore_view']

    def test_full_original_return_applies_after_finalizer_and_owner_release(self):
        leave = self.load_leave()
        self.assertTrue(leave(self.context, self.rig))
        self.assertEqual(self.events, ['owner-enter', 'defer-enter', 'transfer',
                                     'finalize', 'defer-exit', 'owner-finished', 'apply-physics'])

    def test_transfer_failure_cannot_apply_a_pending_choice(self):
        leave = self.load_leave(transfer_error=ValueError('Pose verification failed'))
        with self.assertRaisesRegex(ValueError, 'Pose verification failed'):
            leave(self.context, self.rig)
        self.assertNotIn('apply-physics', self.events)
        self.assertNotIn('owner-finished', self.events)

    def test_final_ownership_failure_cannot_apply_a_pending_choice(self):
        leave = self.load_leave(finalizer_error=ValueError('Retained Action changed'))
        with self.assertRaisesRegex(ValueError, 'Retained Action changed'):
            leave(self.context, self.rig)
        self.assertNotIn('apply-physics', self.events)
        self.assertNotIn('owner-finished', self.events)

    def test_original_admission_failure_cannot_apply_a_pending_choice(self):
        leave = self.load_leave(owner_error=ValueError('Owner admission refused'))
        with self.assertRaisesRegex(ValueError, 'Owner admission refused'):
            leave(self.context, self.rig)
        self.assertEqual(self.events, ['owner-enter'])

    def test_noop_original_return_does_not_apply_physics(self):
        leave = self.load_leave(run_result=False)
        self.assertFalse(leave(self.context, self.rig))
        self.assertNotIn('apply-physics', self.events)

    def test_refused_physics_does_not_change_completed_original_result(self):
        self.apply_result = {'applied': [], 'deferred': [],
                             'failed': [{'source': 'Dress', 'message': 'Owner guard refused'}]}
        leave = self.load_leave()
        self.assertTrue(leave(self.context, self.rig))
        self.assertEqual(self.events[-2:], ['owner-finished', 'apply-physics'])

    def test_normal_display_restore_applies_after_display_completion(self):
        restore = self.load_restore()
        self.assertTrue(restore(self.rig))
        self.assertEqual(self.events[-3:], ['frame-visibility', 'display-completed', 'apply-physics'])
        self.assertNotIn('saved-view', self.rig.data)
        self.assertNotIn('saved-refs', self.rig.data)

    def test_capability_display_restore_never_applies_inside_original(self):
        restore = self.load_restore(capability=True)
        self.assertTrue(restore(self.rig, _original_pose=self.capability))
        self.assertNotIn('apply-physics', self.events)
        self.assertEqual(self.groups._FRAME_CACHE, {'unrelated': 'cache'})

    def test_display_preflight_failure_cannot_apply_pending_physics(self):
        restore = self.load_restore(preflight_failure=True)
        with self.assertRaisesRegex(ValueError, 'Saved display ownership changed'):
            restore(self.rig)
        self.assertIn('saved-view', self.rig.data)
        self.assertNotIn('apply-physics', self.events)

    def test_native_display_failure_rolls_back_without_applying_physics(self):
        restore = self.load_restore(restore_failure=True)
        with self.assertRaisesRegex(ValueError, 'Native display restore refused'):
            restore(self.rig)
        self.assertEqual(self.events.count('restore'), 2)
        self.assertIn('saved-view', self.rig.data)
        self.assertNotIn('apply-physics', self.events)

    def test_no_saved_display_leaves_physics_and_other_markers_untouched(self):
        restore = self.load_restore(view=False)
        self.assertFalse(restore(self.rig))
        self.assertIn('saved-refs', self.rig.data)
        self.assertNotIn('apply-physics', self.events)

    def test_failed_choice_does_not_change_completed_display_restore(self):
        self.apply_result = {'applied': [], 'deferred': [],
                             'failed': [{'source': 'Dress', 'message': 'Source removed'}]}
        restore = self.load_restore()
        self.assertTrue(restore(self.rig))
        self.assertNotIn('saved-view', self.rig.data)
        self.assertEqual(self.events[-1], 'apply-physics')


if __name__ == '__main__':
    unittest.main()
