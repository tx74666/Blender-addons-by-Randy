"""Mesh-only, selection-driven mirror planning and transactional replacement.

No rig or vertex group is required. All decisions use the undeformed mesh.
The source is immutable; attachment seams are the only permitted welds.
"""

from dataclasses import dataclass
import hashlib
import math
from statistics import median

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

from . import topology_symmetry as legacy
from . import weight_symmetry as weights
from .mesh_mirror_match import RegionMatcher, choose_unique


class MirrorError(RuntimeError):
    """A preflight failure that can be reported without a traceback."""


@dataclass(frozen=True)
class Candidate:
    faces: tuple
    center: tuple
    distance: float
    automatic: bool
    vertices: tuple = ()
    edges: tuple = ()
    score: float = float('inf')
    coverage: tuple = (0.0, 0.0)
    closed: bool = False


@dataclass
class MirrorPlan:
    obj: object
    reference: object
    reflection: object
    source_faces: tuple
    source_vertices: tuple
    target_faces: tuple
    seams: tuple
    candidates: tuple
    needs_choice: bool
    tolerance: float
    epsilon: float
    fingerprint: str
    reference_matrix: tuple
    groups: tuple
    target_vertices: tuple = ()
    target_edges: tuple = ()
    region_kind: str = 'REGION'


VALUE_PROPERTIES = ("value", "vector", "color", "uv", "quaternion")
INTERNAL_ATTRIBUTES = {"position", ".edge_verts", ".corner_vert", ".corner_edge"}


def _value(item):
    for name in VALUE_PROPERTIES:
        if hasattr(item, name):
            value = getattr(item, name)
            return name, tuple(value) if hasattr(value, "__len__") and not isinstance(value, (str, bytes)) else value
    raise MirrorError("This mesh contains an unsupported attribute value type.")


def _matrix_tuple(matrix):
    return tuple(tuple(row) for row in matrix)


def _fingerprint(obj):
    mesh = obj.data
    # Include artist data as well as positions so a preview cannot apply stale data.
    payload = (legacy._geometry_fingerprint(obj),
               tuple((a.name, a.domain, a.data_type, tuple(_value(d) for d in a.data))
                     for a in mesh.attributes if a.name not in INTERNAL_ATTRIBUTES
                     and not a.name.startswith((".select", ".hide"))),
               tuple((e.use_seam, e.use_edge_sharp, getattr(e, 'use_freestyle_mark', False)) for e in mesh.edges),
               tuple(getattr(p, 'use_freestyle_mark', False) for p in mesh.polygons),
               tuple(tuple(n.vector) for n in mesh.corner_normals) if mesh.has_custom_normals else (),
               tuple(m.name_full if m else None for m in mesh.materials),
               repr(weights._capture_vertex_groups(obj)))
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def mirror_frame(obj, reference=None):
    """Return an affine reflection in mesh coordinates, and its plane covector.

    Mesh Local X means exactly x -> -x. A reference object defines a geometric
    world-space plane (inverse-transpose normal), including scaled/sheared refs.
    """
    if abs(obj.matrix_world.determinant()) < 1e-12:
        raise MirrorError("The mesh has a zero scale; choose an invertible transform.")
    if reference is None:
        return Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0)), Vector((1, 0, 0)), 0.0
    if abs(reference.matrix_world.determinant()) < 1e-12 or abs(obj.matrix_world.determinant()) < 1e-12:
        raise MirrorError("The mesh or reference has a zero scale; choose an invertible transform.")
    normal = (reference.matrix_world.to_3x3().inverted().transposed() @ Vector((1, 0, 0))).normalized()
    origin = reference.matrix_world.translation
    world = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            world[i][j] -= 2 * normal[i] * normal[j]
        world[i][3] = 2 * normal[i] * normal.dot(origin)
    local_normal = obj.matrix_world.to_3x3().transposed() @ normal
    length = local_normal.length
    offset = normal.dot(obj.matrix_world.translation - origin) / length
    return obj.matrix_world.inverted() @ world @ obj.matrix_world, local_normal / length, offset


