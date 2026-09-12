"""Explicit edits of captured forearm loops; never rediscover on pose updates."""
import copy
import math

import bpy
from bpy.props import EnumProperty, IntProperty
from bpy.types import Operator
from mathutils import Vector

from . import forearm_twist_profile as profile
from . import forearm_twist_topology as topology


def runtime():
    from . import forearm_twist
    return forearm_twist


def bounded_record(record):
    """Opt in when editing legacy captures, without regenerating their ratios."""
    record.setdefault("range_start", 0)
    record.setdefault("range_end", len(record["rings"]) - 1)
    record.setdefault("current_ring", min(2, len(record["rings"]) - 1))
    record.setdefault("curve_strength", .4)
    record.setdefault("transition", .1)
    profile.record_range(record)
    if type(record["current_ring"]) is not int or not 0 <= record["current_ring"] < len(record["rings"]):
        raise ValueError("The saved current loop is invalid; recapture or undo the damaged record.")
    return record


def edit(context, change):
    rt = runtime()
    if rt._SESSION is None:
        raise rt.ForearmTwistError("Start a calibration preview first.")
    session = rt._SESSION
    obj = session["mesh"]
    before = rt._records(obj)
    records = copy.deepcopy(before)
    record = bounded_record(records[session["side"]])
    change(record)
    profile.record_range(record)
    if records == before:
        # Blender may commit an unchanged RNA field when focus moves to a
        # button. Such a commit is not an edit and must not consume Undo/Redo.
        rt._update_ui(context)
        return False
    if session.get("symmetry"):
        # Validate the complete requested mirror before accepting this edit.
        rt._prepare_mirror(obj, session["side"], copy.deepcopy(record), records=copy.deepcopy(records))
    rt._apply_runtime_records(obj, records, context.evaluated_depsgraph_get())
    session.setdefault("history", []).append(before)
    session["redo"] = []
    rt._ERRORS.pop(obj.name, None)
    rt._update_ui(context)
    context.view_layer.update()
    return True


def undo(context, *, redo=False):
    rt = runtime()
    session = rt._SESSION
    if session is None:
        return
    source = session.setdefault("redo" if redo else "history", [])
    destination = session.setdefault("history" if redo else "redo", [])
    if not source:
        return
    obj = session["mesh"]
    current = rt._records(obj)
    target = len(source) - 1
    while target >= 0 and source[target] == current:
        target -= 1
    if target < 0:
        source.clear()
        rt._update_ui(context)
        return False
    # Also recover already-open sessions containing older no-op snapshots.
    # Leave both stacks untouched until the effective state applies safely.
    rt._apply_runtime_records(obj, copy.deepcopy(source[target]), context.evaluated_depsgraph_get())
    destination.append(current)
    del source[target:]
    rt._update_ui(context)
    return True


def set_current(context, index):
    rt = runtime()
    if rt._SESSION is None:
        return
    obj, side = rt._SESSION["mesh"], rt._SESSION["side"]
    records = rt._records(obj)
    record = bounded_record(records[side])
    record["current_ring"] = min(max(int(index), 0), len(record["rings"]) - 1)
    rt._write_records(obj, records)
    rt._update_ui(context)


def set_range(context, start, end):
    def change(record):
        profile.validate_range(record["rings"], start, end)
        record.update(range_start=start, range_end=end)
    edit(context, change)


def set_ratio(context, index, value):
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Twist share must be between zero and one.")
    def change(record):
        first, last = profile.record_range(record)
        if not first <= index <= last:
            raise ValueError("The current loop is outside the correction range; move the boundary first.")
        record["rings"][index]["ratio"] = float(value)
    edit(context, change)


def apply_default_profile(context, k=.4):
    def change(record):
        first, last = profile.record_range(record)
        record["rings"] = profile.apply_default(record["rings"], first, last, k)
        record["curve_strength"] = k
    edit(context, change)


def apply_batch(context, current, count, stride, value):
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Twist share must be between zero and one.")
    def change(record):
        first, last = profile.record_range(record)
        if current < first:
            raise ValueError("Move the current loop inside the correction range first.")
        for index in profile.batch_indices(current, count, stride, last):
            record["rings"][index]["ratio"] = float(value)
    edit(context, change)


def smooth_profile(context):
    def change(record):
        first, last = profile.record_range(record)
        rings = record["rings"]
        before = [ring["ratio"] for ring in rings]
        for index in range(first + 1, last):
            t = ((rings[index]["position"] - rings[index - 1]["position"]) /
                 (rings[index + 1]["position"] - rings[index - 1]["position"]))
            neighbor = before[index - 1] * (1 - t) + before[index + 1] * t
            rings[index]["ratio"] = .5 * (before[index] + neighbor)
    edit(context, change)


