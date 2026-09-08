"""Initial in-place full-Mirror binding with an existing Armature modifier.

Run in disposable Blender with --background --factory-startup --disable-autoexec
--python-exit-code 1 --python tests/test_hair_bones_existing_mirror_binding_blender.py.
"""

from pathlib import Path
import sys

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
from character_designer import hair_bones_rig as rig
from test_hair_bones_mirror_controls_blender import fixture, classify
from test_hair_bones_rig_blender import activate, assert_points, evaluated_points, plain_snapshot, weights


def existing_binding(armature_first):
    source, plans, armature = fixture()
    mirror = source.modifiers[0]
    mirror.use_mirror_vertex_groups = False
    subdivision = source.modifiers.new("Artist Subdivision", "SUBSURF")
    subdivision.levels = subdivision.render_levels = 0
    modifier = source.modifiers.new("Existing Character Binding", "ARMATURE")
    modifier.object = armature
    modifier.use_deform_preserve_volume = True
    if armature_first:
        source.modifiers.move(2, 0)
    head = source.vertex_groups.new(name="spine.006")
    head.add(list(range(len(source.data.vertices))), 1.0, "REPLACE")
    mask = source.vertex_groups.new(name="Artist Mask")
    mask.add([0, 4, 6], 0.37, "REPLACE")
    mask.lock_weight = True
    # Adding hair bones cannot replace or edit the humanoid's existing action.
    armature.pose.bones["Arm"].keyframe_insert("rotation_quaternion", frame=1)
    activate(source, "EDIT", vertices=tuple(i for plan in plans for i in plan["vertices"]))
    return source, plans, armature, modifier, mirror, subdivision


def test_existing_binding_succeeds_and_sides_deform_independently(armature_first):
    source, plans, armature, modifier, mirror, subdivision = existing_binding(armature_first)
    baseline = evaluated_points(source)
    old_bones = tuple(armature.data.bones.keys())
    old_action = armature.animation_data.action
    old_mask = {i: values.get("Artist Mask") for i, values in weights(source).items()}
    result = rig.build_hair_bones(bpy.context, source, plans, armature=armature,
                                 parent_bone="spine.006", bone_count=3, mirror_controls=True)
    assert not result["modifier_created"] and not result["rig_created"]
    assert result["armature"] is armature and source.parent is armature
    assert tuple(source.modifiers) == (mirror, modifier, subdivision)
    assert modifier.use_deform_preserve_volume and mirror.use_mirror_vertex_groups
    assert all(armature.data.bones.get(name) for name in old_bones)
    assert armature.animation_data.action is old_action
    assert {i: values.get("Artist Mask") for i, values in weights(source).items()} == old_mask
    assert source.vertex_groups["Artist Mask"].lock_weight
    record = rig._read_records(source)
    assert record["version"] == 3 and record["mirror_layout"] == "BEFORE_ARMATURE_X"
    assert_points(evaluated_points(source), baseline, "Initial binding does not move existing hair")
    result["mesh"] = source
    chains, indices = classify(result, baseline)
    for side, opposite in (("L", "R"), ("R", "L")):
        pose = armature.pose.bones[chains[side]["bones"][0]]
        pose.rotation_mode = "XYZ"
        pose.rotation_euler.x = 0.35
        actual = evaluated_points(source)
        assert max((actual[i] - baseline[i]).length for i in indices[side]) > 0.2
        for still in (opposite, "C"):
            assert_points(tuple(actual[i] for i in indices[still]),
                          tuple(baseline[i] for i in indices[still]),
                          f"{side} chain leaves {still} hair unchanged")
        pose.matrix_basis = Matrix.Identity(4)
        assert_points(evaluated_points(source), baseline, "Rest pose restores hair")


def test_late_failure_restores_stack_weights_bones_and_flags(armature_first):
    source, plans, armature, modifier, mirror, subdivision = existing_binding(armature_first)
    before = plain_snapshot(source)
    old_order = tuple(source.modifiers)
    old_action = armature.animation_data.action
    old_flag = mirror.use_mirror_vertex_groups
    validate = rig._validate_owned

    def fail_after_commit(obj, target, record, snapshot):
        result = validate(obj, target, record, snapshot)
        if record is not None:
            assert tuple(obj.modifiers) == (mirror, modifier, subdivision)
            assert mirror.use_mirror_vertex_groups
            raise rig.HairBonesRigError("Injected failure after moving the existing modifier")
        return result

    rig._validate_owned = fail_after_commit
    try:
        try:
            rig.build_hair_bones(bpy.context, source, plans, armature=armature,
                                 parent_bone="spine.006", bone_count=3, mirror_controls=True)
        except rig.HairBonesRigError as exc:
            assert "Injected failure" in str(exc), str(exc)
        else:
            raise AssertionError("Expected an injected late failure")
    finally:
        rig._validate_owned = validate
    assert tuple(source.modifiers) == old_order
    assert mirror.use_mirror_vertex_groups == old_flag
    assert modifier.use_deform_preserve_volume
    assert armature.animation_data.action is old_action
    assert plain_snapshot(source) == before, "Late failure must restore all existing data and context"


def test_legacy_binding_keeps_its_mirror_order_guard():
    source, plans, armature, modifier, mirror, subdivision = existing_binding(False)
    before = plain_snapshot(source)
    try:
        rig.build_hair_bones(bpy.context, source, plans, armature=armature,
                             parent_bone="spine.006", bone_count=3)
    except rig.HairBonesRigError as exc:
        assert "precede Mirror" in str(exc), str(exc)
    else:
        raise AssertionError("Legacy non-full-Mirror API must retain its existing restriction")
    assert plain_snapshot(source) == before


if __name__ == "__main__":
    for order in (True, False):
        test_existing_binding_succeeds_and_sides_deform_independently(order)
        print(f"PASS existing_binding_succeeds armature_first={order}")
        test_late_failure_restores_stack_weights_bones_and_flags(order)
        print(f"PASS late_failure_restores_everything armature_first={order}")
    test_legacy_binding_keeps_its_mirror_order_guard()
    print("PASS legacy_binding_keeps_order_guard")
    print("PASS all 5 existing-Armature full-Mirror binding checks")
