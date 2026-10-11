"""Owned shared Dress routing for a native same-character animation packet.

Bind mapping still covers every exported Dress bone. Only a packet whose Dress
locals equal its own reference Rest at every sample may delegate those bones to
the existing manual/physics graph. This module never edits that graph or cache.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


STATIC_LOCAL_TOLERANCE = 2.0e-5


class DressAnimationError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise DressAnimationError(message)


@dataclass(frozen=True)
class DressPlan:
    control_names: frozenset
    constraint_keys: frozenset
    driver_keys: frozenset
    delegated_names: frozenset
    static_local_error: float = 0.0


def _matrix(values):
    _require(isinstance(values, (list, tuple)) and len(values) == 16
             and all(type(value) in (int, float) and math.isfinite(value) for value in values),
             'The Dress packet contains an invalid transform matrix.')
    return [[float(value) for value in values[index:index + 4]] for index in range(0, 16, 4)]


def _inverse(matrix):
    rows = [row[:] + [float(i == j) for j in range(4)] for i, row in enumerate(matrix)]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda index: abs(rows[index][column]))
        _require(abs(rows[pivot][column]) > 1.0e-12, 'The Dress packet has a singular parent transform.')
        rows[column], rows[pivot] = rows[pivot], rows[column]
        factor = rows[column][column]
        rows[column] = [value / factor for value in rows[column]]
        for index in range(4):
            if index == column:
                continue
            factor = rows[index][column]
            rows[index] = [value - factor * other for value, other in zip(rows[index], rows[column])]
    return [row[4:] for row in rows]


def _multiply(first, second):
    return [[sum(first[row][index] * second[index][column] for index in range(4))
             for column in range(4)] for row in range(4)]


def _difference(first, second):
    values = [abs(first[row][column] - second[row][column]) for row in range(4) for column in range(4)]
    return max(values) if all(math.isfinite(value) for value in values) else math.inf


def static_packet_proof(package, names):
    """Pure complete-sample proof of rest-local Dress, including parent motion.

    World-space constancy would incorrectly reject inherited Hips motion. A
    constant offset from Rest is also refused: delegating it would discard that
    authored correction. Unity's float serialization may add bounded noise.
    """
    names = frozenset(names)
    if not names:
        return 0.0
    _require(isinstance(package, dict), 'The full animation packet is required to prove static Dress channels.')
    bones, frames = package.get('bones'), package.get('frames')
    _require(isinstance(bones, list) and isinstance(frames, list) and frames,
             'The Dress animation packet is incomplete.')
    lookup = {}
    for index, bone in enumerate(bones):
        _require(isinstance(bone, dict) and isinstance(bone.get('name'), str)
                 and bone['name'] not in lookup, 'The Dress packet bone inventory is ambiguous.')
        lookup[bone['name']] = index
    _require(names <= set(lookup), 'The animation packet omits a delegated Dress bind bone.')
    local_rests = {}
    for name in names:
        index = lookup[name]
        parent = bones[index].get('parent')
        _require(type(parent) is int and 0 <= parent < len(bones) and parent != index,
                 'A delegated Dress bone has no valid packet parent: ' + name)
        local_rests[name] = (index, parent, _multiply(_inverse(_matrix(bones[parent].get('rest'))),
                                                     _matrix(bones[index].get('rest'))))
    worst = 0.0
    for sample, frame in enumerate(frames):
        poses = frame.get('poses') if isinstance(frame, dict) else None
        _require(isinstance(poses, list) and len(poses) == len(bones),
                 'An animation sample omits the complete Dress skeleton.')
        for name, (index, parent, rest) in local_rests.items():
            _require(isinstance(poses[index], dict) and isinstance(poses[parent], dict),
                     'The Dress animation sample has an invalid pose entry.')
            local = _multiply(_inverse(_matrix(poses[parent].get('matrix'))),
                              _matrix(poses[index].get('matrix')))
            error = _difference(local, rest)
            worst = max(worst, error)
            _require(math.isfinite(error) and error <= STATIC_LOCAL_TOLERANCE,
                     f"'{name}' has independently authored Dress motion at sample {sample}; "
                     'preserve its animation in the separate scene instead of replacing the original Dress controls.')
    return worst


def _identity_driver_curve(curve):
    """Prove the generated AVERAGE driver's identity mapping, not its prefix."""
    _require(curve.array_index == 0 and not curve.mute and not curve.modifiers
             and curve.extrapolation == 'LINEAR',
             'The owned Dress physics influence curve was edited.')
    points = tuple(curve.keyframe_points)
    _require(len(points) in (0, 2), 'The owned Dress physics influence curve has custom keys.')
    for index, point in enumerate(points):
        _require(tuple(point.co) == (float(index), float(index))
                 and point.interpolation in {'LINEAR', 'BEZIER'},
                 'The owned Dress physics influence curve no longer maps its input exactly.')
        if point.interpolation == 'BEZIER':
            _require(all(abs(handle[0] - handle[1]) <= 1.e-6
                         for handle in (point.handle_left, point.handle_right)),
                     'The owned Dress physics influence curve has custom handles.')


