"""Owned cloth cage, closed collision proxies, and sequential skirt baking.

All live deformation uses native Blender data. Animation baking samples the
evaluated result into a separate, parentless deform rig and mesh; it never
replaces the artist's editable setup or animation.
"""

import json
import math
from collections import Counter

import bpy
from mathutils import Matrix, Vector

from . import skirt_rig
from .skirt_topology import sample_fit

LEGACY_BACKEND = "LEGACY_CAGE"
ACTUAL_SURFACE_BACKEND = "ACTUAL_SURFACE_DELTA_V1"
DIRECT_SURFACE_BACKEND = "DIRECT_MAIN_CLOTH_V1"
SURFACE_BACKENDS = frozenset({ACTUAL_SURFACE_BACKEND, DIRECT_SURFACE_BACKEND})


class SkirtPhysicsError(ValueError):
    pass


def _record_backend(record):
    """Read an explicit backend; old records remain legacy without migration."""
    physics = record.get("physics")
    if physics is None:
        return LEGACY_BACKEND
    if not isinstance(physics, dict):
        raise SkirtPhysicsError("The saved Dress physics backend is unreadable.")
    value = physics.get("backend", LEGACY_BACKEND)
    if type(value) is not str or value not in SURFACE_BACKENDS | {LEGACY_BACKEND}:
        raise SkirtPhysicsError("Unknown Dress physics backend; preserve its saved setup.")
    return value


def backend(record):
    return _record_backend(record)


def _surface_module(record=None, *, backend_name=None):
    # Legacy load, drawing and physics operations do not import the new service.
    try:
        selected = backend(record) if record is not None else (backend_name or ACTUAL_SURFACE_BACKEND)
        if selected == DIRECT_SURFACE_BACKEND:
            from . import skirt_surface_direct as skirt_surface
        elif selected == ACTUAL_SURFACE_BACKEND:
            from . import skirt_surface
        else:
            raise SkirtPhysicsError("This Dress has no surface backend.")
    except ImportError as error:
        raise SkirtPhysicsError("The installed Dress surface service is unavailable; restore the validated add-on.") from error
    if getattr(skirt_surface, "BACKEND", None) != selected:
        raise SkirtPhysicsError("The installed Dress surface service has a different backend contract.")
    return skirt_surface


def _record(source):
    record = skirt_rig.read_record(source)
    if not record:
        raise SkirtPhysicsError("Create the skirt setup first.")
    rig = bpy.data.objects.get(record["rig"])
    if rig is None or rig.type != "ARMATURE":
        raise SkirtPhysicsError("The skirt rig is missing. Undo its removal first.")
    return record, rig


def _tag(obj, record):
    obj[skirt_rig.OWNER_KEY] = record["owner"]
    obj[skirt_rig.SOURCE_KEY] = bpy.data.objects[record["source"]]
    if obj.data:
        obj.data[skirt_rig.OWNER_KEY] = record["owner"]
        obj.data[skirt_rig.SOURCE_KEY] = bpy.data.objects[record["source"]]


def _mesh(name, vertices, faces, collection, matrix, record):
    data = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    data.from_pydata(vertices, [], faces)
    data.update()
    obj.matrix_world = matrix
    obj.hide_render = True
    obj.display_type = "WIRE"
    obj.color = (0.12, 0.72, 1.0, 1.0)
    _tag(obj, record)
    return obj


def _bind_single(obj, rig, bone):
    matrix = obj.matrix_world.copy()
    obj.parent = rig
    obj.matrix_world = matrix
    group = obj.vertex_groups.new(name=bone)
    group.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    modifier = obj.modifiers.new("Skirt attachment", "ARMATURE")
    modifier.object = rig


def _ellipsoid(center, axis, radius_x, radius_y, half_length, sides=12, rows=8):
    """Closed convex surface, including explicit end caps (no open pipes)."""
    axis = axis.normalized()
    cross = Vector((0, 0, 1)) if abs(axis.z) < 0.85 else Vector((0, 1, 0))
    u = axis.cross(cross).normalized()
    v = axis.cross(u).normalized()
    vertices = [tuple(center - axis * half_length)]
    for row in range(1, rows):
        latitude = -math.pi / 2 + math.pi * row / rows
        for column in range(sides):
            angle = math.tau * column / sides
            point = (center + axis * (math.sin(latitude) * half_length)
                     + u * (math.cos(latitude) * math.cos(angle) * radius_x)
                     + v * (math.cos(latitude) * math.sin(angle) * radius_y))
            vertices.append(tuple(point))
    top = len(vertices)
    vertices.append(tuple(center + axis * half_length))
    faces = []
    for column in range(sides):
        nxt = (column + 1) % sides
        faces.append((0, 1 + nxt, 1 + column))
        for row in range(rows - 2):
            a, b = 1 + row * sides + column, 1 + row * sides + nxt
            faces.append((a, b, b + sides, a + sides))
        last = 1 + (rows - 2) * sides
        faces.append((last + column, last + nxt, top))
    return vertices, faces


def _bone_name(rig, candidates):
    lookup = {b.name.casefold(): b.name for b in rig.data.bones}
    return next((lookup[c.casefold()] for c in candidates if c.casefold() in lookup), None)


