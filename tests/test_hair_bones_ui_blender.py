"""Hair UI operator journey: capture, compare, group, edit a guide and recapture.

Uses registered Blender operators with disposable fixtures, never production files.
Run: blender --background --factory-startup --disable-autoexec
     --python-exit-code 1 --python tests/test_hair_bones_ui_blender.py
"""

import json
from pathlib import Path
import sys

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
import character_designer as cd
from character_designer import hair_bones as ui
from character_designer import hair_bones_groups as groups
from character_designer import hair_bones_rig as rig
from character_designer import hair_bones_variants as variants
from test_hair_bones_topology_blender import Fixture, select, bm_for
from test_hair_bones_rig_blender import activate, make_armature, reset, weights
from test_hair_bones_variants_blender import source_state, rig_pose


def fixture(count=6):
    reset()
    make_armature()
    builder = Fixture()
    layers = tuple(builder.tube(sides=5, rows=5 + index % 3,
                                origin=(index * 0.8, 0, 0), drift=0.015 * (index % 2))
                   for index in range(count))
    source = builder.object("UI Hair Source")
    source["artist_note"] = "Keep original source and previous versions"
    return source, layers


def variant_data(collection):
    mesh, armature = collection[variants.MESH_KEY], collection[variants.ARMATURE_KEY]
    record = rig._read_records(mesh)
    return mesh, armature, record


def version_snapshot(collection):
    mesh, armature, _ = variant_data(collection)
    return {
        "mesh": source_state(mesh), "pose": rig_pose(armature),
        "rest": tuple((bone.name, rig._bone_state(bone)) for bone in armature.data.bones),
        "action": armature.animation_data.action if armature.animation_data else None,
    }


def call(name, **kwargs):
    result = getattr(bpy.ops.character_designer, name)(**kwargs)
    assert result == {"FINISHED"}, (name, result, ui._settings(bpy.context).last_message)


def assert_blocked_guide_keeps_editor(source, guide):
    """A collection-level editing restriction must not reveal/hide any version."""
    original_collections = tuple(guide.users_collection)
    blocked = bpy.data.collections.new("Blocked UI Guide Fixture")
    bpy.context.scene.collection.children.link(blocked)
    blocked.objects.link(guide)
    for collection in original_collections:
        collection.objects.unlink(guide)

    def state():
        return (bpy.context.mode, bpy.context.active_object,
                tuple(bpy.context.selected_objects), source.get(groups.GROUPS_KEY),
                variants._visibility_snapshot(source), variants._visibility(guide))

    try:
        for restriction in ("hide_viewport", "hide_select"):
            setattr(blocked, restriction, True)
            bpy.context.view_layer.update()
            before = state()
            try:
                result = bpy.ops.character_designer.hair_edit_guide()
            except RuntimeError as exc:
                assert "collection" in str(exc).lower(), str(exc)
            else:
                assert result == {"CANCELLED"}
            assert state() == before, (restriction, "blocked guide changed editor, groups or version visibility")
            setattr(blocked, restriction, False)
    finally:
        for collection in original_collections:
            collection.objects.link(guide)
        bpy.data.collections.remove(blocked)
        bpy.context.view_layer.update()


def test_registration_contract():
    cd.register()
    cd._validate_registration_integrity()
    cd.register()
    cd._validate_registration_integrity()
    state = bpy.context.window_manager.character_designer_hair_bones
    assert state.bone_count == 4 and state.mode == "PER_STRAND"
    for name in ("select_hair_strands", "hair_group_selected", "hair_split_selected",
                 "hair_select_group", "hair_edit_guide", "hair_build_version",
                 "hair_show_source", "hair_show_version", "hair_clear_groups",
                 "generate_hair_bones"):
        assert getattr(bpy.ops.character_designer, name).get_rna_type()
    print("PASS test_registration_contract")


