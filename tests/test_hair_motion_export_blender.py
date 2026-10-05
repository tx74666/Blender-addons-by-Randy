"""Hair sidecar/native FBX transaction checks in disposable factory state.

No artist file or Unity project is opened. The roundtrip uses the existing
isolated model worker, then imports its actual FBX into this test scene.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch, Mock

import bmesh
import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import character_setup, hair_bones_rig as hair
from character_designer import hair_strand_registry as registry, hair_motion_profiles as profiles
from character_designer import hair_motion_export as sidecar, hair_wiggle_adapter as preview
from character_designer import unity_export as exporter, unity_export_worker as worker
from test_hair_strand_registry_blender import bound_fixture, initialize_unpaired
from test_hair_bones_rig_blender import activate, make_armature
from test_unity_export_blender import digest, folder_bytes


def test_disposable_backend_disabled_without_callback_or_bake(directory):
    f = fixture(directory / 'disable-backend')
    # A minimal registered RNA stand-in checks direct ID-property disabling,
    # including a newly-created scene whose backend default starts enabled.
    if (bpy.types.Scene.bl_rna.properties.get('wiggle') is not None
            or bpy.types.Object.bl_rna.properties.get('wiggle') is not None):
        return  # An enabled real backend is covered by the adapter integration.
    callbacks = []
    def update(_self, _context):
        callbacks.append('unexpected backend rebuild')
    class ExportTestWiggleScene(bpy.types.PropertyGroup):
        enable: bpy.props.BoolProperty(default=True, update=update)
    class ExportTestWiggleObject(bpy.types.PropertyGroup):
        freeze: bpy.props.BoolProperty(default=False, update=update)
    created = None
    bpy.utils.register_class(ExportTestWiggleScene)
    bpy.utils.register_class(ExportTestWiggleObject)
    try:
        bpy.types.Scene.wiggle = bpy.props.PointerProperty(type=ExportTestWiggleScene)
        bpy.types.Object.wiggle = bpy.props.PointerProperty(type=ExportTestWiggleObject)
        assert bpy.context.scene.wiggle.enable and not f.authority.wiggle.freeze
        worker._disable_hair_simulation()
        assert not bpy.context.scene.wiggle.enable and f.authority.wiggle.freeze
        created = bpy.data.scenes.new('Disposable newly enabled backend')
        assert created.wiggle.enable
        worker._disable_hair_simulation(scenes=(created,), objects=())
        assert not created.wiggle.enable and callbacks == []
    finally:
        if created is not None:
            bpy.data.scenes.remove(created)
        del bpy.types.Scene.wiggle
        del bpy.types.Object.wiggle
        bpy.utils.unregister_class(ExportTestWiggleObject)
        bpy.utils.unregister_class(ExportTestWiggleScene)
    print('PASS disposable old/new scenes disabled and rigs frozen without a backend callback or bake', flush=True)


def expect_error(action, fragment=''):
    try:
        action()
    except ValueError as exc:
        assert fragment.casefold() in str(exc).casefold(), str(exc)
        return
    raise AssertionError('Expected conservative export rejection: ' + fragment)


def fixture(directory, *, accessory=False):
    source, _plans, result = bound_fixture(strands=2, count=3)
    authority = result['armature']
    data = initialize_unpaired(source)
    data = registry.manual_pair(source, data['strands'][0]['strand_id'], data['strands'][1]['strand_id'])
    profiles.initialize(source, registry=data)
    profiles.update(source, data['strands'][0]['strand_id'], group='BACK', registry=data)
    source.shape_key_add(name='Basis')
    expression = source.shape_key_add(name='Artist Breeze L')
    expression.data[4].co.y += 0.08
    expression.value = 0.35
    expression.keyframe_insert('value', frame=1)
    authority.pose.bones[data['strands'][0]['bones'][0]].rotation_mode = 'XYZ'
    authority.pose.bones[data['strands'][0]['bones'][0]].rotation_euler.x = 0.2
    authority.pose.bones[data['strands'][0]['bones'][0]].keyframe_insert('rotation_euler', frame=1)
    main = authority
    if accessory:
        main = make_armature('Main Character')
        # A real collision exercises the merge's explicit renamed-bone mapping.
        collision = data['strands'][0]['bones'][0]
        activate(main, 'EDIT')
        bone = main.data.edit_bones.new(collision)
        bone.head, bone.tail = (0, 0, 0), (0, 0, 0.25)
        bone.use_deform = True
        activate(source)
        authority.parent = main
        authority.parent_type = 'BONE'
        authority.parent_bone = 'spine.006'
    bpy.context.scene.unit_settings.scale_length = 0.01
    activate(source)
    setup = SimpleNamespace(rig=main, body=source, assets=[])
    config = SimpleNamespace(directory=str(directory), filename='HairCharacter', asset_id='fixture-hair-model',
                             extras=[], simple_materials=[])
    return SimpleNamespace(source=source, authority=authority, main=main, registry=data,
                           setup=setup, config=config)


def test_capture_preview_stop_order_and_live_data_preserved(directory):
    f = fixture(directory / 'capture')
    before = digest()
    events, active = [], [True]
    original_capture, original_write = exporter._capture_hair_motion, bpy.data.libraries.write
    def stop(context, **_kwargs):
        events.append('stop')
        active[0] = False
    def capture(objects):
        events.append('capture')
        assert not active[0]
        return original_capture(objects)
    def write(*args, **kwargs):
        events.append('snapshot')
        assert not active[0]
        return original_write(*args, **kwargs)
    class Delegate:
        """Observe the coordinator call while leaving builtin RNA untouched."""
        def __init__(self, target, **overrides):
            self.target, self.overrides = target, overrides

        def __getattr__(self, name):
            return self.overrides[name] if name in self.overrides else getattr(self.target, name)
    observed_bpy = Delegate(bpy, data=Delegate(bpy.data,
        libraries=Delegate(bpy.data.libraries, write=write)))
    process = Mock()
    process.poll.return_value = 0
    with patch.object(character_setup, 'settings', return_value=f.setup), \
            patch.object(preview, 'status', side_effect=lambda _context: {'active': active[0]}), \
            patch.object(preview, 'stop_preview', side_effect=stop), \
            patch.object(exporter, '_capture_hair_motion', side_effect=capture), \
            patch.object(exporter, 'bpy', observed_bpy), \
            patch.object(exporter.subprocess, 'Popen', return_value=process):
        job = exporter.begin_export(bpy.context, f.main, f.config)
        spec = json.loads((job['root'] / 'job.json').read_text(encoding='utf-8'))
        assert spec['asset_id'] == f.config.asset_id
        assert spec['hair_motion']['registry'] == registry.read(f.source, validate=True)
        assert spec['hair_motion']['profiles'] == profiles.effective_all(f.source, registry=f.registry)
        assert spec['hair_motion']['rig'] == f.authority.name
        assert events == ['stop', 'capture', 'snapshot'], events
        assert digest() == before
        exporter.cancel_export(job)
    assert not exporter.export_running() and digest() == before
    print('PASS preview stopped before strict capture/snapshot; source pose, mesh, weights, keys and actions unchanged', flush=True)


def test_invalid_source_authority_topology_and_multiple_sources_fail_before_worker(directory):
    f = fixture(directory / 'proof')
    outside = make_armature('Uncollected Hair authority')
    activate(f.source)
    collected = [f.source, f.authority]
    f.source[hair.RIG_KEY] = outside
    expect_error(lambda: exporter._capture_hair_motion(collected), 'authoritative')
    f.source[hair.RIG_KEY] = f.authority
    clone = f.source.copy()
    clone.data = f.source.data.copy()
    bpy.context.scene.collection.objects.link(clone)
    expect_error(lambda: exporter._capture_hair_motion(collected + [clone]), 'one configured')
    bpy.data.objects.remove(clone, do_unlink=True)
    # Native ownership invalidation must refuse without publishing or spawning.
    first = f.authority.data.bones[f.registry['strands'][0]['bones'][0]]
    first[hair.OWNER_KEY] = 'foreign-owner'
    before = digest()
    with patch.object(character_setup, 'settings', return_value=f.setup), \
            patch.object(exporter.subprocess, 'Popen') as spawn:
        expect_error(lambda: exporter.begin_export(bpy.context, f.main, f.config), 'ownership')
        spawn.assert_not_called()
    assert digest() == before and not Path(f.config.directory).exists()
    first[hair.OWNER_KEY] = hair.OWNER_VALUE
    # Connectivity changed without changing names/counts: reject its old proof.
    f.source.shape_key_clear()
    bm = bmesh.new()
    try:
        bm.from_mesh(f.source.data)
        bm.faces.ensure_lookup_table()
        bm.faces.remove(bm.faces[-1])
        bm.to_mesh(f.source.data)
    finally:
        bm.free()
    f.source.data.update()
    with patch.object(character_setup, 'settings', return_value=f.setup), \
            patch.object(exporter.subprocess, 'Popen') as spawn:
        expect_error(lambda: exporter.begin_export(bpy.context, f.main, f.config), 'topology')
        spawn.assert_not_called()
    assert not Path(f.config.directory).exists()
    print('PASS wrong authority, multiple sources, foreign ownership and dirty native topology refuse before worker/publication', flush=True)


def test_worker_checks_captured_proof_before_cleanup_and_never_guesses_mapping(directory):
    f = fixture(directory / 'snapshot')
    objects = [f.source, f.authority]
    capture = exporter._capture_hair_motion(objects)
    before = digest()
    assert worker._capture_hair_snapshot({'hair_motion': capture}, objects) == capture
    bad = copy.deepcopy(capture)
    bad['registry']['topology'] = '0' * 64
    expect_error(lambda: worker._capture_hair_snapshot({'hair_motion': bad}, objects), 'changed between')
    expect_error(lambda: worker._capture_hair_snapshot({}, objects), 'inventory')
    names = [name for strand in f.registry['strands'] for name in strand['bones']]
    mapping = worker._hair_final_mapping(capture, f.main, {f.main.name: names}, {})
    assert mapping == {name: name for name in names}
    expect_error(lambda: worker._hair_final_mapping(capture, f.main, {f.main.name: names[:-1]}, {}), 'omitted')
    bad = dict(capture, rig='Accessory Missing')
    expect_error(lambda: worker._hair_final_mapping(bad, f.main, {'Accessory Missing': names}, {}), 'explicit')
    assert digest() == before
    print('PASS snapshot proof revalidated before mutation; incomplete retained/accessory bindings refuse without guessing', flush=True)


def _publish_job(f, target, stage, *, capture):
    stage.mkdir(parents=True, exist_ok=True)
    manifest = target / 'HairCharacter.cdesigner.json'
    return {'directory': target, 'stage': stage, 'filename': 'HairCharacter.fbx',
            'asset_id': f.config.asset_id, 'rig': f.main, 'objects': [f.main.name, f.source.name],
            'hair_motion': capture,
            'manifest_hash': exporter._hash(manifest) if manifest.exists() else None}


def _stage_sidecar(f, job):
    fbx = job['stage'] / job['filename']
    fbx.write_bytes(b'FBX publication transaction fixture ' * 8)
    capture = job['hair_motion']
    mapping = {name: name for strand in capture['registry']['strands'] for name in strand['bones']}
    payload = sidecar.build_payload(f.source, capture['registry'], capture['profiles'], mapping,
        job['asset_id'], hashlib.sha256(fbx.read_bytes()).hexdigest(),
        {'source_meters_per_unit': 0.01, 'export_meters_per_unit': 1.0})
    sidecar.write_atomic(job['stage'] / 'HairCharacter.hair-motion.json', payload)
    return {'ok': True, 'files': ['HairCharacter.fbx', 'HairCharacter.hair-motion.json'], 'warnings': [],
            'hair_motion': {'active': True, 'file': 'HairCharacter.hair-motion.json', 'simulation_baked': False}}


def test_publication_stale_inactive_hash_rejection_and_late_atomic_rollback(directory):
    f = fixture(directory / 'publication-fixture')
    capture = exporter._capture_hair_motion([f.source, f.authority])
    target = directory / 'published'
    job = _publish_job(f, target, directory / 'stage-first', capture=capture)
    result = _stage_sidecar(f, job)
    exporter._publish(job, result)
    before = folder_bytes(target)
    failed = _publish_job(f, target, directory / 'stage-mismatch', capture=capture)
    result = _stage_sidecar(f, failed)
    (failed['stage'] / failed['filename']).write_bytes(b'A different FBX hash ' * 8)
    expect_error(lambda: exporter._publish(failed, result), 'sidecar failed')
    assert folder_bytes(target) == before
    failed = _publish_job(f, target, directory / 'stage-late', capture=capture)
    result = _stage_sidecar(f, failed)
    original_replace, calls = exporter.os.replace, []
    def broken_replace(source, destination):
        calls.append(str(destination))
        if len(calls) == 2:
            raise OSError('Injected sidecar publication failure')
        return original_replace(source, destination)
    try:
        with patch.object(exporter.os, 'replace', side_effect=broken_replace):
            exporter._publish(failed, result)
    except OSError:
        pass
    else:
        raise AssertionError('Expected atomic late sidecar failure')
    assert folder_bytes(target) == before
    plain = _publish_job(f, target, directory / 'stage-plain', capture=None)
    (plain['stage'] / plain['filename']).write_bytes(b'New rest model with no Hair config ' * 8)
    published = exporter._publish(plain, {'ok': True, 'files': [plain['filename']], 'warnings': [],
        'hair_motion': {'active': False, 'file': None, 'simulation_baked': False}})
    report = json.loads(Path(published['report_path']).read_text(encoding='utf-8'))
    assert report['hair_motion']['active'] is False
    assert report['hair_motion']['inactive_files'] == ['HairCharacter.hair-motion.json']
    assert any('Inactive Hair' in warning for warning in report['warnings'])
    assert (target / 'HairCharacter.hair-motion.json').read_bytes() == before['HairCharacter.hair-motion.json']
    print('PASS actual sidecar/FBX binding, late full publication rollback and retained inactive manifest policy', flush=True)


def test_actual_fbx_accessory_roundtrip_and_unbaked_artist_state(directory):
    f = fixture(directory / 'roundtrip', accessory=True)
    before = digest()
    with patch.object(character_setup, 'settings', return_value=f.setup):
        result = exporter.export_character(bpy.context, f.main, f.config)
    assert digest() == before, 'Model export changed author mesh, weights, keys, native Rest, pose or Actions'
    manifest = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert manifest['hair_motion']['active'] and manifest['hair_motion']['strands'] == 2
    payload = sidecar.verify_read(Path(result['filepath']).with_suffix('.hair-motion.json'), fbx_path=result['filepath'])
    assert payload['asset_id'] == f.config.asset_id and not payload['simulation_baked']
    assert payload['units']['source_meters_per_unit'] == bpy.context.scene.unit_settings.scale_length
    assert payload['units']['fbx_apply_scale_options'] == 'FBX_SCALE_UNITS'
    assert payload['application']['conversion_validation'] == 'pending'
    assert all('exported_rest' not in strand for strand in payload['strands'])
    names = [name for strand in payload['strands'] for name in strand['exported_bones']]
    explicit = manifest['accessory_bone_mapping'][f.authority.name]
    assert all(strand['exported_bones'] == [explicit[name] for name in strand['source_bones']]
               for strand in payload['strands'])
    assert payload['strands'][0]['exported_bones'][0] != payload['strands'][0]['source_bones'][0]
    filepath = result['filepath']
    activate(f.source)
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    assert 'FINISHED' in bpy.ops.import_scene.fbx(filepath=filepath)
    rigs = [obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE']
    assert len(rigs) == 1
    native = rigs[0].data.bones
    assert set(names).issubset(native.keys())
    for strand in payload['strands']:
        ordered = [native[name] for name in strand['exported_bones']]
        assert all(bone.parent == ordered[index - 1] for index, bone in enumerate(ordered) if index)
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    assert len(meshes) == 1 and 'Artist Breeze L' in meshes[0].data.shape_keys.key_blocks
    assert set(names).issubset(meshes[0].vertex_groups.keys())
    for obj in rigs + meshes:
        assert obj.animation_data is None or obj.animation_data.action is None, 'Normal rest FBX baked animation'
    print('PASS real FBX roundtrip binds renamed accessory Hair bones, preserves artist Shape Key, has no baked Action', flush=True)


def main():
    with tempfile.TemporaryDirectory(prefix='cdesigner-hair-export-test-') as temporary:
        directory = Path(temporary)
        backups = directory / 'backups'
        backups.mkdir()
        original_resource = bpy.utils.user_resource
        def resource(kind, *, path='', create=False):
            if kind == 'DATAFILES' and path == 'character_designer/export_backups':
                return str(backups)
            return original_resource(kind, path=path, create=create)
        with patch.object(bpy.utils, 'user_resource', side_effect=resource):
            for test in (test_disposable_backend_disabled_without_callback_or_bake,
                         test_capture_preview_stop_order_and_live_data_preserved,
                         test_invalid_source_authority_topology_and_multiple_sources_fail_before_worker,
                         test_worker_checks_captured_proof_before_cleanup_and_never_guesses_mapping,
                         test_publication_stale_inactive_hash_rejection_and_late_atomic_rollback,
                         test_actual_fbx_accessory_roundtrip_and_unbaked_artist_state):
                test(directory)
    print('HAIR_MOTION_EXPORT_BLENDER_OK 6', flush=True)


if __name__ == '__main__':
    main()
