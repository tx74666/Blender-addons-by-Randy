"""Coordinate-only symmetry repair with native topology pairs and key deltas.

No spatial search establishes correspondence. Spatial lookup is used only after
the write to certify Blender's ordinary mirror lookup on the selected local axis.
"""
from array import array
from dataclasses import dataclass, field
import hashlib
import math

import bmesh
import bpy
from mathutils import Vector
from mathutils.kdtree import KDTree

from .native_symmetry_pairs import build_vertex_pairs


ORDINARY_MIRROR_TOLERANCE = 0.00002


class RefineSymmetryError(ValueError):
    """A safe preflight or transaction failure that can be shown to the artist."""


@dataclass(frozen=True)
class Analysis:
    matched: int
    misaligned: int
    unmatched: tuple
    max_error: float
    misaligned_vertices: tuple
    centerline: tuple
    fingerprint: str
    pairs: tuple
    diagnostics: tuple = ()


@dataclass(frozen=True)
class Plan:
    obj: object
    analysis: Analysis
    positions: dict
    mode: str
    selected_only: bool
    tolerance: float
    centerline_tolerance: float
    axis: str
    target_pairs: tuple
    target_centerline: tuple
    _before: object = field(repr=False)
    _expected: object = field(repr=False)


@dataclass(frozen=True)
class Validation:
    matched: int
    misaligned: int
    unmatched: tuple
    max_error: float
    misaligned_vertices: tuple
    centerline: tuple
    fingerprint: str
    pairs: tuple
    ordinary_mirror_ready: bool
    changed_vertices: int
    validated_pairs: int


def _array_coordinates(points):
    result = array('f', [0]) * (3 * len(points))
    points.foreach_get('co', result)
    return result


def _flat(coordinates):
    return array('f', (value for coordinate in coordinates for value in coordinate))


def _coordinate(values, index):
    return tuple(values[index * 3:index * 3 + 3])


def _set_coordinate(values, index, coordinate):
    values[index * 3:index * 3 + 3] = array('f', coordinate)


def _edit_bmesh(obj):
    if obj.mode != 'EDIT':
        return None
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    if len(bm.verts) != len(obj.data.vertices):
        raise RefineSymmetryError('Finish the current topology edit before analyzing symmetry.')
    if any(vertex.index != index for index, vertex in enumerate(bm.verts)):
        raise RefineSymmetryError('Finish the current topology edit before analyzing symmetry.')
    return bm


def _preflight(obj, write=False):
    if obj is None or obj.type != 'MESH' or obj.mode not in {'OBJECT', 'EDIT'}:
        raise RefineSymmetryError('Make a Mesh active in Object or Edit Mode.')
    if write and (obj.library or obj.data.library or not obj.is_editable or not obj.data.is_editable):
        raise RefineSymmetryError('The Mesh is linked or read-only.')
    if write and obj.data.users != 1:
        raise RefineSymmetryError('Make this Mesh Single User before refining symmetry.')
    keys = obj.data.shape_keys
    if keys:
        if keys.reference_key is None or any(len(key.data) != len(obj.data.vertices) for key in keys.key_blocks):
            raise RefineSymmetryError('Every Shape Key must have the same vertex count as the Mesh.')
        if write and (keys.library or not keys.is_editable):
            raise RefineSymmetryError('The Shape Keys are read-only.')
        if write and any(getattr(key, 'lock_shape', False) for key in keys.key_blocks):
            raise RefineSymmetryError('Unlock the Shape Keys before refining their Basis.')
    return _edit_bmesh(obj)


def _key_metadata(key):
    animation = key.id_data.animation_data
    return (key.name, key.value, key.mute, key.slider_min, key.slider_max,
            key.vertex_group, key.relative_key.as_pointer() if key.relative_key else 0,
            key.interpolation, getattr(key, 'lock_shape', False),
            animation.action.as_pointer() if animation and animation.action else 0)


