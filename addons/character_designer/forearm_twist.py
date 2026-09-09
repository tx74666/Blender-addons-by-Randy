"""Local, artist-calibrated forearm twist through an owned inverse-skinning key.

The Armature remains the only skinning modifier.  Each update starts from the
current non-owned relative-key mix, never from last frame's corrective output.
Version one deliberately supports an ordinary LBS Armature first in the stack.
"""

import hashlib
import json
import math
import traceback
from array import array

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector

from . import limb_ik
from .forearm_twist_math import corrected_vertex, profile_ratio, twist_angle
from .forearm_twist_topology import detect_rings
from .forearm_twist_symmetry import mirror_ring_pairs
from .ui_constants import SIDEBAR_CATEGORY, rig_page_active

RECORD_KEY = "character_designer_forearm_twist_v1"
PREVIEW_KEY = "character_designer_forearm_twist_preview_v1"
LOCK_KEY = "character_designer_twist_render_lock"
KEY_PREFIX = "CD Forearm Twist."
MAX_ANGLE = math.radians(120.0)
_SESSION = None
_BUSY = False
_UI_BUSY = False
_CACHE = {}
_OUTPUT_CACHE = {}
_KEY_REFERENCES = {}
_ERRORS = {}
_DRAW_HANDLE = None
_RUNTIME_REGISTERED = False
_INITIALIZE_PENDING = False
_SCENE_CLEANUP_PENDING = False
_SCENE_STATE_ACTIVE = False


class ForearmTwistError(ValueError):
    pass


def _records(obj):
    value = obj.get(RECORD_KEY, "")
    if not value:
        return {}
    records = json.loads(value)
    if not isinstance(records, dict):
        raise ForearmTwistError("Forearm calibration data is invalid.")
    return records


def _write_records(obj, records):
    if records:
        obj[RECORD_KEY] = json.dumps(records, separators=(",", ":"), sort_keys=True)
    elif RECORD_KEY in obj:
        del obj[RECORD_KEY]
    _CACHE.pop(obj.as_pointer(), None)
    _OUTPUT_CACHE.pop(obj.as_pointer(), None)


def _managed_key(obj, side, record, *, required=True, repair_name=True):
    """Resolve the owned key without mistaking a renamed key for deleted data.

    RNA references survive ordinary renames while this runtime is loaded. They
    are deliberately discarded across file load and undo, and validated against
    the current Key datablock and its membership before being reused.
    """
    if not isinstance(record, dict) or not isinstance(record.get("key"), str):
        if required:
            raise ForearmTwistError("Forearm calibration data is invalid.")
        return None
    identity = (obj.as_pointer(), side)
    keys = obj.data.shape_keys
    cached = _KEY_REFERENCES.get(identity)
    if cached is not None:
        try:
            valid = (keys is not None and cached.id_data.as_pointer() == keys.as_pointer()
                     and any(key.as_pointer() == cached.as_pointer() for key in keys.key_blocks))
        except (ReferenceError, AttributeError):
            valid = False
        if not valid:
            _KEY_REFERENCES.pop(identity, None)
            cached = None
    expected = record["key"]
    named = keys.key_blocks.get(expected) if keys is not None else None
    if cached is not None:
        if not repair_name:
            return cached
        if named is not None and named.as_pointer() != cached.as_pointer():
            raise ForearmTwistError(f"Another Shape Key uses '{expected}'. Rename that other key, then retry.")
        if cached.name != expected:
            cached.name = expected
        return cached
    if named is not None:
        _KEY_REFERENCES[identity] = named
        return named
    if required:
        raise ForearmTwistError(
            f"Managed Shape Key '{expected}' is missing or renamed. Undo its removal or restore that exact name before continuing or removing calibration.")
    return None


def _remember_named_keys():
    # File evaluation may finish before load_post clears the old references.
    # Remember the now-live, correctly named keys even when no new graph event
    # occurs before the artist next edits a key name.
    for obj in bpy.data.objects:
        if obj.type != "MESH" or RECORD_KEY not in obj:
            continue
        try:
            for side, record in _records(obj).items():
                _managed_key(obj, side, record, required=False)
        except (ForearmTwistError, ValueError, TypeError, AttributeError):
            continue


def _topology(mesh):
    return hashlib.sha256(repr((len(mesh.vertices), tuple(tuple(e.vertices) for e in mesh.edges),
                               tuple(tuple(p.vertices) for p in mesh.polygons))).encode()).hexdigest()


def _rest_signature(armature, names):
    return [[name, list(v for row in armature.data.bones[name].matrix_local for v in row)] for name in names]


def _check_mesh(obj):
    if obj is None or obj.type != "MESH":
        raise ForearmTwistError("Select the character's bound body Mesh in Object Mode.")
    if obj.library or obj.data.library or obj.override_library or obj.data.users != 1:
        raise ForearmTwistError("Use a local Mesh with single-user data.")
    if not obj.modifiers or obj.modifiers[0].type != "ARMATURE":
        raise ForearmTwistError("This prototype needs Armature first in the modifier stack (no preceding Mirror).")
    mod = obj.modifiers[0]
    if sum(m.type == "ARMATURE" for m in obj.modifiers) != 1 or mod.object is None:
        raise ForearmTwistError("Use exactly one Armature modifier with a rig assigned.")
    if (mod.use_deform_preserve_volume or mod.use_bone_envelopes or mod.use_multi_modifier
            or not mod.use_vertex_groups or mod.vertex_group):
        raise ForearmTwistError("Use ordinary Vertex Groups skinning: Preserve Volume, envelopes, Multi Modifier and mask off.")
    if not mod.show_viewport or not mod.show_render:
        raise ForearmTwistError("The Armature must be enabled in the viewport and render.")
    if obj.data.shape_keys and not obj.data.shape_keys.use_relative:
        raise ForearmTwistError("This prototype supports relative Shape Keys only.")
    if obj.show_only_shape_key:
        raise ForearmTwistError("Turn off Shape Key pinning before calibrating.")
    return mod.object


def _resolve_rig(obj, side):
    armature = _check_mesh(obj)
    inventory = limb_ik._validate_inventory(armature)
    rig = inventory["rigs"].get(("ARM", side))
    if rig is None:
        detected = limb_ik.analyze_armature(armature)["limbs"]["ARM"][side]
        if detected["status"] not in {"READY", "WARNING"}:
            raise ForearmTwistError("Could not identify a unique upper-arm / forearm / hand chain.")
        chain = [detected[role] for role in ("upper", "lower", "end")]
        hand = armature.pose.bones[chain[2]]
        if any(not constraint.mute and constraint.influence > 0 for constraint in hand.constraints):
            raise ForearmTwistError("The Hand is driven by another rig; use a Character Designer Hand Target or an unconstrained hand bone.")
        return armature, {"chain": chain, "target": hand.bone, "fk_source": True}
    if inventory["target_rotation_version"] != limb_ik.TARGET_ROTATION_VERSION:
        raise ForearmTwistError("Rebuild Rig once to enable aligned Hand Target rotation.")
    return armature, rig


def _weights(obj, armature, indices):
    names = {group.index: group.name for group in obj.vertex_groups
             if group.name in armature.data.bones and armature.data.bones[group.name].use_deform}
    result = {}
    for index in indices:
        values = {names[g.group]: float(g.weight) for g in obj.data.vertices[index].groups
                  if g.group in names and g.weight > 0.0}
        total = sum(values.values())
        result[index] = {name: value / total for name, value in values.items()} if total else {}
    for name in {name for values in result.values() for name in values}:
        if armature.data.bones[name].bbone_segments != 1:
            raise ForearmTwistError("Bendy Bone segments are not supported by this prototype.")
    return result


