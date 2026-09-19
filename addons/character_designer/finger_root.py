"""Per-finger root guides and conservative placement for existing bone chains."""

from __future__ import annotations

import re

import bmesh
import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    StringProperty,
)
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from .ui_constants import UI_PAGE_MISC, active_ui_page, rig_page_active


EPSILON = 1.0e-7
FINGER_ROOT_ITEMS = (
    ("THUMB", "Thumb", "Guide for the thumb chain."),
    ("INDEX", "Index", "Guide for the index finger chain."),
    ("MIDDLE", "Middle", "Guide for the middle finger chain."),
    ("RING", "Ring", "Guide for the ring finger chain."),
    ("LITTLE", "Little", "Guide for the little finger chain."),
)
FINGER_NAME_TOKENS = {
    "THUMB": ("thumb",),
    "INDEX": ("index", "pointer"),
    "MIDDLE": ("middle",),
    "RING": ("ring",),
    "LITTLE": ("little", "pinky"),
}

_PREVIEW = None
_PREVIEW_HANDLE = None
_PREVIEW_SHADER = None
_PREVIEW_BATCHES = None
_PREVIEW_FAILED = False


class FingerRootError(ValueError):
    """Artist-facing validation error for a finger root guide."""


class CharacterDesignerFingerRootSlot(PropertyGroup):
    key: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    configured: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    mesh_object: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    face_index: IntProperty(default=-1, options={"HIDDEN", "SKIP_SAVE"})
    face_signature: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    surface_point: FloatVectorProperty(
        size=3, subtype="XYZ", options={"HIDDEN", "SKIP_SAVE"}
    )
    normal: FloatVectorProperty(
        size=3, subtype="XYZ", default=(0.0, 0.0, 1.0),
        options={"HIDDEN", "SKIP_SAVE"},
    )
    direction_start: FloatVectorProperty(
        size=3, subtype="XYZ", options={"HIDDEN", "SKIP_SAVE"}
    )
    direction_end: FloatVectorProperty(
        size=3, subtype="XYZ", options={"HIDDEN", "SKIP_SAVE"}
    )
    direction_source: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    direction_rig: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    direction_side: StringProperty(options={"HIDDEN", "SKIP_SAVE"})


class CharacterDesignerFingerRootState(PropertyGroup):
    active_finger: EnumProperty(
        name="Finger",
        items=FINGER_ROOT_ITEMS,
        default="THUMB",
        options={"SKIP_SAVE"},
    )
    root_depth: FloatProperty(
        name="Extra Inward",
        description="Optional additional offset from the centerline intersection along the inward face normal",
        default=0.0,
        min=0.0,
        max=0.25,
        precision=4,
        subtype="DISTANCE",
        unit="LENGTH",
        options={"SKIP_SAVE"},
    )
    finger_side: EnumProperty(
        name="Side",
        description="Choose which Main Rig hand supplies the finger direction line",
        items=(
            ("AUTO", "Nearest", "Choose the closest left or right finger chain to the locked face."),
            ("L", "Left", "Use the left finger chain."),
            ("R", "Right", "Use the right finger chain."),
        ),
        default="AUTO",
        options={"SKIP_SAVE"},
    )
    bone_length_scale: FloatProperty(
        name="Bone Length Scale",
        description="Scale the existing selected finger chain lengths when placing it",
        default=1.0,
        min=0.1,
        max=3.0,
        precision=3,
        options={"SKIP_SAVE"},
    )
    flip_normal: BoolProperty(
        name="Flip Normal",
        description="Use the selected face normal as the inward direction instead of the default opposite",
        default=False,
        options={"SKIP_SAVE"},
    )
    status_level: StringProperty(default="NONE", options={"HIDDEN", "SKIP_SAVE"})
    status: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    slots: CollectionProperty(
        type=CharacterDesignerFingerRootSlot,
        options={"HIDDEN", "SKIP_SAVE"},
    )


def _settings(context):
    window_manager = getattr(context, "window_manager", None)
    return getattr(window_manager, "character_designer_finger_root", None)


