"""Cold saved-guide reconstruction must never inspect current geometry."""
import json
import math
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import bound_fixture, character_designer, C, bank, definition, fingerprint, close
from character_designer import finger_bones, finger_internal, finger_targets


@contextmanager
def no_geometry_reads():
    """Cold display must pass even with every expensive/proof entry blocked."""
    blocked = ((definition, '_snapshot'), (definition, '_validate_mesh'),
               (definition, 'frame'), (definition, 'FrameReader'), (definition, '_key_name'),
               (bank, 'dirty'), (bank, 'adapt_topology'), (bank, 'remap_record'), (bank, 'recheck'),
               (finger_internal, 'verify'), (finger_internal, 'Volume'),
               (finger_targets, 'resolve'), (finger_targets, 'pair'),
               (finger_bones, '_bone_collection'), (bmesh, 'from_edit_mesh'), (bmesh, 'new'))
    with ExitStack() as stack:
        for owner, name in blocked:
            stack.enter_context(patch.object(owner, name, side_effect=AssertionError('Saved display read geometry: '+name)))
        yield


def scoped(guide):
    return SimpleNamespace(scene=C.scene, finger_definition=guide)


def saved_copy(guide, **changes):
    # Independent plain metadata mirrors loading saved RNA after cache loss.
    values = {name: getattr(guide, name) for name in (
        'source', 'record', 'use_basis', 'bend_source', 'bend_record', 'flip_bend', 'confirmed')}
    values.update(changes)
    return SimpleNamespace(**values)


def normalized(frame):
    result = dict(frame)
    result.pop('display_only', None)
    result['path'] = tuple(tuple(point) for point in result['path'])
    for key in ('root', 'tip', 'midpoint', 'direction', 'bend'):
        if result[key] is not None: result[key] = tuple(result[key])
    return result


def test_cold_saved_frame_matches_capture_then_survives_live_geometry_edits():
    obj, _, _ = bound_fixture()
    guide = obj.character_designer_finger_bank.slots['INDEX.L'].guide
    expected = normalized(definition.frame(scoped(guide)))
    text = guide.record
    with no_geometry_reads():
        displayed = definition.saved_frame(scoped(guide))
    assert displayed['display_only'] and normalized(displayed) == expected
    record = json.loads(text)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[record['body']['rings'][2][0]].co.z += .025
    bm.verts.new((50., 50., 50.))  # Unrelated topology must not trigger recensus.
    bmesh.update_edit_mesh(obj.data)
    obj.character_designer_finger_bank.slots['INDEX.L'].error = 'Old live-geometry warning'
    before = fingerprint(obj)
    with no_geometry_reads():
        for _ in range(3):
            restored = saved_copy(guide)
            actual = definition.saved_frame(scoped(restored))
            assert normalized(actual) == expected
            assert actual['bend_error'] == ''
    assert guide.record == text and fingerprint(obj) == before
    try: definition.frame(scoped(guide))
    except ValueError: pass
    else: raise AssertionError('Strict write reference incorrectly accepted edited geometry')


def test_basis_internal_path_is_frozen_across_mode_and_shape_key_changes():
    obj, _, _ = bound_fixture()
    guide = obj.character_designer_finger_bank.slots['INDEX.L'].guide
    captured = json.loads(guide.record)
    expected = [obj.matrix_world @ Vector(point) for point in captured['internal']['path']]
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 1
    for point in obj.active_shape_key.data: point.co.z += 4.
    restored = saved_copy(guide, use_basis=False)
    before = fingerprint(obj)
    with no_geometry_reads():
        displayed = definition.saved_frame(scoped(restored))
        assert displayed['basis'] and displayed['key'] == captured['basis_key']
        assert displayed['internal'] and displayed['path'] == expected
    bpy.ops.object.mode_set(mode='EDIT')
    with no_geometry_reads():
        repeated = definition.saved_frame(scoped(restored))
        assert normalized(repeated) == normalized(displayed)
    assert obj.active_shape_key_index == 1 and fingerprint(obj) == before


def test_saved_points_follow_only_the_source_object_transform():
    obj, _, _ = bound_fixture()
    guide = obj.character_designer_finger_bank.slots['INDEX.L'].guide
    with no_geometry_reads(): original = definition.saved_frame(scoped(guide))
    transform = Matrix.Translation((.2, -.4, .7)) @ Matrix.Rotation(.35, 4, 'Z') @ Matrix.Scale(2., 4)
    obj.matrix_world = transform @ obj.matrix_world
    with no_geometry_reads(): moved = definition.saved_frame(scoped(saved_copy(guide)))
    for point, actual in zip(original['path'], moved['path']): close(transform @ point, actual)
    close(transform @ original['midpoint'], moved['midpoint'])
    assert abs(moved['length']-original['length']*2) < 1e-6
    close(transform.to_3x3() @ original['bend'] / 2, moved['bend'])


