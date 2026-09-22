"""Explicit region mirror syncs proven metadata and never moves bones."""
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import (
    bound_fixture, select, character_designer, C, bank, definition,
    fingerprint, rig_state, assert_rig_equal,
)
from test_finger_topology_adapt_blender import edit_mesh, update
from character_designer import finger_loop_marks as marks
from character_designer import finger_mirror_sync as sync, mesh_mirror as mirror, mesh_mirror_ui as ui


def fixture():
    obj, rig, _ = bound_fixture()
    # The general bone fixture locks this all-body artist group. Region mirror
    # intentionally replaces opposite memberships, so this fixture opts in to
    # their edit without weakening the production locked-group guard.
    obj.vertex_groups['OtherDeform'].lock_weight = False
    rig.data.use_mirror_x = True  # Mesh convenience must never broaden to bones.
    C.scene.character_designer_mesh_mirror.reference = None
    C.scene.character_designer_mesh_mirror.tolerance = 0
    bank.select(C, 'INDEX', 'L')
    return obj, rig


def values(slot):
    return tuple(getattr(slot.guide, field) for field in bank.FIELDS)+(slot.error, slot.bones)


def setup_work(obj, *, partial=False):
    state = obj.character_designer_finger_bank
    record = json.loads(state.slots['INDEX.L'].guide.record)
    bm = edit_mesh(obj)
    # Mark two actual existing loops before making a different middle-row edit.
    saved = marks._saved(obj)
    saved['INDEX.L'] = {str(number): marks._marker(obj, 'INDEX.L', [bm.verts[i] for i in row], record)
                        for number, row in enumerate((record['body']['rings'][1], record['body']['rings'][5]), 1)}
    saved['MIDDLE.R'] = {'1': {'sentinel': 'unrelated marks stay byte-equivalent'}}
    obj[marks.PROPERTY] = json.dumps(saved)
    for index in record['body']['rings'][3]: bm.verts[index].co.z += .004
    update(obj, bm)
    faces = record['body']['faces']
    if partial:
        vertices = {index for row in record['body']['rings'][2:5] for index in row}
        bm = edit_mesh(obj)
        faces = [face.index for face in bm.faces if all(v.index in vertices for v in face.verts)]
        assert len(faces) == 16
    select(obj, faces)
    return faces


def apply_direct(obj, *, target_faces=None):
    plan = mirror.build_plan(C)
    if target_faces is not None:
        # Model the user's explicit numbered choice using independently known
        # fixture faces, never an arbitrary candidate or relaxed matching.
        matches = [index for index, candidate in enumerate(plan.candidates, 1)
                   if set(candidate.faces) == set(target_faces)]
        assert len(matches) == 1, [(i, candidate.faces) for i, candidate in enumerate(plan.candidates, 1)]
        plan = mirror.build_plan(C, target_candidate=matches[0])
        assert set(plan.target_faces) == set(target_faces)
    assert not plan.needs_choice, 'This fixture must provide an explicit target when matching is ambiguous'
    packet = sync.prepare(plan)
    bpy.ops.object.mode_set(mode='OBJECT')
    outcome = {}
    def committed(_selection): outcome.update(sync.apply(C, plan, packet))
    mirror.apply_plan(plan, after_commit=committed)
    return plan, packet, outcome