def _components(faces, blocked=()):
    remaining, result, blocked = set(faces), [], set(blocked)
    while remaining:
        todo = [remaining.pop()]
        component = set(todo)
        while todo:
            face = todo.pop()
            for edge in face.edges:
                if edge in blocked:
                    continue
                for other in edge.link_faces:
                    if other in remaining:
                        remaining.remove(other)
                        component.add(other)
                        todo.append(other)
        result.append(component)
    return result


def _bounds(points):
    return (Vector(tuple(min(p[i] for p in points) for i in range(3))),
            Vector(tuple(max(p[i] for p in points) for i in range(3))))


def _overlaps(a, b, margin):
    return all(a[0][i] - margin <= b[1][i] and b[0][i] - margin <= a[1][i] for i in range(3))


def _vertex_components(vertices):
    remaining = set(vertices)
    while remaining:
        seed = remaining.pop()
        component, todo = {seed}, [seed]
        while todo:
            for edge in todo.pop().link_edges:
                for vertex in edge.verts:
                    if vertex in remaining:
                        remaining.remove(vertex)
                        component.add(vertex)
                        todo.append(vertex)
        yield component


def _region_components(bm, selected, blocked, whole_island):
    if whole_island:
        for vertices in _vertex_components(bm.verts):
            faces = {f for v in vertices for f in v.link_faces}
            if not faces & selected:
                yield vertices, faces
        return
    boundary = {v for edge in blocked for v in edge.verts}
    for faces in _components(set(bm.faces) - selected, blocked):
        vertices = {v for f in faces for v in f.verts}
        # Include dangling damaged geometry on the chosen side of the loop,
        # but never walk across the retained seam to the body/root region.
        todo = list(vertices - boundary)
        while todo:
            for edge in todo.pop().link_edges:
                for vertex in edge.verts:
                    if vertex not in vertices and vertex not in boundary:
                        vertices.add(vertex)
                        todo.append(vertex)
        yield vertices, faces


def _validate_selection_boundary(edges):
    """Closed loops, or paths ending on an existing mesh border; no branches."""
    vertices = {v for edge in edges for v in edge.verts}
    for vertex in vertices:
        degree = sum(edge in edges for edge in vertex.link_edges)
        if degree > 2 or (degree == 1 and not vertex.is_boundary):
            raise MirrorError('The selected region must end at a complete, non-branching boundary loop. Select the faces on the side to replace.')


def _indexed_geometry(faces, transform=None):
    verts = sorted({v for f in faces for v in f.verts}, key=lambda v: v.index)
    lookup = {v: i for i, v in enumerate(verts)}
    return ([transform @ v.co if transform is not None else v.co.copy() for v in verts],
            [tuple(lookup[v] for v in f.verts) for f in sorted(faces, key=lambda f: f.index)])


