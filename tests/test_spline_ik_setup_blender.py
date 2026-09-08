"""Blender 5.2 contract tests for Character Designer's Spline IK Setup.

Run only in an isolated factory scene::

    blender.exe --background --factory-startup --python tests/test_spline_ik_setup_blender.py

The suite deliberately creates all of its data and never opens or saves X.blend.
"""

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDONS_ROOT = PROJECT_ROOT / "addons"
if str(ADDONS_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDONS_ROOT))

import character_designer
from character_designer import spline_ik_setup


TOLERANCE = 1.0e-5


def assert_vector_close(actual, expected, tolerance=TOLERANCE):
    actual = Vector(actual)
    expected = Vector(expected)
    if (actual - expected).length > tolerance:
        raise AssertionError(f"Expected {tuple(expected)}, got {tuple(actual)}")


def assert_scalar_close(actual, expected, tolerance=TOLERANCE):
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"Expected {expected}, got {actual}")


def cancelled_result(call):
    """Blender may raise a RuntimeError when a cancelled operator reports ERROR."""

    try:
        return call()
    except RuntimeError:
        return {"CANCELLED"}


def reset_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for collection in (bpy.data.curves, bpy.data.armatures, bpy.data.meshes):
        for datablock in list(collection):
            if datablock.users == 0:
                collection.remove(datablock)
    settings = getattr(
        bpy.context.window_manager,
        "character_designer_spline_ik",
        None,
    )
    if settings is not None:
        settings.armature = None
        settings.chain_names_json = ""
        settings.chain_count = 0
        settings.active_rig_id = ""
        settings.point_count = 5
        settings.control_size_ratio = 0.06
        settings.replace_existing = False
        settings.last_level = "NONE"
        settings.last_message = ""


def make_armature(name="SplineAuditRig", *, bone_count=5, branch=False):
    armature_data = bpy.data.armatures.new(f"{name}_Data")
    armature = bpy.data.objects.new(name, armature_data)
    bpy.context.scene.collection.objects.link(armature)
    armature.show_in_front = True
    bpy.context.view_layer.objects.active = armature
    armature.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    parent = None
    names = []
    for index in range(bone_count):
        bone = armature_data.edit_bones.new(f"Chain_{index + 1:02d}")
        # A slightly non-collinear chain catches accidental straight-line-only
        # implementations without making the expected tangents ambiguous.
        bone.head = (
            index * 0.8,
            0.12 * math.sin(index * 0.7),
            0.08 * math.cos(index * 0.5),
        )
        bone.tail = (
            (index + 1) * 0.8,
            0.12 * math.sin((index + 1) * 0.7),
            0.08 * math.cos((index + 1) * 0.5),
        )
        if parent is not None:
            bone.parent = parent
            bone.use_connect = True
        parent = bone
        names.append(bone.name)

    branch_name = None
    if branch:
        fork = armature_data.edit_bones.new("Branch")
        fork.head = Vector(parent.head)
        fork.tail = Vector(parent.head) + Vector((0.0, 0.7, 0.0))
        fork.parent = armature_data.edit_bones[names[-2]]
        branch_name = fork.name

    bpy.ops.object.mode_set(mode="POSE")
    return armature, tuple(names), branch_name


def select_pose_chain(armature, names):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="POSE")
    for pose_bone in armature.pose.bones:
        pose_bone.select = pose_bone.name in set(names)
    armature.data.bones.active = armature.data.bones[names[-1]]


def select_edit_chain(armature, names):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    selected = set(names)
    for bone in armature.data.edit_bones:
        enabled = bone.name in selected
        bone.select = enabled
        bone.select_head = enabled
        bone.select_tail = enabled
    armature.data.edit_bones.active = armature.data.edit_bones[names[-1]]


def capture(armature, names, *, edit_mode=False):
    if edit_mode:
        select_edit_chain(armature, names)
    else:
        select_pose_chain(armature, names)
    result = bpy.ops.character_designer.spline_ik_capture_chain()
    if result != {"FINISHED"}:
        settings = bpy.context.window_manager.character_designer_spline_ik
        raise AssertionError(f"Capture failed: {settings.last_message}")
    settings = bpy.context.window_manager.character_designer_spline_ik
    captured = tuple(json.loads(settings.chain_names_json))
    if captured != tuple(names):
        raise AssertionError(f"Expected captured chain {names}, got {captured}")
    return settings


def build(armature, names, *, points=5, replace_existing=False):
    settings = capture(armature, names)
    settings.point_count = points
    settings.replace_existing = replace_existing
    result = bpy.ops.character_designer.spline_ik_build()
    return result, settings


def build_legacy_without_rotation_tilt(armature, names, *, points=5):
    """Create the exact pre-Tilt generated rig through the production builder."""

    original = spline_ik_setup._enable_rotation_tilt

    def skip_rotation_tilt(*_args, **_kwargs):
        return False

    spline_ik_setup._enable_rotation_tilt = skip_rotation_tilt
    try:
        return build(armature, names, points=points)
    finally:
        spline_ik_setup._enable_rotation_tilt = original