def _invariants(obj, bm):
    mesh = obj.data
    if bm is None:
        edges = tuple(tuple(edge.vertices) for edge in mesh.edges)
        faces = tuple(tuple(face.vertices) for face in mesh.polygons)
        weights = tuple(tuple((group.group, group.weight) for group in vertex.groups) for vertex in mesh.vertices)
        uv = tuple((layer.name, tuple(tuple(loop.uv) for loop in layer.data)) for layer in mesh.uv_layers)
    else:
        edges = tuple(tuple(vertex.index for vertex in edge.verts) for edge in bm.edges)
        faces = tuple(tuple(vertex.index for vertex in face.verts) for face in bm.faces)
        deform = bm.verts.layers.deform.active
        weights = tuple(tuple(sorted(vertex[deform].items())) if deform else () for vertex in bm.verts)
        uv = tuple((name, tuple(tuple(loop[bm.loops.layers.uv[name]].uv)
                              for face in bm.faces for loop in face.loops)) for name in bm.loops.layers.uv.keys())
    groups = tuple((group.name, group.index, group.lock_weight) for group in obj.vertex_groups)
    bindings = (obj.parent.as_pointer() if obj.parent else 0, obj.parent_type, obj.parent_bone,
                tuple(tuple(row) for row in obj.matrix_parent_inverse),
                tuple((modifier.as_pointer(), modifier.type,
                       getattr(modifier, 'object', None).as_pointer() if getattr(modifier, 'object', None) else 0)
                      for modifier in obj.modifiers))
    payload = (obj.as_pointer(), mesh.as_pointer(), len(mesh.vertices), edges, faces, uv, groups, weights, bindings)
    return hashlib.sha256(repr(payload).encode()).hexdigest(), edges, faces


def _state(obj):
    bm = _preflight(obj)
    mesh = obj.data
    mesh_coordinates = _array_coordinates(mesh.vertices)
    active = _flat(vertex.co for vertex in bm.verts) if bm else None
    selected = tuple(vertex.index for vertex in (bm.verts if bm else mesh.vertices) if vertex.select and not vertex.hide)
    keys = []
    basis_name = None
    if mesh.shape_keys:
        basis_name = mesh.shape_keys.reference_key.name
        for key in mesh.shape_keys.key_blocks:
            stored = _array_coordinates(key.data)
            live = stored
            layer_values = None
            if bm:
                layer = bm.verts.layers.shape.get(key.name)
                if layer is None:
                    raise RefineSymmetryError(f'Edit Mode has no Shape Key layer for "{key.name}".')
                layer_values = _flat(vertex[layer] for vertex in bm.verts)
                live = active if key == obj.active_shape_key else layer_values
            keys.append({'name': key.name, 'stored': stored, 'live': live,
                         'layer': layer_values, 'metadata': _key_metadata(key)})
            if any(not math.isfinite(value) for values in (stored, live) for value in values):
                raise RefineSymmetryError(f'Shape Key "{key.name}" contains non-finite coordinates.')
        basis = next(item['live'] for item in keys if item['name'] == basis_name)
    else:
        basis = active if bm else mesh_coordinates
    if any(not math.isfinite(value) for value in basis):
        raise RefineSymmetryError('The Basis contains non-finite coordinates.')
    invariant, edges, faces = _invariants(obj, bm)
    digest = hashlib.sha256()
    digest.update(invariant.encode())
    digest.update(repr((obj.mode, obj.active_shape_key_index, selected)).encode())
    digest.update(bytes(vertex.hide for vertex in (bm.verts if bm else mesh.vertices)))
    digest.update(mesh_coordinates.tobytes())
    if active is not None:
        digest.update(active.tobytes())
    for key in keys:
        digest.update(repr(key['metadata']).encode())
        digest.update(key['stored'].tobytes())
        digest.update(key['live'].tobytes())
        if key['layer'] is not None:
            digest.update(key['layer'].tobytes())
    return {'mesh': mesh_coordinates, 'active': active, 'keys': keys, 'basis': basis,
            'basis_name': basis_name, 'selection': selected, 'invariant': invariant,
            'edges': edges, 'faces': faces, 'fingerprint': digest.hexdigest()}


