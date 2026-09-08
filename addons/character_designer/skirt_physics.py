"""Owned cloth cage, closed collision proxies, and sequential skirt baking.

All live deformation uses native Blender data. Animation baking samples the
evaluated result into a separate, parentless deform rig and mesh; it never
replaces the artist's editable setup or animation.
"""

import json
import math

import bpy
from mathutils import Matrix, Vector

from . import skirt_rig
from .skirt_topology import sample_fit


class SkirtPhysicsError(ValueError):
    pass


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


def _make_colliders(context, source, rig, record, collection):
    plan = record["fit"]
    scale = max(plan["height_world"], 1.0e-4)
    waist_world = rig.matrix_world @ Vector(plan["waist_center"])
    attach = rig.parent if rig.parent and rig.parent.type == "ARMATURE" else None
    # Resolve source character from rig attachment, without relying on file-specific names.
    if attach:
        pelvis = rig.parent_bone or _bone_name(attach, ("Hips", "pelvis", "DEF-spine", "hip"))
    else:
        attach, pelvis = rig, record["controls"]["waist"]
    if not pelvis or pelvis not in attach.data.bones:
        attach, pelvis = rig, record["controls"]["waist"]
    inverse = attach.matrix_world.inverted()
    linear = attach.matrix_world.to_3x3()
    world_to_units = 1.0 / max(linear.col[0].length, linear.col[1].length, linear.col[2].length, 1.0e-8)
    specs = []
    fit = plan["fit_waist"]
    rx = (rig.matrix_world.to_3x3() @ Vector(fit["cosine"])).length * 0.78
    ry = (rig.matrix_world.to_3x3() @ Vector(fit["sine"])).length * 0.78
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


def add_physics(context, source):
    record, rig = _record(source)
    if record.get("physics"):
        _cloth(record)
        return record
    original_record = json.loads(json.dumps(record))
    collection = bpy.data.collections.new(f"CD Skirt Collision · {source.name}")
    context.scene.collection.children.link(collection)
    collection[skirt_rig.OWNER_KEY] = record["owner"]
    objects, constraints = [], []
    old_influence = rig.get("physics_influence", 0.0)
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
                      rig.matrix_world.copy(), record)
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
                             "baked_range": None}
        record["owned_objects"].extend(o.name for o in objects)
        record.setdefault("owned_collections", []).append(collection.name)
        rig["physics_influence"] = 1.0
        rig.id_properties_ui("physics_influence").update(min=0.0, max=1.0, default=1.0,
                                                         description="Add simulated cloth sway to manual skirt shaping")
        proxy.hide_set(True)
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
        rig["physics_influence"] = old_influence
        skirt_rig.write_record(source, original_record)
        if isinstance(error, (SkirtPhysicsError, ValueError)):
            raise
        raise SkirtPhysicsError(f"Skirt physics was rolled back: {error}") from error


def _cloth(record):
    physics = record.get("physics")
    proxy = bpy.data.objects.get(physics["proxy"]) if physics else None
    if proxy is None or proxy.get(skirt_rig.OWNER_KEY) != record["owner"]:
        raise SkirtPhysicsError("The owned cloth cage is missing.")
    cloth = next((m for m in proxy.modifiers if m.type == "CLOTH"), None)
    if cloth is None:
        raise SkirtPhysicsError("The cloth cage has no Cloth modifier.")
    return proxy, cloth


def clear_cache(context, source):
    record, _ = _record(source)
    proxy, cloth = _cloth(record)
    with context.temp_override(object=proxy, active_object=proxy, point_cache=cloth.point_cache):
        if cloth.point_cache.is_baked:
            bpy.ops.ptcache.free_bake()
    record["physics"]["baked_range"] = None
    skirt_rig.write_record(source, record)


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
        bone.rotation_mode = "QUATERNION"
        bone.custom_shape = None
        bone.bone.hide = False
        for constraint in list(bone.constraints):
            bone.constraints.remove(constraint)
    for bone_collection in data.collections_all:
        bone_collection.is_visible = True
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
    if kind not in {"SIMULATION", "ANIMATION"}:
        raise SkirtPhysicsError("Unknown skirt bake mode.")
    start, end = int(start), int(end)
    if start > end:
        raise SkirtPhysicsError("Bake end must not be before start.")
    if end - start > 20000:
        raise SkirtPhysicsError("Bake at most 20,001 frames in one operation.")
    record, rig = _record(source)
    proxy, cloth = _cloth(record)
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
