"""Finger roll inspection, preview, selection safety and correction."""

import math
import os
import sys
from types import SimpleNamespace

import bpy
from mathutils import Quaternion, Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "addons")]

import character_designer
from character_designer import finger_bones
from character_designer.ui_constants import UI_PAGE_RIG


def fixture():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    data = bpy.data.armatures.new("Finger Test Rig")
    rig = bpy.data.objects.new("Finger Test Rig", data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    def bone(name, head, tail, parent=None, roll=0.0):
        item = data.edit_bones.new(name)
        item.head, item.tail, item.parent, item.roll = head, tail, parent, roll
        return item

    index_01 = bone("f_index.01.L", (0, 0, 0), (0, 1, 0))
    index_02 = bone("f_index.02.L", (0, 1, 0), (0.18, 1.95, 0), index_01, 0.63)
    index_03 = bone("f_index.03.L", (0.18, 1.95, 0), (0.34, 2.76, 0), index_02, -0.42)
    pinky_01 = bone("f_pinky.01.R", (-0.7, 0, 0), (-0.7, 0.95, 0), roll=0.17)
    pinky_02 = bone("f_pinky.02.R", (-0.7, 0.95, 0), (-0.55, 1.82, 0), pinky_01, roll=-0.51)
    body = bone("upperarm.L", (0, 0, -1), (0, 0, -2))
    for item in (index_01, index_02, index_03, pinky_01, pinky_02, body):
        item.select = True
    for source in (index_01, index_02, index_03, pinky_01, pinky_02):
        name = bpy.utils.flip_name(source.name)
        parent = data.edit_bones.get(bpy.utils.flip_name(source.parent.name)) if source.parent else None
        target = bone(name, (-source.head.x, source.head.y, source.head.z),
                      (-source.tail.x, source.tail.y, source.tail.z), parent, -.83)
        target.select = target.select_head = target.select_tail = False
    data.edit_bones.active = index_01
    return rig, (index_01, index_02, index_03, pinky_01, pinky_02, body)


def test_selection_is_finger_only_and_check_is_read_only():
    rig, bones = fixture()
    settings = bpy.context.window_manager.character_designer
    settings.ui_page = UI_PAGE_RIG
    settings.rig_section = "BODY"
    settings.finger_axis = "X"
    settings.finger_reference = "CHAIN_ROOT"
    rolls = {bone.name: bone.roll for bone in bones}
    result = bpy.ops.character_designer.finger_roll(action="CHECK")
    assert result == {"FINISHED"}
    assert "Ignored 1 non-finger selection(s)" in settings.finger_status
    assert "8 target segment(s)" in settings.finger_status
    assert {bone.name: bone.roll for bone in bones} == rolls


def test_preview_is_non_destructive_and_apply_only_changes_roll():
    rig, bones = fixture()
    settings = bpy.context.window_manager.character_designer
    settings.finger_axis = "X"
    settings.finger_reference = "CHAIN_ROOT"
    before = {
        bone.name: (Vector(bone.head), Vector(bone.tail), float(bone.roll))
        for bone in bones
    }
    assert bpy.ops.character_designer.finger_roll(action="PREVIEW") == {"FINISHED"}
    assert settings.finger_preview_active
    assert {
        bone.name: (Vector(bone.head), Vector(bone.tail), float(bone.roll))
        for bone in bones
    } == before
    assert bpy.ops.character_designer.finger_roll(action="HIDE_PREVIEW") == {"FINISHED"}
    assert not settings.finger_preview_active

    assert bpy.ops.character_designer.finger_roll(action="APPLY") == {"FINISHED"}
    assert "Corrected" in settings.finger_status
    assert math.isclose(bones[0].roll, before[bones[0].name][2], abs_tol=1.0e-7)
    assert math.isclose(bones[3].roll, before[bones[3].name][2], abs_tol=1.0e-7)
    assert math.isclose(bones[5].roll, before[bones[5].name][2], abs_tol=1.0e-7)
    assert all(
        Vector(bone.head) == before[bone.name][0] and Vector(bone.tail) == before[bone.name][1]
        for bone in bones
    )
    for bone in bones[1:3] + bones[4:5]:
        target = finger_bones._target_axis_for_bone(
            finger_bones._axis(bones[0 if bone.name.startswith("f_index") else 3], "X"),
            bone,
            "X",
            bones[0 if bone.name.startswith("f_index") else 3],
        )
        assert finger_bones._axis(bone, "X").dot(target) > 0.9999
    for source in bones[:-1]:
        other = rig.data.edit_bones[bpy.utils.flip_name(source.name)]
        assert finger_bones._axis(other, 'X').dot(-finger_bones.symmetry.reflect(finger_bones._axis(source, 'X'))) > .9999


def test_active_reference_requires_active_finger():
    rig, bones = fixture()
    settings = bpy.context.window_manager.character_designer
    settings.finger_axis = "X"
    settings.finger_reference = "ACTIVE"
    bones[-1].select = True
    bpy.context.object.data.edit_bones.active = bones[-1]
    result = bpy.ops.character_designer.finger_roll(action="CHECK")
    assert result == {"CANCELLED"}
    assert "Active Bone must be a selected finger bone" in settings.finger_status


def test_both_selected_active_z_and_pair_guard():
    rig, bones = fixture()
    settings = bpy.context.window_manager.character_designer
    settings.finger_axis, settings.finger_reference = 'Z', 'ACTIVE'
    for bone in rig.data.edit_bones:
        bone.select = bone.name.startswith('f_')
    plan = finger_bones._build_plan(bpy.context)
    assert len({r['name'] for r in plan['records']}) == len(plan['records']) == 9
    assert bpy.ops.character_designer.finger_roll(action='APPLY') == {'FINISHED'}
    for bone in bones[:-1]:
        other = rig.data.edit_bones[bpy.utils.flip_name(bone.name)]
        a = Quaternion(bone.z_axis, .1) @ (bone.tail-bone.head)-(bone.tail-bone.head)
        b = Quaternion(other.z_axis, .1) @ (other.tail-other.head)-(other.tail-other.head)
        assert (b-finger_bones.symmetry.reflect(a)).length < 1e-5
    rig.data.edit_bones['f_index.03.R'].name = 'unpaired'
    before = {b.name: b.roll for b in rig.data.edit_bones}
    assert bpy.ops.character_designer.finger_roll(action='APPLY') == {'CANCELLED'}
    assert before == {b.name: b.roll for b in rig.data.edit_bones}


def test_bilateral_transaction_and_opposite_pose():
    rig, bones = fixture()
    settings = bpy.context.window_manager.character_designer
    settings.finger_axis, settings.finger_reference = 'X', 'CHAIN_ROOT'
    plan = finger_bones._build_plan(bpy.context)
    before = {b.name: b.roll for b in rig.data.edit_bones}
    rig.data.use_mirror_x = True
    calls = []
    def update():
        calls.append(1)
        if len(calls) == 1: raise RuntimeError('Pair rollback')
    context = SimpleNamespace(object=rig, view_layer=SimpleNamespace(update=update))
    try: finger_bones._apply_plan(context, plan)
    except RuntimeError as exc: assert str(exc) == 'Pair rollback'
    else: assert False
    assert rig.data.use_mirror_x and before == {b.name: b.roll for b in rig.data.edit_bones}
    bpy.ops.object.mode_set(mode='OBJECT')
    pb = rig.pose.bones['f_index.02.R']
    pb.rotation_mode = 'XYZ'
    pb.rotation_euler.x = .2
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='EDIT')
    before = {b.name: b.roll for b in rig.data.edit_bones}
    assert bpy.ops.character_designer.finger_roll(action='APPLY') == {'CANCELLED'}
    assert before == {b.name: b.roll for b in rig.data.edit_bones}


def main():
    character_designer.register()
    try:
        tests = (
            test_selection_is_finger_only_and_check_is_read_only,
            test_preview_is_non_destructive_and_apply_only_changes_roll,
            test_active_reference_requires_active_finger,
            test_both_selected_active_z_and_pair_guard,
            test_bilateral_transaction_and_opposite_pose,
        )
        for test in tests:
            test()
            print("PASS", test.__name__, flush=True)
        print("FINGER_BONES_PASSED", len(tests), flush=True)
    finally:
        character_designer.unregister()


if __name__ == "__main__":
    main()
