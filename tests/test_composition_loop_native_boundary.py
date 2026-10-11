"""Actual AST-extracted tool functions, fake C boundary; no Blender import/run."""
import ast
import copy
import json
import math
import sys
import traceback
from contextlib import contextmanager
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from test_composition_loop_operator_guards import (
    SOURCE, Basis, Context, DataBones, Pose, Poses, actual_helpers, selection_fixture,
)


class RotationPose(Pose):
    rotation_mode = 'XYZ'

    @property
    def rotation_euler(self):
        return self.native['values']

    @rotation_euler.setter
    def rotation_euler(self, value):
        self.native['values'] = list(value)

    @property
    def matrix_basis(self):
        angle = self.rotation_euler[0]
        return Basis((math.cos(angle / 2), math.sin(angle / 2), 0., 0.))

    def path_from_id(self):
        return 'pose.bones["' + self.name + '"]'

    def keyframe_insert(self, **kwargs):
        self.native['keys'].append(NS(co=(kwargs['frame'], self.rotation_euler[0])))
        return True


class RotationPoses(Poses):
    def __iter__(self):
        return iter(RotationPose(native) for native in self.native.values())

    def __getitem__(self, name):
        return RotationPose(self.native[name])


def fixture(background, *, result=None, zero_delta=False, interrupted=False):
    namespace = actual_helpers()
    namespace.update(json=json, traceback=traceback)
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_author_loop')
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    context, rig, session, tracks, backend = selection_fixture()
    poses = RotationPoses(('FK', 'OTHER'))
    for native in poses.native.values():
        native.update(values=[0., 0., 0.], keys=[])
    rig.pose.bones, rig.data.bones = poses, DataBones(poses)
    context = Context(rig, context.scene)
    context.view_layer.update = lambda: None
    rig.animation_data.nla_tweak_strip_time_to_scene = lambda frame: frame
    session.update(spec={'editSpeed': 1., 'fps': 30.}, base_action='Readonly A', base_slot=1,
                   packet_sha256='packet')
    tracks['EDIT'].strips[0].frame_start = 1.
    scope = NS(input_by_native={'Native': 'FK'},
               channels=tuple(('pose.bones["FK"].rotation_euler', i) for i in range(3)))
    row = NS(rig=rig, item_id='Loop', custom_action='Custom', custom_slot=7,
             source_action='Source', source_slot=1, new_animation=False, new_action_id='')
    state = {'committed': False, 'calls': [], 'moving_checks': 0}
    backend._motion_hash = lambda action, _slot: 'after' if action == 'Custom' and state['committed'] else 'before'
    backend._frame_range = lambda _spec: (1., 31.)
    backend.validate = lambda *_args: None
    backend.commit = lambda *_args: state.update(committed=True) or True
    backend._packet_for_item = lambda _row: {'_sha256': 'packet'}
    backend.item_spec = lambda _row: session['spec']
    backend._prior = lambda *_args: {}
    backend.active = lambda _rig: False
    backend._curves = lambda *_args: [NS(data_path='pose.bones["FK"].rotation_euler',
                                         keyframe_points=poses.native['FK']['keys'])]

    @contextmanager
    def opened(*_args):
        yield session, row, scope, tracks

    @contextmanager
    def view(*_args):
        yield context

    class Rotate:
        def poll(self):
            return True

        def __call__(self, *args, **kwargs):
            state['calls'].append((args, kwargs))
            if interrupted:
                raise SystemExit('fake nonreturning C boundary')
            if not zero_delta:
                rig.pose.bones['FK'].rotation_euler = (.073, 0., 0.)
            return {'FINISHED'} if result is None else result

    modules = {'animation_composition': backend,
               'unity_animation': NS(_set_frame=lambda *_args: None)}
    namespace.update(_open=opened, _assert_moving=lambda *_args: state.update(
        moving_checks=state['moving_checks'] + 1))
    imports = {'bpy': NS(app=NS(background=background), ops=NS(transform=NS(rotate=Rotate()))),
               'composition_session_acceptance': NS(_pose_hidden=lambda _bone: False, _view3d=view)}
    return namespace, context, row, modules, state, imports