def test_full_finger_mirrors_edited_geometry_and_syncs_only_target_reference_marks():
    obj, rig = fixture()
    setup_work(obj)
    state = obj.character_designer_finger_bank
    state.slots['INDEX.R'].bones = '{"stale": "binding"}'
    state.slots['INDEX.R'].error = bank.PAIR_SYNC_WARNING
    before = {slot.name: values(slot) for slot in state.slots}
    source_marks, other_marks = marks.records(obj, 'INDEX.L'), marks.records(obj, 'MIDDLE.R')
    rest = rig_state(rig)
    # This source has edited geometry. Old capture validation must not block
    # explicit mesh mirroring; current containment is proved independently.
    with patch.object(definition, 'frame', side_effect=AssertionError('Sync used old capture proof')), \
         patch.object(definition, '_validate_mesh', side_effect=AssertionError('Sync rejected old source evidence')):
        plan, packet, result = apply_direct(obj)
    assert result == {'synced': 1, 'unchanged': 0}, result
    assert set(packet['sources']) == {'INDEX.L'}
    assert {slot.name: values(slot) for slot in state.slots if slot.name != 'INDEX.R'} == {
        key: value for key, value in before.items() if key != 'INDEX.R'}
    target = state.slots['INDEX.R']
    assert not target.bones and not target.error and target.guide.confirmed
    assert definition.frame(bank.scoped(C, target.guide), require_basis=True,
                            require_confirmed=True)['internal']
    assert 'INDEX' not in json.loads(state.survey)['warnings']
    assert state.active == 'INDEX.L'
    assert marks.records(obj, 'INDEX.L') == source_marks
    assert marks.records(obj, 'MIDDLE.R') == other_marks
    target_marks = marks.records(obj, 'INDEX.R')
    assert set(target_marks) == {'1', '2'}
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        reference = json.loads(target.guide.record)
        for number, marker in target_marks.items():
            vertices = marks._recover(bm, marker, int(number))
            marks._ring(obj, bm, reference, vertices)
            assert (Vector(marker['center'])-plan.reflection @ Vector(source_marks[number]['center'])).length < 1e-6
    finally: bm.free()
    assert_rig_equal(rest, rig_state(rig))


def test_partial_region_mirrors_without_replacing_any_saved_reference_or_mark():
    obj, rig = fixture()
    setup_work(obj, partial=True)
    state = obj.character_designer_finger_bank
    target_body = json.loads(state.slots['INDEX.R'].guide.record)['body']
    vertices = {index for row in target_body['rings'][2:5] for index in row}
    bm = edit_mesh(obj)
    target_faces = [face.index for face in bm.faces if all(v.index in vertices for v in face.verts)]
    assert len(target_faces) == 16
    before = {slot.name: values(slot) for slot in state.slots}
    stored, rest = obj[marks.PROPERTY], rig_state(rig)
    _, packet, result = apply_direct(obj, target_faces=target_faces)
    assert not packet['sources'] and result == {'synced': 0, 'unchanged': 1}, result
    assert {slot.name: values(slot) for slot in state.slots} == before
    assert obj[marks.PROPERTY] == stored
    assert 'INDEX' not in json.loads(state.survey)['warnings'], 'The explicit badge refresh must see the repaired pair'
    assert_rig_equal(rest, rig_state(rig))


def test_ambiguous_partial_target_does_not_mirror_or_attempt_metadata_sync():
    obj, rig = fixture()
    setup_work(obj, partial=True)
    plan = mirror.build_plan(C)
    assert plan.needs_choice and plan.candidates, 'Exercise the real target-choice boundary'
    before, saved, rest = fingerprint(obj), sync.snapshot(obj), rig_state(rig)
    reports = []
    operator = SimpleNamespace(sync_fingers=True, target_candidate=0,
                               report=lambda levels, message: reports.append((levels, message)))
    with patch.object(sync, 'prepare', side_effect=AssertionError('Ambiguous target prepared sync')) as prepare, \
         patch.object(sync, 'apply', side_effect=AssertionError('Ambiguous target applied sync')) as apply:
        assert ui.CHARACTERDESIGNER_OT_mesh_mirror.execute(operator, C) == {'CANCELLED'}
        prepare.assert_not_called()
        apply.assert_not_called()
    assert len(reports) == 1 and 'Choose a numbered target candidate' in reports[0][1]
    assert fingerprint(obj) == before and sync.snapshot(obj) == saved
    assert_rig_equal(rest, rig_state(rig))


def test_corrupt_source_reference_keeps_mesh_mirror_available_and_metadata_untouched():
    obj, rig = fixture()
    setup_work(obj)
    state = obj.character_designer_finger_bank
    state.slots['INDEX.L'].guide.record = '{broken saved reference'
    before = {slot.name: values(slot) for slot in state.slots}
    stored, rest = obj[marks.PROPERTY], rig_state(rig)
    _, _, result = apply_direct(obj)
    assert result == {'synced': 0, 'unchanged': 1}, result
    assert {slot.name: values(slot) for slot in state.slots} == before
    assert obj[marks.PROPERTY] == stored
    assert 'INDEX' not in json.loads(state.survey)['warnings']
    assert_rig_equal(rest, rig_state(rig))


