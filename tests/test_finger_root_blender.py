"""Regression tests for per-finger root capture and selected-chain placement."""

from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import character_designer
from character_designer import finger_bones, finger_root
from character_designer.ui_constants import UI_PAGE_RIG


def cleanup():
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for armature in list(bpy.data.armatures):
        if armature.users == 0:
            bpy.data.armatures.remove(armature)


def make_mesh():
    mesh = bpy.data.meshes.new("FingerRootFixtureMesh")
    mesh.from_pydata(
        [
            (0.0, -0.25, -0.25),
            (0.0, 0.25, -0.25),
            (0.0, 0.25, 0.25),
            (0.0, -0.25, 0.25),
            (-0.1, 0.0, -1.0),
            (-0.1, 0.0, 1.0),
        ],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new("FingerRootFixture", mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bm = __import__("bmesh").from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bm.verts.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    bm.faces[0].select_set(True)
    bm.select_flush_mode()
    return obj, bm


def make_armature():
    data = bpy.data.armatures.new("FingerRootFixtureRig")
    obj = bpy.data.objects.new("FingerRootFixtureRig", data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    first = data.edit_bones.new("thumb.01.L")
    first.head = (0.0, 0.0, 0.0)
    first.tail = (0.0, 0.0, 1.0)
    second = data.edit_bones.new("thumb.02.L")
    second.head = first.tail
    second.tail = (0.0, 0.0, 2.0)
    second.parent = first
    second.use_connect = True
    first.select = True
    second.select = True
    data.edit_bones.active = first
    return obj, first, second


def test_capture_intersection_and_place_chain():
    cleanup()
    settings = bpy.context.window_manager.character_designer
    settings.ui_page = UI_PAGE_RIG
    settings.rig_section = "BODY"
    root_settings = bpy.context.window_manager.character_designer_finger_root
    root_settings.active_finger = "THUMB"
    root_settings.root_depth = 0.1
    root_settings.bone_length_scale = 1.0
    root_settings.flip_normal = False

    mesh_obj, bm = make_mesh()
    assert finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context)
    assert finger_root.CHARACTERDESIGNER_OT_finger_root.poll(bpy.context)
    assert bpy.ops.character_designer.finger_root(action="CAPTURE_FACE") == {"FINISHED"}

    for face in bm.faces:
        face.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(vertex.index in {4, 5})
    bm.select_flush_mode()
    assert bpy.ops.character_designer.finger_root(action="CAPTURE_DIRECTION") == {"FINISHED"}
    assert bpy.ops.character_designer.finger_root(action="CHECK") == {"FINISHED"}
    assert bpy.ops.character_designer.finger_root(action="PREVIEW") == {"FINISHED"}
    assert bpy.ops.character_designer.finger_root(action="HIDE_PREVIEW") == {"FINISHED"}

    slot = finger_root._ensure_slots(root_settings)["THUMB"]
    solution = finger_root._guide_solution(root_settings, slot)
    assert (Vector(solution["surface"]) - Vector((0.0, 0.0, 0.0))).length < 1.0e-6
    assert (Vector(solution["root"]) - Vector((-0.2, 0.0, 0.0))).length < 1.0e-6
    assert Vector(solution["direction"]).dot(Vector((0.0, 0.0, 1.0))) > 0.999

    armature_obj, first, second = make_armature()
    assert bpy.ops.character_designer.finger_root(action="APPLY") == {"FINISHED"}
    assert abs(first.head.x + 0.2) < 1.0e-6
    assert abs(first.tail.z - 1.0) < 1.0e-6
    assert abs(second.head.z - first.tail.z) < 1.0e-6
    assert abs(second.tail.z - 2.0) < 1.0e-6
    assert "Placed 2 Thumb bone(s)" in root_settings.status
    assert mesh_obj.data.vertices[0].co == Vector((0.0, -0.25, -0.25))
    assert armature_obj.data.edit_bones["thumb.01.L"].roll == first.roll


def test_main_rig_chain_is_the_default_direction_source():
    cleanup()
    settings = bpy.context.window_manager.character_designer
    settings.ui_page = UI_PAGE_RIG
    settings.rig_section = "BODY"
    root_settings = bpy.context.window_manager.character_designer_finger_root
    root_settings.active_finger = "THUMB"
    root_settings.root_depth = 0.0
    root_settings.finger_side = "AUTO"

    mesh_obj, bm = make_mesh()
    assert bpy.ops.character_designer.finger_root(action="CAPTURE_FACE") == {"FINISHED"}
    armature_obj, _first, _second = make_armature()
    armature_obj.location.x = -0.1
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.scene.character_designer_setup.rig = armature_obj
    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bm = __import__("bmesh").from_edit_mesh(mesh_obj.data)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    bm.faces[0].select_set(True)
    bm.select_flush_mode()
    assert bpy.ops.character_designer.finger_root(action="CAPTURE_DIRECTION") == {"FINISHED"}
    slot = finger_root._ensure_slots(root_settings)["THUMB"]
    assert slot.direction_source == "BONE_CHAIN"
    assert slot.direction_rig == armature_obj.name
    solution = finger_root._guide_solution(root_settings, slot)
    assert (Vector(solution["root"]) - Vector((-0.1, 0.0, 0.0))).length < 1.0e-6


def main():
    character_designer.register()
    try:
        test_capture_intersection_and_place_chain()
        print("PASS test_capture_intersection_and_place_chain", flush=True)
        test_main_rig_chain_is_the_default_direction_source()
        print("PASS test_main_rig_chain_is_the_default_direction_source", flush=True)
        print("FINGER_ROOT_PASSED 2", flush=True)
    finally:
        cleanup()
        character_designer.unregister()


if __name__ == "__main__":
    main()