def overlay_geometry(context, all_rings=False):
    """Actual deformed control-cage cycles, including the owned correction keys."""
    rt = runtime()
    if rt._SESSION is None:
        return []
    session = rt._SESSION
    obj, arm = session["mesh"], session["armature"]
    record = rt._records(obj)[session["side"]]
    if rt._topology(obj.data) != record["topology"]:
        return []
    first, last = profile.record_range(record)
    current = record.get("current_ring", first)
    layers = ([(index, "candidate", (.6, .65, .68, .35), 1.) for index in range(len(record["rings"]))]
              if all_rings else [])
    layers += [(first, "start", (.40, .80, 1., 1.), 6.),
               (last, "end", (.40, .80, 1., 1.), 6.),
               (current, "current", (1., .94, .12, 1.), 2.5)]
    indices = {i for index, *_rest in layers for i in record["rings"][index]["vertices"]}
    depsgraph = context.evaluated_depsgraph_get()
    eval_arm = arm.evaluated_get(depsgraph)
    to_arm = eval_arm.matrix_world.inverted() @ obj.evaluated_get(depsgraph).matrix_world
    mixed = rt._input_mix(obj, indices, set(), depsgraph)
    weights = rt._weights(obj, arm, indices)
    points = {}
    for index in indices:
        point = to_arm @ mixed[index]
        values = weights[index]
        posed = (sum((w * (eval_arm.pose.bones[name].matrix @ arm.data.bones[name].matrix_local.inverted() @ point)
                      for name, w in values.items()), Vector()) if values else point)
        points[index] = eval_arm.matrix_world @ posed
    return [dict(index=index, kind=kind, color=color, width=width,
                 points=[points[i] for i in record["rings"][index]["vertices"] + record["rings"][index]["vertices"][:1]])
            for index, kind, color, width in layers]


def manual_add_loop(context, obj, side, ring):
    rt = runtime()
    if rt._SESSION is not None:
        raise ValueError("Confirm this preview before adding a selected mesh loop.")
    arm, rig = rt._resolve_rig(obj, side)
    records = rt._records(obj)
    if side not in records:
        raise ValueError("Capture a range first, or use the selected loop as a seed.")
    record = bounded_record(rt._current_record(obj, arm, rig, side, records[side], records))
    anchors = [frozenset(record["rings"][record[name]]["vertices"])
               for name in ("range_start", "range_end", "current_ring")]
    rings = topology.append_ring(obj, arm, rig["chain"][1], record["rings"], ring["vertices"])
    for item in rings:
        item.setdefault("ratio", rt.profile_ratio(item["position"], profile.profile_knots(record["rings"])))
    fresh = rt._capture_record(obj, arm, rig, side, {s: r for s, r in records.items() if s != side},
                               owned_record=record, rings_override=rings)
    fresh.update({key: value for key, value in record.items()
                  if key not in {"rings", "vertices", "positions", "range_start", "range_end", "current_ring"}})
    for name, anchor in zip(("range_start", "range_end", "current_ring"), anchors):
        fresh[name] = next(i for i, item in enumerate(rings) if frozenset(item["vertices"]) == anchor)
    records[side] = fresh
    if record.get("paired"):
        # Mirrored missing loops must be captured as a pair, or neither is committed.
        records, _ = rt._prepare_mirror(obj, side, fresh, records=records, complete_missing=True)
    rt._apply_runtime_records(obj, records, context.evaluated_depsgraph_get())


def mirrored_capture(obj, arm, lower_name, rings):
    """Map confirmed topology across the Basis, then measure the opposite axis."""
    from mathutils.kdtree import KDTree
    coords = obj.data.shape_keys.reference_key.data if obj.data.shape_keys else obj.data.vertices
    tree = KDTree(len(coords))
    for index, point in enumerate(coords):
        tree.insert(point.co, index)
    tree.balance()
    tolerance = max(1.e-7, max((p.co.length for p in coords), default=1.) * 1.e-6)
    result = []
    for ring in rings:
        mapped = []
        for index in ring["vertices"]:
            point = coords[index].co
            candidates = tree.find_range(Vector((-point.x, point.y, point.z)), tolerance)
            if len(candidates) != 1:
                raise ValueError("No unique opposite loop was found; turn off Mirror Calibration or repair the selection.")
            mapped.append(candidates[0][1])
        captured = topology.capture_loop(obj, arm, lower_name, mapped)
        captured["ratio"] = ring["ratio"]
        result.append(captured)
    return result


