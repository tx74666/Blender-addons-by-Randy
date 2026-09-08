"""Blender 5.2 integration tests for Character Designer Limb IK."""

import json
import math
import os
import sys

import bpy
from bpy.props import PointerProperty
from mathutils import Matrix, Quaternion, Vector


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS = os.path.join(ROOT, "addons")
if ADDONS not in sys.path:
    sys.path.insert(0, ADDONS)

from character_designer import limb_ik


POLE_DIRECTION_PROPERTIES = {
    ("ARM", "L"): "left_arm_pole_direction",
    ("ARM", "R"): "right_arm_pole_direction",
    ("LEG", "L"): "left_leg_pole_direction",
    ("LEG", "R"): "right_leg_pole_direction",
}
DEFAULT_POLE_DIRECTIONS = {
    "ARM": Vector((0.0, 1.0, 0.0)),
    "LEG": Vector((0.0, -1.0, 0.0)),
}


def pole_direction_property(kind, side):
    return POLE_DIRECTION_PROPERTIES[(kind, side)]


def set_pole_direction(settings, kind, side, value):
    setattr(settings, pole_direction_property(kind, side), value)


def ensure_registered():
    if hasattr(bpy.types.WindowManager, "character_designer_limb_ik"):
        return
    registered = []
    try:
        for cls in limb_ik.LIMB_IK_CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.WindowManager.character_designer_limb_ik = PointerProperty(
            type=limb_ik.CharacterDesignerLimbIKState,
            options={"SKIP_SAVE"},
        )
    except Exception:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise


def ensure_unregistered():
    if hasattr(bpy.types.WindowManager, "character_designer_limb_ik"):
        del bpy.types.WindowManager.character_designer_limb_ik
    for cls in reversed(limb_ik.LIMB_IK_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def reset_scene():
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in tuple(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for data in tuple(bpy.data.armatures):
        if data.users == 0:
            bpy.data.armatures.remove(data)
    for mesh in tuple(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    settings = bpy.context.window_manager.character_designer_limb_ik
    settings.armature = None
    settings.analysis_json = ""
    settings.direct_preroll_json = ""
    settings.build_method = "ROLL_DECOUPLED"
    settings.last_level = "NONE"
    settings.last_message = ""
    for kind in limb_ik.KINDS:
        for side in limb_ik.SIDES:
            for role in limb_ik.ROLES:
                setattr(settings, limb_ik._field_name(kind, side, role), "")
            set_pole_direction(settings, kind, side, DEFAULT_POLE_DIRECTIONS[kind])


def add_bone(edit_bones, name, head, tail, parent=None, *, deform=True):
    bone = edit_bones.new(name)
    bone.head = head
    bone.tail = tail
    bone.parent = parent
    bone.use_connect = parent is not None and Vector(parent.tail) == Vector(head)
    bone.use_deform = deform
    return bone


def make_humanoid(name="Humanoid", *, near_straight=False, include_right=True, roll_offset=0.0):
    data = bpy.data.armatures.new(name + "Data")
    armature = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bones = data.edit_bones
    hips = add_bone(bones, "Hips", (0, 0, 0.9), (0, 0, 1.15))
    chest = add_bone(bones, "Chest", (0, 0, 1.15), (0, 0, 1.55), hips)
    sides = ("L", "R") if include_right else ("L",)
    for side in sides:
        sign = 1.0 if side == "L" else -1.0
        shoulder = add_bone(bones, f"shoulder.{side}", (0, 0, 1.5), (0.2 * sign, 0, 1.5), chest)
        # Match X's Armature-local convention: the Foot points toward -Y, so
        # elbows bend behind at +Y and knees bend forward at -Y.
        elbow_y = 0.004 if near_straight else 0.12
        upper = add_bone(
            bones,
            f"upper_arm.{side}",
            (0.2 * sign, 0, 1.5),
            (0.6 * sign, elbow_y, 1.46),
            shoulder,
        )
        upper.roll = roll_offset * sign
        lower = add_bone(
            bones,
            f"forearm.{side}",
            (0.6 * sign, elbow_y, 1.46),
            (1.0 * sign, 0, 1.42),
            upper,
        )
        lower.roll = -roll_offset * 0.37 * sign
        hand = add_bone(
            bones,
            f"hand.{side}",
            (1.0 * sign, 0, 1.42),
            (1.18 * sign, -0.02, 1.41),
            lower,
        )
        hand.roll = roll_offset * 0.21 * sign
        thigh = add_bone(
            bones,
            f"thigh.{side}",
            (0.15 * sign, 0, 1.15),
            (0.15 * sign, -0.1, 0.58),
            hips,
        )
        thigh.roll = roll_offset * 0.53 * sign
        shin = add_bone(
            bones,
            f"shin.{side}",
            (0.15 * sign, -0.1, 0.58),
            (0.15 * sign, 0, 0.04),
            thigh,
        )
        shin.roll = -roll_offset * 0.28 * sign
        foot = add_bone(
            bones,
            f"foot.{side}",
            (0.15 * sign, 0, 0.04),
            (0.15 * sign, -0.3, 0.02),
            shin,
        )
        foot.roll = roll_offset * 0.16 * sign
    bpy.ops.object.mode_set(mode="POSE")
    return armature


def analyze(armature):
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    if armature.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")
    result = bpy.ops.character_designer.limb_ik_analyze()
    return result, bpy.context.window_manager.character_designer_limb_ik


def owned_bones(armature):
    return [bone for bone in armature.data.bones if bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE]


def cancelled_result(operation):
    try:
        return operation()
    except RuntimeError:
        return {"CANCELLED"}


def assert_matrix_close(actual, expected, label, *, location=5.0e-4, rotation=4.0e-3, scale=5.0e-4):
    location_error = (actual.translation - expected.translation).length
    rotation_error = actual.to_quaternion().rotation_difference(expected.to_quaternion()).angle
    actual_scale = actual.to_scale()
    expected_scale = expected.to_scale()
    scale_error = (actual_scale - expected_scale).length
    if location_error > location or rotation_error > rotation or scale_error > scale:
        raise AssertionError(f"{label}: location={location_error}, rotation={rotation_error}, scale={scale_error}")


def _pole_plane_alignment(armature, key):
    rig = limb_ik._validate_inventory(armature)["rigs"][key]
    upper = armature.pose.bones[rig["chain"][0]]
    lower = armature.pose.bones[rig["chain"][1]]
    pole = armature.pose.bones[rig["pole"].name]
    start = Vector(upper.head)
    joint = Vector(lower.head)
    end = Vector(lower.tail)
    axis = end - start
    if axis.length <= 1.0e-9:
        raise AssertionError(f"{key} has no usable chain axis")
    axis.normalize()
    residual = (joint - start) - axis * (joint - start).dot(axis)
    pole_projection = (Vector(pole.head) - start) - axis * (Vector(pole.head) - start).dot(axis)
    if residual.length <= 1.0e-8 or pole_projection.length <= 1.0e-8:
        raise AssertionError(f"{key} has a degenerate evaluated bend/Pole projection")
    return residual.normalized().dot(pole_projection.normalized())


def _axis_residual(start, point, end):
    start = Vector(start)
    point = Vector(point)
    axis = Vector(end) - start
    if axis.length <= 1.0e-9:
        raise AssertionError("Cannot measure a bend against a degenerate source-end chord")
    axis.normalize()
    return (point - start) - axis * (point - start).dot(axis)


def rest_bend_direction(armature, names):
    upper = armature.data.bones[names[0]]
    lower = armature.data.bones[names[1]]
    residual = _axis_residual(upper.head_local, lower.head_local, lower.tail_local)
    if residual.length <= 1.0e-9:
        raise AssertionError(f"{names[:2]} has no measurable modeled rest bend")
    return residual.normalized()


def assert_pole_plane_alignment(armature, key, label, tolerance=0.999):
    alignment = _pole_plane_alignment(armature, key)
    if alignment <= tolerance:
        raise AssertionError(f"{label} actual bend did not follow the Pole projection: {alignment}")


def projected_pole_direction(armature, key, expected):
    kind, side = key
    chain = tuple(
        getattr(
            bpy.context.window_manager.character_designer_limb_ik,
            limb_ik._field_name(kind, side, role),
        )
        for role in limb_ik.ROLES
    )
    start = Vector(armature.data.bones[chain[0]].head_local)
    end = Vector(armature.data.bones[chain[1]].tail_local)
    projected = limb_ik._project_perpendicular(Vector(expected), end - start)
    if projected.length <= 1.0e-9:
        raise AssertionError(f"{key} expected Pole direction is chain-parallel")
    return projected.normalized()


def assert_rest_pole_direction(armature, key, expected, label, tolerance=0.999999):
    rig = limb_ik._validate_inventory(armature)["rigs"][key]
    start = Vector(armature.data.bones[rig["chain"][0]].head_local)
    source_joint = Vector(armature.data.bones[rig["chain"][1]].head_local)
    end = Vector(armature.data.bones[rig["chain"][1]].tail_local)
    expected = limb_ik._project_perpendicular(Vector(expected), end - start)
    expected.normalize()
    joint = limb_ik._joint_on_direction_plane(start, source_joint, end, expected)
    actual = Vector(rig["pole"].head_local) - joint
    if min(actual.length, expected.length) <= 1.0e-9:
        raise AssertionError(f"{label} has a degenerate Pole direction")
    alignment = actual.normalized().dot(expected.normalized())
    if alignment <= tolerance:
        raise AssertionError(f"{label} did not use the configured Armature-local direction: {alignment}")


def assert_short_rest_line_aims_at_pole(armature, key, label, tolerance=0.999999):
    rig = limb_ik._validate_inventory(armature)["rigs"][key]
    joint = Vector(armature.data.bones[rig["chain"][1]].head_local)
    pole = Vector(rig["pole"].head_local)
    line = rig["line"]
    line_head = Vector(line.head_local)
    line_axis = Vector(line.tail_local) - line_head
    full_guide = pole - joint
    if (line_head - joint).length > 2.0e-6:
        raise AssertionError(f"{label} short line does not start at the Rest joint")
    if min(line_axis.length, full_guide.length) <= limb_ik.EPSILON:
        raise AssertionError(f"{label} has a degenerate short line or joint-to-Pole guide")
    if line_axis.length >= full_guide.length * 0.2:
        raise AssertionError(f"{label} Edit helper is not short: {line_axis.length} / {full_guide.length}")
    alignment = line_axis.normalized().dot(full_guide.normalized())
    if alignment <= tolerance:
        raise AssertionError(f"{label} short line points away from its Pole: {alignment}")


def test_rotation_error_uses_shortest_quaternion_angle():
    class QuaternionMatrixProxy:
        def __init__(self, quaternion):
            self.quaternion = quaternion

        def to_quaternion(self):
            return self.quaternion.copy()

    identity = Quaternion((1.0, 0.0, 0.0, 0.0))
    opposite_sign = Quaternion((-1.0, 0.0, 0.0, 0.0))
    error = limb_ik._rotation_error(
        QuaternionMatrixProxy(identity),
        QuaternionMatrixProxy(opposite_sign),
    )
    if not math.isfinite(error) or error > 5.0e-7:
        raise AssertionError(f"Equivalent q/-q rotations produced {error} radians")


def test_analysis_is_read_only_and_detects_both_sides():
    reset_scene()
    armature = make_humanoid()
    before = limb_ik._armature_digest(armature)
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    payload = json.loads(settings.analysis_json)
    for kind in limb_ik.KINDS:
        for side in limb_ik.SIDES:
            if payload["limbs"][kind][side]["status"] not in {"READY", "WARNING"}:
                raise AssertionError(payload["limbs"][kind][side])
    if limb_ik._armature_digest(armature) != before or owned_bones(armature):
        raise AssertionError("Analyze Rig mutated the Armature")
    if limb_ik.ARMATURE_ID_KEY in armature.data:
        raise AssertionError("Analyze Rig wrote ownership to Armature Data")


def test_direct_preroll_check_is_read_only_and_keeps_existing_mch_rig():
    reset_scene()
    armature = make_humanoid()
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    settings.selected_limb = "LEFT_ARM"
    before_digest = limb_ik._armature_digest(armature)
    before_dirty = bpy.data.is_dirty
    if bpy.ops.character_designer.limb_ik_direct_preroll_check() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    payload = json.loads(settings.direct_preroll_json)
    check = payload["result"]
    if check["status"] != "ALIGNED":
        raise AssertionError(f"Modeled +Y arm should already be Direct-aligned: {check}")
    if abs(check["rest_plane_offset_degrees"]) > 1.0e-4 or check["joint_shift"] > 1.0e-6:
        raise AssertionError(f"Aligned Direct check proposed an unnecessary Rest edit: {check}")
    if limb_ik._armature_digest(armature) != before_digest or bpy.data.is_dirty != before_dirty:
        raise AssertionError("Direct Pre-Roll check changed the source Armature or Blend dirty state")

    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory_before = limb_ik._validate_inventory(armature)
    owned_before = {bone.name for bone in inventory_before["bones"]}
    constraints_before = {
        (pose_bone.name, constraint.name)
        for pose_bone, constraint, _record in inventory_before["records"]
    }
    if bpy.ops.character_designer.limb_ik_direct_preroll_check() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory_after = limb_ik._validate_inventory(armature)
    if {bone.name for bone in inventory_after["bones"]} != owned_before:
        raise AssertionError("Direct Pre-Roll check removed or replaced MCH/ORI bones")
    constraints_after = {
        (pose_bone.name, constraint.name)
        for pose_bone, constraint, _record in inventory_after["records"]
    }
    if constraints_after != constraints_before:
        raise AssertionError("Direct Pre-Roll check changed the generated constraint graph")


def test_direct_preroll_check_reports_replane_and_exact_straight_risk():
    reset_scene()
    armature = make_humanoid(include_right=False)
    bpy.ops.object.mode_set(mode="EDIT")
    upper = armature.data.edit_bones["upper_arm.L"]
    lower = armature.data.edit_bones["forearm.L"]
    upper.tail.y = -0.12
    lower.head = upper.tail
    bpy.ops.object.mode_set(mode="POSE")
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    settings.selected_limb = "LEFT_ARM"
    before_digest = limb_ik._armature_digest(armature)
    if bpy.ops.character_designer.limb_ik_direct_preroll_check() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    check = json.loads(settings.direct_preroll_json)["result"]
    if check["status"] != "REPLANE_REQUIRED":
        raise AssertionError(f"Opposite Rest bend was not rejected for Direct IK: {check}")
    if abs(abs(check["rest_plane_offset_degrees"]) - 180.0) > 1.0e-3:
        raise AssertionError(f"Opposite Rest bend did not report a 180 degree plane offset: {check}")
    if check["joint_shift"] <= 0.1 or limb_ik._armature_digest(armature) != before_digest:
        raise AssertionError(f"Re-plane report was not read-only/actionable: {check}")

    bpy.ops.object.mode_set(mode="EDIT")
    upper = armature.data.edit_bones["upper_arm.L"]
    lower = armature.data.edit_bones["forearm.L"]
    midpoint = (Vector(upper.head) + Vector(lower.tail)) * 0.5
    upper.tail = midpoint
    lower.head = midpoint
    bpy.ops.object.mode_set(mode="POSE")
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_direct_preroll_check() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    straight = json.loads(settings.direct_preroll_json)["result"]
    if straight["status"] != "UNSTABLE":
        raise AssertionError(f"Exact-straight Direct chain was not marked unstable: {straight}")


def test_direct_preroll_values_make_replaned_direct_start_pose_exact():
    reset_scene()
    armature = make_humanoid(include_right=False, roll_offset=0.73)
    bpy.ops.object.mode_set(mode="EDIT")
    upper = armature.data.edit_bones["upper_arm.L"]
    lower = armature.data.edit_bones["forearm.L"]
    upper.tail.y = -0.12
    lower.head = upper.tail
    bpy.ops.object.mode_set(mode="POSE")
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_direct_preroll_check() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    check = json.loads(settings.direct_preroll_json)["result"]
    if check["status"] != "REPLANE_REQUIRED":
        raise AssertionError(f"Fixture did not expose a Rest-plane mismatch: {check}")

    bpy.ops.object.mode_set(mode="EDIT")
    upper = armature.data.edit_bones["upper_arm.L"]
    lower = armature.data.edit_bones["forearm.L"]
    proposed_joint = Vector(check["proposed_joint"])
    upper.tail = proposed_joint
    lower.head = proposed_joint
    lower.parent = upper
    lower.use_connect = True
    upper.roll = math.radians(check["proposed_upper_roll_degrees"])
    lower.roll = math.radians(check["proposed_lower_roll_degrees"])
    bpy.ops.object.mode_set(mode="POSE")
    rest = {
        name: armature.data.bones[name].matrix_local.copy()
        for name in ("upper_arm.L", "forearm.L")
    }
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    settings.selected_limb = "LEFT_ARM"
    plans = limb_ik._plans_for_selected(bpy.context, armature, settings)
    limb_ik._build_plans(
        bpy.context,
        armature,
        plans,
        schema=limb_ik.ENHANCED_SCHEMA,
    )
    bpy.context.view_layer.update()
    inventory = limb_ik._validate_inventory(armature)
    if inventory["schema"] != limb_ik.ENHANCED_SCHEMA:
        raise AssertionError("Direct Pre-Roll fixture did not build the helper-free schema")
    forbidden = ("MCH_upper", "MCH_forearm", "ORI_upper", "ORI_forearm")
    if any(any(token in bone.name for token in forbidden) for bone in inventory["bones"]):
        raise AssertionError("Direct Pre-Roll fixture unexpectedly generated MCH/ORI bones")
    for name, expected in rest.items():
        pose = armature.pose.bones[name].matrix
        position_error = (pose.translation - expected.translation).length
        rotation_error = pose.to_quaternion().rotation_difference(expected.to_quaternion()).angle
        deform = pose @ expected.inverted()
        deform_rotation = deform.to_quaternion().angle
        if position_error > 2.0e-5 or rotation_error > 2.0e-4 or deform_rotation > 2.0e-4:
            raise AssertionError(
                f"Re-planed Direct start pose changed {name}: "
                f"position={position_error}, rotation={rotation_error}, deform={deform_rotation}"
            )
    assert_pole_plane_alignment(armature, ("ARM", "L"), "Direct Pre-Roll start pose")


def _rest_signature(armature, names):
    return {
        name: {
            "head": tuple(round(float(value), 9) for value in armature.data.bones[name].head_local),
            "tail": tuple(round(float(value), 9) for value in armature.data.bones[name].tail_local),
            "matrix": tuple(
                round(float(value), 9)
                for row in armature.data.bones[name].matrix_local
                for value in row
            ),
            "parent": armature.data.bones[name].parent.name if armature.data.bones[name].parent else "",
            "connect": bool(armature.data.bones[name].use_connect),
        }
        for name in names
    }


def test_direct_preroll_minimal_build_and_remove_restores_exact_rest():
    reset_scene()
    armature = make_humanoid(include_right=False, roll_offset=0.73)
    bpy.ops.object.mode_set(mode="EDIT")
    upper = armature.data.edit_bones["upper_arm.L"]
    lower = armature.data.edit_bones["forearm.L"]
    upper.tail.y = -0.12
    lower.head = upper.tail
    bpy.ops.object.mode_set(mode="POSE")
    source_names = tuple(
        bone.name for bone in armature.data.bones if bone.get(limb_ik.OWNER_KEY) != limb_ik.OWNER_VALUE
    )
    original_rest = _rest_signature(armature, source_names)
    original_digest = limb_ik._armature_digest(armature)
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    settings.selected_limb = "LEFT_ARM"
    settings.build_method = "DIRECT_PREROLL"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    inventory = limb_ik._validate_inventory(armature)
    expected_display = limb_ik.LIMB_SPEC["ARM"]["display"].format(side="L")
    expected_bones = {"CTRL_hand_IK.L", "CTRL_elbow_pole.L", expected_display}
    if inventory["schema"] != limb_ik.DIRECT_PREROLL_SCHEMA:
        raise AssertionError(f"Direct build used schema {inventory['schema']}")
    if {bone.name for bone in inventory["bones"]} != expected_bones:
        raise AssertionError(
            "Direct build was not Target + Pole + one hidden display helper: "
            f"{[bone.name for bone in inventory['bones']]}"
        )
    if {record["role"] for _pb, _constraint, record in inventory["records"]} != {
        "IK",
        "END_ROTATION",
        "AUTO_OFFSET_ROTATION",
        "POLE_DISPLAY_TRACK",
    }:
        raise AssertionError("Direct build generated unexpected constraints")
    rig = inventory["rigs"][("ARM", "L")]
    display = rig["display"]
    pole = rig["pole"]
    unexpected_helpers = {
        bone.name
        for bone in inventory["bones"]
        if bone.name.startswith(("MCH_", "ORI_", "VIS_")) and bone.name != expected_display
    }
    if (
        inventory["master"] is not None
        or unexpected_helpers
        or display is None
        or display.name != expected_display
        or display.parent is None
        or display.parent.name != pole.name
        or display.use_connect
        or display.use_deform
        or not display.hide
        or not display.hide_select
        or armature.pose.bones[pole.name].custom_shape_transform is None
        or armature.pose.bones[pole.name].custom_shape_transform.name != display.name
    ):
        raise AssertionError(
            "Direct build did not keep its sole Pole-display helper hidden and display-only: "
            f"master={inventory['master']}, unexpected={unexpected_helpers}, "
            f"display={getattr(display, 'name', None)}, parent={getattr(display.parent, 'name', None)}, "
            f"pole={pole.name}, connect={display.use_connect}, deform={display.use_deform}, "
            f"hide={display.hide}, hide_select={display.hide_select}, "
            f"transform={getattr(armature.pose.bones[pole.name].custom_shape_transform, 'name', None)}"
        )
    if limb_ik.DIRECT_REST_KEY not in armature.data or limb_ik._armature_digest(armature) == original_digest:
        raise AssertionError("Direct build did not persist its reversible Rest re-plane")
    for name in ("upper_arm.L", "forearm.L"):
        assert_matrix_close(
            armature.pose.bones[name].matrix,
            armature.data.bones[name].matrix_local,
            f"Direct start frame {name}",
            location=2.0e-5,
            rotation=2.0e-4,
        )
    assert_pole_plane_alignment(armature, ("ARM", "L"), "Direct minimal start pose")

    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if _rest_signature(armature, source_names) != original_rest:
        raise AssertionError("Direct Remove did not restore the exact source Rest skeleton")
    if limb_ik._armature_digest(armature) != original_digest:
        raise AssertionError("Direct Remove did not restore the original Armature digest")
    if any(key in armature.data for key in (limb_ik.ARMATURE_ID_KEY, limb_ik.SCHEMA_KEY, limb_ik.DIRECT_REST_KEY)):
        raise AssertionError("Direct Remove left ownership or Rest metadata")
    if owned_bones(armature) or limb_ik._owned_constraint_records(armature):
        raise AssertionError("Direct Remove left generated bones or constraints")


def test_direct_preroll_incremental_build_all_and_method_mixing():
    reset_scene()
    armature = make_humanoid(roll_offset=0.41)
    source_names = tuple(bone.name for bone in armature.data.bones)
    original_rest = _rest_signature(armature, source_names)
    _result, settings = analyze(armature)
    settings.build_method = "DIRECT_PREROLL"
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    first = limb_ik._validate_inventory(armature)
    if len(first["bones"]) != 3 or len(first["records"]) != 4:
        raise AssertionError(
            "Selected Direct build was not exactly Target + Pole + hidden Pole display"
        )

    before_mix = (
        _rest_signature(armature, source_names),
        {bone.name for bone in first["bones"]},
        armature.data[limb_ik.DIRECT_REST_KEY],
    )
    settings.build_method = "ROLL_DECOUPLED"
    settings.selected_limb = "RIGHT_ARM"
    if cancelled_result(bpy.ops.character_designer.limb_ik_build_selected) != {"CANCELLED"}:
        raise AssertionError("Stable build mixed into an existing Direct Armature")
    after_mix_inventory = limb_ik._validate_inventory(armature)
    after_mix = (
        _rest_signature(armature, source_names),
        {bone.name for bone in after_mix_inventory["bones"]},
        armature.data[limb_ik.DIRECT_REST_KEY],
    )
    if after_mix != before_mix:
        raise AssertionError("Rejected Stable/Direct mix changed the existing Direct rig")

    settings.build_method = "DIRECT_PREROLL"
    if bpy.ops.character_designer.limb_ik_build_all() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory = limb_ik._validate_inventory(armature)
    if inventory["schema"] != limb_ik.DIRECT_PREROLL_SCHEMA or len(inventory["rigs"]) != 4:
        raise AssertionError("Direct Build All did not complete all four limbs")
    if len(inventory["bones"]) != 12 or len(inventory["records"]) != 16:
        raise AssertionError(
            f"Direct Build All was not minimal: bones={len(inventory['bones'])}, records={len(inventory['records'])}"
        )
    display_names = {rig["display"].name for rig in inventory["rigs"].values()}
    unexpected_helpers = {
        bone.name
        for bone in inventory["bones"]
        if bone.name.startswith(("MCH_", "ORI_", "VIS_")) and bone.name not in display_names
    }
    if inventory["master"] is not None or unexpected_helpers or len(display_names) != 4:
        raise AssertionError(
            "Direct Build All generated anything beyond one hidden Pole display per limb"
        )
    for rig in inventory["rigs"].values():
        display = rig["display"]
        if (
            display.parent is None
            or display.parent.name != rig["pole"].name
            or display.use_connect
            or display.use_deform
            or not display.hide
            or not display.hide_select
        ):
            raise AssertionError("A Direct Pole display helper is visible or has an edited hierarchy")
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if _rest_signature(armature, source_names) != original_rest:
        raise AssertionError("Multi-limb Direct Remove did not restore every source Rest bone")


def test_incremental_build_refuses_damaged_existing_widget_resources():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    settings.build_method = "DIRECT_PREROLL"
    settings.selected_limb = "LEFT_LEG"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    target = armature.pose.bones["CTRL_foot_IK.L"]
    original_shape = target.custom_shape
    target.custom_shape = None
    before_inventory = limb_ik._validate_inventory(armature)
    before_bones = {bone.name for bone in armature.data.bones}
    before_ids = {rig["rig_id"] for rig in before_inventory["rigs"].values()}
    before_direct_registry = armature.data[limb_ik.DIRECT_REST_KEY]

    settings.selected_limb = "RIGHT_LEG"
    if cancelled_result(bpy.ops.character_designer.limb_ik_build_selected) != {"CANCELLED"}:
        raise AssertionError("Incremental Build compounded an invalid existing Foot assignment")
    after_inventory = limb_ik._validate_inventory(armature)
    if {bone.name for bone in armature.data.bones} != before_bones:
        raise AssertionError("Refused incremental Build left new generated bones")
    if {rig["rig_id"] for rig in after_inventory["rigs"].values()} != before_ids:
        raise AssertionError("Refused incremental Build changed the existing rig IDs")
    if armature.data.get(limb_ik.DIRECT_REST_KEY) != before_direct_registry:
        raise AssertionError("Refused incremental Build changed Direct Rest metadata")
    if target.custom_shape is not None:
        raise AssertionError("Refused incremental Build rewrote the artist-visible invalid assignment")

    target.custom_shape = original_shape
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)


def test_direct_preroll_build_failure_rolls_back_source_rest():
    reset_scene()
    armature = make_humanoid(include_right=False, roll_offset=0.37)
    bpy.ops.object.mode_set(mode="EDIT")
    upper = armature.data.edit_bones["upper_arm.L"]
    lower = armature.data.edit_bones["forearm.L"]
    upper.tail.y = -0.12
    lower.head = upper.tail
    bpy.ops.object.mode_set(mode="POSE")
    source_names = tuple(bone.name for bone in armature.data.bones)
    original_rest = _rest_signature(armature, source_names)
    original_digest = limb_ik._armature_digest(armature)
    _result, settings = analyze(armature)
    settings.build_method = "DIRECT_PREROLL"
    settings.selected_limb = "LEFT_ARM"
    original_create = limb_ik._create_constraints_and_shapes

    def injected(*_args, **_kwargs):
        raise RuntimeError("injected Direct post-Rest failure")

    limb_ik._create_constraints_and_shapes = injected
    try:
        if cancelled_result(bpy.ops.character_designer.limb_ik_build_selected) != {"CANCELLED"}:
            raise AssertionError("Injected Direct failure did not cancel")
    finally:
        limb_ik._create_constraints_and_shapes = original_create
    if _rest_signature(armature, source_names) != original_rest or limb_ik._armature_digest(armature) != original_digest:
        raise AssertionError("Direct build rollback did not restore the source Rest skeleton")
    if owned_bones(armature) or limb_ik._owned_constraint_records(armature):
        raise AssertionError("Direct build rollback left generated bones or constraints")
    if any(key in armature.data for key in (limb_ik.ARMATURE_ID_KEY, limb_ik.SCHEMA_KEY, limb_ik.DIRECT_REST_KEY)):
        raise AssertionError("Direct build rollback left metadata")
    if bpy.data.collections.get(limb_ik.WIDGET_COLLECTION_NAME) is not None:
        raise AssertionError("Direct build rollback left widgets")


def test_direct_preroll_rebuild_stays_minimal_and_tamper_is_guarded():
    reset_scene()
    armature = make_humanoid(include_right=False, roll_offset=0.58)
    source_names = tuple(bone.name for bone in armature.data.bones)
    original_rest = _rest_signature(armature, source_names)
    _result, settings = analyze(armature)
    settings.build_method = "DIRECT_PREROLL"
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    first = limb_ik._validate_inventory(armature)
    old_ids = {rig["rig_id"] for rig in first["rigs"].values()}
    target = armature.pose.bones["CTRL_hand_IK.L"]
    target.location += Vector((-0.03, 0.02, 0.04))
    bpy.context.view_layer.update()
    target_before = target.matrix.copy()
    if bpy.ops.character_designer.limb_ik_rebuild() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rebuilt = limb_ik._validate_inventory(armature)
    if (
        rebuilt["schema"] != limb_ik.DIRECT_PREROLL_SCHEMA
        or len(rebuilt["bones"]) != 3
        or len(rebuilt["records"]) != 4
    ):
        raise AssertionError(
            "Direct Rebuild did not preserve Target + Pole + one hidden Pole display"
        )
    if {rig["rig_id"] for rig in rebuilt["rigs"].values()} == old_ids:
        raise AssertionError("Direct Rebuild did not replace its rig ID")
    assert_matrix_close(
        armature.pose.bones["CTRL_hand_IK.L"].matrix,
        target_before,
        "Direct Rebuild target pose",
        location=4.0e-4,
        rotation=3.0e-3,
    )

    bpy.ops.object.mode_set(mode="EDIT")
    armature.data.edit_bones["forearm.L"].roll += 0.05
    bpy.ops.object.mode_set(mode="POSE")
    tampered = _rest_signature(armature, source_names)
    owned_before = {bone.name for bone in owned_bones(armature)}
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Direct Remove accepted an edited applied Rest bone")
    if _rest_signature(armature, source_names) != tampered or {bone.name for bone in owned_bones(armature)} != owned_before:
        raise AssertionError("Rejected Direct Remove changed the tampered rig")

    bpy.ops.object.mode_set(mode="EDIT")
    armature.data.edit_bones["forearm.L"].roll -= 0.05
    bpy.ops.object.mode_set(mode="POSE")
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if _rest_signature(armature, source_names) != original_rest:
        raise AssertionError("Direct Rebuild/Remove lost the original pre-Build Rest skeleton")


def test_legacy_direct_schema4_without_display_helper_rebuilds_to_schema5():
    """An untouched 0.31.1 Direct rig must remain readable and upgradeable."""

    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    settings.build_method = "DIRECT_PREROLL"
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    current = limb_ik._validate_inventory(armature)
    rig = current["rigs"][("ARM", "L")]
    pole = armature.pose.bones[rig["pole"].name]
    display = armature.pose.bones[rig["display"].name]
    track = next(
        constraint
        for _owner, constraint, record in rig["entries"]
        if record["role"] == "POLE_DISPLAY_TRACK"
    )

    # Recreate the exact structural difference between schema 4 and schema 5:
    # schema 4 owned only Target + Pole, used the Pole itself as the Custom
    # Shape frame, and had no display-tracking constraint.
    pole.custom_shape_transform = None
    display.constraints.remove(track)
    auto_owner, auto_offset, _auto_record = next(
        entry
        for entry in rig["entries"]
        if entry[2]["role"] == "AUTO_OFFSET_ROTATION"
    )
    auto_registry = limb_ik._constraint_registry(auto_owner, strict=True)
    auto_registry.pop(auto_offset.name)
    limb_ik._write_constraint_registry(auto_owner, auto_registry)
    auto_owner.constraints.remove(auto_offset)
    display_name = display.name
    display.bone.hide = False
    bpy.ops.object.mode_set(mode="EDIT")
    armature.data.edit_bones.remove(armature.data.edit_bones[display_name])
    bpy.ops.object.mode_set(mode="POSE")
    armature.data[limb_ik.SCHEMA_KEY] = limb_ik.LEGACY_DIRECT_PREROLL_SCHEMA
    del armature.data[limb_ik.TARGET_ROTATION_VERSION_KEY]
    bpy.context.view_layer.update()

    legacy = limb_ik._validate_inventory(armature)
    if (
        legacy["schema"] != limb_ik.LEGACY_DIRECT_PREROLL_SCHEMA
        or len(legacy["bones"]) != 2
        or len(legacy["records"]) != 2
        or legacy["rigs"][("ARM", "L")]["display"] is not None
    ):
        raise AssertionError("The intact legacy Direct schema-4 contract was rejected")
    if len(limb_ik._direct_pole_guide_segments(bpy.context)) != 1:
        raise AssertionError("A legacy schema-4 Pole did not retain its persistent shaft")

    if bpy.ops.character_designer.limb_ik_rebuild() != {"FINISHED"}:
        raise AssertionError(f"Legacy Direct schema-4 Rebuild failed: {settings.last_message}")
    upgraded = limb_ik._validate_inventory(armature)
    upgraded_rig = upgraded["rigs"][("ARM", "L")]
    if (
        upgraded["schema"] != limb_ik.DIRECT_PREROLL_SCHEMA
        or len(upgraded["bones"]) != 3
        or len(upgraded["records"]) != 4
        or upgraded_rig["display"] is None
        or armature.pose.bones[upgraded_rig["pole"].name].custom_shape_transform is None
        or armature.pose.bones[upgraded_rig["pole"].name].custom_shape_transform.name
        != upgraded_rig["display"].name
    ):
        raise AssertionError("Legacy Direct schema 4 did not upgrade to the schema-5 display contract")
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)


def test_direct_preroll_initial_build_refuses_pose_animation_and_external_source_dependencies():
    reset_scene()
    armature = make_humanoid(include_right=False, roll_offset=0.31)
    source_names = tuple(bone.name for bone in armature.data.bones)
    original_rest = _rest_signature(armature, source_names)
    _result, settings = analyze(armature)
    settings.build_method = "DIRECT_PREROLL"
    settings.selected_limb = "LEFT_ARM"

    upper = armature.pose.bones["upper_arm.L"]
    upper.rotation_mode = "XYZ"
    upper.rotation_euler.x = 0.1
    bpy.context.view_layer.update()
    if cancelled_result(bpy.ops.character_designer.limb_ik_build_selected) != {"CANCELLED"}:
        raise AssertionError("Direct build accepted a posed source chain")
    upper.rotation_euler.x = 0.0
    bpy.context.view_layer.update()

    upper.keyframe_insert(data_path="rotation_euler", index=0, frame=1)
    if cancelled_result(bpy.ops.character_designer.limb_ik_build_selected) != {"CANCELLED"}:
        raise AssertionError("Direct build accepted source-chain animation")
    armature.animation_data_clear()

    probe = bpy.data.objects.new("DirectSourceProbe", None)
    bpy.context.scene.collection.objects.link(probe)
    dependency = probe.constraints.new(type="COPY_TRANSFORMS")
    dependency.name = "External source dependency"
    dependency.target = armature
    dependency.subtarget = "upper_arm.L"
    if cancelled_result(bpy.ops.character_designer.limb_ik_build_selected) != {"CANCELLED"}:
        raise AssertionError("Direct build accepted an external constraint on a source Rest bone")
    bpy.data.objects.remove(probe, do_unlink=True)

    hook_mesh = bpy.data.meshes.new("DirectSourceHookProbeMesh")
    hook_probe = bpy.data.objects.new("DirectSourceHookProbe", hook_mesh)
    bpy.context.scene.collection.objects.link(hook_probe)
    hook = hook_probe.modifiers.new(name="External source Hook", type="HOOK")
    hook.object = armature
    hook.subtarget = "upper_arm.L"
    if cancelled_result(bpy.ops.character_designer.limb_ik_build_selected) != {"CANCELLED"}:
        raise AssertionError("Direct build accepted a Hook modifier on a source Rest bone")
    bpy.data.objects.remove(hook_probe, do_unlink=True)
    bpy.data.meshes.remove(hook_mesh)

    if _rest_signature(armature, source_names) != original_rest:
        raise AssertionError("Rejected Direct source preflight changed the Rest skeleton")
    if owned_bones(armature) or limb_ik._owned_constraint_records(armature):
        raise AssertionError("Rejected Direct source preflight left generated rig data")
    if any(key in armature.data for key in (limb_ik.ARMATURE_ID_KEY, limb_ik.SCHEMA_KEY, limb_ik.DIRECT_REST_KEY)):
        raise AssertionError("Rejected Direct source preflight left metadata")


def test_direct_preroll_lifecycle_refuses_new_source_dependencies_and_hydrates_mode():
    reset_scene()
    armature = make_humanoid(include_right=False, roll_offset=0.47)
    source_names = tuple(bone.name for bone in armature.data.bones)
    _result, settings = analyze(armature)
    settings.build_method = "DIRECT_PREROLL"
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    settings.build_method = "ROLL_DECOUPLED"
    if bpy.ops.character_designer.limb_ik_analyze() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if settings.build_method != "DIRECT_PREROLL":
        raise AssertionError("Analyze did not hydrate an existing Direct rig's Build Method")

    def lifecycle_signature():
        inventory = limb_ik._validate_inventory(armature)
        return (
            _rest_signature(armature, source_names),
            tuple(sorted(bone.name for bone in inventory["bones"])),
            tuple(sorted(rig["rig_id"] for rig in inventory["rigs"].values())),
            armature.data[limb_ik.DIRECT_REST_KEY],
        )

    baseline = lifecycle_signature()
    upper = armature.pose.bones["upper_arm.L"]
    upper.rotation_mode = "XYZ"
    upper.keyframe_insert(data_path="rotation_euler", index=0, frame=1)
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Direct Remove accepted new source-chain animation")
    if cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) != {"CANCELLED"}:
        raise AssertionError("Direct Rebuild accepted new source-chain animation")
    if lifecycle_signature() != baseline:
        raise AssertionError("Rejected animated Direct lifecycle operation changed the old rig")
    armature.animation_data_clear()

    foreign = upper.constraints.new(type="COPY_LOCATION")
    foreign.name = "Artist source constraint"
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Direct Remove accepted a foreign source constraint")
    if cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) != {"CANCELLED"}:
        raise AssertionError("Direct Rebuild accepted a foreign source constraint")
    upper.constraints.remove(foreign)
    if lifecycle_signature() != baseline:
        raise AssertionError("Rejected constrained Direct lifecycle operation changed the old rig")

    end = armature.pose.bones["hand.L"]
    foreign_end = end.constraints.new(type="LIMIT_ROTATION")
    foreign_end.name = "Artist end source constraint"
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Direct Remove accepted a foreign constraint on the source-chain end")
    if cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) != {"CANCELLED"}:
        raise AssertionError("Direct Rebuild accepted a foreign constraint on the source-chain end")
    end.constraints.remove(foreign_end)
    if lifecycle_signature() != baseline:
        raise AssertionError("Rejected end-constrained Direct lifecycle operation changed the old rig")

    target = armature.pose.bones["CTRL_hand_IK.L"]
    target.matrix_basis.translation = Vector((0.03, 0.0, 0.0))
    set_pole_direction(settings, "ARM", "L", (0.0, -1.0, 0.0))
    if cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) != {"CANCELLED"}:
        raise AssertionError("Direct Rebuild changed direction while its controls were posed")
    if lifecycle_signature() != baseline:
        raise AssertionError("Rejected posed direction change did not preserve the exact old Direct rig")
    target.matrix_basis = Matrix.Identity(4)
    set_pole_direction(settings, "ARM", "L", (0.0, 1.0, 0.0))

    bpy.ops.object.mode_set(mode="EDIT")
    armature.data.edit_bones["CTRL_hand_IK.L"].parent = armature.data.edit_bones["upper_arm.L"]
    bpy.ops.object.mode_set(mode="POSE")
    try:
        limb_ik._validate_inventory(armature)
    except limb_ik.LimbIKError:
        pass
    else:
        raise AssertionError("Direct inventory accepted an edited control parent")
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Direct Remove accepted an edited control hierarchy")
    bpy.ops.object.mode_set(mode="EDIT")
    armature.data.edit_bones["CTRL_hand_IK.L"].parent = None
    armature.data.edit_bones["CTRL_hand_IK.L"].use_connect = False
    bpy.ops.object.mode_set(mode="POSE")
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)


