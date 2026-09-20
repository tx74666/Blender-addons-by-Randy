"""Safe finger bone-roll inspection, preview, and correction tools."""

from __future__ import annotations

import math
import re

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator, Panel
from mathutils import Vector

from .finger_root import draw_finger_root_controls
from . import finger_flex, finger_layout_ui, finger_definition_ui, finger_bank_ui, finger_symmetry as symmetry
from . import finger_workflow_ui
from .ui_constants import SIDEBAR_CATEGORY, rig_page_active


EPSILON = 1.0e-8
ANGLE_EPSILON = math.radians(0.1)
FINGER_TOKENS = (
    "thumb",
    "index",
    "pointer",
    "middle",
    "ring",
    "pinky",
    "little",
)

FINGER_AXIS_ITEMS = (
    (
        "X",
        "Local X",
        "Use the local X axis as the finger flexion axis; this is the axis used by R X X.",
    ),
    (
        "Z",
        "Local Z",
        "Use the local Z axis as the finger flexion axis.",
    ),
)
FINGER_REFERENCE_ITEMS = (
    (
        "CHAIN_ROOT",
        "Per-Finger Base",
        "Keep each selected finger's first bone as its own roll reference.",
    ),
    (
        "ACTIVE",
        "Active Bone",
        "Use the active selected finger bone as the roll reference for all selected fingers.",
    ),
)

_PREVIEW = None
_PREVIEW_HANDLE = None
_PREVIEW_SHADER = None
_PREVIEW_BATCHES = None
_PREVIEW_FAILED = False


def _settings(context):
    window_manager = getattr(context, "window_manager", None)
    return getattr(window_manager, "character_designer", None)


def _armature(context):
    obj = getattr(context, "object", None)
    return obj if obj is not None and obj.type == "ARMATURE" else None


def _bone_collection(armature):
    return armature.data.edit_bones if armature.mode == "EDIT" else armature.data.bones


def _selected_bones(armature):
    collection = _bone_collection(armature)
    selected = [bone for bone in collection if bone.select]
    active = collection.active
    return selected, active


def _finger_key(name):
    lowered = name.lower()
    for token in FINGER_TOKENS:
        if token in lowered:
            return token
    return None


def _side_key(name):
    match = re.search(r"(?:^|[._\-])(L|R)$", name, re.IGNORECASE)
    return match.group(1).upper() if match else "?"


def _segment_number(name):
    match = re.search(r"(?:^|[._\-])(\d+)(?:[._\-]|$)", name)
    return int(match.group(1)) if match else 10_000


def _parent_depth(bone):
    depth = 0
    parent = bone.parent
    while parent is not None and depth < 1024:
        depth += 1
        parent = parent.parent
    return depth


def _sort_key(bone):
    return (_segment_number(bone.name), _parent_depth(bone), bone.name.lower())


def _head(bone):
    return Vector(bone.head if isinstance(bone, bpy.types.EditBone) else bone.head_local)


def _tail(bone):
    return Vector(bone.tail if isinstance(bone, bpy.types.EditBone) else bone.tail_local)


def _axis(bone, axis):
    if isinstance(bone, bpy.types.EditBone):
        return Vector(bone.x_axis if axis == "X" else bone.z_axis).normalized()
    return bone.matrix_local.to_3x3().col[0 if axis == "X" else 2].normalized()


def _bone_direction(bone):
    direction = _tail(bone) - _head(bone)
    if direction.length <= EPSILON:
        raise ValueError(f"Bone '{bone.name}' has zero length.")
    return direction.normalized()


def _project_axis(reference, direction, fallback=()):
    """Project a roll reference into a bone's transverse plane."""

    candidates = [Vector(reference), *(Vector(value) for value in fallback)]
    for candidate in candidates:
        if candidate.length <= EPSILON:
            continue
        projected = candidate - direction * candidate.dot(direction)
        if projected.length > EPSILON:
            return projected.normalized()
    return None


def _selected_finger_groups(armature):
    selected, active = _selected_bones(armature)
    groups = {}
    ignored = []
    for bone in selected:
        finger = _finger_key(bone.name)
        if finger is None:
            ignored.append(bone.name)
            continue
        groups.setdefault((finger, _side_key(bone.name)), []).append(bone)
    for bones in groups.values():
        bones.sort(key=_sort_key)
    return groups, ignored, active


