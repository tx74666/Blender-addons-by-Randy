"""Transactional whole-mesh binding using Blender's native weight solvers.

Only deform-bone weights and one Armature modifier are written to the original.
Calculation uses Basis geometry in world/rest space, with enabled Mirror
modifiers; shape keys and other modifiers remain untouched and are not sampled.
Call from an UNDO operator in Object Mode.
"""

import hashlib
import json
import math
import struct

import bpy

from .selected_bone_weights import _capture_vertex_groups, _restore_vertex_groups

BACKUP_KEY = "character_designer_quick_binding_v1"
RIG_KEY = "character_designer_quick_binding_rig"


class QuickBindError(ValueError):
    """An artist-facing validation or weighting failure."""


def has_binding_backup(target):
    return isinstance(target, bpy.types.Object) and (BACKUP_KEY in target or RIG_KEY in target)


def _topology_signature(target):
    mesh = target.data
    digest = hashlib.sha256()
    digest.update(struct.pack('<III', len(mesh.vertices), len(mesh.edges), len(mesh.polygons)))
    for edge in mesh.edges:
        digest.update(struct.pack('<II', *edge.vertices))
    for polygon in mesh.polygons:
        digest.update(struct.pack('<I', len(polygon.vertices)))
        digest.update(struct.pack('<' + 'I' * len(polygon.vertices), *polygon.vertices))
    return digest.hexdigest()


def _read_backup(target):
    if not has_binding_backup(target):
        raise QuickBindError("No previous-binding backup exists. Bindings made in 0.43.0 cannot restore earlier weights automatically.")
    try:
        record = json.loads(target[BACKUP_KEY])
        assert record['version'] == 1
        assert record['names'] and len(set(record['names'])) == len(record['names'])
        assert len({group['name'] for group in record['groups']}) == len(record['groups'])
        for group in record['groups']:
            assert group['name'] in record['names'] and isinstance(group['index'], int)
            assert isinstance(group['lock_weight'], bool)
            for index, weight in group['weights']:
                assert isinstance(index, int) and 0 <= index < record['vertex_count']
                assert math.isfinite(weight) and 0 <= weight <= 1
        assert isinstance(record['modifier']['created'], bool)
        assert isinstance(record['modifier']['uid'], int)
        assert record['modifier']['created'] or len(record['modifier']['flags']) == 2
        assert RIG_KEY in target
        assert isinstance(record['topology'], str)
        return record
    except (KeyError, TypeError, ValueError, AssertionError):
        raise QuickBindError("The previous-binding backup is missing or damaged; it was kept for recovery.") from None


def _backup_modifier(target, record, *, allow_missing=False):
    # Native UIDs can be reused after deletion. A unique same-UID, same-rig
    # replacement is treated as continuation of this binding, not proven identity.
    # Explicit Restore removes a tool-created binding; other rigs/stacks refuse.
    modifiers = [item for item in target.modifiers if item.type == 'ARMATURE']
    saved = record['modifier']
    if not modifiers and saved['created'] and allow_missing:
        return None
    matches = [item for item in modifiers if getattr(item, 'persistent_uid', None) == saved['uid']]
    if len(modifiers) != 1 or len(matches) != 1 or matches[0].object != target.get(RIG_KEY):
        raise QuickBindError("The recorded Armature modifier was replaced or conflicts with another binding; restore was left untouched.")
    return matches[0]


def _check_backup_topology(target, record):
    if _topology_signature(target) != record['topology']:
        raise QuickBindError("Mesh topology or vertex indexing changed since binding. Restore requires the original connectivity; the backup was kept.")


def _restore_tracked_groups(target, record):
    previous = {group['name']: group for group in record['groups']}
    vertices = tuple(range(len(target.data.vertices)))
    for name in record['names']:
        group = target.vertex_groups.get(name)
        state = previous.get(name)
        if state is None:
            if group is not None:
                target.vertex_groups.remove(group)
            continue
        group = group or target.vertex_groups.new(name=name)
        group.lock_weight = False
        group.remove(vertices)
        for index, weight in state['weights']:
            group.add((index,), weight, 'REPLACE')
        group.lock_weight = state['lock_weight']


