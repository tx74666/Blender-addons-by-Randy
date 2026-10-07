"""Owned Direct Manual skin -> Body attachment -> Cloth -> absolute output.

This first implementation is a cold-install component, not a clearance or
daily-motion guarantee. Same-frame editing shows the pre-Cloth input. Static
recalculation and per-vertex export remain explicitly unsupported.
"""

import copy
import json
import math

import bpy
from mathutils import Matrix, Vector

from . import skirt_rig as skirt
from . import skirt_surface as shared
from . import skirt_physics as physics
from . import skirt_motion_profiles as profiles

BACKEND = "DIRECT_MAIN_CLOTH_V1"
VERSION = 1
ROLE_KEY = shared.ROLE_KEY
NODE_ROLE = "DIRECT_NODE_GROUP"
BODY_NODE_ROLE = "BODY_NODE_GROUP"
STATE_KEY = "character_designer_dress_direct_state_v1"
PIN_GROUP = shared.PIN_GROUP
BODY_MASK = shared.BODY_MASK
OBJECT_ROLES = frozenset({"INPUT_SURFACE", "CLOTH_PROXY", "BODY_ATTACHMENT", "COLLIDER"})
_ROLES = {"INPUT_SURFACE": 1, "CLOTH_PROXY": 1, "BODY_ATTACHMENT": 1, "COLLIDER": 3}


class SkirtDirectError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise SkirtDirectError(message)


def _tag(obj, source, record, role, *, data=True):
    """Tag new Direct helpers without extending the older DELTA role set."""
    _require(role in OBJECT_ROLES or role in {NODE_ROLE, BODY_NODE_ROLE}, "Unknown Direct Dress role.")
    target = getattr(obj, "data", None) if data else None
    if target is not None:
        _require(target != source.data and target.users == 1,
                 "Tag only a new independent Direct helper Mesh; preserve artist data.")
    for key in list(obj.keys()):
        del obj[key]
    obj[skirt.OWNER_KEY], obj[skirt.SOURCE_KEY], obj[ROLE_KEY] = record["owner"], source, role
    if target is not None:
        for key in list(target.keys()):
            del target[key]
        target[skirt.OWNER_KEY], target[skirt.SOURCE_KEY], target[ROLE_KEY] = record["owner"], source, role


def _installation(record):
    value = record.get("physics", {})
    surface = value.get("surface")
    _require(value.get("backend") == BACKEND and isinstance(surface, dict)
             and type(surface.get("version")) is int and surface["version"] == VERSION,
             "Restore the saved Direct Dress installation contract.")
    return surface


def _state(source, record):
    try:
        value = json.loads(source[STATE_KEY])
    except (KeyError, ValueError, TypeError) as error:
        raise SkirtDirectError("Restore the saved Direct Dress preview state.") from error
    _require(type(value) is dict and set(value) == {"version", "owner", "mode", "editing", "pending"}
             and type(value["version"]) is int and value["version"] == VERSION
             and value["owner"] == record["owner"] and value["mode"] in profiles.MODES
             and type(value["editing"]) is bool and type(value["pending"]) is bool,
             "The Direct Dress preview state is incomplete or belongs to another source.")
    return value


def _write_state(source, state):
    source[STATE_KEY] = shared._json(state)


def _object(source, record, role):
    names = _installation(record)["roles"].get(role)
    _require(isinstance(names, list) and len(names) == _ROLES[role], "Restore Direct Dress role: " + role)
    result = []
    for name in names:
        obj = bpy.data.objects.get(name)
        _require(obj is not None and obj.get(ROLE_KEY) == role
                 and obj.get(skirt.OWNER_KEY) == record["owner"] and obj.get(skirt.SOURCE_KEY) == source
                 and name in record["owned_objects"] and not (obj.library or obj.override_library),
                 "Restore the exact owned Direct Dress object: " + str(name))
        result.append(obj)
    return result if role == "COLLIDER" else result[0]


def _overlay(source, record):
    surface = _installation(record)
    modifier = source.modifiers.get(surface["overlay"])
    group = bpy.data.node_groups.get(surface["node_group"])
    _require(modifier is not None and modifier.type == "NODES" and modifier.node_group == group
             and group is not None and group.get(ROLE_KEY) == NODE_ROLE and group.users == 1
             and group.get(skirt.OWNER_KEY) == record["owner"] and group.get(skirt.SOURCE_KEY) == source,
             "Restore the unique owned Direct Dress output.")
    _require(shared._node_content(group) == surface["node_contract"],
             "Preserve the edited Direct Dress node graph before continuing.")
    return modifier


def _use_cloth(state):
    return state["mode"] == "AUTOMATIC" and not state["editing"] and not state["pending"]


def _mode_input(modifier, identifier):
    """Use the actual Blender 5.2 typed modifier input, never old ID props."""
    _require(type(identifier) is str and identifier, "Restore the exact Direct output socket identifier.")
    try:
        value = getattr(modifier.properties.inputs, identifier)
    except (AttributeError, TypeError) as error:
        raise SkirtDirectError("Restore the native Blender 5.2 Direct output input.") from error
    _require(value.type == "VALUE" and value.attribute_name == "" and value.layer_name == ""
             and type(value.value) is bool, "The Direct output must use its native boolean Value input without an attribute or layer.")
    return value


def _sync(source, record, state):
    surface = _installation(record)
    output = _overlay(source, record)
    cloth = _object(source, record, "CLOTH_PROXY").modifiers[-1]
    use_cloth = _use_cloth(state)
    socket = _mode_input(output, surface["mode_socket"])
    socket.value = use_cloth
    _require(type(socket.value) is bool and socket.value == use_cloth, "Blender did not retain the requested Direct output input.")
    output.show_viewport = output.show_render = True
    cloth.show_viewport = cloth.show_render = use_cloth


def _cache_contract(cache):
    # Runtime RAM addresses and transient info are not saved cross-session IDs.
    return {name: getattr(cache, name) for name in
            ("frame_start", "frame_end", "frame_step", "use_disk_cache", "use_external", "filepath", "name", "index")}


def _cache_install_safe(cloth, record):
    shared._cache_upgrade_safe(record["physics"], cloth)
    _require(not cloth.point_cache.info or cloth.point_cache.info.startswith("0 frames in memory (0 B)"),
             "Reset the existing Dress RAM cache explicitly before Direct installation; its payload cannot be rolled back.")


def _raw_mesh(obj):
    mesh = obj.data
    return {"topology": shared._topology(mesh), "coords": [list(v.co) for v in mesh.vertices],
            "groups": shared._groups(obj), "materials": [shared._id(m) for m in mesh.materials],
            "uv": [{"name": uv.name, "active_render": uv.active_render,
                    "data": [list(item.uv) for item in uv.data]} for uv in mesh.uv_layers],
            "uv_active": mesh.uv_layers.active_index,
            "attributes": [{"name": attr.name, "domain": attr.domain, "type": attr.data_type,
                            "data": [shared._rna(item) for item in attr.data]} for attr in mesh.attributes]}


def _raw_without_groups(obj):
    result = _raw_mesh(obj)
    result.pop("groups")
    return result


def _ring_ids(record, count):
    rings = record["fit"]["rings"]
    _require(isinstance(rings, list) and rings and all(isinstance(ring, list) for ring in rings)
             and all(type(i) is int and 0 <= i < count for ring in rings for i in ring)
             and sorted(i for ring in rings for i in ring) == list(range(count)),
             "The Dress rings must prove complete exact native indices.")
    ring = rings[0]
    _require(len(ring) >= 3 and len(ring) == len(set(ring)), "The fixed Dress waist ring has duplicate or missing indices.")
    return ring