def install_legacy_uuid_hook_names(curve, rig_id):
    """Recreate the exact visible Hook naming used before version 0.14.1."""

    registry = json.loads(curve[spline_ik_setup.HOOK_REGISTRY_KEY])
    modifiers = tuple(curve.modifiers)
    if len(registry) != len(modifiers):
        raise AssertionError("Cannot install legacy Hook names on an invalid fixture")
    for index, modifier in enumerate(modifiers):
        legacy_name = f"CDSplineIK_Hook_{rig_id}_{index + 1:02d}"
        modifier.name = legacy_name
        if modifier.name != legacy_name:
            raise AssertionError("Blender did not accept the legacy Hook fixture name")
        registry[index]["name"] = legacy_name
    curve[spline_ik_setup.HOOK_REGISTRY_KEY] = json.dumps(
        registry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    bpy.context.view_layer.update()
    return modifiers


def install_v1_rotation_tilt(armature, names, settings):
    """Install the exact 0.12 SWING_TWIST_Y contract on a pre-Tilt rig."""

    curve, controls = owned_curve_and_controls(settings)
    rig_id = settings.active_rig_id
    spline = curve.data.splines[0]
    tilt_registry = []
    for index, (point, control) in enumerate(zip(spline.bezier_points, controls)):
        control.rotation_mode = "XYZ"
        baseline = float(point.tilt)
        path = spline_ik_setup._tilt_data_path(index)
        spline_ik_setup._add_rotation_tilt_driver(
            curve.data,
            control,
            index,
            baseline,
            rotation_mode=spline_ik_setup.LEGACY_DRIVER_ROTATION_MODE,
        )
        tilt_registry.append(
            {
                "data_path": path,
                "owner": spline_ik_setup.OWNER_VALUE,
                "version": spline_ik_setup.LEGACY_DRIVER_VERSION,
                "rig_id": rig_id,
                "role": "ROTATION_TILT",
                "control_index": index,
                "target": control.name,
                "baseline_tilt": baseline,
            }
        )
    curve.data[spline_ik_setup.TILT_DRIVER_REGISTRY_KEY] = json.dumps(
        tilt_registry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    roll_registry = []
    roll_plan = spline_ik_setup._bone_twist_coefficients(
        armature,
        names,
        len(controls),
    )
    for bone_index, (bone_name, coefficients) in enumerate(roll_plan):
        pose_bone = armature.pose.bones[bone_name]
        original_mode = pose_bone.rotation_mode
        original_matrix = pose_bone.matrix_basis.copy()
        pose_bone.rotation_mode = "XYZ"
        pose_bone.matrix_basis = original_matrix
        baseline_y = float(pose_bone.rotation_euler.y)
        spline_ik_setup._add_bone_roll_driver(
            pose_bone,
            controls,
            coefficients,
            baseline_y,
            rotation_mode=spline_ik_setup.LEGACY_DRIVER_ROTATION_MODE,
        )
        roll_registry.append(
            {
                "data_path": pose_bone.path_from_id("rotation_euler"),
                "owner": spline_ik_setup.OWNER_VALUE,
                "version": spline_ik_setup.LEGACY_DRIVER_VERSION,
                "rig_id": rig_id,
                "role": "BONE_ROLL",
                "bone_index": bone_index,
                "bone": bone_name,
                "baseline_y": baseline_y,
                "baseline_matrix": spline_ik_setup._matrix_to_json(original_matrix),
                "original_rotation_mode": original_mode,
                "coefficients": [
                    {"control_index": index, "weight": weight}
                    for index, weight in sorted(coefficients.items())
                ],
            }
        )
    curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY] = json.dumps(
        roll_registry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if spline_ik_setup.CONTROL_FRAME_REGISTRY_KEY in curve.data:
        del curve.data[spline_ik_setup.CONTROL_FRAME_REGISTRY_KEY]
    bpy.context.view_layer.update()
    state, detail = spline_ik_setup._rotation_tilt_state(curve, controls, rig_id)
    if state != "ENABLED":
        raise AssertionError(detail)
    return curve, controls


def owned_objects(rig_id, role=None):
    return [
        obj
        for obj in bpy.data.objects
        if spline_ik_setup._is_owned(obj, rig_id=rig_id, role=role)
    ]


def owned_curve_and_controls(settings):
    rig_id = settings.active_rig_id
    curves = owned_objects(rig_id, "CURVE")
    controls = sorted(
        owned_objects(rig_id, "CONTROL"),
        key=lambda obj: int(obj[spline_ik_setup.CONTROL_INDEX_KEY]),
    )
    if len(curves) != 1:
        raise AssertionError(f"Expected one owned Curve, found {len(curves)}")
    return curves[0], controls


def evaluated_bezier_point(curve_obj, point_index):
    evaluated = curve_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return Vector(evaluated.data.splines[0].bezier_points[point_index].co)


def evaluated_bezier_handles(curve_obj, point_index):
    evaluated = curve_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    point = evaluated.data.splines[0].bezier_points[point_index]
    return Vector(point.handle_left), Vector(point.handle_right)


def evaluated_bezier_tilts(curve_obj):
    evaluated = curve_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return tuple(
        float(point.tilt)
        for point in evaluated.data.splines[0].bezier_points
    )


def evaluated_curve_vertices(curve_obj):
    """Return fully modifier-evaluated Curve samples as a temporary Mesh."""

    evaluated = curve_obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return tuple(Vector(vertex.co) for vertex in mesh.vertices)
    finally:
        evaluated.to_mesh_clear()


def evaluated_pose_matrices(armature, names):
    evaluated = armature.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return tuple(evaluated.pose.bones[name].matrix.copy() for name in names)


def signed_pose_roll_delta(before, after):
    """Measure rotation of the evaluated bone X axis around its Y axis."""

    before = before.to_3x3()
    after = after.to_3x3()
    old_x = Vector(before.col[0]).normalized()
    new_x = Vector(after.col[0]).normalized()
    roll_axis = Vector(before.col[1]).normalized() + Vector(after.col[1]).normalized()
    if roll_axis.length <= 1.0e-8:
        roll_axis = Vector(before.col[1]).normalized()
    else:
        roll_axis.normalize()
    return math.atan2(
        roll_axis.dot(old_x.cross(new_x)),
        old_x.dot(new_x),
    )


def assert_matrices_close(actual, expected, tolerance=TOLERANCE):
    if len(actual) != len(expected):
        raise AssertionError(f"Expected {len(expected)} matrices, got {len(actual)}")
    for index, (actual_matrix, expected_matrix) in enumerate(zip(actual, expected)):
        error = max(
            abs(left - right)
            for actual_row, expected_row in zip(actual_matrix, expected_matrix)
            for left, right in zip(actual_row, expected_row)
        )
        if error > tolerance:
            raise AssertionError(f"Matrix {index} changed by {error}, tolerance {tolerance}")


def assert_vector_sequences_close(actual, expected, tolerance=TOLERANCE):
    if len(actual) != len(expected):
        raise AssertionError(f"Expected {len(expected)} vectors, got {len(actual)}")
    for actual_vector, expected_vector in zip(actual, expected):
        assert_vector_close(actual_vector, expected_vector, tolerance=tolerance)


def snapshot_counts():
    return {
        "objects": len(bpy.data.objects),
        "curves": len(bpy.data.curves),
        "constraints": sum(
            len(pose_bone.constraints)
            for obj in bpy.data.objects
            if obj.type == "ARMATURE" and obj.pose
            for pose_bone in obj.pose.bones
        ),
    }


def matrix_signature(matrix):
    # Reassigning RNA float matrices during rollback may quantize by a few
    # 1e-6; five decimals is still as strict as the suite's visual/deform
    # tolerance while avoiding false bit-exact failures.
    return tuple(round(value, 5) for row in matrix for value in row)


def driver_signature(id_data):
    animation_data = getattr(id_data, "animation_data", None)
    if animation_data is None:
        return ()
    return tuple(
        sorted(
            (
                fcurve.data_path,
                fcurve.array_index,
                fcurve.driver.type,
                fcurve.driver.expression,
                bool(fcurve.driver.use_self),
                tuple(
                    (
                        variable.name,
                        variable.type,
                        tuple(
                            (
                                target.id.name if target.id is not None else "",
                                getattr(target, "data_path", ""),
                                getattr(target, "transform_type", ""),
                                getattr(target, "transform_space", ""),
                                getattr(target, "rotation_mode", ""),
                            )
                            for target in variable.targets
                        ),
                    )
                    for variable in fcurve.driver.variables
                ),
            )
            for fcurve in animation_data.drivers
        )
    )


def rig_signature(armature, names, rig_id):
    """Capture the user-visible and ownership contract of one generated rig."""

    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        rig_id,
    )
    constraint = inventory["constraint"]
    owner = inventory["constraint_owner"]
    curve = inventory["curve_object"]
    spline = curve.data.splines[0]
    return {
        "counts": snapshot_counts(),
        "constraint": (
            constraint.name,
            constraint.type,
            constraint.target.name if constraint.target else "",
            constraint.chain_count,
            constraint.y_scale_mode,
            constraint.xz_scale_mode,
            constraint.use_curve_radius,
            constraint.use_even_divisions,
            constraint.use_chain_offset,
            constraint.mute,
            round(constraint.influence, 9),
            tuple(owner.constraints).index(constraint),
            owner.get(spline_ik_setup.CONSTRAINT_REGISTRY_KEY, ""),
        ),
        "curve": (
            curve.name,
            curve.data.name,
            curve.data.dimensions,
            curve.data.resolution_u,
            curve.data.render_resolution_u,
            curve.data.twist_mode,
            matrix_signature(curve.matrix_basis),
            curve.get(spline_ik_setup.HOOK_REGISTRY_KEY, ""),
            curve.data.get(spline_ik_setup.TILT_DRIVER_REGISTRY_KEY, ""),
            curve.data.get(spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY, ""),
        ),
        "points": tuple(
            (
                tuple(round(value, 9) for value in point.co),
                tuple(round(value, 9) for value in point.handle_left),
                tuple(round(value, 9) for value in point.handle_right),
                point.handle_left_type,
                point.handle_right_type,
                round(float(point.tilt), 9),
            )
            for point in spline.bezier_points
        ),
        "evaluated_points": tuple(
            tuple(round(value, 9) for value in evaluated_bezier_point(curve, index))
            for index in range(len(spline.bezier_points))
        ),
        "evaluated_tilts": tuple(
            round(value, 9) for value in evaluated_bezier_tilts(curve)
        ),
        "curve_drivers": driver_signature(curve.data),
        "armature_drivers": driver_signature(armature),
        "pose_bone_roll": tuple(
            (
                name,
                armature.pose.bones[name].rotation_mode,
                matrix_signature(armature.pose.bones[name].matrix_basis),
                tuple(round(value, 9) for value in armature.pose.bones[name].rotation_euler),
            )
            for name in names
        ),
        "rotation_tilt": (
            inventory.get("rotation_tilt_state", ""),
            inventory.get("rotation_tilt_detail", ""),
        ),
        "controls": tuple(
            (
                control.name,
                control.empty_display_type,
                round(control.empty_display_size, 9),
                matrix_signature(control.matrix_parent_inverse),
                matrix_signature(control.matrix_basis),
                control.rotation_mode,
                tuple(round(value, 9) for value in control.rotation_euler),
                bool(control.hide_viewport),
                bool(control.hide_render),
                bool(control.hide_get()),
                bool(control.select_get()),
            )
            for control in inventory["controls"]
        ),
        "hooks": tuple(
            (
                modifier.name,
                modifier.type,
                modifier.object.name if modifier.object else "",
                tuple(modifier.vertex_indices),
                modifier.falloff_type,
                round(modifier.strength, 9),
                matrix_signature(modifier.matrix_inverse),
            )
            for modifier in curve.modifiers
        ),
    }


def legacy_conflict_signature(armature, names, settings, curve, controls):
    def action_identity(id_data):
        animation_data = getattr(id_data, "animation_data", None)
        action = animation_data.action if animation_data is not None else None
        return (action.name, action.as_pointer()) if action is not None else ("", 0)

    return {
        "rig": rig_signature(armature, names, settings.active_rig_id),
        "curve_pointer": curve.as_pointer(),
        "curve_data_pointer": curve.data.as_pointer(),
        "curve_action": action_identity(curve.data),
        "armature_action": action_identity(armature),
        "armature_drivers": driver_signature(armature),
        "curve_vertices": tuple(
            tuple(round(value, 7) for value in point)
            for point in evaluated_curve_vertices(curve)
        ),
        "pose": tuple(matrix_signature(matrix) for matrix in evaluated_pose_matrices(armature, names)),
        "controls": tuple(
            (
                control.as_pointer(),
                matrix_signature(control.matrix_parent_inverse),
                matrix_signature(control.matrix_basis),
                matrix_signature(control.matrix_world),
                tuple(round(value, 7) for value in control.delta_location),
                tuple(round(value, 7) for value in control.delta_rotation_euler),
                tuple(round(value, 7) for value in control.delta_rotation_quaternion),
                tuple(round(value, 7) for value in control.delta_scale),
                action_identity(control),
                tuple(
                    (
                        constraint.name,
                        constraint.type,
                        getattr(getattr(constraint, "target", None), "name", ""),
                        round(constraint.influence, 7),
                        bool(constraint.mute),
                    )
                    for constraint in control.constraints
                ),
            )
            for control in controls
        ),
        "context": (
            bpy.context.mode,
            bpy.context.view_layer.objects.active.as_pointer()
            if bpy.context.view_layer.objects.active is not None
            else 0,
            tuple(sorted(obj.as_pointer() for obj in bpy.context.selected_objects)),
        ),
    }


def ensure_spline_module_registered():
    """Allow the suite to diagnose the module while main-package wiring lands."""

    if not hasattr(bpy.types.WindowManager, "character_designer_spline_ik"):
        for cls in spline_ik_setup.SPLINE_IK_SETUP_CLASSES:
            bpy.utils.register_class(cls)
        bpy.types.WindowManager.character_designer_spline_ik = bpy.props.PointerProperty(
            type=spline_ik_setup.CharacterDesignerSplineIKState,
            options={"SKIP_SAVE"},
        )


def ensure_spline_module_unregistered():
    if hasattr(bpy.types.WindowManager, "character_designer_spline_ik"):
        del bpy.types.WindowManager.character_designer_spline_ik
    for cls in reversed(spline_ik_setup.SPLINE_IK_SETUP_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass


def test_strict_chain_capture_pose_and_edit():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    capture(armature, names, edit_mode=False)
    capture(armature, names, edit_mode=True)

    # Gapped selections are invalid and must not rewrite the old capture.
    select_pose_chain(armature, (names[0], names[2], names[3]))
    settings = bpy.context.window_manager.character_designer_spline_ik
    previous = (
        settings.armature,
        settings.chain_names_json,
        settings.chain_count,
        settings.active_rig_id,
    )
    if cancelled_result(bpy.ops.character_designer.spline_ik_capture_chain) != {"CANCELLED"}:
        raise AssertionError("A gapped Pose chain was accepted")
    current = (
        settings.armature,
        settings.chain_names_json,
        settings.chain_count,
        settings.active_rig_id,
    )
    if current != previous:
        raise AssertionError("Failed capture rewrote the previous valid chain")
    if settings.last_level != "WARNING":
        raise AssertionError("Failed recapture did not produce a non-destructive warning")
    status = settings.last_message.casefold()
    if "kept" not in status or "previous" not in status:
        raise AssertionError(
            f"Failed recapture did not clearly say the previous capture was kept: {status!r}"
        )

    reset_scene()
    armature, names, branch_name = make_armature(bone_count=4, branch=True)
    select_pose_chain(armature, (*names, branch_name))
    if cancelled_result(bpy.ops.character_designer.spline_ik_capture_chain) != {"CANCELLED"}:
        raise AssertionError("A branched chain was accepted")


def test_curve_profile_probe_tracks_nonzero_tilt_without_residue():
    reset_scene()
    curve_data = bpy.data.curves.new("TiltProfileProbeData", type="CURVE")
    curve_data.dimensions = "3D"
    curve_data.twist_mode = "MINIMUM"
    spline = curve_data.splines.new(type="BEZIER")
    spline.bezier_points.add(2)
    spline.resolution_u = 8
    for point, co in zip(
        spline.bezier_points,
        ((0.0, 0.0, 0.0), (1.0, 1.1, 0.2), (1.45, 2.0, 1.0)),
    ):
        point.co = co
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"
    curve = bpy.data.objects.new("TiltProfileProbe", curve_data)
    bpy.context.scene.collection.objects.link(curve)
    bpy.context.view_layer.update()
    expected_count = 17
    counts_before = snapshot_counts()
    normals_before, centers_before = spline_ik_setup._evaluated_curve_profile_normals(
        bpy.context,
        curve,
        expected_count,
    )
    if snapshot_counts() != counts_before:
        raise AssertionError("Curve profile probing left temporary data behind")

    tilt = math.radians(37.0)
    for point in spline.bezier_points:
        point.tilt = tilt
    curve_data.update_tag()
    bpy.context.view_layer.update()
    normals_after, centers_after = spline_ik_setup._evaluated_curve_profile_normals(
        bpy.context,
        curve,
        expected_count,
    )
    if snapshot_counts() != counts_before:
        raise AssertionError("Tilted profile probing left temporary data behind")
    assert_vector_sequences_close(centers_after, centers_before, tolerance=2.0e-5)

    for sample_index in (0, 8, 16):
        if sample_index == 0:
            tangent = centers_before[1] - centers_before[0]
        elif sample_index == expected_count - 1:
            tangent = centers_before[-1] - centers_before[-2]
        else:
            tangent = centers_before[sample_index + 1] - centers_before[sample_index - 1]
        tangent.normalize()
        angle = spline_ik_setup._signed_angle_about_axis(
            normals_before[sample_index],
            normals_after[sample_index],
            tangent,
        )
        if abs(abs(angle) - tilt) > math.radians(0.2):
            raise AssertionError(
                f"Known 37° Tilt rotated the profile by {math.degrees(angle):.3f}°"
            )


def test_curve_profile_probe_cleanup_survives_update_failure():
    """A cleanup refresh failure must not strand the temporary probe IDs."""

    reset_scene()
    curve_data = bpy.data.curves.new("TiltProfileCleanupData", type="CURVE")
    curve_data.dimensions = "3D"
    spline = curve_data.splines.new(type="BEZIER")
    spline.bezier_points.add(1)
    spline.resolution_u = 4
    for point, co in zip(spline.bezier_points, ((0.0, 0.0, 0.0), (0.0, 1.0, 0.2))):
        point.co = co
        point.handle_left_type = "AUTO"
        point.handle_right_type = "AUTO"
    curve = bpy.data.objects.new("TiltProfileCleanup", curve_data)
    bpy.context.scene.collection.objects.link(curve)
    bpy.context.view_layer.update()

    class FailingCleanupViewLayer:
        def __init__(self, real_view_layer):
            self.real_view_layer = real_view_layer
            self.update_count = 0

        def update(self):
            self.update_count += 1
            if self.update_count == 2:
                raise RuntimeError("injected cleanup dependency-graph failure")
            self.real_view_layer.update()

    class ContextProxy:
        def __init__(self, real_context):
            self.scene = real_context.scene
            self.view_layer = FailingCleanupViewLayer(real_context.view_layer)
            self._real_context = real_context

        def evaluated_depsgraph_get(self):
            return self._real_context.evaluated_depsgraph_get()

    ids_before = (
        len(bpy.data.objects),
        len(bpy.data.curves),
        len(bpy.data.meshes),
    )
    try:
        spline_ik_setup._evaluated_curve_profile_normals(
            ContextProxy(bpy.context),
            curve,
            5,
        )
    except spline_ik_setup.SplineIKSetupError as exc:
        if "could not be cleaned up" not in str(exc):
            raise AssertionError(f"Unexpected cleanup failure: {exc}") from exc
    else:
        raise AssertionError("The injected cleanup refresh failure was not reported")

    ids_after = (
        len(bpy.data.objects),
        len(bpy.data.curves),
        len(bpy.data.meshes),
    )
    if ids_after != ids_before:
        raise AssertionError(
            f"Cleanup refresh failure left temporary IDs: {ids_before} -> {ids_after}"
        )
    if any(
        datablock.name.startswith("CharacterDesigner_ControlFrameProbe")
        for datablocks in (bpy.data.objects, bpy.data.curves, bpy.data.meshes)
        for datablock in datablocks
    ):
        raise AssertionError("Cleanup refresh failure left a named profile probe behind")


def test_build_from_other_active_mesh_uses_stored_capture_endpoints():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=6)
    settings = capture(armature, names)
    settings.point_count = 7
    expected_root = Vector(armature.data.bones[names[0]].head_local)
    expected_tip = Vector(armature.data.bones[names[-1]].tail_local)

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    mesh = bpy.data.meshes.new("UnrelatedActiveMesh_Data")
    mesh.from_pydata(
        ((0.0, 0.0, 0.0), (0.25, 0.0, 0.0), (0.0, 0.25, 0.0)),
        (),
        ((0, 1, 2),),
    )
    unrelated = bpy.data.objects.new("UnrelatedActiveMesh", mesh)
    bpy.context.scene.collection.objects.link(unrelated)
    unrelated.select_set(True)
    bpy.context.view_layer.objects.active = unrelated

    if bpy.context.mode != "OBJECT" or bpy.context.object is not unrelated:
        raise AssertionError("The unrelated Mesh was not the active Object Mode context")
    if bpy.ops.character_designer.spline_ik_build() != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    curve, _controls = owned_curve_and_controls(settings)
    spline = curve.data.splines[0]
    if len(spline.bezier_points) != settings.point_count:
        raise AssertionError("Object Mode build did not use the complete stored capture")
    assert_vector_close(spline.bezier_points[0].co, expected_root)
    assert_vector_close(spline.bezier_points[-1].co, expected_tip)


def test_five_point_bezier_hooks_and_display_types():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    spline = curve.data.splines[0]
    if spline.type != "BEZIER" or len(spline.bezier_points) != 5:
        raise AssertionError("Build did not create exactly five Bezier points")
    if len(controls) != 5 or len(curve.modifiers) != 5:
        raise AssertionError("Build did not create five controls and five Hooks")
    expected_hook_names = [f"CD Spline IK Hook {index + 1:02d}" for index in range(5)]
    if [modifier.name for modifier in curve.modifiers] != expected_hook_names:
        raise AssertionError("Generated Hook modifiers do not use short readable names")
    if any(settings.active_rig_id in modifier.name for modifier in curve.modifiers):
        raise AssertionError("The internal rig UUID leaked into a visible Hook name")
    if [control.empty_display_type for control in controls] != [
        "CUBE",
        "SPHERE",
        "SPHERE",
        "SPHERE",
        "CUBE",
    ]:
        raise AssertionError("Endpoint/interior control display types are incorrect")
    try:
        registry = json.loads(curve[spline_ik_setup.HOOK_REGISTRY_KEY])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AssertionError("The Curve has no valid generated Hook registry") from exc
    if len(registry) != 5:
        raise AssertionError("The Curve Hook registry does not contain five entries")
    for index, modifier in enumerate(curve.modifiers):
        record = registry[index]
        if (
            modifier.type != "HOOK"
            or modifier.object is not controls[index]
            or record.get("name") != modifier.name
            or record.get("owner") != spline_ik_setup.OWNER_VALUE
            or record.get("version") != spline_ik_setup.RIG_VERSION
            or record.get("rig_id") != settings.active_rig_id
            or record.get("role") != "HOOK"
            or record.get("control_index") != index
            or record.get("target") != controls[index].name
            or not spline_ik_setup._is_owned(
                modifier.object,
                rig_id=settings.active_rig_id,
                role="CONTROL",
            )
        ):
            raise AssertionError("A generated Hook is not bound/tagged to its control")
    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        tuple(json.loads(settings.chain_names_json)),
        settings.active_rig_id,
    )
    if inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_CURRENT:
        raise AssertionError("A new setup was not registered with current Hook names")
    if curve.parent is not armature or any(control.parent is not armature for control in controls):
        raise AssertionError("Generated rig objects must share Armature-local space")


