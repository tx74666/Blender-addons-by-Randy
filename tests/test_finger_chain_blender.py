"""Basic Setup based straight alignment remains separate from Roll and Relax Bones."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import bound_fixture, C, bank, fingerprint, character_designer, select
from character_designer import finger_chain as chain, finger_targets as targets


def refused(fn, text=''):
    try:
        fn()
    except ValueError as exc:
        assert not text or text.casefold() in str(exc).casefold(), str(exc)
    else:
        raise AssertionError('Unsafe bone position operation was accepted')


def rest(rig):
    with targets.edit_rig(C, rig):
        return chain._snapshot_edit(rig)


def bend_straight_pair(rig, digit='INDEX'):
    with targets.edit_rig(C, rig):
        for side in ('L', 'R'):
            bones = [rig.data.edit_bones[f'f_{digit.lower()}.{i:02d}.{side}'] for i in (1, 2, 3)]
            for i, z in enumerate((.012, .019)):
                point = bones[i].tail+Vector((0, 0, z))
                bones[i].tail, bones[i+1].head = point, point


def test_alignment_is_separate_from_roll_and_never_runs_on_thumb():
    obj, rig, _ = bound_fixture()
    rig.data.use_mirror_x = True
    bend_straight_pair(rig)
    before, mesh_before = rest(rig), fingerprint(obj)
    result = chain.align(C, 'INDEX')
    assert result['changed'] > 0 and result['keys'] == ['INDEX.L']
    after = rest(rig)
    for side in ('L',):
        names = [f'f_index.{i:02d}.{side}' for i in (1, 2, 3)]
        root, tip = before[names[0]]['head'], before[names[-1]]['tail']
        for name in names:
            assert after[name]['roll'] == before[name]['roll']
            assert (after[name]['head']-root).cross(tip-root).length < 1e-6
        assert after[names[0]]['head'] == root and after[names[-1]]['tail'] == tip
    assert all(after[name] == item for name, item in before.items()
               if not (name.startswith('f_index.') and name.endswith('.L')))
    assert rig.data.use_mirror_x
    assert fingerprint(obj) == mesh_before and C.edit_object == obj
    refused(lambda: chain.align(C, 'THUMB'), 'excludes Thumb')
    assert rest(rig) == after


def test_alignment_pose_guard_leaves_scene_unchanged():
    obj, rig, _ = bound_fixture()
    bank.select(C, 'INDEX')
    bend_straight_pair(rig)
    rig.pose.bones['f_index.02.L'].rotation_mode = 'XYZ'
    rig.pose.bones['f_index.02.L'].rotation_euler.x = .1
    before = rest(rig)
    refused(lambda: chain.align(C), 'neutral')
    assert rest(rig) == before


def test_active_alignment_ignores_opposite_geometry_but_checks_source():
    obj, rig, _ = bound_fixture()
    rig.data.use_mirror_x = True
    bend_straight_pair(rig)
    state = obj.character_designer_finger_bank
    report = json.loads(state.survey)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for index in report['candidates']['INDEX.R']['rings'][3]: bm.verts[index].co.z += .01
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    report['warnings']['INDEX'] = bank.PAIR_SYNC_WARNING
    state.survey = json.dumps(report)
    state.slots['INDEX.L'].error = bank.PAIR_SYNC_WARNING
    state.slots['INDEX.R'].error = 'Opposite saved reference is stale.'
    rig.pose.bones['f_index.02.R'].rotation_mode = 'XYZ'
    rig.pose.bones['f_index.02.R'].rotation_euler.x = .2
    before, mesh = rest(rig), fingerprint(obj)
    with patch.object(targets, 'pair', side_effect=AssertionError('Active alignment inspected the opposite side')):
        result = chain.align(C, 'INDEX')
    assert result['keys'] == ['INDEX.L'] and result['changed'] > 0
    after = rest(rig)
    assert all(after[name] == value for name, value in before.items()
               if not (name.startswith('f_index.') and name.endswith('.L')))
    assert fingerprint(obj) == mesh and rig.data.use_mirror_x
    assert state.slots['INDEX.L'].error == bank.PAIR_SYNC_WARNING, 'Resolving does not rewrite metadata'
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[report['candidates']['INDEX.L']['rings'][3][0]].co.z += .01
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    before, mesh = rest(rig), fingerprint(obj)
    refused(lambda: chain.align(C, 'INDEX'))
    assert rest(rig) == before and fingerprint(obj) == mesh


def test_right_active_alignment_keeps_left_and_all_other_chains():
    obj, rig, chosen = bound_fixture()
    select(obj, chosen['INDEX.R'])
    bank.capture(C)
    bank.select(C, 'INDEX', 'R')
    rig.data.use_mirror_x = True
    bend_straight_pair(rig)
    before, mesh = rest(rig), fingerprint(obj)
    result = chain.align(C)
    assert result['keys'] == ['INDEX.R'] and result['changed'] > 0
    after = rest(rig)
    assert all(after[name] == value for name, value in before.items()
               if not (name.startswith('f_index.') and name.endswith('.R')))
    assert fingerprint(obj) == mesh and rig.data.use_mirror_x


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FINGER_CHAIN_PASS', flush=True)