def _positive_frame(matrix):
    _require(all(math.isfinite(v) for row in matrix for v in row), "Use finite native Direct object transforms.")
    linear = matrix.to_3x3()
    lengths = [linear.col[i].length for i in range(3)]
    _require(min(lengths) > 0. and linear.determinant() > 0.
             and max(lengths) - min(lengths) <= max(lengths) * 2.e-6
             and all(abs(linear.col[i].dot(linear.col[j])) <= lengths[i] * lengths[j] * 2.e-6
                     for i in range(3) for j in range(i)),
             "Direct collider calibration currently requires positive uniform object transforms without shear; native bone TRS is not restricted.")


def _pins(values, count, ring):
    _require(type(values) is list and len(values) == count
             and all(type(v) in (int, float) and math.isfinite(v) and 0. <= v <= 1. for v in values)
             and all(values[i] == 1. for i in ring), "The native Direct Dress pin vector is incomplete or invalid.")
    return {i: float(v) for i, v in enumerate(values) if v}


def _deform(source, rig, record, *, backup=False):
    from . import skirt_original_mode
    _require(all(len(rig.pose.bones[name].constraints) == 2 for chain in record["chains"] for name in chain["def"]),
             "Preserve extra Dress deform constraints before installing Direct Cloth.")
    # This proves the actual generated AVERAGE drivers and local Manual graph;
    # no evaluated DEF or PHYS pose is copied back into its input.
    working = skirt._ORIGINAL_SESSION_KEY in rig
    owner = rig.get(skirt.ORIGINAL_DISPLAY_OWNER_KEY)
    working |= isinstance(owner, bpy.types.Object) and skirt._ORIGINAL_SESSION_KEY in owner
    relations = skirt_original_mode._relations(rig, source, record, working=working)
    if not backup:
        holder, _id, _path = skirt.physics_control(source)
        _require(type(holder.get("physics_influence")) in (int, float) and holder["physics_influence"] == 0.,
                 "Keep the old Dress bone-physics contribution at zero for Direct Cloth.")
        _require(all(rotation.mute for _pb, _manual, rotation in relations),
                 "The old owned Dress physics rotations must remain paused; Direct Cloth is the only physical writer.")
    return relations


def _closure(source, rig, record):
    manual_names = {n for chain in record["chains"] for n in chain["manual"]}
    physics_names = {n for chain in record["chains"] for n in chain["phys"]}
    generated = []
    for index, chain in enumerate(record["chains"]):
        wire = shared._generated_wire(source, rig, record, index)
        _require(all(mod.show_viewport and mod.show_render for mod in wire.modifiers),
                 "Enable every native Dress Manual Hook before Direct evaluation.")
        generated.append(wire)
        for name in chain["manual"]:
            constraints = list(rig.pose.bones[name].constraints)
            if name == chain["manual"][-1]:
                _require(len(constraints) == 1 and constraints[0].type == "SPLINE_IK"
                         and constraints[0].target == wire and not constraints[0].mute
                         and constraints[0].influence == 1., "Restore the native Dress Manual spline input.")
            else:
                _require(not constraints, "Preserve additional Manual chain constraints before Direct installation.")
        _require(not any(mod.subtarget in physics_names for mod in wire.modifiers),
                 "A Manual wire feeds from Dress PHYS; collision feedback is refused.")
    names = set(shared._manual_names(record)) | manual_names | set(shared._ancestors(rig, record)) | {record["controls"]["waist"]}
    forbidden = {source}
    old_proxy = bpy.data.objects.get(record.get("physics", {}).get("proxy", ""))
    if old_proxy is not None:
        forbidden.add(old_proxy)
    for owner in [rig] + [rig.pose.bones[name] for name in names]:
        _require(not any(getattr(con, "target", None) in forbidden for con in owner.constraints),
                 "A native Manual or Body input depends on a Dress output object.")
    for name in names - manual_names:
        bone = rig.pose.bones[name]
        _require(not any(con.target == rig and con.subtarget in physics_names
                         for con in bone.constraints if hasattr(con, "target") and hasattr(con, "subtarget")),
                 "A Dress upstream input depends on old PHYS.")
    prefixes = tuple(rig.pose.bones[n].path_from_id() for n in names)
    phys_prefixes = tuple(rig.pose.bones[n].path_from_id() for n in physics_names)
    for curve in rig.animation_data.drivers if rig.animation_data else ():
        if curve.data_path.startswith(prefixes):
            _require(not any(target.id in forbidden or (target.id == rig and target.data_path.startswith(phys_prefixes))
                             for variable in curve.driver.variables for target in variable.targets),
                     "A native Direct input driver reads Dress PHYS or its output.")
    return generated


def _mesh_copy(source, name, collection, tx, record, role):
    obj = tx.copy(source, name, collection)
    _tag(obj, source, record, role)
    _require(obj.data.shape_keys is None, "Direct Shape Key inputs are not validated; no Key was removed.")
    obj.animation_data_clear()
    obj.data.animation_data_clear()
    for modifier in list(obj.modifiers)[1:]:
        obj.modifiers.remove(modifier)
    # The native parent, inverse, basis and Armature are copied, not retargeted.
    _require(shared._frame(obj) == shared._frame(source) and not obj.constraints,
             "The independent Direct input chart differs from its original native source.")
    return obj


def _add_colliders(context, source, rig, record, collection, tx, old_record):
    if old_record.get("physics"):
        result = []
        for name in old_record["physics"]["colliders"]:
            original = bpy.data.objects[name]
            obj = tx.copy(original, "CD Direct " + original.name, collection)
            _tag(obj, source, record, "COLLIDER")
            result.append(obj)
        _require(len(result) == 3, "Direct Cloth currently requires the complete pelvis and two-thigh collider set.")
        return result
    attach, pelvis = physics._collider_attachment(rig, record)
    fit_world = physics._collider_rest_world(source, rig, record, attach, pelvis)
    scale = record["fit"]["height_world"]
    linear, inverse = attach.matrix_world.to_3x3(), attach.matrix_world.inverted()
    units = 1. / max(linear.col[i].length for i in range(3))
    fit = record["fit"]["fit_waist"]
    center = inverse @ (fit_world @ Vector(record["fit"]["waist_center"]) - Vector((0., 0., scale * .07)))
    specs = [("Pelvis", pelvis, center, inverse.to_3x3() @ Vector((0., 0., 1.)),
              (fit_world.to_3x3() @ Vector(fit["cosine"])).length * .78 * units,
              (fit_world.to_3x3() @ Vector(fit["sine"])).length * .78 * units, scale * .26 * units)]
    for side in ("L", "R"):
        name = physics._bone_name(attach, ("thigh." + side, "thigh_" + side, "DEF-thigh." + side,
                                         "upper_leg." + side, "UpperLeg_" + side))
        _require(name is not None, "Set both actual Body thigh bones before installing Direct Cloth.")
        bone = attach.data.bones[name]
        axis = bone.tail_local - bone.head_local
        radius = record["fit"]["waist_radius_world"] * .46 * units
        specs.append(("Thigh." + side, name, bone.head_local.lerp(bone.tail_local, .46), axis, radius, radius, axis.length * .57))
    result = []
    for label, bone, center, axis, rx, ry, length in specs:
        verts, faces = physics._ellipsoid(center, axis, rx, ry, length)
        mesh = bpy.data.meshes.new("CD Direct Collider " + label + " · " + source.name)
        tx.data.append(mesh)
        obj = bpy.data.objects.new(mesh.name, mesh)
        tx.objects.append(obj)
        collection.objects.link(obj)
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        obj.matrix_world = attach.matrix_world.copy()
        _tag(obj, source, record, "COLLIDER")
        physics._bind_single(obj, attach, bone)
        obj.modifiers.new("Dress collision", "COLLISION")
        obj.collision.thickness_outer, obj.collision.thickness_inner = scale * .004, scale * .001
        obj.collision.damping = .2
        result.append(obj)
    return result