def test_low_confidence_helper_chain_is_not_guessed():
    reset_scene()
    data = bpy.data.armatures.new("HelpersData")
    armature = bpy.data.objects.new("Helpers", data)
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    upper = add_bone(data.edit_bones, "upper_arm_helper.L", (0.2, 0, 1), (0.5, 0, 1))
    lower = add_bone(data.edit_bones, "forearm_twist.L", (0.5, 0, 1), (0.8, 0, 1), upper)
    add_bone(data.edit_bones, "hand_corrective.L", (0.8, 0, 1), (1, 0, 1), lower)
    bpy.ops.object.mode_set(mode="POSE")
    payload = limb_ik.analyze_armature(armature)
    if payload["limbs"]["ARM"]["L"]["status"] != "MISSING":
        raise AssertionError("Helper/twist/corrective chain was guessed")


def test_pole_direction_defaults_analyze_preserves_and_reset_selected():
    reset_scene()
    armature = make_humanoid(near_straight=True)
    settings = bpy.context.window_manager.character_designer_limb_ik
    for kind in limb_ik.KINDS:
        for side in limb_ik.SIDES:
            actual = Vector(getattr(settings, pole_direction_property(kind, side)))
            if (actual - DEFAULT_POLE_DIRECTIONS[kind]).length > 1.0e-9:
                raise AssertionError(f"Wrong {side} {kind} default Pole direction: {tuple(actual)}")

    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    for kind in limb_ik.KINDS:
        for side in limb_ik.SIDES:
            actual = Vector(getattr(settings, pole_direction_property(kind, side)))
            expected = DEFAULT_POLE_DIRECTIONS[kind]
            if (actual - expected).length > 1.0e-9:
                raise AssertionError(
                    f"First Analyze hydrated {side} {kind} from something other than its fixed axis: "
                    f"{tuple(actual)} != {tuple(expected)}"
                )
    custom = Vector((0.25, 0.9, -0.2)).normalized()
    set_pole_direction(settings, "ARM", "L", custom)
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if (Vector(settings.left_arm_pole_direction) - custom).length > 1.0e-7:
        raise AssertionError("Analyze Rig overwrote the artist's Left Arm Pole direction")
    payload = json.loads(settings.analysis_json)
    if payload["limbs"]["ARM"]["L"]["status"] != "READY":
        raise AssertionError(f"Explicit direction did not resolve a near-straight chain: {payload['limbs']['ARM']['L']}")
    obsolete = ("modeled bend", "roll fallback", "projected -local Z")
    combined_message = settings.last_message + " " + " ".join(payload.get("warnings", ()))
    if any(token in combined_message for token in obsolete):
        raise AssertionError(f"Analyze still advertised obsolete automatic Pole inference: {combined_message}")

    set_pole_direction(settings, "ARM", "R", (0.1, 0.8, 0.3))
    set_pole_direction(settings, "LEG", "L", (-0.2, -0.9, 0.1))
    set_pole_direction(settings, "LEG", "R", (0.3, -0.7, -0.2))
    untouched = {
        key: Vector(getattr(settings, property_name))
        for key, property_name in POLE_DIRECTION_PROPERTIES.items()
        if key != ("ARM", "L")
    }
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_default_pole_direction() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    expected_left_arm = DEFAULT_POLE_DIRECTIONS["ARM"]
    if (Vector(settings.left_arm_pole_direction) - expected_left_arm).length > 1.0e-6:
        raise AssertionError("Default Direction did not restore the fixed Left Arm +Y axis")
    for key, before in untouched.items():
        after = Vector(getattr(settings, pole_direction_property(*key)))
        if (after - before).length > 1.0e-9:
            raise AssertionError(f"Default Direction changed non-selected limb {key}")


def test_x_negative_y_defaults_keep_elbows_back_and_knees_forward():
    """Guard X's -Y-forward anatomical sign convention."""
    reset_scene()
    armature = make_humanoid()
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    expected_signs = {"ARM": 1.0, "LEG": -1.0}
    expected_directions = {}
    source_pose = {}
    for kind in limb_ik.KINDS:
        for side in limb_ik.SIDES:
            key = (kind, side)
            names = tuple(
                getattr(settings, limb_ik._field_name(kind, side, role))
                for role in limb_ik.ROLES
            )
            upper = armature.data.bones[names[0]]
            lower = armature.data.bones[names[1]]
            rest_bend = _axis_residual(upper.head_local, lower.head_local, lower.tail_local)
            expected_sign = expected_signs[kind]
            if rest_bend.y * expected_sign <= 1.0e-4:
                raise AssertionError(
                    f"{key} fixture no longer bends on the anatomical Y side: {tuple(rest_bend)}"
                )
            expected_direction = projected_pole_direction(armature, key, DEFAULT_POLE_DIRECTIONS[kind])
            expected_directions[key] = expected_direction
            actual_default = Vector(getattr(settings, pole_direction_property(kind, side)))
            if (actual_default - DEFAULT_POLE_DIRECTIONS[kind]).length > 1.0e-9:
                raise AssertionError(
                    f"{key} default was not the fixed anatomical axis: "
                    f"actual={tuple(actual_default)}, expected={tuple(DEFAULT_POLE_DIRECTIONS[kind])}"
                )
            for name in names[:2]:
                source_pose[name] = armature.pose.bones[name].matrix.copy()

    if bpy.ops.character_designer.limb_ik_build_all() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    bpy.context.view_layer.update()
    inventory = limb_ik._validate_inventory(armature)

    for key, rig in inventory["rigs"].items():
        kind, _side = key
        expected_sign = expected_signs[kind]
        upper = armature.pose.bones[rig["chain"][0]]
        lower = armature.pose.bones[rig["chain"][1]]
        start = Vector(upper.head)
        end = Vector(lower.tail)
        bend = _axis_residual(start, lower.head, end)
        pole = _axis_residual(start, rig["pole"].head_local, end)
        if bend.y * expected_sign <= 1.0e-4:
            raise AssertionError(f"{key} evaluated bend has the wrong Y sign: {tuple(bend)}")
        if pole.y * expected_sign <= 1.0e-4:
            raise AssertionError(f"{key} Pole has the wrong Y sign: {tuple(pole)}")
        alignment = bend.normalized().dot(pole.normalized())
        if alignment <= 0.999:
            raise AssertionError(f"{key} evaluated bend and Pole disagree: {alignment}")
        saved = Vector(rig["pole"][limb_ik.POLE_DIRECTION_KEY])
        if (saved - expected_directions[key]).length > 1.0e-6:
            raise AssertionError(f"{key} stored the wrong default Pole direction: {tuple(saved)}")
        for name in rig["chain"][:2]:
            assert_matrix_close(armature.pose.bones[name].matrix, source_pose[name], f"{key} default Build {name}")


def make_x_like_lateral_humanoid():
    armature = make_humanoid()
    bpy.ops.object.mode_set(mode="EDIT")
    for side in limb_ik.SIDES:
        sign = 1.0 if side == "L" else -1.0
        thigh = armature.data.edit_bones[f"thigh.{side}"]
        shin = armature.data.edit_bones[f"shin.{side}"]
        # Approximate X's lateral-dominant knee plane: the modeled residual is
        # about 91% local X and 41% local -Y, far from either pure Y fallback.
        joint = Vector((thigh.head.x - sign * 0.22, -0.10, (thigh.head.z + shin.tail.z) * 0.5))
        thigh.tail = joint
        shin.head = joint
    bpy.ops.object.mode_set(mode="POSE")
    return armature


def test_x_like_lateral_knees_ignore_rest_residual_for_anatomical_default():
    reset_scene()
    armature = make_x_like_lateral_humanoid()

    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    expected_directions = {}
    for kind in limb_ik.KINDS:
        for side in limb_ik.SIDES:
            key = (kind, side)
            names = tuple(
                getattr(settings, limb_ik._field_name(kind, side, role))
                for role in limb_ik.ROLES
            )
            modeled = rest_bend_direction(armature, names)
            expected = projected_pole_direction(armature, key, DEFAULT_POLE_DIRECTIONS[kind])
            expected_directions[key] = expected
            actual = Vector(getattr(settings, pole_direction_property(kind, side)))
            if (actual - DEFAULT_POLE_DIRECTIONS[kind]).length > 1.0e-9:
                raise AssertionError(
                    f"{key} default followed Rest noise instead of its fixed anatomical axis: "
                    f"{tuple(actual)} != {tuple(DEFAULT_POLE_DIRECTIONS[kind])}"
                )
            if kind == "LEG":
                if abs(modeled.x) < 0.8:
                    raise AssertionError(f"{key} X-like fixture lost its lateral Rest residual: {tuple(modeled)}")
                if abs(expected.x) > 1.0e-6 or expected.y >= -0.99:
                    raise AssertionError(f"{key} anatomical knee default is not straight forward -Y: {tuple(expected)}")

    if bpy.ops.character_designer.limb_ik_build_all() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    bpy.context.view_layer.update()
    inventory = limb_ik._validate_inventory(armature)
    for key, rig in inventory["rigs"].items():
        saved = Vector(rig["pole"][limb_ik.POLE_DIRECTION_KEY])
        if (saved - expected_directions[key]).length > 1.0e-6:
            raise AssertionError(f"{key} did not store its fixed anatomical direction")
        if key[0] == "LEG" and (abs(saved.x) > 1.0e-5 or saved.y >= -0.99):
            raise AssertionError(f"{key} built a lateral knee Pole instead of straight forward: {tuple(saved)}")
        assert_pole_plane_alignment(armature, key, f"X-like {key} after Build")


