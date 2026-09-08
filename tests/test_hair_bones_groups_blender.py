"""Captured independent strands and legacy group-data compatibility checks."""

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


def independent_signature(plans):
    assert all("members" not in plan and "group_id" not in plan for plan in plans)
    return tuple((plan["signature"], tuple(plan["centers"])) for plan in plans)


def test_group_and_split_metadata_preserves_independent_plans():
    obj, plans, layers = fixture()
    before = independent_signature(groups.build_plans(bpy.context)[1])
    assert groups.captured_strand_count(obj) == 3
    original_ids = {record["id"] for record in groups.read_groups(obj)}
    groups.capture_plans(obj, plans)
    assert {record["id"] for record in groups.read_groups(obj)} == original_ids
    select(obj, (layers[0][-1][0], layers[1][-1][0]))
    merged = groups.group_selected(bpy.context, "Legacy Rear Hair")
    assert len(groups.read_groups(obj)) == 2
    assert len(groups.selected_members(bpy.context, obj)) == 2
    assert independent_signature(groups.build_plans(bpy.context)[1]) == before
    _, selected = groups.build_plans(bpy.context, selected_only=True)
    assert len(selected) == 2 and all("members" not in plan for plan in selected)
    groups.select_group(bpy.context, merged)
    assert len(groups.selected_members(bpy.context, obj)) == 2
    select(obj, (layers[0][-1][0],))
    assert len(groups.split_selected(bpy.context)) == 1
    assert len(groups.read_groups(obj)) == 3
    assert next(record for record in groups.read_groups(obj) if record["id"] == merged)["name"] == "Legacy Rear Hair"
    assert independent_signature(groups.build_plans(bpy.context)[1]) == before
    select(obj, ())
    saved = obj[groups.GROUPS_KEY]
    expect_error(lambda: groups.group_selected(bpy.context), "at least two")
    expect_error(lambda: groups.split_selected(bpy.context), "Select a tip")
    expect_error(lambda: groups.build_plans(bpy.context, selected_only=True), "at least one")
    assert obj[groups.GROUPS_KEY] == saved
    assert groups.captured_strand_count(obj) == 3


def test_shared_roots_are_not_membership_seeds():
    f = Fixture()
    first = f.tube(sides=5, rows=5, drift=-0.32)
    second = f.tube(sides=5, rows=5, root=first[0], drift=0.32)
    obj = f.object("Shared Root Hair")
    bm = bm_for(obj)
    edges = tuple(topology._cd()._bm_edge_key(edge) for edge in bm.edges)
    plans = tuple(topology._strict_plan(obj, bm, layers, edges) for layers in (first, second))
    assert all(plans)
    groups.capture_plans(obj, plans)
    select(obj, first[0])
    assert groups.selected_members(bpy.context, obj) == ()
    expect_error(lambda: groups.group_selected(bpy.context), "at least two")
    assert len(groups.read_groups(obj)) == 2
    select(obj, (first[-1][0],))
    assert len(groups.selected_members(bpy.context, obj)) == 1
    select(obj, (first[-1][0], second[-1][0]))
    groups.group_selected(bpy.context)
    assert len(groups.read_groups(obj)) == 1
    _, result = groups.build_plans(bpy.context)
    assert len(result) == 2 and all("members" not in plan for plan in result)
    assert set(result[0]["layers"][0]) == set(result[1]["layers"][0])
    assert groups.captured_strand_count(obj) == 2


def test_grouped_request_rejected_before_any_mutation():
    obj, plans, layers = fixture()
    select(obj, (layers[0][-1][0], layers[1][-1][0]))
    groups.group_selected(bpy.context)
    saved = obj[groups.GROUPS_KEY]
    vertices = tuple((tuple(v.co), v.select) for v in bm_for(obj).verts)
    objects, curves = set(bpy.data.objects), set(bpy.data.curves)
    # Even source resolution/evaluation must not run for an obsolete mode.
    with patch.object(groups, "source_from_context", side_effect=AssertionError("Resolved source before rejecting retired mode")):
        expect_error(lambda: groups.build_plans(bpy.context, mode="GROUPED"), "retired")
    assert obj[groups.GROUPS_KEY] == saved
    assert tuple((tuple(v.co), v.select) for v in bm_for(obj).verts) == vertices
    assert set(bpy.data.objects) == objects and set(bpy.data.curves) == curves
    assert bpy.context.active_object == obj and obj.mode == "EDIT"