def _reference_bone_for_group(group, active, reference_mode):
    if reference_mode == "ACTIVE":
        if active is None or not active.select or _finger_key(active.name) is None:
            raise ValueError(
                "Active Bone must be a selected finger bone when Active Bone is the reference."
            )
        return active
    return group[0]


def _target_axis_for_bone(reference_axis, bone, axis, reference_bone):
    direction = _bone_direction(bone)
    fallback = [_axis(reference_bone, "Z" if axis == "X" else "X")]
    target = _project_axis(reference_axis, direction, fallback=fallback)
    if target is None:
        raise ValueError(
            f"Cannot find a stable transverse reference for finger bone '{bone.name}'."
        )
    return target


def _build_plan(context):
    armature = _armature(context)
    if armature is None:
        raise ValueError("Select the main Armature before using Fingers.")
    settings = _settings(context)
    axis = getattr(settings, "finger_axis", "X") if settings else "X"
    reference_mode = getattr(settings, "finger_reference", "CHAIN_ROOT") if settings else "CHAIN_ROOT"
    groups, ignored, active = _selected_finger_groups(armature)
    if not groups:
        raise ValueError(
            "Select one or more finger bones. Body bones are ignored by this tool."
        )
    if reference_mode == "ACTIVE" and (active is None or not active.select or _finger_key(active.name) is None):
        raise ValueError(
            "Active Bone must be a selected finger bone when Active Bone is the reference."
        )

    symmetry.guard_rig(armature)
    collection = _bone_collection(armature)
    source_groups = {}
    # One authoritative side per finger, including when both sides are selected.
    # Active side wins if selected; otherwise use the sole selected side or L.
    for finger in sorted({key[0] for key in groups}):
        sides = {key[1] for key in groups if key[0] == finger}
        if "?" in sides:
            raise ValueError("Finger correction needs explicit L/R bone names for synchronized pairs.")
        preferred = _side_key(active.name) if active and _finger_key(active.name) == finger and active.select else None
        side = preferred if preferred in sides else sorted(sides)[0]
        source = {}
        for key, group in groups.items():
            if key[0] != finger: continue
            for bone in group:
                other = symmetry.opposite(collection, bone)
                symmetry.validate_pair(collection, bone, other)
                chosen = bone if _side_key(bone.name) == side else other
                source[chosen.name] = chosen
        source_groups[(finger, side)] = sorted(source.values(), key=_sort_key)

    records, anchors, issues = [], [], []
    for group_key, group in sorted(source_groups.items()):
        reference_bone = _reference_bone_for_group(group, active, reference_mode)
        reference_axis = _axis(reference_bone, axis)
        fallback = _axis(reference_bone, "Z" if axis == "X" else "X")
        if _side_key(reference_bone.name) != group_key[1]:
            # Rotation axes are axial vectors: reflection also reverses sign.
            reference_axis, fallback = -symmetry.reflect(reference_axis), -symmetry.reflect(fallback)
        anchors.append(reference_bone.name)
        for bone in group:
            target = _project_axis(reference_axis, _bone_direction(bone), fallback=(fallback,))
            if target is None:
                raise ValueError(f"Cannot find a stable axis for '{bone.name}'.")
            other = symmetry.opposite(collection, bone)
            opposite_target = _project_axis(-symmetry.reflect(target), _bone_direction(other))
            if opposite_target is None:
                raise ValueError(f"Cannot find a stable opposite axis for '{other.name}'.")
            for item, proposed in ((bone, target), (other, opposite_target)):
                if item.name == reference_bone.name: continue
                current = _axis(item, axis)
                records.append({
                    "name": item.name,
                    "group": f"{group_key[0]}.{_side_key(item.name)}",
                    "reference": reference_bone.name,
                    "current_axis": current.copy(),
                    "target_axis": proposed.copy(),
                    "direction": _bone_direction(item),
                    "center": ((_head(item)+_tail(item))*.5).copy(),
                    "length": float((_tail(item)-_head(item)).length),
                    "angle": float(current.angle(proposed)),
                })

    if not records:
        raise ValueError(
            "Select finger segments with existing matching L/R bones."
        )
    return {
        "armature": armature,
        "armature_pointer": armature.as_pointer(),
        "axis": axis,
        "reference_mode": reference_mode,
        "records": records,
        "anchors": anchors,
        "ignored": ignored,
        "issues": issues,
    }


