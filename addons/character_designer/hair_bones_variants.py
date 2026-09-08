"""Independent, native hair rig versions for comparing strand partitions.

The source mesh and every earlier rig remain intact.  A version owns its mesh,
small attachment rig, animation data, and mirror reference in one collection.
Only the chosen version is shown; returning to the source restores its original
visibility.  Evaluation uses ordinary Blender bones, weights and constraints.
"""

import json

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import hair_bones_rig as rig
from . import hair_bones_mirror as mirror_controls


SOURCE_KEY = "character_designer_hair_variant_source"
MODE_KEY = "character_designer_hair_variant_mode"
MESH_KEY = "character_designer_hair_variant_mesh"
ARMATURE_KEY = "character_designer_hair_variant_armature"
INDEX_KEY = "character_designer_hair_variant_index"
VISIBILITY_KEY = "character_designer_hair_variant_source_visibility"
ATTACHMENT_KEY = "character_designer_hair_variant_attachment"
VERSION_KEY = "character_designer_hair_variant_version"
_GROUP_KEYS = (
    "character_designer_hair_groups", "character_designer_hair_group_source",
    "character_designer_hair_group_id", "character_designer_hair_group_guide",
)


class HairVariantError(ValueError):
    """A version could not be made without changing the original hair."""


def source_for(obj):
    """Resolve a source mesh or a generated mesh/rig back to its source."""
    if not isinstance(obj, bpy.types.Object):
        return None
    source = obj.get(SOURCE_KEY)
    if isinstance(source, bpy.types.Object) and source.type == "MESH":
        return source
    return obj if obj.type == "MESH" else None


def variants_for(source):
    """Return owned version collections in creation order."""
    source = source_for(source)
    if source is None:
        return ()
    return tuple(sorted((collection for collection in bpy.data.collections
                         if collection.get(VERSION_KEY) == 1 and collection.get(SOURCE_KEY) is source),
                        key=lambda item: (int(item.get(INDEX_KEY, 0)), item.name)))


def _visibility(obj):
    return {"hidden": bool(obj.hide_get()), "viewport": bool(obj.hide_viewport),
            "render": bool(obj.hide_render), "select": bool(obj.hide_select)}


def _set_visibility(obj, state):
    obj.hide_viewport = state["viewport"]
    obj.hide_render = state["render"]
    obj.hide_select = state["select"]
    obj.hide_set(state["hidden"])


def _initial_visibility(source):
    previous = variants_for(source)
    if not previous:
        return _visibility(source)
    try:
        value = json.loads(previous[0][VISIBILITY_KEY])
        if set(value) != {"hidden", "viewport", "render", "select"}:
            raise ValueError()
        return {key: bool(flag) for key, flag in value.items()}
    except (KeyError, TypeError, ValueError):
        raise HairVariantError("The source visibility record is missing; restore the source manually first.") from None


def _visibility_snapshot(source):
    collections = variants_for(source)
    objects = {source}
    for collection in collections:
        objects.update(item for item in (collection.get(MESH_KEY), collection.get(ARMATURE_KEY))
                       if isinstance(item, bpy.types.Object))
    return ({item: _visibility(item) for item in objects},
            {item: (item.hide_viewport, item.hide_render) for item in collections})


def _restore_visibility(state):
    objects, collections = state
    for collection, (viewport, render) in collections.items():
        collection.hide_viewport, collection.hide_render = viewport, render
    for obj, visibility in objects.items():
        _set_visibility(obj, visibility)


def _context_snapshot(context, source, attachment):
    state = rig._context_snapshot(context, source, attachment)
    if state["mode"] == "EDIT_CURVE":
        state["curve_selection"] = tuple(
            tuple((bool(point.select_control_point), bool(point.select_left_handle),
                   bool(point.select_right_handle), bool(point.hide))
                  for point in spline.bezier_points) if spline.type == "BEZIER" else
            tuple((bool(point.select), bool(point.hide)) for point in spline.points)
            for spline in context.object.data.splines)
    return state