def _collider_attachment(rig, record):
    attach = rig if skirt_rig.is_shared(record) else (rig.parent if rig.parent and rig.parent.type == "ARMATURE" else None)
    if attach:
        pelvis = (record['shared']['anchor'] if skirt_rig.is_shared(record)
                  else rig.parent_bone or _bone_name(attach, ("Hips", "pelvis", "DEF-spine", "hip")))
    else:
        attach, pelvis = rig, record["controls"]["waist"]
    if not pelvis or pelvis not in attach.data.bones:
        attach, pelvis = rig, record["controls"]["waist"]
    return attach, pelvis


def _collider_rest_world(source, rig, record, attach, pelvis):
    fitted = skirt_rig.fit_rest_world(source, record)
    # A legacy rig's matrix_world already follows its posed bone parent. Bring
    # that fitted space back through the parent's deform before skinning the
    # collider to the same bone. Shared rigs store their unposed fitted space.
    if (not skirt_rig.is_shared(record) and attach is not rig
            and rig.parent == attach and rig.parent_type == "BONE"
            and rig.parent_bone == pelvis):
        fitted = (attach.matrix_world @ attach.data.bones[pelvis].matrix_local
                  @ attach.pose.bones[pelvis].matrix.inverted()
                  @ attach.matrix_world.inverted() @ fitted)
    return fitted


def _make_colliders(context, source, rig, record, collection):
    plan = record["fit"]
    scale = max(plan["height_world"], 1.0e-4)
    attach, pelvis = _collider_attachment(rig, record)
    fit_world = _collider_rest_world(source, rig, record, attach, pelvis)
    waist_world = fit_world @ Vector(plan["waist_center"])
    inverse = attach.matrix_world.inverted()
    linear = attach.matrix_world.to_3x3()
    world_to_units = 1.0 / max(linear.col[0].length, linear.col[1].length, linear.col[2].length, 1.0e-8)
    specs = []
    fit = plan["fit_waist"]
    rx = (fit_world.to_3x3() @ Vector(fit["cosine"])).length * 0.78
    ry = (fit_world.to_3x3() @ Vector(fit["sine"])).length * 0.78
    center = inverse @ (waist_world - Vector((0, 0, scale * 0.07)))
    specs.append(("Pelvis", pelvis, center, inverse.to_3x3() @ Vector((0, 0, 1)),
                  rx * world_to_units, ry * world_to_units, scale * 0.26 * world_to_units))
    for side in ("L", "R"):
        bone_name = _bone_name(attach, (f"thigh.{side}", f"thigh_{side}", f"DEF-thigh.{side}",
                                        f"upper_leg.{side}", f"UpperLeg_{side}"))
        if bone_name:
            bone = attach.data.bones[bone_name]
            axis = bone.tail_local - bone.head_local
            radius = plan["waist_radius_world"] * 0.46 * world_to_units
            specs.append((f"Thigh.{side}", bone_name, bone.head_local.lerp(bone.tail_local, 0.46),
                          axis, radius, radius, axis.length * 0.57))
    result = []
    for label, bone, center, axis, rx, ry, length in specs:
        verts, faces = _ellipsoid(center, axis, rx, ry, length)
        obj = _mesh(f"CD Collider {label} · {source.name}", verts, faces, collection,
                    attach.matrix_world.copy(), record)
        result.append(obj)
        _bind_single(obj, attach, bone)
        obj.modifiers.new("Skirt collision", "COLLISION")
        obj.collision.thickness_outer = scale * 0.004
        obj.collision.thickness_inner = scale * 0.001
        obj.collision.damping = 0.2
        obj.color = (1.0, 0.34, 0.08, 1.0)
        obj.hide_set(True)
    return result