def test_short_hook_names_are_scoped_per_curve():
    reset_scene()
    armature_a, names_a, _branch = make_armature(name="ScopedHookRigA", bone_count=4)
    result, settings = build(armature_a, names_a, points=3)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig_a = settings.active_rig_id
    curve_a, controls_a = owned_curve_and_controls(settings)

    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    armature_b, names_b, _branch = make_armature(name="ScopedHookRigB", bone_count=4)
    result, settings = build(armature_b, names_b, points=3)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig_b = settings.active_rig_id
    curve_b, _controls_b = owned_curve_and_controls(settings)

    expected = ["CD Spline IK Hook 01", "CD Spline IK Hook 02", "CD Spline IK Hook 03"]
    if [modifier.name for modifier in curve_a.modifiers] != expected:
        raise AssertionError("The first Curve did not keep exact short Hook names")
    if [modifier.name for modifier in curve_b.modifiers] != expected:
        raise AssertionError("The second Curve received cross-object numeric suffixes")
    if rig_a == rig_b:
        raise AssertionError("Independent generated rigs unexpectedly share an internal UUID")
    for curve, rig_id in ((curve_a, rig_a), (curve_b, rig_b)):
        registry = json.loads(curve[spline_ik_setup.HOOK_REGISTRY_KEY])
        if any(
            record["name"] != expected[index] or record["rig_id"] != rig_id
            for index, record in enumerate(registry)
        ):
            raise AssertionError("Object-scoped Hook names lost their internal ownership")

    if bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if owned_objects(rig_b):
        raise AssertionError("Removing the second short-name rig left generated objects")
    inventory_a = spline_ik_setup._rig_inventory(
        bpy.context,
        armature_a,
        names_a,
        rig_a,
    )
    if (
        inventory_a["curve_object"].as_pointer() != curve_a.as_pointer()
        or tuple(control.as_pointer() for control in inventory_a["controls"])
        != tuple(control.as_pointer() for control in controls_a)
        or inventory_a["hook_name_state"] != spline_ik_setup.HOOK_NAME_CURRENT
    ):
        raise AssertionError("Removing one short-name rig damaged the other")

    settings = capture(armature_a, names_a)
    if settings.active_rig_id != rig_a:
        raise AssertionError("Recapturing the first rig resolved the wrong internal UUID")
    if bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if owned_objects(rig_a):
        raise AssertionError("Removing the first short-name rig left generated objects")


def test_middle_translation_and_endpoint_rotation_deform_curve():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    bpy.context.view_layer.update()

    before_middle = evaluated_curve_vertices(curve)
    controls[2].location += Vector((0.0, 0.45, 0.18))
    bpy.context.view_layer.update()
    after_middle = evaluated_curve_vertices(curve)
    if len(before_middle) != len(after_middle) or not before_middle:
        raise AssertionError("Curve evaluation changed sample topology unexpectedly")
    if max((after - before).length for before, after in zip(before_middle, after_middle)) < 0.3:
        raise AssertionError("Moving an interior Sphere did not move its Bezier point")

    before_rotation = evaluated_curve_vertices(curve)
    endpoint = Vector(curve.data.splines[0].bezier_points[0].co)
    controls[0].rotation_mode = "XYZ"
    controls[0].rotation_euler.rotate_axis("Z", math.radians(35.0))
    bpy.context.view_layer.update()
    after_rotation = evaluated_curve_vertices(curve)
    if len(before_rotation) != len(after_rotation):
        raise AssertionError("Endpoint rotation changed Curve sample topology")
    before_endpoint = min(before_rotation, key=lambda point: (point - endpoint).length)
    after_endpoint = min(after_rotation, key=lambda point: (point - endpoint).length)
    assert_vector_close(before_endpoint, endpoint)
    assert_vector_close(after_endpoint, endpoint)
    if max(
        (after - before).length
        for before, after in zip(before_rotation, after_rotation)
    ) < 1.0e-3:
        raise AssertionError("Rotating an endpoint Cube did not change evaluated curve samples")


def test_wire_control_rotation_drives_tilt_and_survives_lifecycle():
    """Cube/Sphere rotation authors Tilt while Hooks remain translation-only.

    The generated controls are oriented with local Y along the sampled Curve
    tangent.  A local-Y rotation is therefore the artist-facing roll gesture:
    its magnitude must reach only the matching Bezier point's Tilt.  Moving a
    control must continue to use its Hook without changing Tilt.  Finally, the
    ownership operators must still select, remove, and rebuild this driven rig
    without leaking stale driver targets.
    """

    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    if [control.empty_display_type for control in controls] != [
        "CUBE",
        "SPHERE",
        "SPHERE",
        "SPHERE",
        "CUBE",
    ]:
        raise AssertionError("Tilt regression fixture lost its Cube/Sphere controls")

    bpy.context.view_layer.update()
    baseline_tilts = evaluated_bezier_tilts(curve)
    if len(baseline_tilts) != len(controls):
        raise AssertionError("Generated Curve and control counts do not match")

    # X/Z are explicit swing channels in the Curve/strap frame.  Even a
    # combined two-axis bend with RY left at zero must not leak a geometric
    # Swing-Twist phase into Tilt.
    middle_index = 2
    if controls[middle_index].rotation_mode != "YXZ":
        raise AssertionError("Generated controls do not use the Y-first channel contract")
    before_swing = evaluated_curve_vertices(curve)
    before_swing_bone_y = tuple(
        float(armature.pose.bones[name].rotation_euler.y) for name in names
    )
    controls[middle_index].rotation_euler.x = math.radians(25.0)
    controls[middle_index].rotation_euler.z = math.radians(-31.0)
    bpy.context.view_layer.update()
    after_swing = evaluated_curve_vertices(curve)
    if max(
        (after - before).length for before, after in zip(before_swing, after_swing)
    ) < 1.0e-3:
        raise AssertionError("Combined local X/Z rotation did not steer the Curve")
    for actual, expected in zip(evaluated_bezier_tilts(curve), baseline_tilts):
        assert_scalar_close(actual, expected)
    after_swing_bone_y = tuple(
        float(armature.pose.bones[name].rotation_euler.y) for name in names
    )
    for actual, expected in zip(after_swing_bone_y, before_swing_bone_y):
        assert_scalar_close(actual, expected)
    controls[middle_index].rotation_euler.x = 0.0
    controls[middle_index].rotation_euler.z = 0.0
    bpy.context.view_layer.update()

    # Translation remains a Hook concern.  It moves the matching control point
    # but must not be reinterpreted as roll/Tilt.
    before_middle = evaluated_curve_vertices(curve)
    translation = Vector((0.0, 0.31, -0.17))
    controls[middle_index].location += translation
    bpy.context.view_layer.update()
    after_middle = evaluated_curve_vertices(curve)
    if len(before_middle) != len(after_middle) or not before_middle:
        raise AssertionError("Hook translation changed Curve sample topology")
    if max(
        (after - before).length
        for before, after in zip(before_middle, after_middle)
    ) < 0.2:
        raise AssertionError("Moving the Sphere no longer deforms the Curve through its Hook")
    translated_tilts = evaluated_bezier_tilts(curve)
    for actual, expected in zip(translated_tilts, baseline_tilts):
        assert_scalar_close(actual, expected)

    # Cover both visual controller shapes.  The exact sign is a Curve-frame
    # convention, but it must be consistent and the magnitude must be 1:1 with
    # the local tangent-axis gesture.  No neighbouring point may inherit roll.
    requested_angles = {0: math.radians(31.0), 2: math.radians(-23.0)}
    prior_tilts = translated_tilts
    prior_pose = evaluated_pose_matrices(armature, names)
    for index, angle in requested_angles.items():
        controls[index].rotation_euler.y += angle
    bpy.context.view_layer.update()
    rotated_tilts = evaluated_bezier_tilts(curve)
    rotated_pose = evaluated_pose_matrices(armature, names)
    nonzero_deltas = []
    for index, (before, after) in enumerate(zip(prior_tilts, rotated_tilts)):
        delta = after - before
        if index in requested_angles:
            if abs(abs(delta) - abs(requested_angles[index])) > TOLERANCE:
                raise AssertionError(
                    f"Control {index} rotated {requested_angles[index]} rad but "
                    f"its point Tilt changed by {delta} rad"
                )
            nonzero_deltas.append(delta)
        else:
            assert_scalar_close(after, before)

    if nonzero_deltas[0] * nonzero_deltas[1] >= 0.0:
        raise AssertionError("Opposite controller rotations did not produce opposite Tilt")
    if max(
        abs(signed_pose_roll_delta(before, after))
        for before, after in zip(prior_pose, rotated_pose)
    ) < math.radians(5.0):
        raise AssertionError(
            "Curve point Tilt changed, but the evaluated Spline IK bones did not roll"
        )
    if max(
        (before.translation - after.translation).length
        for before, after in zip(prior_pose, rotated_pose)
    ) > 1.0e-4:
        raise AssertionError("Rotation Tilt moved evaluated bone positions instead of only rolling")

    # A refused duplicate Build must be a strict no-op for the generated rig,
    # including its evaluated Tilt and object identities.
    rig_id = settings.active_rig_id
    curve_pointer = curve.as_pointer()
    control_pointers = tuple(control.as_pointer() for control in controls)
    if cancelled_result(bpy.ops.character_designer.spline_ik_build) != {"CANCELLED"}:
        raise AssertionError("Duplicate Build unexpectedly replaced the driven rig")
    same_curve, same_controls = owned_curve_and_controls(settings)
    if (
        same_curve.as_pointer() != curve_pointer
        or tuple(control.as_pointer() for control in same_controls) != control_pointers
    ):
        raise AssertionError("Refused duplicate Build replaced Curve or control identities")
    for actual, expected in zip(evaluated_bezier_tilts(same_curve), rotated_tilts):
        assert_scalar_close(actual, expected)

    # Selection remains exactly owner-scoped even after authoring Tilt.
    sentinel = bpy.data.objects.new("TiltSelectionSentinel", None)
    bpy.context.scene.collection.objects.link(sentinel)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    sentinel.select_set(True)
    bpy.context.view_layer.objects.active = sentinel
    if bpy.ops.character_designer.spline_ik_select_controls() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if set(bpy.context.selected_objects) != set(controls) or sentinel.select_get():
        raise AssertionError("Select Controls regressed after Tilt authoring")

    curve_data_name = curve.data.name
    control_names = tuple(control.name for control in controls)
    if bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if owned_objects(rig_id):
        raise AssertionError("Remove Generated left Tilt-enabled owned objects behind")
    if bpy.data.curves.get(curve_data_name) is not None:
        raise AssertionError("Remove Generated left driven Curve Data behind")
    if any(bpy.data.objects.get(name) is not None for name in control_names):
        raise AssertionError("Remove Generated left Tilt driver targets behind")

    # The stored chain capture remains usable after cleanup.  A fresh build
    # must recreate working Tilt controls instead of depending on deleted IDs.
    if bpy.ops.character_designer.spline_ik_build() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rebuilt_curve, rebuilt_controls = owned_curve_and_controls(settings)
    rebuilt_before = evaluated_bezier_tilts(rebuilt_curve)
    rebuild_angle = math.radians(17.0)
    rebuilt_controls[1].rotation_euler.y += rebuild_angle
    bpy.context.view_layer.update()
    rebuilt_after = evaluated_bezier_tilts(rebuilt_curve)
    for index, (before, after) in enumerate(zip(rebuilt_before, rebuilt_after)):
        if index == 1:
            if abs(abs(after - before) - rebuild_angle) > TOLERANCE:
                raise AssertionError("Rebuilt Sphere no longer drives its matching Tilt")
        else:
            assert_scalar_close(after, before)


