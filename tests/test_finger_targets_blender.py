"""Whole-chain Bone Roll calibration independent of retired joint topology."""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
import bmesh
import bpy
from mathutils import Vector, Quaternion
sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import bound_fixture, fingerprint, targets, refused, character_designer, C, bank, select


def rolls(rig):
    with targets.edit_rig(C, rig):
        return {bone.name: bone.roll for bone in rig.data.edit_bones}


def assert_other_rolls(rig, before, side, digits=None):
    after = rolls(rig)
    for name, value in before.items():
        affected = name.endswith('.'+side) and (digits is None or any(name.startswith('f_'+digit.lower()+'.') for digit in digits))
        if not affected: assert abs(after[name]-value) < 1e-6, name

def test_all_and_selected_complete_chains():
    obj, rig, chosen = bound_fixture()
    rig.data.use_mirror_x = True
    old_rolls = rolls(rig)
    before = fingerprint(obj)
    geometry = {b.name: (tuple(b.head_local), tuple(b.tail_local), b.parent.name if b.parent else '') for b in rig.data.bones}
    start = time.perf_counter()
    result = targets.calibrate(C)
    print('PERF_ALL', time.perf_counter()-start, result, flush=True)
    assert len(result['success']) == 5 and not result['skipped'] and not result['failed']
    assert result['success']['THUMB'] == 2
    assert_other_rolls(rig, old_rolls, 'L')
    assert rig.data.use_mirror_x
    assert C.edit_object == obj and fingerprint(obj) == before
    assert geometry == {b.name: (tuple(b.head_local), tuple(b.tail_local), b.parent.name if b.parent else '') for b in rig.data.bones}
    for key, body in json.loads(obj.character_designer_finger_bank.survey)['candidates'].items():
        if not key.endswith('.L'): continue
        r = json.loads(obj.character_designer_finger_bank.slots[key].guide.record)
        n = Vector(r['basis']['normal'])
        for b in targets.resolve(obj, rig, key, targets.index(rig)):
            t = (b.tail_local-b.head_local).normalized()
            x = b.matrix_local.to_3x3().col[0]
            assert (Quaternion(x, .05) @ t-t).normalized().dot((-n+t*n.dot(t)).normalized()) > .99
    refused(lambda: targets.calibrate(C, selected=True))
    bpy.ops.object.mode_set(mode='OBJECT'); obj.select_set(False); rig.select_set(True); C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in rig.data.edit_bones: bone.select = bone.select_head = bone.select_tail = False
    rig.data.edit_bones['f_middle.02.L'].select = True
    result = targets.calibrate(C, selected=True)
    assert result['success'] == {'MIDDLE': 3}, result
    assert C.edit_object == rig

def test_pair_roll_rollback_and_stale_chain():
    obj, rig, _ = bound_fixture()
    with targets.edit_rig(C, rig):
        old = {b.name: b.roll for b in rig.data.edit_bones}
    def fail(digit):
        if digit == 'INDEX': raise RuntimeError('injected active-side failure')
    result = targets.calibrate(C, after_pair=fail)
    assert 'INDEX' in result['failed'] and len(result['success']) == 4
    with targets.edit_rig(C, rig):
        assert all(abs(b.roll-old[b.name]) < 1e-6 for b in rig.data.edit_bones if 'index' in b.name)
        rig.data.edit_bones['f_middle.02.L'].tail.x += .02
    result = targets.calibrate(C)
    assert 'MIDDLE' in result['skipped']