def controller_status(armature, target_name):
    """Report the actual mesh route without changing the hand's input channel."""
    matching = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or RECORD_KEY not in obj:
            continue
        if not any(m.type == "ARMATURE" and m.object == armature for m in obj.modifiers):
            continue
        try:
            for side, record in _records(obj).items():
                # Resolve current controllers, including a rig built after FK
                # calibration; the deformation route depends on the chain.
                _arm, rig = _resolve_rig(obj, side)
                if rig["target"].name == target_name:
                    matching.append((obj, record))
        except (ValueError, KeyError, TypeError):
            return False, "Forearm Twist: check body calibration"
    if not matching:
        return False, "Forearm Twist: not calibrated"
    if any(obj.name in _ERRORS for obj, _record in matching):
        return False, "Forearm Twist paused: check body panel"
    active = sum(record.get("enabled", True) for _obj, record in matching)
    if active == len(matching):
        return True, "Forearm Twist controls sleeve rotation"
    if active:
        return False, "Forearm Twist: some meshes disabled"
    return False, "Forearm Twist disabled: original skinning"


def _input_mix(obj, indices, owned_names, depsgraph):
    keys = obj.data.shape_keys
    basis = keys.reference_key
    result = {i: basis.data[i].co.copy() for i in indices}
    evaluated_keys = keys.evaluated_get(depsgraph)
    for key in keys.key_blocks:
        if key == basis or key.name in owned_names or key.mute:
            continue
        evaluated_key = evaluated_keys.key_blocks.get(key.name)
        value = float(evaluated_key.value if evaluated_key is not None else key.value)
        if abs(value) < 1.0e-12:
            continue
        mask = obj.vertex_groups.get(key.vertex_group) if key.vertex_group else None
        for i in indices:
            factor = value
            if key.vertex_group:
                factor *= next((g.weight for g in obj.data.vertices[i].groups
                                if mask is not None and g.group == mask.index), 0.0)
            result[i] += (key.data[i].co - key.relative_key.data[i].co) * factor
    return result


def _set_render_lock(scene):
    active = False
    for obj in scene.objects:
        if obj.type != "MESH" or RECORD_KEY not in obj:
            continue
        try:
            active |= any(r.get("enabled", True) for r in _records(obj).values())
        except (ValueError, TypeError, AttributeError):
            _ERRORS[obj.name] = "Forearm calibration data is invalid."
    if active:
        if LOCK_KEY not in scene:
            scene[LOCK_KEY] = bool(scene.render.use_lock_interface)
        scene.render.use_lock_interface = True
    elif LOCK_KEY in scene:
        scene.render.use_lock_interface = bool(scene[LOCK_KEY])
        del scene[LOCK_KEY]


def _calculate_object(obj, depsgraph):
    records = _prepare_runtime_records(obj)
    if records != _records(obj):
        _apply_runtime_records(obj, records, depsgraph)
    else:
        _calculate_records(obj, depsgraph)


def _apply_runtime_records(obj, records, depsgraph):
    """Commit migration and its evaluated output together, or restore both."""
    old_json = obj.get(RECORD_KEY)
    old_index = obj.active_shape_key_index
    snapshots, added = [], []
    old_records = _records(obj)
    old_references = {identity: key for identity, key in _KEY_REFERENCES.items()
                      if identity[0] == obj.as_pointer()}
    for side, record in old_records.items():
        key = _managed_key(obj, side, record)
        coords = array("f", [0.0]) * (3 * len(key.data))
        key.data.foreach_get("co", coords)
        snapshots.append((key, coords, key.value, key.mute))
    try:
        for side, record in records.items():
            if side not in old_records:
                key = obj.shape_key_add(name=record["key"], from_mix=False)
                added.append(key)
                key.relative_key = obj.data.shape_keys.reference_key
                key.mute = True
                _KEY_REFERENCES[(obj.as_pointer(), side)] = key
        _write_records(obj, records)
        _calculate_records(obj, depsgraph)
    except Exception:
        for key in added:
            obj.shape_key_remove(key)
        for key, coords, value, mute in snapshots:
            key.data.foreach_set("co", coords)
            key.value, key.mute = value, mute
        if old_json is None:
            obj.pop(RECORD_KEY, None)
        else:
            obj[RECORD_KEY] = old_json
        for identity in tuple(_KEY_REFERENCES):
            if identity[0] == obj.as_pointer():
                _KEY_REFERENCES.pop(identity, None)
        _KEY_REFERENCES.update(old_references)
        _CACHE.pop(obj.as_pointer(), None)
        _OUTPUT_CACHE.pop(obj.as_pointer(), None)
        if obj.data.shape_keys:
            obj.data.shape_keys.update_tag()
        obj.data.update()
        raise
    finally:
        obj.active_shape_key_index = old_index


def _calculate_records(obj, depsgraph):
    records = _records(obj)
    if not records:
        return
    keys = obj.data.shape_keys
    if keys is None:
        raise ForearmTwistError("The managed corrective Shape Key was removed; remove this calibration and start again.")
    armature = _check_mesh(obj)
    if obj.mode == "EDIT":
        raise ForearmTwistError("Calibration pauses in Edit Mode; return to Object Mode to resume.")
    topology = _topology(obj.data)
    owned_names = {r["key"] for r in records.values()}
    # The shape output is managed data. Validate its structure even when pose
    # inputs have not changed, and repair value/mute after manual UI changes.
    managed_state = []
    managed_coordinates = []
    for side, record in records.items():
        key = _managed_key(obj, side, record)
        if key is None or key.relative_key != keys.reference_key or key.vertex_group:
            raise ForearmTwistError("The managed corrective key was renamed or changed; restore it or remove calibration.")
        managed_state.append((key.name, float(key.value), bool(key.mute)))
        coordinates = array("f", [0.0]) * (len(key.data) * 3)
        key.data.foreach_get("co", coordinates)
        managed_coordinates.append(coordinates.tobytes())
    basis_coordinates = array("f", [0.0]) * (len(keys.reference_key.data) * 3)
    keys.reference_key.data.foreach_get("co", basis_coordinates)
    all_indices = sorted({i for r in records.values() for i in r["vertices"]})
    base = _input_mix(obj, all_indices, owned_names, depsgraph)
    weights = _weights(obj, armature, all_indices)
    eval_arm = armature.evaluated_get(depsgraph)
    eval_obj = obj.evaluated_get(depsgraph)
    to_arm = eval_arm.matrix_world.inverted() @ eval_obj.matrix_world
    from_arm = to_arm.inverted()
    bone_names = {name for values in weights.values() for name in values}
    bone_names.update(name for r in records.values() for name in r["chain"])
    transforms = {name: eval_arm.pose.bones[name].matrix @ armature.data.bones[name].matrix_local.inverted()
                  for name in bone_names}
    signature = (obj[RECORD_KEY], topology, basis_coordinates.tobytes(), tuple(v for row in to_arm for v in row),
                 tuple((n, tuple(v for row in mat for v in row)) for n, mat in sorted(transforms.items())),
                 tuple((i, tuple(base[i]), tuple(sorted(weights[i].items()))) for i in all_indices))
    expected_state = [(r["key"], 1.0, not r.get("enabled", True)) for r in records.values()]
    if (_CACHE.get(obj.as_pointer()) == signature and managed_state == expected_state
            and _OUTPUT_CACHE.get(obj.as_pointer()) == managed_coordinates):
        return
    outputs = []
    for side, record in records.items():
        key = _managed_key(obj, side, record)
        if key is None or key.relative_key != keys.reference_key or key.vertex_group:
            raise ForearmTwistError("The managed corrective key was renamed or changed; restore it or remove calibration.")
        if record["armature"] != armature.name or record["topology"] != topology:
            raise ForearmTwistError("Mesh topology or rig binding changed; remove calibration and capture the loops again.")
        if record["rest"] != _rest_signature(armature, record["chain"]):
            raise ForearmTwistError("The rig is changing; Forearm Twist will resume when its arm chain is ready.")
        lower, hand = record["chain"][1:]
        if not record.get("enabled", True):
            outputs.append((key, False, []))
            continue
        lower_bone = armature.data.bones[lower]
        axis = (lower_bone.tail_local - lower_bone.head_local).normalized()
        pivot = armature.data.bones[hand].head_local
        angle = twist_angle(transforms[lower], transforms[hand], axis)
        if record.get("enabled", True) and abs(angle) > MAX_ANGLE + 1.0e-5:
            raise ForearmTwistError("Forearm twist is outside the prototype's -120 to +120 degree range.")
        knots = [(float(r["position"]), float(r["ratio"])) for r in record["rings"]]
        knots = [(0.0, 0.0)] + [(p, r) for p, r in knots if 1.0e-6 < p < 1.0 - 1.0e-6] + [(1.0, 1.0)]
        output = []
        for i, position in zip(record["vertices"], record["positions"]):
            ratio = profile_ratio(position, knots)
            point = to_arm @ base[i]
            corrected = corrected_vertex(point, transforms, weights[i], lower, hand, axis, pivot, ratio, angle=angle)
            output.append((i, keys.reference_key.data[i].co + (from_arm @ corrected - base[i])))
        outputs.append((key, record.get("enabled", True), output))
    # Commit only after every side has passed, so a failed inverse never leaves
    # a half-written mesh.  All unselected coordinates remain their Basis.
    for key, enabled, output in outputs:
        key.value = 1.0
        key.mute = not enabled
        # Relative keys store full coordinate snapshots: copying Basis outside
        # the sleeve is essential when the artist edits unrelated Basis points.
        key.data.foreach_set("co", basis_coordinates)
        for index, coordinate in output:
            key.data[index].co = coordinate
    keys.update_tag()
    obj.data.update()
    # Recopying a Key datablock during a render can replace its evaluated
    # animated values with the original values. Re-evaluate its animation too,
    # preserving other (e.g. facial) keys without writing to artist channels.
    keys.update_tag(refresh={"TIME"})
    _CACHE[obj.as_pointer()] = signature
    _OUTPUT_CACHE[obj.as_pointer()] = []
    for key, _enabled, _output in outputs:
        coordinates = array("f", [0.0]) * (len(key.data) * 3)
        key.data.foreach_get("co", coordinates)
        _OUTPUT_CACHE[obj.as_pointer()].append(coordinates.tobytes())
    _ERRORS.pop(obj.name, None)


