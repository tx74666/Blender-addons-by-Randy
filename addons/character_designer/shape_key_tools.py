"""Safe local cleanup tools for accidental Shape Key deformation."""

from dataclasses import dataclass
import traceback

import bmesh
import bpy
from bpy.types import Operator, Panel
from mathutils import Vector
from mathutils.kdtree import KDTree

from .ui_constants import SIDEBAR_CATEGORY, UI_PAGE_MISC, active_ui_page


class ShapeKeyCleanupError(ValueError):
    """An artist-facing Shape Key cleanup preflight or write failure."""


@dataclass(frozen=True)
class ShapeKeyClearPlan:
    obj: object
    key_names: tuple
    vertex_count: int
    relative_keys: tuple
    sources: tuple
    requested_vertex_indices: tuple
    vertex_indices: tuple
    mirror_pairs: tuple
    unmatched_mirror_count: int
    before: tuple
    targets: tuple
    changed_count: int


def _active_edit_mesh(context):
    if context.mode != "EDIT_MESH":
        raise ShapeKeyCleanupError("Use Shape Key cleanup in Mesh Edit Mode.")
    obj = context.view_layer.objects.active if context.view_layer else None
    if obj is None or obj.type != "MESH" or context.object is not obj:
        raise ShapeKeyCleanupError("Make one Mesh active in Edit Mode.")
    if obj.data.users != 1:
        raise ShapeKeyCleanupError(
            "The Mesh data is shared by multiple objects; make it Single User first."
        )
    return obj