def add_physics(context, source, *, backend=None, body=None, capability=None):
    """Keep legacy callers unchanged; surface installation is an explicit transaction."""
    if backend is not None and (type(backend) is not str or backend not in SURFACE_BACKENDS | {LEGACY_BACKEND}):
        raise SkirtPhysicsError("Unknown requested Dress physics backend.")
    skirt_rig._require_controls_for_setup(source)
    record, rig = _record(source)
    current_backend = _record_backend(record)
    if backend in SURFACE_BACKENDS:
        # The service owns the entire installation/upgrade transaction, including
        # any legacy starting graph. Do not commit a partial legacy upgrade here.
        intent = {} if capability is None else {"capability": capability}
        return _surface_module(backend_name=backend).install(context, source, body=body, **intent)
    if backend == LEGACY_BACKEND and current_backend in SURFACE_BACKENDS:
        raise SkirtPhysicsError("Remove or explicitly rebuild the Dress surface setup before changing its backend.")
    if record.get("physics"):
        validate_physics(source)
        return record
    original_record = json.loads(json.dumps(record))
    collection = bpy.data.collections.new(f"CD Skirt Collision · {source.name}")
    context.scene.collection.children.link(collection)
    collection[skirt_rig.OWNER_KEY] = record["owner"]
    objects, constraints = [], []
    influence, _driver_id, _path = skirt_rig.physics_control(source)
    old_influence = influence.get("physics_influence", 0.0)
    try:
        plan = record["fit"]
        sides = record["chain_count"] * 4
        rows = record["segment_count"] * 3
        verts = [sample_fit(plan, row / rows, math.tau * col / sides)
                 for row in range(rows + 1) for col in range(sides)]
        faces = [(row * sides + col, row * sides + (col + 1) % sides,
                  (row + 1) * sides + (col + 1) % sides, (row + 1) * sides + col)
                 for row in range(rows) for col in range(sides)]
        # Use a sibling collection for the cloth; collision filtering includes only closed colliders.
        destination = rig.users_collection[0] if rig.users_collection else context.scene.collection
        proxy = _mesh(f"CD Cloth Cage · {source.name}", verts, faces, destination,
                      skirt_rig.fit_rest_world(source, record), record)
        objects.append(proxy)
        _bind_single(proxy, rig, record["controls"]["waist"])
        pin = proxy.vertex_groups.new(name="CD Waist Pin")
        pin.add(list(range(sides)), 1.0, "REPLACE")
        pin.add(list(range(sides, sides * 2)), 0.35, "REPLACE")
        cloth = proxy.modifiers.new("Skirt physics", "CLOTH")
        settings = cloth.settings
        settings.vertex_group_mass = pin.name
        settings.quality = 8
        settings.mass = 0.15
        settings.tension_stiffness = 25.0
        settings.compression_stiffness = 25.0
        settings.shear_stiffness = 10.0
        settings.bending_stiffness = 0.8
        settings.tension_damping = 5.0
        settings.compression_damping = 5.0
        settings.shear_damping = 5.0
        settings.bending_damping = 1.0
        settings.air_damping = 3.0
        settings.pin_stiffness = 1.0
        settings.use_dynamic_mesh = False
        collision = cloth.collision_settings
        collision.use_collision = True
        collision.collection = collection
        collision.distance_min = plan["height_world"] * 0.008
        collision.collision_quality = 4
        # Neighboring samples already share edges. Self-collision is left off
        # for this compact control cage; it is not a final garment solver.
        collision.use_self_collision = False
        cloth.point_cache.frame_start = context.scene.frame_start
        cloth.point_cache.frame_end = context.scene.frame_end
        for chain_index, chain in enumerate(record["chains"]):
            for segment, bone_name in enumerate(chain["phys"]):
                name = f"CD Sample {chain_index:02d}.{segment:02d}"
                group = proxy.vertex_groups.new(name=name)
                group.add([(segment + 1) * 3 * sides + chain_index * 4], 1.0, "REPLACE")
                bone = rig.pose.bones[bone_name]
                constraint = bone.constraints.new("DAMPED_TRACK")
                constraints.append((bone, constraint))
                constraint.name = "CD Physics Aim"
                constraint.target = proxy
                constraint.subtarget = name
                constraint.track_axis = "TRACK_Y"
        objects.extend(_make_colliders(context, source, rig, record, collection))
        record["physics"] = {"proxy": proxy.name, "colliders": [o.name for o in objects[1:]],
                             "collection": collection.name, "rows": rows + 1, "columns": sides,
                             "baked_range": None,
                             "pin_weights": [1.0 if index < sides else 0.35 if index < sides * 2 else 0.0
                                             for index in range(len(verts))]}
        record["owned_objects"].extend(o.name for o in objects)
        record.setdefault("owned_collections", []).append(collection.name)
        influence["physics_influence"] = 1.0
        influence.id_properties_ui("physics_influence").update(min=0.0, max=1.0, default=1.0,
                                                         description="Add simulated cloth sway to manual skirt shaping")
        proxy.hide_set(True)
        _physics_graph(source, rig, record)
        skirt_rig.write_record(source, record)
        context.view_layer.update()
        return record
    except Exception as error:
        for bone, constraint in reversed(constraints):
            bone.constraints.remove(constraint)
        # Include partially created collider objects if a modifier assignment failed.
        for obj in list(bpy.data.objects):
            if obj in objects or (obj.get(skirt_rig.OWNER_KEY) == record["owner"] and collection in obj.users_collection):
                data = obj.data
                bpy.data.objects.remove(obj, do_unlink=True)
                if data and data.users == 0:
                    bpy.data.meshes.remove(data)
        if collection.name in bpy.data.collections:
            bpy.data.collections.remove(collection)
        influence["physics_influence"] = old_influence
        skirt_rig.write_record(source, original_record)
        if isinstance(error, (SkirtPhysicsError, ValueError)):
            raise
        raise SkirtPhysicsError(f"Skirt physics was rolled back: {error}") from error


def _require(condition, message):
    if not condition:
        raise SkirtPhysicsError(message)


def _owned_mesh(obj, source, record):
    _require(obj is not None and obj.type == "MESH" and obj.data is not None,
             "An owned Dress physics mesh is missing.")
    _require(obj.get(skirt_rig.OWNER_KEY) == record["owner"]
             and obj.get(skirt_rig.SOURCE_KEY) == source
             and obj.data.get(skirt_rig.OWNER_KEY) == record["owner"]
             and obj.data.get(skirt_rig.SOURCE_KEY, source) == source
             and obj.name in record["owned_objects"],
             "Restore the Dress physics mesh ownership before continuing.")
    _require(not (obj.library or obj.override_library or obj.data.library)
             and obj.data.users == 1 and obj.data.shape_keys is None,
             "Keep the Dress physics meshes local, unshared and without Shape Keys.")
    _require(not obj.constraints and obj.animation_data is None and obj.data.animation_data is None,
             "Preserve custom Dress physics animation or constraints before continuing.")
    _require(all(math.isfinite(value) for vertex in obj.data.vertices for value in vertex.co),
             "A Dress physics mesh contains invalid coordinates.")


