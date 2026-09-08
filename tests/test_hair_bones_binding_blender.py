"""In-place hair binding/removal against disposable Blender scenes.

Runs under --background --factory-startup --disable-autoexec --python-exit-code 1.
No production scene is opened or saved. Legacy cleanup uses the frozen 0.40.2
release in a disposable subprocess, never a hidden current-generation route.
"""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import hair_bones_binding as binding
from character_designer import hair_bones_rig as rig
from character_designer import hair_bones_topology as topology
from character_designer import hair_bones_variants as variants
from test_hair_bones_rig_blender import (
    activate, assert_points, evaluated_points, make_armature, make_hair, plain_snapshot, reset, weights,
)
from test_hair_bones_variants_blender import database_counts, source_state
from test_hair_bones_mirror_controls_blender import fixture as mirror_fixture, evaluated_topology


def count(value):
    return value if isinstance(value, int) else len(value)


def bones_snapshot(armature):
    return tuple((bone.name, rig._bone_state(bone)) for bone in armature.data.bones)


def scene_fixture():
    reset()
    armature = make_armature()
    armature.data.bones["spine.006"].name = "Head"
    source, plans = make_hair()
    # Replace the existing root bridge with a triangulated cap joined to both
    # root rings. Its center belongs to the cap, never to either hair strand.
    bm = bmesh.new()
    bm.from_mesh(source.data)
    bm.verts.ensure_lookup_table()
    bridge = next(face for face in bm.faces if {vertex.index for vertex in face.verts} == {0, 1, 21, 22})
    border = list(bridge.verts)
    bm.faces.remove(bridge)
    center = sum((vertex.co for vertex in border), Vector()) / len(border)
    center.z += 0.12
    cap_vertex = bm.verts.new(center)
    for index, vertex in enumerate(border):
        bm.faces.new((vertex, border[(index + 1) % len(border)], cap_vertex))
    bm.to_mesh(source.data)
    bm.free()
    bm = bmesh.new()
    bm.from_mesh(source.data)
    for domain in (bm.verts, bm.edges, bm.faces):
        domain.ensure_lookup_table()
        domain.index_update()
    allowed, edges, adjacency = topology._visible_graph(bm)
    plans = tuple(topology._discover(source, bm, allowed, edges, adjacency, set()))
    bm.free()
    source["artist_note"] = "Preserve original mesh and artist data"
    source.vertex_groups.new(name="Artist Mask").add([0, 6, 21], 0.73, "REPLACE")
    modifier = source.modifiers.new("Artist Subdivision", "SUBSURF")
    modifier.show_viewport = False
    source.shape_key_add(name="Basis")
    key = source.shape_key_add(name="Artist Detail")
    key.data[-1].co.x += 0.03
    source.active_shape_key_index = 0
    activate(source)
    return source, plans, armature


def must_stop(call, words=()):
    try:
        call()
    except (ValueError, RuntimeError) as exc:
        if words:
            assert any(word in str(exc).lower() for word in words), str(exc)
        return str(exc)
    raise AssertionError("Unsafe operation must stop before changing any data")


def test_in_place_bind_cap_mask_and_repeat_remove():
    source, plans, armature = scene_fixture()
    original_mesh = source.data
    original_objects = set(bpy.data.objects)
    before, old_bones = source_state(source), bones_snapshot(armature)
    baseline = evaluated_points(source)
    covered = {index for plan in plans for index in plan["vertices"]}
    cap = set(range(len(source.data.vertices))) - covered
    roots = {index for plan in plans for index in plan["layers"][0]}
    assert len(cap) == 1
    for repeat in range(2):
        result = binding.bind_hair(bpy.context, source, plans, bone_count=4, armature=armature)
        assert source.data is original_mesh and set(bpy.data.objects) == original_objects
        assert result["armature"] is armature and result["parent_bone"] == "Head"
        assert count(result["cap_vertices"]) == len(cap)
        assert binding.is_bound(source)
        assert len(result["chains"]) == 2
        added = set(armature.data.bones) - {armature.data.bones[name] for name, _ in old_bones}
        assert len(added) == 8 and all(bone.use_deform for bone in added)
        for chain in result["chains"]:
            assert len(chain["bones"]) == 4
            assert armature.data.bones[chain["bones"][0]].parent.name == "Head"
        skin = weights(source)
        for index in cap | roots:
            assert skin[index].get("Head") == 1.0
            assert not any(skin[index].get(bone.name, 0.0) for bone in added)
        assert all(skin[index]["Artist Mask"] == 0.73 for index in (0, 6, 21))
        assert_points(evaluated_points(source), baseline, "Binding original hair has no rest jump")
        # Head directly drives both uncaptured cap and independent strands.
        head = armature.pose.bones["Head"]
        head.rotation_mode = "XYZ"
        head.rotation_euler = (0.12, -0.2, 0.08)
        bpy.context.view_layer.update()
        delta = armature.matrix_world @ head.matrix @ head.bone.matrix_local.inverted() @ armature.matrix_world.inverted()
        assert_points(evaluated_points(source), tuple(delta @ point for point in baseline), "Head follows without dedicated attachment rig")
        head.matrix_basis = Matrix.Identity(4)
        bpy.context.view_layer.update()
        removed = binding.remove_hair_binding(bpy.context, source)
        assert count(removed["removed_bones"]) == 8
        assert not binding.is_bound(source)
        assert source.data is original_mesh and set(bpy.data.objects) == original_objects
        assert source_state(source) == before
        assert bones_snapshot(armature) == old_bones
        assert_points(evaluated_points(source), baseline, "Unbinding restores original shape")