def test_rotation_tilt_interpolates_across_chain_and_maps_endpoints():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=7)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    bpy.context.view_layer.update()

    baseline_pose = evaluated_pose_matrices(armature, names)
    baseline_tilts = evaluated_bezier_tilts(curve)
    angle = math.radians(25.0)
    dominant_bones = []
    profiles = []
    for control_index, control in enumerate(controls):
        control.rotation_euler.y = angle
        bpy.context.view_layer.update()

        tilts = evaluated_bezier_tilts(curve)
        for point_index, (before, after) in enumerate(zip(baseline_tilts, tilts)):
            expected = before + angle if point_index == control_index else before
            assert_scalar_close(after, expected)

        pose = evaluated_pose_matrices(armature, names)
        if max(
            (before.translation - after.translation).length
            for before, after in zip(baseline_pose, pose)
        ) > 2.0e-5:
            raise AssertionError(
                f"Control {control_index} Tilt changed evaluated bone positions"
            )
        profile = tuple(
            signed_pose_roll_delta(before, after)
            for before, after in zip(baseline_pose, pose)
        )
        profiles.append(profile)
        dominant = max(range(len(profile)), key=lambda index: abs(profile[index]))
        dominant_bones.append(dominant)
        expected_position = (
            control_index * (len(names) - 1) / (len(controls) - 1)
        )
        if abs(dominant - expected_position) > 1.0:
            raise AssertionError(
                f"Control {control_index} chiefly rolled bone {dominant}; "
                f"expected it near chain position {expected_position}"
            )
        if abs(profile[dominant]) < angle * 0.55:
            raise AssertionError(
                f"Control {control_index} did not materially roll its chain region"
            )

        control.rotation_euler.y = 0.0
        bpy.context.view_layer.update()
        assert_matrices_close(
            evaluated_pose_matrices(armature, names),
            baseline_pose,
            tolerance=5.0e-5,
        )

    if dominant_bones[0] != 0 or dominant_bones[-1] != len(names) - 1:
        raise AssertionError(
            f"Endpoint controls mapped to {dominant_bones[0]} and {dominant_bones[-1]}"
        )
    if dominant_bones != sorted(dominant_bones):
        raise AssertionError(
            f"Control influence did not progress root-to-tip: {dominant_bones}"
        )
    center_profile = profiles[len(controls) // 2]
    if (
        abs(center_profile[len(names) // 2]) < angle * 0.8
        or max(abs(center_profile[0]), abs(center_profile[-1])) > math.radians(1.0)
    ):
        raise AssertionError("The middle control's Roll was not localized around mid-chain")

    # Multiple authored controls must blend along the chain rather than all
    # controls driving every bone uniformly.
    controls[1].rotation_euler.y = angle
    controls[-2].rotation_euler.y = -angle
    bpy.context.view_layer.update()
    blended_pose = evaluated_pose_matrices(armature, names)
    blended = tuple(
        signed_pose_roll_delta(before, after)
        for before, after in zip(baseline_pose, blended_pose)
    )
    if blended[1] < math.radians(10.0) or blended[-2] > -math.radians(10.0):
        raise AssertionError(
            "Opposite interior controls did not produce root-side/tip-side Roll interpolation"
        )
    blended_tilts = evaluated_bezier_tilts(curve)
    for index, (before, after) in enumerate(zip(baseline_tilts, blended_tilts)):
        expected = before + (angle if index == 1 else -angle if index == 3 else 0.0)
        assert_scalar_close(after, expected)


def test_legacy_rotation_tilt_upgrade_preserves_current_pose():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=7)
    result, settings = build_legacy_without_rotation_tilt(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        settings.active_rig_id,
    )
    if inventory["rotation_tilt_state"] != "MISSING":
        raise AssertionError("The legacy fixture unexpectedly contains Rotation Tilt")
    legacy_hooks = install_legacy_uuid_hook_names(
        curve,
        settings.active_rig_id,
    )
    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        settings.active_rig_id,
    )
    if inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_LEGACY:
        raise AssertionError("The missing-driver legacy Hook fixture was not recognized")

    # Preserve a meaningful already-posed legacy setup: Hook translation,
    # handle rotation, and artist-authored static Tilt all need to survive the
    # in-place migration without a visible jump.
    controls[2].location += Vector((0.0, 0.37, -0.16))
    controls[1].rotation_mode = "XYZ"
    controls[1].rotation_euler.z += math.radians(19.0)
    static_tilts = tuple(math.radians(value) for value in (-7.0, 3.0, 11.0, -5.0, 2.0))
    for point, value in zip(curve.data.splines[0].bezier_points, static_tilts):
        point.tilt = value
    curve.data.update_tag()
    bpy.context.view_layer.update()

    object_ids = (curve.as_pointer(), *(control.as_pointer() for control in controls))
    world_before = tuple(control.matrix_world.copy() for control in controls)
    raw_before = tuple(
        (
            point.co.copy(),
            point.handle_left.copy(),
            point.handle_right.copy(),
            float(point.tilt),
        )
        for point in curve.data.splines[0].bezier_points
    )
    curve_before = evaluated_curve_vertices(curve)
    pose_before = evaluated_pose_matrices(armature, names)
    context_before = (
        bpy.context.mode,
        bpy.context.view_layer.objects.active,
        tuple(bpy.context.selected_objects),
    )

    if bpy.ops.character_designer.spline_ik_enable_rotation_tilt() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    upgraded_curve, upgraded_controls = owned_curve_and_controls(settings)
    if object_ids != (
        upgraded_curve.as_pointer(),
        *(control.as_pointer() for control in upgraded_controls),
    ):
        raise AssertionError("Legacy upgrade replaced generated object identities")
    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        settings.active_rig_id,
    )
    if inventory["rotation_tilt_state"] != "ENABLED":
        raise AssertionError(inventory["rotation_tilt_detail"])
    if inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_CURRENT:
        raise AssertionError("Legacy Enable did not clean UUID-bearing Hook names")
    if inventory["hook_modifiers"] != legacy_hooks:
        raise AssertionError("Legacy Enable replaced Hook modifier identities")
    if [modifier.name for modifier in legacy_hooks] != [
        f"CD Spline IK Hook {index + 1:02d}" for index in range(len(legacy_hooks))
    ]:
        raise AssertionError("Legacy Enable did not assign the readable Hook names")

    # The upgrade intentionally changes only the visible controller orientation:
    # local Y keeps the exact hooked Bezier tangent, while X/Z are re-zeroed to
    # the evaluated strap width/front frame.  Position, Hook result, Curve, and
    # bones remain unchanged.
    for control, old_world in zip(upgraded_controls, world_before):
        assert_vector_close(
            control.matrix_world.translation,
            old_world.translation,
            tolerance=2.0e-5,
        )
        old_y = Vector(old_world.col[1].xyz).normalized()
        new_y = Vector(control.matrix_world.col[1].xyz).normalized()
        if old_y.dot(new_y) < 1.0 - 1.0e-5:
            raise AssertionError("Control-axis alignment changed the exact Bezier tangent axis")
        if not control.show_axis:
            raise AssertionError("Aligned controls do not reveal their local axes")
        if matrix_signature(control.matrix_basis) != matrix_signature(Matrix.Identity(4)):
            raise AssertionError("Aligned control channels did not start from a zero basis")
    if spline_ik_setup._control_frame_state(
        upgraded_curve,
        upgraded_controls,
        settings.active_rig_id,
    )[0] != "ENABLED":
        raise AssertionError("The upgraded rig did not record its Curve/strap axis frame")
    assert_vector_sequences_close(
        evaluated_curve_vertices(upgraded_curve),
        curve_before,
        tolerance=2.0e-5,
    )
    assert_matrices_close(
        evaluated_pose_matrices(armature, names),
        pose_before,
        tolerance=1.0e-4,
    )
    aligned_tilts = tuple(
        float(point.tilt) for point in upgraded_curve.data.splines[0].bezier_points
    )
    if not any(
        abs(actual - original) > math.radians(0.1)
        for actual, original in zip(aligned_tilts, static_tilts)
    ):
        raise AssertionError("Curve Tilt baselines were not aligned to the strap-front frame")
    for point, (co, left, right, _old_tilt), expected_tilt in zip(
        upgraded_curve.data.splines[0].bezier_points,
        raw_before,
        aligned_tilts,
    ):
        assert_vector_close(point.co, co)
        assert_vector_close(point.handle_left, left)
        assert_vector_close(point.handle_right, right)
        assert_scalar_close(point.tilt, expected_tilt)
    for actual, expected in zip(evaluated_bezier_tilts(upgraded_curve), aligned_tilts):
        assert_scalar_close(actual, expected)
    if context_before != (
        bpy.context.mode,
        bpy.context.view_layer.objects.active,
        tuple(bpy.context.selected_objects),
    ):
        raise AssertionError("Legacy upgrade changed mode, active object, or selection")

    # The upgraded legacy rig must immediately gain both visible curve Tilt
    # and evaluated bone Roll from the same local-Y gesture.
    twist = math.radians(21.0)
    pose_at_baseline = evaluated_pose_matrices(armature, names)
    tilt_at_baseline = evaluated_bezier_tilts(upgraded_curve)
    upgraded_controls[2].rotation_euler.y += twist
    bpy.context.view_layer.update()
    upgraded_tilts = evaluated_bezier_tilts(upgraded_curve)
    for index, (before, after) in enumerate(zip(tilt_at_baseline, upgraded_tilts)):
        assert_scalar_close(after, before + (twist if index == 2 else 0.0))
    upgraded_pose = evaluated_pose_matrices(armature, names)
    if max(
        abs(signed_pose_roll_delta(before, after))
        for before, after in zip(pose_at_baseline, upgraded_pose)
    ) < math.radians(10.0):
        raise AssertionError("The upgraded legacy control did not roll the evaluated chain")


def test_enabled_v1_rotation_tilt_upgrades_axes_in_place():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=7)
    result, settings = build_legacy_without_rotation_tilt(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = install_v1_rotation_tilt(armature, names, settings)
    rig_id = settings.active_rig_id
    legacy_hooks = install_legacy_uuid_hook_names(curve, rig_id)

    # A real 0.12 rig may already contain a combined swing/twist pose.  The
    # migration must absorb that evaluated result into the new zero point.
    controls[0].rotation_euler = tuple(
        math.radians(value) for value in (13.0, 24.0, -17.0)
    )
    controls[2].rotation_euler = tuple(
        math.radians(value) for value in (-21.0, -16.0, 28.0)
    )
    controls[2].location += Vector((0.0, 0.23, -0.11))
    bpy.context.view_layer.update()

    expected_plan = spline_ik_setup._evaluated_control_frame_plan(
        bpy.context,
        curve,
        controls,
    )
    object_pointers = (curve.as_pointer(), *(control.as_pointer() for control in controls))
    tilt_registry = json.loads(curve.data[spline_ik_setup.TILT_DRIVER_REGISTRY_KEY])
    roll_registry = json.loads(curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY])
    curve_driver_pointers = tuple(
        spline_ik_setup._driver_for_path(curve.data, record["data_path"]).as_pointer()
        for record in tilt_registry
    )
    roll_driver_pointers = tuple(
        spline_ik_setup._armature_driver_for_path(
            armature,
            record["data_path"],
        ).as_pointer()
        for record in roll_registry
    )
    curve_before = evaluated_curve_vertices(curve)
    tilts_before = evaluated_bezier_tilts(curve)
    pose_before = evaluated_pose_matrices(armature, names)
    control_positions_before = tuple(control.matrix_world.translation.copy() for control in controls)

    if bpy.ops.character_designer.spline_ik_enable_rotation_tilt() != {"FINISHED"}:
        raise AssertionError(settings.last_message)

    migrated_curve, migrated_controls = owned_curve_and_controls(settings)
    if object_pointers != (
        migrated_curve.as_pointer(),
        *(control.as_pointer() for control in migrated_controls),
    ):
        raise AssertionError("v1 axis migration replaced generated object identities")
    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        rig_id,
    )
    if (
        inventory["rotation_tilt_state"] != "ENABLED"
        or inventory["control_frame_state"] != "ENABLED"
        or inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_CURRENT
        or spline_ik_setup._rotation_tilt_contract_version(curve.data)
        != spline_ik_setup.TILT_DRIVER_VERSION
    ):
        raise AssertionError(
            inventory["rotation_tilt_detail"] or inventory["control_frame_detail"]
        )
    if inventory["hook_modifiers"] != legacy_hooks:
        raise AssertionError("v1 axis migration replaced Hook modifier identities")
    if any(rig_id in modifier.name for modifier in legacy_hooks):
        raise AssertionError("v1 axis migration left the UUID in visible Hook names")

    migrated_tilt_registry = json.loads(
        curve.data[spline_ik_setup.TILT_DRIVER_REGISTRY_KEY]
    )
    migrated_roll_registry = json.loads(
        curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY]
    )
    if curve_driver_pointers != tuple(
        spline_ik_setup._driver_for_path(curve.data, record["data_path"]).as_pointer()
        for record in migrated_tilt_registry
    ):
        raise AssertionError("v1 axis migration replaced Curve driver FCurves")
    if roll_driver_pointers != tuple(
        spline_ik_setup._armature_driver_for_path(
            armature,
            record["data_path"],
        ).as_pointer()
        for record in migrated_roll_registry
    ):
        raise AssertionError("v1 axis migration replaced bone Roll driver FCurves")
    for control, position, target_frame in zip(
        migrated_controls,
        control_positions_before,
        expected_plan["frames"],
    ):
        assert_vector_close(control.matrix_world.translation, position, tolerance=2.0e-5)
        if control.rotation_mode != "YXZ" or not control.show_axis:
            raise AssertionError("Migrated controls do not expose the YXZ local-axis contract")
        if matrix_signature(control.matrix_basis) != matrix_signature(Matrix.Identity(4)):
            raise AssertionError("Migrated v1 control did not start from zero local channels")
        for axis_index in range(3):
            actual_axis = Vector(control.matrix_world.col[axis_index].xyz).normalized()
            expected_axis = Vector(target_frame.col[axis_index].xyz).normalized()
            if actual_axis.dot(expected_axis) < 1.0 - 1.0e-5:
                raise AssertionError("Migrated control axis does not match the strap frame")
    assert_vector_sequences_close(
        evaluated_curve_vertices(curve),
        curve_before,
        tolerance=2.0e-5,
    )
    for actual, expected in zip(evaluated_bezier_tilts(curve), tilts_before):
        assert_scalar_close(actual, expected)
    assert_matrices_close(
        evaluated_pose_matrices(armature, names),
        pose_before,
        tolerance=1.0e-4,
    )

    # X/Z now bend only.  They must not leak into Curve Tilt or the driven
    # PoseBone Y channels even when combined.
    middle = migrated_controls[2]
    before_swing_curve = evaluated_curve_vertices(curve)
    before_swing_tilts = evaluated_bezier_tilts(curve)
    before_swing_bone_y = tuple(
        float(armature.pose.bones[name].rotation_euler.y) for name in names
    )
    middle.rotation_euler.x = math.radians(19.0)
    middle.rotation_euler.z = math.radians(-27.0)
    bpy.context.view_layer.update()
    if max(
        (after - before).length
        for before, after in zip(before_swing_curve, evaluated_curve_vertices(curve))
    ) < 1.0e-3:
        raise AssertionError("Migrated X/Z controls no longer bend the Curve")
    for actual, expected in zip(evaluated_bezier_tilts(curve), before_swing_tilts):
        assert_scalar_close(actual, expected)
    after_swing_bone_y = tuple(
        float(armature.pose.bones[name].rotation_euler.y) for name in names
    )
    for actual, expected in zip(after_swing_bone_y, before_swing_bone_y):
        assert_scalar_close(actual, expected)

    middle.rotation_euler.x = 0.0
    middle.rotation_euler.z = 0.0
    bpy.context.view_layer.update()
    before_twist = evaluated_bezier_tilts(curve)
    pose_before_twist = evaluated_pose_matrices(armature, names)
    twist = math.radians(18.0)
    middle.rotation_euler.y = twist
    bpy.context.view_layer.update()
    for index, (before, after) in enumerate(
        zip(before_twist, evaluated_bezier_tilts(curve))
    ):
        assert_scalar_close(after, before + (twist if index == 2 else 0.0))
    if max(
        abs(signed_pose_roll_delta(before, after))
        for before, after in zip(pose_before_twist, evaluated_pose_matrices(armature, names))
    ) < math.radians(8.0):
        raise AssertionError("Migrated local Y no longer rolls the evaluated strap chain")