def standalone(source, *, kind='BONES'):
    sample = {'path': [[0., 0., 0.], [.15, 1., 0.], [.2, 2., .05], [0., 3., .1]],
              'normal': None, 'label': 'Rest bone chain'}
    record = {'kind': kind, 'key': 'Basis', 'basis_key': 'Basis', 'basis': sample,
              'current': sample, 'direction': 'Bone Head to Tail',
              'bones': [{'name': 'DeletedBone', 'head': [0, 0, 0], 'tail': [0, 3, 0], 'parent': ''}]}
    return SimpleNamespace(source=source, record=json.dumps(record), use_basis=True,
                           bend_source=None, bend_record='', flip_bend=False, confirmed=True)


def test_standalone_saved_bone_path_does_not_resolve_live_bones():
    if C.object and C.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    rig = bpy.data.objects.new('EmptySavedRig', bpy.data.armatures.new('EmptySavedRig'))
    C.collection.objects.link(rig)
    assert not rig.data.bones
    guide = standalone(rig)
    with no_geometry_reads(): displayed = definition.saved_frame(scoped(guide))
    assert displayed['path'] == list(map(Vector, json.loads(guide.record)['basis']['path']))
    assert displayed['length'] > 3. and not displayed['internal'] and displayed['bend'] is None
    assert displayed['display_only']
    try: definition.frame(scoped(guide))
    except ValueError: pass
    else: raise AssertionError('Strict bone reference accepted a missing bone')


def test_saved_top_normal_has_precedence_and_missing_evidence_omits_bend():
    source = bpy.data.objects.new('SavedGuideSource', None)
    top = bpy.data.objects.new('SavedGuideTop', None)
    C.collection.objects.link(source)
    C.collection.objects.link(top)
    top.matrix_world = Matrix.Rotation(math.pi/2, 4, 'Z')
    guide = standalone(source, kind='MESH')
    record = json.loads(guide.record)
    record['basis'].update(path=[[0., 0., 0.], [0., 3., 0.]], normal=[0., 0., 1.])
    guide.record = json.dumps(record)
    guide.bend_source = top
    guide.bend_record = json.dumps({'basis': {'normal': [0., 1., 0.]}})
    with no_geometry_reads():
        displayed = definition.saved_frame(scoped(guide))
        close(displayed['bend'], (1., 0., 0.))
        assert not displayed['bend_error']
        reversed_guide = saved_copy(guide, flip_bend=True)
        close(definition.saved_frame(scoped(reversed_guide))['bend'], (-1., 0., 0.))
        missing = definition.saved_frame(scoped(saved_copy(guide, bend_source=None)))
        assert missing['bend'] is None and missing['bend_error'] == 'Saved bend reference is unavailable.'
        malformed = definition.saved_frame(scoped(saved_copy(guide, bend_record='{broken')))
        assert malformed['bend'] is None
        assert malformed['path'] == displayed['path']


def test_malformed_saved_path_fails_without_falling_back_to_geometry():
    source = bpy.data.objects.new('MalformedGuideSource', None)
    C.collection.objects.link(source)
    guide = standalone(source)
    malformed = ('{}', '[]', '{broken', json.dumps({'basis': {'path': [[0, 0, 0], [0, 0, 0]]}}),
                 json.dumps({'basis': {'path': [[0, 0, 0], [float('nan'), 1, 0]]}}))
    with no_geometry_reads():
        for text in malformed:
            try: definition.saved_frame(scoped(saved_copy(guide, record=text)))
            except ValueError: pass
            else: raise AssertionError('Invalid saved path was accepted')


def test_legacy_pending_start_and_show_use_saved_data_only():
    from character_designer import finger_definition_ui as ui
    obj, _, _ = bound_fixture()
    guide = obj.character_designer_finger_bank.slots['INDEX.L'].guide
    pending = SimpleNamespace(pending_source=obj, pending=guide.record)
    with no_geometry_reads():
        first = definition.saved_pending_frame(scoped(pending))
        assert bpy.ops.character_designer.finger_definition(action='SHOW') == {'FINISHED'}
    record = json.loads(guide.record)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[record['body']['rings'][2][0]].co.z += .02
    bmesh.update_edit_mesh(obj.data)
    with no_geometry_reads():
        assert definition.saved_pending_frame(scoped(pending)) == first
        assert ui.cached_frame(C)[0]['display_only']


def test_obsolete_monitor_errors_clear_without_changing_saved_reference():
    obj, _, _ = bound_fixture()
    state = obj.character_designer_finger_bank
    slot = state.slots['INDEX.L']
    saved = slot.guide.record
    message = 'This finger surface changed beyond complete loop subdivision/dissolve; rebind this finger only.'
    slot.error = slot.guide.status = state.bone_status = message
    state.slots['INDEX.R'].error = bank.PAIR_SYNC_WARNING
    state.status = 'An unrelated explicit action message'
    with no_geometry_reads():
        assert bank.discard_legacy_view_errors() == 3
        assert not slot.error and not slot.guide.status and not state.bone_status
        assert slot.guide.record == saved and slot.guide.confirmed
        assert state.slots['INDEX.R'].error == bank.PAIR_SYNC_WARNING
        assert state.status == 'An unrelated explicit action message'
        assert bank.discard_legacy_view_errors() == 0


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FINGER_SAVED_GUIDES_PASS', flush=True)
