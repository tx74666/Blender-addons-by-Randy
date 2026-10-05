"""Owned native Dress surface delta, with a fixed neutral Controls reference.

O = S + C - H0. S keeps the author's skin, Keys, manual pose and Original
corrections. C is a Basis cloth surface. H0 shares only the native waist/Body
and physics inputs; its skirt controls and wires have a generated neutral
baseline. No frame callback, Action copy, vertex rematching or Rest rewrite
participates in evaluation. Installation is explicit and transactional.
"""

import copy
import hashlib
import json
import math

import bpy
from mathutils import Matrix, Vector

from . import skirt_rig as skirt
from .skirt_topology import sample_fit

BACKEND = "ACTUAL_SURFACE_DELTA_V1"
ROLE_KEY = "character_designer_skirt_surface_role"
OBJECT_ROLES = frozenset({"CLOTH_PROXY", "BODY_ATTACHMENT", "NEUTRAL_RIG",
                          "NEUTRAL_WIRE", "NEUTRAL_SURFACE", "TRACKER"})
NODE_ROLE = "DELTA_NODE_GROUP"
VERSION = 1
COLLIDER_CONTRACT_VERSION = 2
PIN_GROUP = "CD Waist Pin"
BODY_MASK = "CD Body Waist Attachment"
OVERLAY = "Dress Surface Delta"
_LIMIT = 2.0e-6
_CHANNELS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale")
_TUNE_CLOTH = {"quality", "mass", "tension_stiffness", "compression_stiffness", "shear_stiffness",
               "bending_stiffness", "tension_damping", "compression_damping", "shear_damping",
               "bending_damping", "air_damping", "pin_stiffness"}
_TUNE_COLLISION = {"collision_quality", "distance_min", "use_self_collision", "self_distance_min", "self_friction"}
_NODES = {"Input": "NodeGroupInput", "Output": "NodeGroupOutput",
          "Cloth": "GeometryNodeObjectInfo", "Reference": "GeometryNodeObjectInfo",
          "Position": "GeometryNodeInputPosition", "Index": "GeometryNodeInputIndex",
          "Cloth Index": "GeometryNodeSampleIndex", "Reference Index": "GeometryNodeSampleIndex",
          "Difference": "ShaderNodeVectorMath", "Overlay": "ShaderNodeVectorMath",
          "Set Position": "GeometryNodeSetPosition"}
_LINKS = (("Input", "Geometry", "Set Position", "Geometry"),
          ("Cloth", "Geometry", "Cloth Index", "Geometry"),
          ("Reference", "Geometry", "Reference Index", "Geometry"),
          ("Position", "Position", "Cloth Index", "Value"),
          ("Position", "Position", "Reference Index", "Value"),
          ("Index", "Index", "Cloth Index", "Index"),
          ("Index", "Index", "Reference Index", "Index"),
          ("Cloth Index", "Value", "Difference", 0),
          ("Reference Index", "Value", "Difference", 1),
          ("Position", "Position", "Overlay", 0),
          ("Difference", "Vector", "Overlay", 1),
          ("Overlay", "Vector", "Set Position", "Position"),
          ("Set Position", "Geometry", "Output", "Geometry"))


class SkirtSurfaceError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise SkirtSurfaceError(message)


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _matrix(value):
    return [[float(number) for number in row] for row in value]


def _distance(first, second):
    return max(abs(first[row][column] - second[row][column]) for row in range(4) for column in range(4))


def _id(value):
    return None if value is None else {"type": value.bl_rna.identifier, "name": value.name}


def _rna(owner, exclude=()):
    """Complete writable scalar/pointer RNA; never skip a failed native read."""
    result = {}
    for prop in owner.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type" or name in exclude or prop.is_readonly or prop.type == "COLLECTION":
            continue
        value = getattr(owner, name)
        if prop.type == "POINTER":
            if value is not None and not isinstance(value, bpy.types.ID):
                continue  # e.g. Modifier settings, explicitly captured below.
            value = _id(value)
        elif getattr(prop, "is_array", False):
            value = _matrix(value) if hasattr(value, "row") else list(value)
        elif prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            raise SkirtSurfaceError("Unsupported native setting: " + name)
        if isinstance(value, set):
            value = sorted(value)
        result[name] = value
    return result


def _drivers(obj):
    curves = obj.animation_data.drivers if obj.animation_data else ()
    return [{"curve": _rna(curve), "keys": [_rna(point) for point in curve.keyframe_points],
             "samples": [_rna(point) for point in curve.sampled_points],
             "modifiers": [{"rna": _rna(modifier), "collections": {
                 prop.identifier: [_rna(item) for item in getattr(modifier, prop.identifier)]
                 for prop in modifier.bl_rna.properties if prop.type == "COLLECTION"}}
                 for modifier in curve.modifiers], "driver": _rna(curve.driver),
             "variables": [{"rna": _rna(variable), "targets": [_rna(target) for target in variable.targets]}
                           for variable in curve.driver.variables]} for curve in curves]


def _topology(mesh):
    return {"count": len(mesh.vertices), "edges": [list(edge.vertices) for edge in mesh.edges],
            "faces": [list(face.vertices) for face in mesh.polygons],
            "loops": [[loop.vertex_index, loop.edge_index] for loop in mesh.loops]}


def _groups(obj):
    return {"names": [group.name for group in obj.vertex_groups],
            "weights": [[[weight.group, float(weight.weight)] for weight in vertex.groups]
                        for vertex in obj.data.vertices]}


def _basis(source):
    keys = source.data.shape_keys
    _require(keys is None or keys.use_relative, "Use relative Dress Shape Keys before installing surface physics.")
    return [list(point.co) for point in keys.reference_key.data] if keys else [list(vertex.co) for vertex in source.data.vertices]


def _source_contract(source, record):
    modifiers = [modifier for modifier in source.modifiers
                 if modifier.name != record.get("physics", {}).get("surface", {}).get("overlay")]
    return {"mesh": _id(source.data), "frame": _frame(source), "basis": _digest(_basis(source)),
            "topology": _digest(_topology(source.data)), "groups": _digest(_groups(source)),
            "modifiers": [_rna(modifier, {"is_active"}) for modifier in modifiers]}


def _frame(obj):
    return {"parent": _id(obj.parent), "type": obj.parent_type, "bone": obj.parent_bone,
            "inverse": _matrix(obj.matrix_parent_inverse), "basis": _matrix(obj.matrix_basis)}


def _manual_names(record):
    controls = record["controls"]
    return ([controls[level] for level in ("mid", "hem")]
            + [name for entry in controls["chains"] for name in entry.values()]
            + [name for chain in record["chains"] for name in chain["manual"]])


def _owned_bones(record):
    return set.union(*skirt._bone_collection_layout(record))


def _ancestors(rig, record):
    names = []
    bone = rig.data.bones[record["controls"]["waist"]].parent
    while bone:
        _require(bone.name not in _owned_bones(record), "Dress has an unexpected upstream generated parent.")
        names.append(bone.name)
        bone = bone.parent
    return names


def _neutral(bone):
    bone.location = (0., 0., 0.)
    bone.rotation_euler = (0., 0., 0.)
    bone.rotation_quaternion = (1., 0., 0., 0.)
    bone.rotation_axis_angle = (0., 0., 1., 0.)
    bone.scale = (1., 1., 1.)


def _clear(collection):
    for item in list(collection):
        collection.remove(item)


def _rest(rig, names):
    from . import skirt_original_mode
    return skirt_original_mode._rest(rig, names)


def _tag(obj, source, record, role, *, data=True):
    _require(role in OBJECT_ROLES or role == NODE_ROLE, "Unknown Dress surface role.")
    for key in list(obj.keys()):
        del obj[key]
    obj[skirt.OWNER_KEY], obj[skirt.SOURCE_KEY], obj[ROLE_KEY] = record["owner"], source, role
    if data and getattr(obj, "data", None):
        for key in list(obj.data.keys()):
            del obj.data[key]
        obj.data[skirt.OWNER_KEY], obj.data[skirt.SOURCE_KEY], obj.data[ROLE_KEY] = record["owner"], source, role


def _object(record, role, source):
    names = record["physics"]["surface"]["roles"].get(role)
    _require(isinstance(names, list) and len(names) == 1, "A Dress surface role is missing or ambiguous: " + role)
    obj = bpy.data.objects.get(names[0])
    _require(obj is not None and obj.get(ROLE_KEY) == role and obj.get(skirt.OWNER_KEY) == record["owner"]
             and obj.get(skirt.SOURCE_KEY) == source and obj.name in record["owned_objects"]
             and not (obj.library or obj.override_library), "Restore Dress surface ownership: " + role)
    return obj


def _copy_scalars(source, target, exclude=()):
    for prop in source.bl_rna.properties:
        if prop.identifier in exclude or prop.identifier == "rna_type" or prop.is_readonly:
            continue
        if prop.type in {"BOOLEAN", "INT", "FLOAT", "ENUM", "STRING"}:
            value = getattr(source, prop.identifier)
            setattr(target, prop.identifier, list(value) if getattr(prop, "is_array", False) else value)


def _world_follow(owner, target, bone="", name="Dress input follow"):
    constraint = owner.constraints.new("COPY_TRANSFORMS")
    constraint.name, constraint.target, constraint.subtarget = name, target, bone
    constraint.owner_space = constraint.target_space = "WORLD"
    constraint.mix_mode, constraint.remove_target_shear = "REPLACE", False
    constraint.influence, constraint.mute, constraint.active = 1., False, False
    return constraint