def _plan_summary(plan):
    changed = sum(record["angle"] > ANGLE_EPSILON for record in plan["records"])
    ignored = len(plan["ignored"])
    groups = len(set(record["group"] for record in plan["records"]))
    suffix = f" Ignored {ignored} non-finger selection(s)." if ignored else ""
    issue_suffix = f" {len(plan['issues'])} bone(s) need inspection." if plan["issues"] else ""
    return (
        f"{groups} finger chain(s), {len(plan['records'])} target segment(s), "
        f"{changed} roll change(s) on Local {plan['axis']}."
        + suffix
        + issue_suffix
    )


def _set_status(settings, level, message):
    if settings is None:
        return
    settings.finger_status_level = level
    settings.finger_status = message


def _tag_redraw():
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _clear_preview(context=None):
    global _PREVIEW, _PREVIEW_BATCHES, _PREVIEW_FAILED
    _PREVIEW = None
    _PREVIEW_BATCHES = None
    _PREVIEW_FAILED = False
    settings = _settings(context or bpy.context)
    if settings is not None:
        settings.finger_preview_active = False
    _tag_redraw()


def _ensure_preview_handler():
    global _PREVIEW_HANDLE
    if _PREVIEW_HANDLE is None:
        _PREVIEW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            _draw_preview,
            (),
            "WINDOW",
            "POST_VIEW",
        )


def _world_point(armature, point):
    return tuple(armature.matrix_world @ Vector(point))


def _build_preview_batches(plan):
    armature = plan["armature"]
    current = []
    target = []
    for record in plan["records"]:
        center = Vector(record["center"])
        scale = max(record["length"] * 0.45, 0.01)
        current.extend((_world_point(armature, center), _world_point(armature, center + record["current_axis"] * scale)))
        target.extend((_world_point(armature, center), _world_point(armature, center + record["target_axis"] * scale)))
    return current, target


def _draw_preview():
    global _PREVIEW_SHADER, _PREVIEW_BATCHES, _PREVIEW_FAILED
    if _PREVIEW is None or _PREVIEW_FAILED or bpy.app.background:
        return
    try:
        context = bpy.context
        if context.area is None or context.area.type != "VIEW_3D":
            return
        settings = _settings(context)
        if settings is None or not settings.finger_preview_active:
            return
        armature = _PREVIEW["armature"]
        if armature is None or armature.as_pointer() != _PREVIEW["armature_pointer"]:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader

        if _PREVIEW_SHADER is None:
            _PREVIEW_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
        if _PREVIEW_BATCHES is None:
            current, target = _build_preview_batches(_PREVIEW)
            _PREVIEW_BATCHES = (
                batch_for_shader(_PREVIEW_SHADER, "LINES", {"pos": current}),
                batch_for_shader(_PREVIEW_SHADER, "LINES", {"pos": target}),
            )

        previous_depth = gpu.state.depth_test_get()
        previous_depth_mask = gpu.state.depth_mask_get()
        previous_blend = gpu.state.blend_get()
        previous_line_width = gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(3.0)
            _PREVIEW_SHADER.bind()
            _PREVIEW_SHADER.uniform_float("color", (1.0, 0.18, 0.08, 0.9))
            _PREVIEW_BATCHES[0].draw(_PREVIEW_SHADER)
            _PREVIEW_SHADER.uniform_float("color", (0.12, 1.0, 0.25, 0.95))
            _PREVIEW_BATCHES[1].draw(_PREVIEW_SHADER)
        finally:
            gpu.state.line_width_set(previous_line_width)
            gpu.state.blend_set(previous_blend)
            gpu.state.depth_mask_set(previous_depth_mask)
            gpu.state.depth_test_set(previous_depth)
    except Exception:
        _PREVIEW_FAILED = True