def test_default_operator_restores_only_selected_fixed_anatomical_axis():
    reset_scene()
    armature = make_x_like_lateral_humanoid()
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    key = ("LEG", "L")
    names = tuple(
        getattr(settings, limb_ik._field_name(*key, role))
        for role in limb_ik.ROLES
    )
    modeled = rest_bend_direction(armature, names)
    expected = DEFAULT_POLE_DIRECTIONS["LEG"]
    if abs(modeled.x) < 0.8 or modeled.dot(expected) >= 0.8:
        raise AssertionError(f"Default-operator fixture is not distinct from the fixed -Y axis: {tuple(modeled)}")

    custom = {
        ("ARM", "L"): Vector((0.2, 0.7, -0.1)).normalized(),
        ("ARM", "R"): Vector((-0.1, 0.8, 0.2)).normalized(),
        ("LEG", "L"): Vector((0.3, 0.4, 0.5)).normalized(),
        ("LEG", "R"): Vector((-0.4, 0.2, -0.3)).normalized(),
    }
    for limb_key, direction in custom.items():
        set_pole_direction(settings, *limb_key, direction)
    untouched = {
        limb_key: Vector(getattr(settings, pole_direction_property(*limb_key)))
        for limb_key in custom
        if limb_key != key
    }

    settings.selected_limb = "LEFT_LEG"
    if bpy.ops.character_designer.limb_ik_default_pole_direction() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    actual = Vector(settings.left_leg_pole_direction)
    if (actual - expected).length > 1.0e-6:
        raise AssertionError(
            f"Default Direction did not restore the fixed straight-forward axis: "
            f"{tuple(actual)} != {tuple(expected)}"
        )
    for limb_key, before in untouched.items():
        after = Vector(getattr(settings, pole_direction_property(*limb_key)))
        if (after - before).length > 1.0e-7:
            raise AssertionError(f"Default Direction changed non-selected limb {limb_key}")


def test_saved_pole_direction_hydrates_without_automatic_migration():
    reset_scene()
    armature = make_humanoid(include_right=False)
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    settings.selected_limb = "LEFT_ARM"
    saved_direction = Vector((0.0, 1.0, 0.25)).normalized()
    settings.left_arm_pole_direction = saved_direction
    effective_saved = projected_pole_direction(armature, ("ARM", "L"), saved_direction)
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig = limb_ik._validate_inventory(armature)["rigs"][("ARM", "L")]
    if (Vector(rig["pole"][limb_ik.POLE_DIRECTION_KEY]) - effective_saved).length > 1.0e-6:
        raise AssertionError("Build did not persist the explicit Pole direction")

    # Simulate returning to this Armature in a later session.  The fixed +Y
    # default must not overwrite a generated Pole's saved artist choice.
    settings.left_arm_pole_direction = DEFAULT_POLE_DIRECTIONS["ARM"]
    settings.pole_direction_armature_token = ""
    result, settings = analyze(armature)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if (Vector(settings.left_arm_pole_direction) - effective_saved).length > 1.0e-6:
        raise AssertionError("Analyze silently migrated an existing saved Pole direction")


def test_explicit_pole_direction_validation_and_runtime_plane():
    # The source joint is modeled toward -Y, but the explicit +Y setting must
    # determine placement.  Direction magnitude must not alter Pole Distance.
    args = (
        Vector((0.0, 0.0, 0.0)),
        Vector((1.0, -0.2, 0.0)),
        Vector((2.0, 0.0, 0.0)),
        Vector((0.0, 0.0, 1.0)),
    )
    pole_unit, _angle, effective_unit = limb_ik._pole_solution(*args, Vector((0.0, 1.0, 0.0)), 0.75)
    pole_scaled, _angle, effective_scaled = limb_ik._pole_solution(*args, Vector((0.0, 5.0, 0.0)), 0.75)
    expected_distance = ((args[1] - args[0]).length + (args[2] - args[1]).length) * 0.75
    desired_joint = limb_ik._joint_on_direction_plane(
        args[0], args[1], args[2], Vector((0.0, 1.0, 0.0))
    )
    for label, pole in (("unit", pole_unit), ("scaled", pole_scaled)):
        direction = pole - desired_joint
        if direction.normalized().dot(Vector((0.0, 1.0, 0.0))) <= 0.999999:
            raise AssertionError(f"{label} explicit Pole direction lost +Y: {tuple(pole)}")
        if abs(direction.length - expected_distance) > 1.0e-6:
            raise AssertionError(f"{label} direction magnitude changed Pole Distance: {direction.length}")
    for label, effective in (("unit", effective_unit), ("scaled", effective_scaled)):
        if Vector(effective).normalized().dot(Vector((0.0, 1.0, 0.0))) <= 0.999999:
            raise AssertionError(f"{label} effective Pole direction was not normalized +Y: {tuple(effective)}")

    invalid_directions = (
        Vector((0.0, 0.0, 0.0)),
        Vector((float("nan"), 1.0, 0.0)),
        Vector((1.0, 0.0, 0.0)),
    )
    for direction in invalid_directions:
        try:
            limb_ik._pole_solution(*args, direction, 0.75)
        except limb_ik.LimbIKError:
            pass
        else:
            raise AssertionError(f"Invalid/chain-parallel Pole direction was accepted: {tuple(direction)}")

    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    settings.selected_limb = "LEFT_ARM"
    # Oppose both this fixture's +Y Rest bend and the fixed anatomical +Y default.
    # This proves the control and actual solver plane come from the explicit setting.
    settings.left_arm_pole_direction = (0.0, -1.0, 0.0)
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    assert_rest_pole_direction(armature, ("ARM", "L"), (0.0, -1.0, 0.0), "opposed Left Arm")
    assert_pole_plane_alignment(armature, ("ARM", "L"), "opposed Left Arm after Build")
    rig = limb_ik._validate_inventory(armature)["rigs"][("ARM", "L")]
    armature.pose.bones[rig["target"].name].location += Vector((-0.04, -0.03, 0.05))
    bpy.context.view_layer.update()
    assert_pole_plane_alignment(armature, ("ARM", "L"), "opposed Left Arm after target move")


def test_build_selected_right_leg_and_build_all_atomic_completion():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    configured = {
        ("ARM", "L"): Vector((0.0, -1.0, 0.15)).normalized(),
        ("ARM", "R"): Vector((0.0, -1.0, -0.2)).normalized(),
        ("LEG", "L"): Vector((0.1, 1.0, 0.0)).normalized(),
        ("LEG", "R"): Vector((-0.12, 1.0, 0.0)).normalized(),
    }
    for key, direction in configured.items():
        set_pole_direction(settings, *key, direction)
    settings.selected_limb = "RIGHT_LEG"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory = limb_ik._validate_inventory(armature)
    if set(inventory["rigs"]) != {("LEG", "R")}:
        raise AssertionError(f"Build IK did not isolate Right Leg: {set(inventory['rigs'])}")
    assert_rest_pole_direction(armature, ("LEG", "R"), configured[("LEG", "R")], "Build IK Right Leg")
    expected_call = tuple(
        (
            plan.chain.kind,
            plan.chain.side,
            tuple(round(value, 6) for value in plan.pole_direction),
        )
        for plan in limb_ik._plans_for_all(bpy.context, armature, settings)
    )
    original_build_plans = limb_ik._build_plans
    build_all_calls = []

    def recording_build_plans(context, target_armature, plans, **kwargs):
        build_all_calls.append(
            tuple(
                (plan.chain.kind, plan.chain.side, tuple(round(value, 6) for value in plan.pole_direction))
                for plan in plans
            )
        )
        return original_build_plans(context, target_armature, plans, **kwargs)

    limb_ik._build_plans = recording_build_plans
    try:
        if bpy.ops.character_designer.limb_ik_build_all() != {"FINISHED"}:
            raise AssertionError(settings.last_message)
    finally:
        limb_ik._build_plans = original_build_plans
    if build_all_calls != [expected_call]:
        raise AssertionError(
            f"Build All was not one four-chain transaction: actual={build_all_calls}, expected={expected_call}"
        )
    inventory = limb_ik._validate_inventory(armature)
    if set(inventory["rigs"]) != {(kind, side) for kind in limb_ik.KINDS for side in limb_ik.SIDES}:
        raise AssertionError("Build All did not complete every remaining limb")
    if len(inventory["bones"]) != 37 or len(inventory["records"]) != 47:
        raise AssertionError("Build All completion produced an inexact current-schema inventory")
    for key, direction in configured.items():
        assert_rest_pole_direction(armature, key, direction, f"Build All {key}")

    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    # A partially edited chain must reject the whole request before _build_plans
    # writes Master, controls, constraints, collections, or ownership.
    settings.right_leg_lower = ""
    if cancelled_result(bpy.ops.character_designer.limb_ik_build_all) != {"CANCELLED"}:
        raise AssertionError("Build All accepted a partially filled limb")
    if owned_bones(armature) or limb_ik._owned_constraint_records(armature):
        raise AssertionError("Rejected Build All left generated bones or constraints")
    if limb_ik.ARMATURE_ID_KEY in armature.data or limb_ik.SCHEMA_KEY in armature.data:
        raise AssertionError("Rejected Build All left ownership metadata")
    if bpy.data.collections.get(limb_ik.WIDGET_COLLECTION_NAME) is not None:
        raise AssertionError("Rejected Build All left a widget collection")

    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    settings.right_leg_pole_direction = (0.0, 0.0, 0.0)
    if cancelled_result(bpy.ops.character_designer.limb_ik_build_all) != {"CANCELLED"}:
        raise AssertionError("Build All accepted a zero Pole direction")
    if owned_bones(armature) or limb_ik._owned_constraint_records(armature):
        raise AssertionError("Invalid-direction Build All mutated generated bones or constraints")
    if limb_ik.ARMATURE_ID_KEY in armature.data or limb_ik.SCHEMA_KEY in armature.data:
        raise AssertionError("Invalid-direction Build All left ownership metadata")
    if bpy.data.collections.get(limb_ik.WIDGET_COLLECTION_NAME) is not None:
        raise AssertionError("Invalid-direction Build All left a widget collection")


def test_build_arm_then_leg_and_exact_contract():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    digest = json.loads(settings.analysis_json)["digest"]
    selected_before = {bone.name for bone in armature.pose.bones if bone.select}
    active_before = armature.data.bones.active.name if armature.data.bones.active else ""
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if limb_ik._armature_digest(armature) != digest:
        raise AssertionError("Generated controls polluted source digest")
    if bpy.ops.character_designer.limb_ik_build_leg() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    expected = {
        "CTRL_master",
        "CTRL_hand_IK.L", "CTRL_elbow_pole.L", "CTRL_hand_IK.R", "CTRL_elbow_pole.R",
        "VIS_elbow_pole_line.L", "MCH_elbow_pole_aim.L", "VIS_elbow_pole_line.R", "MCH_elbow_pole_aim.R",
        "MCH_upper_arm_IK.L", "MCH_forearm_IK.L", "ORI_upper_arm_IK.L", "ORI_forearm_IK.L",
        "MCH_upper_arm_IK.R", "MCH_forearm_IK.R", "ORI_upper_arm_IK.R", "ORI_forearm_IK.R",
        "CTRL_foot_IK.L", "CTRL_knee_pole.L", "CTRL_foot_IK.R", "CTRL_knee_pole.R",
        "VIS_knee_pole_line.L", "MCH_knee_pole_aim.L", "VIS_knee_pole_line.R", "MCH_knee_pole_aim.R",
        "MCH_thigh_IK.L", "MCH_shin_IK.L", "ORI_thigh_IK.L", "ORI_shin_IK.L",
        "MCH_thigh_IK.R", "MCH_shin_IK.R", "ORI_thigh_IK.R", "ORI_shin_IK.R",
        "CTRL_heel_roll.L", "MCH_foot_target.L", "CTRL_heel_roll.R", "MCH_foot_target.R",
    }
    if {bone.name for bone in owned_bones(armature)} != expected:
        raise AssertionError("Generated control bone set is wrong")
    inventory = limb_ik._validate_inventory(armature)
    if len(inventory["rigs"]) != 4 or len(inventory["records"]) != 47:
        raise AssertionError("Owned rig inventory is incomplete")
    for (_kind, _side), rig in inventory["rigs"].items():
        ik = next(constraint for _pb, constraint, record in rig["entries"] if record["role"] == "IK")
        if ik.chain_count != 2 or ik.target is not armature or ik.pole_target is not armature:
            raise AssertionError("IK contract is wrong")
        if rig["target"].use_deform or rig["pole"].use_deform:
            raise AssertionError("Control bones deform Meshes")
    if bpy.context.mode != "POSE" or {bone.name for bone in armature.pose.bones if bone.select} != selected_before:
        raise AssertionError("Build did not restore Pose selection/mode")
    if active_before and armature.data.bones.active.name != active_before:
        raise AssertionError("Build changed active Pose bone")


def test_explicit_direction_handles_arbitrary_roll_without_pose_pop():
    for roll in (0.4, 1.1, 1.57, -0.75, -1.4):
        reset_scene()
        armature = make_humanoid(include_right=False, roll_offset=roll)
        _result, settings = analyze(armature)
        settings.left_arm_pole_direction = DEFAULT_POLE_DIRECTIONS["ARM"]
        names = ("upper_arm.L", "forearm.L", "hand.L")
        before = {
            name: (
                armature.pose.bones[name].matrix.copy(),
                Vector(armature.pose.bones[name].head),
                Vector(armature.pose.bones[name].tail),
            )
            for name in names
        }
        if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
            raise AssertionError(f"roll={roll}: {settings.last_message}")
        bpy.context.view_layer.update()
        for name in names:
            pose_bone = armature.pose.bones[name]
            matrix, head, tail = before[name]
            position_error = (Vector(pose_bone.head) - head).length + (Vector(pose_bone.tail) - tail).length
            rotation_error = pose_bone.matrix.to_quaternion().rotation_difference(matrix.to_quaternion()).angle
            if position_error > 2.0e-4 or rotation_error > 2.0e-3:
                raise AssertionError(
                    f"roll={roll} {name} popped: position={position_error}, rotation={rotation_error}"
                )
        assert_rest_pole_direction(armature, ("ARM", "L"), DEFAULT_POLE_DIRECTIONS["ARM"], f"roll={roll}")
        assert_pole_plane_alignment(armature, ("ARM", "L"), f"roll={roll} after Build")


def test_build_from_non_rest_pose_preserves_full_limb_pose():
    reset_scene()
    armature = make_humanoid(include_right=False, roll_offset=0.73)
    _result, settings = analyze(armature)
    rotations = {
        "upper_arm.L": (0.18, -0.11, 0.24),
        "forearm.L": (-0.09, 0.21, -0.16),
        "hand.L": (0.31, -0.19, 0.27),
    }
    for name, euler in rotations.items():
        pose_bone = armature.pose.bones[name]
        pose_bone.rotation_mode = "XYZ"
        pose_bone.rotation_euler = euler
    bpy.context.view_layer.update()
    upper = armature.pose.bones["upper_arm.L"]
    lower = armature.pose.bones["forearm.L"]
    start = Vector(upper.head)
    joint = Vector(lower.head)
    axis = Vector(lower.tail) - start
    projection = start + axis * (joint - start).dot(axis) / axis.length_squared
    evaluated_direction = joint - projection
    if evaluated_direction.length <= 1.0e-8:
        raise AssertionError("Non-rest fixture has no usable explicit Pole direction")
    settings.left_arm_pole_direction = evaluated_direction.normalized()
    before = {
        name: (armature.pose.bones[name].matrix.copy(), Vector(armature.pose.bones[name].head), Vector(armature.pose.bones[name].tail))
        for name in rotations
    }
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    bpy.context.view_layer.update()
    for name, (matrix, head, tail) in before.items():
        pose_bone = armature.pose.bones[name]
        position_error = (Vector(pose_bone.head) - head).length + (Vector(pose_bone.tail) - tail).length
        rotation_error = pose_bone.matrix.to_quaternion().rotation_difference(matrix.to_quaternion()).angle
        if position_error > 3.0e-4 or rotation_error > 3.0e-3:
            raise AssertionError(
                f"Non-rest Build popped {name}: position={position_error}, rotation={rotation_error}"
            )
    inventory = limb_ik._validate_inventory(armature)
    rig = inventory["rigs"][("ARM", "L")]
    line = armature.pose.bones[rig["line"].name]
    pole = armature.pose.bones[rig["pole"].name]
    joint = armature.pose.bones[rig["chain"][1]]
    line_head_error = (Vector(line.head) - Vector(joint.head)).length
    line_tail_error = (Vector(line.tail) - Vector(pole.head)).length
    if line_head_error > 3.0e-4 or line_tail_error > 3.0e-4:
        raise AssertionError(
            "Non-rest Build placed the dynamic Pole line away from the evaluated joint/Pole: "
            f"head={line_head_error}, tail={line_tail_error}"
        )
    actual_direction = Vector(pole.head) - Vector(joint.head)
    if actual_direction.normalized().dot(evaluated_direction.normalized()) <= 0.999999:
        raise AssertionError("Non-rest Build did not use the explicit evaluated Pole direction")
    assert_pole_plane_alignment(armature, ("ARM", "L"), "non-rest Build")


