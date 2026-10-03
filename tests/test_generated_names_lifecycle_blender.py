"""Real restricted enable and save/reopen of existing legacy skirt resources.

Run in a disposable --background --factory-startup Blender process. Saved files
are temporary test fixtures; this script never opens a user project.
"""
import json
import math
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import addon_utils
import bpy
from _bpy_restrict_state import RestrictBlend


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import generated_names as names
from character_designer import skirt_rig as skirt


def enable():
    errors = []
    module = addon_utils.enable('character_designer', default_set=False,
                                persistent=True, refresh_handled=True,
                                handle_error=errors.append)
    assert not errors, repr(errors)
    assert module is not None and module.__addon_enabled__, 'Restricted add-on enable failed.'
    assert Path(module.__file__).resolve() == ROOT / 'addons' / 'character_designer' / '__init__.py'
    module._validate_registration_integrity()
    return module


def pending():
    assert bpy.app.handlers.load_post.count(names._after_load) == 1
    assert bpy.app.timers.is_registered(names._cleanup_deferred)


def run_pending():
    """Emulate one normal UI timer tick while the background test owns Python."""
    pending()
    bpy.app.timers.unregister(names._cleanup_deferred)
    actual, results = names.clean_generated_names, []

    def capture(*args, **kwargs):
        result = actual(*args, **kwargs)
        results.append(result)
        return result

    with patch.object(names, 'clean_generated_names', side_effect=capture):
        assert names._cleanup_deferred() is None
    assert len(results) == 1, 'Deferred cleanup did not run exactly once.'
    assert not results[0]['skipped'], results[0]
    assert not bpy.app.timers.is_registered(names._cleanup_deferred)
    return results[0]


def fixture():
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    columns, levels = 24, 6
    vertices, faces = [], []
    for ring in range(levels):
        fraction = ring / (levels - 1)
        for column in range(columns):
            angle = math.tau * column / columns
            vertices.append(((.35 + .35 * fraction) * math.cos(angle),
                             (.28 + .25 * fraction) * math.sin(angle),
                             1.1 - .7 * fraction))
    for ring in range(levels - 1):
        for column in range(columns):
            first = ring * columns + column
            following = ring * columns + (column + 1) % columns
            faces.append((first, first + columns, following + columns, following))
    mesh = bpy.data.meshes.new('Artist Dress Mesh')
    mesh.from_pydata(vertices, [], faces)
    source = bpy.data.objects.new('Dress', mesh)
    bpy.context.scene.collection.objects.link(source)
    bpy.context.view_layer.objects.active = source
    source.select_set(True)
    source.vertex_groups.new(name='Artist Pin').add([3, 25], .42, 'REPLACE')
    source.shape_key_add(name='Basis')
    fold = source.shape_key_add(name='Artist Fold')
    fold.data[0].co.z += .015
    fold.value = .23
    fold.keyframe_insert('value', frame=1)
    record = skirt.build_skirt(bpy.context, source)
    rig = source[skirt.RIG_KEY]
    rig.pose.bones[record['controls']['chains'][0]['hem']].location.x = .04
    replacements = {}
    # Generated groups already occupy the clean prefix after build; the actual
    # object's prefix is authoritative for constructing a historical fixture.
    prefix = rig.name.removesuffix('_Rig')
    for obj in tuple(bpy.data.objects):
        if obj is source or obj.get(skirt.OWNER_KEY) != record['owner']:
            continue
        for item in (obj, obj.data):
            old = item.name
            assert old.startswith(prefix + '_'), old
            item.name = prefix + '_' + record['owner'][:6] + old[len(prefix):]
            replacements[old] = item.name
    helpers = bpy.data.collections[record['owned_collections'][1]]
    old = helpers.name
    helpers.name = 'Skirt wire and shapes | ' + record['owner'][:6]
    replacements[old] = helpers.name

    def remap(value):
        if isinstance(value, str):
            return replacements.get(value, value)
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        return value

    skirt.write_record(source, remap(record))
    record = skirt.read_record(source)
    skirt._check_existing_geometry(source, record)
    return source


def artist_state(source):
    rig = source[skirt.RIG_KEY]
    keys = source.data.shape_keys
    return {
        'owner': source[skirt.OWNER_KEY],
        'vertices': tuple(tuple(vertex.co) for vertex in source.data.vertices),
        'faces': tuple(tuple(face.vertices) for face in source.data.polygons),
        'groups': tuple(group.name for group in source.vertex_groups),
        'weights': tuple(tuple((entry.group, entry.weight) for entry in vertex.groups)
                         for vertex in source.data.vertices),
        'keys': tuple((key.name, key.value, tuple(tuple(point.co) for point in key.data))
                      for key in keys.key_blocks),
        'action': keys.animation_data.action.name,
        'bones': tuple((bone.name, tuple(tuple(row) for row in bone.matrix_local))
                       for bone in rig.data.bones),
        'pose': tuple((bone.name, tuple(tuple(row) for row in bone.matrix_basis))
                      for bone in rig.pose.bones),
    }