def update_runtime(scene, depsgraph=None):
    global _BUSY
    if _BUSY:
        return
    _BUSY = True
    try:
        depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
        for obj in scene.objects:
            if obj.type != "MESH" or RECORD_KEY not in obj:
                continue
            try:
                _calculate_object(obj, depsgraph)
            except Exception as exc:
                _ERRORS[obj.name] = str(exc)
                _CACHE.pop(obj.as_pointer(), None)
                # Invalid inputs fall back to the original skinning, never a
                # stale correction from a different pose.
                if obj.data.shape_keys:
                    try:
                        failed_records = _records(obj)
                    except (ValueError, TypeError, AttributeError):
                        failed_records = {}
                    for side, record in failed_records.items():
                        key = _managed_key(obj, side, record, required=False, repair_name=False)
                        if key is not None and not key.mute:
                            key.mute = True
    finally:
        _BUSY = False


@persistent
def _graph_post(scene, depsgraph):
    if _INITIALIZE_PENDING or _SCENE_CLEANUP_PENDING:
        _complete_runtime_lifecycle()
    update_runtime(scene, depsgraph)


@persistent
def _frame_post(scene, depsgraph=None):
    if _INITIALIZE_PENDING or _SCENE_CLEANUP_PENDING:
        _complete_runtime_lifecycle()
    update_runtime(scene, depsgraph)


@persistent
def _load_post(_dummy):
    global _SESSION
    _SESSION = None
    if _INITIALIZE_PENDING or _SCENE_CLEANUP_PENDING:
        _complete_runtime_lifecycle()
    _CACHE.clear()
    _OUTPUT_CACHE.clear()
    _KEY_REFERENCES.clear()
    _ERRORS.clear()
    _recover_previews()
    _remember_named_keys()
    for scene in bpy.data.scenes:
        _set_render_lock(scene)
    update_runtime(bpy.context.scene)


@persistent
def _undo_post(_dummy):
    if _INITIALIZE_PENDING or _SCENE_CLEANUP_PENDING:
        _complete_runtime_lifecycle()
    _CACHE.clear()
    _OUTPUT_CACHE.clear()
    _KEY_REFERENCES.clear()
    _ERRORS.clear()
    _recover_previews()
    _remember_named_keys()
    update_runtime(bpy.context.scene)


@persistent
def _restore_preview(_dummy):
    if _SESSION is not None:
        finish_test(bpy.context, False)
    _recover_previews()


def _recover_previews():
    """Discard transient calibration snapshots restored by Undo/autosave.

    The interactive session lives in Python, but an unrelated Blender Undo
    operator can snapshot its temporary datablocks. A small serialized rollback
    marker keeps those snapshots reversible after the Python session is gone.
    """
    global _SESSION
    if _SESSION is not None:
        return
    for obj in tuple(bpy.data.objects):
        if obj.type != "MESH" or PREVIEW_KEY not in obj:
            continue
        try:
            marker = json.loads(obj[PREVIEW_KEY])
            arm = bpy.data.objects.get(marker["armature_name"])
            key = _managed_key(obj, marker["side"], {"key": KEY_PREFIX + marker["side"]}, required=False)
            marker.update(mesh=obj, armature=arm,
                          old_key_coordinates=([p.co.copy() for p in key.data]
                                               if marker.pop("had_calibration", False) and key is not None else None))
            _SESSION = marker
            finish_test(bpy.context, False, refresh=False)
        except (KeyError, ValueError, TypeError, ReferenceError, RuntimeError) as exc:
            _SESSION = None
            _ERRORS[obj.name] = f"Could not restore interrupted calibration: {exc}"


def _rotation_vector(quaternion):
    q = quaternion.normalized()
    if q.w < 0:
        q.negate()
    vector = Vector((q.x, q.y, q.z))
    length = vector.length
    # mathutils' axis-angle fallback is not a unit axis at tiny rotations.
    # Use the quaternion logarithm directly so finite-difference columns stay
    # accurate for the one-milliradian Jacobian perturbation.
    return vector * (2.0 if length < 1.0e-8 else 2.0 * math.atan2(length, q.w) / length)