def test_idempotent_build_and_foreign_name_fail_closed():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    before = ({bone.name for bone in armature.data.bones}, len(limb_ik._owned_constraint_records(armature)))
    if bpy.ops.character_designer.limb_ik_build_arm() != {"CANCELLED"}:
        raise AssertionError("Repeated Build should refuse an exact existing rig")
    after = ({bone.name for bone in armature.data.bones}, len(limb_ik._owned_constraint_records(armature)))
    if before != after:
        raise AssertionError("Repeated Build duplicated or changed rig data")

    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    bpy.ops.object.mode_set(mode="EDIT")
    foreign = add_bone(armature.data.edit_bones, "CTRL_hand_IK.L", (0, 0, 0), (0, 0.1, 0))
    foreign.use_deform = False
    bpy.ops.object.mode_set(mode="POSE")
    # This source change intentionally requires a fresh read-only Analyze.
    analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"CANCELLED"}:
        raise AssertionError("Build overwrote a foreign same-name control")
    if armature.data.bones["CTRL_hand_IK.L"].get(limb_ik.OWNER_KEY):
        raise AssertionError("Foreign same-name control was retagged")


def test_build_fault_rolls_back_without_residue():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    original = limb_ik._create_constraints_and_shapes

    def injected(*_args, **_kwargs):
        raise RuntimeError("injected build failure")

    limb_ik._create_constraints_and_shapes = injected
    try:
        if cancelled_result(bpy.ops.character_designer.limb_ik_build_arm) != {"CANCELLED"}:
            raise AssertionError("Injected Build failure did not cancel")
    finally:
        limb_ik._create_constraints_and_shapes = original
    if owned_bones(armature) or limb_ik.ARMATURE_ID_KEY in armature.data:
        raise AssertionError("Build rollback left generated Armature data")
    if bpy.data.collections.get(limb_ik.WIDGET_COLLECTION_NAME) is not None or armature.data.collections.get(limb_ik.CONTROL_COLLECTION_NAME) is not None:
        raise AssertionError(
            f"Build rollback left generated collections: widgets={bpy.data.collections.get(limb_ik.WIDGET_COLLECTION_NAME)}, controls={armature.data.collections.get(limb_ik.CONTROL_COLLECTION_NAME)}"
        )


def test_remove_refuses_foreign_dependency_then_removes_exact_data():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    source = armature.pose.bones["Chest"]
    foreign = source.constraints.new(type="COPY_LOCATION")
    foreign.name = "Artist uses hand control"
    foreign.target = armature
    foreign.subtarget = "CTRL_hand_IK.L"
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"CANCELLED"}:
        raise AssertionError("Remove accepted a foreign constraint dependency")
    if not owned_bones(armature):
        raise AssertionError("Refused Remove deleted generated controls")
    source.constraints.remove(foreign)
    generated = armature.pose.bones["CTRL_hand_IK.L"]
    foreign_on_control = generated.constraints.new(type="COPY_ROTATION")
    foreign_on_control.name = "Artist constraint on generated control"
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"CANCELLED"}:
        raise AssertionError("Remove accepted a foreign IK/Copy constraint on a generated control")
    generated.constraints.remove(foreign_on_control)
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if owned_bones(armature) or limb_ik._owned_constraint_records(armature):
        raise AssertionError("Remove left generated Armature data")
    if bpy.data.collections.get(limb_ik.WIDGET_COLLECTION_NAME) is not None:
        raise AssertionError("Remove left widget collection")


def test_remove_scans_cross_armature_and_constraint_animation_dependencies():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory = limb_ik._validate_inventory(armature)
    widget = armature.pose.bones["CTRL_hand_IK.L"].custom_shape

    foreign_data = bpy.data.armatures.new("ForeignRigData")
    foreign_rig = bpy.data.objects.new("ForeignRig", foreign_data)
    bpy.context.scene.collection.objects.link(foreign_rig)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = foreign_rig
    foreign_rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    add_bone(foreign_data.edit_bones, "Foreign", (0, 0, 0), (0, 0.2, 0), deform=False)
    bpy.ops.object.mode_set(mode="POSE")
    foreign_pose = foreign_rig.pose.bones["Foreign"]
    dependency = foreign_pose.constraints.new(type="COPY_LOCATION")
    dependency.target = armature
    dependency.subtarget = "CTRL_hand_IK.L"
    foreign_pose.custom_shape = widget
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Remove accepted a cross-Armature control/widget dependency")
    bpy.data.objects.remove(foreign_rig, do_unlink=True)
    bpy.data.armatures.remove(foreign_data)

    source = armature.pose.bones["Chest"]
    source.custom_shape_transform = armature.pose.bones["CTRL_hand_IK.L"]
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Remove accepted a Custom Shape Transform dependency")
    source.custom_shape_transform = None

    rig = inventory["rigs"][("ARM", "L")]
    lower, ik, _record = next(entry for entry in rig["entries"] if entry[2]["role"] == "IK")
    ik.keyframe_insert(data_path="influence", frame=1)
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Remove accepted animation on an owned IK constraint")
    armature.animation_data_clear()
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)


def test_remove_scans_object_constraints_and_driver_constraint_reads():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    foreign = bpy.data.objects.new("ForeignEmpty", None)
    bpy.context.scene.collection.objects.link(foreign)
    object_dependency = foreign.constraints.new(type="COPY_LOCATION")
    object_dependency.name = "Object uses generated hand control"
    object_dependency.target = armature
    object_dependency.subtarget = "CTRL_hand_IK.L"
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Remove accepted an Object constraint dependency")
    foreign.constraints.remove(object_dependency)

    foreign.parent = armature
    foreign.parent_type = "BONE"
    foreign.parent_bone = "CTRL_hand_IK.L"
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Remove accepted an Object bone-parent dependency")
    foreign.parent = None

    inventory = limb_ik._validate_inventory(armature)
    lower, ik, _record = next(
        entry for entry in inventory["rigs"][("ARM", "L")]["entries"] if entry[2]["role"] == "IK"
    )
    foreign["probe"] = 0.0
    driver = foreign.driver_add('["probe"]')
    variable = driver.driver.variables.new()
    variable.name = "owned_ik"
    variable.type = "SINGLE_PROP"
    variable.targets[0].id = armature
    variable.targets[0].data_path = limb_ik._owned_constraint_path(lower.name, ik.name) + ".influence"
    driver.driver.expression = variable.name
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Remove accepted a driver read from an owned IK constraint")
    bpy.data.objects.remove(foreign, do_unlink=True)

    raw_registry = lower.get(limb_ik.CONSTRAINT_REGISTRY_KEY)
    registry = json.loads(raw_registry)
    registry[ik.name]["chain"] = ["forearm.L"]
    lower[limb_ik.CONSTRAINT_REGISTRY_KEY] = json.dumps(registry)
    if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
        raise AssertionError("Remove accepted a malformed owned registry chain")
    if not owned_bones(armature):
        raise AssertionError("Malformed-registry refusal deleted generated controls")
    lower[limb_ik.CONSTRAINT_REGISTRY_KEY] = raw_registry
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)


def test_enhanced_widgets_and_dynamic_pole_contract():
    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    chain = limb_ik.LimbChain("ARM", "L", "upper_arm.L", "forearm.L", "hand.L")
    expected_plan = limb_ik._build_plan(
        armature,
        chain,
        settings.pole_distance_ratio,
        Vector(settings.left_arm_pole_direction),
    )
    planned_pole_axis = expected_plan.pole_tail - expected_plan.pole_head
    if planned_pole_axis.length <= limb_ik.EPSILON:
        raise AssertionError("Planned Pole control bone has no usable display length")
    if planned_pole_axis.normalized().dot(expected_plan.pole_direction.normalized()) <= 0.999999:
        raise AssertionError(
            f"Planned Pole tail does not point along the configured bend direction: {tuple(planned_pole_axis)}"
        )
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory = limb_ik._validate_inventory(armature)
    if inventory["schema"] != limb_ik.CURRENT_SCHEMA or inventory["master"].name != limb_ik.MASTER_NAME:
        raise AssertionError("Current Master schema was not generated")
    expected_counts = {
        "HAND": 32,
        "MASTER": 24,
        "POLE_ARROW": 5,
        "POLE_LINE": 2,
        "SHOULDER": 44,
    }
    widgets = {
        obj.get(limb_ik.KIND_KEY): obj
        for obj in bpy.data.objects
        if limb_ik._owned(obj, inventory["armature_id"], role="WIDGET")
    }
    if {kind: len(widgets[kind].data.vertices) for kind in expected_counts} != expected_counts:
        raise AssertionError("Rain-inspired Arm/Master/Pole widget topology changed")
    master_data = inventory["master"]
    master_pose = armature.pose.bones[limb_ik.MASTER_NAME]
    if Vector(master_data.head_local).length > 1.0e-8:
        raise AssertionError(f"Master head is not at Armature-local zero: {tuple(master_data.head_local)}")
    shape_rotation = master_pose.custom_shape_rotation_euler.to_matrix()
    master_basis = master_data.matrix_local.to_3x3()
    ground_heights = [
        abs((Vector(master_data.head_local) + master_basis @ (shape_rotation @ vertex.co)).z)
        for vertex in widgets["MASTER"].data.vertices
    ]
    if max(ground_heights, default=0.0) > 1.0e-7:
        raise AssertionError(f"Master widget is not horizontal at Armature-local ground: {max(ground_heights)}")
    finger_vertices, finger_edges = limb_ik._widget_geometry("FINGER")
    if len(finger_vertices) != 32 or len(finger_edges) != 32 or "FINGER" in widgets:
        raise AssertionError("A rig with no Finger source bones unexpectedly created the Finger widget")
    source_widgets = inventory.get("source_widgets")
    if source_widgets is None or set(source_widgets["bones"]) != {"shoulder.L"}:
        raise AssertionError("The existing Shoulder source bone did not receive the arched widget")
    if any("finger" in bone.name.lower() for bone in owned_bones(armature)):
        raise AssertionError("The Limb build generated Finger controls prematurely")

    rig = inventory["rigs"][("ARM", "L")]
    pole = armature.pose.bones[rig["pole"].name]
    line = armature.pose.bones[rig["line"].name]
    display = armature.pose.bones[rig["display"].name]
    pole_data = rig["pole"]
    line_data = rig["line"]
    built_pole_axis = Vector(pole_data.tail_local) - Vector(pole_data.head_local)
    saved_direction = Vector(pole_data[limb_ik.POLE_DIRECTION_KEY]).normalized()
    if built_pole_axis.length <= limb_ik.EPSILON or built_pole_axis.normalized().dot(saved_direction) <= 0.999999:
        raise AssertionError(
            f"Built Pole tail does not point along its saved bend direction: {tuple(built_pole_axis)}"
        )
    if (Vector(pole_data.tail_local) - expected_plan.pole_tail).length > 2.0e-6:
        raise AssertionError(
            f"Built Pole tail differs from its plan: "
            f"built={tuple(pole_data.tail_local)}, planned={tuple(expected_plan.pole_tail)}"
        )
    if pole_data.parent is None or pole_data.parent.name != limb_ik.MASTER_NAME or pole_data.use_connect:
        raise AssertionError("Pole control is not a Keep Offset child of the Master")
    if line_data.parent is None or line_data.parent.name != rig["chain"][0] or line_data.use_connect:
        raise AssertionError("Pole line helper is not a Keep Offset child of the upper limb")
    joint_rest = Vector(armature.data.bones[rig["chain"][1]].head_local)
    pole_rest = Vector(pole_data.head_local)
    line_rest_axis = Vector(line_data.tail_local) - Vector(line_data.head_local)
    full_joint_to_pole = pole_rest - joint_rest
    if (Vector(line_data.head_local) - joint_rest).length > 2.0e-6:
        raise AssertionError("Pole line helper does not start at the Rest joint")
    if line_rest_axis.length <= limb_ik.EPSILON or line_rest_axis.length >= full_joint_to_pole.length:
        raise AssertionError(
            f"Pole line Rest bone is not shorter than joint-to-Pole: "
            f"helper={line_rest_axis.length}, full={full_joint_to_pole.length}"
        )
    if line_rest_axis.normalized().dot(full_joint_to_pole.normalized()) <= 0.999999:
        raise AssertionError("Short Pole line Rest bone does not aim toward the Pole")
    bpy.context.view_layer.update()
    if (Vector(line.tail) - Vector(pole.head)).length > 2.0e-4:
        raise AssertionError("Pole line STRETCH_TO did not extend the short Rest helper to the Pole")
    if not all(pole.lock_rotation) or not all(pole.lock_scale) or rig["line"].hide_select is not True or rig["display"].hide is not True:
        raise AssertionError("Pole/helper visibility or channel locks are wrong")
    rest_aim = Vector(armature.data.bones[rig["chain"][1]].head_local) - Vector(rig["display"].head_local)
    rest_negative_y = -Vector(rig["display"].matrix_local.to_3x3().col[1])
    rest_alignment = rest_negative_y.normalized().dot(rest_aim.normalized())
    if rest_alignment <= 0.999999:
        raise AssertionError(f"Pole arrow rest -Y does not aim at the joint: {rest_alignment}")
    pole.location += Vector((0.0, 0.11, 0.07))
    bpy.context.view_layer.update()
    line_error = (Vector(line.tail) - Vector(pole.head)).length
    aim = Vector(armature.pose.bones[rig["chain"][1]].head) - Vector(display.head)
    display_negative_y = -Vector(display.matrix.to_3x3().col[1])
    aim_error = 1.0 - display_negative_y.normalized().dot(aim.normalized())
    if line_error > 2.0e-4 or aim_error > 2.0e-4:
        raise AssertionError(f"Dynamic Pole visuals did not follow: line={line_error}, aim={aim_error}")