def test_metadata_commit_failure_rolls_back_mesh_groups_and_every_saved_field():
    obj, rig = fixture()
    setup_work(obj)
    plan = mirror.build_plan(C)
    packet = sync.prepare(plan)
    bpy.ops.object.mode_set(mode='OBJECT')
    old, before, saved, rest = obj.data, fingerprint(obj), sync.snapshot(obj), rig_state(rig)
    original = sync._commit
    def broken(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('injected metadata commit failure')
    with patch.object(sync, '_commit', side_effect=broken):
        try: mirror.apply_plan(plan, after_commit=lambda _: sync.apply(C, plan, packet))
        except RuntimeError as exc: assert 'injected metadata' in str(exc)
        else: raise AssertionError('Injected sync failure was swallowed')
    assert obj.data == old and fingerprint(obj) == before
    assert sync.snapshot(obj) == saved
    assert_rig_equal(rest, rig_state(rig))


def test_existing_operator_opt_in_is_one_undo_action_and_restores_source_selection():
    obj, rig = fixture()
    setup_work(obj)
    rest = rig_state(rig)
    operator = bpy.ops.character_designer.mirror_selected_region
    assert operator.get_rna_type().properties['sync_fingers'].default is False
    assert 'UNDO' in ui.CHARACTERDESIGNER_OT_mesh_mirror.bl_options
    assert operator(sync_fingers=True) == {'FINISHED'}
    assert C.mode == 'EDIT_MESH' and C.edit_object == obj
    assert obj.character_designer_finger_bank.active == 'INDEX.L'
    assert obj.character_designer_finger_bank.bone_status == 'Mirrored; synced 1 finger(s), 0 left unchanged'
    bm = edit_mesh(obj)
    assert any(v.select for v in bm.verts)
    assert all(v.co.x > 0 for v in bm.verts if v.select), 'Source-side selection must remain active'
    assert_rig_equal(rest, rig_state(rig))


def test_plain_existing_mirror_does_not_sync_and_failed_plan_never_calls_sync():
    obj, _ = fixture()
    setup_work(obj)
    saved = sync.snapshot(obj)
    with patch.object(sync, 'prepare', side_effect=AssertionError('Plain mirror requested sync')):
        assert bpy.ops.character_designer.mirror_selected_region() == {'FINISHED'}
    assert sync.snapshot(obj) == saved
    select(obj, ())
    before = fingerprint(obj)
    reports = []
    failed = SimpleNamespace(sync_fingers=True, target_candidate=0,
                             report=lambda levels, message: reports.append((levels, message)))
    with patch.object(sync, 'apply', side_effect=AssertionError('Failed mirror attempted sync')):
        assert ui.CHARACTERDESIGNER_OT_mesh_mirror.execute(failed, C) == {'CANCELLED'}
    assert len(reports) == 1 and reports[0][0] == {'ERROR'}
    assert sync.snapshot(obj) == saved and fingerprint(obj) == before


def test_post_commit_display_failure_keeps_successful_mesh_and_metadata_result():
    from character_designer import finger_loop_marks_ui
    obj, rig = fixture()
    setup_work(obj)
    before, rest = fingerprint(obj), rig_state(rig)
    old = obj.data
    reports = []
    operator = SimpleNamespace(sync_fingers=True, target_candidate=0,
                               report=lambda levels, message: reports.append((levels, message)))
    with patch.object(finger_loop_marks_ui, 'refresh', side_effect=RuntimeError('injected display failure')) as refresh:
        assert ui.CHARACTERDESIGNER_OT_mesh_mirror.execute(operator, C) == {'FINISHED'}
        refresh.assert_called_once()
    assert obj.data != old and fingerprint(obj) != before
    assert C.mode == 'EDIT_MESH' and C.edit_object == obj
    assert reports == [({'INFO'}, 'Mirrored; synced 1 finger(s), 0 left unchanged')], reports
    target = obj.character_designer_finger_bank.slots['INDEX.R']
    assert definition.frame(bank.scoped(C, target.guide), require_basis=True)['internal']
    assert set(marks.records(obj, 'INDEX.R')) == {'1', '2'}
    assert_rig_equal(rest, rig_state(rig))


if __name__ == '__main__':
    try:
        character_designer.register()
        tests = [test for name, test in list(globals().items()) if name.startswith('test_')]
        for test in tests:
            test()
            print('PASS', test.__name__, flush=True)
        print('FINGER_MIRROR_SYNC_TESTS_PASSED', len(tests), flush=True)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
