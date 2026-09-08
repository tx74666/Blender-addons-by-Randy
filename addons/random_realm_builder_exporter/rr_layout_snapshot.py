from datetime import datetime, timezone

import bpy
from mathutils import Matrix

try:
    from .rr_builder_constants import LAYOUT_SNAPSHOT_MATRIX_PROP, LAYOUT_SNAPSHOT_ROTATION_MODE_PROP
except ImportError:
    from rr_builder_constants import LAYOUT_SNAPSHOT_MATRIX_PROP, LAYOUT_SNAPSHOT_ROTATION_MODE_PROP


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


def sync_collider_origin_to_target(obj):
    target = collider_target_object(obj)
    if target is None:
        return False

    # Keep the collider's own mesh orientation/scale while its origin follows the visual asset.
    matrix = obj.matrix_world.copy()
    matrix.translation = target.matrix_world.translation
    obj.matrix_world = matrix
    return True


def sync_collider_origins_to_targets():
    synced = 0
    for obj in bpy.data.objects:
        if sync_collider_origin_to_target(obj):
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
    restored = 0
    skipped = []
    objects = [
        obj for obj in bpy.data.objects
        if obj.get(LAYOUT_SNAPSHOT_MATRIX_PROP) is not None
    ]
    objects.sort(key=object_hierarchy_depth)

    for obj in objects:
        if collider_target_object(obj) is not None:
            continue

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