def _body_preflight(body, rig, source, record):
    # Reject before allocating helpers or evaluating a temporary REST frame.
    # Forearm corrective Keys require the artist Body Mesh to retain one user.
    shared._require(body is not None and body.type == "MESH" and body != source
                    and not (body.library or body.data.library or body.override_library or body.data.override_library)
                    and body.data.users == 1 and body.parent_type == "OBJECT",
                    "Choose the registered local Body mesh with one native Mesh user.")
    modifiers = list(body.modifiers)
    body_rig = rig if skirt.is_shared(record) else rig.parent
    shared._require([m.type for m in modifiers] in (["ARMATURE"], ["ARMATURE", "SUBSURF"])
                    and body_rig is not None and body_rig.type == "ARMATURE" and body.parent in (None, body_rig)
                    and modifiers[0].object == body_rig and modifiers[0].use_vertex_groups
                    and not modifiers[0].use_bone_envelopes and not modifiers[0].vertex_group
                    and not modifiers[0].use_multi_modifier and all(m.show_viewport and m.show_render for m in modifiers),
                    "Use the registered Body's native Armature and optional Subsurf for Dress collision.")
    forbidden = {group.index for group in body.vertex_groups if group.name in shared._owned_bones(record)}
    shared._require(not any(w.group in forbidden and w.weight > 0. for vertex in body.data.vertices for w in vertex.groups),
                    "The Body depends on Dress deform bones; collision feedback is refused.")
    return body_rig


def _group_mapping(obj):
    return [{"name": g.name, "index": g.index, "lock_weight": g.lock_weight} for g in obj.vertex_groups]


def _body_source_contract(body):
    # Key values may legitimately animate. The relay reads their actual result,
    # without duplicating, clearing, or owning any artist Mesh/Key/Action.
    return {"mesh": shared._id(body.data), "keys": shared._id(body.data.shape_keys),
            "frame": shared._frame(body), "topology": shared._digest(shared._topology(body.data)),
            "groups": shared._digest(shared._groups(body)), "mapping": _group_mapping(body),
            "modifiers": [shared._rna(m, {"is_active"}) for m in body.modifiers]}


def _body_node(source, record):
    surface = _installation(record)
    clone = _object(source, record, "BODY_ATTACHMENT")
    group = bpy.data.node_groups.get(surface["body_node_group"])
    _require(group is not None and group.get(ROLE_KEY) == BODY_NODE_ROLE and group.users == 1
             and not (group.library or group.override_library) and group.animation_data is None
             and group.get(skirt.OWNER_KEY) == record["owner"] and group.get(skirt.SOURCE_KEY) == source
             and clone.modifiers[0].type == "NODES" and clone.modifiers[0].node_group == group
             and shared._node_content(group) == surface["body_node_contract"],
             "Restore the unique owned Body geometry relay and its exact native node graph.")
    return group


def _body(context, body, rig, source, record, collection, tx):
    _body_preflight(body, rig, source, record)
    mesh = bpy.data.meshes.new("CD Body Relay · " + source.name)
    tx.data.append(mesh)
    clone = bpy.data.objects.new(mesh.name, mesh)
    tx.objects.append(clone)
    collection.objects.link(clone)
    _tag(clone, source, record, "BODY_ATTACHMENT")
    clone.matrix_world = body.matrix_world.copy()
    shared._world_follow(clone, body, name="Dress Body object input")
    for original in body.vertex_groups:
        group = clone.vertex_groups.new(name=original.name)
        group.lock_weight = original.lock_weight
    _require(_group_mapping(clone) == _group_mapping(body), "The empty Body relay group mapping differs from its actual input.")
    group = bpy.data.node_groups.new("Dress Body Relay · " + source.name, "GeometryNodeTree")
    tx.nodes.append(group)
    _tag(group, source, record, BODY_NODE_ROLE, data=False)
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    info, output = group.nodes.new("GeometryNodeObjectInfo"), group.nodes.new("NodeGroupOutput")
    info.name, output.name = "Actual Body", "Output"
    info.transform_space = "RELATIVE"
    info.inputs["Object"].default_value = body
    info.inputs["As Instance"].default_value = False
    output.is_active_output = True
    group.links.new(info.outputs["Geometry"], output.inputs["Geometry"])
    _require(len(group.links) == 1 and group.links[0].is_valid, "Blender did not create the native Body relay geometry link.")
    nodes = clone.modifiers.new("Dress actual Body geometry", "NODES")
    nodes.node_group = group
    saved_active = nodes.is_active
    clone.modifiers.new("Dress Body collision", "COLLISION")
    nodes.is_active = saved_active
    templates = [bpy.data.objects[name].collision for name in record["physics"]["colliders"]]
    _require(templates and all(shared._rna(t) == shared._rna(templates[0]) for t in templates),
             "The owned collider settings differ; choose explicit collision tuning before upgrading.")
    shared._copy_scalars(templates[0], clone.collision)
    clone.hide_render = clone.hide_select = True
    clone.hide_set(False)
    return clone, group