def restore_binding(context, target):
    """Restore only weights/modifier state owned by the first successful bind.

    The backup survives saving/reopening and repeated binds. Subsequent unrelated
    vertex groups, modifiers, geometry, UVs, shape keys, and rig additions remain.
    """
    if context.mode != 'OBJECT' or target is None or target.type != 'MESH':
        raise QuickBindError("Choose a mesh in Object Mode to restore its previous binding.")
    if target.library or target.data.library or target.data.users != 1:
        raise QuickBindError("Make the target mesh local and single-user before restoring.")
    record = _read_backup(target)
    _check_backup_topology(target, record)
    modifier = _backup_modifier(target, record, allow_missing=True)
    before = _capture_vertex_groups(target)
    active_group = target.vertex_groups.active_index
    flags = (modifier.use_vertex_groups, modifier.use_bone_envelopes) if modifier else None
    raw, armature = target[BACKUP_KEY], target.get(RIG_KEY)
    result = {"vertex_count": len(target.data.vertices), "group_count": len(record['groups']),
              "removed_modifier": bool(modifier and record['modifier']['created'])}
    try:
        _restore_tracked_groups(target, record)
        if modifier and not record['modifier']['created']:
            modifier.use_vertex_groups, modifier.use_bone_envelopes = record['modifier']['flags']
        target.vertex_groups.active_index = min(active_group, max(0, len(target.vertex_groups) - 1))
        del target[BACKUP_KEY]
        if RIG_KEY in target:
            del target[RIG_KEY]
        if modifier and record['modifier']['created']:
            target.modifiers.remove(modifier)
        return result
    except Exception as exc:
        _restore_vertex_groups(target, before)
        target.vertex_groups.active_index = active_group
        target[BACKUP_KEY] = raw
        if armature is not None:
            target[RIG_KEY] = armature
        if modifier and not record['modifier']['created']:
            modifier.use_vertex_groups, modifier.use_bone_envelopes = flags
        raise QuickBindError(f"Restoring weights failed; the binding and backup were kept: {exc}") from exc


def _armature_modifier(obj, armature):
    modifiers = [item for item in obj.modifiers if item.type == 'ARMATURE']
    if len(modifiers) > 1 or any(item.object != armature for item in modifiers):
        raise QuickBindError(f"{obj.name}: resolve the conflicting Armature modifier first.")
    if obj.parent and obj.parent.type == 'ARMATURE' and obj.parent != armature:
        raise QuickBindError(f"{obj.name}: its parent belongs to a different Armature.")
    return modifiers[0] if modifiers else None


def _check_mirror_groups(obj):
    if any(item.type == 'MIRROR' and item.show_viewport and any(item.use_axis)
           and not item.use_mirror_vertex_groups for item in obj.modifiers):
        raise QuickBindError(f"{obj.name}: enable Vertex Groups in its Mirror modifier so opposite-side weights follow correctly.")


def _validate(context, target, armature, body, mode):
    if context.mode != 'OBJECT':
        raise QuickBindError("Switch to Object Mode before Quick Bind.")
    if target is None or target.type != 'MESH' or not target.data.vertices:
        raise QuickBindError("Choose a non-empty target mesh.")
    if armature is None or armature.type != 'ARMATURE' or armature.mode == 'EDIT':
        raise QuickBindError("Choose the character's main Armature outside Edit Mode.")
    if target.library or target.data.library or target.data.users != 1:
        raise QuickBindError("Make the target mesh local and single-user before Quick Bind.")
    if mode not in {'TRANSFER', 'AUTO'}:
        raise QuickBindError("Choose Nearest Face Interpolated or Automatic Weights.")
    names = tuple(bone.name for bone in armature.data.bones if bone.use_deform)
    if not names:
        raise QuickBindError("The main Armature has no deform bones.")
    locked = [group.name for group in target.vertex_groups if group.name in names and group.lock_weight]
    if locked:
        raise QuickBindError("Unlock deform groups before replacing weights: " + ', '.join(locked[:4]))
    modifier = _armature_modifier(target, armature)
    if modifier and not modifier.show_viewport:
        raise QuickBindError(f"{target.name}: enable the Armature modifier in the viewport before Quick Bind.")
    if modifier and modifier.vertex_group:
        raise QuickBindError(f"{target.name}: clear the Armature modifier's Vertex Group mask before whole-mesh Quick Bind.")
    _check_mirror_groups(target)
    objects = [target, armature]
    if mode == 'TRANSFER':
        if body is None or body.type != 'MESH' or not body.data.polygons or body == target:
            raise QuickBindError("Choose a separate weighted Body mesh with faces.")
        _armature_modifier(body, armature)
        _check_mirror_groups(body)
        source_indices = {group.index for group in body.vertex_groups if group.name in names}
        if not any(member.group in source_indices and member.weight > 1e-8
                   for vertex in body.data.vertices for member in vertex.groups):
            raise QuickBindError("The Body has no weights for this Armature's deform bones.")
        objects.append(body)
    for obj in objects:
        if obj.parent_type in {'BONE', 'BONE_RELATIVE'}:
            raise QuickBindError(f"{obj.name}: bone-parented objects need a separate rest-space setup.")
        if not all(math.isfinite(value) for row in obj.matrix_world for value in row):
            raise QuickBindError(f"{obj.name}: invalid object transform.")
        if abs(obj.matrix_world.determinant()) < 1e-12:
            raise QuickBindError(f"{obj.name}: object scale must not be zero.")
    return names, modifier