def draw_session(layout, context, record):
    rt = runtime()
    settings = context.window_manager.character_designer_forearm_twist
    for notice in rt._SESSION.get("notices", []):
        import textwrap
        for line in textwrap.wrap(notice, 42):
            layout.label(text=line, icon="INFO")
    first, last = profile.record_range(record)
    current = record["current_ring"]
    ring = record["rings"][current]
    row = layout.row()
    row.enabled = not rt._SESSION.get("pose_locked")
    row.prop(settings, "test_angle", slider=True)
    if rt._SESSION.get("pose_locked"):
        layout.label(text="Editing at the animated pose", icon="KEY_HLT")
    row = layout.row(align=True)
    row.prop(settings, "range_start")
    row.prop(settings, "range_end")
    row = layout.row(align=True)
    row.operator("character_designer.forearm_loop_edit", text="", icon="TRIA_LEFT").action = "PREVIOUS"
    row.prop(settings, "ring_index", text="Current")
    row.operator("character_designer.forearm_loop_edit", text="", icon="TRIA_RIGHT").action = "NEXT"
    row.operator("character_designer.forearm_loop_pick", text="Pick", icon="EYEDROPPER")
    layout.label(text=f"Loop {current + 1} / {len(record['rings'])} · {len(ring['vertices'])} vertices")
    row = layout.row(align=True)
    row.operator("character_designer.forearm_loop_edit", text="Set Start").action = "START"
    row.operator("character_designer.forearm_loop_edit", text="Set End").action = "END"
    row = layout.row()
    row.enabled = first <= current <= last
    row.prop(settings, "ratio", slider=True)
    try:
        angle = rt.current_twist_angle(context)
        layout.label(text=f"Target: {math.degrees(angle) * ring['ratio']:.1f}° · Wrist: {math.degrees(angle):.1f}°")
    except (ValueError, KeyError):
        layout.label(text="Current angle unavailable", icon="ERROR")
    blend = profile.range_influence(ring["position"], record["rings"], first, last, record.get("transition", .1))
    if blend < .999:
        layout.label(text=f"Extra correction blend: {blend:.0%}", icon="INFO")
    layout.prop(settings, "show_batch", icon="TRIA_DOWN" if settings.show_batch else "TRIA_RIGHT", emboss=False)
    if settings.show_batch:
        box = layout.box()
        box.prop(settings, "boundary_step")
        row = box.row(align=True)
        for action, text in (("START_BACK", "Start −"), ("START_FORWARD", "Start +"),
                             ("END_BACK", "End −"), ("END_FORWARD", "End +")):
            row.operator("character_designer.forearm_loop_edit", text=text).action = action
        row = box.row(align=True)
        row.prop(settings, "batch_count")
        row.prop(settings, "batch_stride")
        box.prop(settings, "batch_ratio", slider=True)
        batch = box.column()
        batch.enabled = first <= current <= last
        if batch.enabled:
            indices = profile.batch_indices(current, settings.batch_count, settings.batch_stride, last)
            batch.label(text=f"{len(indices)} loops: {indices[0] + 1} → {indices[-1] + 1}")
        batch.operator("character_designer.forearm_loop_edit", text="Apply Share to Batch").action = "BATCH"
        box.prop(settings, "curve_strength", slider=True)
        box.operator("character_designer.forearm_loop_edit", text="Apply Default Distribution").action = "DEFAULT"
        box.operator("character_designer.forearm_loop_edit", text="Smooth Selected Range Once").action = "SMOOTH"
        box.prop(settings, "transition", slider=True)
    row = layout.row(align=True)
    row.operator("character_designer.forearm_loop_edit", text="Undo Edit", icon="LOOP_BACK").action = "UNDO"
    row.operator("character_designer.forearm_loop_edit", text="Redo", icon="LOOP_FORWARDS").action = "REDO"
    row = layout.row(align=True)
    row.operator("character_designer.forearm_twist_finish", text="Confirm", icon="CHECKMARK").action = "CONFIRM"
    row.operator("character_designer.forearm_twist_finish", text="Cancel", icon="X").action = "CANCEL"
    layout.label(text="Esc restores pose and this edit's settings.")