def test_direct_pole_guide_segments_and_dynamic_arrow_display():
    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    settings.build_method = "DIRECT_PREROLL"
    if bpy.ops.character_designer.limb_ik_build_all() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory = limb_ik._validate_inventory(armature)
    if (
        inventory["schema"] != limb_ik.DIRECT_PREROLL_SCHEMA
        or len(inventory["bones"]) != 6
        or len(inventory["records"]) != 8
    ):
        raise AssertionError("Two Direct limbs did not build the schema-5 three-bone contract")
    bone_count = len(armature.data.bones)
    constraint_count = len(inventory["records"])

    # The persistent Pole custom shape is the one and only arrowhead.  Its
    # apex is at the displayed Pole origin; its base lies on local -Y and is
    # perpendicular to local Y.  A hidden display helper keeps that -Y axis
    # aimed back at the bend joint, while the GPU guide contributes only the
    # permanently visible straight shaft--never a second arrowhead.
    arrow_vertices, arrow_edges = limb_ik._widget_geometry("POLE_ARROW")
    if len(arrow_vertices) != 5 or len(arrow_edges) != 8:
        raise AssertionError("The single Pole arrowhead topology changed")
    arrow_points = tuple(Vector(value) for value in arrow_vertices)
    base_center = sum(arrow_points[:4], Vector((0.0, 0.0, 0.0))) / 4.0
    tip_to_base = base_center - arrow_points[4]
    if tip_to_base.length <= limb_ik.EPSILON or tip_to_base.normalized().dot(Vector((0.0, -1.0, 0.0))) <= 0.999999:
        raise AssertionError("Pole arrowhead base is not behind its tip on local -Y")
    for start, end in ((0, 1), (1, 2), (2, 3), (3, 0)):
        edge = arrow_points[end] - arrow_points[start]
        if edge.length <= limb_ik.EPSILON or abs(edge.normalized().dot(Vector((0.0, 1.0, 0.0)))) > 1.0e-6:
            raise AssertionError("Pole arrowhead base is not perpendicular to the Pole axis")
    tamper_track = None
    for rig in inventory["rigs"].values():
        lower = armature.pose.bones[rig["chain"][1]]
        pole = armature.pose.bones[rig["pole"].name]
        display = armature.pose.bones[rig["display"].name]
        if pole.custom_shape is None or pole.custom_shape.get(limb_ik.KIND_KEY) != "POLE_ARROW":
            raise AssertionError("A Direct Pole does not use the existing Pole arrowhead")
        if (
            pole.custom_shape_transform is None
            or pole.custom_shape_transform.name != display.name
        ):
            raise AssertionError("A Direct Pole does not use its dynamic display frame")
        if (
            display.bone.parent is None
            or display.bone.parent.name != pole.name
            or display.bone.use_connect
            or display.bone.use_deform
            or not display.bone.hide
            or not display.bone.hide_select
        ):
            raise AssertionError("The Direct Pole display helper is not hidden and display-only")
        display_track = next(
            (
                constraint
                for owner, constraint, record in rig["entries"]
                if owner.name == display.name and record["role"] == "POLE_DISPLAY_TRACK"
            ),
            None,
        )
        if (
            display_track is None
            or display_track.type != "DAMPED_TRACK"
            or display_track.target is not armature
            or display_track.subtarget != lower.name
            or abs(float(display_track.head_tail)) > 1.0e-7
            or display_track.track_axis != "TRACK_NEGATIVE_Y"
        ):
            raise AssertionError("The Direct Pole display helper has no exact joint-tracking constraint")
        if tamper_track is None:
            tamper_track = display_track
        joint_world = armature.matrix_world @ Vector(lower.head)
        pole_world = limb_ik._custom_shape_anchor_world(armature, pole)
        negative_y_world = armature.matrix_world.to_3x3() @ (
            -Vector(display.matrix.to_3x3().col[1])
        )
        arrow_to_joint = joint_world - pole_world
        if (
            arrow_to_joint.length <= limb_ik.EPSILON
            or negative_y_world.length <= limb_ik.EPSILON
            or negative_y_world.normalized().dot(arrow_to_joint.normalized()) <= 0.9999
        ):
            raise AssertionError("The arrowhead base plane is not perpendicular to its live shaft")

    # Head/tail=0 is what aims at the elbow/knee joint (the lower bone head).
    # A value of 1 silently aims at the wrist/ankle instead, so inventory must
    # fail closed rather than accepting a visually plausible but wrong arrow.
    if tamper_track is None:
        raise AssertionError("No schema-5 Pole display track was available for tamper coverage")
    original_head_tail = float(tamper_track.head_tail)
    tamper_track.head_tail = 1.0
    try:
        try:
            limb_ik._validate_inventory(armature)
        except limb_ik.LimbIKError:
            pass
        else:
            raise AssertionError("Inventory accepted Pole-display Damped Track head_tail=1")
    finally:
        tamper_track.head_tail = original_head_tail
    limb_ik._validate_inventory(armature)

    pole_names = [rig["pole"].name for rig in inventory["rigs"].values()]
    for pose_bone in armature.pose.bones:
        pose_bone.select = False
    segments = limb_ik._direct_pole_guide_segments(bpy.context)
    if len(segments) != 2:
        raise AssertionError(
            f"Unselected Direct Poles did not keep both straight shafts visible: {len(segments)}"
        )
    theme_wire = limb_ik._theme_rgba(
        bpy.context.preferences.themes[0].view_3d.wire,
        (0.0, 0.0, 0.0, 1.0),
    )
    if any(
        len(segment) != 3
        or max(abs(float(actual) - float(expected)) for actual, expected in zip(segment[2], theme_wire))
        > 2.0e-6
        for segment in segments
    ):
        raise AssertionError("Unselected Direct Pole shafts did not use the theme wire color")

    # A hidden Pole must not leak its shaft, but restoring visibility must not
    # depend on selection state.
    hidden_pole = armature.data.bones[pole_names[0]]
    hidden_pole.hide = True
    if len(limb_ik._direct_pole_guide_segments(bpy.context)) != 1:
        raise AssertionError("A hidden Direct Pole still exposed its viewport shaft")
    hidden_pole.hide = False
    if len(limb_ik._direct_pole_guide_segments(bpy.context)) != 2:
        raise AssertionError("Restoring a Direct Pole did not restore its unselected shaft")

    # A different visible rig can retain Bone selection while it is no longer
    # in Pose Mode.  That stale state must not duplicate the live Pose rig's
    # permanently visible guides.
    stale = armature.copy()
    stale.data = armature.data.copy()
    stale.name = "StaleObjectModePoleRig"
    bpy.context.scene.collection.objects.link(stale)
    stale.matrix_world.translation.x += 3.0
    for name in pole_names:
        stale.pose.bones[name].select = True
    if stale.mode != "OBJECT":
        raise AssertionError("Stale Pole fixture unexpectedly entered Pose Mode")
    if len(limb_ik._direct_pole_guide_segments(bpy.context)) != 2:
        raise AssertionError("An Object Mode rig's stale Pole selection displayed a guide")
    stale_data = stale.data
    for pose_bone in stale.pose.bones:
        pose_bone.custom_shape = None
        pose_bone.custom_shape_transform = None
    bpy.data.objects.remove(stale, do_unlink=True)
    bpy.data.armatures.remove(stale_data)

    expected = []
    for rig in inventory["rigs"].values():
        pole = armature.pose.bones[rig["pole"].name]
        expected.append(
            (
                armature.matrix_world @ armature.pose.bones[rig["chain"][1]].head,
                limb_ik._custom_shape_anchor_world(armature, pole),
            )
        )
    unmatched = list(segments)
    for expected_joint, expected_pole in expected:
        match_index = next(
            (
                index
                for index, segment in enumerate(unmatched)
                for joint, pole in (segment[:2],)
                if (joint - expected_joint).length < 1.0e-7 and (pole - expected_pole).length < 1.0e-7
            ),
            None,
        )
        if match_index is None:
            raise AssertionError("A Direct Pole guide does not join the solved joint to its Pole head")
        unmatched.pop(match_index)

    vertices = limb_ik._pole_guide_line_vertices(segments)
    if len(vertices) != 2 * len(segments):
        raise AssertionError(f"Pole shaft batch did not emit two vertices per guide: {len(vertices)}")
    for index, segment in enumerate(segments):
        joint, pole = segment[:2]
        emitted_joint, emitted_pole = vertices[2 * index:2 * index + 2]
        if (
            (emitted_joint - joint).length > 1.0e-7
            or (emitted_pole - pole).length > 1.0e-7
            or (emitted_pole - emitted_joint).length <= limb_ik.EPSILON
            or any(not math.isfinite(component) for point in (emitted_joint, emitted_pole) for component in point)
        ):
            raise AssertionError("GPU Pole guide is not exactly the joint-to-arrowhead shaft")

    # Selection of another control must not suppress the always-on shafts.
    target_name = next(iter(inventory["rigs"].values()))["target"].name
    armature.pose.bones[target_name].select = True
    armature.data.bones.active = armature.data.bones[target_name]
    if len(limb_ik._direct_pole_guide_segments(bpy.context)) != 2:
        raise AssertionError("Selecting a Target suppressed the persistent Direct Pole shafts")
    armature.pose.bones[target_name].select = False

    arm_rig = inventory["rigs"][("ARM", "L")]
    pole = armature.pose.bones[arm_rig["pole"].name]
    display = armature.pose.bones[arm_rig["display"].name]
    lower = armature.pose.bones[arm_rig["chain"][1]]
    old_display_axis = -Vector(display.matrix.to_3x3().col[1]).normalized()
    old_pole_points = tuple(segment[1].copy() for segment in segments)
    pole.location += Vector((0.07, -0.05, 0.03))
    bpy.context.view_layer.update()
    moved_segments = limb_ik._direct_pole_guide_segments(bpy.context)
    if len(moved_segments) != 2 or all(
        min((point - old).length for old in old_pole_points) < 1.0e-6
        for _joint, point, _color in moved_segments
    ):
        raise AssertionError("Direct Pole guide endpoints did not follow the moved Pole")
    live_arrow_to_joint = Vector(lower.head) - Vector(display.head)
    live_display_axis = -Vector(display.matrix.to_3x3().col[1])
    if (
        live_arrow_to_joint.length <= limb_ik.EPSILON
        or live_display_axis.length <= limb_ik.EPSILON
        or live_display_axis.normalized().dot(live_arrow_to_joint.normalized()) <= 0.9999
        or live_display_axis.normalized().dot(old_display_axis) > 0.999999
    ):
        raise AssertionError("Moving the Pole did not dynamically re-aim its arrowhead display frame")
    current_joint_world = armature.matrix_world @ Vector(lower.head)
    current_tip_world = limb_ik._custom_shape_anchor_world(armature, pole)
    if not any(
        (joint - current_joint_world).length < 1.0e-7
        and (tip - current_tip_world).length < 1.0e-7
        for joint, tip, _color in moved_segments
    ):
        raise AssertionError("The persistent shaft did not remain joined to the dynamic arrowhead")

    visual_offset = Vector((0.03, -0.02, 0.04))
    pole.custom_shape_translation = visual_offset
    visual_segments = limb_ik._direct_pole_guide_segments(bpy.context)
    expected_visual_tip = armature.matrix_world @ (display.matrix @ visual_offset)
    matching_visual_tips = [
        tip
        for _joint, tip, _color in visual_segments
        if (tip - expected_visual_tip).length <= 1.0e-7
    ]
    if len(visual_segments) != 2 or len(matching_visual_tips) != 1:
        raise AssertionError("Direct Pole guide did not stay unified with its display-only visual offset")
    if len(armature.data.bones) != bone_count or len(limb_ik._validate_inventory(armature)["records"]) != constraint_count:
        raise AssertionError("Read-only Pole guides added a bone or constraint")

    bpy.ops.object.mode_set(mode="OBJECT")
    if limb_ik._direct_pole_guide_segments(bpy.context):
        raise AssertionError("Stale Pole selection displayed guides outside Pose Mode")
    bpy.ops.object.mode_set(mode="POSE")

    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if limb_ik._direct_pole_guide_segments(bpy.context):
        raise AssertionError("Pole guides remained after Remove")

    _result, settings = analyze(armature)
    settings.build_method = "ROLL_DECOUPLED"
    if bpy.ops.character_designer.limb_ik_build_all() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if limb_ik._direct_pole_guide_segments(bpy.context):
        raise AssertionError("Stable rigs duplicated their bone-based Pole lines with viewport guides")


def test_auto_align_target_all_constraint_spaces_and_rollback():
    cases = (
        ("DIRECT_PREROLL", "LEFT_ARM"),
        ("DIRECT_PREROLL", "LEFT_LEG"),
        ("ROLL_DECOUPLED", "LEFT_ARM"),
        ("ROLL_DECOUPLED", "LEFT_LEG"),
    )
    for build_method, selected_limb in cases:
        reset_scene()
        armature = make_humanoid(include_right=False)
        _result, settings = analyze(armature)
        settings.build_method = build_method
        settings.selected_limb = selected_limb
        if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
            raise AssertionError(settings.last_message)
        kind, side = limb_ik.SELECTED_LIMBS[selected_limb]
        inventory = limb_ik._validate_inventory(armature)
        rig = inventory["rigs"][(kind, side)]
        target = armature.pose.bones[rig["target"].name]
        solver = armature.pose.bones[rig["solver_target"].name]
        end = armature.pose.bones[rig["chain"][2]]
        end_rotation = next(
            constraint
            for _owner, constraint, record in rig["entries"]
            if record["role"] == "END_ROTATION"
        )
        auto_offset_rotation = next(
            constraint
            for _owner, constraint, record in rig["entries"]
            if record["role"] == "AUTO_OFFSET_ROTATION"
        )

        if build_method == "ROLL_DECOUPLED":
            master = armature.pose.bones[limb_ik.MASTER_NAME]
            master.rotation_mode = "XYZ"
            master.location = (0.08, -0.04, 0.03)
            master.rotation_euler = (0.06, -0.04, 0.11)
        target.rotation_mode = "XYZ"
        target.location += Vector((0.035, -0.02, 0.025))
        target.rotation_euler = (0.31, -0.23, 0.38)
        if kind == "LEG" and rig["heel"] is not None:
            heel = armature.pose.bones[rig["heel"].name]
            heel.rotation_mode = "XYZ"
            heel.rotation_euler = (0.24, 0.09, 0.0)
        bpy.context.view_layer.update()

        solver_position_before = (armature.matrix_world @ solver.matrix).to_translation()
        bone_count = len(armature.data.bones)
        constraint_count = len(inventory["records"])
        if inventory["target_rotation_version"] != limb_ik.TARGET_ROTATION_VERSION:
            raise AssertionError(f"{build_method} {selected_limb}: Target rotation feature marker is missing")
        if target.rotation_mode != "XYZ":
            raise AssertionError(f"{build_method} {selected_limb}: Target did not use Local XYZ channels")
        if (
            inventory["rigs"][(kind, side)]["auto_align"]
            or end_rotation.mute
            or not auto_offset_rotation.mute
        ):
            raise AssertionError(f"{build_method} {selected_limb}: new rig did not start in Manual mode")
        if bpy.ops.character_designer.limb_ik_auto_align_target(action="ENABLE") != {"FINISHED"}:
            raise AssertionError(f"{build_method} {selected_limb}: enabling Auto failed: {settings.last_message}")
        inventory_after = limb_ik._validate_inventory(armature)
        solver_position_after = (armature.matrix_world @ solver.matrix).to_translation()
        if (solver_position_after - solver_position_before).length > 2.0e-4:
            raise AssertionError(f"{build_method} {selected_limb}: enabling Auto moved the IK solver point")
        if (
            not inventory_after["rigs"][(kind, side)]["auto_align"]
            or not end_rotation.mute
            or auto_offset_rotation.mute
        ):
            raise AssertionError(f"{build_method} {selected_limb}: Auto state and rotation constraints did not agree")
        if len(armature.data.bones) != bone_count or len(inventory_after["records"]) != constraint_count:
            raise AssertionError(f"{build_method} {selected_limb}: Auto Align added rig data")

        # Auto uses the live solved chain as its base, but the same visible
        # Target retains three local animator rotation offsets.  Every channel
        # must rotate the wrist/ankle without moving the IK point.
        for axis in range(3):
            end_before_axis = end.matrix.copy()
            solver_before_axis = (armature.matrix_world @ solver.matrix).to_translation()
            target.rotation_euler[axis] += 0.14
            bpy.context.view_layer.update()
            if limb_ik._rotation_error(end.matrix, end_before_axis) < 0.03:
                raise AssertionError(
                    f"{build_method} {selected_limb}: Auto mode ignored Target local axis {axis}"
                )
            # Stable legs deliberately put the ankle solver under the visible
            # sole/heel pivot, so rotating that foot control can move the ankle
            # around its pivot.  Every other target rotates about the IK point.
            if (
                not (build_method == "ROLL_DECOUPLED" and kind == "LEG")
                and (
                    (armature.matrix_world @ solver.matrix).to_translation()
                    - solver_before_axis
                ).length
                > 2.0e-4
            ):
                raise AssertionError(
                    f"{build_method} {selected_limb}: Target rotation axis {axis} moved the IK point"
                )

        solver_before_manual = (armature.matrix_world @ solver.matrix).to_translation()
        end_before_manual = armature.matrix_world @ end.matrix.copy()
        if bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") != {"FINISHED"}:
            raise AssertionError(f"{build_method} {selected_limb}: disabling Auto failed: {settings.last_message}")
        inventory_manual = limb_ik._validate_inventory(armature)
        if (
            inventory_manual["rigs"][(kind, side)]["auto_align"]
            or end_rotation.mute
            or not auto_offset_rotation.mute
        ):
            raise AssertionError(f"{build_method} {selected_limb}: Manual mode did not restore its rotation constraints")
        if (
            (armature.matrix_world @ solver.matrix).to_translation() - solver_before_manual
        ).length > 2.0e-4:
            raise AssertionError(f"{build_method} {selected_limb}: Auto-to-Manual moved the solver point")
        if limb_ik._rotation_error(armature.matrix_world @ end.matrix, end_before_manual) > 3.0e-3:
            raise AssertionError(f"{build_method} {selected_limb}: Auto-to-Manual popped the end orientation")
        manual_end = end.matrix.copy()
        target.rotation_euler.rotate_axis("Z", 0.14)
        bpy.context.view_layer.update()
        if limb_ik._rotation_error(end.matrix, manual_end) < 0.03:
            raise AssertionError(f"{build_method} {selected_limb}: Target rotation did not resume in Manual mode")

    # Direct rigs authored before 0.29.7 accepted WORLD/WORLD end rotation.
    # The mode switch must branch from the actual constraint spaces, not merely
    # schema + limb kind, so the compatibility relation remains safely usable.
    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig = limb_ik._validate_inventory(armature)["rigs"][("ARM", "L")]
    target = armature.pose.bones[rig["target"].name]
    solver = armature.pose.bones[rig["solver_target"].name]
    end = armature.pose.bones[rig["chain"][2]]
    end_rotation = next(
        constraint
        for _owner, constraint, record in rig["entries"]
        if record["role"] == "END_ROTATION"
    )
    auto_offset_rotation = next(
        constraint
        for _owner, constraint, record in rig["entries"]
        if record["role"] == "AUTO_OFFSET_ROTATION"
    )
    end_rotation.target_space = "WORLD"
    end_rotation.owner_space = "WORLD"
    target.rotation_mode = "XYZ"
    target.location += Vector((0.025, -0.015, 0.02))
    target.rotation_euler = (0.27, -0.19, 0.34)
    bpy.context.view_layer.update()
    solver_position_before = (armature.matrix_world @ solver.matrix).to_translation()
    if bpy.ops.character_designer.limb_ik_auto_align_target(action="ENABLE") != {"FINISHED"}:
        raise AssertionError(f"legacy Direct WORLD/WORLD enable: {settings.last_message}")
    desired_end_world = armature.matrix_world @ end.matrix.copy()
    if bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") != {"FINISHED"}:
        raise AssertionError(f"legacy Direct WORLD/WORLD disable: {settings.last_message}")
    if (
        (armature.matrix_world @ solver.matrix).to_translation() - solver_position_before
    ).length > 2.0e-4:
        raise AssertionError("legacy Direct WORLD/WORLD Auto Align moved the solver point")
    if limb_ik._rotation_error(armature.matrix_world @ end.matrix, desired_end_world) > 3.0e-3:
        raise AssertionError("legacy Direct WORLD/WORLD Auto Align used the wrong space branch")

    # A failure while leaving Auto must restore the Target, metadata, and mute
    # state exactly.
    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig = limb_ik._validate_inventory(armature)["rigs"][("ARM", "L")]
    target = armature.pose.bones[rig["target"].name]
    end_rotation = next(
        constraint
        for _owner, constraint, record in rig["entries"]
        if record["role"] == "END_ROTATION"
    )
    auto_offset_rotation = next(
        constraint
        for _owner, constraint, record in rig["entries"]
        if record["role"] == "AUTO_OFFSET_ROTATION"
    )
    target.rotation_mode = "XYZ"
    target.rotation_euler = (0.2, -0.1, 0.3)
    bpy.context.view_layer.update()
    if bpy.ops.character_designer.limb_ik_auto_align_target(action="ENABLE") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    before = target.matrix_basis.copy()
    original_solver = limb_ik._solve_auto_align_target

    def fail_align(*_args, **_kwargs):
        raise RuntimeError("injected Auto Align failure")

    limb_ik._solve_auto_align_target = fail_align
    try:
        if cancelled_result(
            lambda: bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE")
        ) != {"CANCELLED"}:
            raise AssertionError("Injected Auto Align failure did not cancel")
    finally:
        limb_ik._solve_auto_align_target = original_solver
    assert_matrix_close(target.matrix_basis, before, "Auto Align rollback target basis")
    if (
        not end_rotation.mute
        or auto_offset_rotation.mute
        or target.bone.get(limb_ik.AUTO_ALIGN_KEY) is not True
    ):
        raise AssertionError("Auto Align rollback did not restore enabled state")

    # Also force a failure only after the Target has been written and the
    # constraint has been re-enabled.  This guards the actual recovery path,
    # not just the pre-write solver exception above.
    def invalid_align(*_args, **_kwargs):
        return "BASIS", Matrix.Translation((3.0, -2.0, 1.0)) @ before

    limb_ik._solve_auto_align_target = invalid_align
    try:
        if cancelled_result(
            lambda: bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE")
        ) != {"CANCELLED"}:
            raise AssertionError("Post-write Auto Align validation failure did not cancel")
    finally:
        limb_ik._solve_auto_align_target = original_solver
    assert_matrix_close(target.matrix_basis, before, "post-write Auto Align rollback target basis")
    if (
        not end_rotation.mute
        or auto_offset_rotation.mute
        or target.bone.get(limb_ik.AUTO_ALIGN_KEY) is not True
    ):
        raise AssertionError("Post-write Auto Align rollback did not restore enabled state")


