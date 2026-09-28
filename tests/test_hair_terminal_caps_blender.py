"""Short terminal hair disks follow their own last bone, never the head cap.

Run in a factory background Blender with --disable-autoexec and
--python-exit-code 1. All scenes are disposable; the reopen check writes only
inside a temporary directory and reopens in the same process.
"""

import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import hair_bones_binding as binding
from character_designer import hair_bones_rig as rig
from character_designer import hair_bones_topology as topology
from test_hair_bones_binding_blender import bones_snapshot, must_stop, scene_fixture
from test_hair_bones_binding_guards_blender import assert_near, full_state
from test_hair_bones_rig_blender import activate, assert_points, evaluated_points, weights
from test_hair_bones_topology_blender import bm_for, select
from test_hair_bones_variants_blender import source_state


def fixture():
    source, previous_plans, armature = scene_fixture()
    source.shape_key_clear()
    bm = bmesh.new()
    bm.from_mesh(source.data)
    bm.verts.ensure_lookup_table()
    patches = {}
    for strand_index, plan in enumerate(previous_plans):
        bm.verts.ensure_lookup_table()
        # Use straight terminal tangents so the planar test disk is exactly
        # perpendicular to the strand; its connectivity is deliberately mixed.
        root_center = Vector(plan["centers"][0])
        for layer in plan["layers"]:
            center = sum((bm.verts[index].co for index in layer), Vector()) / len(layer)
            for index in layer:
                bm.verts[index].co.x += root_center.x - center.x
                bm.verts[index].co.y += root_center.y - center.y
        ring = tuple(bm.verts[index] for index in plan["layers"][-1])
        center = sum((vertex.co for vertex in ring), Vector()) / len(ring)
        hub = bm.verts.new(center)
        added = [hub]
        triangles = [(ring[index], ring[(index + 1) % len(ring)], hub)
                     for index in range(len(ring))]
        if strand_index:
            # Seven interior vertices in one triangulated disk. These vertices
            # cannot be represented as regular equal-sized strand sections.
            refined = []
            for first, second, third in triangles:
                inside = bm.verts.new((first.co + second.co + third.co) / 3)
                nested = bm.verts.new((first.co + second.co + inside.co) / 3)
                added.extend((inside, nested))
                refined.extend(((first, second, nested), (second, inside, nested),
                                (inside, first, nested), (second, third, inside),
                                (third, first, inside)))
            triangles = refined
        for triangle in triangles:
            bm.faces.new(triangle)
        bm.verts.index_update()
        patches[plan["signature"]] = tuple(vertex.index for vertex in added)
    bm.to_mesh(source.data)
    bm.free()
    source.data.update()
    source.shape_key_add(name="Basis")
    detail = source.shape_key_add(name="Artist Tip Detail")
    detail.data[-1].co.x += 0.002
    source.active_shape_key_index = 0
    indices = tuple(index for patch_indices in patches.values() for index in patch_indices)
    source.vertex_groups.get("Artist Mask").add(indices, 0.63, "REPLACE")
    source.vertex_groups.new(name="Head").add(indices, 0.2, "REPLACE")
    source.vertex_groups.new(name="Arm").add(indices, 0.8, "REPLACE")
    bm = bmesh.new()
    bm.from_mesh(source.data)
    try:
        for domain in (bm.verts, bm.edges, bm.faces):
            domain.ensure_lookup_table()
            domain.index_update()
        edges = tuple(topology._cd()._bm_edge_key(edge) for edge in bm.edges)
        plans = tuple(topology._strict_plan(source, bm, plan["layers"], edges, explicit_direction=True)
                      for plan in previous_plans)
        assert all(plans)
        assert {plan["signature"] for plan in plans} == set(patches)
    finally:
        bm.free()
    return source, plans, armature, patches