def _hierarchy(target, record):
    controls = record['controls']
    waist = controls['waist']
    parents = {waist: record['shared']['anchor'], controls['mid']: waist, controls['hem']: waist}
    connected = {name: False for name in parents}
    for entry in controls['chains']:
        parents.update({entry[level]: controls[level] for level in ('mid', 'hem')})
        connected.update({entry[level]: False for level in ('mid', 'hem')})
    for chain in record['chains']:
        for layer in ('manual', 'phys', 'def'):
            for index, name in enumerate(chain[layer]):
                parents[name] = chain[layer][index - 1] if index else waist
                connected[name] = index > 0
    for name, parent in parents.items():
        bone = target.data.bones[name]
        _require(bone.parent is not None and bone.parent.name == parent
                 and bone.use_inherit_rotation and bone.inherit_scale == 'FULL'
                 and bone.use_local_location and bone.use_connect == connected[name],
                 "The owned Dress hierarchy or inheritance changed at '" + name + "'.")


def _owned_wire(source, target, record, index):
    """Exact neutral wire/Hook contract shipped by skirt_rig + shared migration.

    Migration preserves the effective bone-parent frame rather than equating
    matrix_parent_inverse with shared.space_matrix. Full-strength point Hooks
    cancel that object frame; their exact bind inverse supplies the Rest units.
    """
    import bpy
    from mathutils import Matrix, Vector
    from . import skirt_rig as skirt
    from .skirt_topology import sample_fit

    chain = record['chains'][index]
    wire = bpy.data.objects.get(chain['curve'])
    _require(wire is not None and wire.type == 'CURVE'
             and wire.get(skirt.OWNER_KEY) == record['owner'] and wire.get(skirt.SOURCE_KEY) == source
             and wire.data.users == 1 and wire.data.shape_keys is None
             and wire.animation_data is None and wire.data.animation_data is None and not wire.constraints,
             'The owned Dress wire is missing, reassigned or has custom animation/constraints.')
    _require(wire.parent == target and wire.parent_type == 'BONE'
             and wire.parent_bone == record['shared']['anchor'] and len(wire.data.splines) == 1,
             'The owned Dress wire attachment was edited.')
    spline = wire.data.splines[0]
    _require(spline.type == 'NURBS' and len(spline.points) == 3 and not spline.use_cyclic_u
             and spline.order_u == 3 and spline.use_endpoint_u,
             'The owned Dress neutral wire shape was edited.')
    angle = math.tau * index / record['chain_count']
    expected = [sample_fit(record['fit'], value, angle) for value in (0., .5, 1.)]
    _require(all((Vector(point.co[:3]) - Vector(value)).length <= 1.e-6
                 and abs(point.co[3] - 1.) <= 1.e-7
                 and abs(point.radius - 1.) <= 1.e-7 and abs(point.tilt) <= 1.e-7
                 for point, value in zip(spline.points, expected)),
             'The owned Dress wire Basis was edited; preserve its calibration.')
    names = (record['controls']['waist'], record['controls']['chains'][index]['mid'],
             record['controls']['chains'][index]['hem'])
    _require(len(wire.modifiers) == 3
             and all(modifier.type == 'HOOK' and modifier.object == target and modifier.subtarget == name
                     and modifier.strength == 1. and modifier.falloff_type == 'NONE'
                     and list(modifier.vertex_indices) == [point]
                     and modifier.show_viewport and modifier.show_render
                     for point, (modifier, name) in enumerate(zip(wire.modifiers, names))),
             'The owned Dress wire has edited or disabled exact point Hooks.')
    space = Matrix(record['shared']['space_matrix'])
    _require(all(_difference(modifier.matrix_inverse, target.data.bones[name].matrix_local.inverted() @ space) <= 2.e-6
                 and modifier.center.length <= 1.e-7
                 for modifier, name in zip(wire.modifiers, names)),
             'The owned Dress Hook bind inverse or center was edited; preserve its calibration.')
    return wire


def _manual_graph(source, target, record):
    accepted = set()
    for index, chain in enumerate(record['chains']):
        wire = _owned_wire(source, target, record, index)
        for name in chain['manual']:
            constraints = tuple(target.pose.bones[name].constraints)
            if name != chain['manual'][-1]:
                _require(not constraints, 'An owned Dress manual bone has an extra constraint.')
                continue
            _require(len(constraints) == 1, 'The owned Dress manual spline is missing or has an extra constraint.')
            constraint = constraints[0]
            _require(constraint.name == 'Skirt manual wire' and constraint.type == 'SPLINE_IK'
                     and constraint.target == wire and not constraint.mute and constraint.influence == 1.
                     and constraint.chain_count == record['segment_count']
                     and constraint.use_even_divisions and not constraint.use_curve_radius
                     and not constraint.use_chain_offset and constraint.use_original_scale
                     and constraint.y_scale_mode == 'FIT_CURVE' and constraint.xz_scale_mode == 'NONE',
                     'The owned Dress manual Spline IK settings were edited.')
            accepted.add((name, constraint.name))
    return accepted