def _test_pose(context, angle):
    """Replace axial twist while retaining the test's original wrist swing.

    Every requested angle starts from the captured pose. Quaternion increments
    avoid the XYZ parameter singularity at +/-90 degrees; only the existing
    Target's XYZ channels are written, with no new rig state or constraints.
    """
    from mathutils import Euler, Quaternion

    session = _SESSION
    arm = session["armature"]
    target = arm.pose.bones[session["target"]]
    lower, hand = session["chain"][1:]
    lower_bone = arm.data.bones[lower]
    axis = (lower_bone.tail_local - lower_bone.head_local).normalized()
    rest_hand = arm.data.bones[hand].matrix_local
    def evaluated_pose():
        arm.update_tag(refresh={"OBJECT"})
        context.view_layer.update()
        return arm.evaluated_get(context.evaluated_depsgraph_get()).pose

    original_mode = session.get("rotation_mode", "XYZ")
    if original_mode == "QUATERNION":
        original_rotation = Quaternion(session["quaternion"]).normalized()
    elif original_mode == "AXIS_ANGLE":
        original_angle, *original_axis = session["axis_angle"]
        original_axis = Vector(original_axis)
        original_rotation = (Quaternion(original_axis.normalized(), original_angle)
                             if original_axis.length_squared > 1.0e-12 else Quaternion())
    else:
        original_rotation = Euler(session["rotation"], original_mode).to_quaternion()
    target.rotation_euler = original_rotation.to_euler("XYZ")
    original_pose = evaluated_pose()
    lower_deform = original_pose.bones[lower].matrix @ arm.data.bones[lower].matrix_local.inverted()
    hand_deform = original_pose.bones[hand].matrix @ rest_hand.inverted()
    lower_rotation = lower_deform.to_quaternion().normalized()
    relative = lower_rotation.conjugated() @ hand_deform.to_quaternion().normalized()
    original_twist = twist_angle(lower_deform, hand_deform, axis)
    swing = (relative @ Quaternion(axis, -original_twist)).normalized()
    wanted = (lower_rotation @ swing @ Quaternion(axis, angle) @ rest_hand.to_quaternion()).normalized()
    for _ in range(18):
        actual = evaluated_pose().bones[hand].matrix.to_quaternion().normalized()
        residual = _rotation_vector(actual.inverted() @ wanted)
        if residual.length < 2.0e-5:
            update_runtime(context.scene, context.evaluated_depsgraph_get())
            return
        current = target.rotation_euler.copy()
        current_rotation = current.to_quaternion()
        columns = []
        step = 1.0e-3
        for axis_index in range(3):
            increment_axis = Vector((1.0 if index == axis_index else 0.0 for index in range(3)))
            perturbed_rotation = current_rotation @ Quaternion(increment_axis, step)
            target.rotation_euler = perturbed_rotation.to_euler("XYZ", current)
            perturbed = evaluated_pose().bones[hand].matrix.to_quaternion().normalized()
            columns.append(_rotation_vector(actual.inverted() @ perturbed) / step)
        target.rotation_euler = current
        jacobian = Matrix(columns).transposed()
        if abs(jacobian.determinant()) < 1.0e-7:
            # A constrained or otherwise unresponsive Target may still have
            # fewer than three responsive axes. Never invert that Jacobian.
            delta = jacobian.transposed() @ residual
        else:
            delta = jacobian.inverted() @ residual
        if delta.length > 0.7:
            delta *= 0.7 / delta.length
        if delta.length > 1.0e-10:
            next_rotation = current_rotation @ Quaternion(delta.normalized(), delta.length)
            target.rotation_euler = next_rotation.to_euler("XYZ", current)
    raise ForearmTwistError("Could not match the Hand Target to the test angle; the original pose will be restored.")


def _update_ui(context):
    global _UI_BUSY
    if _SESSION is None:
        return
    _UI_BUSY = True
    try:
        settings = context.window_manager.character_designer_forearm_twist
        rings = _records(_SESSION["mesh"])[_SESSION["side"]]["rings"]
        settings.ring_index = min(max(1, settings.ring_index), len(rings))
        settings.ratio = rings[settings.ring_index - 1]["ratio"]
    finally:
        _UI_BUSY = False
    _redraw()


def _ring_changed(_self, context):
    if not _UI_BUSY:
        _update_ui(context)


def set_ratio(context, index, value):
    if _SESSION is None:
        raise ForearmTwistError("Start a twist test first.")
    obj = _SESSION["mesh"]
    records = _records(obj)
    ring = records[_SESSION["side"]]["rings"][index]
    if ring["position"] <= 1.0e-6 or ring["position"] >= 1.0 - 1.0e-6:
        raise ForearmTwistError("The elbow and wrist anchors stay at 0% and 100%.")
    ring["ratio"] = min(1.0, max(0.0, float(value)))
    _write_records(obj, records)
    update_runtime(context.scene, context.evaluated_depsgraph_get())
    _redraw()


def _ratio_changed(self, context):
    if _UI_BUSY or _SESSION is None:
        return
    try:
        set_ratio(context, self.ring_index - 1, self.ratio)
    except Exception as exc:
        _ERRORS[_SESSION["mesh"].name] = str(exc)


def _angle_changed(self, context):
    if _UI_BUSY or _SESSION is None:
        return
    try:
        _test_pose(context, self.test_angle)
    except Exception as exc:
        obj_name = _SESSION["mesh"].name
        finish_test(context, False)
        _ERRORS[obj_name] = str(exc)


class CharacterDesignerForearmTwistState(PropertyGroup):
    side: EnumProperty(name="Arm", items=(("L", "Left Arm", ""), ("R", "Right Arm", "")), options={"SKIP_SAVE"})
    symmetry: BoolProperty(name="Mirror Calibration", default=True,
                           description="On Confirm, copy loop shares to the other arm; each hand keeps its own pose")
    ring_index: IntProperty(name="Loop", default=1, min=1, update=_ring_changed, options={"SKIP_SAVE"})
    ratio: FloatProperty(name="Twist Share", default=0.5, min=0.0, max=1.0, subtype="FACTOR", update=_ratio_changed, options={"SKIP_SAVE"})
    test_angle: FloatProperty(name="Test Angle", default=math.pi / 2, min=-MAX_ANGLE, max=MAX_ANGLE,
                              subtype="ANGLE", update=_angle_changed, options={"SKIP_SAVE"})


def _capture_record(obj, arm, rig, side, records, *, owned_record=None):
    """Capture this side from its own topology and weights, without writes."""
    rings = detect_rings(obj, arm, rig["chain"][1], rig["chain"][2])
    if len(rings) < 3:
        raise ForearmTwistError("Need at least three closed forearm loops; this topology could not be captured.")
    for ring in rings:
        t = min(1.0, max(0.0, ring["position"]))
        ring["ratio"] = t * t * (3 - 2 * t)
    vertices = sorted({i for ring in rings for i in ring["vertices"]})
    # Include irregular vertices in the corridor, but only when they are
    # connected to the captured sleeve and influenced by this forearm.
    lower = arm.data.bones[rig["chain"][1]]
    to_arm = arm.matrix_world.inverted() @ obj.matrix_world
    axis = (lower.tail_local - lower.head_local).normalized()
    coords = obj.data.shape_keys.reference_key.data if obj.data.shape_keys else obj.data.vertices
    positions = {v.index: (to_arm @ coords[v.index].co - lower.head_local).dot(axis) / lower.length for v in obj.data.vertices}
    all_weights = _weights(obj, arm, range(len(obj.data.vertices)))
    last_ring = max(r["position"] for r in rings)
    corridor = {i for i, p in positions.items() if p >= 0 and (
                (p <= last_ring and sum(all_weights[i].get(n, 0) for n in rig["chain"][1:]) > 0.5)
                or (p > 1.0 and all_weights[i].get(rig["chain"][1], 0.0) > 1.0e-6))}
    # The editable rings end at the wrist, but lower-bone weights often
    # continue into the palm. Carry the 100% endpoint through that existing
    # blend until the lower influence ends; stopping at the wrist ring
    # would leave a collapsed, uncorrected seam immediately beside it.
    selected = set(vertices)
    changed = True
    while changed:
        changed = False
        for edge in obj.data.edges:
            a, b = edge.vertices
            if a in selected and b in corridor and b not in selected:
                selected.add(b); changed = True
            if b in selected and a in corridor and a not in selected:
                selected.add(a); changed = True
    vertices = sorted(selected)
    # A loop is one artist control even when its vertices are not perfectly
    # coplanar. Every member must receive that loop's exact saved share.
    for ring in rings:
        for index in ring["vertices"]:
            positions[index] = ring["position"]
    if any(set(vertices) & set(other["vertices"]) for other in records.values()):
        raise ForearmTwistError("The captured sleeve overlaps an existing calibration.")
    key_name = owned_record["key"] if owned_record else KEY_PREFIX + side
    if owned_record:
        _managed_key(obj, side, owned_record)
    elif obj.data.shape_keys and key_name in obj.data.shape_keys.key_blocks:
        raise ForearmTwistError(f"An unowned key named {key_name} already exists.")
    record = {"version": 1, "armature": arm.name, "chain": list(rig["chain"]), "target": rig["target"].name,
              "topology": _topology(obj.data), "rest": _rest_signature(arm, rig["chain"]),
              "rings": rings, "vertices": vertices, "positions": [positions[i] for i in vertices],
              "key": key_name, "enabled": True, "created_basis": obj.data.shape_keys is None}
    return record


