"""Native, non-destructive hair version integration tests; no production writes."""

import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

import bpy
from mathutils import Euler, Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import hair_bones_rig as rig
from character_designer import hair_bones_variants as variants
from test_hair_bones_rig_blender import (
    activate, reset, make_armature, make_hair, weights, evaluated_points, assert_points, plain_snapshot,
)


def source_state(source):
    source.update_from_editmode()
    keys = source.data.shape_keys
    return {
        "geometry": tuple(tuple(v.co) for v in source.data.vertices),
        "groups": tuple((g.name, g.lock_weight) for g in source.vertex_groups),
        "weights": weights(source),
        "modifiers": tuple((m.name, m.type, m.object if m.type == "ARMATURE" else None,
                             m.mirror_object if m.type == "MIRROR" else None) for m in source.modifiers),
        "properties": tuple((key, repr(value)) for key, value in source.items()),
        "keys": tuple((k.name, k.value, tuple(tuple(v.co) for v in k.data)) for k in keys.key_blocks) if keys else None,
        "key_action": keys.animation_data.action if keys and keys.animation_data else None,
        "action": source.animation_data.action if source.animation_data else None,
        "parent": source.parent,
        "parent_inverse": tuple(tuple(row) for row in source.matrix_parent_inverse),
        "matrix": tuple(tuple(row) for row in source.matrix_world),
    }


def grouped(plans, n=3):
    """An invalid retired request used only to verify rejection before mutation."""
    result = []
    for start in range(0, len(plans), n):
        members = plans[start:start + n]
        centers = tuple(tuple(sum((Vector(member["centers"][i]) for member in members), Vector()) / len(members))
                        for i in range(len(members[0]["centers"])))
        result.append({"signature": f"group-{start}", "group_id": f"group-{start}", "group_name": f"Group {start}",
                       "members": members, "vertices": tuple(sorted({v for member in members for v in member["vertices"]})),
                       "centers": centers, "direction_confirmable": True, "root_tip_rule": "AVERAGE"})
    return tuple(result)


def rig_pose(arm):
    return tuple((pose.name, tuple(tuple(row) for row in pose.matrix_basis)) for pose in arm.pose.bones)


def database_counts():
    return tuple((name, len(getattr(bpy.data, name))) for name in
                 ("objects", "meshes", "armatures", "collections", "shape_keys", "actions"))