def _roll_target_vector(bone, target_axis, axis):
    direction = _bone_direction(bone)
    if axis == "Z":
        return target_axis
    # EditBone.align_roll aligns local Z.  Construct the Z vector that makes
    # local X equal to the requested flexion axis while keeping local Y along
    # the bone head-to-tail direction.
    roll_reference = target_axis.cross(direction)
    if roll_reference.length <= EPSILON:
        raise ValueError(f"Cannot construct a roll axis for '{bone.name}'.")
    return roll_reference.normalized()


def _apply_plan(context, plan):
    armature = plan["armature"]
    if armature.mode != "EDIT":
        raise ValueError("Enter Armature Edit Mode before applying finger correction.")
    if context.object is not armature or armature.as_pointer() != plan["armature_pointer"]:
        raise ValueError("The active Armature changed; run Check again before applying.")

    edit_bones = armature.data.edit_bones
    symmetry.guard_pose(armature, [r['name'] for r in plan['records']])
    mirror_x = armature.data.use_mirror_x
    before = {}
    try:
        armature.data.use_mirror_x = False
        for record in plan["records"]:
            bone = edit_bones.get(record["name"])
            if bone is None:
                raise ValueError(f"Finger bone '{record['name']}' no longer exists.")
            before[bone.name] = float(bone.roll)
            target = Vector(record["target_axis"])
            roll_reference = _roll_target_vector(bone, target, plan["axis"])
            bone.align_roll(roll_reference)
            actual = _axis(bone, plan["axis"])
            if actual.dot(target) < 1.0 - 1.0e-4:
                raise ValueError(f"Blender could not align the local {plan['axis']} axis of '{bone.name}'.")
        armature.update_tag(refresh={"DATA"})
        context.view_layer.update()
    except Exception:
        for name, roll in before.items():
            bone = edit_bones.get(name)
            if bone is not None:
                bone.roll = roll
        armature.update_tag(refresh={"DATA"})
        context.view_layer.update()
        raise
    finally:
        armature.data.use_mirror_x = mirror_x

    changed = 0
    for name, old_roll in before.items():
        bone = edit_bones.get(name)
        if bone is not None and abs(((float(bone.roll) - old_roll + math.pi) % math.tau) - math.pi) > ANGLE_EPSILON:
            changed += 1
    return changed


class CHARACTERDESIGNER_OT_finger_roll(Operator):
    bl_idname = "character_designer.finger_roll"
    bl_label = "Finger Roll Tools"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        items=(
            ("CHECK", "Check", "Inspect selected finger bone axes without changing the rig."),
            ("PREVIEW", "Preview", "Show current axes in red and proposed axes in green without changing the rig."),
            ("HIDE_PREVIEW", "Hide Preview", "Remove the non-destructive viewport preview."),
            ("APPLY", "Apply Correction", "Apply the proposed roll correction to selected finger segments and their existing L/R counterparts."),
        ),
        default="CHECK",
    )

    @classmethod
    def poll(cls, context):
        return _armature(context) is not None

    def execute(self, context):
        settings = _settings(context)
        try:
            if self.action == "HIDE_PREVIEW":
                _clear_preview(context)
                _set_status(settings, "INFO", "Finger preview hidden; no bone data was changed.")
                self.report({"INFO"}, "Finger preview hidden.")
                return {"FINISHED"}

            plan = _build_plan(context)
            summary = _plan_summary(plan)
            if self.action == "CHECK":
                _clear_preview(context)
                _set_status(settings, "INFO", summary)
                self.report({"INFO"}, summary)
                return {"FINISHED"}
            if self.action == "PREVIEW":
                global _PREVIEW, _PREVIEW_BATCHES, _PREVIEW_FAILED
                _PREVIEW = plan
                _PREVIEW_BATCHES = None
                _PREVIEW_FAILED = False
                _ensure_preview_handler()
                if settings is not None:
                    settings.finger_preview_active = True
                    settings.finger_status_level = "INFO"
                    settings.finger_status = summary + " Red=current, green=proposed."
                _tag_redraw()
                self.report({"INFO"}, "Finger preview shown; red=current, green=proposed.")
                return {"FINISHED"}

            changed = _apply_plan(context, plan)
            _clear_preview(context)
            message = f"Corrected {changed} finger bone roll(s) across both hands; Head/Tail and weights were unchanged."
            _set_status(settings, "SUCCESS", message)
            self.report({"INFO"}, message)
            return {"FINISHED"}
        except (ValueError, RuntimeError, KeyError, TypeError, ReferenceError) as exc:
            message = str(exc)
            _set_status(settings, "WARNING", message)
            self.report({"WARNING"}, message)
            return {"CANCELLED"}