def _restore_context(context, source, state):
    rig._restore_context(context, source, state)
    if state["mode"] == "EDIT_MESH":
        # BMesh select-mode assignment can flush a sparse vertex selection.
        # Restore mode and hidden flags first, then the saved selection.
        bm = bmesh.from_edit_mesh(source.data)
        saved = state["mesh_selection"]
        bm.select_mode = saved["select_mode"]
        domains = ((bm.faces, "faces"), (bm.edges, "edges"), (bm.verts, "vertices"))
        for domain, name in domains:
            domain.ensure_lookup_table()
            for element, (_, hidden) in zip(domain, saved[name]):
                element.hide = hidden
        for domain, name in domains:
            for element, (selected, _) in zip(domain, saved[name]):
                element.select = selected
        bm.select_history.clear()
        lookup = {"V": bm.verts, "E": bm.edges, "F": bm.faces}
        for kind, index in saved["history"]:
            bm.select_history.add(lookup[kind][index])
        bm.faces.active = bm.faces[saved["active_face"]] if saved["active_face"] >= 0 else None
        bmesh.update_edit_mesh(source.data, loop_triangles=False, destructive=False)
    elif state["mode"] == "EDIT_CURVE":
        bpy.ops.object.mode_set(mode="EDIT")
        for spline, saved in zip(context.object.data.splines, state["curve_selection"]):
            if spline.type == "BEZIER":
                for point, (control, left, right, hidden) in zip(spline.bezier_points, saved):
                    point.hide = hidden
                    point.select_control_point = control
                    point.select_left_handle = left
                    point.select_right_handle = right
            else:
                for point, (selected, hidden) in zip(spline.points, saved):
                    point.hide = hidden
                    point.select = selected


def _collection(value):
    if isinstance(value, dict):
        value = value.get("collection")
    if not isinstance(value, bpy.types.Collection) or value.get(VERSION_KEY) != 1:
        raise HairVariantError("Choose a generated hair version.")
    return value


def show_variant(context, variant):
    """Show one result and select its independent FK controls in Pose Mode."""
    collection = _collection(variant)
    source = source_for(collection.get(SOURCE_KEY))
    mesh, armature = collection.get(MESH_KEY), collection.get(ARMATURE_KEY)
    if source is None or not isinstance(mesh, bpy.types.Object) or not isinstance(armature, bpy.types.Object):
        raise HairVariantError("This version is incomplete; its source, mesh, or rig was removed.")
    record = rig._read_records(mesh)
    if not record or mesh.get(rig.RIG_KEY) is not armature:
        raise HairVariantError("This version no longer has its recorded hair controls.")
    names = tuple(name for chain in record["chains"] for name in chain["bones"])
    if not names or any(armature.pose.bones.get(name) is None for name in names):
        raise HairVariantError("A hair control was removed from this version.")
    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for candidate in variants_for(source):
        candidate.hide_viewport = candidate is not collection
        candidate.hide_render = candidate is not collection
    source.hide_set(True)
    source.hide_render = True
    for obj in (mesh, armature):
        obj.hide_viewport = False
        obj.hide_set(False)
    armature.hide_select = False
    context.view_layer.update()
    rig._select_chains(context, armature, names)
    return mesh


def show_source(context, source):
    """Hide comparison copies and resume editing the original hair mesh."""
    source = source_for(source)
    if source is None or context.view_layer.objects.get(source.name) is not source:
        raise HairVariantError("The original hair mesh is not in the current View Layer.")
    visibility = _initial_visibility(source)
    if visibility["viewport"] or visibility["hidden"] or visibility["select"]:
        raise HairVariantError("Reveal and unlock the original source before returning to Edit Mode.")
    if context.object is not None and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for collection in variants_for(source):
        collection.hide_viewport = True
        collection.hide_render = True
    _set_visibility(source, visibility)
    context.view_layer.update()
    rig._mode(context, source, "EDIT")
    return source


