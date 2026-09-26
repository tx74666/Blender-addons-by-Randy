from datetime import datetime, timezone

import bpy
from mathutils import Matrix

try:
    from .rr_builder_constants import LAYOUT_SNAPSHOT_MATRIX_PROP, LAYOUT_SNAPSHOT_ROTATION_MODE_PROP
    from .rr_modeling_origin import apply_origin_to_mesh_object
except ImportError:
    from rr_builder_constants import LAYOUT_SNAPSHOT_MATRIX_PROP, LAYOUT_SNAPSHOT_ROTATION_MODE_PROP
    from rr_modeling_origin import apply_origin_to_mesh_object


def matrix_to_snapshot(matrix):
    return [float(value) for row in matrix for value in row]


def matrix_from_snapshot(values):
    if values is None or len(values) != 16:
        return None
    return Matrix((
        tuple(float(value) for value in values[0:4]),
        tuple(float(value) for value in values[4:8]),
        tuple(float(value) for value in values[8:12]),
        tuple(float(value) for value in values[12:16]),
    ))


def object_hierarchy_depth(obj):
    depth = 0
    parent = obj.parent if obj is not None else None
    while parent is not None:
        depth += 1
        parent = parent.parent
    return depth


def collider_target_object(obj):
    if obj is None:
        return None

    target_name = obj.get("rr_collider_target")
    if not target_name:
        return None

    return bpy.data.objects.get(target_name)


def validate_collider_origin_alignment(obj, target):
    if obj is None or obj.type != "MESH" or obj.data is None or target is None:
        raise RuntimeError("Collider must be a mesh with an existing owner.")
    if obj.mode != "OBJECT":
        raise RuntimeError("Switch to Object Mode before changing collider ownership or origin.")
    if obj.library is not None or obj.data.library is not None or not obj.is_editable:
        raise RuntimeError(f"{obj.name}: linked collider data is read-only; make a local mesh first.")
    if obj.data.shape_keys is not None:
        raise RuntimeError(f"{obj.name}: collider origin alignment does not support shape keys.")
    if obj.modifiers or obj.constraints or obj.animation_data is not None:
        raise RuntimeError(f"{obj.name}: use a static collider mesh without modifiers, constraints, or animation.")
    try:
        obj.matrix_world.inverted()
        target.matrix_world.inverted()
    except ValueError as exc:
        raise RuntimeError(f"{obj.name}: collider and owner transforms must have nonzero scale.") from exc


def align_collider_origin_preserve_geometry(obj, target):
    validate_collider_origin_alignment(obj, target)
    if (obj.matrix_world.translation - target.matrix_world.translation).length <= 0.000001:
        return False
    old_world = obj.matrix_world.copy()
    saved_matrix = matrix_from_snapshot(obj.get(LAYOUT_SNAPSHOT_MATRIX_PROP))
    # Changing Location alone would move the authored collision shape. Bake the
    # inverse offset into a private mesh, retaining parent/rotation/scale and children.
    if not apply_origin_to_mesh_object(obj, target.matrix_world.translation):
        raise RuntimeError(f"{obj.name}: could not align collider origin without moving its geometry.")
    if saved_matrix is not None:
        # Rebase the old saved pose as well: its matrix now acts on offset mesh
        # coordinates. Without this, Link after Save Layout makes Restore drift.
        obj[LAYOUT_SNAPSHOT_MATRIX_PROP] = matrix_to_snapshot(
            saved_matrix @ old_world.inverted() @ obj.matrix_world
        )
    return True


def sync_collider_origin_to_target(obj):
    target = collider_target_object(obj)
    if target is None:
        return False

    return align_collider_origin_preserve_geometry(obj, target)


def sync_collider_origins_to_targets():
    candidates = [(obj, collider_target_object(obj)) for obj in bpy.data.objects]
    candidates = [(obj, target) for obj, target in candidates if target is not None]
    # Validate every participant before changing any mesh in a layout operation.
    for obj, target in candidates:
        validate_collider_origin_alignment(obj, target)
    synced = 0
    for obj, target in candidates:
        if align_collider_origin_preserve_geometry(obj, target):
            synced += 1

    if synced:
        bpy.context.view_layer.update()
    return synced


def snapshot_layout(settings):
    sync_collider_origins_to_targets()

    saved = 0
    for obj in bpy.data.objects:
        obj[LAYOUT_SNAPSHOT_MATRIX_PROP] = matrix_to_snapshot(obj.matrix_world)
        obj[LAYOUT_SNAPSHOT_ROTATION_MODE_PROP] = obj.rotation_mode
        saved += 1

    if settings is not None:
        settings.layout_snapshot_count = saved
        settings.layout_snapshot_saved_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return saved


def restore_layout_snapshot():
    for obj in bpy.data.objects:
        target = collider_target_object(obj)
        if target is not None:
            validate_collider_origin_alignment(obj, target)
    restored = 0
    skipped = []
    objects = [
        obj for obj in bpy.data.objects
        if obj.get(LAYOUT_SNAPSHOT_MATRIX_PROP) is not None
    ]
    objects.sort(key=object_hierarchy_depth)

    for obj in objects:
        matrix = matrix_from_snapshot(obj.get(LAYOUT_SNAPSHOT_MATRIX_PROP))
        if matrix is None:
            skipped.append(obj.name)
            continue

        try:
            rotation_mode = obj.get(LAYOUT_SNAPSHOT_ROTATION_MODE_PROP)
            if rotation_mode:
                obj.rotation_mode = rotation_mode
            obj.matrix_world = matrix
            restored += 1
        except Exception:
            skipped.append(obj.name)

    bpy.context.view_layer.update()
    restored += sync_collider_origins_to_targets()
    return restored, skipped


def clear_layout_snapshot(settings=None):
    cleared = 0
    for obj in bpy.data.objects:
        had_snapshot = False
        if LAYOUT_SNAPSHOT_MATRIX_PROP in obj:
            del obj[LAYOUT_SNAPSHOT_MATRIX_PROP]
            had_snapshot = True
        if LAYOUT_SNAPSHOT_ROTATION_MODE_PROP in obj:
            del obj[LAYOUT_SNAPSHOT_ROTATION_MODE_PROP]
            had_snapshot = True
        if had_snapshot:
            cleared += 1

    if settings is not None:
        settings.layout_snapshot_count = 0
        settings.layout_snapshot_saved_at = ""
    return cleared


__all__ = [name for name, value in globals().items() if callable(value) and not name.startswith("_")]