def _current_record(obj, arm, rig, side, record, records):
    """Keep the artist's loop profile through binding and rig reconstruction."""
    if (record["armature"] != arm.name or record["topology"] != _topology(obj.data)
            or record["chain"] != list(rig["chain"])):
        raise ForearmTwistError("Mesh topology or rig chain changed; remove calibration and capture again.")
    if record["rest"] == _rest_signature(arm, rig["chain"]):
        record["target"] = rig["target"].name
        return record
    fresh = _capture_record(obj, arm, rig, side, {s: r for s, r in records.items() if s != side}, owned_record=record)
    old_rings = {frozenset(r["vertices"]): r for r in record["rings"]}
    if (len(old_rings) != len(fresh["rings"])
            or any(frozenset(r["vertices"]) not in old_rings for r in fresh["rings"])):
        raise ForearmTwistError("The captured loops changed; remove calibration and capture again.")
    for ring in fresh["rings"]:
        ring["ratio"] = old_rings[frozenset(ring["vertices"])]["ratio"]
    fresh["created_basis"] = record.get("created_basis", False)
    fresh["enabled"] = record.get("enabled", True)
    for name in ("paired", "profile_source"):
        if name in record:
            fresh[name] = record[name]
    return fresh


def _prepare_runtime_records(obj):
    """Read-only migration: profiles persist, current rig frames are recaptured."""
    records = _records(obj)
    if not records or PREVIEW_KEY in obj or (_SESSION and _SESSION.get("mesh") == obj):
        return records
    arm = _check_mesh(obj)
    if obj.mode == "EDIT":
        raise ForearmTwistError("Forearm Twist resumes after leaving Mesh Edit Mode.")
    if arm.mode == "EDIT":
        raise ForearmTwistError("Forearm Twist resumes after leaving bone Edit Mode.")
    for side in tuple(records):
        _arm, rig = _resolve_rig(obj, side)
        records[side] = _current_record(obj, arm, rig, side, records[side], records)
    # Old files did not have a paired flag. They migrate to the symmetric UI.
    # Explicit single-side Python calls remain useful for non-paired fixtures.
    if any(record.get("paired", True) for record in records.values()):
        preferred = next((r.get("profile_source") for r in records.values()
                          if r.get("profile_source") in records), None)
        source_side = preferred or ("L" if "L" in records else "R")
        source = records[source_side]
        records, _other = _prepare_mirror(obj, source_side, source, records=records)
    return records


def _prepare_mirror(obj, source_side, source_record=None, *, records=None):
    records = _records(obj) if records is None else records
    source = source_record if source_record is not None else records.get(source_side)
    if source is None:
        raise ForearmTwistError("Calibrate this arm before syncing the other arm.")
    arm, source_rig = _resolve_rig(obj, source_side)
    source = _current_record(obj, arm, source_rig, source_side, source, records)
    target_side = "R" if source_side == "L" else "L"
    _arm, target_rig = _resolve_rig(obj, target_side)
    target = records.get(target_side)
    if target is not None:
        target = _current_record(obj, arm, target_rig, target_side, target, records)
        _managed_key(obj, target_side, target)
    else:
        target = _capture_record(obj, arm, target_rig, target_side, {**records, source_side: source})
        # The source calibration owns a Basis if one was needed.
        target["created_basis"] = False
    for source_index, target_index in mirror_ring_pairs(obj, arm, source, target):
        target["rings"][target_index]["ratio"] = source["rings"][source_index]["ratio"]
    source["target"] = source_rig["target"].name
    target["target"] = target_rig["target"].name
    target["enabled"] = source.get("enabled", True)
    for record in (source, target):
        record["paired"] = True
        record["profile_source"] = source_side
    records[source_side] = source
    records[target_side] = target
    return records, target_side


def mirror_calibration(context, obj, source_side, *, _from_test=False):
    """Copy shares atomically; poses, weights and side-specific support stay local."""
    global _BUSY
    if _SESSION is not None and not _from_test:
        raise ForearmTwistError("Confirm or cancel the current test first.")
    records, target_side = _prepare_mirror(obj, source_side)
    old_json = obj.get(RECORD_KEY)
    old_index = obj.active_shape_key_index
    old_errors = _ERRORS.get(obj.name)
    snapshots = []
    for side, record in _records(obj).items():
        key = _managed_key(obj, side, record)
        coordinates = array("f", [0.0]) * (len(key.data) * 3)
        key.data.foreach_get("co", coordinates)
        snapshots.append((key, coordinates, key.value, key.mute))
    added = None
    previous_busy = _BUSY
    _BUSY = True
    try:
        if target_side not in _records(obj):
            added = obj.shape_key_add(name=records[target_side]["key"], from_mix=False)
            added.relative_key = obj.data.shape_keys.reference_key
            _KEY_REFERENCES[(obj.as_pointer(), target_side)] = added
        _write_records(obj, records)
        context.view_layer.update()
        _calculate_object(obj, context.evaluated_depsgraph_get())
    except Exception:
        if added is not None:
            obj.shape_key_remove(added)
            _KEY_REFERENCES.pop((obj.as_pointer(), target_side), None)
        for key, coordinates, value, mute in snapshots:
            key.data.foreach_set("co", coordinates)
            key.value, key.mute = value, mute
        obj[RECORD_KEY] = old_json
        _CACHE.pop(obj.as_pointer(), None)
        _OUTPUT_CACHE.pop(obj.as_pointer(), None)
        if old_errors is None:
            _ERRORS.pop(obj.name, None)
        else:
            _ERRORS[obj.name] = old_errors
        if obj.data.shape_keys:
            obj.data.shape_keys.update_tag()
        obj.data.update()
        raise
    finally:
        obj.active_shape_key_index = old_index
        _BUSY = previous_busy
    _set_render_lock(context.scene)
    context.view_layer.update()
    _redraw()
    return records[target_side]


def context_mesh(context):
    """Resolve the body while an artist is manipulating its hand controller."""
    obj = context.object
    if obj is None or obj.type == "MESH":
        return obj
    if obj.type != "ARMATURE":
        return None
    meshes = [candidate for candidate in context.scene.objects if candidate.type == "MESH"
              and any(mod.type == "ARMATURE" and mod.object == obj for mod in candidate.modifiers)]
    calibrated = [candidate for candidate in meshes if RECORD_KEY in candidate]
    candidates = calibrated or meshes
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ForearmTwistError("Select the body Mesh once to identify which mesh to calibrate.")
    return None


def _restore_selection(context, selection):
    if not selection:
        return
    obj = context.view_layer.objects.get(selection["active"])
    if obj is None:
        return
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for candidate in context.selected_objects:
        candidate.select_set(False)
    for name in selection["selected"]:
        candidate = context.view_layer.objects.get(name)
        if candidate is not None:
            candidate.select_set(True)
    context.view_layer.objects.active = obj
    if selection["mode"] == "POSE" and obj.type == "ARMATURE":
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="POSE")