def test_artist_journey_and_preserved_versions():
    source, layers = fixture()
    settings = ui._settings(bpy.context)
    settings.bone_count = 3
    settings.mode = "PER_STRAND"
    geometry = tuple(tuple(point.co) for point in bm_for(source).verts)
    call("select_hair_strands")
    assert settings.source is source
    assert len(groups.read_groups(source)) == 6
    captured_source = source_state(source)
    call("hair_build_version")
    assert source_state(source) == captured_source
    assert bpy.context.mode == "POSE"
    first = variants.variants_for(source)[0]
    first_mesh, first_arm, first_record = variant_data(first)
    assert len(first_record["chains"]) == 6
    assert sum(len(chain["bones"]) for chain in first_record["chains"]) == 18
    assert settings.active_variant == first.name

    # Simulate artist work that must never be silently replaced by another build.
    bone_names = first_record["chains"][0]["bones"]
    moving = first_arm.pose.bones[bone_names[1]]
    moving.rotation_mode = "XYZ"
    moving.rotation_euler.x = 0.35
    moving.keyframe_insert("rotation_euler", frame=1)
    tip = first_record["chains"][0]["layers"][-1][0]
    first_mesh.vertex_groups[bone_names[-1]].add([tip], 0.75, "REPLACE")
    first_mesh.vertex_groups[bone_names[0]].add([tip], 0.25, "REPLACE")
    before_first = version_snapshot(first)

    activate(first_mesh, "EDIT")
    assert ui._source(bpy.context) is source
    for name in ("select_hair_strands", "hair_group_selected", "hair_split_selected"):
        assert not getattr(bpy.ops.character_designer, name).poll(), (name, "must edit original source")

    call("hair_show_source")
    assert bpy.context.active_object is source and bpy.context.mode == "EDIT_MESH"
    assert first.hide_viewport and first.hide_render and not source.hide_get()
    select(source, [layers[index][-1][0] for index in range(3)])
    call("hair_group_selected")
    assert settings.mode == "GROUPED"
    selected_group = settings.active_group
    assert len(next(record for record in groups.read_groups(source) if record["id"] == selected_group)["members"]) == 3
    select(source, [layers[index][-1][0] for index in range(3, 6)])
    call("hair_group_selected")
    assert sorted(len(record["members"]) for record in groups.read_groups(source)) == [3, 3]
    second_group = settings.active_group
    call("hair_select_group")
    assert len(groups.selected_members(bpy.context, source)) == 3
    _, before_guide_plans = groups.build_plans(bpy.context, mode="GROUPED")
    averaged = next(plan for plan in before_guide_plans if plan["group_id"] == second_group)

    call("hair_edit_guide")
    guide = bpy.context.active_object
    assert guide.type == "CURVE" and bpy.context.mode == "EDIT_CURVE"
    assert ui._source(bpy.context) is source
    assert len(guide.data.splines[0].points) == 4
    call("hair_show_source")
    guide.hide_viewport = True
    guide.hide_select = True
    guide.hide_set(True)
    call("hair_edit_guide")
    assert bpy.context.active_object is guide and bpy.context.mode == "EDIT_CURVE"
    assert not guide.hide_viewport and not guide.hide_select and not guide.hide_get()
    # Execute actual Edit Curve transforms, then build directly without leaving
    # the guide manually. This catches stale Edit Mode coordinate reads.
    assert bpy.ops.curve.select_all(action="SELECT") == {"FINISHED"}
    offset = Vector((0.23, -0.08, 0.04))
    assert bpy.ops.transform.translate(value=offset) == {"FINISHED"}
    _, guide_plans = groups.build_plans(bpy.context, mode="GROUPED", source=source)
    edited = next(plan for plan in guide_plans if plan["group_id"] == second_group)
    assert edited["root_tip_rule"] == "GROUP_GUIDE"
    for end in (0, -1):
        assert (Vector(edited["centers"][end]) - Vector(averaged["centers"][end]) - offset).length < 1e-5
    call("hair_build_version")
    assert bpy.context.mode == "POSE"
    assert len(variants.variants_for(source)) == 2
    second = variants.variants_for(source)[1]
    second_mesh, second_arm, second_record = variant_data(second)
    assert len(second_record["chains"]) == 2
    assert sum(len(chain["bones"]) for chain in second_record["chains"]) == 6
    assert sorted(len(chain["members"]) for chain in second_record["chains"]) == [3, 3]
    written = next(chain for chain in second_record["chains"] if chain["group_id"] == second_group)
    assert written["centers"] == [list(point) for point in edited["centers"]]
    assert version_snapshot(first) == before_first
    before_second = version_snapshot(second)
    assert_blocked_guide_keeps_editor(source, guide)
    assert version_snapshot(first) == before_first and version_snapshot(second) == before_second

    # Switching versions affects visibility and control selection only.
    settings.active_variant = first.name
    call("hair_show_version")
    assert bpy.context.active_object is first_arm and bpy.context.mode == "POSE"
    assert not first.hide_viewport and second.hide_viewport and source.hide_get()
    settings.active_variant = second.name
    call("hair_show_version")
    assert bpy.context.active_object is second_arm and first.hide_viewport and not second.hide_viewport
    call("hair_show_source")
    select(source, [layers[0][-1][0]])
    call("hair_split_selected")
    assert sorted(len(record["members"]) for record in groups.read_groups(source)) == [1, 2, 3]
    assert version_snapshot(first) == before_first and version_snapshot(second) == before_second

    # Source coordinate edits and recapture are independent of both results.
    bm = bm_for(source)
    for index in layers[0][2]:
        bm.verts[index].co.x += 0.04
    bmesh.update_edit_mesh(source.data, loop_triangles=False, destructive=False)
    call("hair_clear_groups")
    assert not groups.read_groups(source)
    assert not any(vertex.select for vertex in bm_for(source).verts)
    assert guide.name in bpy.data.objects, "Recapture must keep the artist's guide"
    call("select_hair_strands")
    assert len(groups.read_groups(source)) == 6
    assert tuple(tuple(point.co) for point in bm_for(source).verts) != geometry
    assert version_snapshot(first) == before_first and version_snapshot(second) == before_second
    assert variants.variants_for(source) == (first, second)
    print("PASS test_artist_journey_and_preserved_versions")


def test_legacy_generate_operator_remains_available():
    source, _ = fixture(count=1)
    ui._settings(bpy.context).bone_count = 3
    call("select_hair_strands")
    call("generate_hair_bones")
    record = rig._read_records(source)
    assert len(record["chains"]) == 1 and len(record["chains"][0]["bones"]) == 3
    assert not variants.variants_for(source)
    assert bpy.context.mode == "POSE" and source.get(rig.RIG_KEY) is bpy.context.active_object
    print("PASS test_legacy_generate_operator_remains_available")


if __name__ == "__main__":
    try:
        test_registration_contract()
        test_artist_journey_and_preserved_versions()
        test_legacy_generate_operator_remains_available()
        cd._validate_registration_integrity()
        cd.unregister()
        assert not hasattr(bpy.types.WindowManager, "character_designer_hair_bones")
        cd.register()
        cd._validate_registration_integrity()
        print("HAIR_BONES_UI_TESTS_OK")
    finally:
        cd.unregister()
