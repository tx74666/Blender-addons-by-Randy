"""Forearm cached preflight stays exact across direct RNA and runtime edits."""
import math
import os
import sys
from unittest.mock import patch

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import forearm_twist as runtime
from character_designer import forearm_twist_cache as cache
import test_forearm_twist_blender as fixtures
from test_forearm_twist_ranges_blender import bilateral_fixture


def ready():
    fixture = bilateral_fixture()
    obj = fixture['mesh']
    runtime.start_test(bpy.context, obj, symmetry=False, initial_angle=math.pi / 2)
    runtime.finish_test(bpy.context, confirm=True)
    runtime.mirror_calibration(bpy.context, obj, 'L')
    fixture['target'].rotation_mode = 'XYZ'
    graph = bpy.context.evaluated_depsgraph_get()
    runtime.update_runtime(bpy.context.scene, graph)
    assert not runtime._ERRORS, runtime._ERRORS
    return fixture, graph


def managed(obj):
    return [obj.data.shape_keys.key_blocks[r['key']] for r in runtime._records(obj).values()]


def test_warm_and_unrelated_updates_skip_heavy_geometry_proofs():
    f, graph = ready()
    unrelated = bpy.data.objects.new('Unrelated cache probe', None)
    bpy.context.collection.objects.link(unrelated)
    bpy.context.view_layer.update()
    runtime.update_runtime(bpy.context.scene, graph)
    with patch.object(runtime, '_topology_digest', wraps=runtime._topology_digest) as topology, \
         patch.object(runtime, 'mirror_ring_pairs', wraps=runtime.mirror_ring_pairs) as pairs, \
         patch.object(runtime.limb_ik, '_validate_inventory', wraps=runtime.limb_ik._validate_inventory) as inventory:
        for _ in range(3):
            runtime.update_runtime(bpy.context.scene, graph)
        assert topology.call_count == pairs.call_count == 0
        assert inventory.call_count == 3, 'Keep one complete rig safety validation per update'
        unrelated.location.x += 1
        bpy.context.view_layer.update()
        assert topology.call_count == pairs.call_count == 0
    with patch.object(runtime, '_topology_digest', wraps=runtime._topology_digest) as topology, \
         patch.object(runtime, 'mirror_ring_pairs', wraps=runtime.mirror_ring_pairs) as pairs, \
         patch.object(runtime, 'corrected_vertex', wraps=runtime.corrected_vertex) as corrected:
        fixtures.pose_target(f, 35.)
        runtime.update_runtime(bpy.context.scene, graph)
        assert corrected.call_count > 0
        assert topology.call_count == pairs.call_count == 0, 'Pose is not a geometry-proof input'
    fixtures.assert_runtime_geometry(f, 'Cached preflight still computes a changed pose')


def test_same_count_topology_edits_invalidate_without_graph_notification():
    f, graph = ready()
    obj = f['mesh']
    edge = obj.data.edges[0]
    old = tuple(edge.vertices)
    with patch.object(runtime, '_topology_digest', wraps=runtime._topology_digest) as topology:
        # Same counts and same undirected edge, but changed exact saved topology.
        # No update_tag()/view_layer.update(): direct callers must also be safe.
        edge.vertices = old[::-1]
        runtime.update_runtime(bpy.context.scene, graph)
        assert topology.call_count == 1
        assert 'topology' in runtime._ERRORS[obj.name].lower()
        assert all(key.mute for key in managed(obj))
        edge.vertices = old
        runtime.update_runtime(bpy.context.scene, graph)
        assert topology.call_count == 2
        assert obj.name not in runtime._ERRORS
        assert all(not key.mute for key in managed(obj))


def test_basis_record_and_frame_changes_recheck_symmetry():
    f, graph = ready()
    obj, arm = f['mesh'], f['armature']
    basis = obj.data.shape_keys.reference_key
    index = f['rings'][3][0]
    old = basis.data[index].co.copy()
    with patch.object(runtime, 'mirror_ring_pairs', wraps=runtime.mirror_ring_pairs) as pairs:
        basis.data[index].co.x += .002
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count == 1 and obj.name in runtime._ERRORS
        assert all(key.mute for key in managed(obj))
        basis.data[index].co = old
        runtime.update_runtime(bpy.context.scene, graph)
        assert obj.name not in runtime._ERRORS
        records = runtime._records(obj)
        records['L']['rings'][3]['ratio'] = .71
        runtime._write_records(obj, records)
        before = pairs.call_count
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count > before and obj.name not in runtime._ERRORS
        matrix = arm.matrix_world.copy()
        arm.location.x += .1
        bpy.context.view_layer.update()
        runtime.update_runtime(bpy.context.scene, graph)
        assert obj.name in runtime._ERRORS and all(key.mute for key in managed(obj))
        arm.matrix_world = matrix
        bpy.context.view_layer.update()
        runtime.update_runtime(bpy.context.scene, graph)
        assert obj.name not in runtime._ERRORS