def test_auto_align_target_refuses_locked_animated_or_driven_state():
    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    settings.selected_limb = "LEFT_ARM"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig = limb_ik._validate_inventory(armature)["rigs"][("ARM", "L")]
    target = armature.pose.bones[rig["target"].name]
    end_rotation = next(
        constraint
        for _owner, constraint, record in rig["entries"]
        if record["role"] == "END_ROTATION"
    )

    target.rotation_mode = "XYZ"
    target_driver = target.driver_add("rotation_euler", 0)
    target_driver.driver.expression = "0.0"
    if bpy.ops.character_designer.limb_ik_auto_align_target(action="ENABLE") != {"FINISHED"}:
        raise AssertionError("Enabling Auto unnecessarily rejected a driven Target")
    if cancelled_result(
        lambda: bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE")
    ) != {"CANCELLED"}:
        raise AssertionError("Disabling Auto overwrote a driven Target")
    target.driver_remove("rotation_euler", 0)
    if bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    target.keyframe_insert(data_path="rotation_euler", frame=1.0)
    if bpy.ops.character_designer.limb_ik_auto_align_target(action="ENABLE") != {"FINISHED"}:
        raise AssertionError("Enabling Auto unnecessarily rejected a keyed Target")
    if cancelled_result(
        lambda: bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE")
    ) != {"CANCELLED"}:
        raise AssertionError("Disabling Auto overwrote a keyed Target")
    animation = armature.animation_data
    if animation is not None:
        animation.action = None
    if bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE") != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    constraint_driver = end_rotation.driver_add("mute")
    constraint_driver.driver.expression = "0.0"
    bpy.context.view_layer.update()
    if cancelled_result(
        lambda: bpy.ops.character_designer.limb_ik_auto_align_target(action="ENABLE")
    ) != {"CANCELLED"}:
        raise AssertionError("Auto Align overrode a driven END_ROTATION constraint")
    end_rotation.driver_remove("mute")

    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    settings.build_method = "ROLL_DECOUPLED"
    settings.selected_limb = "LEFT_LEG"
    if bpy.ops.character_designer.limb_ik_build_selected() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig = limb_ik._validate_inventory(armature)["rigs"][("LEG", "L")]
    target = armature.pose.bones[rig["target"].name]
    if bpy.ops.character_designer.limb_ik_auto_align_target(action="ENABLE") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    target.lock_location = (True, False, False)
    if cancelled_result(
        lambda: bpy.ops.character_designer.limb_ik_auto_align_target(action="DISABLE")
    ) != {"CANCELLED"}:
        raise AssertionError("Auto Align ignored a locked Stable Foot Target location")


def test_master_incremental_build_and_global_transport_no_double_transform():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    master = armature.pose.bones[limb_ik.MASTER_NAME]
    master.rotation_mode = "XYZ"
    master.location = (0.27, -0.16, 0.11)
    master.rotation_euler = (0.09, -0.06, 0.17)
    master.scale = (1.04, 1.04, 1.04)
    bpy.context.view_layer.update()
    master_basis = master.matrix_basis.copy()
    new_leg_plane_bones = {"thigh.L", "shin.L", "thigh.R", "shin.R"}
    tracked = tuple(
        bone.name
        for bone in armature.data.bones
        if bone.get(limb_ik.OWNER_KEY) != limb_ik.OWNER_VALUE and bone.name not in new_leg_plane_bones
    ) + (
        "CTRL_hand_IK.L", "CTRL_hand_IK.R", "CTRL_elbow_pole.L", "CTRL_elbow_pole.R",
    )
    before = {name: armature.pose.bones[name].matrix.copy() for name in tracked}
    if bpy.ops.character_designer.limb_ik_build_leg() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    assert_matrix_close(armature.pose.bones[limb_ik.MASTER_NAME].matrix_basis, master_basis, "incremental Build restored Master basis")
    for name, matrix in before.items():
        assert_matrix_close(armature.pose.bones[name].matrix, matrix, f"incremental Build doubled '{name}'")

    inventory = limb_ik._validate_inventory(armature)
    if len([bone for bone in inventory["bones"] if bone.name == limb_ik.MASTER_NAME]) != 1:
        raise AssertionError("Incremental Build duplicated Master")
    followed = ("Hips", "CTRL_hand_IK.L", "CTRL_elbow_pole.L", "CTRL_foot_IK.L", "CTRL_heel_roll.L")
    old_master = master.matrix.copy()
    old_matrices = {name: armature.pose.bones[name].matrix.copy() for name in followed}
    old_relatives = {name: old_master.inverted() @ matrix for name, matrix in old_matrices.items()}
    master.location += Vector((0.13, 0.08, -0.04))
    master.rotation_euler.rotate_axis("Z", 0.12)
    master.scale = tuple(value * 1.02 for value in master.scale)
    bpy.context.view_layer.update()
    new_master = master.matrix.copy()
    for name in followed:
        relative = new_master.inverted() @ armature.pose.bones[name].matrix
        assert_matrix_close(relative, old_relatives[name], f"Master transport relation for '{name}'", location=8.0e-4, rotation=6.0e-3, scale=1.5e-3)

    rebuild_tracked = tuple(bone.name for bone in armature.data.bones if bone.get(limb_ik.OWNER_KEY) != limb_ik.OWNER_VALUE) + followed[1:]
    rebuild_before = {name: armature.pose.bones[name].matrix.copy() for name in rebuild_tracked}
    rebuild_master_basis = master.matrix_basis.copy()
    if bpy.ops.character_designer.limb_ik_rebuild() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    assert_matrix_close(armature.pose.bones[limb_ik.MASTER_NAME].matrix_basis, rebuild_master_basis, "moved-Master Rebuild restored basis")
    for name, matrix in rebuild_before.items():
        assert_matrix_close(armature.pose.bones[name].matrix, matrix, f"moved-Master Rebuild changed '{name}'", location=1.0e-3, rotation=7.0e-3, scale=2.0e-3)


def test_master_scale_guards_and_nonuniform_remove():
    for scale in ((float("nan"), 1.0, 1.0), (float("inf"), 1.0, 1.0)):
        try:
            limb_ik._validate_master_build_scale(scale)
        except limb_ik.LimbIKError:
            pass
        else:
            raise AssertionError(f"Master scale validation accepted non-finite values: {scale}")

    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    master = armature.pose.bones[limb_ik.MASTER_NAME]
    for scale in ((1.0, 1.2, 1.0), (0.0, 0.0, 0.0), (-1.0, -1.0, -1.0)):
        master.scale = scale
        if cancelled_result(bpy.ops.character_designer.limb_ik_build_leg) != {"CANCELLED"}:
            raise AssertionError(f"Incremental Build accepted invalid Master scale {scale}")
        inventory = limb_ik._validate_inventory(armature)
        if set(inventory["rigs"]) != {("ARM", "L"), ("ARM", "R")}:
            raise AssertionError("Rejected invalid-scale Build changed the existing rig")
        master.scale = (1.0, 1.0, 1.0)
        bpy.context.view_layer.update()

    master.scale = (-0.8, -0.8, -0.8)
    if cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) != {"CANCELLED"}:
        raise AssertionError("Rebuild accepted negative uniform Master scale")
    if set(limb_ik._validate_inventory(armature)["rigs"]) != {("ARM", "L"), ("ARM", "R")}:
        raise AssertionError("Rejected invalid-scale Rebuild changed the existing rig")

    master.scale = (1.0, 1.35, 0.75)
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(f"Remove was incorrectly blocked by nonuniform Master scale: {settings.last_message}")
    if owned_bones(armature) or limb_ik.ARMATURE_ID_KEY in armature.data or limb_ik.SCHEMA_KEY in armature.data:
        raise AssertionError("Nonuniform-Master Remove left generated rig data")


def test_owned_constraint_contract_tampering_fails_closed():
    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if bpy.ops.character_designer.limb_ik_build_leg() != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    def constraint_for(role, *, kind=None):
        inventory = limb_ik._validate_inventory(armature)
        return next(
            constraint
            for _pose_bone, constraint, record in inventory["records"]
            if record["role"] == role and (kind is None or record.get("kind") == kind)
        )

    def assert_rejected(constraint, property_name, bad_value):
        old_value = getattr(constraint, property_name)
        setattr(constraint, property_name, bad_value)
        try:
            try:
                limb_ik._validate_inventory(armature)
            except limb_ik.LimbIKError:
                pass
            else:
                raise AssertionError(
                    f"Inventory accepted edited {constraint.type}.{property_name}={bad_value!r}"
                )
        finally:
            setattr(constraint, property_name, old_value)
        limb_ik._validate_inventory(armature)

    ik = constraint_for("IK", kind="ARM")
    assert_rejected(ik, "influence", 0.5)
    assert_rejected(ik, "mute", True)
    assert_rejected(ik, "use_tail", False)
    assert_rejected(ik, "use_stretch", True)
    assert_rejected(ik, "target_space", "POSE")
    assert_rejected(ik, "owner_space", "POSE")

    end_rotation = constraint_for("END_ROTATION", kind="ARM")
    assert_rejected(end_rotation, "target_space", "POSE")
    assert_rejected(end_rotation, "owner_space", "POSE")
    assert_rejected(end_rotation, "mix_mode", "ADD")

    master_follow = constraint_for("MASTER_FOLLOW")
    assert_rejected(master_follow, "target_space", "WORLD")
    assert_rejected(master_follow, "owner_space", "WORLD")
    assert_rejected(master_follow, "mix_mode", "AFTER")

    line = constraint_for("POLE_LINE_STRETCH", kind="ARM")
    assert_rejected(line, "influence", 0.25)
    assert_rejected(line, "target_space", "POSE")
    display = constraint_for("POLE_DISPLAY_TRACK", kind="ARM")
    assert_rejected(display, "mute", True)
    assert_rejected(display, "owner_space", "POSE")
    heel = constraint_for("HEEL_LIMIT", kind="LEG")
    assert_rejected(heel, "influence", 0.75)
    assert_rejected(heel, "owner_space", "WORLD")


def test_foot_sole_heel_roll_and_internal_target():
    reset_scene()
    armature = make_humanoid(include_right=False)
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_leg() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory = limb_ik._validate_inventory(armature)
    rig = inventory["rigs"][("LEG", "L")]
    widgets = {
        obj.get(limb_ik.KIND_KEY): obj
        for obj in bpy.data.objects
        if limb_ik._owned(obj, inventory["armature_id"], role="WIDGET")
    }
    if len(widgets["FOOT"].data.vertices) != 46 or len(widgets["HEEL"].data.vertices) != 58:
        raise AssertionError("Rain Foot/FootRoll widget topology changed")
    target = armature.pose.bones[rig["target"].name]
    heel = armature.pose.bones[rig["heel"].name]
    solver = armature.pose.bones[rig["solver_target"].name]
    if rig["heel"].parent.name != target.name or rig["solver_target"].parent.name != heel.name:
        raise AssertionError("Foot target -> Heel -> MCH solver hierarchy is wrong")
    target.location += Vector((0.09, -0.04, 0.03))
    bpy.context.view_layer.update()
    target_before_roll = target.matrix.copy()
    heel_pivot = Vector(heel.head)
    solver_before = Vector(solver.head)
    foot = armature.pose.bones[rig["chain"][2]]
    foot_before = foot.matrix.copy()
    pivot_radius = (solver_before - heel_pivot).length
    heel.rotation_mode = "XYZ"
    heel.rotation_euler.x = math.radians(28.0)
    heel.rotation_euler.y = math.radians(9.0)
    bpy.context.view_layer.update()
    if (Vector(solver.head) - solver_before).length < 1.0e-3:
        raise AssertionError("Heel Roll did not move the internal Foot target around its pivot")
    if abs((Vector(solver.head) - Vector(heel.head)).length - pivot_radius) > 3.0e-4:
        raise AssertionError("Heel Roll did not preserve the solver's heel-pivot radius")
    assert_matrix_close(target.matrix, target_before_roll, "Heel Roll unexpectedly moved the visible Foot control")
    if (Vector(foot.head) - foot_before.translation).length < 1.0e-3:
        raise AssertionError("Leg IK did not respond to the internal Foot target")