def _follow_proof(constraint, target, bone):
    _require(constraint.type == "COPY_TRANSFORMS" and constraint.target == target and constraint.subtarget == bone
             and constraint.owner_space == constraint.target_space == "WORLD"
             and constraint.mix_mode == "REPLACE" and not constraint.remove_target_shear
             and not constraint.mute and abs(constraint.influence - 1.) <= 1.e-7,
             "Restore the exact native Dress upstream input.")


class _Transaction:
    def __init__(self, context, source):
        self.context, self.source = context, source
        self.raw = source.get(skirt.RECORD_KEY)
        self.objects, self.data, self.collections, self.nodes = [], [], [], []
        self.copied_keys = []
        self.targets, self.overlay = [], None
        self.context_state = skirt._context_state(context)
        self.frame, self.subframe = context.scene.frame_current, context.scene.frame_subframe
        self.rigs = {}
        self.flags = []
        self.original_rig = source.get(skirt.RIG_KEY)
        self.pose_channels = {bone.name: {name: list(getattr(bone, name)) for name in _CHANNELS}
                              for bone in self.original_rig.pose.bones}
        self.rig_basis = self.original_rig.matrix_basis.copy()
        self.input_rigs = {self.original_rig: (self.pose_channels, self.rig_basis)}
        self.keys = {}
        self.remember_keys(source.data.shape_keys)

    def remember_keys(self, keys):
        if keys is not None and keys not in self.keys:
            self.keys[keys] = (keys.eval_time, [(block, block.value, block.mute) for block in keys.key_blocks])

    def track(self, obj):
        self.objects.append(obj)
        if obj.data and obj.data not in self.data:
            self.data.append(obj.data)
        return obj

    def copy(self, obj, name, collection, *, data=True):
        result = obj.copy()
        _require(result != obj, "Dress evaluation requires an independent Object copy.")
        # Enrol each returned native ID before the next allocation/assignment
        # or link can fail. The original shared data is never enrolled.
        self.objects.append(result)
        result.name = name
        if data:
            copied = obj.data.copy()
            _require(copied != obj.data and not copied.library and not copied.override_library,
                     "Dress evaluation requires independent local data.")
            self.data.append(copied)
            key = getattr(copied, "shape_keys", None)
            if key is not None:
                _require(key != getattr(obj.data, "shape_keys", None) and not key.library and not key.override_library,
                         "Dress evaluation requires an independent local Key copy.")
                # Capture before assigning/linking the Mesh: even a detached
                # Blender 5.1 Key can retain a phantom user after clearing.
                self.copied_keys.append((copied, key.name, key.as_pointer()))
                _require(bpy.data.shape_keys.get(key.name) == key
                         and bpy.data.user_map(subset={key}).get(key, set()) == {copied},
                         "The copied Dress Key has unexpected native users; preserve it.")
            result.data = copied
        collection.objects.link(result)
        return result

    def forget_copied_key(self, data):
        # Called only after the independent-copy helper proved removal. A
        # later artist Key may legitimately reuse that native display name.
        self.copied_keys = [receipt for receipt in self.copied_keys if receipt[0] != data]

    def rollback_copied_keys(self):
        failures, retained = [], []
        for receipt in self.copied_keys:
            _data, name, pointer = receipt
            try:
                key = bpy.data.shape_keys.get(name)
                if key is None:
                    continue
                _require(key.as_pointer() == pointer and not key.library and not key.override_library
                         and not bpy.data.user_map(subset={key}).get(key, set()),
                         "A copied Dress Key changed or acquired an outside native user; it was preserved.")
                bpy.data.batch_remove(ids=(key,))
                _require(bpy.data.shape_keys.get(name) is None,
                         "Blender did not remove the captured copied Dress Key.")
            except Exception as error:
                retained.append(receipt)
                failures.append(str(error))
        self.copied_keys = retained
        _require(not failures, "Dress copied Key rollback needs recovery: " + "; ".join(failures))

    def disable(self, cloth):
        self.flags.append((cloth, cloth.show_viewport, cloth.show_render))
        cloth.show_viewport = cloth.show_render = False

    def rest(self, rig):
        if rig not in self.rigs:
            self.rigs[rig] = rig.data.pose_position
        if rig not in self.objects and rig not in self.input_rigs:
            self.input_rigs[rig] = ({bone.name: {field: list(getattr(bone, field)) for field in _CHANNELS}
                                    for bone in rig.pose.bones}, rig.matrix_basis.copy())
        rig.data.pose_position = "REST"

    def restore_context(self):
        for rig, state in self.rigs.items():
            try:
                if bpy.data.objects.get(rig.name) is rig:
                    rig.data.pose_position = state
            except ReferenceError:
                continue  # Only the transaction's deleted independent rig.
        self.context.scene.frame_set(self.frame, subframe=self.subframe)
        for rig, (all_channels, basis) in self.input_rigs.items():
            for name, channels in all_channels.items():
                for field, values in channels.items():
                    setattr(rig.pose.bones[name], field, values)
            rig.matrix_basis = basis
        for keys, (time, blocks) in self.keys.items():
            keys.eval_time = time
            for block, value, mute in blocks:
                block.value, block.mute = value, mute
        skirt._restore_context(self.context, self.context_state)
        self.context.view_layer.update()

    def enable_last(self):
        for modifier, viewport, render in self.flags:
            modifier.show_viewport, modifier.show_render = viewport, render
        self.flags.clear()

    def rollback(self):
        for constraint, target in self.targets:
            constraint.target = target
        if self.overlay is not None and self.overlay in self.source.modifiers.values():
            self.source.modifiers.remove(self.overlay)
        if self.raw is None:
            self.source.pop(skirt.RECORD_KEY, None)
        else:
            self.source[skirt.RECORD_KEY] = self.raw
        # Do not retain modifier RNA pointers owned by objects about to go.
        owned_modifiers = {modifier.as_pointer() for obj in self.objects for modifier in obj.modifiers}
        self.flags = [entry for entry in self.flags if entry[0].as_pointer() not in owned_modifiers]
        for obj in reversed(self.objects):
            if bpy.data.objects.get(obj.name) is obj:
                bpy.data.objects.remove(obj, do_unlink=True)
        for group in reversed(self.nodes):
            if bpy.data.node_groups.get(group.name) is group and group.users == 0:
                bpy.data.node_groups.remove(group)
        for data in reversed(self.data):
            if data.users == 0:
                bpy.data.batch_remove(ids=(data,))
        for collection in reversed(self.collections):
            if bpy.data.collections.get(collection.name) is collection:
                bpy.data.collections.remove(collection)
        self.rollback_copied_keys()


def _points(obj, context):
    graph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
    try:
        points = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        _require(all(math.isfinite(value) for point in points for value in point), "Nonfinite Dress native coordinates.")
        return points
    finally:
        evaluated.to_mesh_clear()


def _bind(context, obj, modifier):
    skirt._activate(context, obj)
    with context.temp_override(object=obj, active_object=obj):
        result = bpy.ops.object.surfacedeform_bind(modifier=modifier.name)
    _require("FINISHED" in result and modifier.is_bound, "Blender could not bind the owned Dress surface. No fallback was applied.")


def _nojump(before, after, context):
    _require(len(before) == len(after) and before, "Dress binding changed exact vertex indices.")
    error = max((first - second).length for first, second in zip(before, after))
    _require(error <= _LIMIT and error * context.scene.unit_settings.scale_length <= _LIMIT,
             "Native Dress binding changed the neutral surface; the installation was rolled back.")


def _generated_wire(source, rig, record, index):
    chain = record["chains"][index]
    wire = bpy.data.objects.get(chain["curve"])
    _require(wire is not None and wire.type == "CURVE" and wire.get(skirt.OWNER_KEY) == record["owner"]
             and wire.get(skirt.SOURCE_KEY) == source and wire.data.users == 1 and wire.data.shape_keys is None
             and wire.animation_data is None and wire.data.animation_data is None and not wire.constraints,
             "Preserve custom or missing Dress wire data before installing surface physics.")
    expected_parent = ("BONE", record["shared"]["anchor"]) if skirt.is_shared(record) else ("OBJECT", "")
    _require(wire.parent == rig and (wire.parent_type, wire.parent_bone) == expected_parent
             and len(wire.data.splines) == 1, "Restore the generated Dress wire attachment.")
    spline = wire.data.splines[0]
    _require(spline.type == "NURBS" and len(spline.points) == 3 and not spline.use_cyclic_u
             and spline.order_u == 3 and spline.use_endpoint_u, "A generated Dress neutral wire is not available.")
    angle = math.tau * index / record["chain_count"]
    expected = [sample_fit(record["fit"], t, angle) for t in (0., .5, 1.)]
    _require(all((Vector(point.co[:3]) - Vector(value)).length <= 1.e-6
                 and abs(point.co[3] - 1.) <= 1.e-7 for point, value in zip(spline.points, expected)),
             "The Dress wire Basis was edited. Recalibrate its neutral baseline explicitly.")
    names = (record["controls"]["waist"], record["controls"]["chains"][index]["mid"],
             record["controls"]["chains"][index]["hem"])
    _require(len(wire.modifiers) == 3 and all(modifier.type == "HOOK" and modifier.object == rig
             and modifier.subtarget == name and modifier.strength == 1. and modifier.falloff_type == "NONE"
             and list(modifier.vertex_indices) == [point] for point, (modifier, name) in enumerate(zip(wire.modifiers, names))),
             "Restore the exact three generated neutral Dress Hooks.")
    space = Matrix(record["shared"]["space_matrix"]) if skirt.is_shared(record) else Matrix.Identity(4)
    _require(all(_distance(modifier.matrix_inverse, rig.data.bones[name].matrix_local.inverted() @ space) <= 2.e-6
                 and modifier.center.length <= 1.e-7 for modifier, name in zip(wire.modifiers, names))
             and all(abs(point.radius - 1.) <= 1.e-7 and abs(point.tilt) <= 1.e-7 for point in spline.points),
             "The Dress Hook bind or wire baseline was edited. Recalibrate explicitly.")
    return wire