def test_two_versions_independent_and_source_unchanged():
    reset()
    original_rig = make_armature()
    # Regress the real X head's small tilt: assigning a matrix to a zero-length
    # EditBone before its length used to lose this direction and move the hair.
    activate(original_rig, "EDIT")
    original_rig.data.edit_bones["spine.006"].tail.y += 0.004
    original_rig.data.edit_bones["spine.006"].roll = 0.047
    activate(original_rig)
    source, plans = make_hair(strands=6)
    source["artist_note"] = "Never overwrite this mesh"
    source["character_designer_hair_groups"] = "owned source groups stay here"
    source.shape_key_add(name="Basis")
    detail = source.shape_key_add(name="Artist Detail")
    detail.data[-1].co.x += 0.07
    detail.value = 0.25
    detail.keyframe_insert("value", frame=1)
    source.active_shape_key_index = 0
    source.keyframe_insert("location", frame=1)
    mask = source.vertex_groups.new(name="Artist Mask")
    mask.add([0, 5], 0.73, "REPLACE")
    source.modifiers.new("Mirror", "MIRROR")
    baseline = evaluated_points(source)
    activate(source, "EDIT", vertices=plans[0]["vertices"])
    before = source_state(source)
    original_bones = tuple(original_rig.data.bones.keys())
    a = variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=3)
    assert_points(evaluated_points(a["mesh"]), baseline, "Small head tilt does not move fresh binding")
    assert source_state(source) == before
    assert tuple(original_rig.data.bones.keys()) == original_bones
    assert sum(len(chain["bones"]) for chain in a["chains"]) == 36
    assert a["mesh"].data is not source.data
    assert a["mesh"].data.shape_keys is not source.data.shape_keys
    assert a["mesh"].data.shape_keys.animation_data.action is not source.data.shape_keys.animation_data.action
    assert a["mesh"].animation_data.action is not source.animation_data.action
    assert "character_designer_hair_groups" not in a["mesh"]
    assert source.hide_get() and source.hide_render
    assert bpy.context.mode == "POSE" and bpy.context.object is a["armature"]
    assert variants.source_for(a["mesh"]) is source and variants.source_for(a["armature"]) is source
    pose = a["armature"].pose.bones[a["chains"][0]["bones"][1]]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler.x = 0.35
    pose.keyframe_insert("rotation_euler", frame=1)
    a_pose = rig_pose(a["armature"])
    a_action = a["armature"].animation_data.action
    a_weights = weights(a["mesh"])
    b = variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=4)
    assert sum(len(chain["bones"]) for chain in b["chains"]) == 48
    assert len(b["armature"].data.bones) == 49
    assert variants.variants_for(source) == (a["collection"], b["collection"])
    assert rig_pose(a["armature"]) == a_pose and a["armature"].animation_data.action is a_action
    assert weights(a["mesh"]) == a_weights
    assert source_state(source) == before
    assert a["collection"].hide_viewport and a["collection"].hide_render
    assert not b["collection"].hide_viewport and not b["collection"].hide_render
    # The previous FK edit stays local to version A; each version follows the
    # character head on both sides, without the addon running.
    variants.show_variant(bpy.context, a)
    base_a = evaluated_points(a["mesh"])
    variants.show_variant(bpy.context, b)
    base_b = evaluated_points(b["mesh"])
    head = original_rig.pose.bones["spine.006"]
    head.rotation_mode = "XYZ"
    head.rotation_euler = (0.18, -0.23, 0.31)
    bpy.context.view_layer.update()
    transform = original_rig.matrix_world @ head.matrix @ head.bone.matrix_local.inverted() @ original_rig.matrix_world.inverted()
    assert_points(evaluated_points(b["mesh"]), tuple(transform @ point for point in base_b), "B head and mirrored hair")
    variants.show_variant(bpy.context, a)
    assert_points(evaluated_points(a["mesh"]), tuple(transform @ point for point in base_a), "A head and local FK")
    head.rotation_euler = (0, 0, 0)
    variants.show_source(bpy.context, source)
    assert bpy.context.mode == "EDIT_MESH" and bpy.context.object is source
    assert not source.hide_get() and not source.hide_render
    assert all(item.hide_viewport and item.hide_render for item in variants.variants_for(source))
    assert source_state(source) == before
    variants.show_variant(bpy.context, b)
    return source, original_rig, a, b


def test_owned_source_rebinding_and_transforms():
    reset()
    original_rig = make_armature()
    original_rig.matrix_world = Matrix.LocRotScale(Vector((1, -2, 0.5)), Euler((0.1, -0.2, 0.3)).to_quaternion(), Vector((1.2, 1.2, 1.2)))
    source, plans = make_hair()
    source.matrix_world = Matrix.LocRotScale(Vector((0.7, -1.8, 0.4)), Euler((-0.1, 0.15, 0.2)).to_quaternion(), Vector((1.1, 0.8, 1.4)))
    source.modifiers.new("Mirror", "MIRROR")
    activate(source)
    native = rig.build_hair_bones(bpy.context, source, plans, bone_count=3)
    source.parent = original_rig
    source.matrix_parent_inverse = original_rig.matrix_world.inverted()
    bpy.context.view_layer.update()
    before_points = evaluated_points(source)
    old_reference = source.get(rig.MIRROR_KEY)
    before = source_state(source)
    a = variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=3)
    assert_points(evaluated_points(a["mesh"]), before_points, "Fresh copy uses original rest shape with transforms")
    assert source_state(source) == before
    assert source.get(rig.MIRROR_KEY) is old_reference
    assert old_reference is not None
    assert a["mesh"].get(rig.MIRROR_KEY) is None
    assert all(mod.mirror_object is None for mod in a["mesh"].modifiers if mod.type == "MIRROR")
    assert all(a["mesh"].vertex_groups.get(name) is None for chain in native["chains"] for name in chain["bones"])
    assert all(original_rig.data.bones.get(name) for chain in native["chains"] for name in chain["bones"])
    assert len(a["armature"].data.bones) == 13
    base = evaluated_points(a["mesh"])
    head = original_rig.pose.bones["spine.006"]
    head.rotation_mode = "XYZ"
    head.rotation_euler = (0.16, -0.22, 0.08)
    bpy.context.view_layer.update()
    transform = original_rig.matrix_world @ head.matrix @ head.bone.matrix_local.inverted() @ original_rig.matrix_world.inverted()
    assert_points(evaluated_points(a["mesh"]), tuple(transform @ point for point in base), "Transformed binding follows world head")