def _ensure_slots(settings):
    if settings is None:
        return None
    existing = {slot.key: slot for slot in settings.slots}
    for key, _label, _description in FINGER_ROOT_ITEMS:
        if key not in existing:
            slot = settings.slots.add()
            slot.key = key
            existing[key] = slot
    return existing


def _active_slot(context):
    settings = _settings(context)
    slots = _ensure_slots(settings)
    if slots is None:
        raise FingerRootError("Finger root state is unavailable.")
    slot = slots.get(settings.active_finger)
    if slot is None:
        raise FingerRootError("Choose a finger before capturing a guide.")
    return settings, slot


def _mesh_edit_data(context):
    obj = getattr(context, "edit_object", None)
    if context.mode != "EDIT_MESH" or obj is None or obj.type != "MESH":
        raise FingerRootError("Use Mesh Edit Mode for face and direction capture.")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.faces.index_update()
    bm.verts.index_update()
    return obj, bm


def _world_normal(obj, normal):
    matrix = obj.matrix_world.to_3x3().inverted().transposed()
    result = matrix @ Vector(normal)
    if result.length <= EPSILON:
        raise FingerRootError("The selected face has no stable world normal.")
    return result.normalized()


def _face_signature(face):
    values = sorted(
        tuple(round(float(component), 6) for component in vertex.co)
        for vertex in face.verts
    )
    return repr(values)


def _capture_face(context):
    settings, slot = _active_slot(context)
    obj, bm = _mesh_edit_data(context)
    selected = [face for face in bm.faces if face.select and not face.hide]
    if len(selected) != 1:
        raise FingerRootError("Select exactly one palm-side face for the active finger.")
    face = selected[0]
    center = obj.matrix_world @ face.calc_center_median()
    normal = _world_normal(obj, face.normal)
    slot.mesh_object = obj.name
    slot.face_index = face.index
    slot.face_signature = _face_signature(face)
    slot.surface_point = tuple(center)
    slot.normal = tuple(normal)
    slot.configured = bool(slot.face_signature) and (
        Vector(slot.direction_end) - Vector(slot.direction_start)
    ).length > EPSILON
    settings.status_level = "INFO"
    settings.status = f"{settings.active_finger.title()} root face captured from {obj.name}."


def _preferred_rig(context):
    from .character_setup import preferred_rig

    rig = preferred_rig(context)
    if rig is not None and rig.type == "ARMATURE":
        return rig
    raise FingerRootError(
        "Set Main Rig in Character Setup before capturing the automatic finger direction."
    )


def _bone_sort_key(bone):
    return (_segment_number(bone.name), _parent_depth(bone), bone.name.lower())


def _finger_chains(rig, finger_key):
    groups = {}
    for bone in rig.data.bones:
        if _finger_key(bone.name) != finger_key:
            continue
        groups.setdefault(_side_key(bone.name), []).append(bone)
    for bones in groups.values():
        bones.sort(key=_bone_sort_key)
    return groups


def _world_bone_point(rig, point):
    return rig.matrix_world @ Vector(point)


def _capture_bone_direction(context):
    settings, slot = _active_slot(context)
    if not slot.face_signature:
        raise FingerRootError(
            f"Lock the {settings.active_finger.title()} center face before capturing its direction."
        )
    rig = _preferred_rig(context)
    chains = _finger_chains(rig, settings.active_finger)
    if settings.finger_side != "AUTO":
        chains = {settings.finger_side: chains.get(settings.finger_side, [])}
    candidates = []
    face_center = Vector(slot.surface_point)
    for side, bones in chains.items():
        if not bones:
            continue
        start = _world_bone_point(rig, bones[0].head)
        end = _world_bone_point(rig, bones[-1].tail)
        midpoint = (start + end) * 0.5
        candidates.append(((midpoint - face_center).length, side, start, end))
    if not candidates:
        side = settings.finger_side if settings.finger_side != "AUTO" else "L/R"
        raise FingerRootError(
            f"No {settings.active_finger.title()} chain was found on side {side} in Main Rig '{rig.name}'."
        )
    _distance, side, start, end = min(candidates, key=lambda item: item[0])
    if (end - start).dot(((start + end) * 0.5) - face_center) < 0.0:
        start, end = end, start
    slot.direction_start = tuple(start)
    slot.direction_end = tuple(end)
    slot.direction_source = "BONE_CHAIN"
    slot.direction_rig = rig.name
    slot.direction_side = side
    slot.configured = True
    settings.status_level = "INFO"
    settings.status = (
        f"{settings.active_finger.title()} direction locked to {rig.name} {side} bone chain."
    )