def build_plan(target, mapped_names, *, package=None):
    """Read-only, exact shared-source, wire, native constraint and driver proof."""
    import bpy
    from . import skirt_rig as skirt, skirt_original_mode

    names = frozenset(mapped_names)
    raw = target.get(skirt.SHARED_SOURCES_KEY, {})
    _require(hasattr(raw, 'items'), 'The shared Dress source registry is invalid.')
    items = tuple(raw.items())
    owned_bones = {bone.name for bone in target.data.bones if bone.get(skirt.OWNER_KEY)}
    if not items:
        _require(not owned_bones, 'Owned Dress bones have no complete shared source registry.')
        return None
    _require(not skirt._ORIGINAL_SESSION_KEY in target,
             'Return to Controls in Bone Display before importing motion on the original rig.')
    seen, exact_bones, controls, constraints, drivers, delegated = set(), set(), set(), set(), set(), set()
    animation_drivers = tuple(target.animation_data.drivers) if target.animation_data else ()
    worst = 0.0
    try:
        for owner, source in items:
            _require(isinstance(owner, str) and owner and isinstance(source, bpy.types.Object)
                     and source.type == 'MESH' and source not in seen and source.get(skirt.RIG_KEY) == target,
                     'An exact shared Dress source is missing, duplicated or reassigned.')
            seen.add(source)
            record = skirt.read_record(source)
            _require(record is not None and skirt.is_shared(record) and record['owner'] == owner
                     and source.get(skirt.OWNER_KEY) == owner,
                     'The shared Dress source record does not match its exact owner.')
            _require(type(record['chain_count']) is int and 3 <= record['chain_count'] <= 64
                     and record['chain_count'] == len(record['chains']) == len(record['controls']['chains'])
                     and type(record['segment_count']) is int and 1 <= record['segment_count'] <= 32
                     and all(len(chain[layer]) == record['segment_count']
                             for chain in record['chains'] for layer in ('manual', 'phys', 'def')),
                     'The shared Dress chain inventory is invalid.')
            control, deform, mechanism = skirt._bone_collection_layout(record)
            entire = control | deform | mechanism
            _require(not entire & exact_bones, 'A Dress bone belongs to more than one source.')
            exact_bones.update(entire)
            _hierarchy(target, record)
            constraints.update(_manual_graph(source, target, record))
            # Shipped 0.77.23 has no complete read-only physics graph validator.
            # Supporting the native Manual setup must not silently admit cloth
            # graphs or deploy an unrelated pending physics backend rewrite.
            _require(record.get('physics') is None,
                     'This Dress physics setup has no supported complete animation-routing validator. '
                     'Keep the separate animation scene and preserve its physics graph.')
            skirt_original_mode._relations(target, source, record)
            for chain in record['chains']:
                for name in chain['phys']:
                    relations = tuple(target.pose.bones[name].constraints)
                    _require(not relations, 'An inactive Dress physics bone has unrecognized constraints.')
                for name in chain['def']:
                    pb = target.pose.bones[name]
                    _require(len(pb.constraints) == 2, 'A Dress deform bone has extra constraints.')
                    constraints.update((name, constraint.name) for constraint in pb.constraints)
                    manual = pb.constraints['Skirt manual pose']
                    rotation = pb.constraints['Skirt physics delta']
                    _require(not manual.mute and not rotation.mute,
                             'The owned Dress manual or physics deform constraint was disabled.')
                    path = rotation.path_from_id('influence')
                    matches = [curve for curve in animation_drivers if curve.data_path == path]
                    _require(len(matches) == 1, 'The owned Dress physics influence driver is missing or duplicated.')
                    curve = matches[0]
                    _identity_driver_curve(curve)
                    _require(curve.driver.type == 'AVERAGE' and not curve.driver.use_self
                             and len(curve.driver.variables) == 1
                             and curve.driver.variables[0].name == 'physics',
                             'The owned Dress physics influence driver was edited.')
                    drivers.add((path, 0))
            waist = record['controls']['waist']
            holder, _driver_id, _path = skirt.physics_control(source)
            influence = holder.get('physics_influence')
            _require(type(influence) in (int, float) and math.isfinite(influence) and 0. <= influence <= 1.,
                     'The owned Dress physics influence value is invalid.')
            native = deform | {waist}
            _require(all(target.data.bones[name].use_deform for name in native),
                     'An owned Dress deform/bind bone was disabled.')
            covered = names & native
            _require(not covered or covered == native,
                     'The animation packet omits part of this shared Dress bind skeleton.')
            if covered:
                worst = max(worst, static_packet_proof(package, covered))
                delegated.update(covered)
            controls.update(entire - native)
            _require(all(not target.data.bones[name].use_deform for name in entire - native),
                     'An owned Dress control/helper was turned into a bind bone.')
        _require(exact_bones == owned_bones, 'The shared Dress source inventory omits owned bones.')
        return DressPlan(frozenset(controls), frozenset(constraints), frozenset(drivers),
                         frozenset(delegated), worst)
    except DressAnimationError:
        raise
    except ValueError as exc:
        raise DressAnimationError(str(exc)) from exc
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise DressAnimationError('The shared Dress graph record is incomplete; preserve its saved setup.') from exc
