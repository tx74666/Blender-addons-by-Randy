"""Cold and cached batch guides use frozen JSON; write proofs remain strict."""
import json
import sys
import traceback
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import (
    bound_fixture, character_designer, C, bank, definition, fingerprint, rig_state,
    assert_rig_equal,
)
from test_finger_topology_adapt_blender import split_ring, edit_mesh, update
from test_finger_preview_cache_blender import no_reads, saved_metadata, edited_vertex
from character_designer import finger_definition_ui as guides, finger_preview_cache as cache
from character_designer import finger_bone_tools as bones


NAMES = tuple(digit+'.L' for digit in bank.detect.DIGITS)


def fixture():
    bones.hide()
    bones.invalidate()
    guides.hide(invalidate=True)
    obj, rig, _ = bound_fixture()
    state = obj.character_designer_finger_bank
    state.active, state.visible_digits, state.selection_initialized = 'INDEX.L', set(bank.detect.DIGITS), True
    C.scene.character_designer_finger_definition.overlays_enabled = True
    C.view_layer.update()
    guides.show()
    return obj, rig


def assert_frame(actual, expected):
    assert actual['key'] == expected['key'] and actual['basis'] == expected['basis']
    assert actual['internal'] == expected['internal']
    assert abs(actual['length']-expected['length']) < 1e-7
    assert len(actual['path']) == len(expected['path'])
    for a, b in zip(actual['path'], expected['path']): assert (a-b).length < 1e-7
    for field in ('root', 'tip', 'midpoint', 'direction'):
        assert (actual[field]-expected[field]).length < 1e-7, field
    if expected['bend'] is None: assert actual['bend'] is None
    else: assert (actual['bend']-expected['bend']).length < 1e-7
    assert actual['bend_error'] == expected['bend_error']


def direct(obj, name, **kwargs):
    return definition.frame(bank.scoped(C, obj.character_designer_finger_bank.slots[name].guide), **kwargs)


def failure(fn):
    try: fn()
    except ValueError as exc: return str(exc)
    raise AssertionError('Invalid reference unexpectedly passed validation')


def test_five_cold_ui_frames_match_capture_without_any_mesh_reads():
    obj, _ = fixture()
    expected = {name: direct(obj, name) for name in NAMES}
    before, saved = fingerprint(obj), saved_metadata(obj)
    C.view_layer.update()
    guides.redraw()
    with no_reads():
        frames = {frame['bank_key']: frame for frame in guides.display_frames(C)}
    assert set(frames) == set(NAMES)
    for name in NAMES:
        assert_frame(frames[name], expected[name])
        assert frames[name]['display_only']
    assert fingerprint(obj) == before and saved_metadata(obj) == saved


def test_cold_bend_and_25_real_edits_preserve_cached_guides_without_reads():
    obj, rig = fixture()
    before, rest, saved = fingerprint(obj), rig_state(rig), saved_metadata(obj)
    C.view_layer.update()
    guides.redraw()
    # Neither bend nor the shared reference cache has been built yet.
    assert not bones._saved_previews
    with no_reads():
        bones.show_bend(C)
        first = {frame['bank_key']: frame for frame in guides.display_frames(C)}
        assert {name for _, name, _ in bones._preview['labels']} == set(NAMES)
        assert bones._preview['groups']
        for _ in range(3):
            result = cache.evaluate_many(C, obj, NAMES)
            for name in NAMES:
                assert result[name][0] is first[name] and not result[name][1]
            assert {frame['bank_key']: frame for frame in guides.display_frames(C)} == first
            bpy.ops.character_designer.finger_definition(action='OVERLAYS')
            assert bones._preview is None
            bpy.ops.character_designer.finger_definition(action='OVERLAYS')
            assert bones._preview and guides.display_frames(C)
            C.view_layer.update()
        retained = bones._preview
        with patch.object(bones.targets, 'index', side_effect=AssertionError('Edit rebuilt bone index')) as index, \
             patch.object(bones.targets, 'resolve', side_effect=AssertionError('Edit resolved bones')) as resolve:
            for step in range(25):
                bm, vertex = edited_vertex(obj)
                vertex.co.z += .0008
                bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
                C.view_layer.update()
                assert bones._preview is retained and bones._request is None, step
                assert bones._valid(C) is retained
                assert {frame['bank_key']: frame for frame in guides.display_frames(C)} == first
                assert not guides._display_cache['errors']
            assert index.call_count == resolve.call_count == 0
        assert saved_metadata(obj) == saved
    assert fingerprint(obj) != before, 'The test must exercise actual mesh edits'
    assert_rig_equal(rest, rig_state(rig))


