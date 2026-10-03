"""Synchronous pose transfers publish one corrective for the final/rolled-back pose."""
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import body_original_mode as original, bone_collections, bone_display
from character_designer import control_pose_assets as poses, forearm_twist as runtime, limb_ik_fk
import test_forearm_twist_blender as fixtures
from test_forearm_twist_cache_blender import ready, managed


def single_ready():
    f = fixtures.make_fixture('ROLL_DECOUPLED')
    runtime.start_test(bpy.context, f['mesh'], side='L', initial_angle=math.pi / 2)
    runtime.finish_test(bpy.context, confirm=True)
    bone_collections.simplify_body_collections(f['armature'])
    fixtures.pose_target(f, 25.)
    fixtures.assert_runtime_geometry(f, 'Single-side setup')
    return f


def test_nested_batch_publishes_only_final_correction():
    f, _graph = ready()
    obj = f['mesh']
    keys = fixtures.key_snapshot(obj)
    before = fixtures.key_snapshot(obj, names=tuple(r['key'] for r in runtime._records(obj).values()))
    with patch.object(runtime, '_prepare_runtime_records', wraps=runtime._prepare_runtime_records) as prepare:
        with runtime.defer_runtime(bpy.context):
            fixtures.pose_target(f, 14.)
            with runtime.defer_runtime(bpy.context):
                fixtures.pose_target(f, 28.)
                runtime.update_runtime(bpy.context.scene)
            assert runtime._BUSY, 'A nested scope must retain its outer guard'
            fixtures.pose_target(f, 39.)
            assert prepare.call_count == 0, 'Do not calculate intermediate poses'
            assert fixtures.key_snapshot(obj, names=tuple(r['key'] for r in runtime._records(obj).values())) == before
        assert not runtime._BUSY
        assert prepare.call_count == 1, 'Publish and validate the final pose exactly once'
    assert fixtures.key_snapshot(obj) == keys
    fixtures.assert_runtime_geometry(f, 'Nested pose batch final output')


def test_failure_restores_guard_and_cached_invalid_proof_stays_paused():
    f, _graph = ready()
    obj = f['mesh']
    basis = obj.data.shape_keys.reference_key
    index = f['rings'][3][0]
    old = basis.data[index].co.copy()
    keys = fixtures.key_snapshot(obj, names=('ExistingFace', 'ExistingForearm'))
    basis.data[index].co.x += .002
    runtime.update_runtime(bpy.context.scene)
    failure = runtime._ERRORS[obj.name]
    with patch.object(runtime, 'mirror_ring_pairs', wraps=runtime.mirror_ring_pairs) as pairs, \
         patch.object(runtime, '_prepare_runtime_records', wraps=runtime._prepare_runtime_records) as prepare:
        try:
            with runtime.defer_runtime(bpy.context):
                fixtures.pose_target(f, 35.)
                raise ValueError('injected pose transaction failure')
        except ValueError as exc:
            assert str(exc) == 'injected pose transaction failure'
        else:
            raise AssertionError('The caller failure was lost')
        assert not runtime._BUSY and prepare.call_count == 1
        assert pairs.call_count == 0, 'Retain exact failed-proof caching'
        assert runtime._ERRORS[obj.name] == failure and all(key.mute for key in managed(obj))
        basis.data[index].co = old
        with runtime.defer_runtime(bpy.context):
            fixtures.pose_target(f, 38.)
        assert pairs.call_count == 1, 'Direct Basis repair must invalidate a cached failure'
        assert obj.name not in runtime._ERRORS and all(not key.mute for key in managed(obj))
    assert fixtures.key_snapshot(obj, names=('ExistingFace', 'ExistingForearm')) == keys
    fixtures.assert_runtime_geometry(f, 'Repaired inputs after a failed batch')
    runtime._BUSY = True
    try:
        with patch.object(runtime, 'update_runtime', side_effect=AssertionError('Caller owns deferred output')):
            with runtime.defer_runtime(bpy.context):
                assert runtime._BUSY
            assert runtime._BUSY
    finally:
        runtime._BUSY = False
    with patch.object(runtime, 'update_runtime', side_effect=RuntimeError('injected final update failure')):
        try:
            with runtime.defer_runtime(bpy.context):
                pass
        except RuntimeError as exc:
            assert str(exc) == 'injected final update failure'
        else:
            raise AssertionError('Final update failure was hidden')
    assert not runtime._BUSY, 'Even a final update failure must not leave correction paused'


