"""Owned, reversible object parenting for Quick Bind.

Parent records are independent of the first weight backup, including backups
created before Quick Bind established an Outliner parent. Attaching changes the
parent inverse rather than the object's local or delta transform channels.
"""

import json
import math

import bpy
from mathutils import Matrix


PARENT_KEY = "character_designer_quick_binding_parent_v1"
PARENT_RIG_KEY = "character_designer_quick_binding_parent_rig"
OLD_PARENT_KEY = "character_designer_quick_binding_previous_parent"
_KEYS = (PARENT_KEY, PARENT_RIG_KEY, OLD_PARENT_KEY)
_CHANNELS = (
    "location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
    "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale",
)


class ParentingError(ValueError):
    """A parent change that cannot safely preserve the artist's object."""


def _channels(target):
    return {name: tuple(getattr(target, name)) for name in _CHANNELS}


def capture_state(target):
    """Capture transform channels, parent and this module's metadata only."""
    return {
        "parent": target.parent,
        "parent_type": target.parent_type,
        "parent_bone": target.parent_bone,
        "parent_vertices": tuple(target.parent_vertices),
        "inverse": target.matrix_parent_inverse.copy(),
        "basis": target.matrix_basis.copy(),
        "world": target.matrix_world.copy(),
        "rotation_mode": target.rotation_mode,
        "channels": _channels(target),
        "metadata": {key: target[key] for key in _KEYS if key in target},
    }


def rollback(context, target, state):
    """Restore a captured state without decomposing its object transforms."""
    target.parent = state["parent"]
    target.parent_type = state["parent_type"]
    target.parent_bone = state["parent_bone"]
    target.parent_vertices = state["parent_vertices"]
    target.matrix_parent_inverse = state["inverse"]
    target.rotation_mode = state["rotation_mode"]
    for name, values in state["channels"].items():
        setattr(target, name, values)
    for key in _KEYS:
        if key in state["metadata"]:
            target[key] = state["metadata"][key]
        elif key in target:
            del target[key]
    context.view_layer.update()


def _valid_matrix(matrix, description, *, invertible=False):
    if (len(matrix) != 4 or len(matrix[0]) != 4
            or not all(math.isfinite(value) for row in matrix for value in row)):
        raise ParentingError(f"{description} has an invalid transform; parenting was left unchanged.")
    if invertible and abs(matrix.determinant()) < 1e-12:
        raise ParentingError(f"{description} has a zero-scale transform; parenting was left unchanged.")


def _verify_world(target, expected):
    actual = target.matrix_world
    _valid_matrix(actual, target.name)
    tolerance = 1e-5 * max(1.0, max(abs(value) for row in expected for value in row))
    if max(abs(a - b) for ra, rb in zip(actual, expected) for a, b in zip(ra, rb)) > tolerance:
        raise ParentingError(
            f"{target.name}: changing its parent would move or distort it; the original parent was kept."
        )


def _check_cycle(target, parent):
    seen = set()
    while parent is not None:
        pointer = parent.as_pointer()
        if parent == target or pointer in seen:
            raise ParentingError("This parent would create a cycle; parenting was left unchanged.")
        seen.add(pointer)
        parent = parent.parent


def _validate_objects(context, target, rig):
    if not isinstance(target, bpy.types.Object) or target.type != 'MESH':
        raise ParentingError("Choose a mesh to attach to Main Rig.")
    if target.library:
        raise ParentingError("Make the target object local before changing its parent.")
    if not isinstance(rig, bpy.types.Object) or rig.type != 'ARMATURE':
        raise ParentingError("Choose the character's Main Rig before attaching its mesh.")
    context.view_layer.update()
    _valid_matrix(target.matrix_world, target.name)
    _valid_matrix(rig.matrix_world, rig.name, invertible=True)


def _record(target):
    if not any(key in target for key in _KEYS):
        return None
    try:
        record = json.loads(target[PARENT_KEY])
        assert record["version"] == 1
        assert isinstance(record["had_parent"], bool)
        assert record["parent_type"] == 'OBJECT'
        assert isinstance(record["parent_bone"], str)
        inverse = Matrix(record["parent_inverse"])
        assert len(inverse) == 4 and len(inverse[0]) == 4
        assert all(math.isfinite(value) for row in inverse for value in row)
        owner = target[PARENT_RIG_KEY]
        assert isinstance(owner, bpy.types.Object) and owner.type == 'ARMATURE'
        if not record["had_parent"]:
            assert OLD_PARENT_KEY not in target
        return record
    except (KeyError, TypeError, ValueError, AssertionError):
        raise ParentingError("The saved parent record is missing or damaged; it was kept for recovery.") from None