class CHARACTERDESIGNER_OT_forearm_loop_add(Operator):
    bl_idname = "character_designer.forearm_loop_add"
    bl_label = "Add Selected Loop"
    bl_description = "Append a missing selected closed mesh loop to the saved capture"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        rt = runtime()
        obj = rt.context_mesh(context)
        was_edit = bool(obj and obj.mode == "EDIT")
        try:
            side = context.window_manager.character_designer_forearm_twist.side
            arm, rig = rt._resolve_rig(obj, side)
            captured = topology.selected_loop(obj, arm, rig["chain"][1])
            if was_edit:
                bpy.ops.object.mode_set(mode="OBJECT")
            manual_add_loop(context, obj, side, captured)
            return {"FINISHED"}
        except (ValueError, RuntimeError, KeyError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        finally:
            if was_edit and obj.mode != "EDIT":
                bpy.ops.object.mode_set(mode="EDIT")


class CHARACTERDESIGNER_OT_forearm_loop_edit(Operator):
    bl_idname = "character_designer.forearm_loop_edit"
    bl_label = "Edit Captured Forearm Loops"
    bl_options = {"REGISTER"}
    action: EnumProperty(items=tuple((name, name.title(), "") for name in
        ("PREVIOUS", "NEXT", "START", "END", "START_BACK", "START_FORWARD", "END_BACK", "END_FORWARD",
         "BATCH", "DEFAULT", "SMOOTH", "UNDO", "REDO")))

    def execute(self, context):
        rt = runtime()
        try:
            if rt._SESSION is None:
                raise ValueError("Start a calibration preview first.")
            settings = context.window_manager.character_designer_forearm_twist
            record = rt._records(rt._SESSION["mesh"])[rt._SESSION["side"]]
            first, last = profile.record_range(record)
            current = record["current_ring"]
            action = self.action
            if action in {"PREVIOUS", "NEXT"}:
                set_current(context, current + (1 if action == "NEXT" else -1))
            elif action in {"START", "END"}:
                set_range(context, current if action == "START" else first, current if action == "END" else last)
            elif action.startswith(("START_", "END_")):
                offset = settings.boundary_step * (-1 if action.endswith("BACK") else 1)
                set_range(context, first + offset if action.startswith("START") else first,
                          last + offset if action.startswith("END") else last)
            elif action == "BATCH":
                apply_batch(context, current, settings.batch_count, settings.batch_stride, settings.batch_ratio)
            elif action == "DEFAULT":
                apply_default_profile(context, settings.curve_strength)
            elif action == "SMOOTH":
                smooth_profile(context)
            elif action in {"UNDO", "REDO"}:
                undo(context, redo=action == "REDO")
            return {"FINISHED"}
        except (ValueError, RuntimeError, KeyError) as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}


class CHARACTERDESIGNER_OT_forearm_loop_pick(Operator):
    bl_idname = "character_designer.forearm_loop_pick"
    bl_label = "Pick Captured Loop"
    bl_description = "Click a captured loop on the deformed mesh; Esc cancels selection"

    def invoke(self, context, _event):
        if runtime()._SESSION is None:
            return {"CANCELLED"}
        runtime()._SESSION["picking"] = True
        context.window_manager.modal_handler_add(self)
        runtime()._redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        rt = runtime()
        if rt._SESSION is None:
            return {"CANCELLED"}
        if event.type in {"ESC", "RIGHTMOUSE"}:
            rt._SESSION["picking"] = False
            rt._redraw()
            return {"CANCELLED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            from bpy_extras.view3d_utils import location_3d_to_region_2d
            best = (18., None)
            # Sidebar invocation must project against a WINDOW region, not the sidebar.
            for area in context.screen.areas:
                if area.type != "VIEW_3D":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW" and
                               r.x <= event.mouse_x < r.x + r.width and r.y <= event.mouse_y < r.y + r.height), None)
                if region is None:
                    continue
                mouse = Vector((event.mouse_x - region.x, event.mouse_y - region.y))
                for layer in overlay_geometry(context, True):
                    if layer["kind"] != "candidate":
                        continue
                    pts = [location_3d_to_region_2d(region, area.spaces.active.region_3d, point) for point in layer["points"]]
                    for a, b in zip(pts, pts[1:]):
                        if a is None or b is None:
                            continue
                        delta = b - a
                        amount = max(0., min(1., (mouse - a).dot(delta) / max(delta.length_squared, 1.e-12)))
                        distance = (mouse - (a + delta * amount)).length
                        if distance < best[0]:
                            best = distance, layer["index"]
            if best[1] is not None:
                set_current(context, best[1])
                rt._SESSION["picking"] = False
                return {"FINISHED"}
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}


EDIT_CLASSES = (CHARACTERDESIGNER_OT_forearm_loop_edit, CHARACTERDESIGNER_OT_forearm_loop_pick,
                CHARACTERDESIGNER_OT_forearm_loop_add)
