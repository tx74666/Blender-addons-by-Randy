"""Read Blender's own Topology Mirror table without touching the artist mesh.

Blender exposes its topology correspondence through Select Mirror, rather than
through a Python lookup function. Binary-coded selections read the entire native
table in ``vertex_count.bit_length()`` operator calls on a disposable scene.
Only that disposable mesh enters Edit Mode. No artist mode, selection, topology,
Shape Key, group, or mirror setting is changed, and no temporary ID is retained.

The lookup is strictly native: unavailable or nonreciprocal entries stay
unmatched. Coordinates only orient a known pair and qualify a near-plane native
self-pair; they never search for or substitute a counterpart.
"""

from functools import lru_cache
import math

import bmesh
import bpy

from .symmetry_pairs import SymmetryPairError, VertexPairMap


def _validated_input(coordinates, edges, faces, axis):
    axis = str(axis).upper()
    if axis not in {'X', 'Y', 'Z'}:
        raise SymmetryPairError('Choose the X, Y or Z symmetry axis.')
    points = tuple(tuple(float(value) for value in coordinate) for coordinate in coordinates)
    if any(len(point) != 3 or not all(math.isfinite(value) for value in point) for point in points):
        raise SymmetryPairError('Every vertex must have a finite 3D coordinate.')
    count = len(points)
    links = tuple(tuple(edge) for edge in edges)
    polygons = tuple(tuple(face) for face in faces)
    if any(len(edge) != 2 or len(set(edge)) != 2
           or any(not isinstance(index, int) or not 0 <= index < count for index in edge)
           for edge in links):
        raise SymmetryPairError('An edge has invalid vertex indices.')
    if len({tuple(sorted(edge)) for edge in links}) != len(links):
        raise SymmetryPairError('Duplicate edges prevent a faithful native mirror lookup.')
    if any(len(face) < 3 or len(set(face)) != len(face)
           or any(not isinstance(index, int) or not 0 <= index < count for index in face)
           for face in polygons):
        raise SymmetryPairError('A face has invalid or repeated vertex indices.')
    return points, links, polygons, axis


def _operator_context(scene, obj, *, editing=False):
    layer = scene.view_layers[0]
    return bpy.context.temp_override(
        scene=scene, view_layer=layer, object=obj, active_object=obj,
        edit_object=obj if editing else None,
        selected_objects=[obj], selected_editable_objects=[obj],
        objects_in_mode=[obj] if editing else [],
        objects_in_mode_unique_data=[obj] if editing else [],
        mode='EDIT_MESH' if editing else 'OBJECT',
    )