def _capture_vertex_direction(context):
    settings, slot = _active_slot(context)
    obj, bm = _mesh_edit_data(context)
    selected = [vertex for vertex in bm.verts if vertex.select and not vertex.hide]
    if len(selected) != 2:
        raise FingerRootError(
            "No usable Main Rig finger chain was found. Lock a center face and set Main Rig, "
            "or select exactly two vertices for a manual direction line."
        )
    start, end = sorted(selected, key=lambda vertex: vertex.index)
    world_start = obj.matrix_world @ start.co
    world_end = obj.matrix_world @ end.co
    if (world_end - world_start).length <= EPSILON:
        raise FingerRootError("The manual direction guide needs two different vertices.")
    slot.direction_start = tuple(world_start)
    slot.direction_end = tuple(world_end)
    slot.direction_source = "VERTEX_LINE"
    slot.direction_rig = ""
    slot.direction_side = ""
    slot.configured = bool(slot.face_signature)
    settings.status_level = "INFO"
    settings.status = f"{settings.active_finger.title()} manual vertex direction locked."


def _capture_direction(context):
    try:
        _capture_bone_direction(context)
    except FingerRootError as bone_error:
        try:
            _capture_vertex_direction(context)
        except FingerRootError:
            raise bone_error


def _guide_solution(settings, slot):
    if not slot.face_signature:
        raise FingerRootError(
            f"Capture one root face for {settings.active_finger.title()} first."
        )
    direction = Vector(slot.direction_end) - Vector(slot.direction_start)
    if direction.length <= EPSILON:
        raise FingerRootError(
            f"Capture the Main Rig direction for {settings.active_finger.title()} first."
        )
    direction.normalize()
    normal = Vector(slot.normal)
    if normal.length <= EPSILON:
        raise FingerRootError("The captured face normal is invalid; capture the face again.")
    normal.normalize()
    cosine = direction.dot(normal)
    denominator = 1.0 - cosine * cosine
    if denominator <= EPSILON:
        raise FingerRootError(
            "The face normal and finger centerline are parallel; choose a face whose normal points toward the centerline."
        )
    inward = normal if settings.flip_normal else -normal
    face_center = Vector(slot.surface_point)
    start = Vector(slot.direction_start)
    offset = start - face_center
    face_line_parameter = (
        offset.dot(normal) - cosine * offset.dot(direction)
    ) / denominator
    direction_line_parameter = (
        cosine * offset.dot(normal) - offset.dot(direction)
    ) / denominator
    face_line_point = face_center + normal * face_line_parameter
    centerline_point = start + direction * direction_line_parameter
    line_gap = (face_line_point - centerline_point).length
    if line_gap > 0.005:
        raise FingerRootError(
            f"The face-normal line misses the finger centerline by {line_gap:.4f} m; "
            "choose a more central face or use Flip Normal."
        )
    inward_depth = (centerline_point - face_center).dot(inward)
    if inward_depth <= EPSILON:
        raise FingerRootError(
            "The intersection is on the outside of the face; choose the opposite face or enable Flip Normal."
        )
    root = centerline_point + inward * float(settings.root_depth)
    return {
        "surface": face_center,
        "face_line_point": face_line_point,
        "centerline_point": centerline_point,
        "root": root,
        "normal": normal,
        "direction": direction,
        "line_gap": line_gap,
        "inward_depth": inward_depth,
        "direction_start": start,
        "direction_end": start + direction * (Vector(slot.direction_end) - start).length,
    }


def _finger_key(name):
    lowered = name.lower()
    for key, tokens in FINGER_NAME_TOKENS.items():
        if any(token in lowered for token in tokens):
            return key
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