def _preflight_data(obj):
    mesh = obj.data
    if mesh.users != 1 or not obj.is_editable or not mesh.is_editable:
        raise MirrorError("Use an editable, single-user mesh before mirroring.")
    if mesh.shape_keys:
        if obj.active_shape_key_index != 0:
            raise MirrorError("Select the Basis shape key before mirroring topology.")
        if mesh.shape_keys.animation_data:
            raise MirrorError("Animated/driven Shape Keys are not supported by topology replacement; no data was changed.")
        if not mesh.shape_keys.use_relative:
            raise MirrorError("Absolute Shape Keys are not supported by topology replacement; no data was changed.")
        for key in mesh.shape_keys.key_blocks:
            if getattr(key, 'lock_shape', False):
                raise MirrorError(f'Shape Key "{key.name}" is locked; unlock it explicitly before replacing topology.')
            if key.vertex_group and _opposite_group(key.vertex_group) != key.vertex_group:
                raise MirrorError(f'Shape Key "{key.name}" has a side-specific group mask; its mirrored influence cannot be preserved safely.')
    if mesh.animation_data:
        raise MirrorError("Mesh-data animation cannot be safely remapped; no data was changed.")
    for modifier in obj.modifiers:
        if modifier.type == 'MIRROR' and (modifier.show_viewport or modifier.show_render):
            raise MirrorError(f'{modifier.name}: a Mirror modifier already generates the opposite side. Disable it for viewport and render explicitly before baking geometry.')
        if modifier.type == 'MULTIRES' and modifier.total_levels:
            raise MirrorError("Multires sculpt levels cannot survive topology replacement; use a separate base mesh.")
        if modifier.type in {'HOOK', 'MESH_DEFORM', 'SURFACE_DEFORM', 'SOFT_BODY', 'CLOTH', 'PARTICLE_SYSTEM'}:
            raise MirrorError(f'{modifier.name}: vertex-index/baked simulation data cannot be safely remapped.')
    if len(mesh.skin_vertices):
        raise MirrorError("Skin vertex data is not supported by this topology operation.")
    if any(child.parent == obj and child.parent_type in {'VERTEX', 'VERTEX_3'} for child in bpy.data.objects):
        raise MirrorError("An object is parented to mesh vertices; its vertex indices cannot be safely remapped.")
    for attribute in mesh.attributes:
        if attribute.name in INTERNAL_ATTRIBUTES:
            continue
        if attribute.domain not in {'POINT', 'EDGE', 'FACE', 'CORNER'}:
            raise MirrorError(f'Cannot preserve attribute "{attribute.name}" on {attribute.domain}.')
        for item in attribute.data[:1]:
            _value(item)


