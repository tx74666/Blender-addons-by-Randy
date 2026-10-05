"""Public Hair remove/rebind identity checks in disposable native scenes.

Run serially under Blender factory startup. This file never opens an artist
scene and launches no subprocess. Save/reopen uses one temporary test blend.
The public binding workflow, rather than patched UUIDs, must retain identity.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import hair_bones_binding as binding
from character_designer import hair_bones_rig as hair
from character_designer import hair_motion_lifecycle as lifecycle
from character_designer import hair_motion_profiles as profiles
from character_designer import hair_strand_registry as registry
from character_designer import hair_wiggle_adapter as adapter
from test_hair_bones_binding_blender import scene_fixture, bones_snapshot, must_stop
from test_hair_bones_rig_blender import activate
from test_hair_bones_variants_blender import source_state, database_counts


def artist_state(source):
    result = source_state(source)
    owned = {registry.REGISTRY_KEY, profiles.PROFILE_KEY,
             lifecycle.OWNER_KEY, lifecycle.IDENTITY_KEY}
    result['properties'] = tuple(item for item in result['properties'] if item[0] not in owned)
    return result


def identities(record):
    return tuple((item['strand_id'], item['chain_id'], item['order'],
                  item['pair_id'], item['mirror_id'], item['side'], item['pair_proof'])
                 for item in record['strands'])


def state_differences(before, after):
    return {key: (before[key], after[key]) for key in before if before[key] != after[key]}


def bone_differences(before, after):
    first, second = dict(before), dict(after)
    return {name: (first.get(name), second.get(name)) for name in first.keys() | second.keys()
            if first.get(name) != second.get(name)}


def lifecycle_counts():
    return database_counts(), len(bpy.data.texts)


def identity_holder(source):
    holder = source[lifecycle.OWNER_KEY]
    assert isinstance(holder, bpy.types.Text)
    assert holder[lifecycle.HOLDER_SOURCE_KEY] == source
    assert holder[lifecycle.HOLDER_UID_KEY] == json.loads(source[lifecycle.IDENTITY_KEY])['source_uid']
    return holder


def configured_fixture(*, claim=True):
    source, plans, armature = scene_fixture()
    # Animation belongs to artist data, not to generated bones being removed.
    source.keyframe_insert('location', frame=1)
    source.data.shape_keys.key_blocks['Artist Detail'].keyframe_insert('value', frame=1)
    original, old_bones = artist_state(source), bones_snapshot(armature)
    binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
    data = registry.initialize(source)
    if not all(item['mirror_id'] for item in data['strands']):
        first, second = data['strands']
        data = registry.manual_pair(source, first['strand_id'], second['strand_id'])
    profiles.initialize(source, registry=data)
    strand_id = data['strands'][0]['strand_id']
    depth = [
        {'position': 0.0, 'recovery': .91, 'damping': .86, 'mass': .8, 'gravity': 1.4},
        {'position': .37, 'recovery': .72, 'damping': .79, 'mass': 1.3, 'gravity': 2.2},
        {'position': 1.0, 'recovery': .48, 'damping': .64, 'mass': 1.7, 'gravity': 3.8},
    ]
    profiles.update(source, strand_id, group='FRONT',
                    overrides={'recovery': .91, 'damping': .86, 'gravity': 1.4,
                               'stretch': .13, 'depth': depth}, registry=data)
    profiles.apply_to_group(source, strand_id, registry=data)
    profiles.update(source, strand_id, overrides={'stretch': .23}, registry=data)
    # This is the same explicit source initialization hook as the UI. Native
    # binding is still proven; this does not fabricate or replace a source UID.
    if claim:
        lifecycle.claim_bound(source)
    return source, plans, armature, original, old_bones


def test_plain_public_remove_keeps_existing_property_reversibility():
    source, plans, armature = scene_fixture()
    before, native, text_count = source_state(source), bones_snapshot(armature), len(bpy.data.texts)
    binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
    assert lifecycle.OWNER_KEY not in source and lifecycle.IDENTITY_KEY not in source
    binding.remove_hair_binding(bpy.context, source)
    assert source_state(source) == before and bones_snapshot(armature) == native
    assert lifecycle.OWNER_KEY not in source and lifecycle.IDENTITY_KEY not in source
    assert len(bpy.data.texts) == text_count


def test_public_remove_rebind_three_to_five_preserves_strands_settings_and_artist_data():
    source, plans, armature, original, native = configured_fixture()
    mesh, keys = source.data, source.data.shape_keys
    before = registry.read(source)
    settings = profiles.read(source, registry=before)
    effective = profiles.effective_all(source, registry=before)
    sampled = {key: [profiles.depth_sample(value, point) for point in (0., .2, .37, .8, 1.)]
               for key, value in effective.items()}
    uid = before['source_uid']
    holder = identity_holder(source)
    text_count = len(bpy.data.texts)
    raw = (source[registry.REGISTRY_KEY], source[profiles.PROFILE_KEY])
    for count in (5, 2):
        binding.remove_hair_binding(bpy.context, source)
        assert not binding.is_bound(source)
        assert identity_holder(source) == holder
        assert lifecycle.source_uid_for_bind(source) == uid
        assert (source[registry.REGISTRY_KEY], source[profiles.PROFILE_KEY]) == raw
        assert artist_state(source) == original and bones_snapshot(armature) == native
        result = binding.bind_hair(bpy.context, source, plans, bone_count=count, armature=armature)
        assert all(len(chain['bones']) == count for chain in result['chains'])
        assert json.loads(source[hair.RECORD_KEY])['source_id'] == uid
        assert (source[registry.REGISTRY_KEY], source[profiles.PROFILE_KEY]) == raw
        # Old segment Rest is deliberately stale until the explicit refresh.
        must_stop(lambda: registry.read(source), ('rest', 'segmentation'))
        current = registry.reconcile(source)
        refreshed = profiles.reconcile(source, registry=current)
        assert current['source_uid'] == uid and identities(current) == identities(before)
        assert refreshed == settings
        assert profiles.effective_all(source, registry=current) == effective
        assert {key: [profiles.depth_sample(value, point) for point in (0., .2, .37, .8, 1.)]
                for key, value in profiles.effective_all(source, registry=current).items()} == sampled
        assert source.data == mesh and source.data.shape_keys == keys
        assert identity_holder(source) == holder and len(bpy.data.texts) == text_count
        raw = (source[registry.REGISTRY_KEY], source[profiles.PROFILE_KEY])
    binding.remove_hair_binding(bpy.context, source)
    assert artist_state(source) == original and bones_snapshot(armature) == native


def test_saved_unbound_source_reopens_with_owner_pointer_and_rebinds():
    source, plans, armature, _original, _native = configured_fixture()
    data = registry.read(source)
    settings = profiles.read(source, registry=data)
    binding.remove_hair_binding(bpy.context, source)
    source_name, armature_name, mesh_name = source.name, armature.name, source.data.name
    holder_name = identity_holder(source).name
    raw = (source[registry.REGISTRY_KEY], source[profiles.PROFILE_KEY], source[lifecycle.IDENTITY_KEY])
    with tempfile.TemporaryDirectory(prefix='cd_hair_motion_lifecycle_') as temporary:
        path = str(Path(temporary) / 'unbound_motion_identity.blend')
        assert bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False) == {'FINISHED'}
        assert bpy.ops.wm.open_mainfile(filepath=path, load_ui=False) == {'FINISHED'}
        source, armature = bpy.data.objects[source_name], bpy.data.objects[armature_name]
        activate(source)
        assert source.data.name == mesh_name and identity_holder(source).name == holder_name
        assert lifecycle.source_uid_for_bind(source) == data['source_uid']
        assert (source[registry.REGISTRY_KEY], source[profiles.PROFILE_KEY], source[lifecycle.IDENTITY_KEY]) == raw
        binding.bind_hair(bpy.context, source, plans, bone_count=5, armature=armature)
        current = registry.reconcile(source)
        assert identities(current) == identities(data)
        assert profiles.reconcile(source, registry=current) == settings


def test_actual_object_copy_rejects_bound_and_retained_unbound_identity():
    source, plans, armature, _original, _native = configured_fixture()
    uid = registry.read(source)['source_uid']
    holder = identity_holder(source)
    for unbound in (False, True):
        if unbound:
            binding.remove_hair_binding(bpy.context, source)
        clone = source.copy()
        clone.data = source.data.copy()
        bpy.context.scene.collection.objects.link(clone)
        assert clone.get(lifecycle.OWNER_KEY) == holder, 'Object.copy must share the separate ownership holder'
        assert holder[lifecycle.HOLDER_SOURCE_KEY] == source, 'Copied Objects must not remap another ID datablock'
        original, copied = source_state(source), source_state(clone)
        counts = lifecycle_counts()
        must_stop(lambda: lifecycle.source_uid_for_bind(clone), ('copied', 'ownership', 'owned', 'ambiguous', 'changed'))
        must_stop(lambda: binding.bind_hair(bpy.context, clone, plans, bone_count=5, armature=armature))
        assert source_state(source) == original and source_state(clone) == copied
        assert lifecycle_counts() == counts
        mesh = clone.data
        bpy.data.objects.remove(clone, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        assert lifecycle.source_uid_for_bind(source) == uid


def test_unbound_copy_cannot_inherit_identity_after_original_owner_is_deleted():
    source, plans, armature, _original, _native = configured_fixture()
    binding.remove_hair_binding(bpy.context, source)
    clone = source.copy()
    clone.data = source.data.copy()
    bpy.context.scene.collection.objects.link(clone)
    holder = identity_holder(source)
    assert clone[lifecycle.OWNER_KEY] == holder and holder[lifecycle.HOLDER_SOURCE_KEY] == source
    bpy.data.objects.remove(source, do_unlink=True)
    activate(clone)
    assert holder.get(lifecycle.HOLDER_SOURCE_KEY) is None
    before, native, counts = source_state(clone), bones_snapshot(armature), lifecycle_counts()
    must_stop(lambda: lifecycle.source_uid_for_bind(clone), ('copied', 'ownership'))
    must_stop(lambda: binding.bind_hair(bpy.context, clone, plans, bone_count=5, armature=armature),
              ('copied', 'ownership'))
    assert source_state(clone) == before and bones_snapshot(armature) == native
    assert lifecycle_counts() == counts and not binding.is_bound(clone)


def test_unbound_legacy_owner_missing_and_changed_topology_are_read_only_rejections():
    source, _plans, _armature, _original, _native = configured_fixture()
    data = registry.read(source)
    binding.remove_hair_binding(bpy.context, source)
    token = lifecycle.snapshot(source)
    del source[lifecycle.OWNER_KEY]
    del source[lifecycle.IDENTITY_KEY]
    before = source_state(source)
    must_stop(lambda: lifecycle.source_uid_for_bind(source), ('unbound legacy', 'owner'))
    assert source_state(source) == before
    lifecycle.restore(source, token)
    source.data.vertices[4].co.x += .001
    source.data.update()
    assert lifecycle.source_uid_for_bind(source) == data['source_uid'], 'Coordinates are not topology'
    source.data.vertices.add(1)
    source.data.update()
    before = source_state(source)
    must_stop(lambda: lifecycle.source_uid_for_bind(source), ('topology',))
    assert source_state(source) == before


def test_claim_write_failure_rolls_back_only_owned_metadata():
    source, _plans, armature, _original, _native = configured_fixture(claim=False)
    before, bones, counts = source_state(source), bones_snapshot(armature), lifecycle_counts()
    artist_text = bpy.data.texts.new('Artist Notes')
    artist_text.write('Preserve this unrelated Text and its contents.\n')
    artist_body = artist_text.as_string()
    counts = lifecycle_counts()
    original = lifecycle._store
    def fail_after_owner(obj, key, value):
        if key == lifecycle.IDENTITY_KEY:
            raise RuntimeError('Injected identity write failure')
        original(obj, key, value)
    with patch.object(lifecycle, '_store', side_effect=fail_after_owner):
        must_stop(lambda: lifecycle.claim_bound(source), ('identity write',))
    assert lifecycle.OWNER_KEY not in source and lifecycle.IDENTITY_KEY not in source
    assert source_state(source) == before and bones_snapshot(armature) == bones
    assert lifecycle_counts() == counts, (counts, lifecycle_counts(),
        [(text.name, text.users, text.use_fake_user, text.get(lifecycle.HOLDER_SOURCE_KEY),
          lifecycle._owned_holder(text, source), text.as_string() == lifecycle._HOLDER_BODY)
         for text in bpy.data.texts])
    assert artist_text.as_string() == artist_body
    lifecycle.claim_bound(source)
    holder, holder_body = identity_holder(source), identity_holder(source).as_string()
    counts = lifecycle_counts()
    before = source_state(source)
    with patch.object(lifecycle, '_store', side_effect=fail_after_owner):
        must_stop(lambda: lifecycle.claim_bound(source), ('identity write',))
    assert source_state(source) == before
    assert lifecycle_counts() == counts and identity_holder(source) == holder and holder.as_string() == holder_body


def test_public_remove_failure_rolls_back_newly_claimed_marker():
    source, _plans, armature, _original, _native = configured_fixture(claim=False)
    before, native, counts = source_state(source), bones_snapshot(armature), lifecycle_counts()
    with patch.object(binding, '_restore_touched_weights', side_effect=RuntimeError('Injected removal failure')):
        must_stop(lambda: binding.remove_hair_binding(bpy.context, source), ('injected',))
    after, current_native = source_state(source), bones_snapshot(armature)
    assert after == before, state_differences(before, after)
    assert current_native == native, bone_differences(native, current_native)
    assert lifecycle.OWNER_KEY not in source and lifecycle.IDENTITY_KEY not in source
    assert binding.is_bound(source) and lifecycle_counts() == counts


def test_public_late_rebind_failure_restores_retained_identity_and_author_data():
    source, plans, armature, _original, _native = configured_fixture()
    binding.remove_hair_binding(bpy.context, source)
    before, native, counts = source_state(source), bones_snapshot(armature), lifecycle_counts()
    original_capture = binding._capture_vertex_groups
    original_claim = lifecycle.claim_bound
    events = []
    calls = 0
    def capture(obj):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert 'claim' in events, 'Failure must follow native builder identity claim'
            raise RuntimeError('Injected post-bind failure')
        return original_capture(obj)
    def claim(*args, **kwargs):
        result = original_claim(*args, **kwargs)
        events.append('claim')
        return result
    with patch.object(binding, '_capture_vertex_groups', side_effect=capture), \
            patch.object(lifecycle, 'claim_bound', side_effect=claim), \
            patch.object(lifecycle, 'restore', wraps=lifecycle.restore) as restore:
        must_stop(lambda: binding.bind_hair(bpy.context, source, plans, bone_count=5, armature=armature), ('post-bind',))
        assert restore.called, 'The enclosing binding transaction must restore its identity checkpoint'
    after, current_native = source_state(source), bones_snapshot(armature)
    assert after == before, state_differences(before, after)
    assert current_native == native, bone_differences(native, current_native)
    assert lifecycle_counts() == counts and not binding.is_bound(source)


def test_public_bind_and_remove_stop_preview_before_first_mesh_snapshot():
    source, plans, armature = scene_fixture()
    active, stops, reads = True, [], []
    original_snapshot = hair._mesh_snapshot
    def status(_context):
        return {'active': active}
    def stop(_context, **_kwargs):
        nonlocal active
        stops.append('stop')
        active = False
    def capture(obj):
        assert not active, 'Preview must restore author state before source snapshot'
        reads.append(obj.name)
        return original_snapshot(obj)
    with patch.object(adapter, 'status', side_effect=status), \
            patch.object(adapter, 'stop_preview', side_effect=stop), \
            patch.object(hair, '_mesh_snapshot', side_effect=capture):
        binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
        assert stops == ['stop'] and reads
        active = True
        reads.clear()
        binding.remove_hair_binding(bpy.context, source)
        assert stops == ['stop', 'stop'] and reads


def main():
    tests = (test_plain_public_remove_keeps_existing_property_reversibility,
             test_public_remove_rebind_three_to_five_preserves_strands_settings_and_artist_data,
             test_saved_unbound_source_reopens_with_owner_pointer_and_rebinds,
             test_actual_object_copy_rejects_bound_and_retained_unbound_identity,
             test_unbound_copy_cannot_inherit_identity_after_original_owner_is_deleted,
             test_unbound_legacy_owner_missing_and_changed_topology_are_read_only_rejections,
             test_claim_write_failure_rolls_back_only_owned_metadata,
             test_public_remove_failure_rolls_back_newly_claimed_marker,
             test_public_late_rebind_failure_restores_retained_identity_and_author_data,
             test_public_bind_and_remove_stop_preview_before_first_mesh_snapshot)
    for test in tests:
        test()
        print('PASS', test.__name__)
    print('HAIR_MOTION_LIFECYCLE_TESTS_OK', len(tests))


if __name__ == '__main__':
    main()