def _selected_chain(armature, finger_key):
    collection = armature.data.edit_bones if armature.mode == "EDIT" else armature.data.bones
    selected = [bone for bone in collection if bone.select and _finger_key(bone.name) == finger_key]
    if not selected:
        raise FingerRootError(
            f"Select the {finger_key.title()} bone chain in Armature Edit Mode."
        )
    sides = {_side_key(bone.name) for bone in selected}
    if len(sides) != 1:
        raise FingerRootError("Select one side of one finger chain at a time.")
    selected.sort(key=lambda bone: (_segment_number(bone.name), _parent_depth(bone), bone.name.lower()))
    if selected[0].use_connect:
        raise FingerRootError(
            f"The first {finger_key.title()} bone is connected to its parent; disconnect it before placement."
        )
    return selected


def _armature(context):
    obj = getattr(context, "object", None)
    return obj if obj is not None and obj.type == "ARMATURE" else None


def _build_preview_points(context, solution, finger_key):
    points = [
        ("guide", Vector(solution["surface"])),
        ("guide", Vector(solution["face_line_point"])),
        ("guide", Vector(solution["direction_start"])),
        ("guide", Vector(solution["direction_end"])),
        ("guide", Vector(solution["centerline_point"])),
        ("guide", Vector(solution["root"])),
    ]
    armature = _armature(context)
    if armature is not None:
        chain = _selected_chain(armature, finger_key)
        direction = Vector(solution["direction"])
        current = Vector(solution["root"])
        for bone in chain:
            length = max((Vector(bone.tail) - Vector(bone.head)).length, 0.001)
            next_point = current + direction * length * 1.0
            points.append(("bone", current.copy()))
            points.append(("bone", next_point.copy()))
            current = next_point
    return points


def _set_status(settings, level, message):
    settings.status_level = level
    settings.status = message


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
    _tag_redraw()


def _ensure_preview_handler():
    global _PREVIEW_HANDLE
    if _PREVIEW_HANDLE is None:
        _PREVIEW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(
            _draw_preview, (), "WINDOW", "POST_VIEW"
        )


def _draw_preview():
    global _PREVIEW_SHADER, _PREVIEW_BATCHES, _PREVIEW_FAILED
    if _PREVIEW is None or _PREVIEW_FAILED or bpy.app.background:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader

        if _PREVIEW_SHADER is None:
            _PREVIEW_SHADER = gpu.shader.from_builtin("UNIFORM_COLOR")
        if _PREVIEW_BATCHES is None:
            guide = []
            bones = []
            points = _PREVIEW["points"]
            for kind, point in points:
                if kind == "bone":
                    bones.append(tuple(point))
                else:
                    guide.append(tuple(point))
            _PREVIEW_BATCHES = (
                batch_for_shader(_PREVIEW_SHADER, "LINES", {"pos": guide})
                if len(guide) >= 2 else None,
                batch_for_shader(_PREVIEW_SHADER, "LINES", {"pos": bones})
                if len(bones) >= 2 else None,
            )
        previous_depth = gpu.state.depth_test_get()
        previous_mask = gpu.state.depth_mask_get()
        previous_blend = gpu.state.blend_get()
        previous_width = gpu.state.line_width_get()
        try:
            gpu.state.depth_test_set("NONE")
            gpu.state.depth_mask_set(False)
            gpu.state.blend_set("ALPHA")
            gpu.state.line_width_set(3.0)
            _PREVIEW_SHADER.bind()
            _PREVIEW_SHADER.uniform_float("color", (0.1, 0.8, 1.0, 0.95))
            if _PREVIEW_BATCHES[0] is not None:
                _PREVIEW_BATCHES[0].draw(_PREVIEW_SHADER)
            _PREVIEW_SHADER.uniform_float("color", (1.0, 0.25, 0.08, 0.95))
            if _PREVIEW_BATCHES[1] is not None:
                _PREVIEW_BATCHES[1].draw(_PREVIEW_SHADER)
        finally:
            gpu.state.line_width_set(previous_width)
            gpu.state.blend_set(previous_blend)
            gpu.state.depth_mask_set(previous_mask)
            gpu.state.depth_test_set(previous_depth)
    except Exception:
        _PREVIEW_FAILED = True