def _selected_vertex_indices(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    indices = tuple(sorted(vertex.index for vertex in bm.verts if vertex.select))
    if not indices:
        raise ShapeKeyCleanupError("Select the vertices whose Shape Key deformation should be cleared.")
    return indices


def _edit_mesh_coordinates(obj):
    shape_keys = obj.data.shape_keys
    if shape_keys is not None and shape_keys.reference_key is not None:
        return tuple(point.co.copy() for point in shape_keys.reference_key.data)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    return tuple(vertex.co.copy() for vertex in bm.verts)


def _mirror_counterpart_indices(obj, requested_indices):
    """Find actual local-X counterparts in the Basis mesh.

    A half-Mesh has no second base vertex to edit, so it naturally returns no
    pair; its generated side follows the cleared source through the modifier.
    A full Mesh can still have real bilateral topology without a Mirror
    modifier, so pairing must not depend on the modifier being present.
    Ambiguous or distant matches are left untouched.
    """

    mirrors = tuple(
        modifier for modifier in obj.modifiers
        if modifier.type == "MIRROR"
        and modifier.show_viewport
        and modifier.use_axis[0]
    )
    if any(modifier.mirror_object is not None for modifier in mirrors):
        return tuple(sorted(requested_indices)), (), 0

    coordinates = _edit_mesh_coordinates(obj)
    if not coordinates:
        return tuple(sorted(requested_indices)), (), 0
    minimum = Vector((min(co.x for co in coordinates),
                      min(co.y for co in coordinates),
                      min(co.z for co in coordinates)))
    maximum = Vector((max(co.x for co in coordinates),
                      max(co.y for co in coordinates),
                      max(co.z for co in coordinates)))
    tolerance = max(1.0e-7, (maximum - minimum).length * 1.0e-5)
    tree = KDTree(len(coordinates))
    for index, coordinate in enumerate(coordinates):
        tree.insert(coordinate, index)
    tree.balance()

    selected = set(requested_indices)
    pairs = []
    pair_keys = set()
    unmatched = 0
    for source_index in sorted(requested_indices):
        reflected = coordinates[source_index].copy()
        reflected.x = -reflected.x
        if abs(reflected.x - coordinates[source_index].x) <= tolerance:
            continue
        hits = sorted(
            (item for item in tree.find_range(reflected, tolerance)
             if item[1] != source_index),
            key=lambda item: (item[2], item[1]),
        )
        if not hits:
            unmatched += 1
            continue
        if len(hits) > 1 and abs(hits[1][2] - hits[0][2]) <= tolerance * 0.1:
            unmatched += 1
            continue
        target_index = hits[0][1]
        selected.add(target_index)
        pair_key = tuple(sorted((source_index, target_index)))
        if pair_key not in pair_keys:
            pair_keys.add(pair_key)
            pairs.append(pair_key)
    return tuple(sorted(selected)), tuple(pairs), unmatched


def _select_edit_vertices(obj, indices):
    selected = set(indices)
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    for vertex in bm.verts:
        vertex.select_set(vertex.index in selected)
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def _key_data_coordinates(key, indices):
    if key is None:
        raise ShapeKeyCleanupError("A selected Shape Key disappeared during cleanup.")
    try:
        return tuple(key.data[index].co.copy() for index in indices)
    except (IndexError, TypeError) as exc:
        raise ShapeKeyCleanupError(
            f'Shape Key "{key.name}" does not match the Mesh vertex count.'
        ) from exc


def _bmesh_shape_coordinates(obj, key_name, indices):
    """Read a Shape Key's edit-mode layer without going through Mesh data."""

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    shape_layer = bm.verts.layers.shape.get(key_name)
    if shape_layer is None:
        raise ShapeKeyCleanupError(
            f'Edit Mode has no BMesh Shape Key layer for "{key_name}".'
        )
    try:
        return tuple(bm.verts[index][shape_layer].copy() for index in indices)
    except (IndexError, TypeError) as exc:
        raise ShapeKeyCleanupError(
            f'Shape Key "{key_name}" does not match the Edit Mesh vertex count.'
        ) from exc


def _verify_records(obj, vertex_indices, records):
    """Verify Mesh and Edit Mesh copies of every write before reporting success."""

    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    active_key = obj.active_shape_key
    for key_name, expected in records:
        key = obj.data.shape_keys.key_blocks.get(key_name)
        actual_mesh = _key_data_coordinates(key, vertex_indices)
        actual_bmesh = _bmesh_shape_coordinates(obj, key_name, vertex_indices)
        for index, (mesh_value, bmesh_value, expected_value) in enumerate(
            zip(actual_mesh, actual_bmesh, expected)
        ):
            if (mesh_value - expected_value).length_squared > 1.0e-20:
                raise ShapeKeyCleanupError(
                    f'Shape Key "{key_name}" Mesh data verification failed '
                    f'at vertex {vertex_indices[index]}.'
                )
            if (bmesh_value - expected_value).length_squared > 1.0e-20:
                raise ShapeKeyCleanupError(
                    f'Shape Key "{key_name}" Edit Mode verification failed '
                    f'at vertex {vertex_indices[index]}.'
                )
            if active_key is not None and active_key.name == key_name:
                active_value = bm.verts[vertex_indices[index]].co
                if (active_value - expected_value).length_squared > 1.0e-20:
                    raise ShapeKeyCleanupError(
                        f'Shape Key "{key_name}" active-coordinate verification failed '
                        f'at vertex {vertex_indices[index]}.'
                    )


def _clear_baselines(keys, reference):
    """Resolve selected parents to their final baseline; reject cyclic key graphs."""
    selected = {key.name for key in keys}
    dependencies = {}
    baselines = {}
    for key in keys:
        current = key
        visited = set()
        while current != reference:
            if current.name in visited:
                raise ShapeKeyCleanupError(
                    f'Shape Key "{key.name}" has a cyclic relative-key dependency; no keys were cleared.'
                )
            visited.add(current.name)
            relative = current.relative_key
            if relative is None:
                raise ShapeKeyCleanupError(f'Shape Key "{current.name}" has no relative key.')
            dependencies[current.name] = relative.name
            current = relative
        current = key.relative_key
        while current.name in selected:
            current = current.relative_key
        baselines[key.name] = current
    return baselines, tuple(dependencies.items())


def build_shape_key_clear_plan(context):
    obj = _active_edit_mesh(context)
    shape_keys = obj.data.shape_keys
    if shape_keys is None or not shape_keys.key_blocks:
        raise ShapeKeyCleanupError("The active Mesh has no Shape Keys.")
    if not shape_keys.use_relative:
        raise ShapeKeyCleanupError(
            "This cleanup currently supports relative Shape Keys only; absolute keys were not changed."
        )

    reference = shape_keys.reference_key
    keys = tuple(
        key for key in shape_keys.key_blocks
        if key != reference and bool(key.select)
    )
    if not keys:
        raise ShapeKeyCleanupError(
            "Select one or more editable Shape Keys in the Shape Keys list; Basis cannot be cleared."
        )

    for key in keys:
        if getattr(key, "lock_shape", False):
            raise ShapeKeyCleanupError(f'Shape Key "{key.name}" is locked; unlock it before clearing.')
    baselines, relative_keys = _clear_baselines(keys, reference)
    # Flush the artist's pending Edit Mode changes through Blender once. Native
    # editing also moves keys relative to the active key; reading vertex.co alone
    # would miss that propagation and apply the active-key delta a second time.
    obj.update_from_editmode()
    requested_indices = _selected_vertex_indices(obj)
    indices, mirror_pairs, unmatched_mirror_count = _mirror_counterpart_indices(
        obj, requested_indices
    )
    before = []
    targets = []
    changed_count = 0
    source_keys = {key.name: key for key in (*keys, *baselines.values())}
    sources = {name: _key_data_coordinates(key, indices) for name, key in source_keys.items()}
    for key in keys:
        original = sources[key.name]
        baseline = sources[baselines[key.name].name]
        before.append((key.name, original))
        targets.append((key.name, baseline))
        changed_count += sum(
            1 for old, new in zip(original, baseline)
            if (old - new).length_squared > 1.0e-20
        )

    return ShapeKeyClearPlan(
        obj=obj,
        key_names=tuple(key.name for key in keys),
        vertex_count=len(obj.data.vertices),
        relative_keys=relative_keys,
        sources=tuple(sources.items()),
        requested_vertex_indices=requested_indices,
        vertex_indices=indices,
        mirror_pairs=mirror_pairs,
        unmatched_mirror_count=unmatched_mirror_count,
        before=tuple(before),
        targets=tuple(targets),
        changed_count=changed_count,
    )


def _write_records(obj, vertex_indices, records):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    shape_layers = bm.verts.layers.shape
    active_key = obj.active_shape_key
    for key_name, coordinates in records:
        key = obj.data.shape_keys.key_blocks.get(key_name)
        if key is None:
            raise ShapeKeyCleanupError(f'Shape Key "{key_name}" disappeared during cleanup.')
        shape_layer = shape_layers.get(key_name)
        for index, coordinate in zip(vertex_indices, coordinates):
            key.data[index].co = coordinate
            if shape_layer is not None:
                bm.verts[index][shape_layer] = coordinate
            if active_key is not None and active_key.name == key.name:
                # In Edit Mode, BMesh vertex.co is the active Shape Key's
                # coordinate. update_edit_mesh() would otherwise copy the
                # stale active coordinates back over KeyBlock.data.
                bm.verts[index].co = coordinate


def apply_shape_key_clear_plan(plan):
    obj = plan.obj
    if bpy.context.view_layer.objects.active is not obj or bpy.context.object is not obj:
        raise ShapeKeyCleanupError("The active Mesh changed; select the same Mesh and try again.")
    if obj.mode != "EDIT":
        raise ShapeKeyCleanupError("The Mesh left Edit Mode; run Shape Key cleanup again.")
    current_indices = _selected_vertex_indices(obj)
    if current_indices != plan.requested_vertex_indices:
        raise ShapeKeyCleanupError("The vertex selection changed; run Shape Key cleanup again.")
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    shape_keys = obj.data.shape_keys
    if (shape_keys is None or not shape_keys.use_relative
            or len(bm.verts) != plan.vertex_count or len(obj.data.vertices) != plan.vertex_count):
        raise ShapeKeyCleanupError("The Mesh or Shape Keys changed; run Shape Key cleanup again.")
    keys = shape_keys.key_blocks
    selected_names = tuple(key.name for key in keys if key != shape_keys.reference_key and key.select)
    if selected_names != plan.key_names or any(getattr(keys[name], 'lock_shape', False) for name in plan.key_names):
        raise ShapeKeyCleanupError("The Shape Key selection or locks changed; run cleanup again.")
    for name, relative_name in plan.relative_keys:
        key = keys.get(name)
        if key is None or key.relative_key is None or key.relative_key.name != relative_name:
            raise ShapeKeyCleanupError("A relative Shape Key changed; run cleanup again.")
    for name, expected in plan.sources:
        key = keys.get(name)
        live = (tuple(bm.verts[index].co.copy() for index in plan.vertex_indices)
                if key == obj.active_shape_key else _bmesh_shape_coordinates(obj, name, plan.vertex_indices))
        if live != expected or _key_data_coordinates(key, plan.vertex_indices) != expected:
            raise ShapeKeyCleanupError("Shape Key coordinates changed after planning; run cleanup again.")

    try:
        _write_records(obj, plan.vertex_indices, plan.targets)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        obj.data.update()
        _verify_records(obj, plan.vertex_indices, plan.targets)
        _select_edit_vertices(obj, plan.vertex_indices)
    except Exception as exc:
        try:
            _write_records(obj, plan.vertex_indices, plan.before)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            obj.data.update()
            _verify_records(obj, plan.vertex_indices, plan.before)
        except Exception as rollback:
            raise ShapeKeyCleanupError(
                f"Shape Key cleanup failed ({exc}); rollback also failed ({rollback})."
            ) from rollback
        raise ShapeKeyCleanupError(f"Shape Key cleanup was rolled back: {exc}") from exc
    return plan


class _ShapeKeyClearOperator(Operator):
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (
            active_ui_page(context) == UI_PAGE_MISC
            and context.mode == "EDIT_MESH"
            and context.view_layer is not None
            and context.view_layer.objects.active is not None
            and context.view_layer.objects.active.type == "MESH"
        )

    def execute(self, context):
        try:
            plan = build_shape_key_clear_plan(context)
            apply_shape_key_clear_plan(plan)
        except ShapeKeyCleanupError as exc:
            self.report({"WARNING"}, str(exc))
            return {"CANCELLED"}
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Shape Key cleanup failed safely: {exc}")
            return {"CANCELLED"}

        if plan.changed_count:
            self.report(
                {"INFO"},
                f"Cleared {plan.changed_count} selected Shape Key point(s) from "
                f"{len(plan.key_names)} selected key(s); "
                f"selected {len(plan.mirror_pairs)} mirrored pair(s); Undo available.",
            )
        else:
            self.report({"INFO"}, "Selected vertices already match their relative key in the selected Shape Keys.")
        return {"FINISHED"}


class CHARACTERDESIGNER_OT_clear_shape_key_selected(_ShapeKeyClearOperator):
    bl_idname = "character_designer.clear_shape_key_selected"
    bl_label = "Clear Selected from Chosen Keys"
    bl_description = (
        "Restore selected vertices in every Shape Key selected in the Shape Keys list "
        "to its own relative key"
    )


class CHARACTERDESIGNER_PT_shape_key_tools(Panel):
    bl_label = "Shape Key"
    bl_idname = "CHARACTERDESIGNER_PT_shape_key_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return active_ui_page(context) == UI_PAGE_MISC

    def draw(self, context):
        layout = self.layout
        layout.label(text="Clear accidental selected-vertex deformation", icon="SHAPEKEY_DATA")
        layout.operator(
            "character_designer.clear_shape_key_selected",
            text="Clear Selected from Chosen Keys",
            icon="KEY_HLT",
        )


SHAPE_KEY_CLASSES = (
    CHARACTERDESIGNER_OT_clear_shape_key_selected,
    CHARACTERDESIGNER_PT_shape_key_tools,
)
