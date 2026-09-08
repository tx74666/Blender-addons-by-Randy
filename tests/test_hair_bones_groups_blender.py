"""Persistent hair groups, unequal-length averaging, guides and failure checks."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import hair_bones_groups as groups
from character_designer import hair_bones_topology as topology
from test_hair_bones_topology_blender import Fixture, select, bm_for


def close(a, b, eps=1.0e-5):
    assert (Vector(a) - Vector(b)).length < eps, (a, b)


def expect_error(action, message=None):
    try:
        action()
    except groups.HairGroupsError as exc:
        if message:
            assert message in str(exc), str(exc)
        return
    raise AssertionError("Expected HairGroupsError")


def fixture():
    f = Fixture()
    a = f.tube(sides=5, rows=5, origin=(0, 0, 0), drift=0.05)
    b = f.tube(sides=5, rows=8, origin=(1.0, 0, 0), drift=-0.035)
    c = f.tube(sides=5, rows=6, origin=(2.0, 0, 0), drift=0.0)
    obj = f.object("Grouped Hair Fixture")
    _, plans = topology.discover_strands(bpy.context)
    assert len(plans) == 3
    groups.capture_plans(obj, plans)
    return obj, plans, (a, b, c)


def test_group_and_split():
    obj, plans, layers = fixture()
    records = groups.read_groups(obj)
    assert len(records) == 3
    original_ids = {record["id"] for record in records}
    groups.capture_plans(obj, plans)
    assert {record["id"] for record in groups.read_groups(obj)} == original_ids
    select(obj, (layers[0][-1][0], layers[1][-1][0]))
    merged = groups.group_selected(bpy.context, "Rear Hair")
    assert len(groups.read_groups(obj)) == 2
    assert len(groups.selected_members(bpy.context, obj)) == 2
    _, grouped = groups.build_plans(bpy.context, mode="GROUPED")
    assert len(grouped) == 2, "Building cannot be limited by the two selected members"
    merged_plan = next(plan for plan in grouped if plan["group_id"] == merged)
    assert len(merged_plan["members"]) == 2
    for end in (0, -1):
        expected = sum((Vector(member["centers"][end]) for member in merged_plan["members"]), Vector()) / 2
        close(merged_plan["centers"][end], expected)
    # Unequal lengths use equal normalized arc positions, not layer index.
    samples = [groups._sample(member["centers"], len(merged_plan["centers"])) for member in merged_plan["members"]]
    for index, point in enumerate(merged_plan["centers"]):
        close(point, (samples[0][index] + samples[1][index]) / 2)
    _, individual = groups.build_plans(bpy.context, mode="PER_STRAND")
    assert len(individual) == 3
    signature = merged_plan["signature"]
    groups.select_group(bpy.context, merged)
    assert len(groups.selected_members(bpy.context, obj)) == 2
    select(obj, (layers[0][-1][0],))
    split_ids = groups.split_selected(bpy.context)
    assert len(split_ids) == 1 and len(groups.read_groups(obj)) == 3
    assert next(record for record in groups.read_groups(obj) if record["id"] == merged)["name"] == "Rear Hair"
    select(obj, ())
    before = obj[groups.GROUPS_KEY]
    expect_error(lambda: groups.group_selected(bpy.context), "at least two")
    expect_error(lambda: groups.split_selected(bpy.context), "Select a tip")
    assert obj[groups.GROUPS_KEY] == before
    # Group order is irrelevant to stable membership signatures.
    select(obj, (layers[1][-1][0], layers[0][-1][0]))
    new_id = groups.group_selected(bpy.context, "Rear Hair 2")
    _, rebuilt = groups.build_plans(bpy.context, mode="GROUPED")
    assert next(plan for plan in rebuilt if plan["group_id"] == new_id)["signature"] == signature


def test_shared_roots_are_not_membership_seeds():
    f = Fixture()
    first = f.tube(sides=5, rows=5, drift=-0.32)
    second = f.tube(sides=5, rows=5, root=first[0], drift=0.32)
    obj = f.object("Shared Root Hair")
    _, detected = topology.discover_strands(bpy.context)
    # Construct valid complete root-attached bands; overlap is only their root.
    bm = bm_for(obj)
    edges = tuple(topology._cd()._bm_edge_key(edge) for edge in bm.edges)
    plans = tuple(topology._strict_plan(obj, bm, layers, edges) for layers in (first, second))
    assert all(plans)
    groups.capture_plans(obj, plans)
    select(obj, first[0])
    assert groups.selected_members(bpy.context, obj) == ()
    select(obj, (first[-1][0],))
    assert len(groups.selected_members(bpy.context, obj)) == 1
    select(obj, (first[-1][0], second[-1][0]))
    group_id = groups.group_selected(bpy.context)
    assert len(groups.read_groups(obj)) == 1
    _, result = groups.build_plans(bpy.context, mode="GROUPED")
    assert len(result) == 1 and result[0]["group_id"] == group_id


def test_guide_transforms_and_rollback():
    obj, plans, layers = fixture()
    obj.matrix_world = Matrix.Translation((3, 4, 5)) @ Matrix.Diagonal((1.2, 0.8, 1.1, 1.0))
    select(obj, (layers[0][-1][0], layers[1][-1][0]))
    group_id = groups.group_selected(bpy.context, "Guide Test")
    before = obj[groups.GROUPS_KEY]
    objects_before, curves_before = set(bpy.data.objects), set(bpy.data.curves)
    with patch.object(groups, "_write", side_effect=groups.HairGroupsError("Injected metadata failure")):
        expect_error(lambda: groups.create_group_guide(bpy.context, group_id), "Injected")
    assert set(bpy.data.objects) == objects_before and set(bpy.data.curves) == curves_before
    assert obj[groups.GROUPS_KEY] == before and obj.mode == "EDIT"
    guide = groups.create_group_guide(bpy.context, group_id)
    assert guide.type == "CURVE" and guide.parent == obj and obj.mode == "EDIT"
    assert groups.create_group_guide(bpy.context, group_id) == guide
    bpy.context.view_layer.update()
    _, generated = groups.build_plans(bpy.context, mode="GROUPED")
    group_plan = next(plan for plan in generated if plan["group_id"] == group_id)
    assert group_plan["root_tip_rule"] == "GROUP_GUIDE"
    close(group_plan["centers"][0], guide.data.splines[0].points[0].co[:3])
    old = Vector(group_plan["centers"][-1])
    guide.data.splines[0].points[-1].co.x += 0.31
    guide.location.y += 0.23
    bpy.context.view_layer.update()
    _, edited = groups.build_plans(bpy.context, mode="GROUPED")
    close(next(plan for plan in edited if plan["group_id"] == group_id)["centers"][-1], old + Vector((0.31, 0.23, 0)))
    # Explicit guide point order is retained even when root is lower than tip.
    spline = guide.data.splines[0]
    coordinates = [point.co.copy() for point in spline.points]
    for point, coordinate in zip(spline.points, reversed(coordinates)):
        point.co = coordinate
    _, reversed_result = groups.build_plans(bpy.context, mode="GROUPED")
    close(next(plan for plan in reversed_result if plan["group_id"] == group_id)["centers"][0], old + Vector((0.31, 0.23, 0)))
    spline.use_cyclic_u = True
    expect_error(lambda: groups.build_plans(bpy.context, mode="GROUPED"), "must be open")
    spline.use_cyclic_u = False
    # Guide selection resolves its source without editing source geometry.
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    guide.select_set(True)
    bpy.context.view_layer.objects.active = guide
    source, _ = groups.build_plans(bpy.context, mode="GROUPED")
    assert source == obj
    # Curve Edit Mode stores edits separately; consume the live transformed data.
    _, before_edit = groups.build_plans(bpy.context, mode="GROUPED")
    old_center = next(plan for plan in before_edit if plan["group_id"] == group_id)["centers"][0]
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.curve.select_all(action="SELECT")
    delta_world = Vector((0.16, 0.08, 0.0))
    bpy.ops.transform.translate(value=delta_world)
    _, while_editing = groups.build_plans(bpy.context, mode="GROUPED", source=obj)
    assert guide.mode == "EDIT", "Reading a guide must not leave Curve Edit Mode"
    new_center = next(plan for plan in while_editing if plan["group_id"] == group_id)["centers"][0]
    close(new_center, Vector(old_center) + obj.matrix_world.inverted().to_3x3() @ delta_world)
    bpy.ops.curve.spline_type_set(type="BEZIER")
    _, bezier = groups.build_plans(bpy.context, mode="GROUPED", source=obj)
    assert next(plan for plan in bezier if plan["group_id"] == group_id)["root_tip_rule"] == "GROUP_GUIDE"
    bpy.ops.object.mode_set(mode="OBJECT")
    # Object renaming cannot orphan a pointer-owned guide.
    guide.name = "Edited Artist Guide"
    obj.name = "Renamed Hair Source"
    assert next(record for record in groups.read_groups(obj) if record["id"] == group_id)["guide"] == guide
    return obj, guide, group_id


def test_coordinate_recompute_and_topology_rejection():
    obj, plans, layers = fixture()
    _, before = groups.build_plans(bpy.context)
    saved = obj[groups.GROUPS_KEY]
    bm = bm_for(obj)
    for vertex in bm.verts:
        vertex.co.x += 0.3
    bmesh.update_edit_mesh(obj.data)
    _, changed = groups.build_plans(bpy.context)
    assert obj[groups.GROUPS_KEY] == saved
    for first, second in zip(before, changed):
        close(Vector(first["centers"][0]) + Vector((0.3, 0, 0)), second["centers"][0])
    bm.verts.new((100, 100, 100))
    bmesh.update_edit_mesh(obj.data)
    expect_error(lambda: groups.build_plans(bpy.context), "topology changed")
    expect_error(lambda: groups.capture_plans(obj, plans), "topology changed")
    assert obj[groups.GROUPS_KEY] == saved


def test_invalid_and_missing_guide():
    obj, plans, layers = fixture()
    select(obj, (layers[0][-1][0], layers[1][-1][0]))
    group_id = groups.group_selected(bpy.context)
    guide = groups.create_group_guide(bpy.context, group_id, point_count=2)
    bpy.context.view_layer.update()
    duplicate = guide.copy()
    obj.users_collection[0].objects.link(duplicate)
    before = obj[groups.GROUPS_KEY]
    expect_error(lambda: groups.build_plans(bpy.context, mode="GROUPED"), "duplicate")
    assert obj[groups.GROUPS_KEY] == before
    bpy.data.objects.remove(duplicate, do_unlink=True)
    guide.data.splines[0].points[-1].co = guide.data.splines[0].points[0].co
    expect_error(lambda: groups.build_plans(bpy.context, mode="GROUPED"), "no usable length")
    curve = guide.data
    bpy.data.objects.remove(guide, do_unlink=True)
    bpy.data.curves.remove(curve)
    expect_error(lambda: groups.build_plans(bpy.context, mode="GROUPED"), "missing")
    guide = groups.create_group_guide(bpy.context, group_id, point_count=2)
    bpy.context.view_layer.update()
    assert next(record for record in groups.read_groups(obj) if record["id"] == group_id)["guide"] == guide
    _, rebuilt = groups.build_plans(bpy.context, mode="GROUPED")
    assert rebuilt
    groups.clear_groups(obj)
    assert groups.read_groups(obj) == [] and guide.name in bpy.data.objects


def test_guide_uses_visible_collection():
    obj, plans, layers = fixture()
    group_id = groups.read_groups(obj)[0]["id"]
    bpy.ops.object.mode_set(mode="OBJECT")
    first = bpy.data.collections.new("A Excluded Hair Collection")
    second = bpy.data.collections.new("Z Visible Hair Collection")
    bpy.context.scene.collection.children.link(first)
    bpy.context.scene.collection.children.link(second)
    for collection in tuple(obj.users_collection):
        collection.objects.unlink(obj)
    first.objects.link(obj)
    second.objects.link(obj)
    root_layer = bpy.context.view_layer.layer_collection
    root_layer.children[first.name].exclude = True
    bpy.context.view_layer.update()
    assert obj.users_collection[0] == first
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    guide = groups.create_group_guide(bpy.context, group_id)
    assert tuple(guide.users_collection) == (second,)
    assert groups.guide_in_editable_collection(bpy.context, guide)
    assert root_layer.children[first.name].exclude
    # A hidden parent also blocks a seemingly visible child collection.
    root_layer.children[second.name].hide_viewport = True
    assert not groups.guide_in_editable_collection(bpy.context, guide)
    root_layer.children[second.name].hide_viewport = False
    second.hide_select = True
    assert not groups.guide_in_editable_collection(bpy.context, guide)
    second.hide_select = False
    bpy.ops.object.mode_set(mode="OBJECT")
    # When all source collections are blocked, a new guide falls back to the
    # visible scene root without revealing either existing source collection.
    root_layer.children[second.name].exclude = True
    settings_before = obj[groups.GROUPS_KEY]
    context = type("Context", (), {"active_object": obj, "view_layer": bpy.context.view_layer,
                                   "scene": bpy.context.scene})()
    another_id = groups.read_groups(obj)[1]["id"]
    another = groups.create_group_guide(context, another_id)
    assert tuple(another.users_collection) == (bpy.context.scene.collection,)
    assert root_layer.children[first.name].exclude and root_layer.children[second.name].exclude
    assert obj[groups.GROUPS_KEY] != settings_before


def test_guide_immediate_transform_refresh():
    obj, plans, layers = fixture()
    obj.matrix_world = (Matrix.Translation((0.42, -0.36, 1.67))
                        @ Matrix.Rotation(0.37, 4, "Z")
                        @ Matrix.Diagonal((0.027, 0.019, 0.031, 1.0)))
    select(obj, (layers[0][-1][0], layers[1][-1][0]))
    group_id = groups.group_selected(bpy.context)
    _, before = groups.build_plans(bpy.context, mode="GROUPED")
    averaged = next(plan for plan in before if plan["group_id"] == group_id)
    guide = groups.create_group_guide(bpy.context, group_id, point_count=5)
    # No external view_layer.update: immediate UI generation must use the new
    # parent's evaluated transform instead of an initial identity matrix.
    _, immediate = groups.build_plans(bpy.context, mode="GROUPED")
    guided = next(plan for plan in immediate if plan["group_id"] == group_id)
    for end in (0, -1):
        close(guided["centers"][end], averaged["centers"][end])
        close(guide.matrix_world @ Vector(guide.data.splines[0].points[end].co[:3]),
              obj.matrix_world @ Vector(averaged["centers"][end]))
    # Pending edits of both source and guide transforms must also be consumed
    # without requiring an unrelated interaction or a caller-side refresh.
    obj.location.x += 0.9
    obj.rotation_euler.z += 0.23
    obj.scale.y *= 1.7
    guide.location = (0.31, -0.23, 0.17)
    guide.rotation_euler.z = 0.28
    _, changed = groups.build_plans(bpy.context, mode="GROUPED")
    edited = next(plan for plan in changed if plan["group_id"] == group_id)
    expected_local = Matrix.Translation((0.31, -0.23, 0.17)) @ Matrix.Rotation(0.28, 4, "Z")
    for end in (0, -1):
        close(edited["centers"][end], expected_local @ Vector(guide.data.splines[0].points[end].co[:3]))
    assert obj.mode == "EDIT"


def test_save_reopen():
    obj, guide, group_id = test_guide_transforms_and_rollback()
    obj_name, guide_name = obj.name, guide.name
    _, plans = groups.build_plans(bpy.context, mode="GROUPED")
    expected = next(plan for plan in plans if plan["group_id"] == group_id)
    path = Path(tempfile.gettempdir()) / "character_designer_hair_group_test.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    obj, guide = bpy.data.objects[obj_name], bpy.data.objects[guide_name]
    assert guide[groups.GUIDESOURCE_KEY] == obj
    assert next(record for record in groups.read_groups(obj) if record["id"] == group_id)["guide"] == guide
    source, plans = groups.build_plans(bpy.context, mode="GROUPED")
    assert source == obj
    actual = next(plan for plan in plans if plan["group_id"] == group_id)
    assert actual["signature"] == expected["signature"]
    for first, second in zip(actual["centers"], expected["centers"]):
        close(first, second)
    path.unlink(missing_ok=True)


def main():
    tests = (test_group_and_split, test_shared_roots_are_not_membership_seeds,
             test_guide_transforms_and_rollback, test_coordinate_recompute_and_topology_rejection,
             test_invalid_and_missing_guide, test_guide_uses_visible_collection,
             test_guide_immediate_transform_refresh, test_save_reopen)
    for test in tests:
        test()
        print("HAIR_GROUP_TEST=" + json.dumps({"test": test.__name__, "status": "passed"}))
    print("HAIR_GROUP_RESULT=passed")


if __name__ == "__main__":
    main()