def test_transaction_and_unrelated_weights():
    reset()
    arm = make_armature()
    source, plans = make_hair()
    source.shape_key_add(name="Basis")
    key = source.shape_key_add(name="Detail")
    key.keyframe_insert("value", frame=1)
    source.active_shape_key_index = 0
    source.modifiers.new("Mirror", "MIRROR")
    activate(source, "EDIT", vertices=[6, 7])
    before = source_state(source)
    selected = tuple(v.index for v in source.data.vertices if v.select)
    counts = database_counts()
    original = variants.show_variant
    try:
        def fail(context, result):
            original(context, result)
            raise RuntimeError("Injected after completed rig and visibility mutation")
        variants.show_variant = fail
        try:
            variants.build_variant(bpy.context, source, plans, bone_count=3)
            assert False, "Expected injected rollback"
        except variants.HairVariantError as exc:
            assert "Injected after" in str(exc) and "Rollback needs" not in str(exc), str(exc)
    finally:
        variants.show_variant = original
    assert source_state(source) == before
    assert database_counts() == counts, (database_counts(), counts)
    assert bpy.context.mode == "EDIT_MESH" and bpy.context.object is source
    source.update_from_editmode()
    assert tuple(v.index for v in source.data.vertices if v.select) == selected
    assert not source.hide_get() and not source.hide_render
    activate(source)
    mod = source.modifiers.new("Original Binding", "ARMATURE")
    mod.object = arm
    source.modifiers.move(len(source.modifiers) - 1, 0)
    group = source.vertex_groups.new(name="Arm")
    group.add([0], 1.0, "REPLACE")
    before = source_state(source)
    counts = database_counts()
    try:
        variants.build_variant(bpy.context, source, plans, bone_count=3)
        assert False, "Unrelated deformation must not disappear"
    except variants.HairVariantError as exc:
        assert "outside the head/hair chains" in str(exc), str(exc)
    assert source_state(source) == before and database_counts() == counts


def test_standalone_and_repeated_hidden_source():
    reset()
    source, plans = make_hair()
    activate(source)
    a = variants.build_variant(bpy.context, source, plans, bone_count=2)
    b = variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=4)
    assert len(a["chains"]) == len(b["chains"]) == 2
    assert sum(len(chain["bones"]) for chain in a["chains"]) == 4
    assert sum(len(chain["bones"]) for chain in b["chains"]) == 8
    assert not b["armature"].pose.bones[b["parent_bone"]].constraints
    assert len(variants.variants_for(source)) == 2