def build_plan(context, reference=None, tolerance=0.0, target_candidate=0):
    """Read-only preflight. Candidate 0 is automatic, 1..n explicitly select a target.

    A plan with needs_choice=True is previewable but never executable. There is
    deliberately no 'append anyway' option when an overlapping target exists.
    """
    obj = context.edit_object
    if context.mode != 'EDIT_MESH' or obj is None or obj.type != 'MESH':
        raise MirrorError("Enter Mesh Edit Mode and select one source face region or a whole hair island.")
    if len(context.objects_in_mode_unique_data) != 1:
        raise MirrorError("Edit only one mesh for Mirror Selected Region.")
    obj.update_from_editmode()
    _preflight_data(obj)
    reflection, normal, offset = mirror_frame(obj, reference)
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
        seq.index_update()
    selected = {f for f in bm.faces if f.select and not f.hide}
    if not selected:
        raise MirrorError("Select complete source faces (or a linked hair island).")
    source_verts = {v for f in selected for v in f.verts}
    if any(v.select and not v.hide and v not in source_verts for v in bm.verts):
        raise MirrorError("The selection contains loose/partial vertices; select complete source faces only.")
    if len(_components(selected)) != 1:
        raise MirrorError("Select one connected source region at a time.")
    source_edges = {e for f in selected for e in f.edges}
    if any(len(e.link_faces) > 2 for e in source_edges):
        raise MirrorError("The source has a non-manifold edge; repair it before mirroring.")
    bounds = _bounds([v.co for v in source_verts])
    span = (bounds[1] - bounds[0]).length
    epsilon = max(span * 1e-6, 1e-7)
    if any(not all(math.isfinite(c) for c in v.co) for v in bm.verts):
        raise MirrorError("The mesh has non-finite coordinates; repair it before mirroring.")
    if any(f.calc_area() <= epsilon * epsilon for f in selected):
        raise MirrorError("The source contains degenerate faces; repair them before mirroring.")
    side_values = [normal.dot(v.co) + offset for v in source_verts]
    signs = {1 if value > 0 else -1 for value in side_values if abs(value) > epsilon}
    if len(signs) != 1:
        raise MirrorError("Select one side plus optional centerline vertices; faces crossing both sides are not cut automatically.")
    side = signs.pop()
    tolerance = tolerance or max(median([e.calc_length() for e in source_edges]) * .10, epsilon * 8)
    centers = {v for v in source_verts if abs(normal.dot(v.co) + offset) <= epsilon}
    attached = {e for e in source_edges if any(f not in selected for f in e.link_faces)}
    # A centerline is a virtual boundary even when no opposite half exists yet.
    center_edges = {e for e in source_edges if len(e.link_faces) == 1 and all(v in centers for v in e.verts)}
    seam_edges = attached | center_edges
    _validate_selection_boundary(seam_edges)
    whole_island = not seam_edges
    seam_verts = {v for e in seam_edges for v in e.verts}
    if centers - seam_verts:
        raise MirrorError("Centerline contact must form boundary edges, not isolated interior/contact vertices.")
    if any(any(e not in source_edges and not e.link_faces for e in v.link_edges) for v in source_verts):
        raise MirrorError("The source touches loose edges; detach or include a complete face-only region.")
    if any(any(f not in selected for f in v.link_faces) and v not in seam_verts for v in source_verts):
        raise MirrorError("The source touches other faces at only a vertex; the attachment is ambiguous.")
    tree = KDTree(len(bm.verts))
    for v in bm.verts:
        tree.insert(v.co, v.index)
    tree.balance()
    seams = {}
    for v in seam_verts:
        if v in centers:
            seams[v.index] = v.index
            continue
        hits = [index for _, index, distance in tree.find_range(reflection @ v.co, tolerance)
                if bm.verts[index] not in source_verts and (normal.dot(bm.verts[index].co) + offset) * side < -epsilon]
        if len(hits) != 1:
            raise MirrorError("The opposite boundary loop is missing or ambiguous. Select a matching loop-bounded region or the whole island.")
        seams[v.index] = hits[0]
    if len(set(seams.values())) != len(seams):
        raise MirrorError("The attachment would weld unrelated source vertices together.")
    blocked = set()
    for edge in seam_edges:
        a, b = (bm.verts[seams[v.index]] for v in edge.verts)
        other = bm.edges.get((a, b))
        if other is None:
            raise MirrorError("The opposite attachment edges do not form the same seam; no geometry was changed.")
        blocked.add(other)
    source_points, source_polys = _indexed_geometry(selected, reflection)
    mirror_bounds = _bounds(source_points)
    margin = max(tolerance * 2, span * .05)
    matcher = RegionMatcher(source_points, source_polys, tolerance)
    candidates, obstructions = [], []
    for verts, component in _region_components(bm, selected, blocked, whole_island):
        ordered_verts = sorted(verts, key=lambda v: v.index)
        indices = {v: i for i, v in enumerate(ordered_verts)}
        points = [v.co.copy() for v in ordered_verts]
        polys = [tuple(indices[v] for v in f.verts) for f in sorted(component, key=lambda f: f.index)]
        # The rest of a connected body legitimately touches the mirror bounds
        # at the seam. Test its interior, not shared seam endpoints alone.
        interior_samples = [v.co for v in verts if v.index not in seams.values()]
        interior_samples.extend(f.calc_center_median() for f in component)
        if not interior_samples or not _overlaps(mirror_bounds, _bounds(interior_samples), margin):
            continue
        evidence = matcher.compare(points, polys)
        if not evidence.plausible:
            continue
        if blocked:
            touched = {e for f in component for e in f.edges if e in blocked}
            if touched != blocked:
                # Nearby independent hair is still an obstruction: never append
                # just because the attachment graph did not identify it.
                if not touched:
                    obstructions.append(component)
                continue
        if any((normal.dot(v.co) + offset) * side > epsilon for v in verts):
            obstructions.append(component)
            continue
        edges = {e for f in component for e in f.edges}
        external = {e for e in edges if any(f not in component for f in e.link_faces)}
        if external - blocked:
            obstructions.append(component)
            continue
        # Components on the same center seam can touch the rest only at those
        # vertices; reject hidden vertex-only connections and loose attachments.
        outside_verts = {v for v in verts if any(f not in component for f in v.link_faces)
                         or any(any(other not in verts for other in e.verts) for e in v.link_edges)}
        if outside_verts - {bm.verts[i] for i in seams.values()}:
            obstructions.append(component)
            continue
        center = sum(points, Vector()) / len(points)
        region_edges = {e for v in verts for e in v.link_edges if all(other in verts for other in e.verts)}
        closed = bool(component) and all(len(e.link_faces) == 2 for e in region_edges)
        candidates.append(Candidate(tuple(sorted(f.index for f in component)), tuple(center), evidence.distance,
                                    evidence.confident, tuple(v.index for v in ordered_verts),
                                    tuple(sorted(e.index for e in region_edges)), evidence.score,
                                    evidence.coverage, closed))
    candidates.sort(key=lambda c: c.vertices[0])
    if not candidates and obstructions:
        raise MirrorError("The mirrored area overlaps another connected region. The target cannot be isolated safely; select a complete island or a matching attachment seam.")
    chosen = None
    if target_candidate:
        if not 1 <= target_candidate <= len(candidates):
            raise MirrorError("That target candidate is no longer available; preview again.")
        chosen = candidates[target_candidate - 1]
    elif not obstructions:
        chosen = choose_unique(candidates)
    needs_choice = bool(candidates and chosen is None)
    if obj.data.shape_keys:
        for key in obj.data.shape_keys.key_blocks:
            if any((reflection @ key.data[v.index].co - key.data[v.index].co).length > epsilon * 2 for v in centers):
                raise MirrorError(f'Shape Key "{key.name}" moves the shared centerline off the mirror plane; the source must remain unchanged.')
    plan = MirrorPlan(obj, reference, reflection, tuple(sorted(f.index for f in selected)),
                      tuple(sorted(v.index for v in source_verts)), chosen.faces if chosen else (),
                      tuple(sorted(seams.items())), tuple(candidates), needs_choice, tolerance, epsilon,
                      '', _matrix_tuple(reference.matrix_world) if reference else (), ())
    plan.target_vertices = chosen.vertices if chosen else ()
    plan.target_edges = chosen.edges if chosen else ()
    plan.region_kind = 'ISLAND' if whole_island else 'LOOP_REGION'
    # Blender exposes some attribute collections only in Object Mode. Normalize
    # the edit buffer before snapshotting, then restore the user's mode/selection.
    bpy.ops.object.mode_set(mode='OBJECT')
    try:
        _preflight_data(obj)
        plan.fingerprint = _fingerprint(obj)
        plan.groups = weights._capture_vertex_groups(obj)
    finally:
        bpy.ops.object.mode_set(mode='EDIT')
    return plan