def test_enhanced_tagless_rebuild_failure_recovers_then_upgrades_and_removes():
    """0.26.1 schema-2 rigs had no optional Pole Direction bone tag."""
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    plans = limb_ik._plans_for_kind(bpy.context, armature, settings, "ARM")
    limb_ik._build_plans(
        bpy.context,
        armature,
        plans,
        schema=limb_ik.ENHANCED_SCHEMA,
    )

    inventory = limb_ik._validate_inventory(armature)
    if inventory["schema"] != limb_ik.ENHANCED_SCHEMA or set(inventory["rigs"]) != {
        ("ARM", "L"),
        ("ARM", "R"),
    }:
        raise AssertionError("Schema-2 tagless fixture did not start as an exact enhanced Arm rig")
    for rig in inventory["rigs"].values():
        pole = rig["pole"]
        if limb_ik.POLE_DIRECTION_KEY not in pole:
            raise AssertionError("Current Build did not create the optional Pole Direction tag")
        del pole[limb_ik.POLE_DIRECTION_KEY]

    def snapshot_signature(current):
        controls = {}
        for bone in current["bones"]:
            pose_bone = armature.pose.bones[bone.name]
            controls[bone.name] = {
                "head": tuple(float(value) for value in bone.head_local),
                "tail": tuple(float(value) for value in bone.tail_local),
                "parent": bone.parent.name if bone.parent else "",
                "basis": tuple(float(value) for row in pose_bone.matrix_basis for value in row),
                "rotation_mode": pose_bone.rotation_mode,
            }
        constraints = {
            constraint.name: {
                "owner": pose_bone.name,
                "role": record["role"],
                "rig_id": record.get("rig_id", ""),
                "pole_angle": float(constraint.pole_angle) if constraint.type == "IK" else None,
            }
            for pose_bone, constraint, record in current["records"]
        }
        widgets = {
            obj.get(limb_ik.KIND_KEY): (
                tuple(tuple(float(value) for value in vertex.co) for vertex in obj.data.vertices),
                tuple(tuple(int(value) for value in edge.vertices) for edge in obj.data.edges),
            )
            for obj in bpy.data.objects
            if limb_ik._owned(obj, current["armature_id"], role="WIDGET")
        }
        return {
            "armature_id": current["armature_id"],
            "schema": current["schema"],
            "rig_ids": {key: rig["rig_id"] for key, rig in current["rigs"].items()},
            "controls": controls,
            "constraints": constraints,
            "widgets": widgets,
        }

    tagless = limb_ik._validate_inventory(armature)
    if any(rig["pole_direction"] is not None for rig in tagless["rigs"].values()):
        raise AssertionError("Schema-2 inventory did not accept the missing optional Pole Direction tag")
    before = snapshot_signature(tagless)

    original_create = limb_ik._create_constraints_and_shapes
    calls = {"count": 0}

    def fail_new_build_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("injected schema-2 tagless rebuild failure")
        return original_create(*args, **kwargs)

    limb_ik._create_constraints_and_shapes = fail_new_build_once
    try:
        if cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) != {"CANCELLED"}:
            raise AssertionError("Injected schema-2 tagless Rebuild failure did not cancel")
    finally:
        limb_ik._create_constraints_and_shapes = original_create
    if calls["count"] < 2:
        raise AssertionError("Failed schema-2 Rebuild did not invoke snapshot recovery")

    restored = limb_ik._validate_inventory(armature)
    after = snapshot_signature(restored)
    for field in ("armature_id", "schema", "rig_ids"):
        if after[field] != before[field]:
            raise AssertionError(f"Failed schema-2 tagless Rebuild changed snapshot {field}")
    if set(after["controls"]) != set(before["controls"]):
        raise AssertionError("Failed schema-2 tagless Rebuild changed the owned bone inventory")
    for name, old_control in before["controls"].items():
        new_control = after["controls"][name]
        if (
            new_control["parent"] != old_control["parent"]
            or new_control["rotation_mode"] != old_control["rotation_mode"]
        ):
            raise AssertionError(f"Failed schema-2 tagless Rebuild changed control metadata on '{name}'")
        for field in ("head", "tail", "basis"):
            error = max(
                abs(new_value - old_value)
                for new_value, old_value in zip(new_control[field], old_control[field])
            )
            if error > 1.0e-6:
                raise AssertionError(
                    f"Failed schema-2 tagless Rebuild changed control '{name}' {field}: {error}"
                )
    if set(after["constraints"]) != set(before["constraints"]):
        raise AssertionError("Failed schema-2 tagless Rebuild changed owned constraint names")
    for name, old_constraint in before["constraints"].items():
        new_constraint = after["constraints"][name]
        for field in ("owner", "role", "rig_id"):
            if new_constraint[field] != old_constraint[field]:
                raise AssertionError(
                    f"Failed schema-2 tagless Rebuild changed constraint '{name}' {field}"
                )
        old_angle = old_constraint["pole_angle"]
        new_angle = new_constraint["pole_angle"]
        if old_angle is None:
            if new_angle is not None:
                raise AssertionError(f"Failed schema-2 tagless Rebuild changed constraint '{name}' type")
        elif new_angle is None or abs(new_angle - old_angle) > 1.0e-7:
            raise AssertionError(f"Failed schema-2 tagless Rebuild changed '{name}' Pole Angle")
    if after["widgets"] != before["widgets"]:
        raise AssertionError("Failed schema-2 tagless Rebuild changed exact widget geometry")
    if any(limb_ik.POLE_DIRECTION_KEY in rig["pole"] for rig in restored["rigs"].values()):
        raise AssertionError("Snapshot recovery added a Pole Direction tag to the old 0.26.1 rig")

    if bpy.ops.character_designer.limb_ik_rebuild() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    upgraded = limb_ik._validate_inventory(armature)
    if upgraded["schema"] != limb_ik.CURRENT_SCHEMA:
        raise AssertionError(
            f"Successful Rebuild did not upgrade schema 2 to schema {limb_ik.CURRENT_SCHEMA}"
        )
    if any(
        rig["pole_direction"] is None or limb_ik.POLE_DIRECTION_KEY not in rig["pole"]
        for rig in upgraded["rigs"].values()
    ):
        raise AssertionError("Successful Rebuild did not upgrade tagless schema-2 Poles")
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if (
        owned_bones(armature)
        or limb_ik._owned_constraint_records(armature)
        or limb_ik.ARMATURE_ID_KEY in armature.data
        or limb_ik.SCHEMA_KEY in armature.data
    ):
        raise AssertionError("Remove left ownership residue after the schema-2 tagless upgrade")


def test_legacy_v1_remove_rebuild_upgrade_and_failure_recovery():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    plans = limb_ik._plans_for_kind(bpy.context, armature, settings, "ARM")
    limb_ik._build_plans(bpy.context, armature, plans, schema=limb_ik.LEGACY_SCHEMA)
    legacy = limb_ik._validate_inventory(armature)
    if legacy["schema"] != limb_ik.LEGACY_SCHEMA or len(legacy["bones"]) != 4 or len(legacy["records"]) != 4:
        raise AssertionError("Legacy v1 fixture is not exact")
    if bpy.ops.character_designer.limb_ik_rebuild() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    upgraded = limb_ik._validate_inventory(armature)
    if upgraded["schema"] != limb_ik.CURRENT_SCHEMA or upgraded["master"] is None:
        raise AssertionError("Rebuild did not upgrade legacy v1 to the current schema")
    if bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    analyze(armature)
    plans = limb_ik._plans_for_kind(bpy.context, armature, settings, "ARM")
    limb_ik._build_plans(bpy.context, armature, plans, schema=limb_ik.LEGACY_SCHEMA)
    legacy = limb_ik._validate_inventory(armature)
    old_ids = {rig["rig_id"] for rig in legacy["rigs"].values()}
    old_widget_counts = {
        obj.get(limb_ik.KIND_KEY): (len(obj.data.vertices), len(obj.data.edges))
        for obj in bpy.data.objects
        if limb_ik._owned(obj, legacy["armature_id"], role="WIDGET")
    }
    original = limb_ik._create_constraints_and_shapes
    calls = {"count": 0}

    def fail_upgrade_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("injected legacy upgrade failure")
        return original(*args, **kwargs)

    limb_ik._create_constraints_and_shapes = fail_upgrade_once
    try:
        if cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) != {"CANCELLED"}:
            raise AssertionError("Injected legacy upgrade failure did not cancel")
    finally:
        limb_ik._create_constraints_and_shapes = original
    restored = limb_ik._validate_inventory(armature)
    if restored["schema"] != limb_ik.LEGACY_SCHEMA or {rig["rig_id"] for rig in restored["rigs"].values()} != old_ids:
        raise AssertionError("Failed upgrade did not restore the exact legacy rig IDs/schema")
    restored_widget_counts = {
        obj.get(limb_ik.KIND_KEY): (len(obj.data.vertices), len(obj.data.edges))
        for obj in bpy.data.objects
        if limb_ik._owned(obj, restored["armature_id"], role="WIDGET")
    }
    if restored_widget_counts != old_widget_counts:
        raise AssertionError("Failed upgrade changed legacy widget geometry")


def test_rebuild_and_flip_pole():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    old_ids = {rig["rig_id"] for rig in limb_ik._validate_inventory(armature)["rigs"].values()}
    operator = bpy.ops.character_designer.limb_ik_flip_pole
    if operator(kind="ARM", side="L") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rebuilt_directions = {
        ("ARM", "L"): Vector((0.0, -1.0, 0.0)),
        ("ARM", "R"): Vector((0.0, 1.0, 0.35)).normalized(),
    }
    for key, direction in rebuilt_directions.items():
        set_pole_direction(settings, *key, direction)
    if bpy.ops.character_designer.limb_ik_rebuild() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory = limb_ik._validate_inventory(armature)
    new_ids = {rig["rig_id"] for rig in inventory["rigs"].values()}
    if old_ids & new_ids or len(new_ids) != 2:
        raise AssertionError("Rebuild did not replace both Arm rigs exactly once")
    for key, direction in rebuilt_directions.items():
        assert_rest_pole_direction(armature, key, direction, f"Rebuild {key}")
        assert_pole_plane_alignment(armature, key, f"Rebuild {key} solver plane")
        assert_short_rest_line_aims_at_pole(armature, key, f"first Rebuild {key}")

    first_rebuild_ids = new_ids
    if bpy.ops.character_designer.limb_ik_rebuild() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    second_inventory = limb_ik._validate_inventory(armature)
    second_rebuild_ids = {rig["rig_id"] for rig in second_inventory["rigs"].values()}
    if first_rebuild_ids & second_rebuild_ids or len(second_rebuild_ids) != 2:
        raise AssertionError("Second Rebuild did not replace both Arm rigs exactly once")
    for key, direction in rebuilt_directions.items():
        assert_rest_pole_direction(armature, key, direction, f"second Rebuild {key}")
        assert_pole_plane_alignment(armature, key, f"second Rebuild {key} solver plane")
        assert_short_rest_line_aims_at_pole(armature, key, f"second Rebuild {key}")


def test_context_restore_failure_rolls_back_new_build():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    original = limb_ik._restore_context
    calls = {"count": 0}

    def fail_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise limb_ik.LimbIKError("injected context restore failure")
        return original(*args, **kwargs)

    limb_ik._restore_context = fail_once
    try:
        if cancelled_result(bpy.ops.character_designer.limb_ik_build_arm) != {"CANCELLED"}:
            raise AssertionError("Build reported success after context restore failure")
    finally:
        limb_ik._restore_context = original
    if owned_bones(armature) or limb_ik._owned_constraint_records(armature):
        raise AssertionError("Context restore failure left a committed new rig")


def test_remove_and_rebuild_faults_restore_old_rig():
    reset_scene()
    armature = make_humanoid()
    _result, settings = analyze(armature)
    if bpy.ops.character_designer.limb_ik_build_arm() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    old_ids = {rig["rig_id"] for rig in limb_ik._validate_inventory(armature)["rigs"].values()}
    armature.pose.bones["CTRL_hand_IK.L"].location.x += 0.12
    bpy.context.view_layer.update()
    tracked_names = ("upper_arm.L", "forearm.L", "hand.L", "CTRL_hand_IK.L", "CTRL_elbow_pole.L")
    pose_before = {name: armature.pose.bones[name].matrix.copy() for name in tracked_names}

    original_remove = limb_ik._remove_owned

    def fail_after_remove(*args, **kwargs):
        original_remove(*args, **kwargs)
        raise RuntimeError("injected post-remove failure")

    limb_ik._remove_owned = fail_after_remove
    try:
        if cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove("EXEC_DEFAULT")) != {"CANCELLED"}:
            raise AssertionError("Injected Remove failure did not cancel")
    finally:
        limb_ik._remove_owned = original_remove
    restored_ids = {rig["rig_id"] for rig in limb_ik._validate_inventory(armature)["rigs"].values()}
    if restored_ids != old_ids:
        raise AssertionError("Failed Remove did not restore the exact old rig IDs")
    for name, matrix in pose_before.items():
        error = armature.pose.bones[name].matrix.to_quaternion().rotation_difference(matrix.to_quaternion()).angle
        location_error = (armature.pose.bones[name].matrix.translation - matrix.translation).length
        if error > 3.0e-3 or location_error > 3.0e-4:
            raise AssertionError(f"Failed Remove did not restore posed '{name}': {location_error}, {error}")

    original_create = limb_ik._create_constraints_and_shapes
    calls = {"count": 0}

    def fail_new_build_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("injected rebuild failure")
        return original_create(*args, **kwargs)

    limb_ik._create_constraints_and_shapes = fail_new_build_once
    try:
        if cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) != {"CANCELLED"}:
            raise AssertionError("Injected Rebuild failure did not cancel")
    finally:
        limb_ik._create_constraints_and_shapes = original_create
    restored_ids = {rig["rig_id"] for rig in limb_ik._validate_inventory(armature)["rigs"].values()}
    if restored_ids != old_ids:
        raise AssertionError("Failed Rebuild did not restore the exact old rig IDs")
    for name, matrix in pose_before.items():
        error = armature.pose.bones[name].matrix.to_quaternion().rotation_difference(matrix.to_quaternion()).angle
        location_error = (armature.pose.bones[name].matrix.translation - matrix.translation).length
        if error > 3.0e-3 or location_error > 3.0e-4:
            raise AssertionError(f"Failed Rebuild did not restore posed '{name}': {location_error}, {error}")


def main():
    ensure_registered()
    tests = (
        test_rotation_error_uses_shortest_quaternion_angle,
        test_analysis_is_read_only_and_detects_both_sides,
        test_direct_preroll_check_is_read_only_and_keeps_existing_mch_rig,
        test_direct_preroll_check_reports_replane_and_exact_straight_risk,
        test_direct_preroll_values_make_replaned_direct_start_pose_exact,
        test_direct_preroll_minimal_build_and_remove_restores_exact_rest,
        test_direct_preroll_incremental_build_all_and_method_mixing,
        test_incremental_build_refuses_damaged_existing_widget_resources,
        test_direct_preroll_build_failure_rolls_back_source_rest,
        test_direct_preroll_rebuild_stays_minimal_and_tamper_is_guarded,
        test_legacy_direct_schema4_without_display_helper_rebuilds_to_schema5,
        test_direct_preroll_initial_build_refuses_pose_animation_and_external_source_dependencies,
        test_direct_preroll_lifecycle_refuses_new_source_dependencies_and_hydrates_mode,
        test_low_confidence_helper_chain_is_not_guessed,
        test_pole_direction_defaults_analyze_preserves_and_reset_selected,
        test_x_negative_y_defaults_keep_elbows_back_and_knees_forward,
        test_x_like_lateral_knees_ignore_rest_residual_for_anatomical_default,
        test_default_operator_restores_only_selected_fixed_anatomical_axis,
        test_saved_pole_direction_hydrates_without_automatic_migration,
        test_explicit_pole_direction_validation_and_runtime_plane,
        test_build_selected_right_leg_and_build_all_atomic_completion,
        test_build_arm_then_leg_and_exact_contract,
        test_explicit_direction_handles_arbitrary_roll_without_pose_pop,
        test_build_from_non_rest_pose_preserves_full_limb_pose,
        test_idempotent_build_and_foreign_name_fail_closed,
        test_build_fault_rolls_back_without_residue,
        test_remove_refuses_foreign_dependency_then_removes_exact_data,
        test_remove_scans_cross_armature_and_constraint_animation_dependencies,
        test_remove_scans_object_constraints_and_driver_constraint_reads,
        test_enhanced_widgets_and_dynamic_pole_contract,
        test_direct_pole_guide_segments_and_dynamic_arrow_display,
        test_auto_align_target_all_constraint_spaces_and_rollback,
        test_auto_align_target_refuses_locked_animated_or_driven_state,
        test_master_incremental_build_and_global_transport_no_double_transform,
        test_master_scale_guards_and_nonuniform_remove,
        test_owned_constraint_contract_tampering_fails_closed,
        test_foot_sole_heel_roll_and_internal_target,
        test_enhanced_tagless_rebuild_failure_recovers_then_upgrades_and_removes,
        test_legacy_v1_remove_rebuild_upgrade_and_failure_recovery,
        test_rebuild_and_flip_pole,
        test_context_restore_failure_rolls_back_new_build,
        test_remove_and_rebuild_faults_restore_old_rig,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        reset_scene()
        ensure_unregistered()
    print(f"PASS Limb IK {len(tests)} tests")


if __name__ == "__main__":
    main()