def _reference(context, source, rig, record, tracker, destination, tx):
    reference = tx.copy(rig, "CD Neutral Dress Rig · " + source.name, destination)
    _tag(reference, source, record, "NEUTRAL_RIG")
    # This owned evaluator always needs constraints, even when the artist's
    # original armature happens to display Rest during installation.
    reference.data.pose_position = "POSE"
    reference.animation_data_clear()
    reference.data.animation_data_clear()
    _clear(reference.constraints)
    # _activate updates the native graph before entering Edit Mode. Dispose of
    # copied Body/IK dependencies before that first evaluation, not after their
    # target bones have been pruned from this independent owned rig.
    for bone in reference.pose.bones:
        _clear(bone.constraints)
        _neutral(bone)
        bone.custom_shape = None
    _world_follow(reference, rig, name="Dress rig world input")
    owned = _owned_bones(record)
    ancestors = _ancestors(rig, record)
    # Keep only the proved service subset. Copying a character's full Body/IK
    # graph into a permanent reference would waste evaluation and record size.
    keep = owned | set(ancestors)
    skirt._activate(context, reference, "EDIT")
    for bone in list(reference.data.edit_bones):
        if bone.name not in keep:
            reference.data.edit_bones.remove(bone)
    bpy.ops.object.mode_set(mode="OBJECT")
    _require(set(reference.data.bones.keys()) == keep and _rest(reference, keep) == _rest(rig, keep),
             "The independent Dress reference changed its exact Rest subset.")
    # Object/Body input follows are native. No saved/copy Action can become
    # stale when the author changes Body Controls or poses Original directly.
    for name in ancestors + [record["controls"]["waist"]]:
        _world_follow(reference.pose.bones[name], rig, name, "Dress waist input" if name in owned else "Dress Body input")
    wires = []
    for index, chain in enumerate(record["chains"]):
        original = _generated_wire(source, rig, record, index)
        wire = tx.copy(original, "CD Neutral " + original.name, destination)
        _tag(wire, source, record, "NEUTRAL_WIRE")
        wire.parent = reference
        wire.parent_type, wire.parent_bone = original.parent_type, original.parent_bone
        wire.matrix_parent_inverse, wire.matrix_basis = original.matrix_parent_inverse.copy(), original.matrix_basis.copy()
        for old, new in zip(original.modifiers, wire.modifiers):
            new.object = reference
            new.matrix_inverse, new.center = old.matrix_inverse.copy(), old.center.copy()
        wires.append(wire)
        old_spline = rig.pose.bones[chain["manual"][-1]].constraints.get("Skirt manual wire")
        _require(old_spline is not None and old_spline.type == "SPLINE_IK" and old_spline.target == original,
                 "Restore the generated Dress manual spline target.")
        spline = reference.pose.bones[chain["manual"][-1]].constraints.new("SPLINE_IK")
        spline.name = "Skirt manual wire"
        spline.target = wire
        spline.chain_count = record["segment_count"]
        spline.use_even_divisions = True
        spline.use_curve_radius = False
        spline.y_scale_mode, spline.xz_scale_mode = "FIT_CURVE", "NONE"
        for segment, (manual, physics, deform) in enumerate(zip(chain["manual"], chain["phys"], chain["def"])):
            old_aim = rig.pose.bones[physics].constraints[0]
            aim = reference.pose.bones[physics].constraints.new("DAMPED_TRACK")
            _copy_scalars(old_aim, aim)
            aim.target, aim.subtarget = tracker, old_aim.subtarget
            copy_transform = reference.pose.bones[deform].constraints.new("COPY_TRANSFORMS")
            copy_transform.name = "Skirt manual pose"
            copy_transform.target, copy_transform.subtarget = reference, manual
            copy_transform.owner_space = copy_transform.target_space = "LOCAL"
            copy_transform.mix_mode = "REPLACE"
            rotation = reference.pose.bones[deform].constraints.new("COPY_ROTATION")
            old_rotation = rig.pose.bones[deform].constraints.get("Skirt physics delta")
            _copy_scalars(old_rotation, rotation, {"influence", "mute", "active"})
            rotation.target, rotation.subtarget, rotation.mute = reference, physics, False
            rotation.owner_space = rotation.target_space = "LOCAL"
            rotation.mix_mode = "BEFORE"
            driver = rotation.driver_add("influence").driver
            driver.type = "AVERAGE"
            variable = driver.variables.new()
            variable.name, variable.type = "physics", "SINGLE_PROP"
            _control, identifier, path = skirt.physics_control(source)
            variable.targets[0].id, variable.targets[0].data_path = identifier, path
    reference.hide_render = True
    reference.hide_set(True)
    return reference, wires, ancestors


def _fixed_mesh(source, name, destination, tx, record, role):
    obj = tx.copy(source, name, destination)
    _tag(obj, source, record, role)
    obj.animation_data_clear()
    _clear(obj.constraints)
    basis = _basis(source)
    if obj.data.shape_keys:
        from .mesh_copy import clear_copied_shape_keys
        clear_copied_shape_keys(source, obj)
        tx.forget_copied_key(obj.data)
    obj.data.animation_data_clear()
    for vertex, value in zip(obj.data.vertices, basis):
        vertex.co = value
    obj.show_only_shape_key = False
    return obj


def _body(context, body, rig, source, record, collection, tx):
    _require(body is not None and body.type == "MESH" and not (body.library or body.data.library or body.override_library)
             and body.parent_type == "OBJECT",
             "Choose the registered local Body mesh with the same Main Rig.")
    modifiers = list(body.modifiers)
    body_rig = rig if skirt.is_shared(record) else rig.parent
    _require([modifier.type for modifier in modifiers] in (["ARMATURE"], ["ARMATURE", "SUBSURF"])
             and body_rig is not None and body_rig.type == "ARMATURE" and body.parent in (None, body_rig)
             and modifiers[0].object == body_rig and modifiers[0].use_vertex_groups
             and not modifiers[0].use_bone_envelopes and not modifiers[0].vertex_group
             and not modifiers[0].use_multi_modifier and all(modifier.show_viewport and modifier.show_render for modifier in modifiers),
             "Use the registered Body's native Armature and optional Subsurf for Dress collision.")
    forbidden = {group.index for group in body.vertex_groups if group.name in _owned_bones(record)}
    _require(not any(weight.group in forbidden and weight.weight > 0 for vertex in body.data.vertices for weight in vertex.groups),
             "The Body depends on Dress deform bones; collision feedback is refused.")
    # Mesh/Keys are a live read-only reference. Never put ownership tags on
    # artist data, copy current Key values, or clear the Body's animation.
    clone = tx.copy(body, "CD Body Collision · " + source.name, collection, data=False)
    _tag(clone, source, record, "BODY_ATTACHMENT", data=False)
    clone.animation_data_clear()
    _clear(clone.constraints)
    _world_follow(clone, body, name="Dress Body object input")
    saved_active = [modifier.is_active for modifier in clone.modifiers]
    clone.modifiers.new("Dress Body collision", "COLLISION")
    clone.modifiers[-1].is_active = False
    for modifier, active in zip(clone.modifiers, saved_active):
        modifier.is_active = active
    from . import skirt_physics as physics
    templates = [bpy.data.objects[name].collision for name in record["physics"]["colliders"]]
    _require(templates and all(_rna(template) == _rna(templates[0]) for template in templates),
             "The owned collider settings differ; choose explicit collision tuning before upgrading.")
    _copy_scalars(templates[0], clone.collision)
    clone.hide_render = True
    clone.hide_select = True  # Its Mesh is the artist Body's live shared data.
    clone.hide_set(False)
    return clone


def _node_group(actual, reference, source, record, tx):
    group = bpy.data.node_groups.new("CD Dress Surface Delta · " + source.name, "GeometryNodeTree")
    tx.nodes.append(group)
    _tag(group, source, record, NODE_ROLE, data=False)
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    for name, kind in _NODES.items():
        node = group.nodes.new(kind)
        node.name, node.mute = name, False
    group.nodes["Output"].is_active_output = True
    for name, target in (("Cloth", actual), ("Reference", reference)):
        node = group.nodes[name]
        node.transform_space = "RELATIVE"
        node.inputs["Object"].default_value = target
        node.inputs["As Instance"].default_value = False
    for name in ("Cloth Index", "Reference Index"):
        node = group.nodes[name]
        node.data_type, node.domain, node.clamp = "FLOAT_VECTOR", "POINT", False
    group.nodes["Difference"].operation = "SUBTRACT"
    group.nodes["Overlay"].operation = "ADD"
    group.nodes["Set Position"].inputs["Selection"].default_value = True
    group.nodes["Set Position"].inputs["Offset"].default_value = (0., 0., 0.)
    for first, output, second, input_socket in _LINKS:
        group.links.new(group.nodes[first].outputs[output], group.nodes[second].inputs[input_socket])
    return group