def _source_attachment(context, source, record):
    modifiers = tuple(item for item in source.modifiers if item.type == "ARMATURE")
    if len(modifiers) > 1:
        raise HairVariantError("Comparison copies support one source Armature modifier.")
    modifier = modifiers[0] if modifiers else None
    if modifier and (modifier.object is None or modifier.object.type != "ARMATURE"):
        raise HairVariantError("The source Armature modifier has no valid rig.")
    if source.parent and (source.parent_type != "OBJECT" or source.parent.type != "ARMATURE"):
        raise HairVariantError("Comparison hair must be unparented or Object-parented to its Armature.")
    candidates = [item for item in (modifier.object if modifier else None,
                                   source.parent, source.get(rig.RIG_KEY)) if item is not None]
    if any(item.type != "ARMATURE" for item in candidates) or len(set(candidates)) > 1:
        raise HairVariantError("The source parent, modifier, and recorded hair rig do not agree.")
    attachment_rig = candidates[0] if candidates else None
    parent_bone = record["parent"] if record else ""
    if attachment_rig is None:
        heads = [(item, rig._head_bone(item)) for item in context.scene.objects
                 if item.type == "ARMATURE" and item.get(SOURCE_KEY) is None]
        heads = [(item, name) for item, name in heads if name]
        if len(heads) > 1:
            raise HairVariantError("More than one head rig is available; bind the source to its intended rig first.")
        if heads:
            attachment_rig, parent_bone = heads[0]
    if attachment_rig:
        parent_bone = parent_bone or rig._head_bone(attachment_rig)
        if not parent_bone or attachment_rig.data.bones.get(parent_bone) is None:
            raise HairVariantError("The source rig needs a recognizable head or recorded hair attachment.")
        rig._check_rest(context, attachment_rig, {parent_bone})
    elif record:
        raise HairVariantError("The source's recorded hair rig was removed.")
    return attachment_rig, parent_bone, modifier


def _preflight(context, source, plans, bone_count):
    if source is None or source.type != "MESH" or context.view_layer.objects.get(source.name) is not source:
        raise HairVariantError("Choose a source hair mesh in the current View Layer.")
    if source.library or source.data.library or source.override_library or source.data.override_library:
        raise HairVariantError("Make the source hair local before creating comparison versions.")
    if context.mode not in {"OBJECT", "EDIT_MESH", "EDIT_CURVE", "POSE"}:
        raise HairVariantError("Use Object Mode, Pose Mode, or edit the source hair mesh/group guide.")
    if context.mode == "EDIT_MESH" and (context.edit_object is not source or len(context.objects_in_mode) != 1):
        raise HairVariantError("Finish editing other meshes before generating this hair version.")
    if context.mode == "EDIT_CURVE" and (len(context.objects_in_mode) != 1
            or context.edit_object.get("character_designer_hair_group_source") is not source):
        raise HairVariantError("Finish editing other curves before generating this hair version.")
    if any(not item.mute and item.influence > rig.EPSILON for item in source.constraints):
        raise HairVariantError("Apply or disable source object constraints before creating hair versions.")
    if source.data.shape_keys and source.active_shape_key_index != 0:
        raise HairVariantError("Select the Basis Shape Key before generating a hair version.")
    snapshot = rig._mesh_snapshot(source)
    rig._plan_copy(plans, snapshot, bone_count)
    mirror_controls.preflight(source, plans)
    record = rig._read_records(source)
    attachment, parent, modifier = _source_attachment(context, source, record)
    mirrors = rig._mirror_preflight(source, attachment, parent, record, modifier)
    owned = set()
    if record:
        if source.get(rig.RIG_KEY) is not attachment:
            raise HairVariantError("The source's hair rig ownership is incomplete.")
        if record["topology"] != snapshot["topology"]:
            raise HairVariantError("The source topology changed after binding; use an unbound source copy.")
        owned = {name for chain in record["chains"] for name in chain["bones"]}
        for name in owned:
            bone = attachment.data.bones.get(name)
            if (bone is None or bone.get(rig.OWNER_KEY) != rig.OWNER_VALUE
                    or bone.get(rig.SOURCE_KEY) is not source):
                raise HairVariantError("The source's generated hair bone ownership changed.")
    return attachment, parent, modifier, record, owned, mirrors


def _copy_animation(data, copies):
    animation = data.animation_data
    if animation is None:
        return
    cache = {}

    def independent(action):
        if action is None:
            return None
        if action not in cache:
            cache[action] = action.copy()
            copies.append(cache[action])
        return cache[action]

    if animation.action:
        animation.action = independent(animation.action)
    for track in animation.nla_tracks:
        for strip in track.strips:
            if strip.type == "CLIP" and strip.action:
                strip.action = independent(strip.action)