def test_cold_guides_after_loop_addition_do_not_adapt_saved_definitions():
    obj, _ = fixture()
    expected = {name: direct(obj, name) for name in NAMES}
    saved = saved_metadata(obj)
    split_ring(obj, 'INDEX.L')
    before = fingerprint(obj)
    C.view_layer.update()
    guides.redraw()
    with no_reads():
        result = cache.evaluate_many(C, obj, NAMES)
        thumb = obj.character_designer_finger_bank.slots[NAMES[0]]
        assert cache.lookup(thumb.guide, thumb.error)[0] is result[NAMES[0]][0]
        frames = {value['bank_key']: value for value in guides.display_frames(C)}
    assert set(frames) == set(NAMES)
    assert all(result[name][0] is frames[name] and not result[name][1] for name in NAMES)
    for name in NAMES: assert_frame(frames[name], expected[name])
    with no_reads():
        assert cache.evaluate_many(C, obj, NAMES) == result
        assert len(guides.display_frames(C)) == 5
    assert fingerprint(obj) == before and saved_metadata(obj) == saved


def test_damaged_finger_keeps_saved_visuals_but_explicit_frame_refuses_it():
    obj, _ = fixture()
    expected = {name: direct(obj, name) for name in NAMES}
    saved = saved_metadata(obj)
    added = split_ring(obj, 'INDEX.L')
    bm = edit_mesh(obj)
    bm.verts[added[0]].co.z += .015
    update(obj, bm)
    before = fingerprint(obj)
    C.view_layer.update()
    guides.redraw()
    with no_reads():
        result = cache.evaluate_many(C, obj, NAMES)
    assert all(result[name][0] and not result[name][1] for name in NAMES)
    for name in NAMES: assert_frame(result[name][0], expected[name])
    with no_reads():
        assert cache.evaluate_many(C, obj, NAMES) == result
        assert {frame['bank_key'] for frame in guides.display_frames(C)} == set(NAMES)
        assert not guides._display_cache['errors']
    assert failure(lambda: direct(obj, 'INDEX.L', require_basis=True))
    assert fingerprint(obj) == before and saved_metadata(obj) == saved


def external_top(obj):
    """Capture a real independent YZ face, with a different bend direction."""
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = bpy.data.meshes.new('IndependentBendSurface')
    mesh.from_pydata([(0, 0, 0), (0, 1, 0), (0, 1, 1), (0, 0, 1)], [], [(0, 1, 2, 3)])
    top = bpy.data.objects.new('IndependentBendSurface', mesh)
    C.collection.objects.link(top)
    obj.select_set(False)
    top.select_set(True)
    C.view_layer.objects.active = top
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    source, record = definition._mesh_capture(C, path=False)
    assert source == top and Vector(record['basis']['normal']).x > .99
    bpy.ops.object.mode_set(mode='OBJECT')
    top.select_set(False)
    obj.select_set(True)
    C.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    for name in ('INDEX.L', 'MIDDLE.L'):
        guide = obj.character_designer_finger_bank.slots[name].guide
        guide.bend_source, guide.bend_record = top, json.dumps(record)
    return top


def test_external_bend_visual_is_frozen_but_explicit_action_checks_evidence():
    obj, _ = fixture()
    top = external_top(obj)
    expected = {name: direct(obj, name, require_bend=True) for name in NAMES}
    before, top_before, saved = fingerprint(obj), fingerprint(top), saved_metadata(obj)
    C.view_layer.update()
    guides.redraw()
    with no_reads():
        result = cache.evaluate_many(C, obj, NAMES)
    for name in NAMES: assert_frame(result[name][0], expected[name])
    assert result['MIDDLE.L'][0]['bend'].x < -.99, 'The independent normal was not used'
    assert fingerprint(obj) == before and fingerprint(top) == top_before
    # Editing a separate top object cannot change the accepted saved normal.
    # A bone write still has to prove its current top-surface evidence.
    top.data.vertices[0].co.z += .2
    top.data.update()
    C.view_layer.update()
    changed = fingerprint(top)
    guides.redraw()
    with no_reads():
        result = cache.evaluate_many(C, obj, NAMES)
        for name in NAMES:
            data, error = result[name]
            assert data and not error and not data['bend_error']
            assert_frame(data, expected[name])
        bank.select(C, 'INDEX', 'L')
        bones.hide()
        bones.invalidate()
        bones.show_bend(C)
        assert {name for _, name, _ in bones._preview['labels']} == {'INDEX.L'}
    for name in ('INDEX.L', 'MIDDLE.L'):
        assert failure(lambda: direct(obj, name, require_bend=True))
    assert fingerprint(obj) == before and fingerprint(top) == changed and saved_metadata(obj) == saved