def _node_content(group):
    return {"interface": [{"name": item.name, "type": item.socket_type, "direction": item.in_out}
                          for item in group.interface.items_tree if item.item_type == "SOCKET"],
            "nodes": {node.name: {"type": node.bl_idname, "rna": _rna(node, {"location", "width", "height", "select", "label"}),
                                  "inputs": {str(index): _rna(socket, {"hide", "hide_value"})
                                             for index, socket in enumerate(node.inputs)}} for node in group.nodes},
            "links": [[link.from_node.name, list(link.from_node.outputs).index(link.from_socket),
                       link.to_node.name, list(link.to_node.inputs).index(link.to_socket), link.is_valid]
                      for link in group.links]}


def _node_verify(group, actual, neutral):
    _require(len(group.nodes) == len(_NODES) and set(group.nodes.keys()) == set(_NODES)
             and all(group.nodes[name].bl_idname == kind and not group.nodes[name].mute for name, kind in _NODES.items()),
             "Restore the eleven exact native Dress overlay nodes.")
    expected = []
    for first, output, second, input_socket in _LINKS:
        from_socket = group.nodes[first].outputs[output]
        to_socket = group.nodes[second].inputs[input_socket]
        expected.append((first, list(group.nodes[first].outputs).index(from_socket), second,
                         list(group.nodes[second].inputs).index(to_socket)))
    native = [(link.from_node.name, list(link.from_node.outputs).index(link.from_socket),
               link.to_node.name, list(link.to_node.inputs).index(link.to_socket)) for link in group.links]
    _require(sorted(native) == sorted(expected) and all(link.is_valid for link in group.links),
             "Restore the exact S + C - H0 native Dress links.")
    for name, target in (("Cloth", actual), ("Reference", neutral)):
        node = group.nodes[name]
        _require(node.transform_space == "RELATIVE" and node.inputs["Object"].default_value == target
                 and not node.inputs["As Instance"].default_value, "Restore the exact relative Dress overlay targets.")
    for name in ("Cloth Index", "Reference Index"):
        node = group.nodes[name]
        _require(node.data_type == "FLOAT_VECTOR" and node.domain == "POINT" and not node.clamp,
                 "Dress surface sampling must retain unclamped exact vertex indices.")
    _require(group.nodes["Difference"].operation == "SUBTRACT" and group.nodes["Overlay"].operation == "ADD"
             and group.nodes["Set Position"].inputs["Selection"].default_value
             and list(group.nodes["Set Position"].inputs["Offset"].default_value) == [0., 0., 0.],
             "Restore the S + C - H0 position formula.")


def _graph(rig):
    driven = {curve.data_path for curve in rig.animation_data.drivers} if rig.animation_data else set()
    return {bone.name: [_rna(constraint, {"influence"} if constraint.path_from_id() + ".influence" in driven else ())
                        for constraint in bone.constraints] for bone in rig.pose.bones}


def _helper_contract(obj, *, dynamic_pin=False):
    groups = _groups(obj) if obj.type == "MESH" else None
    if dynamic_pin:
        index = obj.vertex_groups[PIN_GROUP].index
        groups["weights"] = [[weight for weight in weights if weight[0] != index] for weights in groups["weights"]]
    result = {"type": obj.type, "data": _id(obj.data), "frame": _frame(obj),
              "constraints": [_rna(constraint) for constraint in obj.constraints], "groups": groups,
              "modifiers": [_rna(modifier, {"show_viewport", "show_render", "is_active"}) for modifier in obj.modifiers]}
    if obj.type == "MESH":
        result["mesh"] = _digest({"topology": _topology(obj.data), "coords": [list(vertex.co) for vertex in obj.data.vertices]})
    elif obj.type == "CURVE":
        result["curve"] = {"data": _rna(obj.data), "splines": [{"rna": _rna(spline), "points": [_rna(point) for point in spline.points]}
                            for spline in obj.data.splines]}
        result["hook_indices"] = [list(modifier.vertex_indices) for modifier in obj.modifiers if modifier.type == "HOOK"]
    elif obj.type == "ARMATURE":
        result["rest"] = _rest(obj, obj.data.bones.keys())
        result["graph"] = _graph(obj)
        result["drivers"] = _drivers(obj)
        result["channels"] = {bone.name: {"mode": bone.rotation_mode, "values": {name: list(getattr(bone, name)) for name in _CHANNELS}}
                              for bone in obj.pose.bones}
    return result


def _collider_contract(obj):
    """Keep the generated collider graph/topology fixed, allowing vertex fitting.

    Surface v1 remains readable. Its old combined topology/coordinate hash is
    migrated only after an exact proof, by the explicit Select Colliders action.
    Native finite-coordinate and connected, positive closed-volume checks still
    run separately; this does not grant permission to reweight or rebuild a mesh.
    """
    graph = _helper_contract(obj)
    graph.pop("mesh")
    return {"version": COLLIDER_CONTRACT_VERSION, "graph": graph,
            "topology": _digest(_topology(obj.data))}


def _collider_contract_matches(obj, saved):
    if isinstance(saved, dict) and "version" in saved:
        _require(type(saved["version"]) is int and saved["version"] == COLLIDER_CONTRACT_VERSION,
                 "The saved Dress collider contract version is unsupported.")
        return _collider_contract(obj) == saved
    # Legacy surface v1 keeps its exact combined mesh hash until an explicit,
    # validated fitting preparation can save the separate topology contract.
    return isinstance(saved, dict) and _helper_contract(obj) == saved


def _collider_inventory_contract(surface, names, body):
    saved = surface.get("colliders")
    _require(isinstance(saved, dict) and set(saved) == set(names[:-1])
             and all(isinstance(item, dict) for item in saved.values()),
             "Restore the exact independent Dress collider contract inventory.")
    _require(all("version" not in item or (type(item["version"]) is int
                 and item["version"] == COLLIDER_CONTRACT_VERSION) for item in saved.values()),
             "The saved Dress collider contract version is unsupported.")
    versions = {item.get("version") for item in saved.values()}
    _require(versions in ({None}, {COLLIDER_CONTRACT_VERSION}),
             "Finish the explicit Dress collider contract migration before continuing.")
    if versions == {COLLIDER_CONTRACT_VERSION}:
        _require(body.hide_select, "Keep the shared Body collision input unselectable; fit only the independent pelvis and thigh meshes.")
    return saved


def prepare_collider_fitting(source, rig, record):
    """Read-only preflight; the UI commits the returned record transactionally."""
    actual, cloth = validate(source, rig, record)
    cache = cloth.point_cache
    _require(not cache.is_baked and not cache.is_baking and not cache.use_external
             and not record["physics"].get("baked_range"),
             "Reset Dress before fitting its colliders. Finish any bake and disable external cache first.")
    # An ordinary unsealed disk cache is an owned Reset input, not an external
    # cache. Do not require disabling it or change/cache-delete it here.
    body = _object(record, "BODY_ATTACHMENT", source)
    names = record["physics"]["colliders"]
    _require(names[-1] == body.name, "Restore the exact Dress collision collection.")
    colliders = [bpy.data.objects[name] for name in names[:-1]]
    updated = copy.deepcopy(record)
    updated["physics"]["surface"]["colliders"] = {
        name: _collider_contract(bpy.data.objects[name]) for name in names[:-1]}
    return updated, colliders, body


def _cache_upgrade_safe(physics, cloth):
    cache = cloth.point_cache
    _require(not cache.is_baked and not cache.is_baking and not cache.use_external and not cache.use_disk_cache
             and not physics.get("baked_range"),
             "Reset Dress explicitly and disable its disk cache before upgrading a baked, external or disk-cached setup.")


def _other_cloth_preflight(context, allowed=()):
    """Binding changes scene time; do not evaluate unknown artist caches."""
    blocked = [obj.name + ": " + modifier.name for obj in context.scene.objects for modifier in obj.modifiers
               if modifier.type == "CLOTH" and (modifier.show_viewport or modifier.show_render)
               and modifier not in allowed]
    _require(not blocked, "Disable other active Cloth before installing Dress surface physics: " + ", ".join(blocked))


def _installation_context_preflight(context, source, rig, body):
    """Refuse live author editing/preview before any binding time change."""
    inputs = [source, rig, context.view_layer.objects.active]
    if body is not None:
        inputs.append(body)
        inputs.extend(modifier.object for modifier in body.modifiers
                      if modifier.type == "ARMATURE" and modifier.object is not None)
    _require(not any(obj is not None and obj.mode == "EDIT" for obj in inputs),
             "Finish Edit Mode on the active object and Dress/Body inputs before installing surface physics.")
    from . import hair_wiggle_adapter
    # status reads the session source name. A stale preview still must refuse
    # installation without dereferencing that source or stopping its handlers.
    _require(getattr(hair_wiggle_adapter, "_SESSION", None) is None,
             "Stop the active Hair live preview before installing Dress surface physics.")
    _require(not hair_wiggle_adapter.status(context)["active"],
             "Stop the active Hair live preview before installing Dress surface physics.")


def _cloth_contract(cloth):
    return {"settings": _rna(cloth.settings, _TUNE_CLOTH),
            "collision": _rna(cloth.collision_settings, _TUNE_COLLISION),
            "effectors": _rna(cloth.settings.effector_weights, {"gravity"})}


def _outside_users(objects, allowed):
    users = bpy.data.user_map(subset=set(objects))
    for obj in objects:
        unexpected = set(users.get(obj, ())) - allowed
        labels = sorted(getattr(getattr(item, "bl_rna", None), "identifier", type(item).__name__)
                        + " '" + getattr(item, "name", "<unnamed>") + "'" for item in unexpected)
        _require(not unexpected, "Preserve an external user of Dress helper " + obj.name + ": " + ", ".join(labels))