def test_repeated_invalid_basis_stays_paused_and_direct_repair_recovers():
    f, graph = ready()
    obj = f['mesh']
    basis = obj.data.shape_keys.reference_key
    index = f['rings'][3][0]
    old = basis.data[index].co.copy()
    artist_keys = fixtures.key_snapshot(obj, names=('ExistingFace', 'ExistingForearm'))
    unrelated = bpy.data.objects.new('Unrelated failed-proof probe', None)
    bpy.context.collection.objects.link(unrelated)
    bpy.context.view_layer.update()
    with patch.object(runtime, 'mirror_ring_pairs', wraps=runtime.mirror_ring_pairs) as pairs, \
         patch.object(runtime.limb_ik, '_validate_inventory', wraps=runtime.limb_ik._validate_inventory) as inventory:
        basis.data[index].co.x += .002
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count == 1 and obj.name in runtime._ERRORS
        failure = runtime._ERRORS[obj.name]
        assert all(key.mute for key in managed(obj))
        before_inventory = inventory.call_count
        for _ in range(3):
            runtime.update_runtime(bpy.context.scene, graph)
        assert inventory.call_count == before_inventory + 3, 'A cached failure must retain rig safety validation'
        unrelated.location.x += 1
        bpy.context.view_layer.update()
        fixtures.pose_target(f, 35.)
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count == 1, 'An unchanged failed Basis proof must not rescan the Mesh'
        assert runtime._ERRORS[obj.name] == failure
        assert all(key.mute for key in managed(obj))
        assert fixtures.key_snapshot(obj, names=('ExistingFace', 'ExistingForearm')) == artist_keys
        # Direct same-count repairs have no notification requirement: the exact
        # Basis fingerprint must replace the failed proof and resume correction.
        basis.data[index].co = old
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count == 2 and obj.name not in runtime._ERRORS
        assert all(not key.mute for key in managed(obj))
    fixtures.assert_runtime_geometry(f, 'Direct Basis repair after a cached failed proof')


def test_failed_proof_rechecks_changed_frames_rings_and_chain_rest():
    f, _graph = ready()
    obj, arm = f['mesh'], f['armature']
    records = runtime._records(obj)
    source, target = records['L'], records['R']
    basis = obj.data.shape_keys.reference_key
    index = f['rings'][3][0]
    old = basis.data[index].co.copy()
    matrix = arm.matrix_world.copy()
    head = arm.data.bones[source['chain'][0]].head_local.copy()
    busy = runtime._BUSY
    runtime._BUSY = True
    try:
        basis.data[index].co.x += .002
        with patch.object(runtime, 'mirror_ring_pairs', wraps=runtime.mirror_ring_pairs) as pairs:
            def refused():
                try:
                    cache.mirror_pairs(obj, arm, source, target, runtime.mirror_ring_pairs)
                except ValueError as exc:
                    return exc
                raise AssertionError('The invalid mirror proof was accepted')

            first = refused()
            repeated = refused()
            assert pairs.call_count == 1 and type(repeated) is type(first) and repeated.args == first.args
            assert repeated is not first, 'Repeat a failed proof with a fresh exception'
            arm.location.x += .1
            bpy.context.view_layer.update()
            refused()
            assert pairs.call_count == 2, 'A changed armature frame must recheck a failed proof'
            arm.matrix_world = matrix
            bpy.context.view_layer.update()
            position = source['rings'][3]['position']
            source['rings'][3]['position'] += .01
            refused()
            assert pairs.call_count == 3, 'Changed captured ring positions must recheck a failed proof'
            source['rings'][3]['position'] = position
            fixtures.activate_mesh(arm)
            bpy.ops.object.mode_set(mode='EDIT')
            arm.data.edit_bones[source['chain'][0]].head.x += .001
            bpy.ops.object.mode_set(mode='OBJECT')
            refused()
            assert pairs.call_count == 4, 'Changed chain Rest endpoints must recheck a failed proof'
            bpy.ops.object.mode_set(mode='EDIT')
            arm.data.edit_bones[source['chain'][0]].head = head
            bpy.ops.object.mode_set(mode='OBJECT')
            fixtures.activate_mesh(obj)
            basis.data[index].co = old
            result = cache.mirror_pairs(obj, arm, source, target, runtime.mirror_ring_pairs)
            assert pairs.call_count == 5 and len(result) == len(source['rings'])
            assert cache.mirror_pairs(obj, arm, source, target, runtime.mirror_ring_pairs) == result
            assert pairs.call_count == 5, 'A repaired proof must resume ordinary success caching'
    finally:
        if arm.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        arm.matrix_world = matrix
        basis.data[index].co = old
        fixtures.activate_mesh(obj)
        runtime._BUSY = busy