def test_existing_binding_and_modifier_order_restored():
    source, plans, armature = scene_fixture()
    before_mod = source.modifiers.new("Existing Head Armature", "ARMATURE")
    before_mod.object = armature
    source.modifiers.move(len(source.modifiers) - 1, 0)
    source.vertex_groups.new(name="Head").add(list(range(len(source.data.vertices))), 1.0, "REPLACE")
    source.parent = armature
    source.matrix_parent_inverse = armature.matrix_world.inverted()
    before = source_state(source)
    old_bones = bones_snapshot(armature)
    result = binding.bind_hair(bpy.context, source, plans, bone_count=3)
    assert result["armature"] is armature
    assert len([modifier for modifier in source.modifiers if modifier.type == "ARMATURE"]) == 1
    binding.remove_hair_binding(bpy.context, source)
    after = source_state(source)
    assert after == before, {key: (before[key], after[key]) for key in before if before[key] != after[key]}
    assert bones_snapshot(armature) == old_bones
    assert source.modifiers[0].as_pointer() == before_mod.as_pointer()


def test_mirror_is_complete_and_central_strand_is_one_chain():
    source, plans, armature = mirror_fixture(transformed=True)
    armature.data.bones["spine.006"].name = "Head"
    artist_subdivision = source.modifiers.new("Artist Subdivision", "SUBSURF")
    artist_subdivision.show_viewport = False
    before, old_bones = source_state(source), bones_snapshot(armature)
    original_mesh = source.data
    objects = set(bpy.data.objects)
    baseline, topology = evaluated_points(source), evaluated_topology(source)
    result = binding.bind_hair(bpy.context, source, plans, bone_count=3, armature=armature)
    assert source.data is original_mesh and set(bpy.data.objects) == objects
    assert len(result["chains"]) == 3
    chains = {}
    for chain in result["chains"]:
        first = armature.data.bones[chain["bones"][0]]
        local_x = (source.matrix_world.inverted() @ armature.matrix_world @ first.head_local).x
        side = "L" if local_x > 0.4 else "R" if local_x < -0.4 else "C"
        assert side not in chains
        chains[side] = chain
        assert first.parent.name == "Head"
    assert set(chains) == {"L", "R", "C"}
    modifiers = tuple(source.modifiers)
    assert next(i for i, item in enumerate(modifiers) if item.type == "MIRROR") < next(i for i, item in enumerate(modifiers) if item.type == "ARMATURE")
    assert_points(evaluated_points(source), baseline, "In-place mirrored bind has no rest jump")
    indices = {side: [] for side in chains}
    inverse = source.matrix_world.inverted()
    for index, point in enumerate(baseline):
        x = (inverse @ point).x
        indices["L" if x > 0.4 else "R" if x < -0.4 else "C"].append(index)
    for side in ("L", "R", "C"):
        pose = armature.pose.bones[chains[side]["bones"][0]]
        pose.rotation_mode = "XYZ"
        pose.rotation_euler.x = 0.28
        moved = evaluated_points(source)
        assert max((moved[i] - baseline[i]).length for i in indices[side]) > 0.15
        for other in chains.keys() - {side}:
            assert_points(tuple(moved[i] for i in indices[other]), tuple(baseline[i] for i in indices[other]),
                          "Separate strand keeps independent deformation")
        assert evaluated_topology(source) == topology, "Central Mirror seam stays welded"
        pose.matrix_basis = Matrix.Identity(4)
    binding.remove_hair_binding(bpy.context, source)
    after = source_state(source)
    # Blender decomposes/restores nonunit object matrices with float precision.
    assert max(abs(a - b) for row_a, row_b in zip(after["matrix"], before["matrix"])
               for a, b in zip(row_a, row_b)) < 2e-6
    after["matrix"] = before["matrix"]
    assert after == before, {key: (before[key], after[key]) for key in before if before[key] != after[key]}
    assert bones_snapshot(armature) == old_bones