def test_failure_restores_guide_edit_and_prior_pose():
    reset()
    make_armature()
    source, plans = make_hair()
    curve = bpy.data.curves.new("Owned Group Guide", "CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("BEZIER")
    spline.bezier_points.add(2)
    for i, point in enumerate(spline.bezier_points):
        point.co = (i * 0.1, 0.0, 2.0 - i * 0.4)
        point.select_control_point = i == 1
        point.select_left_handle = i == 0
        point.select_right_handle = i == 2
    guide = bpy.data.objects.new("Owned Group Guide", curve)
    bpy.context.scene.collection.objects.link(guide)
    guide["character_designer_hair_group_source"] = source
    activate(guide, "EDIT")
    selected = tuple((p.select_control_point, p.select_left_handle, p.select_right_handle)
                     for p in curve.splines[0].bezier_points)
    coordinates = tuple(tuple(p.co) for p in curve.splines[0].bezier_points)
    before = source_state(source)
    counts = database_counts()
    original = variants.show_variant

    def fail(context, result):
        original(context, result)
        raise RuntimeError("Injected after version activation")

    try:
        variants.show_variant = fail
        try:
            variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=4)
            assert False
        except variants.HairVariantError as exc:
            assert "Injected after" in str(exc) and "Rollback needs" not in str(exc), str(exc)
    finally:
        variants.show_variant = original
    assert bpy.context.mode == "EDIT_CURVE" and bpy.context.object is guide
    assert selected == tuple((p.select_control_point, p.select_left_handle, p.select_right_handle)
                             for p in curve.splines[0].bezier_points)
    assert coordinates == tuple(tuple(p.co) for p in curve.splines[0].bezier_points)
    assert source_state(source) == before and database_counts() == counts
    a = variants.build_variant(bpy.context, source, plans, bone_count=3)
    pose = a["armature"].pose.bones[a["chains"][0]["bones"][0]]
    pose.rotation_mode = "XYZ"
    pose.rotation_euler.x = 0.26
    pose.keyframe_insert("rotation_euler", frame=1)
    previous_pose = rig_pose(a["armature"])
    counts = database_counts()
    try:
        variants.show_variant = fail
        try:
            variants.build_variant(bpy.context, source, plans, mode="PER_STRAND", bone_count=4)
            assert False
        except variants.HairVariantError as exc:
            assert "Injected after" in str(exc) and "Rollback needs" not in str(exc), str(exc)
    finally:
        variants.show_variant = original
    assert bpy.context.mode == "POSE" and bpy.context.object is a["armature"]
    assert rig_pose(a["armature"]) == previous_pose
    assert not a["collection"].hide_viewport and not a["collection"].hide_render
    assert source.hide_get() and source.hide_render
    assert source_state(source) == before and database_counts() == counts


def test_removed_grouped_generation_is_atomic():
    reset()
    make_armature()
    source, plans = make_hair()
    source.modifiers.new("Mirror", "MIRROR")
    activate(source, "EDIT", vertices=[6, 7])
    before, counts = plain_snapshot(source), database_counts()
    for mode, proposed in (("GROUPED", plans), ("GROUPED", grouped(plans)),
                           ("PER_STRAND", grouped(plans))):
        try:
            variants.build_variant(bpy.context, source, proposed, mode=mode, bone_count=3)
            raise AssertionError("Removed shared-chain generation must stop before mutation")
        except (variants.HairVariantError, rig.HairBonesRigError) as exc:
            assert any(word in str(exc).lower() for word in ("shared", "grouped", "per strand", "per-strand")), str(exc)
        assert plain_snapshot(source) == before and database_counts() == counts
        assert bpy.context.mode == "EDIT_MESH" and bpy.context.object is source
    # Direct rig service cannot bypass the public version builder's guard.
    try:
        rig.build_hair_bones(bpy.context, source, grouped(plans), bone_count=3)
        raise AssertionError("Direct shared-chain plans must also be rejected")
    except rig.HairBonesRigError as exc:
        assert any(word in str(exc).lower() for word in ("shared", "grouped", "per strand", "per-strand")), str(exc)
    assert plain_snapshot(source) == before and database_counts() == counts