def fingerprint(obj):
    return _state(obj)['fingerprint']


def _axis_index(axis):
    if axis not in {'X', 'Y', 'Z'}:
        raise RefineSymmetryError('Choose the X, Y or Z symmetry axis.')
    return 'XYZ'.index(axis)


def _mirror(coordinate, component):
    return tuple(-value if index == component else value for index, value in enumerate(coordinate))


def _error(first, second, component=0):
    return math.sqrt(sum((value - other) ** 2 for value, other in zip(_mirror(first, component), second)))


def _analyze_state(state, selected_only, tolerance, centerline_tolerance, axis='X'):
    component = _axis_index(axis)
    if not (math.isfinite(tolerance) and 0 < tolerance < ORDINARY_MIRROR_TOLERANCE):
        raise RefineSymmetryError('Error Tolerance must be smaller than 0.00002 mesh-local units.')
    if not (math.isfinite(centerline_tolerance) and 0 <= centerline_tolerance < ORDINARY_MIRROR_TOLERANCE):
        raise RefineSymmetryError('Centerline Tolerance must be smaller than 0.00002 mesh-local units.')
    coordinates = tuple(_coordinate(state['basis'], index) for index in range(len(state['basis']) // 3))
    try:
        correspondence = build_vertex_pairs(coordinates, state['edges'], state['faces'], axis=axis,
                                            centerline_tolerance=centerline_tolerance)
    except ValueError as exc:
        raise RefineSymmetryError(str(exc)) from exc
    selection = set(state['selection'])
    pairs = tuple(pair for pair in correspondence.pairs if not selected_only or selection.intersection(pair))
    centers = tuple(index for index in correspondence.centerline if not selected_only or index in selection)
    unmatched = tuple(index for index in correspondence.unmatched if not selected_only or index in selection)
    errors = tuple(_error(coordinates[a], coordinates[b], component) for a, b in pairs)
    misaligned_pairs = tuple(pair for pair, error in zip(pairs, errors) if error > tolerance)
    bad_centers = tuple(index for index in centers if 2 * abs(coordinates[index][component]) > tolerance)
    misaligned_vertices = tuple(sorted({index for pair in misaligned_pairs for index in pair} | set(bad_centers)))
    maximum = max((*errors, *(2 * abs(coordinates[index][component]) for index in centers)), default=0.0)
    return Analysis(len(pairs), len(misaligned_pairs) + len(bad_centers), unmatched, maximum,
                    misaligned_vertices, centers, state['fingerprint'], pairs, correspondence.diagnostics)


def analyze(obj, selected_only=True, tolerance=1e-6, centerline_tolerance=1e-6, axis='X'):
    """Read-only topology correspondence and Basis mirror error report."""
    state = _state(obj)
    if selected_only and not state['selection']:
        raise RefineSymmetryError('Select a region to analyze, or turn off Selected Region Only.')
    return _analyze_state(state, selected_only, tolerance, centerline_tolerance, axis)


def _expected_state(before, positions):
    result = {'mesh': array('f', before['mesh']),
              'active': array('f', before['active']) if before['active'] is not None else None,
              'keys': [], 'basis': array('f', before['basis'])}
    for index, coordinate in positions.items():
        _set_coordinate(result['basis'], index, coordinate)
        _set_coordinate(result['mesh'], index, coordinate)
        if not before['keys'] and result['active'] is not None:
            _set_coordinate(result['active'], index, coordinate)
    for old in before['keys']:
        key = dict(old, stored=array('f', old['stored']), live=array('f', old['live']),
                   layer=array('f', old['layer']) if old['layer'] is not None else None)
        for index, coordinate in positions.items():
            old_basis = _coordinate(before['basis'], index)
            old_key = _coordinate(old['live'], index)
            new_key = tuple(coordinate[axis] + old_key[axis] - old_basis[axis] for axis in range(3))
            _set_coordinate(key['stored'], index, new_key)
            _set_coordinate(key['live'], index, new_key)
            if key['layer'] is not None:
                _set_coordinate(key['layer'], index, new_key)
        result['keys'].append(key)
    return result


def plan(obj, mode='AVERAGE', selected_only=True, tolerance=1e-6, centerline_tolerance=1e-6, axis='X'):
    _preflight(obj, write=True)
    if mode not in {'LEFT_TO_RIGHT', 'RIGHT_TO_LEFT', 'AVERAGE'}:
        raise RefineSymmetryError('Choose Left to Right, Right to Left or Average.')
    component = _axis_index(axis)
    before = _state(obj)
    report = _analyze_state(before, selected_only, tolerance, centerline_tolerance, axis)
    selection = set(before['selection'])
    if selected_only and not selection:
        raise RefineSymmetryError('Select the region to repair first, or turn off Selected Region Only.')
    positions, target_pairs = {}, []
    for negative, positive in report.pairs:
        a, b = _coordinate(before['basis'], negative), _coordinate(before['basis'], positive)
        if _error(a, b, component) == 0:
            target_pairs.append((negative, positive))
            continue
        if mode == 'AVERAGE':
            if selected_only and not {negative, positive}.issubset(selection):
                raise RefineSymmetryError('Average needs both vertices of each affected pair selected. Select both sides of this region.')
            middle = tuple(Vector(tuple((value + other) / 2 for value, other in zip(_mirror(a, component), b))))
            positions[positive] = middle
            positions[negative] = _mirror(middle, component)
        else:
            source, target = (positive, negative) if mode == 'LEFT_TO_RIGHT' else (negative, positive)
            if selected_only and target not in selection:
                continue
            co = _coordinate(before['basis'], source)
            positions[target] = _mirror(co, component)
        target_pairs.append((negative, positive))
    centers = []
    for index in report.centerline:
        co = _coordinate(before['basis'], index)
        centers.append(index)
        if co[component] != 0:
            positions[index] = tuple(0.0 if index == component else value for index, value in enumerate(co))
    if not positions:
        if report.misaligned:
            raise RefineSymmetryError('Select the destination side for this repair mode. No selected vertex can be repaired.')
        if report.unmatched and not report.pairs and not report.centerline:
            raise RefineSymmetryError('No reliable topology pairs were found in this region.')
    expected = _expected_state(before, positions)
    if before['keys'] and expected['active'] is not None:
        active_name = obj.active_shape_key.name
        expected['active'] = array('f', before['active'])
        active_values = next(key['live'] for key in expected['keys'] if key['name'] == active_name)
        for index in positions:
            _set_coordinate(expected['active'], index, _coordinate(active_values, index))
    return Plan(obj, report, positions, mode, selected_only, tolerance, centerline_tolerance, axis,
                tuple(target_pairs), tuple(centers), before, expected)


def _write(obj, records, indices):
    bm = _edit_bmesh(obj)
    for index in indices:
        obj.data.vertices[index].co = _coordinate(records['mesh'], index)
        if bm and records['active'] is not None:
            bm.verts[index].co = _coordinate(records['active'], index)
    if obj.data.shape_keys:
        for record in records['keys']:
            key = obj.data.shape_keys.key_blocks.get(record['name'])
            if key is None:
                raise RefineSymmetryError('A Shape Key disappeared during the operation.')
            layer = bm.verts.layers.shape.get(key.name) if bm else None
            for index in indices:
                key.data[index].co = _coordinate(record['stored'], index)
                if layer and record['layer'] is not None:
                    bm.verts[index][layer] = _coordinate(record['layer'], index)
    if bm:
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    obj.data.update()


def _assert_coordinates(actual, expected):
    for field_name in ('mesh', 'active'):
        if actual[field_name] != expected[field_name]:
            raise RefineSymmetryError(f'{field_name.title()} coordinate verification failed.')
    if len(actual['keys']) != len(expected['keys']):
        raise RefineSymmetryError('The Shape Key list changed during refinement.')
    for old, new in zip(actual['keys'], expected['keys']):
        if old['name'] != new['name'] or any(old[field_name] != new[field_name]
                                           for field_name in ('stored', 'live', 'layer', 'metadata')):
            raise RefineSymmetryError(f'Shape Key "{new["name"]}" coordinate verification failed.')


def _ordinary_lookup(coordinates, pairs, centers, axis='X'):
    component = _axis_index(axis)
    tree = KDTree(len(coordinates))
    for index, coordinate in enumerate(coordinates):
        tree.insert(coordinate, index)
    tree.balance()
    for first, second in (*pairs, *((index, index) for index in centers)):
        for source, target in ((first, second), (second, first)):
            co = coordinates[source]
            hits = sorted(tree.find_range(_mirror(co, component), ORDINARY_MIRROR_TOLERANCE), key=lambda hit: hit[2])
            if (not hits or hits[0][1] != target or hits[0][2] >= ORDINARY_MIRROR_TOLERANCE
                    or len(hits) > 1 and abs(hits[1][2] - hits[0][2]) <= 1e-12):
                raise RefineSymmetryError(f'Ordinary {axis} Mirror has an ambiguous or missing counterpart in the repaired region.')


def validate(obj, repair_plan):
    """Verify the complete coordinate transaction and ordinary spatial lookup."""
    current = _state(obj)
    component = _axis_index(repair_plan.axis)
    if current['invariant'] != repair_plan._before['invariant']:
        raise RefineSymmetryError('Topology, UV, weights or armature relationships changed.')
    _assert_coordinates(current, repair_plan._expected)
    coordinates = tuple(_coordinate(current['basis'], index) for index in range(len(current['basis']) // 3))
    for first, second in repair_plan.target_pairs:
        if _error(coordinates[first], coordinates[second], component) > repair_plan.tolerance:
            raise RefineSymmetryError('A repaired pair failed geometric symmetry validation.')
    if any(coordinates[index][component] != 0 for index in repair_plan.target_centerline):
        raise RefineSymmetryError(f'A repaired centerline vertex is not on {repair_plan.axis}=0.')
    _ordinary_lookup(coordinates, repair_plan.target_pairs, repair_plan.target_centerline, repair_plan.axis)
    report = _analyze_state(current, repair_plan.selected_only, repair_plan.tolerance, repair_plan.centerline_tolerance, repair_plan.axis)
    return Validation(report.matched, report.misaligned, report.unmatched, report.max_error,
                      report.misaligned_vertices, report.centerline, report.fingerprint, report.pairs,
                      True, len(repair_plan.positions), len(repair_plan.target_pairs))


def apply(obj, repair_plan):
    _preflight(obj, write=True)
    if obj is not repair_plan.obj or _state(obj)['fingerprint'] != repair_plan.analysis.fingerprint:
        raise RefineSymmetryError('The mesh, Shape Keys or selection changed. Analyze and preview again.')
    indices = tuple(sorted(repair_plan.positions))
    try:
        _write(obj, repair_plan._expected, indices)
        return validate(obj, repair_plan)
    except Exception as exc:
        try:
            # Restore the complete snapshot, including a coordinate outside
            # the target set if an unexpected callback altered it.
            _write(obj, repair_plan._before, range(len(repair_plan._before['mesh']) // 3))
            restored = _state(obj)
            if restored['invariant'] != repair_plan._before['invariant']:
                raise RefineSymmetryError('Protected data changed during the operation.')
            _assert_coordinates(restored, repair_plan._before)
        except Exception as rollback:
            raise RefineSymmetryError(f'Refinement failed ({exc}); rollback could not be verified ({rollback}).') from rollback
        raise RefineSymmetryError(f'Refinement was rolled back: {exc}') from exc