def test_target_resolution_existing_nearest_and_ambiguous():
    source, plans, near = scene_fixture()
    far = make_armature("Distant Character")
    far.data.bones["spine.006"].name = "Head"
    far.location.x = 20
    bpy.context.view_layer.update()
    activate(source)
    armature, head = binding.resolve_target(bpy.context, source)
    assert armature is near and head == "Head"
    modifier = source.modifiers.new("Artist Target", "ARMATURE")
    modifier.object = far
    armature, head = binding.resolve_target(bpy.context, source)
    assert armature is far, "Existing binding wins over the nearest rig"
    source.modifiers.remove(modifier)
    far.matrix_world = near.matrix_world.copy()
    bpy.context.view_layer.update()
    before, counts = plain_snapshot(source), database_counts()
    must_stop(lambda: binding.resolve_target(bpy.context, source), ("choose", "ambig", "same", "equally", "multiple"))
    assert plain_snapshot(source) == before and database_counts() == counts
    assert binding.resolve_target(bpy.context, source, armature=near) == (near, "Head")


def test_posed_head_refuses_before_mutation():
    source, plans, armature = scene_fixture()
    pose = armature.pose.bones["Head"]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler.x = 0.3
    bpy.context.view_layer.update()
    before, counts = plain_snapshot(source), database_counts()
    must_stop(lambda: binding.bind_hair(bpy.context, source, plans, armature=armature), ("rest", "pose"))
    assert plain_snapshot(source) == before and database_counts() == counts
    assert not binding.is_bound(source)


def test_remove_refuses_external_bone_dependency():
    source, plans, armature = scene_fixture()
    result = binding.bind_hair(bpy.context, source, plans, armature=armature)
    dependent = bpy.data.objects.new("Artist External Follower", None)
    bpy.context.scene.collection.objects.link(dependent)
    constraint = dependent.constraints.new("COPY_TRANSFORMS")
    constraint.target = armature
    constraint.subtarget = result["chains"][0]["bones"][0]
    before, counts = plain_snapshot(source), database_counts()
    must_stop(lambda: binding.remove_hair_binding(bpy.context, source), ("depend", "refer", "constraint", "used"))
    assert plain_snapshot(source) == before and database_counts() == counts
    assert binding.is_bound(source)
    dependent.constraints.remove(constraint)
    binding.remove_hair_binding(bpy.context, source)
    assert dependent.name in bpy.data.objects and not binding.is_bound(source)


def test_saved_binding_removes_after_reopen():
    source, plans, armature = scene_fixture()
    original = {"weights": weights(source), "groups": [group.name for group in source.vertex_groups],
                "modifiers": [(modifier.name, modifier.type) for modifier in source.modifiers],
                "mesh": source.data.name, "bones": [bone.name for bone in armature.data.bones],
                "vertices": [list(vertex.co) for vertex in source.data.vertices]}
    binding.bind_hair(bpy.context, source, plans, armature=armature)
    with tempfile.TemporaryDirectory(prefix="hair-binding-reopen-") as temporary:
        directory = Path(temporary)
        payload = directory / "expected.json"
        payload.write_text(json.dumps({"source": source.name, "rig": armature.name, "original": original}), encoding="utf-8")
        check = directory / "check.py"
        check.write_text('''import json, sys
from pathlib import Path
import bpy
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
from character_designer import hair_bones_binding as binding
record = json.loads(Path(sys.argv[sys.argv.index("--") + 2]).read_text())
source, arm = bpy.data.objects[record["source"]], bpy.data.objects[record["rig"]]
assert binding.is_bound(source)
binding.remove_hair_binding(bpy.context, source)
original = record["original"]
assert not binding.is_bound(source) and source.data.name == original["mesh"]
assert [bone.name for bone in arm.data.bones] == original["bones"]
assert [group.name for group in source.vertex_groups] == original["groups"]
assert [(modifier.name, modifier.type) for modifier in source.modifiers] == [tuple(value) for value in original["modifiers"]]
assert [list(vertex.co) for vertex in source.data.vertices] == original["vertices"]
names = {group.index: group.name for group in source.vertex_groups}
weights = {str(vertex.index): {names[entry.group]: round(entry.weight, 7) for entry in vertex.groups} for vertex in source.data.vertices}
assert weights == original["weights"]
assert source.parent is None
print("HAIR_BINDING_REOPEN_REMOVE_OK")
''', encoding="utf-8")
        blend = directory / "bound.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
        process = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                                  str(blend), "--python-exit-code", "1", "--python", str(check),
                                  "--", str(ROOT / "addons"), str(payload)], capture_output=True, text=True, timeout=120)
        assert process.returncode == 0 and "HAIR_BINDING_REOPEN_REMOVE_OK" in process.stdout, (process.stdout, process.stderr)