def start_paired_test(context):
    obj = context_mesh(context)
    if obj is None:
        raise ForearmTwistError("Select the body Mesh or a Hand Target.")
    if context.mode not in {"OBJECT", "POSE"}:
        raise ForearmTwistError("Finish editing, then start the twist test.")
    arm = _check_mesh(obj)
    side = "L"
    active = context.active_pose_bone
    if active is not None:
        for candidate_side in ("L", "R"):
            _arm, rig = _resolve_rig(obj, candidate_side)
            if active.name in (rig["target"].name, *rig["chain"]):
                side = candidate_side
                break
    selection = {"active": context.object.name, "mode": context.mode,
                 "selected": [candidate.name for candidate in context.selected_objects]}
    try:
        if context.mode == "POSE":
            bpy.ops.object.mode_set(mode="OBJECT")
        for candidate in context.selected_objects:
            candidate.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        context.view_layer.update()
        record = start_test(context, obj, side, symmetry=True)
        _SESSION["selection"] = selection
        marker = json.loads(obj[PREVIEW_KEY])
        marker["selection"] = selection
        obj[PREVIEW_KEY] = json.dumps(marker)
        return record
    except Exception:
        _restore_selection(context, selection)
        raise


def toggle_paired_calibration(context, obj):
    if _SESSION is not None:
        raise ForearmTwistError("Finish the current test first.")
    records = _prepare_runtime_records(obj)
    if not records:
        return
    enabled = not all(record.get("enabled", True) for record in records.values())
    for record in records.values():
        record["enabled"] = enabled
    _apply_runtime_records(obj, records, context.evaluated_depsgraph_get())
    _set_render_lock(context.scene)
    _redraw()


def remove_paired_calibration(context, obj):
    global _BUSY
    if _SESSION is not None:
        raise ForearmTwistError("Finish the current test first.")
    records = _records(obj)
    # Validate ownership before deleting either side, so a refusal is atomic.
    for side, record in records.items():
        key = _managed_key(obj, side, record)
        if any(other != key and other.relative_key == key for other in obj.data.shape_keys.key_blocks):
            raise ForearmTwistError("Another Shape Key references the corrective key; change its reference before removal.")
    old_busy = _BUSY
    _BUSY = True
    try:
        for side in tuple(records):
            remove_calibration(context, obj, side)
    finally:
        _BUSY = old_busy
    context.view_layer.update()
    _set_render_lock(context.scene)


def start_test(context, obj, side="L", *, symmetry=False):
    global _SESSION, _UI_BUSY
    if _SESSION is not None:
        raise ForearmTwistError("Confirm or cancel the current twist test first.")
    if context.mode != "OBJECT":
        raise ForearmTwistError("Use Object Mode and select the bound body Mesh.")
    if context.screen and context.screen.is_animation_playing:
        raise ForearmTwistError("Stop playback before calibrating.")
    arm, rig = _resolve_rig(obj, side)
    target = arm.pose.bones[rig["target"].name]
    if ((target.rotation_mode != "XYZ" and not rig.get("fk_source"))
            or any(target.lock_rotation) or target.lock_rotation_w):
        raise ForearmTwistError("Unlock hand rotation before calibrating; generated Targets need XYZ mode.")
    if limb_ik._target_transform_has_driver(arm, target.name) or limb_ik._target_transform_has_keyed_animation(arm, target.name):
        raise ForearmTwistError("Calibrate before keyframing the Hand Target; the test does not overwrite animation.")
    # Resolve a completed bind/rebuild before taking the preview snapshot.
    # This is normal persistent evaluation, independent of the temporary test.
    if RECORD_KEY in obj:
        update_runtime(context.scene, context.evaluated_depsgraph_get())
    records = _records(obj)
    if side in records:
        records[side] = _current_record(obj, arm, rig, side, records[side], records)
    # Fail an invalid mirror before changing the pose or creating any keys.
    if symmetry:
        source = records.get(side) or _capture_record(obj, arm, rig, side, records)
        prepared, other_side = _prepare_mirror(obj, side, source)
        other = records.get(other_side)
        if other and other["rest"] != prepared[other_side]["rest"]:
            raise ForearmTwistError("The other arm is still rebuilding; retry when its rig is ready.")
    old_json = obj.get(RECORD_KEY)
    old_index = obj.active_shape_key_index
    created_basis = obj.data.shape_keys is None
    old_key_coordinates = None
    if side in records:
        record = records[side]
        key = _managed_key(obj, side, record)
        if key is None:
            raise ForearmTwistError("The managed key is missing; remove calibration and start again.")
        old_key_coordinates = [p.co.copy() for p in key.data]
    else:
        record = _capture_record(obj, arm, rig, side, records)
        record["paired"] = bool(symmetry)
        key_name = record["key"]
        if created_basis:
            obj.shape_key_add(name="Basis", from_mix=False)
        key = obj.shape_key_add(name=key_name, from_mix=False)
        key.relative_key = obj.data.shape_keys.reference_key
        key.value = 1.0
        records[side] = record
        _KEY_REFERENCES[(obj.as_pointer(), side)] = key
    _SESSION = {"mesh": obj, "armature": arm, "side": side, "chain": list(rig["chain"]),
                "target": target.name, "rotation": tuple(target.rotation_euler),
                "rotation_mode": target.rotation_mode, "quaternion": tuple(target.rotation_quaternion),
                "axis_angle": tuple(target.rotation_axis_angle),
                "old_json": old_json, "old_index": old_index, "created_basis": created_basis,
                "old_key_coordinates": old_key_coordinates, "old_key_mute": key.mute, "old_key_value": key.value,
                "frame": context.scene.frame_current, "action": None, "symmetry": symmetry}
    _SESSION["mesh_name"] = obj.name
    _SESSION["armature_name"] = arm.name
    _SESSION["modal"] = False
    try:
        marker = {name: _SESSION[name] for name in ("side", "target", "rotation", "old_json", "old_index",
                  "created_basis", "old_key_mute", "old_key_value", "mesh_name", "armature_name",
                  "rotation_mode", "quaternion", "axis_angle")}
        marker["had_calibration"] = old_key_coordinates is not None
        obj[PREVIEW_KEY] = json.dumps(marker)
        record["enabled"] = True
        _write_records(obj, records)
        obj.active_shape_key_index = old_index
        _set_render_lock(context.scene)
        if target.rotation_mode != "XYZ":
            rotation = target.matrix_basis.to_quaternion()
            target.rotation_mode = "XYZ"
            target.rotation_euler = rotation.to_euler("XYZ")
        _UI_BUSY = True
        settings = context.window_manager.character_designer_forearm_twist
        settings.side = side
        settings.test_angle = math.pi / 2
        settings.ring_index = min(3, len(record["rings"]))
        _UI_BUSY = False
        _test_pose(context, settings.test_angle)
        _update_ui(context)
        if obj.name in _ERRORS:
            raise ForearmTwistError(_ERRORS[obj.name])
        return record
    except Exception:
        _UI_BUSY = False
        finish_test(context, False)
        raise