def terminal_patches(source, plans):
    bm = bmesh.new()
    bm.from_mesh(source.data)
    try:
        for domain in (bm.verts, bm.edges, bm.faces):
            domain.ensure_lookup_table()
            domain.index_update()
        return topology.terminal_patches(source, bm, plans)
    finally:
        bm.free()


def test_tip_disks_select_with_their_strands_without_changing_core_plans():
    source, plans, _, expected = fixture()
    assert terminal_patches(source, plans) == expected
    cap, patches = binding._binding_regions(source, plans)
    assert patches == expected and len(cap) == 1
    assert binding._cap_vertices(source, plans) == cap
    before = source_state(source)
    activate(source, "EDIT")
    select(source, (next(iter(expected.values()))[0],))
    _, local = topology.discover_strands(bpy.context)
    assert len(local) == 1 and local[0]["signature"] == plans[0]["signature"]
    _, selected = topology.select_strands(bpy.context, respect_selection=False)
    assert {plan["signature"] for plan in selected} == set(expected)
    core = {index for plan in plans for index in plan["vertices"]}
    tips = {index for patch_indices in patches.values() for index in patch_indices}
    assert {vertex.index for vertex in bm_for(source).verts if vertex.select} == core | tips
    assert all(set(plan["vertices"]).isdisjoint(tips) for plan in selected)
    assert source_state(source) == before


def test_terminal_weights_deform_with_last_bone_and_remove_exactly():
    source, plans, armature, patches = fixture()
    before, bones = source_state(source), bones_snapshot(armature)
    baseline = evaluated_points(source)
    result = binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
    assert bpy.context.active_object is armature and bpy.context.mode == "POSE"
    assert result["tip_patches"] == patches
    assert binding._read(source)["tip_patches"] == {signature: list(indices) for signature, indices in patches.items()}
    assert_points(evaluated_points(source), baseline, "Terminal binding has no rest displacement")
    skin = weights(source)
    cap = set(result["cap_vertices"])
    roots = {index for plan in plans for index in plan["layers"][0]}
    deform = {bone.name for bone in armature.data.bones if bone.use_deform}
    for chain in result["chains"]:
        for index in patches[chain["signature"]]:
            assert {name: value for name, value in skin[index].items() if name in deform} == {chain["bones"][-1]: 1.0}
            assert skin[index]["Artist Mask"] == 0.63
    for index in cap | roots:
        assert {name: value for name, value in skin[index].items() if name in deform} == {"Head": 1.0}

    chain = result["chains"][0]
    final = armature.pose.bones[chain["bones"][-1]]
    final.rotation_mode = "XYZ"
    final.rotation_euler = (0.35, 0.0, 0.0)
    bpy.context.view_layer.update()
    bent = evaluated_points(source)
    delta = armature.matrix_world @ final.matrix @ final.bone.matrix_local.inverted() @ armature.matrix_world.inverted()
    for index in patches[chain["signature"]]:
        assert_points((bent[index],), (delta @ baseline[index],), "Terminal patch follows final bone")
    assert any((bent[index] - baseline[index]).length > 1e-5 for index in patches[chain["signature"]])
    assert_points(tuple(bent[index] for index in cap | roots), tuple(baseline[index] for index in cap | roots),
                  "Terminal rotation cannot move the head cap or roots")
    final.matrix_basis = Matrix.Identity(4)
    head = armature.pose.bones["Head"]
    head.rotation_mode = "XYZ"
    head.rotation_euler = (0.11, -0.15, 0.07)
    bpy.context.view_layer.update()
    delta = armature.matrix_world @ head.matrix @ head.bone.matrix_local.inverted() @ armature.matrix_world.inverted()
    assert_points(evaluated_points(source), tuple(delta @ point for point in baseline), "Head carries cap and terminal disks")
    head.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    binding.remove_hair_binding(bpy.context, source)
    assert_near(source_state(source), before)
    assert bones_snapshot(armature) == bones