def _apply(context, settings, slot, solution):
    armature = _armature(context)
    if armature is None or armature.mode != "EDIT":
        raise FingerRootError("Select the armature and enter Armature Edit Mode before Apply.")
    chain = _selected_chain(armature, settings.active_finger)
    direction = armature.matrix_world.to_3x3().inverted() @ Vector(solution["direction"])
    if direction.length <= EPSILON:
        raise FingerRootError("The finger direction cannot be converted into armature space.")
    direction.normalize()
    root = armature.matrix_world.inverted() @ Vector(solution["root"])
    before = {
        bone.name: (Vector(bone.head), Vector(bone.tail), float(bone.roll))
        for bone in chain
    }
    lengths = {
        bone.name: max((Vector(bone.tail) - Vector(bone.head)).length, EPSILON)
        for bone in chain
    }
    try:
        current = root
        for bone in chain:
            length = lengths[bone.name] * float(settings.bone_length_scale)
            bone.head = current
            bone.tail = current + direction * length
            current = Vector(bone.tail)
        armature.update_tag(refresh={"DATA"})
        context.view_layer.update()
    except Exception:
        for name, (head, tail, roll) in before.items():
            bone = armature.data.edit_bones.get(name)
            if bone is not None:
                bone.head = head
                bone.tail = tail
                bone.roll = roll
        armature.update_tag(refresh={"DATA"})
        context.view_layer.update()
        raise
    return len(chain)


class CHARACTERDESIGNER_OT_finger_root(Operator):
    bl_idname = "character_designer.finger_root"
    bl_label = "Finger Root Guide"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(
        items=(
            ("CAPTURE_FACE", "Capture Face", "Capture one root face in Mesh Edit Mode."),
            ("CAPTURE_DIRECTION", "Lock Direction", "Use the nearest Main Rig finger chain as the direction line; fall back to two selected vertices."),
            ("CHECK", "Check", "Validate the active finger root guide."),
            ("PREVIEW", "Preview", "Show the active root and direction guide in the viewport."),
            ("HIDE_PREVIEW", "Hide Preview", "Hide the active finger root preview."),
            ("CLEAR", "Clear", "Clear the active finger root guide."),
            ("APPLY", "Apply", "Place the selected existing finger chain at the active root."),
        ),
        default="CHECK",
    )

    @classmethod
    def poll(cls, context):
        return rig_page_active(context, "BODY") or active_ui_page(context) == UI_PAGE_MISC

    def execute(self, context):
        settings = _settings(context)
        if settings is None:
            self.report({"ERROR"}, "Finger root state is unavailable.")
            return {"CANCELLED"}
        _ensure_slots(settings)
        try:
            if self.action == "HIDE_PREVIEW":
                _clear_preview(context)
                _set_status(settings, "INFO", "Finger root preview hidden.")
                return {"FINISHED"}
            if self.action == "CLEAR":
                _root_state, slot = _active_slot(context)
                slot.configured = False
                slot.mesh_object = ""
                slot.face_index = -1
                slot.face_signature = ""
                slot.surface_point = (0.0, 0.0, 0.0)
                slot.normal = (0.0, 0.0, 1.0)
                slot.direction_start = (0.0, 0.0, 0.0)
                slot.direction_end = (0.0, 0.0, 0.0)
                _clear_preview(context)
                _set_status(settings, "INFO", f"{settings.active_finger.title()} guide cleared.")
                return {"FINISHED"}
            if self.action == "CAPTURE_FACE":
                _capture_face(context)
                return {"FINISHED"}
            if self.action == "CAPTURE_DIRECTION":
                _capture_direction(context)
                return {"FINISHED"}

            _root_state, slot = _active_slot(context)
            solution = _guide_solution(settings, slot)
            if self.action == "CHECK":
                _clear_preview(context)
                _set_status(
                    settings,
                    "SUCCESS",
                    f"{settings.active_finger.title()} guide valid; line gap {solution['line_gap']:.4f} m, inward depth {solution['inward_depth']:.4f} m.",
                )
                return {"FINISHED"}
            if self.action == "PREVIEW":
                solution["direction_start"] = Vector(slot.direction_start)
                solution["direction_end"] = Vector(slot.direction_end)
                points = _build_preview_points(context, solution, settings.active_finger)
                global _PREVIEW, _PREVIEW_BATCHES, _PREVIEW_FAILED
                _PREVIEW = {"points": points}
                _PREVIEW_BATCHES = None
                _PREVIEW_FAILED = False
                _ensure_preview_handler()
                _set_status(settings, "INFO", "Finger root preview shown; blue=surface/guide, orange=bone chain.")
                _tag_redraw()
                return {"FINISHED"}
            changed = _apply(context, settings, slot, solution)
            _clear_preview(context)
            _set_status(
                settings,
                "SUCCESS",
                f"Placed {changed} {settings.active_finger.title()} bone(s); weights and bone rolls were unchanged.",
            )
            return {"FINISHED"}
        except (FingerRootError, RuntimeError, ValueError, TypeError, ReferenceError) as exc:
            _set_status(settings, "WARNING", str(exc))
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