def _check_owned(target, rig):
    if target.get(PARENT_RIG_KEY) != rig:
        raise ParentingError("The saved parent belongs to another rig; its record was kept.")
    if target.parent != rig or target.parent_type != 'OBJECT':
        raise ParentingError("The mesh has a new parent; the saved parent record was kept.")


def _check_parent_change(target):
    if target.parent_type != 'OBJECT':
        raise ParentingError(f"{target.name}: only Object parenting can be changed by Quick Bind.")
    if any(not constraint.mute and constraint.influence != 0 for constraint in target.constraints):
        raise ParentingError(
            f"{target.name}: active object constraints need a separate parent setup before Quick Bind."
        )
    if target.parent is not None:
        _valid_matrix(target.parent.matrix_world, target.parent.name, invertible=True)
    _valid_matrix(target.matrix_parent_inverse, target.name + " parent inverse", invertible=True)


def preflight(context, target, rig):
    """Validate attachment without changing parent, channels or metadata."""
    _validate_objects(context, target, rig)
    record = _record(target)
    if record is not None:
        _check_owned(target, rig)
    if target.parent == rig and target.parent_type == 'OBJECT':
        return
    _check_parent_change(target)
    _check_cycle(target, rig)


def attach(context, target, rig):
    """Attach under Main Rig, preserving world placement and local channels."""
    preflight(context, target, rig)
    if target.parent == rig and target.parent_type == 'OBJECT':
        return False
    state = capture_state(target)
    effective_parent = (state["parent"].matrix_world @ state["inverse"]
                        if state["parent"] is not None else Matrix.Identity(4))
    inverse = rig.matrix_world.inverted() @ effective_parent
    record = {
        "version": 1,
        "had_parent": state["parent"] is not None,
        "parent_type": state["parent_type"],
        "parent_bone": state["parent_bone"],
        "parent_inverse": [list(row) for row in state["inverse"]],
    }
    try:
        target.parent = rig
        target.parent_type = 'OBJECT'
        target.parent_bone = ''
        target.matrix_parent_inverse = inverse
        context.view_layer.update()
        _verify_world(target, state["world"])
        if target.rotation_mode != state["rotation_mode"] or _channels(target) != state["channels"]:
            raise ParentingError("Parenting changed the mesh's transform channels; its original parent was kept.")
        target[PARENT_KEY] = json.dumps(record, separators=(',', ':'))
        target[PARENT_RIG_KEY] = rig
        if state["parent"] is not None:
            target[OLD_PARENT_KEY] = state["parent"]
        return True
    except Exception as exc:
        rollback(context, target, state)
        if isinstance(exc, ParentingError):
            raise
        raise ParentingError(f"Attaching the mesh failed; its original parent was kept: {exc}") from exc


def check_restore(context, target, rig):
    """Validate an owned restoration; artist-created rig parents are untouched."""
    record = _record(target)
    if record is None:
        return
    _validate_objects(context, target, rig)
    _check_owned(target, rig)
    _check_parent_change(target)
    if record["had_parent"]:
        parent = target.get(OLD_PARENT_KEY)
        if not isinstance(parent, bpy.types.Object):
            raise ParentingError("The saved previous parent no longer exists; its record was kept.")
        if parent == rig:
            raise ParentingError("The saved previous parent conflicts with Main Rig; its record was kept.")
        _valid_matrix(parent.matrix_world, parent.name, invertible=True)
        _check_cycle(target, parent)


def restore(context, target, rig):
    """Restore our previous parent while retaining the mesh's current placement."""
    check_restore(context, target, rig)
    record = _record(target)
    if record is None:
        return False
    state = capture_state(target)
    parent = target.get(OLD_PARENT_KEY) if record["had_parent"] else None
    effective_parent = rig.matrix_world @ target.matrix_parent_inverse
    try:
        target.parent = parent
        target.parent_type = record["parent_type"]
        target.parent_bone = record["parent_bone"]
        if parent is not None:
            target.matrix_parent_inverse = parent.matrix_world.inverted() @ effective_parent
        else:
            # Without a parent, Blender must represent the transform in channels.
            # Verification below rejects a shear that cannot survive that step.
            target.matrix_parent_inverse = Matrix(record["parent_inverse"])
            target.matrix_world = state["world"]
        context.view_layer.update()
        _verify_world(target, state["world"])
        if parent is not None and (target.rotation_mode != state["rotation_mode"]
                                   or _channels(target) != state["channels"]):
            raise ParentingError("Restoring the parent changed transform channels; the binding was kept.")
        for key in _KEYS:
            if key in target:
                del target[key]
        return True
    except Exception as exc:
        rollback(context, target, state)
        if isinstance(exc, ParentingError):
            raise
        raise ParentingError(f"Restoring the previous parent failed; its record was kept: {exc}") from exc