def _body_geometry_proof(context, body, clone):
    # Both objects are read from one actual native graph. This is bind geometry
    # equivalence, not an inside-volume or collision-clearance acceptance.
    graph = context.evaluated_depsgraph_get()
    captured = []
    for obj in (body, clone):
        evaluated = obj.evaluated_get(graph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
        try:
            captured.append((shared._topology(mesh), [evaluated.matrix_world @ v.co for v in mesh.vertices]))
        finally:
            evaluated.to_mesh_clear()
    _require(captured[0][0] == captured[1][0] and captured[0][1], "The native Body relay changed evaluated topology or produced no Mesh.")
    shared._nojump(captured[0][1], captured[1][1], context)
    return {"native_same_graph": True, "vertex_count": len(captured[0][1]),
            "topology": shared._digest(captured[0][0]), "threshold_m": shared._LIMIT,
            "maximum_m": max((a - b).length for a, b in zip(captured[0][1], captured[1][1])) * context.scene.unit_settings.scale_length,
            "clearance_accepted": False}


def _configure(cloth, values, height, collection, old_cloth=None):
    if old_cloth is not None:
        shared._copy_scalars(old_cloth.settings, cloth.settings)
        shared._copy_scalars(old_cloth.collision_settings, cloth.collision_settings)
        shared._copy_scalars(old_cloth.settings.effector_weights, cloth.settings.effector_weights)
    else:
        mapping = {"quality": "quality", "mass": "mass", "stretch": "tension_stiffness", "shear": "shear_stiffness",
                   "bend": "bending_stiffness", "damping": "tension_damping", "bend_damping": "bending_damping",
                   "air_damping": "air_damping", "pin_stiffness": "pin_stiffness"}
        for key, field in mapping.items():
            setattr(cloth.settings, field, values[key])
        cloth.settings.compression_stiffness = values["stretch"]
        cloth.settings.compression_damping = cloth.settings.shear_damping = values["damping"]
        cloth.settings.effector_weights.gravity = values["gravity"]
        cloth.collision_settings.use_collision = True
        cloth.collision_settings.collision_quality = values["collision_quality"]
        cloth.collision_settings.distance_min = height * values["collision_margin"]
        cloth.collision_settings.use_self_collision = values["self_collision"]
        cloth.collision_settings.self_distance_min = height * values["self_margin"]
        cloth.collision_settings.self_friction = values["self_friction"]
    cloth.settings.vertex_group_mass = PIN_GROUP
    cloth.settings.rest_shape_key = None
    cloth.settings.use_dynamic_mesh = True
    cloth.collision_settings.collection = collection


def _node_group(source, input_obj, actual, record, tx):
    group = bpy.data.node_groups.new("Dress Direct Output · " + source.name, "GeometryNodeTree")
    tx.nodes.append(group)
    group[ROLE_KEY], group[skirt.OWNER_KEY], group[skirt.SOURCE_KEY] = NODE_ROLE, record["owner"], source
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    mode = group.interface.new_socket(name="Use Cloth", in_out="INPUT", socket_type="NodeSocketBool")
    mode.default_value = False
    inp, out = group.nodes.new("NodeGroupInput"), group.nodes.new("NodeGroupOutput")
    switch = group.nodes.new("GeometryNodeSwitch")
    switch.input_type = "GEOMETRY"
    position, index = group.nodes.new("GeometryNodeInputPosition"), group.nodes.new("GeometryNodeInputIndex")
    for obj, socket_name in ((input_obj, "False"), (actual, "True")):
        info = group.nodes.new("GeometryNodeObjectInfo")
        info.transform_space = "RELATIVE"
        info.inputs["Object"].default_value, info.inputs["As Instance"].default_value = obj, False
        group.links.new(info.outputs["Geometry"], switch.inputs[socket_name])
    sample = group.nodes.new("GeometryNodeSampleIndex")
    sample.data_type, sample.domain, sample.clamp = "FLOAT_VECTOR", "POINT", False
    setpos = group.nodes.new("GeometryNodeSetPosition")
    setpos.inputs["Selection"].default_value, setpos.inputs["Offset"].default_value = True, (0., 0., 0.)
    for a, b in ((inp.outputs["Use Cloth"], switch.inputs["Switch"]),
                 (switch.outputs["Output"], sample.inputs["Geometry"]),
                 (position.outputs["Position"], sample.inputs["Value"]), (index.outputs["Index"], sample.inputs["Index"]),
                 (inp.outputs["Geometry"], setpos.inputs["Geometry"]), (sample.outputs["Value"], setpos.inputs["Position"]),
                 (setpos.outputs["Geometry"], out.inputs["Geometry"])):
        group.links.new(a, b)
    _require(all(link.is_valid for link in group.links), "Blender did not create valid Direct absolute-index links.")
    return group, mode.identifier


def _install_checkpoint(_context, _source, _stage):
    """Private QA can replace this no-op to verify exact installation rollback."""


def _restore_optional(source, key, value):
    if value is None:
        source.pop(key, None)
    else:
        source[key] = value


def _restore_ui(holder, values):
    ui = holder.id_properties_ui("physics_influence")
    ui.clear()
    if values:
        ui.update(**values)
    _require(ui.as_dict() == values, "The old Dress property UI could not be restored.")


def _restore_active(source, modifiers, flags):
    _require(tuple(source.modifiers) == modifiers, "The original source modifier inventory could not be restored.")
    # Native False is a no-op. A prior True selects that exact old modifier.
    for modifier, active in zip(modifiers, flags):
        modifier.is_active = active
    _require([m.is_active for m in modifiers] == flags, "The original source modifier UI state could not be restored.")


def install(context, source, *, body=None, capability=None):
    _require(source.data.shape_keys is None, "Direct Dress Shape Key input is not validated. Preserve these Keys; no changes were made.")
    from . import forearm_twist
    # Cover REST entry, native bind, context restoration and every rollback
    # attempt. A transient installer state must not mute artist corrective Keys.
    with forearm_twist.defer_runtime(context, flush_on_exit=False):
        return _install(context, source, body=body, capability=capability)


def _install(context, source, *, body=None, capability=None):
    skirt._require_controls_for_setup(source)
    record = skirt.read_record(source)
    _require(record is not None, "Build Dress controls before installing Direct Cloth.")
    _require(source.data.shape_keys is None, "Direct Dress Shape Key input is not validated. Preserve these Keys; no changes were made.")
    rig = source[skirt.RIG_KEY]
    if record.get("physics", {}).get("backend") == BACKEND:
        validate(source, rig, record)
        return record
    _require(physics.backend(record) == physics.LEGACY_BACKEND,
             "DELTA-to-Direct migration is not validated. Preserve its graph and cached result.")
    _body_preflight(body, rig, source, record)
    shared._installation_context_preflight(context, source, rig, body)
    _require(source.mode in {"OBJECT", "POSE"} and not source.constraints and source.data.users == 1
             and source.animation_data is None and source.data.animation_data is None
             and [m.type for m in source.modifiers] in (["ARMATURE"], ["ARMATURE", "SUBSURF"]),
             "Use the original unanimated Dress Mesh with its native Armature and optional Subsurf.")
    skin = source.modifiers[0]
    _require(skin.object == rig and skin.show_viewport and skin.show_render and skin.use_vertex_groups
             and not skin.use_bone_envelopes and not skin.use_multi_modifier and not skin.vertex_group,
             "Restore the original native Dress vertex-group skin.")
    _require(body is not None and body.type == "MESH", "Choose the actual registered Body mesh for Direct Cloth.")
    _require(math.isfinite(context.scene.unit_settings.scale_length) and context.scene.unit_settings.scale_length > 0.,
             "Use a finite positive physical scene scale for Direct Dress.")
    for obj in (source, rig, body):
        _positive_frame(obj.matrix_world)
    skirt._check_existing_geometry(source, record)
    _closure(source, rig, record)
    relations = _deform(source, rig, record, backup=True)
    forbidden_groups = {group.index for group in source.vertex_groups
                        if group.name in {n for chain in record["chains"] for n in chain["phys"]}}
    _require(not any(weight.group in forbidden_groups and weight.weight > 0.
                     for vertex in source.data.vertices for weight in vertex.groups),
             "The original Dress skin directly weights PHYS bones; Direct collision feedback is refused.")
    holder, _id, path = skirt.physics_control(source)
    from . import skirt_motion_tuning as tuning
    tuning._influence_editable(rig, path)
    _require(type(holder.get("physics_influence")) in (int, float)
             and holder["physics_influence"] in (0., 1.), "Preserve an intermediate Dress blend before installing Direct Cloth.")
    profile = profiles.read(source, record)
    _require(capability is None or capability in profiles.CAPABILITIES, "Unknown Direct Dress generation intent.")
    if profile is None:
        chosen = capability or "BOTH"
        profile = profiles.fresh(record, capability=chosen, mode="MANUAL" if chosen == "MANUAL" else "AUTOMATIC")
    elif capability is not None:
        profile = profiles.edited(profile, record, capability=capability)
    old_cloth = None
    if record.get("physics"):
        _proxy, old_cloth = physics._verify_physics_graph(source, rig, record)
        _cache_install_safe(old_cloth, record)
        _require(old_cloth.settings.rest_shape_key is None and old_cloth.settings.effector_weights.collection is None
                 and all(p.identifier == "vertex_group_mass" or not getattr(old_cloth.settings, p.identifier)
                         for p in old_cloth.settings.bl_rna.properties if p.identifier.startswith("vertex_group_")),
                 "Preserve custom old Cloth group, Rest or effector inputs before Direct installation.")
    shared._other_cloth_preflight(context, (old_cloth,) if old_cloth else ())
    count = len(source.data.vertices)
    ring = _ring_ids(record, count)
    _require(PIN_GROUP not in source.vertex_groups and BODY_MASK not in source.vertex_groups,
             "Preserve conflicting source pin or Body attachment groups before installation.")
    original_record, original_profile, original_state = source[skirt.RECORD_KEY], source.get(profiles.PROFILE_KEY), source.get(STATE_KEY)
    old_influence, old_ui = holder["physics_influence"], holder.id_properties_ui("physics_influence").as_dict()
    original_modifiers = tuple(source.modifiers)
    original_active = [m.is_active for m in original_modifiers]
    source_raw = shared._digest(_raw_mesh(source))
    saved_rotations = [(pb.name, rotation.name, rotation.mute) for pb, _manual, rotation in relations]
    tx = shared._Transaction(context, source)
    tx.remember_keys(body.data.shape_keys)
    success = False
    try:
        if old_cloth:
            tx.disable(old_cloth)
        for _pb, _manual, rotation in relations:
            rotation.mute = True
        holder["physics_influence"] = 0.
        # All native bind evaluation occurs with old Cloth paused. The artist's
        # channels, Keys, frame and object modes are restored before activation.
        tx.rest(rig)
        body_rigs = [m.object for m in body.modifiers if m.type == "ARMATURE" and m.object is not None]
        for body_rig in body_rigs:
            tx.rest(body_rig)
        context.scene.frame_set(0)
        context.view_layer.update()
        home = context.scene
        destination = home.collection
        collision = bpy.data.collections.new("CD Direct Collision · " + source.name)
        tx.collections.append(collision)
        destination.children.link(collision)
        collision[skirt.OWNER_KEY], collision[skirt.SOURCE_KEY] = record["owner"], source
        candidate = copy.deepcopy(record)
        colliders = _add_colliders(context, source, rig, record, collision, tx, record)
        # Relay the actual final Body; never share its artist Mesh/Keys or skin it twice.
        templates = copy.deepcopy(record)
        templates["physics"] = {"colliders": [o.name for o in colliders]}
        clone, body_group = _body(context, body, rig, source, templates, collision, tx)
        body_bind_proof = _body_geometry_proof(context, body, clone)
        input_obj = _mesh_copy(source, "CD Dress Input · " + source.name, destination, tx, record, "INPUT_SURFACE")
        mask = input_obj.vertex_groups.new(name=BODY_MASK)
        mask.add(ring, 1., "REPLACE")
        sd = input_obj.modifiers.new("Dress Body attachment", "SURFACE_DEFORM")
        sd.target, sd.vertex_group, sd.strength, sd.use_sparse_bind = clone, BODY_MASK, 1., True
        bind_before = shared._points(input_obj, context)
        shared._bind(context, input_obj, sd)
        _require(sd.is_bound and sd.target == clone, "The new native Body attachment did not remain bound.")
        bind_after = shared._points(input_obj, context)
        shared._nojump(bind_before, bind_after, context)
        _install_checkpoint(context, source, "bound")
        actual = tx.copy(input_obj, "CD Dress Cloth · " + source.name, destination)
        _tag(actual, source, record, "CLOTH_PROXY")
        _require(actual.modifiers[1].is_bound and actual.modifiers[1].target == clone
                 and shared._frame(actual) == shared._frame(input_obj), "The independent Cloth copy lost the actual native Body binding.")
        rows, columns = record["segment_count"] * 3 + 1, record["chain_count"] * 4
        pin_values = profiles.surface_pin_weights(profile["settings"], record["fit"], rows, columns)
        weights = _pins(pin_values, count, ring)
        pin = actual.vertex_groups.new(name=PIN_GROUP)
        for i, value in weights.items():
            pin.add([i], value, "REPLACE")
        active = [m.is_active for m in actual.modifiers]
        cloth = actual.modifiers.new("Dress Physics", "CLOTH")
        cloth.is_active = False  # False is native no-op; restore previous True.
        for modifier, was_active in zip(actual.modifiers, active):
            modifier.is_active = was_active
        _require([m.is_active for m in list(actual.modifiers)[:-1]] == active, "The Cloth append changed its input modifier UI state.")
        _configure(cloth, profile["settings"], record["fit"]["height_world"], collision, old_cloth)
        cloth.point_cache.frame_start, cloth.point_cache.frame_end, cloth.point_cache.frame_step = home.frame_start, home.frame_end, 1
        cloth.point_cache.use_disk_cache = cloth.point_cache.use_external = False
        cloth.show_viewport = cloth.show_render = False
        group, mode_socket = _node_group(source, input_obj, actual, record, tx)
        overlay = source.modifiers.new("Dress Direct Output", "NODES")
        tx.overlay = overlay
        overlay.node_group = group
        if len(source.modifiers) == 3:
            source.modifiers.move(2, 1)
        for modifier, was_active in zip([m for m in source.modifiers if m != overlay], original_active):
            modifier.is_active = was_active
        _require([m.is_active for m in source.modifiers if m != overlay] == original_active,
                 "The Direct output append changed the source modifier UI state.")
        roles = {"INPUT_SURFACE": [input_obj.name], "CLOTH_PROXY": [actual.name], "BODY_ATTACHMENT": [clone.name],
                 "COLLIDER": [obj.name for obj in colliders]}
        surface = {"version": VERSION, "roles": roles, "home_scene": home.name, "home_root": destination.name,
                   "overlay": overlay.name, "node_group": group.name, "mode_socket": mode_socket,
                   "node_roles": {NODE_ROLE: [group.name], BODY_NODE_ROLE: [body_group.name]},
                   "body_node_group": body_group.name, "body_node_contract": shared._node_content(body_group),
                   "body_source_contract": _body_source_contract(body), "body_relay_bind_proof": body_bind_proof,
                   "pin_group": PIN_GROUP, "body": body.name, "body_frame": shared._frame(clone),
                   "body_constraints": [shared._rna(con) for con in clone.constraints],
                   "body_collision": shared._rna(clone.collision), "body_topology": shared._digest(shared._topology(body.data)),
                   "body_groups": shared._digest(shared._groups(body)), "source_raw": source_raw,
                   "source_rest": shared._rest(rig, shared._owned_bones(record) | set(shared._ancestors(rig, record))),
                   "cloth_contract": shared._cloth_contract(cloth), "contracts": {},
                   "backup": {"record_raw": original_record, "profile_raw": original_profile, "state_raw": original_state,
                              "influence": old_influence, "influence_ui": old_ui, "rotations": saved_rotations,
                              "old_cloth": None if old_cloth is None else [record["physics"]["proxy"], old_cloth.name,
                                                                         tx.flags[0][1], tx.flags[0][2]],
                              "old_cache_contract": None if old_cloth is None else _cache_contract(old_cloth.point_cache)},
                   "limits": {"cold_install_native_acceptance": False, "clearance_guaranteed": False,
                              "shape_keys_supported": False, "same_frame_recalculation_supported": False,
                              "per_vertex_export_supported": False}}
        candidate["physics"] = {"backend": BACKEND, "proxy": actual.name, "colliders": [o.name for o in colliders] + [clone.name],
                                "collection": collision.name, "rows": rows, "columns": columns,
                                "pin_weights": pin_values, "baked_range": None, "surface": surface}
        surface["bind_proof"] = {"native_bound": True, "rest_vertex_count": len(bind_after),
                                 "rest_maximum_m": max((a - b).length for a, b in zip(bind_before, bind_after)) * home.unit_settings.scale_length,
                                 "threshold_m": shared._LIMIT, "actual_pose_clearance_accepted": False}
        candidate["owned_objects"].extend(o.name for o in tx.objects)
        candidate.setdefault("owned_collections", []).append(collision.name)
        surface["source_contract"] = shared._source_contract(source, candidate)
        surface["node_contract"] = shared._node_content(group)
        for obj in tx.objects:
            surface["contracts"][obj.name] = shared._helper_contract(obj, dynamic_pin=(obj == actual))
        _require(shared._digest(_raw_mesh(source)) == source_raw, "Direct installation changed artist Mesh data.")
        for obj in tx.objects:
            obj.hide_render, obj.hide_select = True, True
            obj.hide_set(True)
        tx.restore_context()
        state = {"version": VERSION, "owner": record["owner"], "mode": profile["mode"], "editing": False, "pending": False}
        skirt.write_record(source, candidate)
        profiles.write(source, profile, candidate)
        _write_state(source, state)
        _sync(source, candidate, state)
        validate(source, rig, candidate)
        _install_checkpoint(context, source, "validated")
        # Public callers must receive the same normalized metadata as a later
        # no-op/read. JSON converts tuple receipts to lists without changing
        # their values. Read and verify before discarding rollback flags.
        persisted = skirt.read_record(source)
        _require(shared._json(persisted) == shared._json(candidate), "The saved Direct installation differs from its successful transaction.")
        # Old endpoints remain paused, with their source record preserved.
        tx.flags.clear()
        success = True
        return persisted
    except Exception as error:
        failures = []
        def attempt(label, callback):
            try:
                callback()
            except Exception as failure:
                failures.append(label + ": " + str(failure))

        # No individual recovery failure may prevent subsequent restorations.
        attempt("owned IDs", tx.rollback)
        for name, con_name, mute in saved_rotations:
            attempt("rotation " + name, lambda n=name, c=con_name, v=mute: setattr(rig.pose.bones[n].constraints[c], "mute", v))
        attempt("influence", lambda: holder.__setitem__("physics_influence", old_influence))
        attempt("property UI", lambda: _restore_ui(holder, old_ui))
        attempt("record", lambda: source.__setitem__(skirt.RECORD_KEY, original_record))
        for key, value in ((profiles.PROFILE_KEY, original_profile), (STATE_KEY, original_state)):
            attempt("metadata " + key, lambda k=key, v=value: _restore_optional(source, k, v))
        attempt("source modifier UI", lambda: _restore_active(source, original_modifiers, original_active))
        attempt("artist context", tx.restore_context)
        attempt("old endpoint flags", tx.enable_last)
        if failures:
            raise SkirtDirectError("Direct installation rollback needs recovery: " + "; ".join(failures) + "; " + str(error)) from error
        raise


def validate(source, rig, record):
    surface = _installation(record)
    _require(source.get(skirt.RIG_KEY) == rig and record["source"] == source.name and record["rig"] == rig.name
             and source.data.shape_keys is None, "Restore the exact Direct source, Main Rig and supported no-Key input.")
    _require(shared._source_contract(source, record) == surface["source_contract"]
             and shared._digest(_raw_mesh(source)) == surface["source_raw"],
             "Dress Mesh, UV, groups, native input modifiers or placement changed. Recalibrate explicitly.")
    _require(set(surface["roles"]) == set(_ROLES), "Restore the complete Direct object role inventory.")
    input_obj, actual, clone = (_object(source, record, role) for role in ("INPUT_SURFACE", "CLOTH_PROXY", "BODY_ATTACHMENT"))
    colliders = _object(source, record, "COLLIDER")
    output = _overlay(source, record)
    home = bpy.data.scenes.get(surface["home_scene"])
    collision = bpy.data.collections.get(record["physics"]["collection"])
    _require(home is not None and home.collection.name == surface["home_root"] and collision is not None
             and collision in home.collection.children.values() and not collision.children
             and collision.get(skirt.OWNER_KEY) == record["owner"] and collision.get(skirt.SOURCE_KEY) == source
             and collision.name in record["owned_collections"] and not collision.hide_viewport and not collision.hide_render
             and set(collision.objects) == set(colliders + [clone]), "Restore the exact enabled Direct collision collection.")
    _require([m.type for m in input_obj.modifiers] == ["ARMATURE", "SURFACE_DEFORM"]
             and [m.type for m in actual.modifiers] == ["ARMATURE", "SURFACE_DEFORM", "CLOTH"]
             and record["physics"]["proxy"] == actual.name
             and record["physics"]["colliders"] == [o.name for o in colliders] + [clone.name],
             "Restore the Direct pre-Cloth input and single native physical writer.")
    cloth = actual.modifiers[-1]
    for obj in (input_obj, actual, clone, *colliders):
        _require(obj.data is not None and obj.data.users == 1 and obj.data.shape_keys is None
                 and not (obj.data.library or obj.data.override_library) and obj.data.get(ROLE_KEY) == obj.get(ROLE_KEY)
                 and obj.data.get(skirt.OWNER_KEY) == record["owner"] and obj.data.get(skirt.SOURCE_KEY) == source
                 and obj.animation_data is None and obj.data.animation_data is None,
                 "Preserve external data or animation on Direct helper: " + obj.name)
        _require(shared._helper_contract(obj, dynamic_pin=(obj == actual)) == surface["contracts"][obj.name],
                 "Preserve edited Direct helper geometry, groups or native RNA: " + obj.name)
    count = len(source.data.vertices)
    ring = _ring_ids(record, count)
    expected_pin = _pins(record["physics"]["pin_weights"], count, ring)
    _require(physics._same_weights(physics._weights(actual)[PIN_GROUP], expected_pin), "Restore exact native Direct pin weights.")
    _require(shared._topology(input_obj.data) == shared._topology(actual.data) == shared._topology(source.data)
             and _raw_without_groups(input_obj) == _raw_without_groups(actual) == _raw_without_groups(source),
             "Direct exact-index data, UV or original material correspondence changed.")
    for obj in (input_obj, actual):
        arm, sd = list(obj.modifiers)[:2]
        _require(arm.object == rig and shared._rna(arm, {"is_active"}) == shared._rna(source.modifiers[0], {"is_active"})
                 and sd.is_bound and sd.target == clone and sd.vertex_group == BODY_MASK and sd.strength == 1.
                 and sd.use_sparse_bind and not sd.invert_vertex_group and sd.show_viewport and sd.show_render
                 and shared._frame(obj) == shared._frame(source), "Restore the live native skin and actual bound Body80 input.")
        mask = physics._weights(obj)[BODY_MASK]
        _require(mask == {i: 1. for i in ring}, "Restore the exact raw waist Body attachment index set.")
    _require(cloth.settings.vertex_group_mass == PIN_GROUP and cloth.settings.rest_shape_key is None
             and cloth.settings.use_dynamic_mesh and cloth.settings.effector_weights.collection is None
             and cloth.collision_settings.collection == collision and shared._cloth_contract(cloth) == surface["cloth_contract"],
             "Restore the Direct dynamic-rest Cloth, saved material and exact collision targets.")
    upstream = bpy.data.objects.get(surface["body"])
    _require(upstream is not None, "Restore the actual registered Body geometry input.")
    _body_preflight(upstream, rig, source, record)
    body_group = _body_node(source, record)
    _require(surface["node_roles"] == {NODE_ROLE: [output.node_group.name], BODY_NODE_ROLE: [body_group.name]}
             and clone.data != upstream.data and clone.data.shape_keys is None
             and not clone.data.vertices and not clone.data.edges and not clone.data.polygons
             and _group_mapping(clone) == _group_mapping(upstream)
             and _body_source_contract(upstream) == surface["body_source_contract"]
             and len(clone.constraints) == 1 and [m.type for m in clone.modifiers] == ["NODES", "COLLISION"]
             and all(m.show_viewport and m.show_render for m in clone.modifiers)
             and body_group.nodes["Actual Body"].inputs["Object"].default_value == upstream
             and body_group.nodes["Actual Body"].transform_space == "RELATIVE"
             and body_group.nodes["Actual Body"].inputs["As Instance"].default_value is False
             and shared._frame(clone) == surface["body_frame"]
             and [shared._rna(c) for c in clone.constraints] == surface["body_constraints"]
             and shared._rna(clone.collision) == surface["body_collision"]
             and shared._digest(shared._topology(upstream.data)) == surface["body_topology"]
             and shared._digest(shared._groups(upstream)) == surface["body_groups"],
             "Restore the independent read-only final Body relay, its native graph and original group mapping.")
    shared._follow_proof(clone.constraints[0], upstream, "")
    for collider in colliders:
        physics._closed_collider(collider)
    _require(shared._rest(rig, shared._owned_bones(record) | set(shared._ancestors(rig, record))) == surface["source_rest"],
             "Dress or upstream Body Rest changed. Recalibrate explicitly.")
    _deform(source, rig, record)
    _closure(source, rig, record)
    backup = surface["backup"]
    if backup["old_cloth"] is not None:
        old_name, modifier_name, _viewport, _render = backup["old_cloth"]
        old_proxy = bpy.data.objects.get(old_name)
        old = old_proxy.modifiers.get(modifier_name) if old_proxy is not None else None
        _require(old is not None and old.type == "CLOTH" and not old.show_viewport and not old.show_render
                 and _cache_contract(old.point_cache) == backup["old_cache_contract"], "Restore the paused old physical endpoint and cache configuration.")
    state = _state(source, record)
    enabled = _use_cloth(state)
    _require(_mode_input(output, surface["mode_socket"]).value == enabled
             and output.show_viewport and output.show_render and cloth.show_viewport == cloth.show_render == enabled,
             "The Direct editing preview and Cloth output states differ.")
    _require(not cloth.point_cache.use_external and not cloth.point_cache.is_baking
             and cloth.point_cache.frame_start <= cloth.point_cache.frame_end, "Finish the local Direct bake before continuing.")
    return actual, cloth


def capture_mode(source, record):
    validate(source, source[skirt.RIG_KEY], record)
    modifier = _overlay(source, record)
    cloth = _object(source, record, "CLOTH_PROXY").modifiers[-1]
    return {"source": source, "owner": record["owner"], "modifier": modifier, "group": modifier.node_group,
            "cloth": cloth, "raw_state": source[STATE_KEY], "state": _state(source, record),
            "socket": _mode_input(modifier, _installation(record)["mode_socket"]).value,
            "overlay_flags": (modifier.show_viewport, modifier.show_render), "cloth_flags": (cloth.show_viewport, cloth.show_render)}


def restore_mode(source, record, state):
    _require(state["source"] == source and state["owner"] == record["owner"]
             and _overlay(source, record) == state["modifier"] and state["modifier"].node_group == state["group"]
             and _object(source, record, "CLOTH_PROXY").modifiers[-1] == state["cloth"], "The captured Direct preview endpoint changed during rollback.")
    _require(type(state["socket"]) is bool, "The captured Direct output input is not a native boolean.")
    socket = _mode_input(state["modifier"], _installation(record)["mode_socket"])
    source[STATE_KEY] = state["raw_state"]
    socket.value = state["socket"]
    _require(type(socket.value) is bool and socket.value == state["socket"], "Blender did not restore the captured Direct output input.")
    state["modifier"].show_viewport, state["modifier"].show_render = state["overlay_flags"]
    state["cloth"].show_viewport, state["cloth"].show_render = state["cloth_flags"]


def set_mode(source, record, mode):
    _require(mode in profiles.MODES, "Unknown Direct Dress output mode.")
    state = _state(source, record)
    _require(not (mode == "AUTOMATIC" and state["pending"]),
             "This edited pose is showing the pre-Cloth input. Reset the simulation explicitly before resuming Automatic; no stale physical result was exposed.")
    if mode == "MANUAL":
        cache = _object(source, record, "CLOTH_PROXY").modifiers[-1].point_cache
        _require(type(cache.is_baked) is bool, "Restore the native Direct Cloth bake state.")
        # Manual preview can change the input while a RAM cache remains.
        # Do not free or seek here; require explicit Reset before Auto.
        state["pending"] = True
    state["mode"] = mode
    _write_state(source, state)
    _sync(source, record, state)


def require_bake_ready(source, record):
    """Refuse inactive/input previews before the caller frees or seeks a cache."""
    state = _state(source, record)
    _require(_use_cloth(state),
             "Reset the Direct Dress simulation and return to Automatic before baking; Manual and Original show the input, not a new physical result.")
    cloth = _object(source, record, "CLOTH_PROXY").modifiers[-1]
    _require(cloth.type == "CLOTH" and cloth.show_viewport is True and cloth.show_render is True,
             "Enable the owned Direct Cloth output before baking.")


def set_editing(source, record, editing):
    _require(type(editing) is bool, "Direct editing must be an explicit boolean state.")
    state = _state(source, record)
    state["editing"] = editing
    if editing:
        state["pending"] = True
    _write_state(source, state)
    _sync(source, record, state)


def reset_completed(context, source, record):
    """Complete only the caller's successful explicit start-frame cache reset.

    This does not seek, recalculate a held pose, or accept collision clearance.
    The public reset service owns native reset and its frame/cache rollback.
    """
    actual, cloth = validate(source, source[skirt.RIG_KEY], record)
    surface = _installation(record)
    state = _state(source, record)
    cache = cloth.point_cache
    _require(context.scene.name == surface["home_scene"]
             and context.scene.frame_current == cache.frame_start
             and context.scene.frame_subframe == 0.,
             "Complete the explicit Direct reset at its actual cache start frame.")
    # The caller commits saved baked_range=None after this handoff; the prior
    # record may still describe the bake which its actual reset just freed.
    _require(not state["editing"] and not cache.is_baked and not cache.is_baking
             and not cache.use_external and not cache.use_disk_cache,
             "Finish Original editing and the local unbaked reset before resuming Direct Cloth.")
    captured = capture_mode(source, record)
    try:
        state["pending"] = False
        _write_state(source, state)
        _sync(source, record, state)
        validate(source, source[skirt.RIG_KEY], record)
    except Exception:
        restore_mode(source, record, captured)
        raise
    return {"start_frame": cache.frame_start, "pending": False,
            "same_frame_recalculation_accepted": False, "clearance_accepted": False}


def preview_status(source, record):
    state = _state(source, record)
    return dict(state, recalculation_supported=False, clearance_accepted=False)


def recalculate_preview(_context, _source):
    raise SkirtDirectError("Same-frame continuous Dress recalculation is not validated. Editing preview preserves your pose; reset and advance the simulation explicitly.")


def prepare_collider_fitting(_source, _rig, _record):
    raise SkirtDirectError("Direct collider refitting is not validated. Preserve the current bound Body and private collision copies.")


def export_capture(source):
    """Capture only the private plain-skin omission boundary, not vertex physics.

    The public model and Action dispatchers still reject Direct export. This
    proof permits a disposable snapshot to retain native Manual/Original skin;
    it does not prove FBX round-trip or equivalence to the Body-attached output.
    """
    record = skirt.read_record(source)
    _require(type(record) is dict, "Restore the saved Direct Dress record before snapshot capture.")
    rig = source[skirt.RIG_KEY]
    actual, cloth = validate(source, rig, record)
    surface, output = _installation(record), _overlay(source, record)
    originals = [modifier for modifier in source.modifiers if modifier != output]
    _require([modifier.type for modifier in originals] in (["ARMATURE"], ["ARMATURE", "SUBSURF"])
             and originals[0].object == rig and originals[0].show_viewport and originals[0].show_render,
             "The plain Direct snapshot must preserve its enabled original Armature and optional Subsurf.")
    relations = _deform(source, rig, record)
    proof = {"version": VERSION, "backend": BACKEND, "source": source.name,
             "rig": rig.name, "owner": record["owner"], "roles": surface["roles"],
             "record_digest": shared._digest(record), "graph_digest": shared._digest(surface),
             "source_contract": shared._source_contract(source, record),
             # Removing the selected modifier may choose a new UI active row;
             # is_active has no geometry effect and is also excluded by the
             # installed source contract. All geometry RNA stays exact.
             "original_modifiers": [shared._rna(modifier, {"is_active"}) for modifier in originals],
             "overlay": output.name, "node_group": output.node_group.name,
             "node_contract": shared._node_content(output.node_group),
             "state_raw": source[STATE_KEY], "state": _state(source, record),
             "mode_socket": surface["mode_socket"],
             "mode_value": _mode_input(output, surface["mode_socket"]).value,
             "overlay_flags": [output.show_viewport, output.show_render],
             "cloth_object": actual.name, "cloth_modifier": cloth.name,
             "cloth_flags": [cloth.show_viewport, cloth.show_render],
             "deform_contract": [{"bone": pb.name, "manual": shared._rna(manual),
                                  "physical": shared._rna(rotation)}
                                 for pb, manual, rotation in relations],
             "private_snapshot_api_only": True, "export_verified": False,
             "omission": {"simulation_baked": False, "physics_omitted": True,
                          "manual_original_preserved": True, "body_attachment_omitted": True,
                          "surface": "PLAIN_NATIVE_SKIN_V1", "final_surface_equivalent": False}}
    # Detach all nested records and normalize native tuples before transport.
    return json.loads(shared._json(proof))


def validate_snapshot(source, proof):
    _require(type(proof) is dict and type(proof.get("version")) is int
             and proof["version"] == VERSION and proof.get("backend") == BACKEND,
             "The private Direct snapshot proof has an invalid version or backend.")
    try:
        captured = shared._json(proof)
    except (TypeError, ValueError) as error:
        raise SkirtDirectError("The private Direct snapshot proof must be finite JSON data.") from error
    # Canonical JSON distinguishes bools from ints and refuses non-finite data;
    # ordinary Python dict equality would accept True in place of numeric 1.
    _require(shared._json(export_capture(source)) == captured,
             "The installed Direct graph or preview state changed since snapshot capture.")


def strip_export_snapshot(source, proof):
    """Omit the owned absolute output only in a disposable exporter snapshot.

    No artist uninstall, Body attachment transfer, cache free or physical bone
    restoration occurs. Public export remains blocked pending separate native
    model/Action validation and backend dispatch.
    """
    from pathlib import Path

    current = Path(bpy.data.filepath).resolve() if bpy.data.filepath else None
    private = (current is not None
               and ((current.name == "character.blend" and current.parent.name.startswith("cdesigner-unity-"))
                    or (current.name == "animation.blend" and current.parent.name.startswith("cdesigner-action-"))))
    _require(bpy.app.background and private and bpy.data.objects.get(source.name) == source
             and not (source.library or source.override_library or source.data.library or source.data.override_library),
             "Strip Direct output only in its local disposable background model or Action snapshot.")
    validate_snapshot(source, proof)
    record = skirt.read_record(source)
    output = _overlay(source, record)
    group = output.node_group
    cloth = _object(source, record, "CLOTH_PROXY").modifiers[-1]
    originals = tuple(modifier for modifier in source.modifiers if modifier != output)
    _require(group.users == 1 and not (group.library or group.override_library or group.use_fake_user),
             "Preserve outside or persistent users of the owned Direct output group.")
    shared._outside_users((group,), {source})
    # Every proof and ownership guard above precedes the first native write.
    flags = cloth.show_viewport, cloth.show_render
    try:
        cloth.show_viewport = cloth.show_render = False
        _require(not cloth.show_viewport and not cloth.show_render,
                 "Blender did not pause the owned Direct Cloth in the private snapshot.")
    except Exception:
        cloth.show_viewport, cloth.show_render = flags
        raise
    source.modifiers.remove(output)
    _require(group.users == 0, "The private Direct output group acquired another user during strip.")
    bpy.data.node_groups.remove(group)
    _require(tuple(source.modifiers) == originals
             and [shared._rna(modifier, {"is_active"}) for modifier in originals] == proof["original_modifiers"]
             and shared._source_contract(source, record) == proof["source_contract"],
             "The private strip changed original Direct skin, Subsurf or source data.")
    _deform(source, source[skirt.RIG_KEY], record)
    return dict(proof["omission"], source=source.name, owner=record["owner"], backend=BACKEND,
                private_snapshot_api_only=True, export_verified=False)


def preflight_remove(context, source, rig, record):
    actual, cloth = validate(source, rig, record)
    surface = _installation(record)
    _require(context.scene.name == surface["home_scene"], "Remove Direct Dress in its installation Scene.")
    shared._cache_upgrade_safe(record["physics"], cloth)
    objects = [bpy.data.objects[name] for names in surface["roles"].values() for name in names]
    group = _overlay(source, record).node_group
    body_group = _body_node(source, record)
    groups = (group, body_group)
    data = tuple(obj.data for obj in objects)
    collection = bpy.data.collections[record["physics"]["collection"]]
    allowed = set(objects) | set(data) | set(groups) | {source, rig, collection, context.scene, context.scene.collection}
    shared._scene_reference_guard(context.scene, objects + list(data) + list(groups) + [collection])
    shared._outside_users(objects + list(data) + list(groups) + [collection], allowed)
    _require(all(g.users == 1 for g in groups), "Preserve outside users of the Direct output or Body relay node group.")
    return {"source": source, "rig": rig, "owner": record["owner"], "record": shared._digest(record),
            "objects": tuple(objects), "owned_objects": tuple(objects), "owned_targets": frozenset(objects),
            "allowed_dependencies": frozenset(), "overlay": _overlay(source, record), "node_group": group,
            "node_groups": groups, "data": data,
            "collection": collection, "backup": copy.deepcopy(surface["backup"])}


def commit_remove(source, opaque):
    record = skirt.read_record(source)
    _require(opaque["source"] == source and opaque["owner"] == record["owner"]
             and opaque["record"] == shared._digest(record), "Direct removal source changed after preflight.")
    validate(source, opaque["rig"], record)
    objects, groups, overlay = opaque["objects"], opaque["node_groups"], opaque["overlay"]
    _require(groups == (_overlay(source, record).node_group, _body_node(source, record))
             and opaque["data"] == tuple(obj.data for obj in objects), "Direct helper data or node ownership changed after removal preflight.")
    source.modifiers.remove(overlay)
    data = opaque["data"]
    for obj in reversed(objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for group in groups:
        _require(group.users == 0, "A Direct graph acquired an outside user during removal.")
        bpy.data.node_groups.remove(group)
    for mesh in data:
        _require(mesh.users == 0, "Direct helper data acquired an outside user during removal.")
        bpy.data.batch_remove(ids=(mesh,))
    bpy.data.collections.remove(opaque["collection"])
    backup, rig = opaque["backup"], opaque["rig"]
    for name, constraint, mute in backup["rotations"]:
        rig.pose.bones[name].constraints[constraint].mute = mute
    holder, _id, _path = skirt.physics_control(source)
    holder["physics_influence"] = backup["influence"]
    ui = holder.id_properties_ui("physics_influence")
    ui.clear()
    if backup["influence_ui"]:
        ui.update(**backup["influence_ui"])
    source[skirt.RECORD_KEY] = backup["record_raw"]
    for key, value in ((profiles.PROFILE_KEY, backup["profile_raw"]), (STATE_KEY, backup["state_raw"])):
        if value is None:
            source.pop(key, None)
        else:
            source[key] = value
    if backup["old_cloth"] is not None:
        name, modifier, viewport, render = backup["old_cloth"]
        old = bpy.data.objects[name].modifiers[modifier]
        old.show_viewport, old.show_render = viewport, render