def _scene_reference_guard(scene, endpoints):
    """Allow scene membership, not other references aggregated into its ID.

    BaseObject and root Collection membership are legitimate weak links. ID
    properties, drivers, Keying Sets, camera/bake settings and embedded native
    node inputs are separate artist references and must be preserved.
    """
    from . import skirt_shared_rig
    endpoints = set(endpoints)
    for path, reference in skirt_shared_rig._id_ref_paths(dict(scene.items())):
        _require(reference not in endpoints, "Scene artist data uses a Dress helper: " + ".".join(path))
    if scene.animation_data:
        _require(not any(target.id in endpoints for curve in scene.animation_data.drivers
                         for variable in curve.driver.variables for target in variable.targets),
                 "A Scene driver uses a Dress helper.")
    for sets in (getattr(scene, "keying_sets", ()), getattr(scene, "keying_sets_all", ())):
        _require(not any(path.id in endpoints for item in sets for path in item.paths),
                 "A Scene Keying Set uses a Dress helper.")
    seen = set()

    def visit(value, path=()):
        pointer = value.as_pointer()
        if pointer in seen:
            return
        seen.add(pointer)
        if callable(getattr(value, "items", None)):
            try:
                properties = dict(value.items())
            except TypeError as error:
                # bpy_struct exposes items() even when this native type has no
                # IDProperty storage. Only that exact capability error is safe.
                if str(error) != "bpy_struct.items(): this type doesn't support IDProperties":
                    raise
                properties = {}
            for key_path, reference in skirt_shared_rig._id_ref_paths(properties):
                _require(reference not in endpoints,
                         "Scene artist data uses a Dress helper: " + ".".join(path + key_path))
        for prop in value.bl_rna.properties:
            name = prop.identifier
            if name in {"rna_type", "id_data"} or prop.type not in {"POINTER", "COLLECTION"}:
                continue
            # These are exactly the accepted scene-membership paths. Do not
            # recurse through unrelated non-embedded IDs (user_map proves them).
            if value == scene and name in {"collection", "objects"}:
                continue
            identifier = value.bl_rna.identifier
            if ((identifier == "ViewLayer" and name == "objects")
                    or (identifier == "LayerCollection" and name == "collection")):
                continue
            child = getattr(value, name)
            if callable(child):
                child = value.get(name) if hasattr(value, "get") else None
            if child is None:
                continue
            child_path = path + (name,)
            if isinstance(child, bpy.types.ID):
                _require(child not in endpoints, "Scene setting uses a Dress helper: " + ".".join(child_path))
                if getattr(child, "is_embedded_data", False):
                    visit(child, child_path)
            elif prop.type == "POINTER" and hasattr(child, "bl_rna"):
                visit(child, child_path)
            elif prop.type == "COLLECTION":
                for index, item in enumerate(child):
                    if isinstance(item, bpy.types.ID):
                        _require(item not in endpoints, "Scene setting references a Dress helper: " + ".".join(child_path))
                    elif hasattr(item, "bl_rna"):
                        visit(item, child_path + (str(index),))

    visit(scene)


def _collection_parents(collection):
    candidates = set(bpy.data.collections) | {scene.collection for scene in bpy.data.scenes}
    return {parent for parent in candidates if parent.children.get(collection.name) == collection}


def _home_memberships(record, objects):
    surface = _surface_record(record)
    home = bpy.data.scenes.get(surface.get("home_scene", ""))
    collection = bpy.data.collections.get(record["physics"]["collection"])
    _require(home is not None and collection is not None and _collection_parents(collection) == {home.collection},
             "Restore the unique installation Scene and Dress collision collection parent.")
    for obj in objects:
        expected = collection if obj.get(ROLE_KEY) == "BODY_ATTACHMENT" else home.collection
        _require(set(obj.users_collection) == {expected} and home.objects.get(obj.name) == obj,
                 "Restore the exact home Scene membership of Dress helper: " + obj.name)
    return home, collection


def _old_endpoint_users(context, proxy, collection, rig, record):
    home = context.scene
    destination = rig.users_collection[0] if rig.users_collection else home.collection
    _require(set(proxy.users_collection) == {destination} and home.objects.get(proxy.name) == proxy
             and _collection_parents(collection) == {home.collection},
             "Restore the generated legacy Dress helper destination and unique home Scene collision parent.")
    _scene_reference_guard(home, (proxy, collection))
    allowed_constraints = {rig.pose.bones[name].constraints[0] for chain in record["chains"] for name in chain["phys"]}
    for obj in bpy.data.objects:
        constraints = list(obj.constraints)
        if obj.type == "ARMATURE":
            constraints += [constraint for bone in obj.pose.bones for constraint in bone.constraints]
        for constraint in constraints:
            for prop in constraint.bl_rna.properties:
                if prop.type == "POINTER" and getattr(constraint, prop.identifier) == proxy:
                    _require(constraint in allowed_constraints and prop.identifier == "target",
                             "The old Dress proxy is used by an artist constraint.")
        for key in obj.keys():
            _require(obj.get(key) != proxy and obj.get(key) != collection,
                     "The old Dress physics endpoint is referenced by artist data.")
        if obj.animation_data:
            _require(not any(target.id == proxy or target.id == collection for curve in obj.animation_data.drivers
                             for variable in curve.driver.variables for target in variable.targets),
                     "The old Dress physics endpoint is referenced by an artist driver.")
    # Native user_map attributes both embedded-root links and ViewLayer Base
    # weak links to the Scene ID, including objects in descendant collections.
    _outside_users([proxy], {destination, rig, home})
    _outside_users([collection], {home.collection, home, proxy})


def _surface_record(record):
    physics = record.get("physics")
    surface = physics.get("surface") if isinstance(physics, dict) else None
    _require(physics is not None and physics.get("backend") == BACKEND and isinstance(surface, dict)
             and type(surface.get("version")) is int and surface["version"] == VERSION and surface.get("owner") == record["owner"],
             "The saved Dress surface backend is incomplete or belongs to another setup.")
    roles = surface.get("roles")
    _require(isinstance(roles, dict) and set(roles) == OBJECT_ROLES
             and all(isinstance(names, list) and names and all(isinstance(name, str) and name for name in names)
                     for names in roles.values()), "Restore the exact Dress surface role inventory.")
    names = [name for values in roles.values() for name in values]
    _require(len(names) == len(set(names)), "A Dress surface object is assigned to more than one role.")
    return surface


def _overlay(source, record):
    surface = _surface_record(record)
    modifier = source.modifiers.get(surface["overlay"])
    group = bpy.data.node_groups.get(surface["node_group"])
    _require(modifier is not None and modifier.type == "NODES" and modifier.node_group == group
             and group is not None and group.get(ROLE_KEY) == NODE_ROLE and group.get(skirt.OWNER_KEY) == record["owner"]
             and group.get(skirt.SOURCE_KEY) == source and group.users == 1,
             "Restore the unique owned Dress surface overlay and node group.")
    _require(_node_content(group) == surface["node_contract"], "Preserve the edited Dress node graph before continuing.")
    return modifier