def test_original_switch_and_both_rollbacks_keep_valid_corrective_output():
    f = single_ready()
    rig, obj = f['armature'], f['mesh']
    fixtures.activate_mesh(rig)
    before_channels, before_view = original._channels(rig), bone_display._snapshot(rig)
    before_pose, keys = original._pose(rig), fixtures.key_snapshot(obj)
    with patch.object(runtime, '_prepare_runtime_records', wraps=runtime._prepare_runtime_records) as prepare, \
         patch.object(original, '_verify', side_effect=ValueError('injected enter rollback')):
        try:
            original.enter(bpy.context, rig)
        except ValueError as exc:
            assert str(exc) == 'injected enter rollback'
        else:
            raise AssertionError('Enter rollback was not exercised')
        assert prepare.call_count == 1 and not runtime._BUSY and not original.active(rig)
    assert original._channels(rig) == before_channels
    assert bone_display._snapshot(rig) == before_view
    assert max(poses._difference(before_pose[name], matrix) for name, matrix in original._pose(rig).items()) < 4e-4
    fixtures.assert_runtime_geometry(f, 'Valid corrective after enter rollback')
    with patch.object(runtime, '_prepare_runtime_records', wraps=runtime._prepare_runtime_records) as prepare, \
         patch.object(bone_display, '_completed', wraps=bone_display._completed) as completed:
        original.enter(bpy.context, rig)
        assert prepare.call_count == 1 and not runtime._BUSY
        assert completed.call_args.kwargs == {'update': False}, 'Pose was already validated before display commit'
    # Original intentionally mutes owned Body constraints. Complete rig
    # validation rejects that suspended control graph, as it did before batching;
    # do not weaken validation merely to keep the corrective enabled there.
    assert 'disabled' in runtime._ERRORS[obj.name]
    assert all(key.mute for key in managed(obj))
    pb = rig.pose.bones[f['lower_name']]
    pb.rotation_mode = 'XYZ'
    pb.rotation_euler.x += .04
    limb_ik_fk._update(bpy.context, rig)
    current = original._channels(rig)
    session, view = rig[original.SESSION], bone_display._snapshot(rig)
    wanted = original._pose(rig)
    with patch.object(runtime, '_prepare_runtime_records', wraps=runtime._prepare_runtime_records) as prepare, \
         patch.object(poses, '_match', side_effect=ValueError('injected return rollback')):
        try:
            original.leave(bpy.context, rig)
        except ValueError as exc:
            assert str(exc) == 'injected return rollback'
        else:
            raise AssertionError('Return rollback was not exercised')
        assert prepare.call_count == 1 and not runtime._BUSY and original.active(rig)
    assert original._channels(rig) == current and rig[original.SESSION] == session
    assert bone_display._snapshot(rig) == view
    assert all(con.mute for con, _entry in original._resolve(rig, json.loads(session)['constraints']))
    assert 'disabled' in runtime._ERRORS[obj.name]
    assert all(key.mute for key in managed(obj))
    assert fixtures.key_snapshot(obj) == keys
    with patch.object(runtime, '_prepare_runtime_records', wraps=runtime._prepare_runtime_records) as prepare, \
         patch.object(bone_display, '_completed', wraps=bone_display._completed) as completed:
        original.leave(bpy.context, rig)
        assert prepare.call_count == 1 and not runtime._BUSY
        assert completed.call_args.kwargs == {'update': False}
    assert max(poses._difference(wanted[name], matrix) for name, matrix in original._pose(rig).items()) < 4e-4
    assert fixtures.key_snapshot(obj) == keys
    fixtures.assert_runtime_geometry(f, 'Valid corrective after authored Controls return')


if __name__ == '__main__':
    count = 0
    for name, test in tuple(globals().items()):
        if name.startswith('test_'):
            test()
            count += 1
            print('PASS', name, flush=True)
    print('FOREARM_RUNTIME_BATCH_PASS', count, flush=True)