def test_enabled_v1_axis_migration_failure_restores_v1_contract():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=7)
    result, settings = build_legacy_without_rotation_tilt(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = install_v1_rotation_tilt(armature, names, settings)
    legacy_hooks = install_legacy_uuid_hook_names(curve, settings.active_rig_id)
    hook_names_before = tuple(modifier.name for modifier in legacy_hooks)
    controls[1].rotation_euler = tuple(
        math.radians(value) for value in (9.0, 17.0, -22.0)
    )
    controls[3].rotation_euler = tuple(
        math.radians(value) for value in (-14.0, -11.0, 26.0)
    )
    bpy.context.view_layer.update()
    registries_before = (
        curve[spline_ik_setup.HOOK_REGISTRY_KEY],
        curve.data[spline_ik_setup.TILT_DRIVER_REGISTRY_KEY],
        curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY],
        curve.data.get(spline_ik_setup.CONTROL_FRAME_REGISTRY_KEY),
    )
    curve_drivers_before = driver_signature(curve.data)
    armature_drivers_before = driver_signature(armature)
    control_before = tuple(
        (
            control.matrix_parent_inverse.copy(),
            control.matrix_basis.copy(),
            control.matrix_world.copy(),
            control.rotation_mode,
            bool(control.show_axis),
        )
        for control in controls
    )
    hooks = spline_ik_setup._generated_hook_modifiers(
        curve,
        controls,
        settings.active_rig_id,
    )
    hook_before = tuple(hook.matrix_inverse.copy() for hook in hooks)
    curve_before = evaluated_curve_vertices(curve)
    tilts_before = evaluated_bezier_tilts(curve)
    pose_before = evaluated_pose_matrices(armature, names)

    original_write = spline_ik_setup._write_control_frame_registry

    def fail_after_driver_conversion(*_args, **_kwargs):
        raise RuntimeError("injected v1 migration commit failure")

    spline_ik_setup._write_control_frame_registry = fail_after_driver_conversion
    try:
        result = cancelled_result(
            bpy.ops.character_designer.spline_ik_enable_rotation_tilt
        )
    finally:
        spline_ik_setup._write_control_frame_registry = original_write
    if result != {"CANCELLED"}:
        raise AssertionError("Injected v1 migration failure did not cancel")

    if registries_before != (
        curve[spline_ik_setup.HOOK_REGISTRY_KEY],
        curve.data[spline_ik_setup.TILT_DRIVER_REGISTRY_KEY],
        curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY],
        curve.data.get(spline_ik_setup.CONTROL_FRAME_REGISTRY_KEY),
    ):
        raise AssertionError("Failed v1 migration did not restore its registries")
    if tuple(modifier.name for modifier in legacy_hooks) != hook_names_before:
        raise AssertionError("Failed v1 migration did not restore UUID-bearing Hook names")
    if (
        driver_signature(curve.data) != curve_drivers_before
        or driver_signature(armature) != armature_drivers_before
    ):
        raise AssertionError("Failed v1 migration did not restore its driver contract")
    for control, snapshot in zip(controls, control_before):
        assert_matrices_close(
            (control.matrix_parent_inverse, control.matrix_basis, control.matrix_world),
            snapshot[:3],
            tolerance=1.0e-6,
        )
        if (control.rotation_mode, bool(control.show_axis)) != snapshot[3:]:
            raise AssertionError("Failed v1 migration changed a control mode or axis display")
    assert_matrices_close(
        tuple(hook.matrix_inverse for hook in hooks),
        hook_before,
        tolerance=1.0e-6,
    )
    assert_vector_sequences_close(
        evaluated_curve_vertices(curve),
        curve_before,
        tolerance=2.0e-5,
    )
    for actual, expected in zip(evaluated_bezier_tilts(curve), tilts_before):
        assert_scalar_close(actual, expected, tolerance=1.0e-6)
    assert_matrices_close(
        evaluated_pose_matrices(armature, names),
        pose_before,
        tolerance=1.0e-4,
    )
    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        settings.active_rig_id,
    )
    if (
        inventory["rotation_tilt_state"] != "ENABLED"
        or inventory["control_frame_state"] != "MISSING"
        or inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_LEGACY
        or spline_ik_setup._rotation_tilt_contract_version(curve.data)
        != spline_ik_setup.LEGACY_DRIVER_VERSION
    ):
        raise AssertionError("Failed v1 migration did not restore the v1 ownership contract")


def test_enabled_rig_hook_name_cleanup_is_atomic():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    rig_id = settings.active_rig_id
    legacy_hooks = install_legacy_uuid_hook_names(curve, rig_id)

    driven_path = f'modifiers["{legacy_hooks[0].name}"].strength'
    hook_driver = curve.driver_add(driven_path)
    hook_driver.driver.type = "SCRIPTED"
    hook_driver.driver.expression = "1.0"
    bpy.context.view_layer.update()
    hook_driver_pointer = hook_driver.as_pointer()
    hook_pointers = tuple(modifier.as_pointer() for modifier in legacy_hooks)

    def cleanup_signature():
        return (
            snapshot_counts(),
            tuple(
                (
                    modifier.as_pointer(),
                    modifier.name,
                    modifier.type,
                    modifier.object.as_pointer() if modifier.object else 0,
                    tuple(modifier.vertex_indices),
                    matrix_signature(modifier.matrix_inverse),
                    round(float(modifier.strength), 7),
                )
                for modifier in curve.modifiers
            ),
            curve[spline_ik_setup.HOOK_REGISTRY_KEY],
            driver_signature(curve),
            tuple(matrix_signature(matrix) for matrix in evaluated_pose_matrices(armature, names)),
            tuple(
                tuple(round(value, 7) for value in vertex)
                for vertex in evaluated_curve_vertices(curve)
            ),
            (
                bpy.context.mode,
                bpy.context.view_layer.objects.active.as_pointer()
                if bpy.context.view_layer.objects.active is not None
                else 0,
                tuple(sorted(obj.as_pointer() for obj in bpy.context.selected_objects)),
            ),
        )

    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        rig_id,
    )
    if (
        inventory["rotation_tilt_state"] != "ENABLED"
        or inventory["control_frame_state"] != "ENABLED"
        or inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_LEGACY
    ):
        raise AssertionError("The enabled legacy-name cleanup fixture is invalid")

    # An unregistered short name on the same Curve must be treated as a
    # collision/foreign modifier, never silently suffixed or adopted.
    foreign = curve.modifiers.new("CD Spline IK Hook 01", type="HOOK")
    foreign.object = controls[0]
    foreign.vertex_indices_set((0, 1, 2))
    collision_before = cleanup_signature()
    if cancelled_result(
        bpy.ops.character_designer.spline_ik_enable_rotation_tilt
    ) != {"CANCELLED"}:
        raise AssertionError("Hook cleanup accepted an unregistered short-name collision")
    if cleanup_signature() != collision_before:
        raise AssertionError("Refused Hook-name collision changed the rig")
    curve.modifiers.remove(foreign)
    bpy.context.view_layer.update()

    # A commit fault after Blender has renamed the modifiers must restore the
    # raw registry, names, modifier-driver path, and every evaluated result.
    failure_before = cleanup_signature()
    original_write = spline_ik_setup._write_hook_registry

    def fail_hook_registry_commit(*_args, **_kwargs):
        raise RuntimeError("injected Hook-name registry failure")

    spline_ik_setup._write_hook_registry = fail_hook_registry_commit
    try:
        cleanup_result = cancelled_result(
            bpy.ops.character_designer.spline_ik_enable_rotation_tilt
        )
    finally:
        spline_ik_setup._write_hook_registry = original_write
    if cleanup_result != {"CANCELLED"}:
        raise AssertionError("Injected Hook-name cleanup failure did not cancel")
    if cleanup_signature() != failure_before:
        raise AssertionError("Failed Hook-name cleanup did not restore the rig exactly")
    if hook_driver.as_pointer() != hook_driver_pointer or hook_driver.data_path != driven_path:
        raise AssertionError("Failed Hook-name cleanup did not restore its modifier driver")

    if bpy.ops.character_designer.spline_ik_enable_rotation_tilt() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        rig_id,
    )
    expected_names = tuple(
        f"CD Spline IK Hook {index + 1:02d}" for index in range(len(legacy_hooks))
    )
    if inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_CURRENT:
        raise AssertionError("Enabled v2 rig did not finish Hook-name cleanup")
    if tuple(modifier.name for modifier in legacy_hooks) != expected_names:
        raise AssertionError("Enabled v2 rig did not receive exact readable Hook names")
    if tuple(modifier.as_pointer() for modifier in legacy_hooks) != hook_pointers:
        raise AssertionError("Hook-name cleanup replaced a modifier")
    expected_driver_path = f'modifiers["{expected_names[0]}"].strength'
    if (
        hook_driver.as_pointer() != hook_driver_pointer
        or hook_driver.data_path != expected_driver_path
    ):
        raise AssertionError("Blender did not preserve the modifier driver across cleanup")
    registry = json.loads(curve[spline_ik_setup.HOOK_REGISTRY_KEY])
    if any(
        record["name"] != expected_names[index] or record["rig_id"] != rig_id
        for index, record in enumerate(registry)
    ):
        raise AssertionError("Hook cleanup changed internal ownership or registry names")

    # The manual Curve-object backup used by Remove cannot losslessly recreate
    # arbitrary artist AnimData. Refuse before deletion instead of claiming a
    # rollback that silently drops this modifier driver.
    remove_before = cleanup_signature()
    if cancelled_result(
        lambda: bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT")
    ) != {"CANCELLED"}:
        raise AssertionError("Remove accepted a generated Curve with Object drivers")
    if cleanup_signature() != remove_before:
        raise AssertionError("Refused Remove changed or lost Curve Object animation")
    if settings.last_level != "WARNING":
        raise AssertionError("Artist-animation Remove refusal should be non-destructive warning")


def test_legacy_rotation_tilt_upgrade_refuses_artist_conflicts_atomically():
    hazards = (
        "CONTROL_ANIMATION",
        "CONTROL_CONSTRAINT",
        "CONTROL_DELTA",
        "CURVE_TILT_ACTION",
        "BONE_ROLL_ACTION",
    )
    for hazard in hazards:
        reset_scene()
        armature, names, _branch = make_armature(bone_count=5)
        result, settings = build_legacy_without_rotation_tilt(armature, names, points=5)
        if result != {"FINISHED"}:
            raise AssertionError(settings.last_message)
        curve, controls = owned_curve_and_controls(settings)

        if hazard == "CONTROL_ANIMATION":
            controls[1].keyframe_insert(data_path="location", frame=1.0)
        elif hazard == "CONTROL_CONSTRAINT":
            target = bpy.data.objects.new("ArtistConstraintTarget", None)
            bpy.context.scene.collection.objects.link(target)
            target.location = (1.0, -0.5, 0.25)
            constraint = controls[1].constraints.new(type="COPY_LOCATION")
            constraint.name = "Artist Copy Location"
            constraint.target = target
            constraint.influence = 0.35
        elif hazard == "CONTROL_DELTA":
            controls[1].delta_rotation_euler.y = math.radians(8.0)
        elif hazard == "CURVE_TILT_ACTION":
            path = spline_ik_setup._tilt_data_path(1)
            curve.data.splines[0].bezier_points[1].tilt = math.radians(6.0)
            curve.data.keyframe_insert(data_path=path, frame=1.0, group="Artist Tilt")
        elif hazard == "BONE_ROLL_ACTION":
            pose_bone = armature.pose.bones[names[2]]
            pose_bone.rotation_mode = "XYZ"
            pose_bone.rotation_euler.y = math.radians(4.0)
            pose_bone.keyframe_insert(
                data_path="rotation_euler",
                index=1,
                frame=1.0,
                group="Artist Bone Roll",
            )
        bpy.context.view_layer.update()

        before = legacy_conflict_signature(
            armature,
            names,
            settings,
            curve,
            controls,
        )
        if cancelled_result(
            bpy.ops.character_designer.spline_ik_enable_rotation_tilt
        ) != {"CANCELLED"}:
            raise AssertionError(f"Legacy upgrade accepted {hazard}")
        after = legacy_conflict_signature(
            armature,
            names,
            settings,
            curve,
            controls,
        )
        if after != before:
            raise AssertionError(f"Refused {hazard} upgrade changed the rig")
        if (
            spline_ik_setup.TILT_DRIVER_REGISTRY_KEY in curve.data
            or spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY in curve.data
        ):
            raise AssertionError(f"Refused {hazard} upgrade left a partial registry")