def _opposite_group(name):
    try:
        return weights._strict_opposite_name(name)
    except weights.WeightSymmetryError:
        return name


def _copy_id_properties(source, target):
    for key in source.keys():
        target[key] = source[key]
        try:
            target.id_properties_ui(key).update_from(source.id_properties_ui(key))
        except (TypeError, KeyError):
            pass


def _rebuild(plan):
    old = plan.obj.data
    target = set(plan.target_faces)
    retained_faces = [p for p in old.polygons if p.index not in target]
    retained_verts = {i for p in retained_faces for i in p.vertices}
    seams = dict(plan.seams)
    target_verts = set(plan.target_vertices) | {i for index in target for i in old.polygons[index].vertices}
    remove_verts = target_verts - retained_verts - set(seams.values())
    # Loose data elsewhere is preserved, not reconstructed solely from faces.
    loose_edges = [e for e in old.edges if e.is_loose
                   and not (e.index in plan.target_edges and set(e.vertices) & remove_verts)]
    if any(set(e.vertices) & remove_verts for e in loose_edges):
        raise MirrorError("The target has loose-edge attachments that cannot be replaced safely.")
    origins = [(False, v.index) for v in old.vertices if v.index not in remove_verts]
    old_to_new = {index: new for new, (_, index) in enumerate(origins)}
    coords = [old.vertices[index].co.copy() for _, index in origins]
    mirrored = {}
    for index in plan.source_vertices:
        if index in seams:
            destination = old_to_new[seams[index]]
            if index != seams[index]:
                origins[destination] = (True, index)
                coords[destination] = plan.reflection @ old.vertices[index].co
        else:
            destination = len(coords)
            origins.append((True, index))
            coords.append(plan.reflection @ old.vertices[index].co)
        mirrored[index] = destination
    polys, face_origins, loop_origins = [], [], []
    for poly in retained_faces:
        polys.append(tuple(old_to_new[i] for i in poly.vertices))
        face_origins.append((False, poly.index))
        loop_origins.extend((False, i) for i in poly.loop_indices)
    for index in plan.source_faces:
        poly = old.polygons[index]
        polys.append(tuple(mirrored[i] for i in reversed(poly.vertices)))
        face_origins.append((True, index))
        loop_origins.extend((True, i) for i in reversed(poly.loop_indices))
    edge_sources = {}
    for edge in old.edges:
        if all(i in old_to_new for i in edge.vertices):
            edge_sources[tuple(sorted(old_to_new[i] for i in edge.vertices))] = (False, edge.index)
    source_edges = {old.loops[i].edge_index for fi in plan.source_faces for i in old.polygons[fi].loop_indices}
    for index in source_edges:
        edge = old.edges[index]
        pair = tuple(sorted(mirrored[i] for i in edge.vertices))
        # Do not overwrite source centerline edge attributes.
        if not all(i in seams and seams[i] == i for i in edge.vertices):
            edge_sources[pair] = (True, index)
    new = bpy.data.meshes.new(old.name + ".Mirror")
    try:
        new.from_pydata(coords, [tuple(old_to_new[i] for i in e.vertices) for e in loose_edges], polys)
        new.update()
        edge_origins = [edge_sources[tuple(sorted(e.vertices))] for e in new.edges]
        mapping = {'POINT': origins, 'EDGE': edge_origins, 'FACE': face_origins, 'CORNER': loop_origins}
        for material in old.materials:
            new.materials.append(material)
        for i, (_, index) in enumerate(face_origins):
            new.polygons[i].material_index = old.polygons[index].material_index
            new.polygons[i].use_smooth = old.polygons[index].use_smooth
            if hasattr(old.polygons[index], 'use_freestyle_mark'):
                new.polygons[i].use_freestyle_mark = old.polygons[index].use_freestyle_mark
        for i, (_, index) in enumerate(edge_origins):
            new.edges[i].use_seam = old.edges[index].use_seam
            new.edges[i].use_edge_sharp = old.edges[index].use_edge_sharp
            if hasattr(old.edges[index], 'use_freestyle_mark'):
                new.edges[i].use_freestyle_mark = old.edges[index].use_freestyle_mark
        # UV layers first, so metadata (active/render/clone) is retained too.
        for layer in old.uv_layers:
            created = new.uv_layers.new(name=layer.name, do_init=False)
            created.active_render = layer.active_render
            created.active_clone = layer.active_clone
        if old.uv_layers:
            new.uv_layers.active_index = old.uv_layers.active_index
        for attribute in old.attributes:
            if attribute.name in INTERNAL_ATTRIBUTES or attribute.name == 'custom_normal':
                continue
            created = new.attributes.get(attribute.name)
            if created is None:
                created = new.attributes.new(attribute.name, attribute.data_type, attribute.domain)
            if created.domain != attribute.domain or created.data_type != attribute.data_type:
                raise MirrorError(f'Attribute "{attribute.name}" cannot be preserved without changing its type.')
            for i, (_, index) in enumerate(mapping[attribute.domain]):
                name, value = _value(attribute.data[index])
                setattr(created.data[i], name, value)
        if old.color_attributes.active_color:
            new.color_attributes.active_color = new.color_attributes[old.color_attributes.active_color.name]
        if old.color_attributes.render_color_index >= 0:
            new.color_attributes.render_color_index = old.color_attributes.render_color_index
        if old.has_custom_normals:
            matrix = plan.reflection.to_3x3().inverted().transposed()
            normals = [tuple((matrix @ old.corner_normals[index].vector).normalized()) if mirror
                       else tuple(old.corner_normals[index].vector) for mirror, index in loop_origins]
            if bpy.app.version >= (4, 5, 0):
                # Free corner normals preserve decoded directions exactly. The
                # legacy setter re-encodes/averages smooth fans and can introduce
                # sharp edges, even on retained parts of this local operation.
                attribute = new.attributes.new('custom_normal', 'FLOAT_VECTOR', 'CORNER')
                attribute.data.foreach_set('vector', [c for normal in normals for c in normal])
                new.update()
            else:
                new.normals_split_custom_set(normals)
        _copy_id_properties(old, new)
        return new, origins, tuple(old_to_new[i] for i in plan.source_vertices), mapping
    except Exception:
        bpy.data.meshes.remove(new)
        raise