def _weights(obj):
    groups = {group.index: group.name for group in obj.vertex_groups}
    result = {name: {} for name in groups.values()}
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            _require(assignment.group in groups and math.isfinite(assignment.weight)
                     and 0.0 <= assignment.weight <= 1.0,
                     "A Dress physics vertex group has invalid weights.")
            # Native groups may contain explicit zero assignments after Tuning.
            if assignment.weight:
                result[groups[assignment.group]][vertex.index] = assignment.weight
    return result


def _same_weights(actual, expected):
    return (actual.keys() == expected.keys()
            and all(abs(actual[index] - expected[index]) <= 1.0e-6 for index in expected))


def _binding(obj, rig, bone, modifier_types):
    modifiers = list(obj.modifiers)
    _require([modifier.type for modifier in modifiers] == modifier_types
             and obj.parent == rig and obj.parent_type == "OBJECT" and not obj.parent_bone,
             "Restore the generated Dress physics modifier order and attachment.")
    armature = modifiers[0]
    _require(armature.object == rig and armature.use_vertex_groups
             and not armature.use_bone_envelopes and not armature.vertex_group
             and not armature.use_multi_modifier
             and all(modifier.show_viewport and modifier.show_render for modifier in modifiers)
             and bone in rig.data.bones,
             "Restore the generated Dress physics bone binding.")
    return modifiers


def _relative_frame(obj, expected):
    actual = obj.matrix_parent_inverse @ obj.matrix_basis
    _require(all(math.isfinite(value) for row in actual for value in row)
             and max(abs(actual[row][column] - expected[row][column])
                     for row in range(4) for column in range(4)) <= 1.0e-5,
             'Restore the generated Dress physics object attachment space.')


def _face_edges(faces):
    return Counter(tuple(sorted((first, second))) for face in faces
                   for first, second in zip(face, face[1:] + face[:1]))


def _closed_collider(obj):
    faces = [tuple(face.vertices) for face in obj.data.polygons]
    _require(len(obj.data.vertices) >= 4 and faces
             and all(len(face) >= 3 and len(set(face)) == len(face) for face in faces),
             "The Dress collision mesh is incomplete.")
    uses = _face_edges(faces)
    _require(all(count == 2 for count in uses.values())
             and set(uses) == {tuple(sorted(edge.vertices)) for edge in obj.data.edges},
             "Close the Dress collision mesh before continuing.")
    adjacent = {index: set() for index in range(len(obj.data.vertices))}
    for first, second in uses:
        adjacent[first].add(second)
        adjacent[second].add(first)
    visited, pending = set(), [0]
    while pending:
        index = pending.pop()
        if index not in visited:
            visited.add(index)
            pending.extend(adjacent[index] - visited)
    _require(len(visited) == len(adjacent), "Keep each Dress collider a connected closed mesh.")
    volume = 0.0
    for face in faces:
        origin = obj.data.vertices[face[0]].co
        for first, second in zip(face[1:-1], face[2:]):
            volume += origin.dot(obj.data.vertices[first].co.cross(obj.data.vertices[second].co)) / 6.0
    _require(math.isfinite(volume) and volume > 1.0e-14,
             "The Dress collider has zero volume or inverted winding.")


def _action_paths(action):
    if action is None:
        return
    # Native layered Actions in supported Blender versions; keep old Actions
    # readable for a saved setup without depending on action.fcurves existing.
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        yield curve.data_path
    else:
        for curve in action.fcurves:
            yield curve.data_path


def _physics_bone_animation(rig, record):
    animation = rig.animation_data
    if animation is None:
        return
    prefixes = tuple(rig.pose.bones[name].path_from_id() for chain in record['chains'] for name in chain['phys'])
    _require(not any(curve.data_path.startswith(prefixes) for curve in animation.drivers),
             "Preserve custom drivers on the Dress physics bones before continuing.")
    actions = [animation.action]
    pending = [strip for track in animation.nla_tracks for strip in track.strips]
    while pending:
        strip = pending.pop()
        actions.append(strip.action)
        pending.extend(getattr(strip, 'strips', ()))
    _require(not any(path.startswith(prefixes) for action in actions for path in _action_paths(action)),
             "Preserve custom animation on the Dress physics bones before continuing.")