def test_control_frame_upgrade_failure_restores_hooks_and_pose():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=7)
    result, settings = build_legacy_without_rotation_tilt(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    rig_id = settings.active_rig_id
    install_legacy_uuid_hook_names(curve, rig_id)
    hooks = spline_ik_setup._generated_hook_modifiers(curve, controls, rig_id)
    hook_names_before = tuple(hook.name for hook in hooks)
    hook_registry_before = curve[spline_ik_setup.HOOK_REGISTRY_KEY]

    control_before = tuple(
        (
            matrix_signature(control.matrix_parent_inverse),
            matrix_signature(control.matrix_basis),
            matrix_signature(control.matrix_world),
            control.rotation_mode,
            bool(control.show_axis),
        )
        for control in controls
    )
    hook_before = tuple(matrix_signature(hook.matrix_inverse) for hook in hooks)
    tilt_before = tuple(
        float(point.tilt) for point in curve.data.splines[0].bezier_points
    )
    curve_before = evaluated_curve_vertices(curve)
    pose_before = evaluated_pose_matrices(armature, names)
    original_add = spline_ik_setup._add_rotation_tilt_driver
    calls = {"count": 0}

    def fail_after_first_driver(*args, **kwargs):
        fcurve = original_add(*args, **kwargs)
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("injected failure after frame alignment")
        return fcurve

    spline_ik_setup._add_rotation_tilt_driver = fail_after_first_driver
    try:
        result = cancelled_result(
            bpy.ops.character_designer.spline_ik_enable_rotation_tilt
        )
    finally:
        spline_ik_setup._add_rotation_tilt_driver = original_add
    if result != {"CANCELLED"}:
        raise AssertionError("Injected control-frame upgrade failure did not cancel")

    if control_before != tuple(
        (
            matrix_signature(control.matrix_parent_inverse),
            matrix_signature(control.matrix_basis),
            matrix_signature(control.matrix_world),
            control.rotation_mode,
            bool(control.show_axis),
        )
        for control in controls
    ):
        raise AssertionError("Failed frame upgrade did not restore controller transforms")
    if hook_before != tuple(matrix_signature(hook.matrix_inverse) for hook in hooks):
        raise AssertionError("Failed frame upgrade did not restore Hook inverses")
    if tuple(hook.name for hook in hooks) != hook_names_before:
        raise AssertionError("Failed frame upgrade did not restore legacy Hook names")
    if curve[spline_ik_setup.HOOK_REGISTRY_KEY] != hook_registry_before:
        raise AssertionError("Failed frame upgrade did not restore the Hook registry")
    if tilt_before != tuple(
        float(point.tilt) for point in curve.data.splines[0].bezier_points
    ):
        raise AssertionError("Failed frame upgrade did not restore Curve Tilt")
    assert_vector_sequences_close(
        evaluated_curve_vertices(curve),
        curve_before,
        tolerance=2.0e-5,
    )
    assert_matrices_close(
        evaluated_pose_matrices(armature, names),
        pose_before,
        tolerance=1.0e-4,
    )
    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        rig_id,
    )
    if (
        inventory["rotation_tilt_state"] != "MISSING"
        or inventory["control_frame_state"] != "MISSING"
        or inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_LEGACY
    ):
        raise AssertionError("Failed frame upgrade left partial ownership metadata")


def test_no_stretch_constraint_contract_and_evaluation():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, _controls = owned_curve_and_controls(settings)
    tip = armature.pose.bones[names[-1]]
    owned = [
        constraint
        for constraint in tip.constraints
        if (
            spline_ik_setup._constraint_record(tip, constraint, armature, names) or {}
        ).get("rig_id")
        == settings.active_rig_id
    ]
    if len(owned) != 1:
        raise AssertionError("Expected exactly one owned Spline IK constraint")
    constraint = owned[0]
    if (
        constraint.type != "SPLINE_IK"
        or constraint.target is not curve
        or constraint.chain_count != len(names)
        or constraint.y_scale_mode != "NONE"
        or constraint.xz_scale_mode != "NONE"
        or constraint.use_curve_radius
    ):
        raise AssertionError("Generated Spline IK does not satisfy the no-stretch contract")

    bpy.context.view_layer.update()
    original_scales = {
        name: armature.evaluated_get(bpy.context.evaluated_depsgraph_get())
        .pose.bones[name]
        .matrix.to_3x3()
        .col[1]
        .length
        for name in names
    }
    # Move the final controller farther away, increasing curve length. The
    # evaluated bone Y scales must remain at their original values.
    _curve, controls = owned_curve_and_controls(settings)
    controls[-1].location += Vector((1.2, 0.2, 0.0))
    bpy.context.view_layer.update()
    evaluated = armature.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for name in names:
        after = evaluated.pose.bones[name].matrix.to_3x3().col[1].length
        if abs(after - original_scales[name]) > 1.0e-4:
            raise AssertionError(f"Bone {name} stretched from {original_scales[name]} to {after}")


def make_foreign_curve(name="UserCurve"):
    curve_data = bpy.data.curves.new(f"{name}_Data", type="CURVE")
    curve_data.dimensions = "3D"
    spline = curve_data.splines.new(type="POLY")
    spline.points.add(1)
    spline.points[0].co = (0.0, 0.0, 0.0, 1.0)
    spline.points[1].co = (3.0, 0.0, 0.0, 1.0)
    curve = bpy.data.objects.new(name, curve_data)
    bpy.context.scene.collection.objects.link(curve)
    curve["user_sentinel"] = "keep"
    return curve


def test_foreign_spline_ik_refusal_and_constraint_only_replace():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    foreign_curve = make_foreign_curve()
    tip = armature.pose.bones[names[-1]]
    foreign = tip.constraints.new(type="SPLINE_IK")
    foreign.name = "User Spline IK"
    foreign.target = foreign_curve
    foreign.chain_count = len(names)

    before = snapshot_counts()
    settings = capture(armature, names)
    settings.point_count = 5
    settings.replace_existing = False
    result = cancelled_result(bpy.ops.character_designer.spline_ik_build)
    if result != {"CANCELLED"}:
        raise AssertionError("A foreign Spline IK was replaced without explicit consent")
    after_refusal = snapshot_counts()
    if after_refusal != before or tip.constraints.get("User Spline IK") != foreign:
        print("REFUSAL COUNTS", before, after_refusal)
        raise AssertionError("Refused build left generated residue or edited the foreign constraint")

    settings.replace_existing = True
    if bpy.ops.character_designer.spline_ik_build() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if tip.constraints.get("User Spline IK") is not None:
        raise AssertionError("Explicit replace failed to remove the foreign constraint")
    if (
        bpy.data.objects.get(foreign_curve.name) is not foreign_curve
        or foreign_curve.get("user_sentinel") != "keep"
        or bpy.data.curves.get(foreign_curve.data.name) is not foreign_curve.data
        or foreign_curve.data.users != 1
        or [obj for obj in bpy.data.objects if getattr(obj, "data", None) is foreign_curve.data]
        != [foreign_curve]
    ):
        raise AssertionError(
            "Replacing a constraint deleted, copied, or edited its user-owned target"
        )


def test_select_and_remove_are_owner_scoped():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    curve_name = curve.name
    user = bpy.data.objects.new("User Empty", None)
    bpy.context.scene.collection.objects.link(user)
    user.empty_display_type = "SPHERE"
    user["user_sentinel"] = "keep"
    user.select_set(True)

    if bpy.ops.character_designer.spline_ik_select_controls() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    selected = {obj.name for obj in bpy.context.selected_objects}
    if selected != {control.name for control in controls} or user.select_get():
        raise AssertionError("Select Controls selected non-owned objects or omitted owned controls")

    rig_id = settings.active_rig_id
    # execute() avoids a UI confirm dialog while retaining the operator's
    # owner-scoped deletion implementation.
    if bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if owned_objects(rig_id):
        raise AssertionError("Remove Generated left owned objects behind")
    if bpy.data.objects.get(user.name) is not user or user.get("user_sentinel") != "keep":
        raise AssertionError("Remove Generated deleted or edited a user object")
    if bpy.data.objects.get(armature.name) is not armature:
        raise AssertionError("Remove Generated deleted the source Armature")
    if bpy.data.objects.get(curve_name) is not None:
        raise AssertionError("Remove Generated left its owned Curve object behind")


def test_select_and_reveal_controls_works_without_gui_areas():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    _curve, controls = owned_curve_and_controls(settings)
    if len(controls) != 5:
        raise AssertionError("Expected five controls before Select & Reveal")

    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    sentinel = bpy.data.objects.new("SelectRevealSentinel", None)
    bpy.context.scene.collection.objects.link(sentinel)
    sentinel.select_set(True)
    bpy.context.view_layer.objects.active = sentinel
    for control in controls:
        control.hide_select = True
        control.hide_viewport = True
        control.hide_set(True)

    # This suite runs with --background, so no VIEW_3D or OUTLINER area is
    # required for the selection/reveal core to succeed.
    if bpy.ops.character_designer.spline_ik_select_controls() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if set(bpy.context.selected_objects) != set(controls):
        raise AssertionError("Select & Reveal did not select exactly all owned controls")
    expected_active = controls[len(controls) // 2]
    if bpy.context.view_layer.objects.active is not expected_active:
        raise AssertionError("Select & Reveal did not make the middle control active")
    for control in controls:
        if control.hide_viewport or control.hide_select or control.hide_get():
            raise AssertionError(f"Select & Reveal left {control.name!r} hidden or unselectable")


def test_transaction_failure_leaves_no_residue():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    settings = capture(armature, names)
    before = snapshot_counts()
    original = spline_ik_setup._create_curve_and_controls

    def fail_after_partial_creation(
        context,
        source_armature,
        chain_names,
        samples,
        control_size,
        rig_id,
        created_objects,
        created_curves,
    ):
        data = bpy.data.curves.new("PartialCurveData", type="CURVE")
        created_curves.append(data)
        obj = bpy.data.objects.new("PartialCurve", data)
        created_objects.append(obj)
        context.scene.collection.objects.link(obj)
        raise RuntimeError("injected transaction failure")

    spline_ik_setup._create_curve_and_controls = fail_after_partial_creation
    try:
        result = cancelled_result(bpy.ops.character_designer.spline_ik_build)
    finally:
        spline_ik_setup._create_curve_and_controls = original
    if result != {"CANCELLED"}:
        raise AssertionError("Injected build failure did not cancel")
    if snapshot_counts() != before:
        raise AssertionError("Injected build failure left objects, Curves, or constraints behind")
    if bpy.data.objects.get("PartialCurve") or bpy.data.curves.get("PartialCurveData"):
        raise AssertionError("Rollback failed to remove explicitly tracked partial data")
    if settings.active_rig_id:
        raise AssertionError("Failed first build incorrectly published a rig id")


def test_malformed_constraint_registry_refuses_build_without_overwrite():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    settings = capture(armature, names, edit_mode=True)
    malformed = "{not valid constraint registry"
    tip = armature.pose.bones[names[-1]]
    tip[spline_ik_setup.CONSTRAINT_REGISTRY_KEY] = malformed
    before = snapshot_counts()
    result = cancelled_result(bpy.ops.character_designer.spline_ik_build)
    if result != {"CANCELLED"}:
        raise AssertionError("Build accepted a malformed constraint registry")
    if tip.get(spline_ik_setup.CONSTRAINT_REGISTRY_KEY) != malformed:
        raise AssertionError("Failed Build overwrote the malformed registry raw value")
    if snapshot_counts() != before or settings.active_rig_id:
        raise AssertionError("Malformed-registry refusal left generated residue")
    if bpy.context.object is not armature or bpy.context.mode != "EDIT_ARMATURE":
        raise AssertionError("Malformed-registry refusal failed to restore Edit Mode")


def test_edit_mode_build_restores_head_tail_and_body_selection_exactly():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    settings = capture(armature, names, edit_mode=True)
    edit_bones = armature.data.edit_bones
    requested = (
        (False, True, False),
        (False, False, True),
        (True, True, True),
        (False, True, True),
        (False, False, False),
    )
    for bone, flags in zip((edit_bones[name] for name in names), requested):
        bone.select, bone.select_head, bone.select_tail = flags
    edit_bones.active = edit_bones[names[1]]
    before = spline_ik_setup._snapshot_edit_bone_state(armature)
    settings.point_count = 5
    if bpy.ops.character_designer.spline_ik_build() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    after = spline_ik_setup._snapshot_edit_bone_state(armature)
    if bpy.context.object is not armature or bpy.context.mode != "EDIT_ARMATURE":
        raise AssertionError("Successful Edit-mode Build did not return to the same Armature")
    if after != before:
        raise AssertionError(f"Edit Bone selection changed: {before!r} -> {after!r}")


def test_remove_fault_injection_restores_complete_rig():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig_id = settings.active_rig_id
    original_constraint_delete = spline_ik_setup._delete_generated_constraint
    original_object_delete = spline_ik_setup._delete_generated_object
    original_curve_delete = spline_ik_setup._delete_generated_curve

    stages = ("CONSTRAINT", "FIRST_OBJECT", "LAST_OBJECT", "CURVE_AFTER_DELETE")
    for stage in stages:
        before = rig_signature(armature, names, rig_id)
        inventory = spline_ik_setup._rig_inventory(
            bpy.context,
            armature,
            names,
            rig_id,
        )
        object_calls = {"count": 0}
        object_total = len(inventory["objects"])

        def delete_constraint(owner, constraint):
            original_constraint_delete(owner, constraint)
            if stage == "CONSTRAINT":
                raise RuntimeError("injected failure after constraint deletion")

        def delete_object(obj):
            original_object_delete(obj)
            object_calls["count"] += 1
            if stage == "FIRST_OBJECT" and object_calls["count"] == 1:
                raise RuntimeError("injected failure after first object deletion")
            if stage == "LAST_OBJECT" and object_calls["count"] == object_total:
                raise RuntimeError("injected failure after last object deletion")

        def delete_curve(curve_data):
            original_curve_delete(curve_data)
            if stage == "CURVE_AFTER_DELETE":
                raise RuntimeError("injected failure after Curve Data deletion")

        spline_ik_setup._delete_generated_constraint = delete_constraint
        spline_ik_setup._delete_generated_object = delete_object
        spline_ik_setup._delete_generated_curve = delete_curve
        try:
            remove_result = cancelled_result(
                lambda: bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT")
            )
        finally:
            spline_ik_setup._delete_generated_constraint = original_constraint_delete
            spline_ik_setup._delete_generated_object = original_object_delete
            spline_ik_setup._delete_generated_curve = original_curve_delete
        if remove_result != {"CANCELLED"}:
            raise AssertionError(f"Injected {stage} removal failure did not cancel")
        if settings.active_rig_id != rig_id:
            raise AssertionError(f"Injected {stage} failure cleared the active rig id")
        after = rig_signature(armature, names, rig_id)
        if after != before:
            for key in before:
                if before[key] != after[key]:
                    print("REMOVE RESTORE DIFF", stage, key, before[key], after[key])
            raise AssertionError(f"Injected {stage} failure did not restore the rig exactly")
        if any(
            "RemovalBackup" in obj.name
            for obj in bpy.data.objects
            if obj is not armature
        ):
            raise AssertionError(f"Injected {stage} failure left a backup Object behind")

    if bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)