def test_obsolete_guides_cannot_block_capture_or_generation():
    obj, plans, layers = fixture()
    expected = independent_signature(groups.build_plans(bpy.context)[1])
    select(obj, (layers[0][-1][0], layers[1][-1][0]))
    group_id = groups.group_selected(bpy.context)
    guide = groups.create_group_guide(bpy.context, group_id, point_count=2)
    saved = obj[groups.GROUPS_KEY]

    def usable_without_guide():
        assert groups.captured_strand_count(obj) == 3
        assert independent_signature(groups.build_plans(bpy.context)[1]) == expected
        records = groups.capture_plans(obj, plans)
        assert len(records) == 2 and sum(len(record["members"]) for record in records) == 3
        assert obj[groups.GROUPS_KEY] == saved

    duplicate = guide.copy()
    obj.users_collection[0].objects.link(duplicate)
    expect_error(lambda: groups.read_groups(obj), "duplicate")
    usable_without_guide()
    bpy.data.objects.remove(duplicate, do_unlink=True)
    spline = guide.data.splines[0]
    spline.use_cyclic_u = True
    usable_without_guide()
    spline.use_cyclic_u = False
    spline.points[-1].co = spline.points[0].co
    usable_without_guide()
    spline.points[0].co.x = float("nan")
    guide.data.splines.new("POLY").points.add(1)
    guide.constraints.new("LIMIT_LOCATION")
    usable_without_guide()
    curve = guide.data
    bpy.data.objects.remove(guide, do_unlink=True)
    bpy.data.curves.remove(curve)
    usable_without_guide()
    assert next(record for record in groups.read_groups(obj) if record["id"] == group_id)["guide"] is None


def test_coordinate_recompute_and_topology_rejection():
    obj, plans, layers = fixture()
    obj.matrix_world = Matrix.Translation((3, 4, 5)) @ Matrix.Diagonal((1.2, 0.8, 1.1, 1.0))
    _, before = groups.build_plans(bpy.context)
    saved = obj[groups.GROUPS_KEY]
    bm = bm_for(obj)
    for vertex in bm.verts:
        vertex.co.x += 0.3
    bmesh.update_edit_mesh(obj.data)
    _, changed = groups.build_plans(bpy.context)
    assert obj[groups.GROUPS_KEY] == saved
    for first, second in zip(before, changed):
        assert first["signature"] == second["signature"]
        for previous, current in zip(first["centers"], second["centers"]):
            close(Vector(previous) + Vector((0.3, 0, 0)), current)
    bm.verts.new((100, 100, 100))
    bmesh.update_edit_mesh(obj.data)
    expect_error(lambda: groups.build_plans(bpy.context), "topology changed")
    expect_error(lambda: groups.captured_strand_count(obj), "topology changed")
    expect_error(lambda: groups.capture_plans(obj, plans), "topology changed")
    assert obj[groups.GROUPS_KEY] == saved


def test_capture_rejects_invalid_data_atomically():
    obj, plans, layers = fixture()
    saved = obj[groups.GROUPS_KEY]
    broken = dict(plans[0])
    broken["vertices"] = ()
    expect_error(lambda: groups.capture_plans(obj, (broken,)), "incomplete")
    assert obj[groups.GROUPS_KEY] == saved
    assert groups.captured_strand_count(obj) == 3
    expect_error(lambda: groups.capture_plans(obj, ()), "Select Hair Strands")
    assert obj[groups.GROUPS_KEY] == saved


def test_save_reopen_and_recapture_preserve_legacy_guides():
    obj, plans, layers = fixture()
    select(obj, (layers[0][-1][0], layers[1][-1][0]))
    group_id = groups.group_selected(bpy.context, "Saved Legacy Group")
    guide = groups.create_group_guide(bpy.context, group_id)
    guide.data.splines[0].use_cyclic_u = True
    duplicate = guide.copy()
    obj.users_collection[0].objects.link(duplicate)
    saved = obj[groups.GROUPS_KEY]
    expected = independent_signature(groups.build_plans(bpy.context)[1])
    obj_name, guide_names = obj.name, (guide.name, duplicate.name)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    guide.select_set(True)
    bpy.context.view_layer.objects.active = guide
    with tempfile.TemporaryDirectory(prefix="cd_hair_capture_") as folder:
        path = Path(folder) / "legacy_capture.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(path))
        obj = bpy.data.objects[obj_name]
        source, reopened = groups.build_plans(bpy.context)
        assert source == obj and obj[groups.GROUPS_KEY] == saved
        assert independent_signature(reopened) == expected
        assert groups.captured_strand_count(obj) == 3
        for name in guide_names:
            assert bpy.data.objects[name][groups.GUIDESOURCE_KEY] == obj
        groups.clear_groups(obj)
        assert groups.captured_strand_count(obj) == 0 and groups.read_groups(obj) == []
        assert all(name in bpy.data.objects for name in guide_names)
        groups.capture_plans(obj, reopened)
        assert groups.captured_strand_count(obj) == 3
        assert all(len(record["members"]) == 1 for record in groups.read_groups(obj))
        assert all(name in bpy.data.objects for name in guide_names)
        assert independent_signature(groups.build_plans(bpy.context, source=obj)[1]) == expected


def main():
    tests = (test_group_and_split_metadata_preserves_independent_plans,
             test_shared_roots_are_not_membership_seeds,
             test_grouped_request_rejected_before_any_mutation,
             test_obsolete_guides_cannot_block_capture_or_generation,
             test_coordinate_recompute_and_topology_rejection,
             test_capture_rejects_invalid_data_atomically,
             test_save_reopen_and_recapture_preserve_legacy_guides)
    for test in tests:
        test()
        print("HAIR_GROUP_TEST=" + json.dumps({"test": test.__name__, "status": "passed"}))
    print("HAIR_GROUP_RESULT=passed")


if __name__ == "__main__":
    main()