def _deform_targets(source, rig, record):
    from . import skirt_original_mode
    corrections = skirt_original_mode._corrections(source, record)
    rotations, names = [], []
    for chain in record['chains']:
        for name, manual, physics in zip(chain['def'], chain['manual'], chain['phys']):
            bone = rig.pose.bones[name]
            copy = bone.constraints.get('Skirt manual pose')
            rotation = bone.constraints.get('Skirt physics delta')
            _require(copy is not None and rotation is not None and copy.type == 'COPY_TRANSFORMS'
                     and rotation.type == 'COPY_ROTATION' and copy.target == rig and rotation.target == rig
                     and copy.subtarget == manual and rotation.subtarget == physics
                     and copy.owner_space == copy.target_space == rotation.owner_space == rotation.target_space == 'LOCAL'
                     and copy.mix_mode == ('BEFORE_FULL' if name in corrections['bones'] else 'REPLACE')
                     and not copy.remove_target_shear and abs(copy.influence - 1.0) <= 1e-7
                     and rotation.mix_mode == 'BEFORE' and rotation.euler_order == 'AUTO'
                     and all((rotation.use_x, rotation.use_y, rotation.use_z))
                     and not any((rotation.invert_x, rotation.invert_y, rotation.invert_z))
                     and list(bone.constraints).index(copy) < list(bone.constraints).index(rotation)
                     and not any(not item.mute for item in bone.constraints if item not in (copy, rotation)),
                     'Restore the generated Dress manual and physics deform targets.')
            names.append(name)
            rotations.append(rotation)
    # Do not use Original's full posing preflight here: keyed waist/Body motion
    # is a legitimate input to simulation and Bake must keep it working.
    skirt_original_mode._constraint_animation(rig, source, names, rotations)


def _verify_physics_graph(source, rig, record):
    """Read-only proof of the complete generated physics graph, including v1 saves.

    Physical coefficients and closed collider vertex fitting remain editable.
    Tuning may atomically replace pin weights and their saved expectation; it
    must validate the old graph before making either change.
    """
    physics = record.get("physics")
    _require(isinstance(physics, dict), "Add Dress physics before continuing.")
    _require(isinstance(record['chain_count'], int) and not isinstance(record['chain_count'], bool)
             and 3 <= record['chain_count'] <= 64
             and record['chain_count'] == len(record['chains'])
             and isinstance(record['segment_count'], int) and not isinstance(record['segment_count'], bool)
             and 1 <= record['segment_count'] <= 32
             and all(len(chain[layer]) == record['segment_count']
                     for chain in record['chains'] for layer in ('manual', 'phys', 'def')),
             'The saved Dress physics chain dimensions are invalid.')
    sides, rows = record['chain_count'] * 4, record['segment_count'] * 3 + 1
    _require(physics.get('columns') == sides and physics.get('rows') == rows,
             "Restore the saved Dress physics cage dimensions.")
    proxy = bpy.data.objects.get(physics.get("proxy", ""))
    _owned_mesh(proxy, source, record)
    waist = record['controls']['waist']
    _attachment, cloth = _binding(proxy, rig, waist, ['ARMATURE', 'CLOTH'])
    _relative_frame(proxy, Matrix(record['shared']['space_matrix'])
                    if skirt_rig.is_shared(record) else Matrix.Identity(4))
    expected_faces = [(row * sides + col, row * sides + (col + 1) % sides,
                       (row + 1) * sides + (col + 1) % sides, (row + 1) * sides + col)
                      for row in range(rows - 1) for col in range(sides)]
    _require(len(proxy.data.vertices) == sides * rows
             and [tuple(face.vertices) for face in proxy.data.polygons] == expected_faces
             and len(proxy.data.edges) == len(_face_edges(expected_faces))
             and {tuple(sorted(edge.vertices)) for edge in proxy.data.edges} == set(_face_edges(expected_faces)),
             "The Dress physics cage topology or vertex indices were edited.")
    _require(all((vertex.co - Vector(sample_fit(record['fit'], vertex.index // sides / (rows - 1),
                                               math.tau * (vertex.index % sides) / sides))).length <= 1.0e-5
                 for vertex in proxy.data.vertices),
             "Preserve the edited Dress physics cage before continuing.")
    pin = physics.get('pin_weights', [1.0 if index < sides else 0.35 if index < sides * 2 else 0.0
                                      for index in range(sides * rows)])
    _require(isinstance(pin, list) and len(pin) == sides * rows
             and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value) and 0.0 <= value <= 1.0 for value in pin)
             and all(abs(value - 1.0) <= 1.0e-6 for value in pin[:sides]),
             "The saved Dress waist pin weights are invalid.")
    expected = {waist: {index: 1.0 for index in range(sides * rows)},
                'CD Waist Pin': {index: value for index, value in enumerate(pin) if value}}
    _require(cloth.settings.vertex_group_mass == 'CD Waist Pin',
             "Restore the Dress Cloth waist pin group.")
    for chain_index, chain in enumerate(record['chains']):
        for segment, name in enumerate(chain['phys']):
            group = f'CD Sample {chain_index:02d}.{segment:02d}'
            expected[group] = {(segment + 1) * 3 * sides + chain_index * 4: 1.0}
            bone = rig.pose.bones[name]
            constraints = list(bone.constraints)
            _require(len(constraints) == 1, "Preserve custom Dress physics bone constraints before continuing.")
            aim = constraints[0]
            _require(aim.name == 'CD Physics Aim' and aim.type == 'DAMPED_TRACK'
                     and aim.target == proxy and aim.subtarget == group and aim.track_axis == 'TRACK_Y'
                     and aim.owner_space == 'WORLD' and aim.target_space == 'WORLD'
                     and not aim.mute and abs(aim.influence - 1.0) <= 1.0e-6
                     and abs(aim.head_tail) <= 1.0e-6,
                     "Restore the generated Dress physics sample targets.")
    actual = _weights(proxy)
    _require(actual.keys() == expected.keys()
             and all(_same_weights(actual[name], values) for name, values in expected.items()),
             "Restore the Dress physics cage binding, pin and sample weights.")
    _physics_bone_animation(rig, record)
    _deform_targets(source, rig, record)
    names = physics.get('colliders')
    _require(isinstance(names, list) and len(names) == len(set(names)) and names,
             "Restore the saved Dress collision objects.")
    collection = bpy.data.collections.get(physics.get('collection', ''))
    _require(collection is not None and collection.get(skirt_rig.OWNER_KEY) == record['owner']
             and collection.name in record.get('owned_collections', ())
             and not (collection.library or collection.override_library) and not collection.children
             and set(collection.objects.keys()) == set(names)
             and proxy.name not in collection.objects and cloth.collision_settings.collection == collection,
             "Restore the owned Dress collision collection and exact members.")
    attach, pelvis = _collider_attachment(rig, record)
    bones = [pelvis]
    bones.extend(name for side in ('L', 'R') if (name := _bone_name(
        attach, (f'thigh.{side}', f'thigh_{side}', f'DEF-thigh.{side}', f'upper_leg.{side}', f'UpperLeg_{side}'))))
    _require(len(names) == len(bones), "Restore the complete Dress pelvis and leg collision set.")
    for name, bone in zip(names, bones):
        collider = bpy.data.objects.get(name)
        _owned_mesh(collider, source, record)
        _binding(collider, attach, bone, ['ARMATURE', 'COLLISION'])
        _relative_frame(collider, Matrix.Identity(4))
        _require(_weights(collider) == {bone: {index: 1.0 for index in range(len(collider.data.vertices))}},
                 "Restore the Dress collider's complete bone weights.")
        _closed_collider(collider)
    cache = cloth.point_cache
    _require(not cache.use_external and not cache.is_baking and cache.frame_start <= cache.frame_end,
             "Finish the native bake or restore the local Dress cache before continuing.")
    return proxy, cloth