class CHARACTERDESIGNER_PT_fingers(Panel):
    bl_label = "Fingers"
    bl_idname = "CHARACTERDESIGNER_PT_fingers"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_order = 20

    @classmethod
    def poll(cls, context):
        return rig_page_active(context, "BODY")

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        armature = _armature(context)
        if settings is None:
            layout.label(text="Character Designer state is unavailable.", icon="ERROR")
            return
        mesh_edit = context.mode == "EDIT_MESH" and context.edit_object is not None

        finger_definition_ui.draw_controls(layout, context)
        if finger_bank_ui.bank.active_object(context):
            finger_workflow_ui.draw_controls(layout, context)
        else:
            if mesh_edit: finger_layout_ui.draw_controls(layout, context)
            finger_flex.draw_definition_controls(layout, context)

        if armature is not None:
            layout.prop(finger_flex.state(context), "show_legacy")

        if armature is not None and finger_flex.state(context).show_legacy:
            roll_box = layout.box()
            roll_box.label(text="Legacy Roll Reference", icon="BONE_DATA")
            roll_box.label(text="Matches existing axes; does not define the bend side.")
            roll_box.label(text="Selected fingers + matching L/R bones.", icon="BONE_DATA")
            roll_box.prop(settings, "finger_axis", text="Flex Axis")
            roll_box.prop(settings, "finger_reference", text="Roll Reference")

            row = roll_box.row(align=True)
            row.operator("character_designer.finger_roll", text="Check", icon="VIEWZOOM").action = "CHECK"
            if settings.finger_preview_active:
                row.operator("character_designer.finger_roll", text="Hide Preview", icon="HIDE_OFF").action = "HIDE_PREVIEW"
            else:
                row.operator("character_designer.finger_roll", text="Preview", icon="HIDE_ON").action = "PREVIEW"
            apply_row = roll_box.row()
            apply_row.enabled = armature.mode == "EDIT"
            apply_row.operator("character_designer.finger_roll", text="Apply Correction", icon="FILE_TICK").action = "APPLY"
            if armature.mode != "EDIT":
                roll_box.label(text="Apply requires Armature Edit Mode.", icon="INFO")
            if settings.finger_status:
                icon = {
                    "SUCCESS": "CHECKMARK",
                    "WARNING": "ERROR",
                    "ERROR": "ERROR",
                }.get(settings.finger_status_level, "INFO")
                roll_box.label(text=settings.finger_status, icon=icon)
        elif armature is None and not mesh_edit:
            layout.label(text="Select the mesh or main Armature.", icon="INFO")

        if finger_flex.state(context).show_legacy:
            finger_flex.draw_controls(layout, context)
            draw_finger_root_controls(layout, context)


FINGER_BONES_CLASSES = (
    *finger_definition_ui.CLASSES,
    *finger_bank_ui.CLASSES,
    *finger_flex.CLASSES,
    *finger_layout_ui.CLASSES,
    *finger_workflow_ui.CLASSES,
    CHARACTERDESIGNER_OT_finger_roll,
    CHARACTERDESIGNER_PT_fingers,
)


def register_finger_bones_runtime():
    """Keep the preview handler lazy; no viewport draw hook is needed until Preview."""
    finger_definition_ui.register_runtime()
    finger_bank_ui.register_runtime()
    finger_flex.register_runtime()
    finger_layout_ui.register_runtime()
    finger_workflow_ui.register_runtime()


def unregister_finger_bones_runtime():
    finger_workflow_ui.unregister_runtime()
    finger_layout_ui.unregister_runtime()
    finger_flex.unregister_runtime()
    finger_definition_ui.unregister_runtime()
    finger_bank_ui.unregister_runtime()
    global _PREVIEW_HANDLE, _PREVIEW_SHADER
    _clear_preview(bpy.context)
    if _PREVIEW_HANDLE is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_PREVIEW_HANDLE, "WINDOW")
        except Exception:
            pass
        _PREVIEW_HANDLE = None
    _PREVIEW_SHADER = None
