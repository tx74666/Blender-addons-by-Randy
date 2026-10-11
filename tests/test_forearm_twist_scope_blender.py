"""Support proof migration and Setup repair in disposable Blender fixtures.

Run Blender --background --factory-startup --python-exit-code 1 --python
tests/test_forearm_twist_scope_blender.py. Production scenes are never opened.
"""
import os
import sys
from unittest.mock import patch

import bpy
import bmesh
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import forearm_twist as runtime, unity_forearm
import test_forearm_twist_blender as fixtures
from test_forearm_twist_setup_blender import ui_settings


def ready():
    f = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False)
    runtime.start_test(bpy.context, f['mesh'])
    runtime.set_ratio(bpy.context, 3, .29)
    runtime.finish_test(bpy.context, True)
    fixtures.pose_target(f, 45.)
    runtime.update_runtime(bpy.context.scene)
    fixtures.assert_runtime_geometry(f, 'Scope fixture')
    ui_settings()
    return f


def output_snapshot(obj):
    return {r['key']: (obj.data.shape_keys.key_blocks[r['key']].as_pointer(),
                       obj.data.shape_keys.key_blocks[r['key']].value,
                       obj.data.shape_keys.key_blocks[r['key']].mute,
                       tuple(tuple(p.co) for p in obj.data.shape_keys.key_blocks[r['key']].data))
            for r in runtime._records(obj).values()}


def outside_edge(obj):
    support = set(runtime._records(obj)['L']['vertices'])
    return next(edge for edge in obj.data.edges if not support.intersection(edge.vertices))


def artist_prefix(obj, count):
    return {name: (key.value, key.mute, key.relative_key.name,
                   tuple(tuple(p.co) for p in key.data[:count]))
            for name, key in obj.data.shape_keys.key_blocks.items()
            if name in ('Basis', 'ExistingFace', 'ExistingForearm')}


def test_healthy_legacy_backfill_changes_only_proof_metadata():
    f = ready()
    obj = f['mesh']
    old = runtime._records(obj)
    old['L'].pop('support_topology')
    runtime._write_records(obj, old)
    before_keys = fixtures.key_snapshot(obj)
    before_outputs = output_snapshot(obj)
    runtime.update_runtime(bpy.context.scene)
    current = runtime._records(obj)
    assert 'support_topology' in current['L']
    current['L'].pop('support_topology')
    assert current == old
    assert fixtures.key_snapshot(obj) == before_keys and output_snapshot(obj) == before_outputs


def test_unrelated_same_count_change_retains_profile_and_owned_keys():
    f = ready()
    obj, arm = f['mesh'], f['armature']
    old = runtime._records(obj)
    before_pose = fixtures.pose_snapshot(arm)
    before_keys, before_outputs = fixtures.key_snapshot(obj), output_snapshot(obj)
    edge = outside_edge(obj)
    edge.vertices = tuple(edge.vertices)[::-1]
    runtime.update_runtime(bpy.context.scene)
    current = runtime._records(obj)
    assert current['L']['topology'] != old['L']['topology']
    current['L']['topology'] = old['L']['topology']
    assert current == old
    assert fixtures.key_snapshot(obj) == before_keys and output_snapshot(obj) == before_outputs
    fixtures.assert_pose_snapshot(arm, before_pose, 'Unrelated topology migration')
    fixtures.assert_runtime_geometry(f, 'Unrelated same-count topology migration')


def test_unrelated_added_vertices_accept_proven_support_without_artist_key_writes():
    f = ready()
    obj, arm = f['mesh'], f['armature']
    old = runtime._records(obj)
    count = len(obj.data.vertices)
    before_keys = artist_prefix(obj, count)
    before_pose = fixtures.pose_snapshot(arm)
    previous_busy = runtime._BUSY
    runtime._BUSY = True
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        mesh = bmesh.from_edit_mesh(obj.data)
        vertices = [mesh.verts.new(Vector(point)) for point in ((0, 0, .1), (.03, 0, .1), (0, .03, .1))]
        mesh.faces.new(vertices)
        bmesh.update_edit_mesh(obj.data)
        bpy.ops.object.mode_set(mode='OBJECT')
    finally:
        if obj.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        runtime._BUSY = previous_busy
    assert len(obj.data.vertices) == count + 3
    runtime.update_runtime(bpy.context.scene)
    current = runtime._records(obj)
    assert current['L']['support_topology'] == old['L']['support_topology']
    assert current['L']['rings'] == old['L']['rings']
    assert artist_prefix(obj, count) == before_keys
    fixtures.assert_pose_snapshot(arm, before_pose, 'Added unrelated geometry')
    fixtures.assert_runtime_geometry(f, 'Added unrelated geometry')


def test_changed_local_connectivity_refuses_before_output_or_artist_mutation():
    f = ready()
    obj = f['mesh']
    old_json, before_outputs = obj[runtime.RECORD_KEY], output_snapshot(obj)
    before_pose, before_keys = fixtures.pose_snapshot(f['armature']), fixtures.key_snapshot(obj)
    edge = next(edge for edge in obj.data.edges
                if set(edge.vertices).intersection(runtime._records(obj)['L']['vertices']))
    edge.vertices = tuple(edge.vertices)[::-1]
    runtime.update_runtime(bpy.context.scene)
    assert obj[runtime.RECORD_KEY] == old_json and obj.name in runtime._ERRORS
    assert 'support' in runtime._ERRORS[obj.name]
    for name, snapshot in output_snapshot(obj).items():
        assert snapshot[:2] == before_outputs[name][:2] and snapshot[3] == before_outputs[name][3]
        assert snapshot[2], 'Failed local proof must pause only the owned output'
    assert fixtures.key_snapshot(obj) == before_keys
    fixtures.assert_pose_snapshot(f['armature'], before_pose, 'Rejected local topology edit')