def _physics_graph(source, rig, record):
    try:
        if backend(record) in SURFACE_BACKENDS:
            return _surface_module(record).validate(source, rig, record)
        return _verify_physics_graph(source, rig, record)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise SkirtPhysicsError('The saved Dress physics graph is incomplete; restore its saved file.') from error


def validate_physics(source):
    """Return (record, rig, proxy, cloth) without changing native or saved data."""
    record, rig = _record(source)
    proxy, cloth = _physics_graph(source, rig, record)
    return record, rig, proxy, cloth


def _cloth(record):
    """Compatibility helper; callers receive the same complete read-only proof."""
    source, rig = bpy.data.objects.get(record['source']), bpy.data.objects.get(record['rig'])
    _require(source is not None and rig is not None, "The owned Dress physics source or rig is missing.")
    return _physics_graph(source, rig, record)


def clear_cache(context, source):
    record, _, proxy, cloth = validate_physics(source)
    with context.temp_override(object=proxy, active_object=proxy, point_cache=cloth.point_cache):
        if cloth.point_cache.is_baked:
            bpy.ops.ptcache.free_bake()
    record["physics"]["baked_range"] = None
    skirt_rig.write_record(source, record)


def reset_simulation(context, source):
    """Discard the owned simulation state and return to its native start frame.

    Cache-Step's native RNA update marks even an unbaked cache outdated. At the
    start frame Cloth then clears that cache and initializes zero-velocity Rest
    simulation state. The actual cache step and artist Actions are unchanged.
    """
    skirt_rig._require_controls_for_setup(source)
    record, _, proxy, cloth = validate_physics(source)
    cache = cloth.point_cache
    start = cache.frame_start
    saved_frame, saved_subframe = context.scene.frame_current, context.scene.frame_subframe
    direct = backend(record) == DIRECT_SURFACE_BACKEND
    service = _surface_module(record) if direct else None
    saved_mode = service.capture_mode(source, record) if direct else None
    try:
        with context.temp_override(object=proxy, active_object=proxy, point_cache=cache):
            if cache.is_baked:
                result = bpy.ops.ptcache.free_bake()
                _require('FINISHED' in result and not cache.is_baked,
                         'Blender could not release the owned Dress cache.')
        cache.frame_step = cache.frame_step
        context.scene.frame_set(start - 1)
        context.scene.frame_set(start)
        if direct:
            service.reset_completed(context, source, record)
        context.view_layer.update()
        _require(not cache.is_baked, 'The Dress cache remained baked after Reset.')
        record['physics']['baked_range'] = None
        skirt_rig.write_record(source, record)
        return {'start': start, 'is_baked': False}
    except Exception as error:
        restore_errors = []
        if direct:
            try:
                service.restore_mode(source, record, saved_mode)
            except Exception as restore_error:
                restore_errors.append(str(restore_error))
        try:
            context.scene.frame_set(saved_frame, subframe=saved_subframe)
        except Exception as restore_error:
            restore_errors.append(str(restore_error))
        if restore_errors:
            raise SkirtPhysicsError(
                'Dress Reset failed; preview/frame restoration also failed: '
                + '; '.join(restore_errors)) from error
        raise