def test_legacy_copy_cleanup_ownership_and_external_object_guard():
    with tempfile.TemporaryDirectory(prefix="hair-binding-cleanup-") as temporary:
        directory = Path(temporary)
        old_addons = directory / "old-addons"
        with zipfile.ZipFile(ROOT / "dist" / "character_designer-0.40.2.zip") as archive:
            assert all(Path(name).parts[0] == "character_designer" and ".." not in Path(name).parts for name in archive.namelist())
            archive.extractall(old_addons)
        process = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                                  "--python-exit-code", "1", "--python", str(ROOT / "tests" / "test_hair_bones_legacy_compat_blender.py"),
                                  "--", "legacy-create", str(old_addons), str(directory)], capture_output=True, text=True, timeout=120)
        assert process.returncode == 0, (process.stdout, process.stderr)
        expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
        bpy.ops.wm.open_mainfile(filepath=str(directory / "legacy.blend"))
        source = bpy.data.objects[expected["source"]]
        collection = bpy.data.collections[expected["version"]]
        owned_mesh, owned_rig = collection[variants.MESH_KEY], collection[variants.ARMATURE_KEY]
        # Real X has an old version rig unlinked from every collection, but its
        # ownership pointer, mesh parent, modifier and bone markers remain.
        for parent_collection in tuple(owned_rig.users_collection):
            parent_collection.objects.unlink(owned_rig)
        external = bpy.data.objects.new("Artist Object In Generated Collection", None)
        collection.objects.link(external)
        before, counts = source_state(source), database_counts()
        must_stop(lambda: binding.remove_generated_copies(bpy.context, source), ("unowned", "unrelated", "external", "artist", "unexpected", "other objects"))
        assert source_state(source) == before and database_counts() == counts
        collection.objects.unlink(external)
        bpy.context.scene.collection.objects.link(external)
        owned_names = (owned_mesh.name, owned_rig.name, collection.name)
        mesh_data, arm_data = owned_mesh.data.name, owned_rig.data.name
        result = binding.remove_generated_copies(bpy.context, source)
        assert count(result["removed_meshes"]) == count(result["removed_rigs"]) == 1
        assert bpy.data.objects.get(owned_names[0]) is None and bpy.data.objects.get(owned_names[1]) is None
        assert bpy.data.collections.get(owned_names[2]) is None
        assert bpy.data.meshes.get(mesh_data) is None and bpy.data.armatures.get(arm_data) is None
        assert bpy.data.objects.get(source.name) is source and source_state(source) == before
        assert not variants.variants_for(source)
        assert bpy.data.objects.get(expected["guide"]) is not None
        assert bpy.data.objects.get(external.name) is external


if __name__ == "__main__":
    for test in (test_in_place_bind_cap_mask_and_repeat_remove,
                 test_existing_binding_and_modifier_order_restored,
                 test_mirror_is_complete_and_central_strand_is_one_chain,
                 test_target_resolution_existing_nearest_and_ambiguous,
                 test_posed_head_refuses_before_mutation,
                 test_remove_refuses_external_bone_dependency,
                 test_saved_binding_removes_after_reopen,
                 test_legacy_copy_cleanup_ownership_and_external_object_guard):
        test()
        print("PASS", test.__name__)
    print("HAIR_BINDING_TESTS_OK")