def draw_finger_root_controls(layout, context):
    settings = _settings(context)
    if settings is None:
        layout.label(text="Finger root state is unavailable.", icon="ERROR")
        return
    slots = _ensure_slots(settings)
    box = layout.box()
    box.label(text="Finger Root / Placement", icon="BONE_DATA")
    box.prop(settings, "active_finger", text="Finger")
    slot = slots.get(settings.active_finger)
    if slot is None:
        return
    if slot.mesh_object:
        box.label(
            text=("Center face + direction locked" if slot.configured else "Center face locked; direction missing"),
            icon="CHECKMARK" if slot.configured else "INFO",
        )
        box.label(text=slot.mesh_object, icon="MESH_DATA")
    else:
        box.label(text="Lock one center face, then lock the Main Rig direction.", icon="INFO")
    box.prop(settings, "finger_side", text="Direction Side")
    row = box.row(align=True)
    row.enabled = context.mode == "EDIT_MESH"
    row.operator("character_designer.finger_root", text="Lock Center Face", icon="FACESEL").action = "CAPTURE_FACE"
    row.operator("character_designer.finger_root", text="Lock Direction", icon="ARROW_LEFTRIGHT").action = "CAPTURE_DIRECTION"
    settings_row = box.row(align=True)
    settings_row.prop(settings, "root_depth")
    settings_row.prop(settings, "bone_length_scale", text="Length")
    box.prop(settings, "flip_normal")
    row = box.row(align=True)
    row.operator("character_designer.finger_root", text="Check", icon="VIEWZOOM").action = "CHECK"
    row.operator("character_designer.finger_root", text="Preview", icon="HIDE_ON").action = "PREVIEW"
    row.operator("character_designer.finger_root", text="Clear", icon="X").action = "CLEAR"
    apply_row = box.row()
    armature = _armature(context)
    apply_row.enabled = armature is not None and armature.mode == "EDIT"
    apply_row.operator("character_designer.finger_root", text="Apply to Selected Chain", icon="FILE_TICK").action = "APPLY"
    if armature is None or armature.mode != "EDIT":
        box.label(text="Apply requires the target Armature in Edit Mode.", icon="INFO")
    if settings.status:
        icon = {"SUCCESS": "CHECKMARK", "WARNING": "ERROR", "ERROR": "ERROR"}.get(
            settings.status_level, "INFO"
        )
        box.label(text=settings.status, icon=icon)


FINGER_ROOT_CLASSES = (
    CharacterDesignerFingerRootSlot,
    CharacterDesignerFingerRootState,
    CHARACTERDESIGNER_OT_finger_root,
)


def register_finger_root_runtime():
    """The draw handler is created lazily by Preview."""


def unregister_finger_root_runtime():
    global _PREVIEW_HANDLE, _PREVIEW_SHADER
    _clear_preview(bpy.context)
    if _PREVIEW_HANDLE is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_PREVIEW_HANDLE, "WINDOW")
        except Exception:
            pass
        _PREVIEW_HANDLE = None
    _PREVIEW_SHADER = None