def _temporary_copy(context, original, temporary, *, mirrors=False):
    obj = original.copy()
    obj.data = original.data.copy()
    temporary.append(obj)
    context.scene.collection.objects.link(obj)
    matrix = original.matrix_world.copy()
    obj.parent = None
    obj.animation_data_clear()
    for constraint in tuple(obj.constraints):
        obj.constraints.remove(constraint)
    obj.matrix_world = matrix
    obj.hide_viewport = False
    obj.hide_render = True
    obj.hide_select = False
    obj.hide_set(False)
    for modifier in tuple(obj.modifiers):
        if not (mirrors and modifier.type == 'MIRROR' and modifier.show_viewport):
            obj.modifiers.remove(modifier)
    if obj.type == 'MESH' and obj.data.shape_keys:
        # Mesh.copy owns an independent Key datablock; the original is untouched.
        obj.shape_key_clear()
    return obj


def _only_deform_groups(obj, names, *, clear=False):
    for group in tuple(obj.vertex_groups):
        if clear or group.name not in names:
            obj.vertex_groups.remove(group)
        else:
            group.lock_weight = False
    for name in names:
        if obj.vertex_groups.get(name) is None:
            obj.vertex_groups.new(name=name)


def _evaluate_mirrors(context, obj):
    if not obj.modifiers:
        return
    context.view_layer.update()
    graph = context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(graph)
    mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
    previous = obj.data
    obj.data = mesh
    for modifier in tuple(obj.modifiers):
        obj.modifiers.remove(modifier)
    if previous.users == 0:
        bpy.data.meshes.remove(previous)


def _activate(context, obj, selected=()):
    for current in tuple(context.selected_objects):
        current.select_set(False)
    for current in (*selected, obj):
        current.select_set(True)
    context.view_layer.objects.active = obj


def _interpolate(context, source, destination):
    modifier = destination.modifiers.new("Quick Bind temporary transfer", 'DATA_TRANSFER')
    modifier.object = source
    modifier.use_vert_data = True
    modifier.data_types_verts = {'VGROUP_WEIGHTS'}
    modifier.vert_mapping = 'POLYINTERP_NEAREST'
    modifier.layers_vgroup_select_src = 'ALL'
    modifier.layers_vgroup_select_dst = 'NAME'
    modifier.mix_mode = 'REPLACE'
    modifier.mix_factor = 1.0
    modifier.use_object_transform = True
    _activate(context, destination)
    with context.temp_override(object=destination, active_object=destination,
                               selected_objects=[destination], selected_editable_objects=[destination]):
        result = bpy.ops.object.modifier_apply(modifier=modifier.name)
    if 'FINISHED' not in result:
        raise QuickBindError("Nearest Face Interpolated did not finish.")


def _run_auto(context, mesh, armature):
    _activate(context, armature, (mesh,))
    with context.temp_override(object=armature, active_object=armature,
                               selected_objects=[mesh, armature], selected_editable_objects=[mesh, armature]):
        result = bpy.ops.object.parent_set(type='ARMATURE_AUTO', keep_transform=True)
    if 'FINISHED' not in result:
        raise QuickBindError("Blender Automatic Weights did not finish.")
    for modifier in tuple(mesh.modifiers):
        mesh.modifiers.remove(modifier)
    matrix = mesh.matrix_world.copy()
    mesh.parent = None
    mesh.matrix_world = matrix


def _normalized_weights(obj, names):
    indices = {group.index: group.name for group in obj.vertex_groups if group.name in names}
    weights = {name: [] for name in names}
    missing = 0
    for vertex in obj.data.vertices:
        memberships = [(indices[item.group], float(item.weight)) for item in vertex.groups
                       if item.group in indices and item.weight > 1e-8]
        total = sum(weight for _, weight in memberships)
        if not math.isfinite(total) or total <= 1e-8:
            missing += 1
            continue
        for name, weight in memberships:
            weights[name].append((vertex.index, weight / total))
    if missing:
        raise QuickBindError(f"Weight calculation left {missing} vertices unweighted; original binding was kept. "
                             "Check Body coverage or mesh geometry before retrying.")
    return weights


def _write_weights(target, weights):
    vertices = tuple(range(len(target.data.vertices)))
    for name, assignments in weights.items():
        group = target.vertex_groups.get(name) or target.vertex_groups.new(name=name)
        group.remove(vertices)
        for index, weight in assignments:
            group.add((index,), weight, 'REPLACE')