def finish_test(context, confirm=False, *, refresh=True):
    global _SESSION
    session = _SESSION
    if session is None:
        return
    if confirm and session.get("symmetry"):
        # Until this transaction succeeds, keep the original preview rollback
        # available. A refusal must never silently accept only half the pair.
        mirror_calibration(context, session["mesh"], session["side"], _from_test=True)
    _SESSION = None
    # Resolve live IDs again: deleting an object while a modal preview is open
    # must not leave dangling RNA in file-load, undo, or add-on cleanup hooks.
    try:
        obj = session["mesh"]
        obj.name
    except ReferenceError:
        obj = None
    try:
        arm = session["armature"]
        arm.name
    except (ReferenceError, AttributeError):
        arm = None
    target = arm.pose.bones.get(session["target"]) if arm is not None else None
    if target is not None:
        target.rotation_mode = session.get("rotation_mode", "XYZ")
        target.rotation_euler = session["rotation"]
        if "quaternion" in session:
            target.rotation_quaternion = session["quaternion"]
            target.rotation_axis_angle = session["axis_angle"]
    if obj is None:
        _CACHE.clear()
        _OUTPUT_CACHE.clear()
        _set_render_lock(context.scene)
        return
    if PREVIEW_KEY in obj:
        del obj[PREVIEW_KEY]
    if obj.mode == "EDIT":
        # Mesh mode changes are cancelled by the modal owner at the next event.
        # Leave Edit Mode before removing a newly created Shape Key.
        if context.object is obj:
            bpy.ops.object.mode_set(mode="OBJECT")
    if not confirm:
        key = _managed_key(obj, session["side"], {"key": KEY_PREFIX + session["side"]}, required=False, repair_name=False)
        if session["old_key_coordinates"] is None:
            if key is not None:
                obj.shape_key_remove(key)
                _KEY_REFERENCES.pop((obj.as_pointer(), session["side"]), None)
            if session["created_basis"] and obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) == 1:
                obj.shape_key_remove(obj.data.shape_keys.reference_key)
        elif key is not None:
            for point, coordinate in zip(key.data, session["old_key_coordinates"]):
                point.co = coordinate
            key.mute = session["old_key_mute"]
            key.value = session["old_key_value"]
        if session["old_json"] is None:
            if RECORD_KEY in obj:
                del obj[RECORD_KEY]
        else:
            obj[RECORD_KEY] = session["old_json"]
    obj.active_shape_key_index = session["old_index"]
    _CACHE.clear()
    _OUTPUT_CACHE.clear()
    _ERRORS.pop(obj.name, None)
    _set_render_lock(context.scene)
    if refresh:
        context.view_layer.update()
        update_runtime(context.scene, context.evaluated_depsgraph_get())
    try:
        _restore_selection(context, session.get("selection"))
    except (RuntimeError, ReferenceError):
        pass
    _redraw()


def remove_calibration(context, obj, side):
    if _SESSION is not None:
        raise ForearmTwistError("Finish the current test first.")
    records = _records(obj)
    record = records.get(side)
    if record is None:
        return
    keys = obj.data.shape_keys
    key = _managed_key(obj, side, record)
    if key is not None:
        if any(k != key and k.relative_key == key for k in keys.key_blocks):
            raise ForearmTwistError("Another Shape Key references the corrective key; change its reference before removal.")
        obj.shape_key_remove(key)
        _KEY_REFERENCES.pop((obj.as_pointer(), side), None)
    del records[side]
    # Transfer ownership of a newly-created Basis when the other side remains.
    if record.get("created_basis") and records:
        next(iter(records.values()))["created_basis"] = True
    if record.get("created_basis") and not records and obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) == 1:
        obj.shape_key_remove(obj.data.shape_keys.reference_key)
    _write_records(obj, records)
    _ERRORS.pop(obj.name, None)
    _set_render_lock(context.scene)
    _redraw()


def _redraw():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _draw_loop():
    if _SESSION is None:
        return
    context = bpy.context
    if context.space_data.type != "VIEW_3D" or not context.space_data.overlay.show_overlays:
        return
    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        obj = _SESSION["mesh"]
        if not obj.visible_get():
            return
        ring_index = context.window_manager.character_designer_forearm_twist.ring_index - 1
        record = _records(obj)[_SESSION["side"]]
        indices = set(record["rings"][ring_index]["vertices"])
        depsgraph = context.evaluated_depsgraph_get()
        arm = _SESSION["armature"]
        eval_arm = arm.evaluated_get(depsgraph)
        weights = _weights(obj, arm, indices)
        to_arm = eval_arm.matrix_world.inverted() @ obj.evaluated_get(depsgraph).matrix_world
        # Show the captured control-cage loop even with Subdivision after skin.
        mixed = _input_mix(obj, indices, set(), depsgraph)
        points = {}
        for i in indices:
            p = to_arm @ mixed[i]
            posed = sum((w * (eval_arm.pose.bones[n].matrix @ arm.data.bones[n].matrix_local.inverted() @ p)
                          for n, w in weights[i].items()), Vector())
            points[i] = eval_arm.matrix_world @ posed
        lines = [points[i] for edge in obj.data.edges if all(i in indices for i in edge.vertices) for i in edge.vertices]
        if not lines:
            return
        shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINES", {"pos": lines})
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
        shader.uniform_float("lineWidth", 3.0)
        shader.uniform_float("color", (1.0, 0.45, 0.04, 1.0))
        batch.draw(shader)
    except (ReferenceError, KeyError, RuntimeError, ValueError, AttributeError):
        pass
    finally:
        try:
            gpu.state.depth_test_set("NONE")
            gpu.state.blend_set("NONE")
        except (UnboundLocalError, AttributeError):
            pass


class CHARACTERDESIGNER_OT_forearm_twist_start(Operator):
    bl_idname = "character_designer.forearm_twist_start"
    bl_label = "Start 90 Degree Test"
    bl_description = "Preview forearm twist and adjust the shared loop profile for both arms"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            start_paired_test(context)
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

    def invoke(self, context, _event):
        result = self.execute(context)
        if result != {"FINISHED"}:
            return result
        self._timer = context.window_manager.event_timer_add(0.2, window=context.window)
        context.window_manager.modal_handler_add(self)
        _SESSION["modal"] = True
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if _SESSION is None:
            context.window_manager.event_timer_remove(self._timer)
            return {"CANCELLED"}
        action = _SESSION.get("action")
        if event.value == "PRESS" and ((event.type == "Z" and event.ctrl)
                                       or event.type in {"G", "R", "S", "X", "DEL", "TAB", "F3"}):
            # Complete the rollback before scene-editing shortcuts can create
            # another Undo snapshot. View navigation and panel sliders pass.
            finish_test(context, False)
            context.window_manager.event_timer_remove(self._timer)
            return {"CANCELLED"}
        try:
            if (context.mode not in {"OBJECT", "POSE"}
                    or _SESSION["mesh"].mode == "EDIT"
                    or context_mesh(context) is not _SESSION["mesh"]):
                action = "CANCEL"
        except (ValueError, ReferenceError):
            action = "CANCEL"
        if event.type == "ESC" or context.scene.frame_current != _SESSION["frame"]:
            action = "CANCEL"
        if action is not None:
            try:
                finish_test(context, action == "CONFIRM")
            except Exception as exc:
                _SESSION["action"] = None
                _ERRORS[_SESSION["mesh"].name] = str(exc)
                self.report({"ERROR"}, str(exc))
                return {"RUNNING_MODAL"}
            context.window_manager.event_timer_remove(self._timer)
            return {"FINISHED"} if action == "CONFIRM" else {"CANCELLED"}
        return {"PASS_THROUGH"}

    def cancel(self, context):
        finish_test(context, False)