def test_hidden_source_layer_reports_visibility_without_mutation():
    reset()
    make_armature()
    source, plans = make_hair()
    collection = bpy.data.collections.new("Hair Hidden In View Layer")
    bpy.context.scene.collection.children.link(collection)
    collection.objects.link(source)
    for old_collection in tuple(source.users_collection):
        if old_collection is not collection:
            old_collection.objects.unlink(source)
    activate(source, "EDIT", vertices=[6, 7])
    layer = bpy.context.view_layer.layer_collection.children[collection.name]
    layer.hide_viewport = True
    bpy.context.view_layer.update()
    try:
        # Blender keeps EDIT_MESH/edit_object while the hidden layer removes
        # the source from objects_in_mode. This is not multi-object editing.
        assert bpy.context.mode == "EDIT_MESH" and bpy.context.edit_object is source
        assert not bpy.context.objects_in_mode and not source.visible_get()
        before, counts = plain_snapshot(source), database_counts()
        source_before = source_state(source)
        try:
            variants.build_variant(bpy.context, source, plans, bone_count=3)
            raise AssertionError("Hidden source must stop before generating a version")
        except variants.HairVariantError as exc:
            assert str(exc) == "Make the source hair visible in this View Layer, then re-enter Mesh Edit Mode."
        assert plain_snapshot(source) == before and source_state(source) == source_before
        assert database_counts() == counts and not variants.variants_for(source)
        assert layer.hide_viewport and not source.visible_get(), "Do not automatically reveal hidden hair"
        assert bpy.context.mode == "EDIT_MESH" and not bpy.context.objects_in_mode
    finally:
        layer.hide_viewport = False
        bpy.context.view_layer.update()
        activate(source)


def test_native_reopen():
    source, original_rig, a, b = test_two_versions_independent_and_source_unchanged()
    head = original_rig.pose.bones["spine.006"]
    head.rotation_mode = "XYZ"
    head.rotation_euler = (0.15, 0.2, -0.1)
    payload = []
    for result in (a, b):
        variants.show_variant(bpy.context, result)
        payload.append({"mesh": result["mesh"].name, "rig": result["armature"].name,
                        "collection": result["collection"].name,
                        "points": [list(point) for point in evaluated_points(result["mesh"])]})
    with tempfile.TemporaryDirectory(prefix="hair-variants-test-") as directory:
        directory = Path(directory)
        blend = directory / "hair-variants.blend"
        check = directory / "check.py"
        payload_file = directory / "expected.json"
        payload_file.write_text(json.dumps(payload), encoding="utf-8")
        check.write_text('''import bpy, json, sys
from mathutils import Vector
from pathlib import Path
assert "character_designer" not in sys.modules
payload = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
for record in payload:
    collection = bpy.data.collections[record["collection"]]
    collection.hide_viewport = False
    obj = bpy.data.objects[record["mesh"]]
    obj.hide_set(False)
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        points = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
        error = max((point - Vector(expected)).length for point, expected in zip(points, record["points"]))
        assert len(points) == len(record["points"]) and error < 8e-5, error
    finally:
        evaluated.to_mesh_clear()
print("HAIR_VARIANTS_NATIVE_REOPEN_OK")
''', encoding="utf-8")
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
        process = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec",
                                  str(blend), "--python-exit-code", "1", "--python", str(check), "--", str(payload_file)],
                                 capture_output=True, text=True, timeout=120)
        assert process.returncode == 0 and "HAIR_VARIANTS_NATIVE_REOPEN_OK" in process.stdout, (process.stdout, process.stderr)
    print("HAIR_VARIANTS_NATIVE_REOPEN_OK")


def main():
    for test in (test_two_versions_independent_and_source_unchanged,
                 test_owned_source_rebinding_and_transforms,
                 test_transaction_and_unrelated_weights,
                 test_standalone_and_repeated_hidden_source,
                 test_failure_restores_guide_edit_and_prior_pose,
                 test_removed_grouped_generation_is_atomic,
                 test_hidden_source_layer_reports_visibility_without_mutation,
                 test_native_reopen):
        test()
        print("PASS", test.__name__)
    print("HAIR_VARIANTS_TESTS_OK")


if __name__ == "__main__":
    main()