def test_nonbasis_edit_keeps_frozen_basis_visual_and_strict_containment_check():
    obj, _ = fixture()
    expected = {name: direct(obj, name) for name in NAMES}
    saved = saved_metadata(obj)
    state = obj.character_designer_finger_bank
    record = json.loads(state.slots['INDEX.L'].guide.record)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 1
    for index in record['body']['vertices']:
        obj.data.shape_keys.key_blocks['Artist'].data[index].co.z += .3
    obj.data.shape_keys.update_tag()
    C.view_layer.update()
    before = fingerprint(obj)
    guides.redraw()
    with no_reads():
        result = cache.evaluate_many(C, obj, NAMES)
    assert all(result[name][0] and not result[name][1] for name in NAMES)
    for name in NAMES:
        assert_frame(result[name][0], expected[name])
        assert result[name][0]['key'] == 'Basis' and result[name][0]['display_only']
    assert 'visible finger deformation' in failure(lambda: direct(obj, 'INDEX.L'))
    assert direct(obj, 'INDEX.L', require_basis=True)['internal'], 'The saved rest reference was damaged'
    with no_reads():
        repeated = cache.evaluate_many(C, obj, NAMES)
        assert repeated == result
    assert obj.active_shape_key_index == 1 and fingerprint(obj) == before and saved_metadata(obj) == saved


def test_missing_saved_path_is_local_error_and_repair_rebuilds_only_that_record():
    obj, _ = fixture()
    with no_reads():
        original = cache.evaluate_many(C, obj, NAMES)
        slot = obj.character_designer_finger_bank.slots['INDEX.L']
        accepted = slot.guide.record
        broken = json.loads(accepted)
        del broken['internal']['path']
        slot.guide.record = json.dumps(broken)
        result = cache.evaluate_many(C, obj, NAMES)
        assert result['INDEX.L'][0] is None and result['INDEX.L'][1]
        assert len(guides.display_frames(C)) == 4
        assert set(guides._display_cache['errors']) == {'INDEX.L'}
        for name in NAMES:
            if name != 'INDEX.L': assert result[name][0] is original[name][0]
        assert cache.evaluate_many(C, obj, NAMES) == result
        slot.guide.record = accepted
        repaired = cache.evaluate_many(C, obj, NAMES)
        assert repaired['INDEX.L'][0] and not repaired['INDEX.L'][1]
        assert len(guides.display_frames(C)) == 5 and not guides._display_cache['errors']
        for name in NAMES:
            if name != 'INDEX.L': assert repaired[name][0] is original[name][0]


def test_actual_save_reopen_rebuilds_frozen_guides_without_mesh_reads():
    obj, rig = fixture()
    expected = {name: direct(obj, name) for name in NAMES}
    bm, vertex = edited_vertex(obj)
    vertex.co.z += .02
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 1
    obj.data.shape_keys.key_blocks['Artist'].value = .35
    # This saved action warning is allowed to remain; it cannot hide guides.
    obj.character_designer_finger_bank.slots['INDEX.L'].error = 'Saved explicit-action warning'
    names, saved, before, rest = (obj.name, rig.name), saved_metadata(obj), fingerprint(obj), rig_state(rig)
    with TemporaryDirectory(prefix='finger-frozen-display-') as directory:
        path = str(Path(directory)/'edited_frozen_references.blend')
        with no_reads():
            assert bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False) == {'FINISHED'}
            guides.display_frames(C)
            guides._display_request = C.scene, obj
            guides._request = C.scene, None, definition.state(C), obj
            assert bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False) == {'FINISHED'}
            # All previous RNA owners are invalid after load. Reacquire by name.
            obj, rig = bpy.data.objects[names[0]], bpy.data.objects[names[1]]
            assert guides._request is None and guides._display_request is None
            assert bones._preview is None and bones._request is None
            result = cache.evaluate_many(C, obj, NAMES)
            for name in NAMES:
                data, error = result[name]
                assert data and not error
                assert_frame(data, expected[name])
            assert len(guides.display_frames(C)) == 5 and not guides._display_cache['errors']
            assert saved_metadata(obj) == saved
        assert obj.active_shape_key_index == 1
        assert abs(obj.data.shape_keys.key_blocks['Artist'].value-.35) < 1e-6
        assert fingerprint(obj) == before
        assert_rig_equal(rest, rig_state(rig))
        assert failure(lambda: direct(obj, 'INDEX.L', require_basis=True))


if __name__ == '__main__':
    try:
        character_designer.register()
        tests = [fn for name, fn in list(globals().items()) if name.startswith('test_')]
        for test in tests:
            test()
            print('PASS', test.__name__, flush=True)
        print('FINGER_BATCH_PREVIEW_TESTS_PASSED', len(tests), flush=True)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