def check_record(source, owner):
    record = skirt.read_record(source)
    assert record is not None and record['owner'] == owner
    rig = source[skirt.RIG_KEY]
    assert rig[skirt.SOURCE_KEY] is source
    assert rig[skirt.OWNER_KEY] == rig.data[skirt.OWNER_KEY] == owner
    assert source.parent is rig
    assert any(modifier.type == 'ARMATURE' and modifier.object is rig
               for modifier in source.modifiers)
    assert all(name in bpy.data.objects for name in record['owned_objects'])
    assert all(name in bpy.data.collections for name in record['owned_collections'])
    skirt._check_existing_geometry(source, record)
    return record


def main():
    assert not hasattr(bpy.types.WindowManager, 'character_designer'), 'Use --factory-startup.'
    actual_register, restricted, cleanup_calls = names.register_handlers, [], []

    def observe_register():
        restricted.append(getattr(bpy.data, 'objects', None) is None)
        return actual_register()

    def unexpected_cleanup(*args, **kwargs):
        cleanup_calls.append((args, kwargs))
        raise AssertionError('Cleanup ran synchronously inside add-on registration.')

    module = None
    try:
        with patch.object(names, 'register_handlers', side_effect=observe_register), \
                patch.object(names, 'clean_generated_names', side_effect=unexpected_cleanup):
            module = enable()
        assert restricted == [True], restricted
        assert not cleanup_calls, cleanup_calls
        pending()
        with RestrictBlend():
            assert names._cleanup_deferred() == .1
            names.register_handlers()
            names.register_handlers()
        pending()
        assert run_pending() == {'renamed': [], 'skipped': []}
        print('PASS real_restricted_enable_and_idempotent_deferred_cleanup', flush=True)

        names.register_handlers()
        pending()
        names.unregister_handlers()
        names.unregister_handlers()
        assert names._after_load not in bpy.app.handlers.load_post
        assert not bpy.app.timers.is_registered(names._cleanup_deferred)
        print('PASS unregister_cancels_pending_cleanup', flush=True)

        source = fixture()
        owner, before = source[skirt.OWNER_KEY], artist_state(source)
        raw = source[skirt.RECORD_KEY]
        addon_utils.disable('character_designer', default_set=False, refresh_handled=True)
        assert not module.__addon_enabled__
        assert source[skirt.RECORD_KEY] == raw and artist_state(source) == before
        assert names._after_load not in bpy.app.handlers.load_post
        assert not bpy.app.timers.is_registered(names._cleanup_deferred)
        module = enable()
        assert source[skirt.RECORD_KEY] == raw and artist_state(source) == before
        pending()
        print('PASS disable_reenable_preserves_persistent_legacy_record', flush=True)

        with tempfile.TemporaryDirectory(prefix='cd_generated_names_lifecycle_') as directory:
            legacy_path = str(Path(directory) / 'legacy.blend')
            cleaned_path = str(Path(directory) / 'cleaned.blend')
            names.unregister_handlers()
            bpy.ops.wm.save_as_mainfile(filepath=legacy_path, check_existing=False)
            names.register_handlers()
            bpy.ops.wm.open_mainfile(filepath=legacy_path, load_ui=False, use_scripts=False)
            assert module.__addon_enabled__
            source = bpy.data.objects['Dress']
            assert source[skirt.RECORD_KEY] == raw, 'Save/reopen discarded the setup record.'
            assert artist_state(source) == before
            check_record(source, owner)
            pending()
            result = run_pending()
            assert result['renamed'], result
            record = check_record(source, owner)
            assert source[skirt.RIG_KEY].name == 'SK_Dress_Rig'
            assert 'Skirt Wire and Shapes | Dress' in record['owned_collections']
            assert all(owner[:6] not in name for name in record['owned_objects'])
            assert artist_state(source) == before
            cleaned_record = source[skirt.RECORD_KEY]
            bpy.ops.wm.save_as_mainfile(filepath=cleaned_path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=cleaned_path, load_ui=False, use_scripts=False)
            source = bpy.data.objects['Dress']
            assert source[skirt.RECORD_KEY] == cleaned_record
            assert artist_state(source) == before
            check_record(source, owner)
            assert run_pending() == {'renamed': [], 'skipped': []}
        print('PASS legacy_save_reopen_cleanup_and_cleaned_record_persistence', flush=True)
    finally:
        addon_utils.disable('character_designer', default_set=False, refresh_handled=True)
        assert names._after_load not in bpy.app.handlers.load_post
        assert not bpy.app.timers.is_registered(names._cleanup_deferred)
    print('GENERATED_NAMES_LIFECYCLE_PASS 4', flush=True)


if __name__ == '__main__':
    main()