def test_stale_legacy_setup_recapture_is_cancellable_and_reuses_owned_key():
    f = ready()
    obj = f['mesh']
    records = runtime._records(obj)
    records['L'].pop('support_topology')
    runtime._write_records(obj, records)
    edge = outside_edge(obj)
    edge.vertices = tuple(edge.vertices)[::-1]
    runtime.update_runtime(bpy.context.scene)
    assert obj.name in runtime._ERRORS
    stale_json = obj[runtime.RECORD_KEY]
    before_outputs, before_keys = output_snapshot(obj), fixtures.key_snapshot(obj)
    before_pose = fixtures.pose_snapshot(f['armature'])
    old_name = records['L']['key']
    fresh = runtime.start_paired_test(bpy.context, repair_stale=True)
    assert fresh['key'] == old_name and fresh['support_topology']
    assert obj.data.shape_keys.key_blocks[old_name].as_pointer() == before_outputs[old_name][0]
    fixtures.assert_pose_snapshot(f['armature'], before_pose, 'Setup repair preview')
    assert fixtures.key_snapshot(obj) == before_keys
    runtime.finish_test(bpy.context, False, refresh=False)
    assert obj[runtime.RECORD_KEY] == stale_json and output_snapshot(obj) == before_outputs
    assert fixtures.key_snapshot(obj) == before_keys
    fixtures.assert_pose_snapshot(f['armature'], before_pose, 'Setup repair cancel')


def test_failed_migration_restores_old_metadata_and_every_output():
    f = ready()
    obj = f['mesh']
    before_json, before_outputs = obj[runtime.RECORD_KEY], output_snapshot(obj)
    before_keys = fixtures.key_snapshot(obj)
    edge = outside_edge(obj)
    edge.vertices = tuple(edge.vertices)[::-1]
    prepared = runtime._prepare_runtime_records(obj)
    assert prepared['L']['topology'] != runtime._records(obj)['L']['topology']
    with patch.object(runtime, 'corrected_vertex', side_effect=RuntimeError('injected scoped migration failure')):
        try:
            runtime._apply_runtime_records(obj, prepared, bpy.context.evaluated_depsgraph_get())
        except RuntimeError as exc:
            assert 'injected scoped migration failure' in str(exc)
        else:
            raise AssertionError('Failure injection did not reach the output transaction')
    assert obj[runtime.RECORD_KEY] == before_json and output_snapshot(obj) == before_outputs
    assert fixtures.key_snapshot(obj) == before_keys


def test_export_accepts_proven_unrelated_edit_without_committing_metadata():
    f = ready()
    obj = f['mesh']
    before_json, before_outputs = obj[runtime.RECORD_KEY], output_snapshot(obj)
    before_keys, before_pose = fixtures.key_snapshot(obj), fixtures.pose_snapshot(f['armature'])
    edge = outside_edge(obj)
    edge.vertices = tuple(edge.vertices)[::-1]
    with patch.object(runtime, '_write_records', side_effect=AssertionError('Capture must not commit metadata')):
        payload = unity_forearm.capture(obj)
    assert payload['sourceTopology'] == runtime._topology(obj.data)
    assert payload['sources'] and payload['sides'][0]['lowerBone'] == f['lower_name']
    assert obj[runtime.RECORD_KEY] == before_json and output_snapshot(obj) == before_outputs
    assert fixtures.key_snapshot(obj) == before_keys
    fixtures.assert_pose_snapshot(f['armature'], before_pose, 'Read-only scoped export capture')


def test_export_refuses_stale_legacy_and_changed_support_without_mutation():
    for legacy in (True, False):
        f = ready()
        obj = f['mesh']
        if legacy:
            records = runtime._records(obj)
            records['L'].pop('support_topology')
            runtime._write_records(obj, records)
            edge = outside_edge(obj)
        else:
            support = set(runtime._records(obj)['L']['vertices'])
            edge = next(edge for edge in obj.data.edges if support.intersection(edge.vertices))
        edge.vertices = tuple(edge.vertices)[::-1]
        before_json, before_outputs = obj[runtime.RECORD_KEY], output_snapshot(obj)
        before_keys = fixtures.key_snapshot(obj)
        fixtures.assert_refused(lambda: unity_forearm.capture(obj), 'Unproved export support')
        assert obj[runtime.RECORD_KEY] == before_json and output_snapshot(obj) == before_outputs
        assert fixtures.key_snapshot(obj) == before_keys


def test_later_export_rejection_preserves_topology_metadata_and_artist_keys():
    f = ready()
    obj = f['mesh']
    edge = outside_edge(obj)
    edge.vertices = tuple(edge.vertices)[::-1]
    before_json, before_outputs = obj[runtime.RECORD_KEY], output_snapshot(obj)
    before_keys = fixtures.key_snapshot(obj)
    obj.modifiers.new('Unsupported export test', 'SOLIDIFY')
    fixtures.assert_refused(lambda: unity_forearm.capture(obj), 'Unsupported post-Armature modifier')
    assert obj[runtime.RECORD_KEY] == before_json and output_snapshot(obj) == before_outputs
    assert fixtures.key_snapshot(obj) == before_keys


if __name__ == '__main__':
    for name, test in tuple(globals().items()):
        if name.startswith('test_'):
            try:
                test()
                print('PASS', name, flush=True)
            finally:
                if runtime._SESSION is not None:
                    runtime.finish_test(bpy.context, False)
    print('FOREARM_SCOPE_PASS', flush=True)