def _redirect_self_drivers(source, clone):
    pairs = [(source, clone), (source.data, clone.data)]
    if source.data.shape_keys:
        pairs.append((source.data.shape_keys, clone.data.shape_keys))
    mapping = dict(pairs)
    for _, copied in pairs:
        animation = copied.animation_data
        if animation:
            for fcurve in animation.drivers:
                for variable in fcurve.driver.variables:
                    for target in variable.targets:
                        if target.id in mapping:
                            target.id = mapping[target.id]


def _prepare_copy_weights(clone, attachment, old_parent, owned, anchor):
    groups = {group.index: group for group in clone.vertex_groups}
    deform = {bone.name for bone in attachment.data.bones if bone.use_deform} if attachment else set()
    reset = set()
    for vertex in clone.data.vertices:
        for value in vertex.groups:
            name = groups[value.group].name
            if value.weight <= rig.EPSILON:
                continue
            if name in deform and name not in owned and name != old_parent:
                raise HairVariantError(f"Source weights use '{name}' outside the head/hair chains; an independent version cannot preserve that binding.")
            if name in owned:
                reset.add(vertex.index)
    # Artist masks and unrelated nondeforming vertex groups are retained.
    for name in owned:
        group = clone.vertex_groups.get(name)
        if group:
            clone.vertex_groups.remove(group)
    original = clone.vertex_groups.get(old_parent) if old_parent else None
    if original:
        original.name = anchor
    group = original or clone.vertex_groups.get(anchor) or clone.vertex_groups.new(name=anchor)
    if reset:
        if group.lock_weight:
            raise HairVariantError("Unlock the source attachment weights before making a comparison copy.")
        group.add(sorted(reset), 1.0, "REPLACE")


def _create_anchor(context, collection, source, attachment, parent, name):
    data = bpy.data.armatures.new(collection.name + " Rig")
    obj = bpy.data.objects.new(collection.name + " Rig", data)
    collection.objects.link(obj)
    obj.matrix_world = attachment.matrix_world.copy() if attachment else Matrix.Identity(4)
    rig._mode(context, obj, "EDIT")
    bone = data.edit_bones.new(name)
    if attachment:
        original = attachment.data.bones[parent]
        # A just-created EditBone has zero length. Assigning its matrix before
        # giving it endpoints can lose a small directional component in Blender.
        # Copy endpoints first, then align the roll to the original local Z axis.
        bone.head = original.head_local
        bone.tail = original.tail_local
        bone.align_roll(original.matrix_local.to_3x3().col[2])
    else:
        bone.head = source.matrix_world.translation
        bone.tail = bone.head + Vector((0, 0.05, 0))
    bone.use_deform = True
    rig._mode(context, obj, "OBJECT")
    obj[SOURCE_KEY] = source
    obj[VERSION_KEY] = 1
    return obj


def _delete_result(context, collection, mesh_data, armature_data, actions):
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    if collection:
        for obj in tuple(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)
    if mesh_data and mesh_data.users == 0:
        bpy.data.meshes.remove(mesh_data)
    if armature_data and armature_data.users == 0:
        bpy.data.armatures.remove(armature_data)
    for action in actions:
        if action.users == 0:
            bpy.data.actions.remove(action)