def validate(source, rig, record):
    """Read-only proof. Native coefficients, author pose/Keys and cache are live."""
    from . import skirt_physics as physics
    surface = _surface_record(record)
    _require(source.get(skirt.RIG_KEY) == rig and record["source"] == source.name and record["rig"] == rig.name,
             "The Dress source and Main Rig identities changed.")
    roles = surface["roles"]
    _require(len(roles["NEUTRAL_WIRE"]) == record["chain_count"], "Restore the complete neutral Dress wire set.")
    objects = []
    for role, names in roles.items():
        for name in names:
            obj = bpy.data.objects.get(name)
            _require(obj is not None and obj.get(ROLE_KEY) == role and obj.get(skirt.OWNER_KEY) == record["owner"]
                     and obj.get(skirt.SOURCE_KEY) == source and name in record["owned_objects"]
                     and not (obj.library or obj.override_library), "Restore an owned Dress surface helper: " + name)
            objects.append(obj)
    _home_memberships(record, objects)
    _require(_source_contract(source, record) == surface["source_contract"],
             "The Dress Basis, topology, groups or original modifiers changed. Recalibrate explicitly.")
    overlay = _overlay(source, record)
    actual, tracker, reference, neutral, body = (_object(record, role, source) for role in
        ("CLOTH_PROXY", "TRACKER", "NEUTRAL_RIG", "NEUTRAL_SURFACE", "BODY_ATTACHMENT"))
    _require(reference.data.pose_position == "POSE", "Keep the owned neutral Dress reference in Pose position.")
    _node_verify(overlay.node_group, actual, neutral)
    _require(record["physics"]["proxy"] == actual.name and surface["tracker"] == tracker.name
             and [modifier.type for modifier in actual.modifiers] == ["ARMATURE", "SURFACE_DEFORM", "CLOTH"]
             and [modifier.type for modifier in tracker.modifiers] == ["SURFACE_DEFORM"]
             and [modifier.type for modifier in neutral.modifiers] == ["ARMATURE"],
             "Restore the Dress surface modifier order and physical cache endpoint.")
    cloth = actual.modifiers[2]
    for obj in objects:
        if obj == body:
            continue  # Shared artist Body data has a separate live proof.
        _require(obj.data is not None and obj.data.get(ROLE_KEY) == obj.get(ROLE_KEY)
                 and obj.data.get(skirt.OWNER_KEY) == record["owner"] and obj.data.get(skirt.SOURCE_KEY) == source
                 and obj.data.users == 1 and not (obj.data.library or obj.data.override_library)
                 and (obj.type != "MESH" or obj.data.shape_keys is None),
                 "Restore the unshared generated Dress surface data.")
        _require(_helper_contract(obj, dynamic_pin=(obj == actual)) == surface["contracts"][obj.name],
                 "Preserve the edited Dress surface helper before continuing: " + obj.name)
        if obj != reference:
            _require(obj.animation_data is None and obj.data.animation_data is None,
                     "Preserve custom Dress helper animation before continuing.")
    _require(reference.animation_data is not None and reference.animation_data.action is None
             and not reference.animation_data.nla_tracks and reference.data.animation_data is None,
             "The neutral Dress reference must use native inputs, never an independent Action.")
    _require(actual.modifiers[1].is_bound and actual.modifiers[1].target == body
             and actual.modifiers[1].vertex_group == BODY_MASK and actual.modifiers[1].strength == 1.
             and actual.modifiers[1].use_sparse_bind and not actual.modifiers[1].invert_vertex_group
             and tracker.modifiers[0].is_bound and tracker.modifiers[0].target == actual,
             "Restore the two bound native Dress surface targets.")
    _require(_topology(actual.data) == _topology(neutral.data) == _topology(source.data),
             "Dress exact-index correspondence changed.")
    _require(actual.modifiers[0].object == rig and neutral.modifiers[0].object == reference
             and actual.vertex_groups[0].name == record["controls"]["waist"]
             and actual.modifiers[0].use_vertex_groups and not actual.modifiers[0].use_bone_envelopes
             and not actual.modifiers[0].use_deform_preserve_volume,
             "Restore the single-waist physical input and fixed Basis neutral skin.")
    pin = record["physics"].get("pin_weights")
    _require(isinstance(pin, list) and len(pin) == len(actual.data.vertices)
             and all(type(value) in (int, float) and math.isfinite(value) and 0. <= value <= 1. for value in pin)
             and all(abs(pin[index] - 1.) <= 1.e-6 for index in record["fit"]["rings"][0]),
             "The actual Dress pin weights are incomplete or invalid.")
    native_pin = physics._weights(actual)[PIN_GROUP]
    _require(physics._same_weights(native_pin, {index: value for index, value in enumerate(pin) if value}),
             "Restore the saved actual Dress pin weights.")
    _require(cloth.settings.vertex_group_mass == surface["pin_group"] == PIN_GROUP
             and cloth.name == surface["cloth_modifier"] and cloth.settings.rest_shape_key is None
             and cloth.settings.effector_weights.collection is None,
             "Restore the native Dress Cloth pin and fixed Rest input.")
    _require(_cloth_contract(cloth) == surface["cloth_contract"],
             "Preserve custom Cloth groups, Rest or collision settings before continuing.")
    upstream = bpy.data.objects.get(surface["body"])
    _require(upstream is not None and body.data == upstream.data and _groups(body) == _groups(upstream)
             and len(body.constraints) == 1 and len(body.modifiers) == len(upstream.modifiers) + 1
             and body.modifiers[-1].type == "COLLISION", "Restore the live read-only registered Body collision reference.")
    _follow_proof(body.constraints[0], upstream, "")
    _require(_frame(body) == surface["body_frame"]
             and [_rna(constraint) for constraint in body.constraints] == surface["body_constraints"]
             and _rna(body.collision) == surface["body_collision"],
             "Restore the exact native Body collision object and its ownership input.")
    _require([_rna(modifier, {"is_active"}) for modifier in body.modifiers[:-1]]
             == [_rna(modifier, {"is_active"}) for modifier in upstream.modifiers]
             and _digest(_topology(upstream.data)) == surface["body_topology"]
             and _digest(_groups(upstream)) == surface["body_groups"],
             "Body topology, group mapping or native skin settings changed. Recalibrate Dress explicitly.")
    collection = bpy.data.collections.get(record["physics"]["collection"])
    collider_names = record["physics"]["colliders"]
    _require(collection is not None and collection.get(skirt.OWNER_KEY) == record["owner"]
             and not collection.children and set(collection.objects.keys()) == set(collider_names)
             and actual.name not in collection.objects and cloth.collision_settings.collection == collection
             and collider_names[-1] == body.name, "Restore the exact Dress collision collection.")
    saved_colliders = _collider_inventory_contract(surface, collider_names, body)
    for name in collider_names[:-1]:
        collider = bpy.data.objects.get(name)
        physics._owned_mesh(collider, source, record)
        _require(_collider_contract_matches(collider, saved_colliders[name]),
                 "Restore the generated pelvis and thigh collider topology, weights and binding. "
                 "Use Select Colliders before fitting an unchanged legacy surface setup.")
        physics._closed_collider(collider)
    _require(len(reference.constraints) == 1, "The neutral Dress object has an extra input.")
    _follow_proof(reference.constraints[0], rig, "")
    for name in surface["upstream_bones"] + [record["controls"]["waist"]]:
        _require(len(reference.pose.bones[name].constraints) == 1, "The neutral Dress waist or Body input changed.")
        _follow_proof(reference.pose.bones[name].constraints[0], rig, name)
    _require(_rest(rig, _owned_bones(record) | set(surface["upstream_bones"])) == surface["source_rest"],
             "The Dress Rest or upstream Body hierarchy changed. Recalibrate explicitly.")
    for chain_index, chain in enumerate(record["chains"]):
        for segment, name in enumerate(chain["phys"]):
            constraints = list(rig.pose.bones[name].constraints)
            _require(len(constraints) == 1 and constraints[0].type == "DAMPED_TRACK"
                     and constraints[0].target == tracker and constraints[0].subtarget == f"CD Sample {chain_index:02d}.{segment:02d}"
                     and constraints[0].track_axis == "TRACK_Y" and not constraints[0].mute
                     and constraints[0].influence == 1., "Restore the exact Dress physics tracker targets.")
        for name in chain["def"]:
            copy_transform, rotation = reference.pose.bones[name].constraints
            _require(copy_transform.mix_mode == "REPLACE" and not copy_transform.mute and not rotation.mute,
                     "The neutral reference must exclude author Original corrections.")
    physics._physics_bone_animation(rig, record)
    _deform_proof(source, rig, record)
    cache = cloth.point_cache
    _require(not cache.use_external and not cache.is_baking and cache.frame_start <= cache.frame_end,
             "Finish the local Dress bake before continuing.")
    holder, _identifier, _path = skirt.physics_control(source)
    influence = holder.get("physics_influence")
    _require(type(influence) in (int, float) and influence in (0., 1.),
             "The Dress surface requires an Automatic or Manual endpoint.")
    enabled = influence == 1.
    _require(overlay.show_viewport == overlay.show_render == enabled,
             "The Dress surface mode differs from its native physics control.")
    return actual, cloth


def _deform_proof(source, rig, record):
    from . import skirt_original_mode
    correction = skirt_original_mode._corrections(source, record)
    owner = rig.get(skirt.ORIGINAL_DISPLAY_OWNER_KEY)
    working = skirt._ORIGINAL_SESSION_KEY in rig or (isinstance(owner, bpy.types.Object) and skirt._ORIGINAL_SESSION_KEY in owner)
    rotations = []
    names = []
    for chain in record["chains"]:
        for name, manual, physics in zip(chain["def"], chain["manual"], chain["phys"]):
            constraints = list(rig.pose.bones[name].constraints)
            _require(len(constraints) == 2, "Preserve extra Dress deform constraints before continuing.")
            copy_transform, rotation = constraints
            modes = {"REPLACE", "BEFORE_FULL"} if working else {"BEFORE_FULL" if name in correction["bones"] else "REPLACE"}
            _require(copy_transform.name == "Skirt manual pose" and copy_transform.type == "COPY_TRANSFORMS"
                     and rotation.name == "Skirt physics delta" and rotation.type == "COPY_ROTATION"
                     and copy_transform.target == rotation.target == rig and copy_transform.subtarget == manual
                     and rotation.subtarget == physics and copy_transform.owner_space == copy_transform.target_space
                     == rotation.owner_space == rotation.target_space == "LOCAL"
                     and copy_transform.mix_mode in modes and not copy_transform.remove_target_shear
                     and copy_transform.influence == 1. and rotation.mix_mode == "BEFORE" and rotation.euler_order == "AUTO"
                     and all((rotation.use_x, rotation.use_y, rotation.use_z))
                     and not any((rotation.invert_x, rotation.invert_y, rotation.invert_z)),
                     "Restore the exact author Dress manual and physics deform graph.")
            _require(working or not (copy_transform.mute or rotation.mute), "Restore Dress Controls constraints.")
            names.append(name)
            rotations.append(rotation)
    skirt_original_mode._constraint_animation(rig, source, names, rotations)


def capture_mode(source, record):
    rig = source.get(skirt.RIG_KEY)
    validate(source, rig, record)
    overlay = _overlay(source, record)
    return {"source": source, "modifier": overlay, "group": overlay.node_group,
            "owner": record["owner"], "flags": (overlay.show_viewport, overlay.show_render)}


def set_mode(source, record, mode):
    _require(mode in {"AUTOMATIC", "MANUAL"}, "Unknown Dress surface mode.")
    # The coordinator has already changed the physics holder. Structural proof
    # must not assume old/new endpoint flags agree during this transaction.
    overlay = _overlay(source, record)
    enabled = mode == "AUTOMATIC"
    overlay.show_viewport = overlay.show_render = enabled