def test_failed_proof_is_rechecked_after_undo_and_unload():
    f, graph = ready()
    obj = f['mesh']
    basis = obj.data.shape_keys.reference_key
    index = f['rings'][3][0]
    old = basis.data[index].co.copy()
    with patch.object(runtime, 'mirror_ring_pairs', wraps=runtime.mirror_ring_pairs) as pairs:
        basis.data[index].co.x += .002
        runtime.update_runtime(bpy.context.scene, graph)
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count == 1 and all(key.mute for key in managed(obj))
        runtime._undo_post(None)
        assert pairs.call_count == 2, 'Undo must discard the old failed proof and validate again'
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count == 2 and obj.name in runtime._ERRORS
        runtime.unregister_forearm_twist_runtime()
        assert not cache._TOPOLOGY and not cache._MIRROR
        runtime.register_forearm_twist_runtime()
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count == 3 and obj.name in runtime._ERRORS
        basis.data[index].co = old
        runtime.update_runtime(bpy.context.scene, graph)
        assert pairs.call_count == 4 and obj.name not in runtime._ERRORS
        assert all(not key.mute for key in managed(obj))
    fixtures.assert_runtime_geometry(f, 'Repair after failed-proof Undo and runtime reload')


def test_weights_artist_keys_output_and_disable_remain_live():
    f, graph = ready()
    obj = f['mesh']
    fixtures.pose_target(f, 32.)
    index = f['rings'][3][0]
    before = managed(obj)[0].data[index].co.copy()
    obj.vertex_groups[f['lower_name']].add([index], .15, 'REPLACE')
    obj.vertex_groups[f['hand_name']].add([index], .85, 'REPLACE')
    runtime.update_runtime(bpy.context.scene, graph)
    assert (managed(obj)[0].data[index].co - before).length > 1e-6
    fixtures.assert_runtime_geometry(f, 'Weights changed after cached preflight')
    obj.data.shape_keys.key_blocks['ExistingForearm'].value = .65
    bpy.context.view_layer.update()
    fixtures.assert_runtime_geometry(f, 'Artist key changed after cached preflight')
    key = managed(obj)[0]
    expected = key.data[index].co.copy()
    key.data[index].co.x += .05
    key.value = .3
    key.mute = True
    runtime.update_runtime(bpy.context.scene, graph)
    assert (key.data[index].co - expected).length < 1e-6 and key.value == 1 and not key.mute
    obj.modifiers[0].use_deform_preserve_volume = True
    runtime.update_runtime(bpy.context.scene, graph)
    assert obj.name in runtime._ERRORS and all(key.mute for key in managed(obj))
    obj.modifiers[0].use_deform_preserve_volume = False
    runtime.update_runtime(bpy.context.scene, graph)
    assert obj.name not in runtime._ERRORS
    runtime.toggle_paired_calibration(bpy.context, obj)
    assert all(key.mute for key in managed(obj))
    runtime.update_runtime(bpy.context.scene, graph)
    with patch.object(runtime, '_topology_digest', wraps=runtime._topology_digest) as topology, \
         patch.object(runtime, 'mirror_ring_pairs', wraps=runtime.mirror_ring_pairs) as pairs:
        runtime.update_runtime(bpy.context.scene, graph)
        assert topology.call_count == pairs.call_count == 0
        assert all(key.mute for key in managed(obj))
    runtime.toggle_paired_calibration(bpy.context, obj)
    fixtures.assert_runtime_geometry(f, 'Enable restores live correction')
    assert cache._TOPOLOGY and cache._MIRROR
    runtime._undo_post(None)
    assert obj.name not in runtime._ERRORS
    runtime.unregister_forearm_twist_runtime()
    assert not cache._TOPOLOGY and not cache._MIRROR
    runtime.register_forearm_twist_runtime()


if __name__ == '__main__':
    for name, test in tuple(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FOREARM_CACHE_PASS', flush=True)