@lru_cache(maxsize=4)
def _lookup_for_topology(count, edges, faces, axis, blender_version):
    """Cache only immutable topology and Python indices, never Blender IDs.

    Topology Mirror ignores coordinates. Deliberately nonsymmetric disposable
    positions also ensure the native topology operator is doing the pairing.
    The full ordered topology key avoids Blender's count-only cache invalidation.
    """
    del blender_version  # The value remains part of the cache key.
    if not count:
        return ()
    scene = mesh = obj = None
    try:
        scene = bpy.data.scenes.new('Character Designer Mirror Lookup')
        mesh = bpy.data.meshes.new('Character Designer Mirror Lookup')
        obj = bpy.data.objects.new('Character Designer Mirror Lookup', mesh)
        # Vertex order and all supplied edge/face indices are preserved. These
        # coordinates are confined to the disposable mesh and are never copied
        # back to the artist's data.
        mesh.from_pydata([(float(index + 1), .0, .0) for index in range(count)], edges, faces)
        scene.collection.objects.link(obj)
        layer = scene.view_layers[0]
        layer.objects.active = obj
        obj.select_set(True, view_layer=layer)
        scene.tool_settings.mesh_select_mode = (True, False, False)
        scene.tool_settings.use_mesh_automerge = False
        scene.tool_settings.use_proportional_edit = False
        mesh.use_mirror_topology = True
        with _operator_context(scene, obj):
            response = bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='EDIT')
            if 'FINISHED' not in response or obj.mode != 'EDIT':
                raise SymmetryPairError('Blender could not start the isolated topology lookup.')
        with _operator_context(scene, obj, editing=True):
            bm = bmesh.from_edit_mesh(mesh)
            bm.verts.ensure_lookup_table()
            bm.verts.index_update()
            values = [0] * count
            # Code index+1 reserves zero for a vertex absent from every mirrored
            # selection. Native self-pairs can therefore be distinguished from
            # native unmatched vertices without a geometric guess.
            for bit in range(count.bit_length()):
                for face in bm.faces:
                    face.select_set(False)
                for edge in bm.edges:
                    edge.select_set(False)
                for index, vertex in enumerate(bm.verts):
                    vertex.select_set(bool((index + 1) & (1 << bit)))
                bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
                response = bpy.ops.mesh.select_mirror('EXEC_DEFAULT', False, axis={axis}, extend=False)
                if 'FINISHED' not in response:
                    raise SymmetryPairError('Blender could not read its native topology mirror table.')
                for index, vertex in enumerate(bm.verts):
                    if vertex.select:
                        values[index] |= 1 << bit
            lookup = tuple(value - 1 for value in values)
            # Select Mirror's output normally is an involution. A Blender error
            # or unsupported table must never turn into a guessed pairing.
            return tuple(target if 0 <= target < count and lookup[target] == index else -1
                         for index, target in enumerate(lookup))
    except SymmetryPairError:
        raise
    except Exception as exc:
        raise SymmetryPairError(f'Blender Topology Mirror lookup failed: {exc}') from exc
    finally:
        try:
            if obj is not None and obj.mode == 'EDIT':
                with _operator_context(scene, obj, editing=True):
                    bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode='OBJECT')
        finally:
            try:
                if obj is not None:
                    bpy.data.objects.remove(obj, do_unlink=True)
            finally:
                try:
                    if mesh is not None:
                        bpy.data.meshes.remove(mesh)
                finally:
                    if scene is not None:
                        bpy.data.scenes.remove(scene)


def clear_native_pair_cache():
    _lookup_for_topology.cache_clear()


def native_vertex_lookup(coordinates, edges, faces=(), *, axis='X'):
    """Return Blender's actual reciprocal mirror index, self index, or -1."""
    points, links, polygons, axis = _validated_input(coordinates, edges, faces, axis)
    return _lookup_for_topology(len(points), links, polygons, axis, tuple(bpy.app.version))


def build_vertex_pairs(coordinates, edges, faces=(), *, axis='X', centerline_tolerance=1e-6):
    """Orient native pairs across the chosen local axis plane at zero.

    Native self-pairs away from the plane, native pairs on the same side, and
    vertices absent from native Topology Mirror remain unmatched.
    """
    points, links, polygons, axis = _validated_input(coordinates, edges, faces, axis)
    if not math.isfinite(centerline_tolerance) or centerline_tolerance < 0:
        raise SymmetryPairError('Centerline tolerance must be finite and nonnegative.')
    lookup = _lookup_for_topology(len(points), links, polygons, axis, tuple(bpy.app.version))
    component = 'XYZ'.index(axis)
    pairs, centers, unmatched, rejected = [], [], [], []
    for index, target in enumerate(lookup):
        position = points[index][component]
        if target == -1:
            unmatched.append(index)
        elif target == index:
            if abs(position) <= centerline_tolerance:
                centers.append(index)
            else:
                unmatched.append(index)
                rejected.append(index)
        elif index < target:
            other = points[target][component]
            if position < 0 < other:
                pairs.append((index, target))
            elif other < 0 < position:
                pairs.append((target, index))
            else:
                unmatched.extend((index, target))
                rejected.extend((index, target))
    diagnostics = ['Pairs come directly from Blender Topology Mirror.']
    if rejected:
        diagnostics.append(f'{len(rejected)} native matches do not cross the {axis}=0 plane and were left unchanged.')
    return VertexPairMap(tuple(sorted(pairs)), tuple(centers), tuple(sorted(unmatched)),
                         (), len(points).bit_length(), True, tuple(diagnostics))