def test_remove_preserves_user_bone_channels_and_faults_restore_driven_pose():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig_id = settings.active_rig_id
    curve, controls = owned_curve_and_controls(settings)

    roll_registry = json.loads(
        curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY]
    )
    control_index = len(controls) // 2
    candidates = []
    for record in roll_registry:
        weight = next(
            (
                float(item["weight"])
                for item in record["coefficients"]
                if item["control_index"] == control_index
            ),
            0.0,
        )
        candidates.append((abs(weight), weight, record))
    _magnitude, driven_weight, driven_record = max(candidates, key=lambda item: item[0])
    if abs(driven_weight) < 0.25:
        raise AssertionError("The fixture has no meaningful middle-control bone Roll weight")
    driven_bone = armature.pose.bones[driven_record["bone"]]
    baseline_y = float(driven_record["baseline_y"])
    original_rotation_mode = driven_record["original_rotation_mode"]

    # Author a real post-build pose: the control contributes generated Y Roll,
    # while all other channels belong to the artist and must survive removal.
    twist = math.radians(27.0)
    controls[control_index].rotation_euler.y = twist
    user_location = Vector((0.13, -0.08, 0.05))
    user_scale = Vector((1.12, 0.91, 1.07))
    user_rotation_x = math.radians(13.0)
    user_rotation_z = math.radians(-17.0)
    driven_bone.location = user_location
    driven_bone.scale = user_scale
    driven_bone.rotation_euler.x = user_rotation_x
    driven_bone.rotation_euler.z = user_rotation_z
    bpy.context.view_layer.update()
    expected_driven_y = baseline_y + driven_weight * twist
    assert_scalar_close(driven_bone.rotation_euler.y, expected_driven_y)

    def current_channels():
        return (
            driven_bone.rotation_mode,
            tuple(round(value, 8) for value in driven_bone.location),
            tuple(round(value, 8) for value in driven_bone.scale),
            tuple(round(value, 8) for value in driven_bone.rotation_euler),
            matrix_signature(driven_bone.matrix_basis),
        )

    original_constraint_delete = spline_ik_setup._delete_generated_constraint
    original_object_delete = spline_ik_setup._delete_generated_object
    original_curve_delete = spline_ik_setup._delete_generated_curve
    stages = ("CONSTRAINT", "FIRST_OBJECT", "LAST_OBJECT", "CURVE_AFTER_DELETE")
    for stage in stages:
        curve, controls = owned_curve_and_controls(settings)
        inventory = spline_ik_setup._rig_inventory(
            bpy.context,
            armature,
            names,
            rig_id,
        )
        before_channels = current_channels()
        before_pose = evaluated_pose_matrices(armature, names)
        before_tilts = evaluated_bezier_tilts(curve)
        before_armature_drivers = driver_signature(armature)
        before_curve_drivers = driver_signature(curve.data)
        before_registry = curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY]
        object_calls = {"count": 0}
        object_total = len(inventory["objects"])

        def delete_constraint(owner, constraint):
            original_constraint_delete(owner, constraint)
            if stage == "CONSTRAINT":
                raise RuntimeError("injected failure after constraint deletion")

        def delete_object(obj):
            original_object_delete(obj)
            object_calls["count"] += 1
            if stage == "FIRST_OBJECT" and object_calls["count"] == 1:
                raise RuntimeError("injected failure after first object deletion")
            if stage == "LAST_OBJECT" and object_calls["count"] == object_total:
                raise RuntimeError("injected failure after last object deletion")

        def delete_curve(curve_data):
            original_curve_delete(curve_data)
            if stage == "CURVE_AFTER_DELETE":
                raise RuntimeError("injected failure after Curve Data deletion")

        spline_ik_setup._delete_generated_constraint = delete_constraint
        spline_ik_setup._delete_generated_object = delete_object
        spline_ik_setup._delete_generated_curve = delete_curve
        try:
            remove_result = cancelled_result(
                lambda: bpy.ops.character_designer.spline_ik_remove_generated(
                    "EXEC_DEFAULT"
                )
            )
        finally:
            spline_ik_setup._delete_generated_constraint = original_constraint_delete
            spline_ik_setup._delete_generated_object = original_object_delete
            spline_ik_setup._delete_generated_curve = original_curve_delete
        if remove_result != {"CANCELLED"}:
            raise AssertionError(f"Injected {stage} removal failure did not cancel")

        restored_curve, restored_controls = owned_curve_and_controls(settings)
        if current_channels() != before_channels:
            raise AssertionError(
                f"Injected {stage} removal did not restore current non-Y bone channels"
            )
        assert_matrices_close(
            evaluated_pose_matrices(armature, names),
            before_pose,
            tolerance=1.0e-4,
        )
        for actual, expected in zip(
            evaluated_bezier_tilts(restored_curve),
            before_tilts,
        ):
            assert_scalar_close(actual, expected)
        if (
            driver_signature(armature) != before_armature_drivers
            or driver_signature(restored_curve.data) != before_curve_drivers
            or restored_curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY]
            != before_registry
        ):
            raise AssertionError(f"Injected {stage} removal did not restore Roll drivers")

        # Prove recovery retargeted both Curve and bone drivers to the restored
        # control objects, rather than merely reproducing a static snapshot.
        restored_control = restored_controls[control_index]
        probe = math.radians(4.0)
        probe_tilts_before = evaluated_bezier_tilts(restored_curve)
        probe_pose_before = evaluated_pose_matrices(armature, names)
        restored_control.rotation_euler.y += probe
        bpy.context.view_layer.update()
        probe_tilts_after = evaluated_bezier_tilts(restored_curve)
        assert_scalar_close(
            probe_tilts_after[control_index],
            probe_tilts_before[control_index] + probe,
        )
        if max(
            abs(signed_pose_roll_delta(before, after))
            for before, after in zip(
                probe_pose_before,
                evaluated_pose_matrices(armature, names),
            )
        ) < math.radians(1.0):
            raise AssertionError(f"Injected {stage} recovery left inert bone Roll drivers")
        restored_control.rotation_euler.y -= probe
        bpy.context.view_layer.update()
        assert_matrices_close(
            evaluated_pose_matrices(armature, names),
            before_pose,
            tolerance=1.0e-4,
        )

    # A successful removal must neutralize only the generated Y Roll.  The
    # user's location, scale, X and Z rotations remain, and Blender's original
    # rotation mode is restored.
    if bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if driven_bone.rotation_mode != original_rotation_mode:
        raise AssertionError(
            f"Remove restored {driven_bone.rotation_mode}, expected {original_rotation_mode}"
        )
    _matrix_location, rotation, _matrix_scale = driven_bone.matrix_basis.decompose()
    neutral_euler = rotation.to_euler("XYZ")
    assert_vector_close(driven_bone.location, user_location)
    assert_vector_close(driven_bone.scale, user_scale)
    assert_scalar_close(neutral_euler.x, user_rotation_x)
    assert_scalar_close(neutral_euler.y, baseline_y)
    assert_scalar_close(neutral_euler.z, user_rotation_z)
    if spline_ik_setup._armature_driver_for_path(
        armature,
        driven_record["data_path"],
    ) is not None:
        raise AssertionError("Successful Remove left the generated Y Roll driver behind")


def test_v1_swing_drivers_remain_recoverable_and_removable():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build_legacy_without_rotation_tilt(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    rig_id = settings.active_rig_id
    curve, controls = install_v1_rotation_tilt(armature, names, settings)
    install_legacy_uuid_hook_names(curve, rig_id)

    inventory = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        rig_id,
    )
    if inventory["rotation_tilt_state"] != "ENABLED":
        raise AssertionError(inventory["rotation_tilt_detail"])
    if inventory["control_frame_state"] != "MISSING":
        raise AssertionError("The true v1 fixture unexpectedly has a frame registry")
    if inventory["hook_name_state"] != spline_ik_setup.HOOK_NAME_LEGACY:
        raise AssertionError("The true v1 fixture did not keep its UUID-bearing Hook names")

    original_delete = spline_ik_setup._delete_generated_constraint

    def fail_after_constraint(owner, constraint):
        original_delete(owner, constraint)
        raise RuntimeError("injected v1 recovery failure")

    spline_ik_setup._delete_generated_constraint = fail_after_constraint
    try:
        remove_result = cancelled_result(
            lambda: bpy.ops.character_designer.spline_ik_remove_generated(
                "EXEC_DEFAULT"
            )
        )
    finally:
        spline_ik_setup._delete_generated_constraint = original_delete
    if remove_result != {"CANCELLED"}:
        raise AssertionError("Injected v1 removal failure did not cancel")

    restored_curve, restored_controls = owned_curve_and_controls(settings)
    restored = spline_ik_setup._rig_inventory(
        bpy.context,
        armature,
        names,
        rig_id,
    )
    if restored["rotation_tilt_state"] != "ENABLED":
        raise AssertionError(restored["rotation_tilt_detail"])
    if restored["control_frame_state"] != "MISSING":
        raise AssertionError("Recovery silently added a v2 control-frame registry")
    if restored["hook_name_state"] != spline_ik_setup.HOOK_NAME_LEGACY:
        raise AssertionError("Recovery silently changed the v1 Hook names")
    restored_tilt = json.loads(
        restored_curve.data[spline_ik_setup.TILT_DRIVER_REGISTRY_KEY]
    )
    restored_roll = json.loads(
        restored_curve.data[spline_ik_setup.ROLL_DRIVER_REGISTRY_KEY]
    )
    if any(
        record["version"] != spline_ik_setup.LEGACY_DRIVER_VERSION
        for record in (*restored_tilt, *restored_roll)
    ):
        raise AssertionError("Recovery silently migrated the v1 driver registry")
    for record, control in zip(restored_tilt, restored_controls):
        driver = spline_ik_setup._driver_for_path(
            restored_curve.data,
            record["data_path"],
        )
        target = driver.driver.variables[0].targets[0]
        if (
            target.id is not control
            or target.rotation_mode != spline_ik_setup.LEGACY_DRIVER_ROTATION_MODE
        ):
            raise AssertionError("Recovery did not restore a working v1 Curve driver")

    if bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT") != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    if owned_objects(rig_id):
        raise AssertionError("Successful removal left v1 generated data behind")