def bind_weights(context, target, armature, *, body=None, mode='TRANSFER'):
    """Replace this rig's deform weights, preserving all other original data.

    Source weights are sampled on Basis geometry plus enabled Mirror modifiers.
    Automatic Weights solves the full mirrored temporary mesh and samples its
    result back onto the original base vertices. No original rig pose, parenting,
    mesh datablock, modifier stack geometry, or shape keys are changed.
    """
    names, existing = _validate(context, target, armature, body, mode)
    had_backup = has_binding_backup(target)
    record = _read_backup(target) if had_backup else None
    if record:
        _check_backup_topology(target, record)
        if target.get(RIG_KEY) != armature or set(record['names']) != set(names):
            raise QuickBindError("The rig or its deform bones changed. Restore Previous Binding before binding the changed rig.")
        _backup_modifier(target, record)
    if existing is not None and not hasattr(existing, 'persistent_uid'):
        raise QuickBindError("Persistent binding restore needs Blender with modifier persistent IDs.")
    previous_active = context.view_layer.objects.active
    previous_selected = tuple(context.selected_objects)
    temporary = []
    before = None
    created = None
    modifier_flags = (existing.use_vertex_groups, existing.use_bone_envelopes) if existing else None
    active_group = target.vertex_groups.active_index
    try:
        destination = _temporary_copy(context, target, temporary)
        _only_deform_groups(destination, names, clear=True)
        if mode == 'TRANSFER':
            source = _temporary_copy(context, body, temporary, mirrors=True)
            _only_deform_groups(source, names)
            _evaluate_mirrors(context, source)
        else:
            source = _temporary_copy(context, target, temporary, mirrors=True)
            _only_deform_groups(source, names, clear=True)
            _evaluate_mirrors(context, source)
            if not source.data.polygons:
                raise QuickBindError("Automatic Weights needs mesh faces.")
            rig = _temporary_copy(context, armature, temporary)
            rig.data.pose_position = 'REST'
            _run_auto(context, source, rig)
            _normalized_weights(source, names)
        _interpolate(context, source, destination)
        weights = _normalized_weights(destination, names)
        before = _capture_vertex_groups(target)
        _write_weights(target, weights)
        modifier = existing
        if modifier is None:
            created = target.modifiers.new("Armature", 'ARMATURE')
            modifier = created
            modifier.object = armature
            # Deform the mirrored cage before subdivision adds interpolated verts.
            # Existing modifiers retain their relative order and settings.
            last_mirror = max((i for i, item in enumerate(target.modifiers)
                               if item.type == 'MIRROR'), default=-1)
            subdivision = next((i for i, item in enumerate(target.modifiers)
                                if item.type == 'SUBSURF' and i > last_mirror), None)
            if subdivision is not None:
                target.modifiers.move(len(target.modifiers) - 1, subdivision)
        modifier.use_vertex_groups = True
        modifier.use_bone_envelopes = False
        if record is None:
            record = {"version": 1, "mode": mode, "vertex_count": len(target.data.vertices),
                      "topology": _topology_signature(target), "names": list(names),
                      "groups": [group for group in before if group['name'] in names],
                      "modifier": {"created": created is not None, "uid": modifier.persistent_uid,
                                   "flags": modifier_flags}}
            target[BACKUP_KEY] = json.dumps(record, separators=(',', ':'))
            target[RIG_KEY] = armature
        target.vertex_groups.active_index = active_group
        return {"mode": mode, "vertex_count": len(target.data.vertices),
                "group_count": sum(bool(values) for values in weights.values()), "modifier": modifier.name}
    except Exception as exc:
        if record is not None and not had_backup:
            for key in (BACKUP_KEY, RIG_KEY):
                if key in target:
                    del target[key]
        if before is not None:
            _restore_vertex_groups(target, before)
            target.vertex_groups.active_index = active_group
        if created is not None:
            target.modifiers.remove(created)
        if existing is not None:
            existing.use_vertex_groups, existing.use_bone_envelopes = modifier_flags
        if isinstance(exc, QuickBindError):
            raise
        raise QuickBindError(f"Quick Bind failed; original binding was kept: {exc}") from exc
    finally:
        for obj in reversed(temporary):
            data = obj.data
            kind = obj.type
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                (bpy.data.meshes if kind == 'MESH' else bpy.data.armatures).remove(data)
        for obj in tuple(context.selected_objects):
            obj.select_set(False)
        for obj in previous_selected:
            if context.view_layer.objects.get(obj.name) is obj:
                obj.select_set(True)
        context.view_layer.objects.active = previous_active