def restore_mode(source, record, state):
    modifier = state["modifier"]
    _require(state["source"] == source and source.modifiers.get(modifier.name) == modifier
             and modifier.node_group == state["group"] and state["group"].get(skirt.OWNER_KEY) == state["owner"],
             "The captured Dress mode endpoint changed during rollback.")
    modifier.show_viewport, modifier.show_render = state["flags"]


def install(context, source, *, body=None, capability=None):
    """Explicit legacy-to-surface transaction; sealed caches are never discarded."""
    from . import skirt_physics as physics, skirt_motion_profiles as profiles, skirt_motion_tuning as tuning
    skirt._require_controls_for_setup(source)
    record = skirt.read_record(source)
    _require(record is not None, "Build Dress controls before installing surface physics.")
    rig = source[skirt.RIG_KEY]
    _installation_context_preflight(context, source, rig, body)
    physics.backend(record)  # Unknown saved backends never become legacy implicitly.
    profile = profiles.read(source, record)
    _require(capability is None or capability in profiles.CAPABILITIES, "Unknown Dress generation intent.")
    _require(profile is None or capability is None or profile["capability"] == capability
             or (profile["capability"] == "MANUAL" and capability == "BOTH"),
             "Preserve the existing Dress generation choice before installing physics.")
    if record.get("physics", {}).get("backend") == BACKEND:
        validate(source, rig, record)
        tuning.initialize(source, capability=capability, context=context)
        return record
    original_raw = source[skirt.RECORD_KEY]
    original_profile = source.get(profiles.PROFILE_KEY)
    holder, _identifier, _path = skirt.physics_control(source)
    tuning._influence_editable(rig, _path)
    original_influence = holder.get("physics_influence", 0.)
    _require(type(original_influence) in (int, float) and original_influence in (0., 1.),
             "Choose a Manual or Automatic endpoint before installing Dress surface physics; preserve intermediate blends.")
    original_influence_ui = holder.id_properties_ui("physics_influence").as_dict()
    allowed_cloth = ()
    if record.get("physics"):
        existing_proxy, existing_cloth = physics._verify_physics_graph(source, rig, record)
        _cache_upgrade_safe(record["physics"], existing_cloth)
        allowed_cloth = (existing_cloth,)
    _other_cloth_preflight(context, allowed_cloth)
    _require(body is not None and body.type == "MESH", "Select the registered Body mesh before installing Dress surface physics.")
    generated_legacy = not record.get("physics")
    tx = _Transaction(context, source)
    old_proxy, old_collection = None, None
    success = False
    commit_started = False
    try:
        if generated_legacy:
            record = physics.add_physics(context, source)
            old_proxy = bpy.data.objects[record["physics"]["proxy"]]
            old_collection = bpy.data.collections[record["physics"]["collection"]]
        old_proxy, old_cloth = physics._verify_physics_graph(source, rig, record)
        _cache_upgrade_safe(record["physics"], old_cloth)
        _require(source.mode in {"OBJECT", "POSE"} and [modifier.type for modifier in source.modifiers]
                 in (["ARMATURE"], ["ARMATURE", "SUBSURF"]) and source.modifiers[0].object == rig
                 and not source.constraints and source.data.users == 1 and source.animation_data is None
                 and source.data.animation_data is None,
                 "Use the original Dress native Armature and optional Subsurf before upgrading.")
        skin = source.modifiers[0]
        _require(skin.use_vertex_groups and not skin.use_bone_envelopes and not skin.use_multi_modifier and not skin.vertex_group,
                 "Preserve custom Dress envelope or multi-modifier skin before upgrading.")
        deform_names = {name for chain in record["chains"] for name in chain["def"]} | {record["controls"]["waist"]}
        extra = {group.index for group in source.vertex_groups if group.name in rig.data.bones
                 and rig.data.bones[group.name].use_deform and group.name not in deform_names}
        _require(not any(weight.group in extra and weight.weight > 0 for vertex in source.data.vertices for weight in vertex.groups),
                 "Dress has additional Body deform weights. Its neutral surface needs an explicit calibration.")
        points = _basis(source)
        _require(len(points) == len(record["fit"]["vertices"])
                 and all((Vector(point) - Vector(value)).length <= 1.e-6
                         for point, value in zip(points, record["fit"]["vertices"])),
                 "The fitted Dress Basis changed. Recalibrate before installing surface physics.")
        rings = record["fit"]["rings"]
        _require(sorted(index for ring in rings for index in ring) == list(range(len(points))),
                 "The saved Dress ring topology does not cover exact source indices.")
        for index in range(record["chain_count"]):
            _generated_wire(source, rig, record, index)
        old_collection = bpy.data.collections[record["physics"]["collection"]]
        _old_endpoint_users(context, old_proxy, old_collection, rig, record)
        _require(all(prop.identifier == "vertex_group_mass" or not getattr(old_cloth.settings, prop.identifier)
                     for prop in old_cloth.settings.bl_rna.properties if prop.identifier.startswith("vertex_group_"))
                 and old_cloth.settings.rest_shape_key is None and old_cloth.settings.effector_weights.collection is None,
                 "Preserve custom Cloth group or Rest inputs before upgrading Dress.")
        destination = context.scene.collection
        collection = bpy.data.collections.new("CD Surface Collision · " + source.name)
        tx.collections.append(collection)
        destination.children.link(collection)
        collection[skirt.OWNER_KEY] = record["owner"]
        for name in record["physics"]["colliders"]:
            collection.objects.link(bpy.data.objects[name])
        updated = copy.deepcopy(record)
        tracker = tx.copy(old_proxy, "CD Cloth Tracker · " + source.name, destination)
        _tag(tracker, source, updated, "TRACKER")
        _clear(tracker.modifiers)
        actual = _fixed_mesh(source, "CD Actual Dress Cloth · " + source.name, destination, tx, updated, "CLOTH_PROXY")
        _clear(actual.modifiers)
        _clear(actual.vertex_groups)
        actual.parent = rig
        actual.parent_type, actual.parent_bone = "OBJECT", ""
        actual.matrix_parent_inverse = Matrix(updated["shared"]["space_matrix"]) if skirt.is_shared(updated) else Matrix.Identity(4)
        actual.matrix_basis = Matrix.Identity(4)
        waist = updated["controls"]["waist"]
        actual.vertex_groups.new(name=waist).add(list(range(len(points))), 1., "REPLACE")
        armature = actual.modifiers.new("Dress waist attachment", "ARMATURE")
        armature.object = rig
        pin = actual.vertex_groups.new(name=PIN_GROUP)
        profile = profiles.read(source, record)
        values = profile["settings"] if profile else profiles.DEFAULTS
        pin_values = profiles.surface_pin_weights(values, updated["fit"], record["physics"]["rows"], record["physics"]["columns"])
        for index, value in enumerate(pin_values):
            if value:
                pin.add([index], value, "REPLACE")
        mask = actual.vertex_groups.new(name=BODY_MASK)
        _require(BODY_MASK not in rig.data.bones and PIN_GROUP not in rig.data.bones,
                 "A physics mask conflicts with a deform bone name.")
        mask.add(rings[0], 1., "REPLACE")
        collider = _body(context, body, rig, source, updated, collection, tx)
        tx.remember_keys(body.data.shape_keys)
        attachment = actual.modifiers.new("Dress Body waist attachment", "SURFACE_DEFORM")
        attachment.target, attachment.vertex_group = collider, mask.name
        attachment.use_sparse_bind, attachment.strength = True, 1.
        cloth = actual.modifiers.new("Dress actual surface physics", "CLOTH")
        _copy_scalars(old_cloth.settings, cloth.settings)
        _copy_scalars(old_cloth.collision_settings, cloth.collision_settings)
        _copy_scalars(old_cloth.settings.effector_weights, cloth.settings.effector_weights)
        cloth.settings.vertex_group_mass = pin.name
        cloth.collision_settings.collection = collection
        _copy_scalars(old_cloth.point_cache, cloth.point_cache, {"index", "name", "filepath", "use_external"})
        cloth.point_cache.use_external = False
        tx.disable(old_cloth)
        tx.disable(cloth)
        reference, wires, ancestors = _reference(context, source, rig, updated, tracker, destination, tx)
        neutral = _fixed_mesh(source, "CD Neutral Dress Surface · " + source.name, destination, tx, updated, "NEUTRAL_SURFACE")
        while len(neutral.modifiers) > 1:
            neutral.modifiers.remove(neutral.modifiers[-1])
        neutral.modifiers[0].object = reference
        transfer = tracker.modifiers.new("Dress actual surface tracker", "SURFACE_DEFORM")
        transfer.target = actual
        # Bind without simulation, at the exact shared Rest chart. Do not copy
        # current animated transforms into Rest or author channels.
        tx.rest(rig)
        tx.rest(reference)
        tx.rest(body.modifiers[0].object)
        context.scene.frame_set(0, subframe=0.)
        context.view_layer.update()
        attachment.show_viewport = attachment.show_render = False
        rest_before = _points(actual, context)
        attachment.show_viewport = attachment.show_render = True
        _bind(context, actual, attachment)
        _nojump(rest_before, _points(actual, context), context)
        tracker_before = _points(tracker, context)
        _bind(context, tracker, transfer)
        _nojump(tracker_before, _points(tracker, context), context)
        tx.restore_context()
        attachment.show_viewport = attachment.show_render = False
        pose_before = _points(actual, context)
        attachment.show_viewport = attachment.show_render = True
        _nojump(pose_before, _points(actual, context), context)
        group = _node_group(actual, neutral, source, updated, tx)
        source_active = [modifier.is_active for modifier in source.modifiers]
        overlay = source.modifiers.new(OVERLAY, "NODES")
        tx.overlay = overlay
        overlay.node_group = group
        source.modifiers.move(len(source.modifiers) - 1, 1)
        overlay.is_active = False
        for modifier, active in zip((modifier for modifier in source.modifiers if modifier != overlay), source_active):
            modifier.is_active = active
        enabled = profile is None or profile["mode"] == "AUTOMATIC"
        overlay.show_viewport = overlay.show_render = enabled
        holder["physics_influence"] = float(enabled)
        for chain in updated["chains"]:
            for name in chain["phys"]:
                aim = rig.pose.bones[name].constraints[0]
                tx.targets.append((aim, aim.target))
                aim.target = tracker
        roles = {"CLOTH_PROXY": [actual.name], "BODY_ATTACHMENT": [collider.name],
                 "NEUTRAL_RIG": [reference.name], "NEUTRAL_WIRE": [wire.name for wire in wires],
                 "NEUTRAL_SURFACE": [neutral.name], "TRACKER": [tracker.name]}
        surface = {"version": VERSION, "owner": record["owner"], "home_scene": context.scene.name,
                   "roles": roles, "tracker": tracker.name,
                   "pin_group": pin.name, "cloth_modifier": cloth.name, "overlay": overlay.name,
                   "node_group": group.name, "body": body.name, "body_topology": _digest(_topology(body.data)),
                   "body_groups": _digest(_groups(body)), "upstream_bones": ancestors,
                   "body_frame": _frame(collider), "body_constraints": [_rna(constraint) for constraint in collider.constraints],
                   "body_collision": _rna(collider.collision),
                   "source_rest": _rest(rig, _owned_bones(record) | set(ancestors)),
                   "cloth_contract": _cloth_contract(cloth),
                   "contracts": {}, "colliders": {name: _collider_contract(bpy.data.objects[name]) for name in record["physics"]["colliders"]},
                   "limits": "Native Basis delta; collision clearance is not guaranteed. Recalibrate after Basis/Rest/topology changes."}
        updated["physics"].update(backend=BACKEND, proxy=actual.name, colliders=record["physics"]["colliders"] + [collider.name],
                                  collection=collection.name, pin_weights=pin_values, baked_range=None, surface=surface)
        updated["owned_objects"] = [name for name in updated["owned_objects"] if name != old_proxy.name]
        updated["owned_objects"].extend(name for names in roles.values() for name in names)
        updated["owned_collections"] = [name for name in updated.get("owned_collections", ()) if name != old_collection.name] + [collection.name]
        surface["source_contract"] = _source_contract(source, updated)
        surface["node_contract"] = _node_content(group)
        for role, names in roles.items():
            for name in names:
                if role != "BODY_ATTACHMENT":
                    surface["contracts"][name] = _helper_contract(bpy.data.objects[name], dynamic_pin=(role == "CLOTH_PROXY"))
        skirt.write_record(source, updated)
        for obj in tx.objects:
            obj.hide_render = True
            obj.hide_set(True)
        tx.restore_context()
        validate(source, rig, updated)
        # Profile adoption/promotion belongs to this installation transaction.
        # A failed native endpoint or metadata write must precede deletion of
        # the old graph so the complete Add Physics operation can roll back.
        tuning.initialize(source, capability=capability, context=context)
        validate(source, rig, updated)
        _require(old_proxy.data.users == 1 and len(old_proxy.users_collection) == 1
                 and set(old_collection.objects.keys()) == set(record["physics"]["colliders"]),
                 "The old Dress physics helper acquired an external user.")
        # Destruction is last: all candidate/record/context checks succeeded.
        tx.flags = [entry for entry in tx.flags if entry[0] == cloth]
        old_data = old_proxy.data
        commit_started = True
        bpy.data.objects.remove(old_proxy, do_unlink=True)
        bpy.data.meshes.remove(old_data)
        bpy.data.collections.remove(old_collection)
        success = True
        return updated
    except Exception as error:
        if commit_started:
            raise SkirtSurfaceError("Dress installation reached its native deletion boundary and was only partially committed. "
                                    "Use Blender Undo or restore the saved scene; deleted cache data was not reconstructed. "
                                    + str(error)) from error
        tx.rollback()
        source[skirt.RECORD_KEY] = original_raw
        holder["physics_influence"] = original_influence
        ui = holder.id_properties_ui("physics_influence")
        ui.clear()
        if original_influence_ui:
            ui.update(**original_influence_ui)
        _require(ui.as_dict() == original_influence_ui, "The Dress physics property UI could not be fully restored.")
        if original_profile is None:
            source.pop(profiles.PROFILE_KEY, None)
        else:
            source[profiles.PROFILE_KEY] = original_profile
        if generated_legacy:
            # The legacy creation was part of this transaction. It is safe to
            # remove only its newly proven IDs, not rebuild artist controls.
            if old_proxy is not None and bpy.data.objects.get(old_proxy.name) is old_proxy:
                tx.flags.clear()  # That newly generated native cache is removed.
                for chain in record["chains"]:
                    for name in chain["phys"]:
                        _clear(rig.pose.bones[name].constraints)
                for name in record["physics"]["colliders"] + [old_proxy.name]:
                    obj = bpy.data.objects.get(name)
                    if obj is not None:
                        data = obj.data
                        bpy.data.objects.remove(obj, do_unlink=True)
                        if data.users == 0:
                            bpy.data.meshes.remove(data)
                if old_collection is not None and bpy.data.collections.get(old_collection.name) is old_collection:
                    bpy.data.collections.remove(old_collection)
        if isinstance(error, (SkirtSurfaceError, physics.SkirtPhysicsError, skirt.SkirtRigError)):
            raise
        raise SkirtSurfaceError("Dress surface installation was rolled back: " + str(error)) from error
    finally:
        try:
            if not success and not commit_started and (tx.objects or tx.rigs or tx.flags or generated_legacy):
                tx.restore_context()
        finally:
            # Last operation: re-enable native Cloth with no frame/evaluation
            # call afterwards, including a context-restoration failure.
            tx.enable_last()