def _set_object_mode(context):
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _export_objects(context, source, rig, record):
    keys = source.data.shape_keys
    if keys and keys.animation_data and (keys.animation_data.action or keys.animation_data.drivers or keys.animation_data.nla_tracks):
        raise SkirtPhysicsError("This skirt has animated Shape Keys. Bake their animation separately before creating a bone-only copy.")
    _set_object_mode(context)
    collection = bpy.data.collections.new(f"Baked Skirt · {source.name}")
    context.scene.collection.children.link(collection)
    data = rig.data.copy()
    output = bpy.data.objects.new(f"Baked Skirt Rig · {source.name}", data)
    collection.objects.link(output)
    output.matrix_world = rig.matrix_world.copy()
    output.rotation_mode = "QUATERNION"
    output.show_in_front = True
    output["character_designer_baked_source"] = source.name
    keep = {record["controls"]["waist"]}
    keep.update(name for chain in record["chains"] for name in chain["def"])
    for item in context.selected_objects:
        item.select_set(False)
    output.select_set(True)
    context.view_layer.objects.active = output
    bpy.ops.object.mode_set(mode="EDIT")
    for bone in list(data.edit_bones):
        if bone.name not in keep:
            data.edit_bones.remove(bone)
    bpy.ops.object.mode_set(mode="OBJECT")
    for bone in output.pose.bones:
        # A new armature Object creates fresh PoseBones even when its data was
        # copied. Preserve artist metadata explicitly on the independent bake.
        original = rig.pose.bones[bone.name]
        for key, value in original.items():
            if key in {skirt_rig.OWNER_KEY, skirt_rig.SOURCE_KEY}:
                continue
            bone[key] = value.to_dict() if hasattr(value, 'to_dict') else value
            try:
                ui = original.id_properties_ui(key).as_dict()
            except TypeError:
                # Nested ID-property groups have no UI-data manager.
                continue
            if ui:
                bone.id_properties_ui(key).update(**ui)
        bone.rotation_mode = "QUATERNION"
        bone.custom_shape = None
        bone.bone.hide = False
        for constraint in list(bone.constraints):
            bone.constraints.remove(constraint)
        # A baked output is an independent artist armature. It must not claim
        # the live source's managed Dress subset after the editable rig is gone.
        for item in (bone, bone.bone):
            for key in (skirt_rig.OWNER_KEY, skirt_rig.SOURCE_KEY):
                if key in item:
                    del item[key]
    for bone_collection in data.collections_all:
        bone_collection.is_visible = True
        for key in (skirt_rig.OWNER_KEY, skirt_rig.SOURCE_KEY):
            if key in bone_collection:
                del bone_collection[key]
    mesh = source.copy()
    mesh.data = source.data.copy()
    collection.objects.link(mesh)
    mesh.name = f"Baked Skirt · {source.name}"
    mesh.parent = output
    # The live source is a child of the skirt rig; preserve its exact rig-local relationship.
    mesh.matrix_parent_inverse = Matrix.Identity(4)
    mesh.matrix_basis = rig.matrix_world.inverted() @ source.matrix_world
    mesh.animation_data_clear()
    for key in (skirt_rig.OWNER_KEY, skirt_rig.SOURCE_KEY, skirt_rig.RECORD_KEY,
                skirt_rig.RIG_KEY, skirt_rig.PARENT_KEY):
        if key in mesh:
            del mesh[key]
    for modifier in mesh.modifiers:
        if modifier.type == "ARMATURE" and modifier.object == rig:
            modifier.object = output
    output["character_designer_baked_mesh"] = mesh.name
    return collection, output, mesh


def _remove_export(collection):
    for obj in list(collection.objects):
        data = obj.data
        animation = obj.animation_data
        action = animation.action if animation else None
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            (bpy.data.armatures if isinstance(data, bpy.types.Armature) else bpy.data.meshes).remove(data)
        if action and action.users == 0:
            bpy.data.actions.remove(action)
    bpy.data.collections.remove(collection)


def _finite_matrix(matrix):
    return all(math.isfinite(value) for row in matrix for value in row)


