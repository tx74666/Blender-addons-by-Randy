"""Pure tests of the actual explicit HoldPose capture and UI entry functions.

Only selected source ASTs are executed; this never imports bpy or the add-on.
"""
import ast
import copy
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'


def actual_function(filename, name, namespace, *, owner=None):
    tree = ast.parse((SOURCE / filename).read_text(encoding='utf-8'))
    container = next(node for node in tree.body
                     if isinstance(node, ast.ClassDef) and node.name == owner) if owner else tree
    node = copy.deepcopy(next(node for node in container.body
                              if isinstance(node, ast.FunctionDef) and node.name == name))
    node.decorator_list = []
    code = compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                   str(SOURCE / filename), 'exec')
    exec(code, namespace)
    return namespace[name]


class PublicationPreflight(Exception):
    """Stop before any filesystem, worker, or publication code is reached."""


class CaptureTests(unittest.TestCase):
    def setup_capture(self, *, target='B', mode='HoldPose', tweak=True, values=None, fail=False):
        self.reads, self.updates = [], []
        values = values if values is not None else {'owned.rotation': [0.25, -0.5]}
        old = [{'path': 'old.rotation', 'index': 0, 'values': [0.0, 1.0]}]
        self.bag = NS(fcurves=copy.deepcopy(old))
        rig = NS(animation_data=NS(use_tweak_mode=tweak))

        def resolve(path):
            self.reads.append(path)
            return values[path]

        rig.path_resolve = resolve
        self.action = object()
        session = {'edit_target': target, 'spec': {'timeMode': mode}, 'rig': rig,
                   'edit_action': self.action, 'edit_slot': 7}
        channels = [(path, index) for path, array in values.items()
                    for index in range(len(array))]

        def constant(bag, path, index, value, start, end):
            if fail and index == 1:
                raise RuntimeError('injected second-channel write failure')
            bag.fcurves.append({'path': path, 'index': index, 'values': [value, value],
                                'frames': [start, end]})

        namespace = {
            'math': math,
            'validate': lambda context, supplied: (session, object(), NS(channels=channels), {}),
            '_bag': lambda action, slot: self.bag,
            '_curves': lambda action, slot: self.bag.fcurves,
            '_curve_payload': copy.deepcopy,
            '_frame_range': lambda spec: (1.0, 3.0),
            '_constant_curve': constant,
            '_write_curve': lambda bag, row: bag.fcurves.append(copy.deepcopy(row)),
        }
        context = NS(view_layer=NS(update=lambda: self.updates.append('update')))
        self.capture = actual_function('animation_composition.py', 'capture_hold_pose', namespace)
        return context, rig, old

    def test_unkeyed_local_controls_become_equal_keys_on_exact_scope(self):
        context, rig, old = self.setup_capture()
        self.assertIs(self.capture(context, rig), self.action)
        self.assertEqual(self.reads, ['owned.rotation', 'owned.rotation'])
        self.assertEqual([row['values'] for row in self.bag.fcurves], [[0.25, 0.25], [-0.5, -0.5]])
        self.assertEqual([row['frames'] for row in self.bag.fcurves], [[1.0, 3.0], [1.0, 3.0]])
        self.assertEqual(self.updates, ['update'])
        self.assertNotEqual(self.bag.fcurves, old)

    def test_paused_or_absent_native_tweak_rejects_before_read_or_write(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                context, rig, old = self.setup_capture(tweak=False)
                if missing:
                    rig.animation_data = None
                with self.assertRaisesRegex(ValueError, 'Resume Editing'):
                    self.capture(context, rig)
                self.assertEqual(self.reads, [])
                self.assertEqual(self.bag.fcurves, old)

    def test_a_loop_and_raise_reject_before_control_reads(self):
        for target, mode in [('A', 'HoldPose'), ('B', 'HoldLoop'), ('B', 'Raise')]:
            with self.subTest(target=target, mode=mode):
                context, rig, old = self.setup_capture(target=target, mode=mode)
                with self.assertRaises(ValueError):
                    self.capture(context, rig)
                self.assertEqual(self.reads, [])
                self.assertEqual(self.bag.fcurves, old)

    def test_bool_and_nonfinite_control_reject_before_curve_mutation(self):
        for invalid in (True, math.nan, math.inf):
            with self.subTest(invalid=invalid):
                context, rig, old = self.setup_capture(values={'owned.rotation': [0.5, invalid]})
                with self.assertRaisesRegex(ValueError, 'finite B-owned'):
                    self.capture(context, rig)
                self.assertEqual(self.bag.fcurves, old)

    def test_partial_curve_failure_restores_exact_previous_curve_payload(self):
        context, rig, old = self.setup_capture(fail=True)
        with self.assertRaisesRegex(RuntimeError, 'second-channel'):
            self.capture(context, rig)
        self.assertEqual(self.bag.fcurves, old)
        self.assertEqual(self.updates, [])


class SyncEntryTests(unittest.TestCase):
    def setup_sync(self, *, mode='HoldPose', target='B', selected_id='owned', active=True, capture_error=None):
        self.events = []
        rig = object()
        item = NS(rig=rig, item_id='owned')
        session = {'edit_target': target, 'spec': {'timeMode': mode}}

        def capture(context, supplied):
            self.assertIs(supplied, rig)
            self.events.append('capture')
            if capture_error:
                raise capture_error

        def stop(saved, supplied):
            raise PublicationPreflight

        worklist = NS(_idle=lambda context, **kw: None, current=lambda context: item,
                      _check_baseline=lambda supplied: None, state=lambda context: NS(),
                      _fresh_workspace=stop)
        namespace = {
            '_host': lambda module: worklist if module == 'animation_worklist' else NS(),
            'item_spec': lambda supplied: session['spec'],
            'active': lambda supplied: active,
            'validate': lambda context, supplied: (session, NS(item_id=selected_id), None, None),
            'capture_hold_pose': capture,
            'commit': lambda context, supplied: self.events.append('commit'),
        }
        sync = actual_function('animation_composition.py', 'begin_sync', namespace)
        return sync, item

    def test_explicit_active_b_hold_captures_before_commit_and_publication(self):
        sync, item = self.setup_sync()
        with self.assertRaises(PublicationPreflight):
            sync(NS(), item)
        self.assertEqual(self.events, ['capture', 'commit'])

    def test_collection_loop_raise_and_closed_sync_never_auto_capture(self):
        cases = [(True, 'HoldPose', True, ['commit']),
                 (False, 'HoldLoop', True, ['commit']),
                 (False, 'Raise', True, ['commit']),
                 (False, 'HoldPose', False, [])]
        for collection, mode, active, expected in cases:
            with self.subTest(collection=collection, mode=mode, active=active):
                sync, item = self.setup_sync(mode=mode, active=active)
                with self.assertRaises(PublicationPreflight):
                    sync(NS(), item, collection=collection)
                self.assertEqual(self.events, expected)

    def test_edit_a_or_foreign_selected_item_rejects_before_mutation(self):
        for target, selected in [('A', 'owned'), ('B', 'foreign')]:
            with self.subTest(target=target, selected=selected):
                sync, item = self.setup_sync(target=target, selected_id=selected)
                with self.assertRaisesRegex(ValueError, 'Edit B'):
                    sync(NS(), item)
                self.assertEqual(self.events, [])

    def test_capture_failure_prevents_commit_and_publication(self):
        sync, item = self.setup_sync(capture_error=ValueError('Resume Editing'))
        with self.assertRaisesRegex(ValueError, 'Resume Editing'):
            sync(NS(), item)
        self.assertEqual(self.events, ['capture'])


class PollTests(unittest.TestCase):
    def poll(self, owner, *, target='B', mode='HoldPose', tweak=True, active=True,
             selected_id='owned', same_rig=True, load_error=None, idle=True):
        rig = NS(animation_data=NS(use_tweak_mode=tweak))
        saved = NS(rig=rig)
        item = NS(item_id=selected_id, rig=rig if same_rig else object(), composition_json='sealed')
        session = {'edit_target': target, 'spec': {'timeMode': mode},
                   'edit_item_id': 'owned', 'rig': rig}

        def load(supplied):
            self.assertIs(supplied, rig)
            if load_error:
                raise load_error
            return session

        namespace = {'_state': lambda context: saved, '_selected': lambda context: item,
                     '_idle': lambda context: idle,
                     '_backend': lambda: NS(active=lambda supplied: active, _load=load)}
        fn = actual_function('animation_composition_ui.py', 'poll', namespace, owner=owner)
        return fn(None, NS())

    def test_save_pose_requires_b_hold_and_live_native_tweak(self):
        owner = 'CHARACTERDESIGNER_OT_composition_capture_hold'
        self.assertTrue(self.poll(owner))
        for changes in ({'target': 'A'}, {'mode': 'HoldLoop'}, {'mode': 'Raise'},
                        {'tweak': False}, {'active': False}, {'idle': False}):
            with self.subTest(changes=changes):
                self.assertFalse(self.poll(owner, **changes))

    def test_sync_b_rejects_a_foreign_item_and_foreign_rig(self):
        owner = 'CHARACTERDESIGNER_OT_composition_sync'
        self.assertTrue(self.poll(owner))
        for changes in ({'target': 'A'}, {'selected_id': 'foreign'}, {'same_rig': False}):
            with self.subTest(changes=changes):
                self.assertFalse(self.poll(owner, **changes))

    def test_closed_composition_sync_keeps_existing_availability(self):
        self.assertTrue(self.poll('CHARACTERDESIGNER_OT_composition_sync', active=False))

    def test_corrupt_recovery_metadata_disables_buttons(self):
        for owner in ('CHARACTERDESIGNER_OT_composition_capture_hold', 'CHARACTERDESIGNER_OT_composition_sync'):
            for error in (ValueError('bad proof'), KeyError('missing'), ReferenceError('removed RNA')):
                with self.subTest(owner=owner, error=error):
                    self.assertFalse(self.poll(owner, load_error=error))

    def test_save_pose_keeps_operator_identity_and_undo(self):
        tree = ast.parse((SOURCE / 'animation_composition_ui.py').read_text(encoding='utf-8'))
        node = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == 'CHARACTERDESIGNER_OT_composition_capture_hold')
        fields = {row.targets[0].id: ast.literal_eval(row.value) for row in node.body
                  if isinstance(row, ast.Assign)}
        self.assertEqual(fields['bl_idname'], 'character_designer.composition_capture_hold')
        self.assertEqual(fields['bl_label'], 'Save Pose')
        self.assertEqual(fields['bl_options'], {'REGISTER', 'UNDO'})


if __name__ == '__main__':
    unittest.main()