def _populate_shapes(staging, old, plan, origins):
    if not old.shape_keys:
        return
    for source in old.shape_keys.key_blocks:
        key = staging.shape_key_add(name=source.name, from_mix=False)
        for i, (mirror, index) in enumerate(origins):
            co = source.data[index].co
            key.data[i].co = plan.reflection @ co if mirror else co
        key.slider_min = min(source.slider_min, key.slider_min)
        key.slider_max = max(source.slider_max, key.slider_max)
        for name in ('slider_min', 'slider_max', 'value', 'mute', 'interpolation', 'vertex_group', 'select'):
            if not hasattr(source, name):
                continue
            setattr(key, name, getattr(source, name))
    dest = staging.data.shape_keys
    for source in old.shape_keys.key_blocks:
        dest.key_blocks[source.name].relative_key = dest.key_blocks[source.relative_key.name]
    dest.use_relative = old.shape_keys.use_relative
    dest.eval_time = old.shape_keys.eval_time
    _copy_id_properties(old.shape_keys, dest)


def _group_maps(plan, origins):
    expected = {state.name: {} for state in plan.groups}
    source_maps = {state.name: dict(state.weights) for state in plan.groups}
    for output, (mirror, index) in enumerate(origins):
        for name, values in source_maps.items():
            if index in values:
                destination = _opposite_group(name) if mirror else name
                expected.setdefault(destination, {})[output] = values[index]
    # A locked group is allowed only if its membership stays identical after
    # remapping retained indices. Removed or mirrored weights count as a change.
    old_to_new = {index: i for i, (mirror, index) in enumerate(origins) if not mirror}
    for state in plan.groups:
        if not state.lock_weight:
            continue
        retained = {old_to_new[i]: value for i, value in state.weights if i in old_to_new}
        if len(retained) != len(state.weights) or retained != expected[state.name]:
            raise MirrorError(f'Locked Vertex Group "{state.name}" would change. Unlock it explicitly first.')
    return expected