class CHARACTERDESIGNER_OT_forearm_twist_finish(Operator):
    bl_idname = "character_designer.forearm_twist_finish"
    bl_label = "Finish Twist Test"
    action: EnumProperty(items=(("CONFIRM", "Confirm", ""), ("CANCEL", "Cancel", "")))

    def execute(self, context):
        if _SESSION is not None:
            if _SESSION.get("modal"):
                _SESSION["action"] = self.action
            else:
                try:
                    finish_test(context, self.action == "CONFIRM")
                except Exception as exc:
                    self.report({"ERROR"}, str(exc))
                    return {"CANCELLED"}
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_forearm_twist_remove(Operator):
    bl_idname = "character_designer.forearm_twist_remove"
    bl_label = "Remove Calibration"
    bl_description = "Remove the forearm calibration from both arms"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            remove_paired_calibration(context, context_mesh(context))
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class CHARACTERDESIGNER_OT_forearm_twist_toggle(Operator):
    bl_idname = "character_designer.forearm_twist_toggle"
    bl_label = "Toggle Forearm Correction"
    bl_description = "Enable or disable forearm correction for both arms"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            toggle_paired_calibration(context, context_mesh(context))
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class CHARACTERDESIGNER_PT_forearm_twist(Panel):
    bl_label = "Forearm Twist (Prototype)"
    bl_idname = "CHARACTERDESIGNER_PT_forearm_twist"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return rig_page_active(context, "BODY")

    def draw(self, context):
        layout = self.layout
        settings = context.window_manager.character_designer_forearm_twist
        try:
            obj = _SESSION["mesh"] if _SESSION is not None else context_mesh(context)
        except (ValueError, ReferenceError) as exc:
            import textwrap
            for line in textwrap.wrap(str(exc), 44):
                layout.label(text=line, icon="INFO")
            return
        if obj is None:
            layout.label(text="Select body Mesh or Hand Target", icon="INFO")
            return
        layout.label(text="Both Arms", icon="MOD_MIRROR")
        if _SESSION:
            record = _records(obj)[_SESSION["side"]]
            ring = record["rings"][settings.ring_index - 1]
            layout.label(text=obj.name)
            layout.prop(settings, "test_angle", slider=True)
            layout.prop(settings, "ring_index")
            layout.label(text=f"Loop {settings.ring_index} / {len(record['rings'])} · {len(ring['vertices'])} vertices")
            row = layout.row()
            row.enabled = 1.0e-6 < ring["position"] < 1.0 - 1.0e-6
            row.prop(settings, "ratio", slider=True)
            layout.label(text=f"Loop rotation: {math.degrees(settings.test_angle) * ring['ratio']:.1f}°")
            row = layout.row(align=True)
            row.operator("character_designer.forearm_twist_finish", text="Confirm", icon="CHECKMARK").action = "CONFIRM"
            row.operator("character_designer.forearm_twist_finish", text="Cancel", icon="X").action = "CANCEL"
            layout.label(text="Esc restores the pose and calibration.")
        else:
            try:
                records = _records(obj)
            except Exception:
                records = {}
            record = records.get("L") or records.get("R")
            layout.operator("character_designer.forearm_twist_start", text="Recalibrate 90°" if record else "Start 90° Test", icon="DRIVER_ROTATIONAL_DIFFERENCE")
            if record:
                enabled = all(item.get("enabled", True) for item in records.values())
                row = layout.row(align=True)
                row.operator("character_designer.forearm_twist_toggle", text="Enabled" if enabled else "Disabled", depress=enabled)
                remove_row = row.row(align=True)
                remove_row.alert = True
                remove_row.operator("character_designer.forearm_twist_remove", text="Remove", icon="TRASH")
            layout.label(text="Local correction · ±120° · add-on required")
        if obj.name in _ERRORS:
            box = layout.box()
            box.alert = True
            # Blender labels do not wrap automatically.
            import textwrap
            for line in textwrap.wrap(_ERRORS[obj.name], 44):
                box.label(text=line)


FOREARM_TWIST_CLASSES = (
    CharacterDesignerForearmTwistState,
    CHARACTERDESIGNER_OT_forearm_twist_start,
    CHARACTERDESIGNER_OT_forearm_twist_finish,
    CHARACTERDESIGNER_OT_forearm_twist_remove,
    CHARACTERDESIGNER_OT_forearm_twist_toggle,
    CHARACTERDESIGNER_PT_forearm_twist,
)

_HANDLERS = (("depsgraph_update_post", _graph_post), ("frame_change_post", _frame_post),
             ("load_pre", _restore_preview), ("save_pre", _restore_preview),
             ("undo_pre", _restore_preview), ("load_post", _load_post),
             ("undo_post", _undo_post), ("redo_post", _undo_post))


def _scene_data_available():
    # addon_utils.enable runs register(), including its rollback, inside
    # RestrictBlend. RNA classes, handlers and timers are available there;
    # scene datablocks and the ordinary context deliberately are not.
    return hasattr(bpy.data, "objects") and hasattr(bpy.data, "scenes")


def _cleanup_runtime_scene():
    """Finish scene-side teardown only when Blender exposes live datablocks."""
    global _SESSION, _SCENE_STATE_ACTIVE
    try:
        if _SESSION is not None:
            try:
                finish_test(bpy.context, False, refresh=False)
            except Exception:
                # Never block class/property cleanup because a preview's IDs
                # were removed. Its serialized rollback marker remains usable.
                traceback.print_exc()
                _SESSION = None
        # Unloading cannot leave a stale pose correction without its code.
        for obj in bpy.data.objects:
            if obj.type != "MESH" or RECORD_KEY not in obj or not obj.data.shape_keys:
                continue
            try:
                for side, record in _records(obj).items():
                    key = _managed_key(obj, side, record, required=False, repair_name=False)
                    if key is not None:
                        key.mute = True
            except (ForearmTwistError, ValueError, TypeError, AttributeError, ReferenceError):
                continue
        for scene in bpy.data.scenes:
            if LOCK_KEY in scene:
                scene.render.use_lock_interface = bool(scene[LOCK_KEY])
                del scene[LOCK_KEY]
    finally:
        _SCENE_STATE_ACTIVE = False
        _CACHE.clear()
        _OUTPUT_CACHE.clear()
        _KEY_REFERENCES.clear()
        _ERRORS.clear()


def _complete_runtime_lifecycle():
    """One-shot timer, also drained before the first load/pose evaluation."""
    global _INITIALIZE_PENDING, _SCENE_CLEANUP_PENDING, _SCENE_STATE_ACTIVE
    if not (_INITIALIZE_PENDING or _SCENE_CLEANUP_PENDING):
        return None
    if not _scene_data_available():
        return 0.05
    if _SCENE_CLEANUP_PENDING:
        _SCENE_CLEANUP_PENDING = False
        _cleanup_runtime_scene()
    if _RUNTIME_REGISTERED and _INITIALIZE_PENDING:
        # Clear first: recovery may tag the dependency graph while initializing.
        _INITIALIZE_PENDING = False
        _SCENE_STATE_ACTIVE = True
        _recover_previews()
        _remember_named_keys()
        for scene in bpy.data.scenes:
            _set_render_lock(scene)
    return None


def _request_runtime_lifecycle():
    if bpy.app.timers.is_registered(_complete_runtime_lifecycle):
        bpy.app.timers.unregister(_complete_runtime_lifecycle)
    if _scene_data_available():
        _complete_runtime_lifecycle()
    elif _INITIALIZE_PENDING or _SCENE_CLEANUP_PENDING:
        bpy.app.timers.register(_complete_runtime_lifecycle, first_interval=0.0)


def register_forearm_twist_runtime():
    global _DRAW_HANDLE, _RUNTIME_REGISTERED, _INITIALIZE_PENDING
    _RUNTIME_REGISTERED = True
    _INITIALIZE_PENDING = True
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler not in handlers:
            handlers.append(handler)
    if not bpy.app.background and _DRAW_HANDLE is None:
        _DRAW_HANDLE = bpy.types.SpaceView3D.draw_handler_add(_draw_loop, (), "WINDOW", "POST_VIEW")
    _CACHE.clear()
    _OUTPUT_CACHE.clear()
    _request_runtime_lifecycle()


def unregister_forearm_twist_runtime():
    global _DRAW_HANDLE, _RUNTIME_REGISTERED, _INITIALIZE_PENDING, _SCENE_CLEANUP_PENDING
    _RUNTIME_REGISTERED = False
    _INITIALIZE_PENDING = False
    _SCENE_CLEANUP_PENDING |= _SCENE_STATE_ACTIVE or _SESSION is not None
    for name, handler in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if handler in handlers:
            handlers.remove(handler)
    if _DRAW_HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_DRAW_HANDLE, "WINDOW")
        _DRAW_HANDLE = None
    _request_runtime_lifecycle()
    _CACHE.clear()
    _OUTPUT_CACHE.clear()
    if not _SCENE_CLEANUP_PENDING:
        _KEY_REFERENCES.clear()
    _ERRORS.clear()