def test_locked_tip_deform_weights_refuse_before_bone_creation():
    source, plans, armature, _ = fixture()
    source.vertex_groups["Arm"].lock_weight = True
    before = full_state(source, armature)
    with patch.object(rig, "build_hair_bones", side_effect=AssertionError("Locked tip weights reached bone creation")) as build:
        must_stop(lambda: binding.bind_hair(bpy.context, source, plans, armature=armature), ("unlock",))
        build.assert_not_called()
    assert_near(full_state(source, armature), before)


def test_failure_after_terminal_write_restores_complete_binding_state():
    source, plans, armature, _ = fixture()
    before = full_state(source, armature)
    original = binding._write_terminal_weights

    def write_then_fail(*args):
        original(*args)
        assert weights(source) != before["source"]["weights"]
        raise RuntimeError("Injected failure after terminal weight write")

    with patch.object(binding, "_write_terminal_weights", side_effect=write_then_fail) as write:
        must_stop(lambda: binding.bind_hair(bpy.context, source, plans, armature=armature), ("injected",))
        write.assert_called_once()
    assert_near(full_state(source, armature), before)
    assert not binding.is_bound(source)


def test_saved_terminal_binding_removes_after_same_process_reopen():
    source, plans, armature, patches = fixture()
    before, bones = source_state(source), bones_snapshot(armature)
    source_name, rig_name = source.name, armature.name
    binding.bind_hair(bpy.context, source, plans, armature=armature)
    with tempfile.TemporaryDirectory(prefix="hair-terminal-reopen-") as temporary:
        path = Path(temporary) / "terminal-binding.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
        source, armature = bpy.data.objects[source_name], bpy.data.objects[rig_name]
        assert binding.is_bound(source)
        assert binding._read(source)["tip_patches"] == {signature: list(indices) for signature, indices in patches.items()}
        binding.remove_hair_binding(bpy.context, source)
        assert_near(source_state(source), before)
        assert bones_snapshot(armature) == bones


def test_invalid_terminal_attachments_are_not_head_caps():
    for invalid in ("long", "multiowner", "wire"):
        source, plans, armature, patches = fixture()
        source.shape_key_clear()
        bm = bmesh.new()
        bm.from_mesh(source.data)
        bm.verts.ensure_lookup_table()
        first = bm.verts[patches[plans[0]["signature"]][0]]
        if invalid == "long":
            tangent = Vector(plans[0]["centers"][-1]) - Vector(plans[0]["centers"][-2])
            first.co += tangent * 8
        elif invalid == "multiowner":
            second = bm.verts[patches[plans[1]["signature"]][0]]
            bm.edges.new((first, second))
        else:
            extra = bm.verts.new(first.co + Vector((0.01, 0, 0)))
            bm.edges.new((first, extra))
        bm.to_mesh(source.data)
        bm.free()
        source.data.update()
        assert plans[0]["signature"] not in terminal_patches(source, plans), invalid
        before = full_state(source, armature)
        must_stop(lambda: binding.bind_hair(bpy.context, source, plans, armature=armature), ("cap", "capture"))
        assert_near(full_state(source, armature), before)


def main():
    assert bpy.app.background
    completed = []
    for test in (
        test_tip_disks_select_with_their_strands_without_changing_core_plans,
        test_terminal_weights_deform_with_last_bone_and_remove_exactly,
        test_locked_tip_deform_weights_refuse_before_bone_creation,
        test_failure_after_terminal_write_restores_complete_binding_state,
        test_saved_terminal_binding_removes_after_same_process_reopen,
        test_invalid_terminal_attachments_are_not_head_caps,
    ):
        test()
        completed.append(test.__name__)
        print("HAIR_TERMINAL_TEST=" + json.dumps({"test": test.__name__, "status": "passed"}))
    print("HAIR_TERMINAL_RESULT=" + json.dumps({"status": "passed", "tests": completed}))


if __name__ == "__main__":
    main()