def test_mixed_rotation_tilt_versions_are_invalid_and_non_mutating():
    for mixed_kind in ("WITHIN_TILT", "CURVE_V1_BONE_V2"):
        reset_scene()
        armature, names, _branch = make_armature(bone_count=5)
        result, settings = build(armature, names, points=5)
        if result != {"FINISHED"}:
            raise AssertionError(settings.last_message)
        curve, controls = owned_curve_and_controls(settings)
        tilt_registry = json.loads(
            curve.data[spline_ik_setup.TILT_DRIVER_REGISTRY_KEY]
        )
        records = tilt_registry[:1] if mixed_kind == "WITHIN_TILT" else tilt_registry
        for record in records:
            record["version"] = spline_ik_setup.LEGACY_DRIVER_VERSION
            fcurve = spline_ik_setup._driver_for_path(
                curve.data,
                record["data_path"],
            )
            fcurve.driver.variables[0].targets[0].rotation_mode = (
                spline_ik_setup.LEGACY_DRIVER_ROTATION_MODE
            )
        curve.data[spline_ik_setup.TILT_DRIVER_REGISTRY_KEY] = json.dumps(
            tilt_registry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        bpy.context.view_layer.update()

        inventory = spline_ik_setup._rig_inventory(
            bpy.context,
            armature,
            names,
            settings.active_rig_id,
        )
        if inventory["rotation_tilt_state"] != "INVALID":
            raise AssertionError(f"Accepted mixed driver versions: {mixed_kind}")
        before = rig_signature(
            armature,
            names,
            settings.active_rig_id,
        )
        if cancelled_result(
            bpy.ops.character_designer.spline_ik_enable_rotation_tilt
        ) != {"CANCELLED"}:
            raise AssertionError(f"Enable accepted mixed driver versions: {mixed_kind}")
        if cancelled_result(
            lambda: bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT")
        ) != {"CANCELLED"}:
            raise AssertionError(f"Remove accepted mixed driver versions: {mixed_kind}")
        after = rig_signature(
            armature,
            names,
            settings.active_rig_id,
        )
        if after != before:
            raise AssertionError(f"Refused mixed-version operation mutated: {mixed_kind}")


def test_existing_owned_build_is_refused_without_changes():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    first_rig_id = settings.active_rig_id
    first_curve, first_controls = owned_curve_and_controls(settings)
    before = snapshot_counts()
    before_pointers = {
        "curve": first_curve.as_pointer(),
        "controls": tuple(control.as_pointer() for control in first_controls),
        "constraint": armature.pose.bones[names[-1]].constraints[0].as_pointer(),
    }
    result = cancelled_result(bpy.ops.character_designer.spline_ik_build)
    if result != {"CANCELLED"}:
        raise AssertionError("Build silently rebuilt an already-owned rig")
    curve, controls = owned_curve_and_controls(settings)
    after_pointers = {
        "curve": curve.as_pointer(),
        "controls": tuple(control.as_pointer() for control in controls),
        "constraint": armature.pose.bones[names[-1]].constraints[0].as_pointer(),
    }
    if (
        snapshot_counts() != before
        or settings.active_rig_id != first_rig_id
        or after_pointers != before_pointers
    ):
        raise AssertionError("Refused owned rebuild changed the existing setup")


def test_duplicate_owned_object_and_curve_data_block_safe_remove():
    for duplicate_kind in ("OBJECT", "CURVE_DATA"):
        reset_scene()
        armature, names, _branch = make_armature(bone_count=5)
        result, settings = build(armature, names, points=5)
        if result != {"FINISHED"}:
            raise AssertionError(settings.last_message)
        curve, controls = owned_curve_and_controls(settings)
        rig_id = settings.active_rig_id
        if duplicate_kind == "OBJECT":
            duplicate = controls[2].copy()
            bpy.context.scene.collection.objects.link(duplicate)
        else:
            duplicate = curve.data.copy()
        before = snapshot_counts()
        before_owned = {
            obj.as_pointer()
            for obj in bpy.data.objects
            if obj.get(spline_ik_setup.RIG_ID_KEY) == rig_id
        }
        result = cancelled_result(
            lambda: bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT")
        )
        if result != {"CANCELLED"}:
            raise AssertionError(f"Remove accepted duplicate tagged {duplicate_kind}")
        after_owned = {
            obj.as_pointer()
            for obj in bpy.data.objects
            if obj.get(spline_ik_setup.RIG_ID_KEY) == rig_id
        }
        if snapshot_counts() != before or after_owned != before_owned:
            raise AssertionError(f"Unsafe {duplicate_kind} removal deleted partial data")
        if duplicate_kind == "OBJECT":
            if bpy.data.objects.get(duplicate.name) is not duplicate:
                raise AssertionError("Duplicate tagged object disappeared")
        elif bpy.data.curves.get(duplicate.name) is not duplicate:
            raise AssertionError("Duplicate tagged Curve Data disappeared")


def make_second_scene_link(armature, name="SharedScene"):
    second = bpy.data.scenes.new(name)
    second.collection.objects.link(armature)
    return second


def test_cross_scene_shared_armature_refuses_build_and_remove():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    settings = capture(armature, names)
    second_scene = make_second_scene_link(armature, "BuildSharedScene")
    before = snapshot_counts()
    result = cancelled_result(bpy.ops.character_designer.spline_ik_build)
    if result != {"CANCELLED"} or snapshot_counts() != before:
        raise AssertionError("Build accepted a cross-Scene shared Armature or left residue")
    bpy.data.scenes.remove(second_scene)

    # Once built safely, sharing the source Armature must make owner-scoped
    # removal refuse without deleting any part of the setup.
    select_pose_chain(armature, names)
    if bpy.ops.character_designer.spline_ik_build() != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    before = snapshot_counts()
    second_scene = make_second_scene_link(armature, "RemoveSharedScene")
    result = cancelled_result(
        lambda: bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT")
    )
    if result != {"CANCELLED"}:
        raise AssertionError("Remove accepted a cross-Scene shared Armature")
    expected = dict(before)
    if snapshot_counts() != expected:
        raise AssertionError("Cross-Scene removal deleted part of the generated setup")
    if bpy.data.objects.get(curve.name) is not curve or any(
        bpy.data.objects.get(control.name) is not control for control in controls
    ):
        raise AssertionError("Cross-Scene removal damaged generated objects")
    bpy.data.scenes.remove(second_scene)


def test_wrong_active_armature_edit_mode_refuses_and_preserves_context():
    reset_scene()
    armature_a, names_a, _branch = make_armature(name="CapturedA", bone_count=5)
    bpy.ops.object.mode_set(mode="OBJECT")
    armature_b, names_b, _branch = make_armature(name="ActiveB", bone_count=3)
    settings = bpy.context.window_manager.character_designer_spline_ik
    settings.armature = armature_a
    settings.chain_names_json = json.dumps(list(names_a))
    settings.chain_count = len(names_a)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    select_edit_chain(armature_b, names_b[:2])
    selected_before = tuple(
        bone.name for bone in armature_b.data.edit_bones if bone.select
    )
    active_before = armature_b.data.edit_bones.active.name
    if bpy.context.object is not armature_b:
        raise AssertionError(
            f"Test setup failed to make Armature B active: {bpy.context.object.name}"
        )
    before = snapshot_counts()
    result = cancelled_result(bpy.ops.character_designer.spline_ik_build)
    if result != {"CANCELLED"}:
        raise AssertionError("Build accepted a different active Armature")
    if (
        bpy.context.object is not armature_b
        or bpy.context.view_layer.objects.active is not armature_b
        or bpy.context.mode != "EDIT_ARMATURE"
        or tuple(bone.name for bone in armature_b.data.edit_bones if bone.select)
        != selected_before
        or armature_b.data.edit_bones.active.name != active_before
        or settings.armature is not armature_a
    ):
        print(
            "WRONG ACTIVE CONTEXT",
            bpy.context.object.name if bpy.context.object else None,
            bpy.context.mode,
            tuple(bone.name for bone in armature_b.data.edit_bones if bone.select),
            armature_b.data.edit_bones.active.name if armature_b.data.edit_bones.active else None,
            snapshot_counts(),
            before,
        )
        raise AssertionError("Refused Build changed the other Armature's Edit context")


def test_tampered_hook_registry_blocks_select_and_remove():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    registry = json.loads(curve[spline_ik_setup.HOOK_REGISTRY_KEY])
    registry[2]["target"] = "UserTamperedTarget"
    curve[spline_ik_setup.HOOK_REGISTRY_KEY] = json.dumps(registry)
    user = bpy.data.objects.new("User Selection Sentinel", None)
    bpy.context.scene.collection.objects.link(user)
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    user.select_set(True)
    bpy.context.view_layer.objects.active = user
    before = snapshot_counts()
    if cancelled_result(bpy.ops.character_designer.spline_ik_select_controls) != {"CANCELLED"}:
        raise AssertionError("Select Controls accepted a tampered Hook registry")
    if bpy.context.selected_objects != [user] or bpy.context.view_layer.objects.active is not user:
        raise AssertionError("Failed Select Controls changed user selection")
    if cancelled_result(
        lambda: bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT")
    ) != {"CANCELLED"}:
        raise AssertionError("Remove Generated accepted a tampered Hook registry")
    if snapshot_counts() != before or any(
        bpy.data.objects.get(control.name) is not control for control in controls
    ):
        raise AssertionError("Tampered-registry refusal deleted generated data")


def test_mixed_hook_name_formats_are_invalid_and_non_mutating():
    reset_scene()
    armature, names, _branch = make_armature(bone_count=5)
    result, settings = build(armature, names, points=5)
    if result != {"FINISHED"}:
        raise AssertionError(settings.last_message)
    curve, controls = owned_curve_and_controls(settings)
    registry = json.loads(curve[spline_ik_setup.HOOK_REGISTRY_KEY])
    legacy_name = f"CDSplineIK_Hook_{settings.active_rig_id}_01"
    curve.modifiers[0].name = legacy_name
    registry[0]["name"] = legacy_name
    curve[spline_ik_setup.HOOK_REGISTRY_KEY] = json.dumps(
        registry,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    bpy.context.view_layer.update()

    def mixed_signature():
        return (
            snapshot_counts(),
            tuple(
                (
                    modifier.as_pointer(),
                    modifier.name,
                    modifier.object.as_pointer() if modifier.object else 0,
                    tuple(modifier.vertex_indices),
                )
                for modifier in curve.modifiers
            ),
            curve[spline_ik_setup.HOOK_REGISTRY_KEY],
            tuple(obj.as_pointer() for obj in controls),
        )

    before = mixed_signature()
    for operation in (
        bpy.ops.character_designer.spline_ik_select_controls,
        bpy.ops.character_designer.spline_ik_enable_rotation_tilt,
        lambda: bpy.ops.character_designer.spline_ik_remove_generated("EXEC_DEFAULT"),
    ):
        if cancelled_result(operation) != {"CANCELLED"}:
            raise AssertionError("A mixed legacy/current Hook-name set was accepted")
        if mixed_signature() != before:
            raise AssertionError("Refusing mixed Hook-name formats changed the rig")


def test_register_unregister_cycle():
    reset_scene()
    ensure_spline_module_unregistered()
    character_designer.unregister()
    if hasattr(bpy.types.WindowManager, "character_designer_spline_ik"):
        raise AssertionError("Spline IK WindowManager state survived unregister")
    for class_name in (
        "CHARACTER_DESIGNER_OT_spline_ik_build",
        "CHARACTER_DESIGNER_OT_spline_ik_clear_status",
        "CHARACTERDESIGNER_PT_spline_ik_setup",
    ):
        if hasattr(bpy.types, class_name):
            raise AssertionError(f"Spline IK class survived unregister: {class_name}")
    character_designer.register()
    character_designer.register()
    # The production package must own this registration; the test helper is
    # intentionally not called here.
    if not hasattr(bpy.types.WindowManager, "character_designer_spline_ik"):
        raise AssertionError("Spline IK state was not restored by register")
    for class_name in (
        "CHARACTER_DESIGNER_OT_spline_ik_clear_status",
        "CHARACTER_DESIGNER_OT_spline_ik_select_controls",
        "CHARACTERDESIGNER_PT_spline_ik_setup",
    ):
        if not hasattr(bpy.types, class_name):
            raise AssertionError(f"Spline IK class was not registered: {class_name}")
    if not bpy.ops.character_designer.spline_ik_build.poll() is False:
        raise AssertionError("Build should not poll without a captured chain")

    settings = bpy.context.window_manager.character_designer_spline_ik
    settings.last_level = "ERROR"
    settings.last_message = "Dismiss this stale status"
    capture_signature = (
        settings.armature,
        settings.chain_names_json,
        settings.chain_count,
        settings.active_rig_id,
    )
    if bpy.ops.character_designer.spline_ik_clear_status() != {"FINISHED"}:
        raise AssertionError("Clear Status operator did not finish")
    if settings.last_level != "NONE" or settings.last_message:
        raise AssertionError("Clear Status did not dismiss the stale status")
    if capture_signature != (
        settings.armature,
        settings.chain_names_json,
        settings.chain_count,
        settings.active_rig_id,
    ):
        raise AssertionError("Clear Status changed captured-chain state")


def main():
    character_designer.register()
    ensure_spline_module_registered()
    tests = (
        test_strict_chain_capture_pose_and_edit,
        test_curve_profile_probe_tracks_nonzero_tilt_without_residue,
        test_curve_profile_probe_cleanup_survives_update_failure,
        test_build_from_other_active_mesh_uses_stored_capture_endpoints,
        test_five_point_bezier_hooks_and_display_types,
        test_short_hook_names_are_scoped_per_curve,
        test_middle_translation_and_endpoint_rotation_deform_curve,
        test_wire_control_rotation_drives_tilt_and_survives_lifecycle,
        test_rotation_tilt_interpolates_across_chain_and_maps_endpoints,
        test_legacy_rotation_tilt_upgrade_preserves_current_pose,
        test_enabled_v1_rotation_tilt_upgrades_axes_in_place,
        test_enabled_v1_axis_migration_failure_restores_v1_contract,
        test_enabled_rig_hook_name_cleanup_is_atomic,
        test_legacy_rotation_tilt_upgrade_refuses_artist_conflicts_atomically,
        test_control_frame_upgrade_failure_restores_hooks_and_pose,
        test_no_stretch_constraint_contract_and_evaluation,
        test_foreign_spline_ik_refusal_and_constraint_only_replace,
        test_select_and_remove_are_owner_scoped,
        test_select_and_reveal_controls_works_without_gui_areas,
        test_transaction_failure_leaves_no_residue,
        test_malformed_constraint_registry_refuses_build_without_overwrite,
        test_edit_mode_build_restores_head_tail_and_body_selection_exactly,
        test_remove_fault_injection_restores_complete_rig,
        test_remove_preserves_user_bone_channels_and_faults_restore_driven_pose,
        test_v1_swing_drivers_remain_recoverable_and_removable,
        test_mixed_rotation_tilt_versions_are_invalid_and_non_mutating,
        test_existing_owned_build_is_refused_without_changes,
        test_duplicate_owned_object_and_curve_data_block_safe_remove,
        test_cross_scene_shared_armature_refuses_build_and_remove,
        test_wrong_active_armature_edit_mode_refuses_and_preserves_context,
        test_tampered_hook_registry_blocks_select_and_remove,
        test_mixed_hook_name_formats_are_invalid_and_non_mutating,
        test_register_unregister_cycle,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        ensure_spline_module_unregistered()
        if hasattr(bpy.types.WindowManager, "character_designer"):
            character_designer.unregister()
    print(f"PASS Spline IK Setup {len(tests)} tests")


if __name__ == "__main__":
    main()