def export_capture(source):
    record = skirt.read_record(source)
    _require(record is not None, "The exported Dress record is missing.")
    validate(source, source[skirt.RIG_KEY], record)
    surface = _surface_record(record)
    return {"version": VERSION, "backend": BACKEND, "source": source.name, "rig": record["rig"],
            "owner": record["owner"], "roles": copy.deepcopy(surface["roles"]),
            "record_digest": _digest(record), "graph_digest": _digest(surface),
            "overlay": surface["overlay"], "node_group": surface["node_group"]}


def validate_snapshot(source, proof):
    _require(isinstance(proof, dict) and proof.get("version") == VERSION and proof.get("backend") == BACKEND,
             "The Dress export surface proof is invalid.")
    actual = export_capture(source)
    _require(actual == proof, "The Dress surface graph changed since export capture.")


def strip_export_snapshot(source, proof):
    validate_snapshot(source, proof)
    record = skirt.read_record(source)
    modifier = _overlay(source, record)
    group = modifier.node_group
    source.modifiers.remove(modifier)
    _require(group.users == 0, "The owned exported Dress graph acquired an external user.")
    bpy.data.node_groups.remove(group)


def preflight_remove(context, source, rig, record):
    validate(source, rig, record)
    surface = _surface_record(record)
    objects = [bpy.data.objects[name] for names in surface["roles"].values() for name in names]
    overlay = _overlay(source, record)
    collection = bpy.data.collections[record["physics"]["collection"]]
    home, collection = _home_memberships(record, objects)
    _require(context.scene == home, "Remove Dress surface data from its saved installation Scene.")
    endpoints = objects + [collection]
    _scene_reference_guard(home, endpoints)
    allowed = set(objects) | {source, rig, overlay.node_group, collection, home, home.collection}
    _outside_users(endpoints, allowed)
    for obj in objects:
        if obj.get(ROLE_KEY) != "BODY_ATTACHMENT":
            _require(obj.data.users == 1, "Preserve externally shared Dress surface data before removing it.")
    _require(overlay.node_group.users == 1, "Preserve externally shared Dress surface nodes before removing them.")
    reference = _object(record, "NEUTRAL_RIG", source)
    dependencies = frozenset((reference, name, reference.pose.bones[name].constraints[0].name)
                             for name in surface["upstream_bones"] + [record["controls"]["waist"]])
    return {"source": source, "rig": rig, "owner": record["owner"], "record": _digest(record),
            "objects": tuple(objects), "owned_objects": tuple(objects), "owned_targets": frozenset(objects),
            "allowed_dependencies": dependencies, "overlay": overlay,
            "node_group": overlay.node_group, "proof": export_capture(source)}


def commit_remove(source, opaque):
    _require(opaque["source"] == source, "Dress removal source changed.")
    validate_snapshot(source, opaque["proof"])
    modifier, group = opaque["overlay"], opaque["node_group"]
    source.modifiers.remove(modifier)
    # Objects go before their shared target data; the Body mesh is never ours.
    owned_data = []
    for obj in opaque["objects"]:
        _require(bpy.data.objects.get(obj.name) is obj, "An owned Dress removal pointer changed.")
        if obj.get(ROLE_KEY) != "BODY_ATTACHMENT":
            owned_data.append(obj.data)
    for obj in reversed(opaque["objects"]):
        bpy.data.objects.remove(obj, do_unlink=True)
    _require(group.users == 0, "An owned Dress node graph has an external user.")
    bpy.data.node_groups.remove(group)
    for data in owned_data:
        _require(data.users == 0, "An owned Dress helper data block has an external user.")
        bpy.data.batch_remove(ids=(data,))