def build_variant(context, source, plans, *, mode="PER_STRAND", bone_count=4):
    """Create an independent result, never overwriting a prior tuned version.

    ``plans`` are fresh source-local strand/group plans.  Returns the rig service
    result plus ``source``, ``mesh``, ``collection`` and ``mode``.  On success the
    new version's hair controls are selected.  Errors restore mode, selection and
    visibility and remove every newly created object/datablock.
    """
    source = source_for(source)
    plans = tuple(plans)
    if mode not in {"PER_STRAND", "GROUPED"}:
        raise HairVariantError("Choose Per Strand or Grouped generation.")
    try:
        attachment, parent, modifier, record, owned, mirrors = _preflight(context, source, plans, bone_count)
    except rig.HairBonesRigError as exc:
        raise HairVariantError(str(exc)) from exc
    original_visibility = _initial_visibility(source)
    state = _context_snapshot(context, source, attachment)
    visibility = _visibility_snapshot(source)
    collection = clone = clone_data = variant_rig = armature_data = None
    actions = []
    try:
        # Flushing Edit Mode is necessary before copying Mesh and Key data.
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        label = "Per Strand" if mode == "PER_STRAND" else "Grouped"
        index = max((int(item.get(INDEX_KEY, 0)) for item in variants_for(source)), default=0) + 1
        collection = bpy.data.collections.new(f"{source.name} · {label} {index:02d}")
        context.scene.collection.children.link(collection)
        clone_data = source.data.copy()
        clone = source.copy()
        clone.data = clone_data
        clone.name = f"{source.name} · {label} {index:02d}"
        collection.objects.link(clone)
        clone.hide_viewport = clone.hide_render = clone.hide_select = False
        clone.hide_set(False)
        for data in (clone, clone_data, clone_data.shape_keys):
            if data:
                _copy_animation(data, actions)
        _redirect_self_drivers(source, clone)
        for key in (rig.RECORD_KEY, rig.RIG_KEY, rig.MIRROR_KEY, *tuple(_GROUP_KEYS),
                    SOURCE_KEY, MODE_KEY, MESH_KEY, ARMATURE_KEY, INDEX_KEY,
                    VISIBILITY_KEY, ATTACHMENT_KEY, VERSION_KEY):
            if key in clone:
                del clone[key]
        for item in tuple(clone.modifiers):
            if item.type == "ARMATURE":
                clone.modifiers.remove(item)
            elif item.type == "MIRROR" and record:
                item.mirror_object = None
        anchor = "Hair Attachment" if attachment else "Hair Anchor"
        while clone.vertex_groups.get(anchor) and anchor != parent and anchor not in owned:
            anchor += "_"
        _prepare_copy_weights(clone, attachment, parent, owned, anchor)
        variant_rig = _create_anchor(context, collection, source, attachment, parent, anchor)
        armature_data = variant_rig.data
        # A copied Armature object parent keeps its original local transform and
        # animation, because the new rig starts in the same object-space frame.
        if clone.parent:
            parent_inverse = source.matrix_parent_inverse.copy()
            basis = source.matrix_basis.copy()
            clone.parent = variant_rig
            clone.matrix_parent_inverse = parent_inverse
            clone.matrix_basis = basis
        context.view_layer.update()
        rig._mode(context, clone, "OBJECT")
        result = rig.build_hair_bones(context, clone, plans, bone_count=bone_count,
                                     armature=variant_rig, parent_bone=anchor,
                                     mirror_controls=True)
        if attachment:
            follow = variant_rig.pose.bones[anchor].constraints.new("COPY_TRANSFORMS")
            follow.name = "Follow Original Head"
            follow.target = attachment
            follow.subtarget = parent
            follow.owner_space = "WORLD"
            follow.target_space = "WORLD"
            follow.mix_mode = "REPLACE"
        variant_rig.data.bones[anchor].hide = True
        for item in (collection, clone, variant_rig):
            item[SOURCE_KEY] = source
            item[MODE_KEY] = mode
            item[VERSION_KEY] = 1
            item[INDEX_KEY] = index
        collection[MESH_KEY] = clone
        collection[ARMATURE_KEY] = variant_rig
        collection[VISIBILITY_KEY] = json.dumps(original_visibility, sort_keys=True)
        if attachment:
            collection[ATTACHMENT_KEY] = attachment
        result.update(source=source, mesh=clone, collection=collection, mode=mode)
        context.view_layer.update()
        # Check the evaluated attachment after adding its native constraint;
        # a mismatched rest frame must roll back instead of shifting the hair.
        rig._check_rest(context, variant_rig, {anchor})
        show_variant(context, collection)
        return result
    except Exception as exc:
        errors = []
        try:
            # _create_anchor may fail after linking but before returning.
            if armature_data is None and collection:
                armature_data = next((item.data for item in collection.objects if item.type == "ARMATURE"), None)
            _delete_result(context, collection, clone_data, armature_data, actions)
        except Exception as rollback:
            errors.append(str(rollback))
        try:
            _restore_visibility(visibility)
            context.view_layer.update()
            _restore_context(context, source, state)
        except Exception as rollback:
            errors.append(str(rollback))
        suffix = " Rollback needs attention: " + "; ".join(errors) if errors else ""
        raise HairVariantError(str(exc) + suffix) from exc