def test_multiple_selection_ambiguity_controllers_and_pose_guard():
    obj, rig, _ = bound_fixture()
    with targets.edit_rig(C, rig):
        for side in ('L', 'R'):
            parent = None
            for i in (1, 2, 3):
                original = rig.data.edit_bones[f'f_index.{i:02d}.{side}']
                other = rig.data.edit_bones.new(f'f_index_alt.{i:02d}.{side}')
                other.head, other.tail, other.parent = original.head, original.tail, parent
                parent = other
            controller = rig.data.edit_bones.new('ctrl_index.01.'+side)
            controller.head, controller.tail = (0, 0, 0), (0, 1, 0)
    result = targets.calibrate(C)
    assert 'INDEX' in result['skipped'] and 'Multiple candidate' in result['skipped']['INDEX']
    assert len(result['success']) == 4
    bpy.ops.object.mode_set(mode='OBJECT'); obj.select_set(False); rig.select_set(True); C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    for b in rig.data.edit_bones: b.select = b.select_head = b.select_tail = False
    for name in ('f_index.02.L', 'f_index.01.R', 'f_middle.02.R'): rig.data.edit_bones[name].select = True
    result = targets.calibrate(C, selected=True)
    assert result['success'] == {'INDEX': 3}, result
    bpy.ops.object.mode_set(mode='POSE')
    rig.pose.bones['f_index.02.R'].rotation_mode = 'XYZ'
    rig.pose.bones['f_index.02.R'].rotation_euler.x = .2
    result = targets.calibrate(C)
    assert 'INDEX' in result['success'], 'An opposite pose must not block active-side calibration'
    rig.pose.bones['f_index.02.L'].rotation_mode = 'XYZ'
    rig.pose.bones['f_index.02.L'].rotation_euler.x = .2
    result = targets.calibrate(C)
    assert 'INDEX' in result['skipped'] and 'neutral' in result['skipped']['INDEX']


def test_opposite_mismatch_does_not_block_but_active_geometry_remains_strict():
    obj, rig, _ = bound_fixture()
    rig.data.use_mirror_x = True
    state = obj.character_designer_finger_bank
    report = json.loads(state.survey)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for index in report['candidates']['INDEX.R']['rings'][3]: bm.verts[index].co.z += .01
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    report['warnings']['INDEX'] = bank.PAIR_SYNC_WARNING
    state.survey = json.dumps(report)
    state.slots['INDEX.L'].error = bank.PAIR_SYNC_WARNING
    state.slots['INDEX.R'].error = 'Opposite reference needs a new Capture.'
    before, mesh = rolls(rig), fingerprint(obj)
    with patch.object(targets, 'pair', side_effect=AssertionError('Calibration inspected opposite side')):
        result = targets.calibrate(C)
    assert len(result['success']) == 5 and not result['failed'] and not result['skipped'], result
    assert_other_rolls(rig, before, 'L')
    assert fingerprint(obj) == mesh and rig.data.use_mirror_x
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[report['candidates']['INDEX.L']['rings'][3][0]].co.z += .01
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=False)
    before, mesh = rolls(rig), fingerprint(obj)
    result = targets.calibrate(C)
    assert 'INDEX' in result['skipped'], result
    assert fingerprint(obj) == mesh
    after = rolls(rig)
    assert all(abs(after[name]-value) < 1e-6 for name, value in before.items() if name.startswith('f_index.'))


def test_selected_uses_current_side_and_right_choice_never_changes_left():
    obj, rig, chosen = bound_fixture()
    select(obj, chosen['INDEX.R'])
    bank.capture(C)
    bank.select(C, 'INDEX', 'L')
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)
    rig.select_set(True)
    C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    rig.data.use_mirror_x = True
    for bone in rig.data.edit_bones: bone.select = bone.select_head = bone.select_tail = False
    rig.data.edit_bones['f_index.02.R'].select = True
    before = rolls(rig)
    refused(lambda: targets.calibrate(C, selected=True))
    assert rolls(rig) == before
    bank.select(C, 'INDEX', 'R')
    result = targets.calibrate(C, selected=True)
    assert result['success'] == {'INDEX': 3}, result
    assert_other_rolls(rig, before, 'R', ('INDEX',))
    assert rig.data.use_mirror_x and C.edit_object == rig

if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FINGER_TARGETS_PASS', flush=True)