def _verify_staged(staging, old, plan, origins, mapping, expected):
    new = staging.data
    if len(new.vertices) != len(origins):
        raise MirrorError("Vertex count verification failed.")
    for index, (mirror, source) in enumerate(origins):
        co = plan.reflection @ old.vertices[source].co if mirror else old.vertices[source].co
        if (new.vertices[index].co - co).length > plan.epsilon:
            raise MirrorError("Position verification failed.")
    for attr in old.attributes:
        if attr.name in INTERNAL_ATTRIBUTES or attr.name == 'custom_normal' or attr.name.startswith('.select'):
            continue
        other = new.attributes.get(attr.name)
        if other is None:
            raise MirrorError(f'Attribute "{attr.name}" was not preserved.')
        for index, (_, source) in enumerate(mapping[attr.domain]):
            if _value(other.data[index]) != _value(attr.data[source]):
                raise MirrorError(f'Attribute "{attr.name}" failed preservation verification.')
    if old.has_custom_normals:
        if not new.has_custom_normals or len(new.corner_normals) != len(mapping['CORNER']):
            raise MirrorError('Custom normals were lost; the original mesh was not changed.')
        normal_matrix = plan.reflection.to_3x3().inverted().transposed()
        custom = new.attributes.get('custom_normal')
        tolerance = 2e-6 if custom and custom.data_type == 'FLOAT_VECTOR' else .001
        for index, (reflected, source) in enumerate(mapping['CORNER']):
            normal = old.corner_normals[source].vector
            expected_normal = (normal_matrix @ normal).normalized() if reflected else normal
            # Older Blender uses quantized loop-space values. Free normals on
            # newer versions must retain directions to float precision.
            if (new.corner_normals[index].vector - expected_normal).length > tolerance:
                raise MirrorError('Custom normal preservation verification failed.')
    if old.shape_keys:
        for source in old.shape_keys.key_blocks:
            key = new.shape_keys.key_blocks[source.name]
            for index, (mirror, origin) in enumerate(origins):
                co = plan.reflection @ source.data[origin].co if mirror else source.data[origin].co
                if (key.data[index].co - co).length > plan.epsilon:
                    raise MirrorError(f'Shape Key "{source.name}" failed verification.')
    states = weights._capture_vertex_groups(staging)
    if {s.name: dict(s.weights) for s in states} != expected:
        raise MirrorError("Vertex group verification failed.")