class NativeBoundary(unittest.TestCase):
    def call(self, background, **kwargs):
        namespace, context, row, modules, state, imports = fixture(background, **kwargs)
        diagnostic, snapshots = {}, []
        with patch.dict(sys.modules, imports):
            result = namespace['_author_loop'](context, row, modules, None,
                diagnostics=diagnostic, checkpoint=lambda: snapshots.append(copy.deepcopy(diagnostic)))
        return result, diagnostic, snapshots, state

    def test_background_real_exec_has_no_undo_registration(self):
        result, diagnostic, snapshots, state = self.call(True)
        self.assertEqual(state['calls'][0][0], ('EXEC_DEFAULT', False))
        self.assertTrue(result['native_FK_transform_and_keys'])
        self.assertEqual(state['moving_checks'], 1)
        self.assertTrue(all(frame['actualundo_requested'] is False for frame in diagnostic['frames']))
        self.assertTrue(snapshots[0]['pendingNativeOperator'])
        self.assertEqual(snapshots[0]['checkpoint_stage'], 'before_native_rotate')
        self.assertNotIn('result', snapshots[0]['frames'][2]['operator'])
        self.assertFalse(snapshots[1]['pendingNativeOperator'])
        self.assertEqual(snapshots[1]['frames'][2]['operator']['result'], ['FINISHED'])

    def test_headful_keeps_original_undo_true(self):
        _result, diagnostic, _snapshots, state = self.call(False)
        self.assertEqual(state['calls'][0][0], ('EXEC_DEFAULT', True))
        self.assertTrue(all(frame['actualundo_requested'] is True for frame in diagnostic['frames']))

    def test_callback_failure_prevents_C_call(self):
        namespace, context, row, modules, state, imports = fixture(True)
        def fail():
            raise OSError('checkpoint unavailable')
        with patch.dict(sys.modules, imports), self.assertRaisesRegex(OSError, 'checkpoint unavailable'):
            namespace['_author_loop'](context, row, modules, None, checkpoint=fail)
        self.assertEqual(state['calls'], [])
        self.assertFalse(state['committed'])

    def test_cancelled_result_never_marks_verified_or_commits(self):
        namespace, context, row, modules, state, imports = fixture(True, result={'CANCELLED'})
        diagnostic, snapshots = {}, []
        with patch.dict(sys.modules, imports), self.assertRaisesRegex(AssertionError, 'did not finish'):
            namespace['_author_loop'](context, row, modules, None, diagnostics=diagnostic,
                checkpoint=lambda: snapshots.append(copy.deepcopy(diagnostic)))
        self.assertEqual(len(snapshots), 1)
        self.assertTrue(diagnostic['pendingNativeOperator'])
        self.assertEqual(diagnostic['frames'][2]['operator']['result'], ['CANCELLED'])
        self.assertFalse(state['committed'])

    def test_finished_with_zero_delta_does_not_clear_pending(self):
        namespace, context, row, modules, state, imports = fixture(True, zero_delta=True)
        diagnostic, snapshots = {}, []
        with patch.dict(sys.modules, imports), self.assertRaisesRegex(AssertionError, 'no actual rotation'):
            namespace['_author_loop'](context, row, modules, None, diagnostics=diagnostic,
                checkpoint=lambda: snapshots.append(copy.deepcopy(diagnostic)))
        self.assertEqual(len(snapshots), 1)
        self.assertTrue(diagnostic['pendingNativeOperator'])
        self.assertFalse(state['committed'])

    def test_nonboolean_background_state_fails_before_C(self):
        namespace, context, row, modules, state, imports = fixture(1)
        with patch.dict(sys.modules, imports), self.assertRaisesRegex(AssertionError, 'exact boolean'):
            namespace['_author_loop'](context, row, modules, None)
        self.assertEqual(state['calls'], [])

    def test_report_owned_checkpoint_survives_nonreturning_boundary(self):
        namespace, context, row, modules, state, imports = fixture(True, interrupted=True)
        saved = NS(items=[row], workspace_path='workspace', workspace_identity='id', model_file='model', model_sha256='sha')
        modules['animation_worklist'] = NS(state=lambda _context: saved)
        captured = []
        class FakePath:
            def __init__(self, _value): pass
            def resolve(self): return self
            def is_dir(self): return True
            def __truediv__(self, _name): return self
            def is_relative_to(self, _directory): return True
            def __str__(self): return 'owned/report.json'
        namespace.update(Path=FakePath, validate_author_config=lambda value: value,
            _modules=lambda: modules, _row=lambda *_args: row, _sealed_loop=lambda _spec: None,
            _write=lambda _path, report: captured.append(copy.deepcopy(report)))
        config = {'evidenceDirectory': 'owned', 'holdLoopWorkItemId': 'actual',
                  'expectedModelSha256': 'actual', 'holdLoopRotationControl': None}
        with patch.dict(sys.modules, imports), self.assertRaisesRegex(SystemExit, 'nonreturning'):
            namespace['author_hold_loop'](context, config)
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0]['complete'])
        self.assertTrue(captured[0]['pendingNativeOperator'])
        self.assertEqual(captured[0]['checkpoint_stage'], 'before_native_rotate')
        self.assertEqual(captured[0]['checkpoint_phase'], 'author_actual_HoldLoop_Custom')
        self.assertNotIn('failure_phase', captured[0])
        self.assertEqual(captured[0]['operator_diagnostics']['frames'][2]['operator']['actualundo_requested'], False)
        self.assertNotIn('result', captured[0]['operator_diagnostics']['frames'][2]['operator'])
        self.assertFalse(state['committed'])


if __name__ == '__main__':
    unittest.main()