def bake_steps(context, source, start, end, kind="SIMULATION"):
    """Cancelable generator: always walk every frame; never sample skipped caches."""
    skirt_rig._require_controls_for_setup(source)
    if kind not in {"SIMULATION", "ANIMATION"}:
        raise SkirtPhysicsError("Unknown skirt bake mode.")
    start, end = int(start), int(end)
    if start > end:
        raise SkirtPhysicsError("Bake end must not be before start.")
    if end - start > 20000:
        raise SkirtPhysicsError("Bake at most 20,001 frames in one operation.")
    record, rig = _record(source)
    proxy, cloth = _cloth(record)
    if backend(record) in SURFACE_BACKENDS and kind == "ANIMATION":
        raise SkirtPhysicsError("The Dress surface uses a per-vertex Cloth result. Bake its simulation; a bone-only animation copy cannot preserve that result.")
    if backend(record) == DIRECT_SURFACE_BACKEND:
        _surface_module(record).require_bake_ready(source, record)
    saved_frame = context.scene.frame_current
    saved_subframe = context.scene.frame_subframe
    saved_active = context.view_layer.objects.active
    saved_selected = list(context.selected_objects)
    saved_mode = saved_active.mode if saved_active else "OBJECT"
    export = None
    finished = False
    previous_quaternions = {}
    old_frame_step = cloth.point_cache.frame_step
    total = end - start + 1
    try:
        _set_object_mode(context)
        clear_cache(context, source)
        cache = cloth.point_cache
        cache.frame_start = start
        cache.frame_end = end
        cache.frame_step = 1
        # Force invalidation even when rebaking an identical interval.
        context.scene.frame_set(start - 1)
        context.scene.frame_set(start)
        if kind == "ANIMATION":
            export = _export_objects(context, source, rig, record)
        for frame in range(start, end + 1):
            context.scene.frame_set(frame)
            graph = context.evaluated_depsgraph_get()
            evaluated_proxy = proxy.evaluated_get(graph)
            evaluated_mesh = evaluated_proxy.to_mesh()
            try:
                if any(not math.isfinite(value) for vertex in evaluated_mesh.vertices for value in vertex.co):
                    raise SkirtPhysicsError(f"Cloth became invalid at frame {frame}; bake canceled.")
            finally:
                evaluated_proxy.to_mesh_clear()
            evaluated = rig.evaluated_get(graph)
            if export:
                output = export[1]
                output.matrix_world = evaluated.matrix_world
                if not _finite_matrix(output.matrix_world):
                    raise SkirtPhysicsError(f"Rig transform became invalid at frame {frame}.")
                if "object" in previous_quaternions:
                    output.rotation_quaternion.make_compatible(previous_quaternions["object"])
                previous_quaternions["object"] = output.rotation_quaternion.copy()
                for path in ("location", "rotation_quaternion", "scale"):
                    output.keyframe_insert(path, frame=frame, group="Character motion")
                # Convert against the sampled parent explicitly, so Blender
                # does not need to reevaluate the whole character per bone.
                bones = sorted(output.pose.bones, key=lambda bone: len(bone.parent_recursive))
                for bone in bones:
                    matrix = evaluated.pose.bones[bone.name].matrix.copy()
                    if not _finite_matrix(matrix):
                        raise SkirtPhysicsError(f"{bone.name} became invalid at frame {frame}.")
                    conversion = {}
                    if bone.parent:
                        conversion = {"parent_matrix": evaluated.pose.bones[bone.parent.name].matrix,
                                      "parent_matrix_local": bone.parent.bone.matrix_local}
                    bone.matrix_basis = bone.bone.convert_local_to_pose(
                        matrix, bone.bone.matrix_local, invert=True, **conversion,
                    )
                    if bone.name in previous_quaternions:
                        bone.rotation_quaternion.make_compatible(previous_quaternions[bone.name])
                    previous_quaternions[bone.name] = bone.rotation_quaternion.copy()
                    for path in ("location", "rotation_quaternion", "scale"):
                        bone.keyframe_insert(path, frame=frame, group=bone.name)
                context.view_layer.update()
            yield frame - start + 1, total, f"Frame {frame} / {end}"
        with context.temp_override(object=proxy, active_object=proxy, point_cache=cloth.point_cache):
            result = bpy.ops.ptcache.bake_from_cache()
            if "FINISHED" not in result or not cloth.point_cache.is_baked:
                raise SkirtPhysicsError("Blender did not seal the sequential cloth cache.")
        record["physics"]["baked_range"] = [start, end]
        skirt_rig.write_record(source, record)
        result = {"start": start, "end": end, "frames": total, "kind": kind}
        if backend(record) in SURFACE_BACKENDS:
            result.update(backend=backend(record), surface=proxy.name)
        if export:
            output = export[1]
            if output.animation_data and output.animation_data.action:
                output.animation_data.action.name = f"Skirt · {source.name} · {start}-{end}"
                action = output.animation_data.action
                # Blender 4.4+ uses layered actions; keys are already per-frame,
                # LINEAR also prevents Bezier overshoot between sampled frames.
                for layer in action.layers:
                    for strip in layer.strips:
                        for bag in strip.channelbags:
                            for curve in bag.fcurves:
                                for key in curve.keyframe_points:
                                    key.interpolation = "LINEAR"
            result.update(rig=output.name, mesh=export[2].name, collection=export[0].name)
            output["character_designer_baked_range"] = [start, end]
            # Keep the comparison copy from drawing over the live source.
            export[0].hide_viewport = True
            export[0].hide_render = True
        finished = True
        return result
    finally:
        if export and not finished:
            _remove_export(export[0])
        if not finished:
            record["physics"]["baked_range"] = None
            skirt_rig.write_record(source, record)
        if not cloth.point_cache.is_baked:
            cloth.point_cache.frame_step = old_frame_step
        context.scene.frame_set(saved_frame, subframe=saved_subframe)
        _set_object_mode(context)
        for item in context.selected_objects:
            item.select_set(False)
        for item in saved_selected:
            if item.name in context.view_layer.objects:
                item.select_set(True)
        if saved_active and saved_active.name in context.view_layer.objects:
            context.view_layer.objects.active = saved_active
            if saved_mode != "OBJECT":
                bpy.ops.object.mode_set(mode=saved_mode)


def _consume(iterator):
    while True:
        try:
            next(iterator)
        except StopIteration as done:
            return done.value


def bake_simulation(context, source, start, end):
    return _consume(bake_steps(context, source, start, end, "SIMULATION"))


def bake_animation(context, source, start, end):
    return _consume(bake_steps(context, source, start, end, "ANIMATION"))