def apply_plan(plan, after_commit=None):
    """Stage and verify every data layer before changing the original object.

    Original groups keep their indices; only missing opposite groups are appended.
    The operator supplies Blender Undo. Any commit exception restores old data.
    """
    obj = plan.obj
    if obj.mode != 'OBJECT':
        raise MirrorError("Leave Edit Mode before applying a prepared plan.")
    if plan.needs_choice:
        raise MirrorError("Choose a numbered target candidate; no geometry was changed.")
    _preflight_data(obj)
    if _fingerprint(obj) != plan.fingerprint or weights._capture_vertex_groups(obj) != plan.groups:
        raise MirrorError("The mesh changed since preview; preview again.")
    if plan.reference and _matrix_tuple(plan.reference.matrix_world) != plan.reference_matrix:
        raise MirrorError("The mirror reference moved since preview; preview again.")
    old, staging, new = obj.data, None, None
    old_group_count = len(obj.vertex_groups)
    active_index, active_shape = obj.vertex_groups.active_index, obj.active_shape_key_index
    try:
        new, origins, selection, mapping = _rebuild(plan)
        expected = _group_maps(plan, origins)
        staging = bpy.data.objects.new(".CharacterDesigner.MirrorStage", new)
        locks = {s.name: s.lock_weight for s in plan.groups}
        for name, values in expected.items():
            group = staging.vertex_groups.new(name=name)
            for index, value in values.items():
                group.add((index,), value, 'REPLACE')
            group.lock_weight = locks.get(name, False)
        _populate_shapes(staging, old, plan, origins)
        _verify_staged(staging, old, plan, origins, mapping, expected)
        # No old weight buffers are edited. The staged mesh already contains
        # the memberships indexed by the unchanged original group order.
        for name in list(expected)[old_group_count:]:
            obj.vertex_groups.new(name=name)
        obj.data = new
        if obj.vertex_groups:
            obj.vertex_groups.active_index = min(active_index, len(obj.vertex_groups) - 1)
        obj.active_shape_key_index = active_shape
        obj.data.update()
        if after_commit:
            after_commit(selection)
        return selection
    except Exception:
        if obj.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if obj.data != old:
            obj.data = old
        # Appended groups have no membership in the original mesh. Removing
        # them cannot alter the original group indices or weights.
        while len(obj.vertex_groups) > old_group_count:
            obj.vertex_groups.remove(obj.vertex_groups[-1])
        if obj.vertex_groups:
            obj.vertex_groups.active_index = min(active_index, len(obj.vertex_groups) - 1)
        obj.active_shape_key_index = active_shape
        raise
    finally:
        if staging:
            bpy.data.objects.remove(staging)
        if new and new.users == 0:
            bpy.data.meshes.remove(new)
