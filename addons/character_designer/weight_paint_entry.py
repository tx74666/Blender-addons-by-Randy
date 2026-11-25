"""One RAM-only session for native weight painting and non-keyed pose preview."""

import bpy
from bpy.types import Operator

_STATE = globals().get('_STATE')
_FINISH_STATUS = globals().get('_FINISH_STATUS')
_SESSION_VERSION = 1
_MODES = frozenset({'OBJECT', 'POSE', 'PAINT_WEIGHT'})


def _references_valid(state):
    try:
        return all(state[key].as_pointer() for key in
                   ('scene', 'layer', 'rig', 'rig_data', 'mesh', 'mesh_data'))
    except (ReferenceError, AttributeError, KeyError, TypeError):
        return False


def active(context=None, *, scene=None, rig=None):
    """O(1) identity check for this one temporary display, not an authorizer."""
    state = _STATE
    if state is None or not _references_valid(state):
        return False
    try:
        if context is None and scene is None:
            context = bpy.context
        if context is not None:
            if context.view_layer != state['layer']:
                return False
            if scene is None:
                scene = context.scene
        return (scene == state['scene']
                and (rig is None or rig == state['rig']))
    except ReferenceError:
        return False


def _paint():
    # Keep the existing native mode helpers; never run their weighting operator.
    from . import selected_bone_weights
    return selected_bone_weights


def _pose_session():
    from . import weight_paint_pose_session
    return weight_paint_pose_session


def phase(context=None):
    """Legacy display-only sessions remain paint sessions until explicitly left."""
    return _STATE.get('phase', 'PAINT') if active(context) else None


def owns_rig(rig):
    """Protect the same live rig across Scene/ViewLayer context changes."""
    return (_STATE is not None and _references_valid(_STATE) and rig == _STATE['rig'])


def last_finish_status(context=None, rig=None):
    """Read-only feedback, never authority to restore a previous session."""
    saved = _FINISH_STATUS
    try:
        if (not saved or not saved['rig'].as_pointer()
                or (rig is not None and rig != saved['rig'])):
            return None
        context = bpy.context if context is None else context
        if context.scene != saved['scene'] or context.view_layer != saved['layer']:
            return None
        return dict(ready=saved['ready'], message=saved['message'],
                    legacy_visibility_repair=saved.get('legacy_visibility_repair'))
    except (AttributeError, ReferenceError, KeyError, TypeError):
        return None


def native_painting(context):
    return (context.mode == 'PAINT_WEIGHT' and context.object is not None
            and context.object.type == 'MESH')


def can_end_native(context):
    if not native_painting(context) or active(context):
        return False
    if _STATE is not None and _references_valid(_STATE):
        if context.object == _STATE['mesh'] or _STATE['rig'] in _bound_rigs(context.object):
            return False
    return True


def _session(context):
    if _STATE is None:
        raise ValueError('There is no active painting session.')
    _owner(context, _STATE)
    if _STATE.get('session_version', _SESSION_VERSION) != _SESSION_VERSION:
        raise ValueError('This painting session belongs to another version; its recovery was retained.')
    if _STATE.get('phase', 'PAINT') not in {'PAINT', 'POSE_PREVIEW'}:
        raise ValueError('The painting session phase is not recognized; its recovery was retained.')
    if _STATE.get('pose_baseline') is not None:
        _pose_session().check(_STATE['mesh'], _STATE['rig'], _STATE['pose_baseline'])
    return _STATE


def _bound_rigs(mesh):
    # Include every real target, even envelope-only or disabled modifiers.
    # Filtering those out could hide an ambiguous deformation stack.
    return tuple(dict.fromkeys(
        modifier.object for modifier in mesh.modifiers
        if modifier.type == 'ARMATURE' and modifier.object is not None))


def _vertex_binding(mesh, rig):
    return any(modifier.type == 'ARMATURE' and modifier.object == rig
               and modifier.use_vertex_groups for modifier in mesh.modifiers)


def _editable(mesh, rig):
    for item in (mesh, mesh.data, rig, rig.data):
        if item.library is not None or not item.is_editable:
            raise ValueError('Use a local, editable Mesh and Armature.')
    # ID users also count metadata references and fake users. Only another
    # Object sharing this native data would receive our temporary display flags.
    mesh_data, rig_data = mesh.data, rig.data
    mesh_owners, rig_owners = [], []
    for obj in bpy.data.objects:
        data = getattr(obj, 'data', None)
        if data == mesh_data:
            mesh_owners.append(obj)
        if data == rig_data:
            rig_owners.append(obj)
    if mesh_owners != [mesh] or rig_owners != [rig]:
        raise ValueError('The Mesh and Armature data must each belong to this Object alone.')


def _mesh_flags(mesh):
    data = mesh.data
    return (data.use_paint_bone_selection, data.use_paint_mask, data.use_paint_mask_vertex)


def _set_mesh_flags(mesh, values):
    data = mesh.data
    data.use_paint_bone_selection, data.use_paint_mask, data.use_paint_mask_vertex = values
    if _mesh_flags(mesh) != values:
        raise RuntimeError('Blender could not restore the native painting selection flags.')


def _available(context, obj):
    return (context.view_layer.objects.get(obj.name) == obj
            and not obj.hide_get(view_layer=context.view_layer)
            and not obj.hide_viewport and obj.visible_get(view_layer=context.view_layer))


def _resolve(context):
    if context.mode not in _MODES:
        raise ValueError('Finish the current edit, then use Object, Pose or Weight Paint Mode.')
    obj = context.view_layer.objects.active
    remembered_group = None
    if obj is not None and obj.type == 'MESH':
        mesh = obj
    elif obj is not None and obj.type == 'ARMATURE':
        meshes = tuple(item for item in context.selected_objects
                       if item.type == 'MESH' and obj in _bound_rigs(item))
        saved = _FINISH_STATUS
        if (not any(item.type == 'MESH' for item in context.selected_objects)
                and saved and _references_valid(saved)
                and saved['scene'] == context.scene and saved['layer'] == context.view_layer
                and saved['rig'] == obj and saved['rig_data'] == obj.data
                and saved['mesh_data'] == saved['mesh'].data and _bound_rigs(saved['mesh']) == (obj,)):
            meshes = (saved['mesh'],)
            remembered_group = saved.get('paint_group')
        if len(meshes) != 1:
            raise ValueError('Select exactly one bound Mesh together with this Armature.')
        mesh = meshes[0]
    else:
        raise ValueError('Select the Mesh to paint, or its Armature and exactly one bound Mesh.')
    rigs = _bound_rigs(mesh)
    if len(rigs) != 1 or rigs[0].type != 'ARMATURE':
        raise ValueError('The Mesh must have one unambiguous vertex-group Armature target.')
    rig = rigs[0]
    if not _vertex_binding(mesh, rig):
        raise ValueError('The Armature binding must use Vertex Groups; its modifiers were kept unchanged.')
    if obj.type == 'ARMATURE' and obj != rig:
        raise ValueError('The active Armature does not match the selected Mesh binding.')
    _editable(mesh, rig)
    if rig.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Finish editing the Armature before painting weights.')
    if not _available(context, mesh) or not _available(context, rig):
        raise ValueError('Show the Mesh and its Armature in this View Layer first.')
    if ('character_designer_body_original_mode_v1' in rig
            or 'character_designer_bone_display_view_v1' in rig.data
            or context.scene.get('character_designer_weight_workspace_v1')):
        raise ValueError('Finish the existing Original or temporary display before painting.')
    names = tuple(bone.name for bone in rig.data.bones
                  if bone.use_deform and mesh.vertex_groups.get(bone.name) is not None)
    if not names:
        raise ValueError('This Mesh has no existing Vertex Groups matching Deform bones.')
    group = mesh.vertex_groups.active
    bone = rig.data.bones.active
    chosen = (remembered_group if remembered_group in names else
              group.name if group is not None and group.name in names else
              bone.name if bone is not None and bone.name in names else names[0])
    return mesh, rig, names, chosen


def _display(rig):
    return {
        'data': rig.data,
        'collections': tuple((item, item.is_visible, item.is_solo)
                             for item in rig.data.collections_all),
        'bones': tuple((bone, bone.hide, bone.hide_select) for bone in rig.data.bones),
        'pose': tuple((bone, bone.hide) for bone in rig.pose.bones if hasattr(bone, 'hide')),
        'shapes': rig.data.show_bone_custom_shapes, 'type': rig.data.display_type,
        'in_front': rig.show_in_front, 'selectable': rig.hide_select,
    }


def _check_display(rig, saved):
    if (rig.data != saved['data']
            or tuple(rig.data.bones) != tuple(item[0] for item in saved['bones'])
            or tuple(rig.data.collections_all) != tuple(item[0] for item in saved['collections'])):
        raise ValueError('The Armature structure changed; restore it before finishing this display.')


def _restore_display(rig, saved):
    _check_display(rig, saved)
    for item, visible, solo in saved['collections']:
        item.is_visible, item.is_solo = visible, solo
    for bone, hidden, unselectable in saved['bones']:
        bone.hide, bone.hide_select = hidden, unselectable
    for bone, hidden in saved['pose']:
        bone.hide = hidden
    rig.data.show_bone_custom_shapes = saved['shapes']
    rig.data.display_type = saved['type']
    rig.show_in_front, rig.hide_select = saved['in_front'], saved['selectable']


def _show(rig, names):
    names = set(names)
    for item in rig.data.collections_all:
        item.is_solo, item.is_visible = False, True
    for bone in rig.data.bones:
        bone.hide = bone.name not in names
        bone.hide_select = bone.name not in names
    for bone in rig.pose.bones:
        if hasattr(bone, 'hide'):
            bone.hide = bone.name not in names
    rig.data.show_bone_custom_shapes = False
    rig.data.display_type = 'OCTAHEDRAL'
    rig.show_in_front, rig.hide_select = True, False


def _show_preview(rig, names, saved):
    _show(rig, names)
    rig.data.show_bone_custom_shapes = True
    rig.data.display_type = saved['type']


def controls_display_needs_repair(context=None, rig=None):
    """A legacy filtered view is not proof of a complete Controls display."""
    state = _STATE
    return bool(state is not None and _references_valid(state)
                and (rig is None or state['rig'] == rig)
                and state.get('controls_display_version') != 2)


def _foreign_display_groups(context, rig):
    from . import bone_display, bone_collections
    result = {}
    for role in ('HAIR', 'DRESS'):
        result[role] = tuple((target, collection) for target, collection in
                            bone_display._control_collections(context, rig, role) if target == rig)
    # Preserve opaque existing Dress boundaries too. A display request does not
    # grant permission to reveal one without the ordinary source-owner proof.
    body = bone_collections.body_collection(rig)
    known = {collection for pairs in result.values() for _target, collection in pairs}
    extra = tuple((rig, collection) for collection in bone_collections.public_collections(rig)
                  if collection != body and collection.get(bone_collections.GROUP_KEY) != 'Original'
                  and collection not in known)
    result['OPAQUE'] = extra
    return result


def _normal_controls_display(context, state, *, repair_legacy=False):
    """Use normal registered Body policy; never derive visibility from weights.

    Modern entries retain observed foreign flags exactly. Only an explicit
    legacy repair may reveal a whole registered, eye-On public cohort matching
    the old blanket hidden/unselectable mask. Its old selection intent is
    unknown and is reported as reset, never as recovered history.
    """
    from . import bone_display, bone_collections
    rig = state['rig']
    legacy = state.get('display_entry_version') != 2 and state.get('controls_display_version') != 2
    if legacy and not repair_legacy:
        raise ValueError('Choose Controls explicitly to repair this older filtered display; pose and weights are retained.')
    # A trusted paused Controls view may contain newer user hides/locks. Its
    # current flags are not the synthetic PAINT mask or an untrusted legacy view.
    trusted_controls = state.get('controls_display_version') == 2 and state.get('phase') == 'POSE_PREVIEW'
    saved = _display(rig) if trusted_controls else state.get('controls_display') or state['display']
    _check_display(rig, saved)
    body = bone_collections.body_collection(rig)
    if body is None:
        raise ValueError('A managed Body display is required for complete Controls; the painting recovery is retained.')
    body_names = bone_collections.fingers.body_members(body)
    foreign = _foreign_display_groups(context, rig)
    foreign_collections = {collection for pairs in foreign.values() for _target, collection in pairs}
    stack = list(foreign_collections)
    while stack:
        collection = stack.pop()
        for child in collection.children:
            if child not in foreign_collections:
                foreign_collections.add(child); stack.append(child)
    foreign_names = {bone.name for collection in foreign_collections for bone in collection.bones}
    if body_names & foreign_names:
        raise ValueError('Body and foreign display groups share participants; preserve that authored layout before this preview.')
    current_off = {collection for collection in foreign_collections if not collection.is_visible}
    collections = {collection: (visible, solo) for collection, visible, solo in saved['collections']}
    flags = {bone.name: (hidden, locked) for bone, hidden, locked in saved['bones']}
    pose_flags = {bone.name: hidden for bone, hidden in saved['pose']}
    affected = bone_display._affected(context, rig)
    before = [(target, bone_display._snapshot(target)) for target in affected]
    other_views = [(target, view) for target, view in before if target != rig]
    reset = set()
    repaired_roles = []
    try:
        _restore_display(rig, saved)
        for collection in current_off:
            collection.is_visible = False
        # This API owns Body membership/current IK-FK policy and the explicit
        # foot/wrist mechanism exclusions. No collection assignment is changed.
        bone_display.show_controls(context, rig, role='BODY')
        for collection in foreign_collections:
            visible, solo = collections[collection]
            collection.is_visible, collection.is_solo = visible and collection not in current_off, solo
        for name in foreign_names:
            bone = rig.data.bones[name]
            bone.hide, bone.hide_select = flags[name]
            if name in pose_flags:
                rig.pose.bones[name].hide = pose_flags[name]
        if legacy:
            old_targets = set(state.get('preview_targets', {}).get('names', ()))
            for name in body_names - old_targets:
                bone = rig.data.bones[name]
                if flags[name] == (True, True) and not bone.hide:
                    bone.hide_select = False; reset.add(name)
            for role in ('HAIR', 'DRESS'):
                pairs = foreign[role]
                hair_tree = tuple(dict.fromkeys(child for _target, root in pairs
                                  for child in bone_display._hair_public_tree(root))) if role == 'HAIR' else ()
                names = (bone_display._hair_public_names(hair_tree) if role == 'HAIR' else
                         {bone.name for _target, collection in pairs for bone in collection.bones})
                eye_on = (bool(hair_tree) and all(collection.is_visible_effectively for collection in hair_tree)
                          if role == 'HAIR' else bool(pairs) and all(collection.is_visible_effectively for _target, collection in pairs))
                blanket = bool(names) and all(flags[name] == (True, True) for name in names)
                if eye_on and blanket:
                    bone_display.show_controls(context, rig, role=role)
                    for name in names:
                        bone = rig.data.bones[name]
                        if not bone.hide:
                            bone.hide_select = False; reset.add(name)
                    repaired_roles.append(role)
        # Body operations must not change independently displayed Dress rigs.
        for target, view in other_views:
            bone_display._restore(target, view)
        # A public foreign collection may also contain a registered mechanism.
        # Normal Controls' exact owned exclusions take precedence over its saved
        # visible mask; selection locks stay untouched. No bone-name heuristic.
        bone_display._hide_registered_foot_mechanisms(rig)
        bone_display._hide_registered_wrist_mechanisms(rig)
        if not body.is_visible_effectively:
            raise ValueError('Body Controls are blocked by a saved ancestor or foreign Solo; the previous display was kept.')
        if not rig.data.show_bone_custom_shapes:
            raise RuntimeError('Normal Controls did not enable existing custom shapes.')
        visible = tuple(bone.name for bone in rig.data.bones
            if not bone.hide and not bone.hide_select
            and not getattr(rig.pose.bones[bone.name], 'hide', False)
            and (not bone.collections or any(collection.is_visible_effectively for collection in bone.collections)))
        if not set(visible) & body_names:
            raise ValueError('No selectable public Body participant is available; existing selection locks were kept.')
        state['controls_display'] = _display(rig)
        state['controls_display_version'] = 2
        state['controls_display_provenance'] = ('EXPLICIT_LEGACY_PUBLIC_REPAIR' if legacy else 'OBSERVED_ENTRY_OR_CONTROLS')
        if legacy:
            state['legacy_selection_mask_reset'] = tuple(sorted(reset))
            state['legacy_public_roles_revealed'] = tuple(repaired_roles)
            state['legacy_controls_repair'] = dict(selection_mask_reset=tuple(sorted(reset)),
                public_roles_revealed=tuple(repaired_roles),
                provenance='Explicit public Controls request; legacy blanket selection intent unknown, not restored history.')
        return visible
    except Exception:
        for target, view in before:
            bone_display._restore(target, view)
        raise


def _transition_checkpoint(context, state, *, include_pose=False):
    return dict(display=_display(state['rig']), flags=_mesh_flags(state['mesh']),
                context=_paint()._capture_context_state(context, state['mesh'], state['rig']),
                rig_mode=state['rig'].mode, phase=state.get('phase', 'PAINT'),
                group=state.get('paint_group'),
                auto=context.scene.tool_settings.use_keyframe_insert_auto,
                controls={key: state[key] for key in ('controls_display', 'controls_display_version',
                    'controls_display_provenance', 'legacy_selection_mask_reset', 'legacy_public_roles_revealed',
                    'legacy_controls_repair') if key in state},
                pose=_pose_session().capture(context, state['mesh'], state['rig']) if include_pose else None)


def _rollback(context, state, previous):
    errors = []
    attempts = []
    if previous['pose'] is not None:
        attempts.append(('pose', lambda: _pose_session().restore(
            context, state['mesh'], state['rig'], previous['pose'])))
    attempts.extend((
        ('display', lambda: _restore_display(state['rig'], previous['display'])),
        ('paint flags', lambda: _set_mesh_flags(state['mesh'], previous['flags'])),
        ('context', lambda: _restore_context(context, state, previous['context'], previous['rig_mode']))))
    try:
        for label, attempt in attempts:
            try:
                attempt()
            except Exception as error:
                errors.append(f'{label}: {error}')
    finally:
        # An unrelated RNA/context recovery failure must not strand Auto Key.
        # Keep the same live session and its original phase for explicit retry.
        state['phase'], state['paint_group'] = previous['phase'], previous['group']
        for key in ('controls_display', 'controls_display_version', 'controls_display_provenance',
                    'legacy_selection_mask_reset', 'legacy_public_roles_revealed', 'legacy_controls_repair'):
            if key in previous.get('controls', {}):
                state[key] = previous['controls'][key]
            else:
                state.pop(key, None)
        try:
            context.scene.tool_settings.use_keyframe_insert_auto = previous['auto']
        except Exception as error:
            errors.append(f'Auto Key: {error}')
    if errors:
        raise RuntimeError('Painting recovery retained: ' + '; '.join(errors))


def _paint_group(state):
    group = state['mesh'].vertex_groups.active
    if (group is None or state['rig'].data.bones.get(group.name) is None
            or not state['rig'].data.bones[group.name].use_deform):
        raise ValueError('Choose an existing Deform vertex group before previewing its pose.')
    return group.name


def _controls_targets(state, *, allow_empty=False):
    mesh, rig = state['mesh'], state['rig']
    names = tuple(b.name for b in rig.data.bones
                  if b.use_deform and mesh.vertex_groups.get(b.name) is not None)
    try:
        return _pose_session().preview_targets(rig, names)
    except ValueError as error:
        if not (allow_empty and str(error).startswith(
                'These driven bones have no verified local pose controls.')):
            raise
        return dict(names=(), controls=(), sources=(), unresolved=names)


def _group_pose_target(rig, targets, group):
    """Find a unique real target for the painted source; never a prefix guess."""
    if group in targets['sources']:
        return group
    mapped = targets.get('paint_targets', {}).get(group)
    if mapped in targets['names']:
        return mapped
    try:
        local = _pose_session().preview_targets(rig, (group,))
    except ValueError as error:
        if not str(error).startswith('These driven bones have no verified local pose controls.'):
            raise
        return None
    from . import limb_ik
    candidates = set()
    controls = set(targets['controls']).intersection(local['controls'])
    for pb in rig.pose.bones:
        try:
            records = limb_ik._constraint_registry(pb, strict=True)
        except (ValueError, AttributeError, TypeError, RuntimeError):
            records = {}
        for con in pb.constraints:
            if con.mute or float(con.influence) <= 1e-6 or getattr(con, 'target', None) != rig:
                continue
            record = records.get(con.name)
            if (isinstance(record, dict) and record.get('owner') == limb_ik.OWNER_VALUE
                    and (group in record.get('chain', ()) or record.get('root') == group)):
                target = record.get('target')
                if target in controls and con.subtarget in {
                        target, record.get('solver_target'), record.get('mch_upper'), record.get('mch_lower')}:
                    candidates.add(target)
            if con.type == 'IK' and group in _pose_session()._chain(pb, int(con.chain_count)):
                if con.subtarget in controls:
                    candidates.add(con.subtarget)
            elif pb.name == group and con.type in {'COPY_ROTATION', 'COPY_TRANSFORMS'}:
                if con.subtarget in controls:
                    candidates.add(con.subtarget)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _posing_selection(rig, targets, group, preferred, current=None):
    visible = set(targets['names'])
    selected = tuple(name for name in preferred['pose_selection'] if name in visible)
    if selected:
        active_name = preferred['active_bone']
        return selected, active_name if active_name in selected else selected[0]
    mapped = _group_pose_target(rig, targets, group)
    if mapped is not None:
        return (mapped,), mapped
    if current is not None:
        active_name = current['active_bone']
        if active_name in visible and active_name in current['pose_selection']:
            return (active_name,), active_name
    return (), ''


def _owner(context, state):
    if not active(context):
        raise ValueError('Return to the Scene and View Layer where painting started.')
    mesh, rig = state['mesh'], state['rig']
    if (context.view_layer.objects.get(mesh.name) != mesh
            or context.view_layer.objects.get(rig.name) != rig
            or mesh.data != state['mesh_data'] or _bound_rigs(mesh) != (rig,)
            or not _vertex_binding(mesh, rig)):
        raise ValueError('The painting Mesh or its Armature binding changed; it was kept.')
    _editable(mesh, rig)
    if context.view_layer.objects.active not in (mesh, rig) or context.mode not in _MODES:
        raise ValueError('Select this painting Mesh or its Armature outside Edit Mode first.')
    _check_display(rig, state['display'])


def _restore_rig_mode(context, rig, expected):
    _paint()._force_object_mode(context)
    rig.select_set(True)
    context.view_layer.objects.active = rig
    if rig.mode != expected:
        if rig.mode != 'OBJECT':
            result = bpy.ops.object.mode_set(mode='OBJECT')
            if 'FINISHED' not in result or rig.mode != 'OBJECT':
                raise RuntimeError('Blender could not restore the Armature Object Mode.')
        if expected != 'OBJECT':
            result = bpy.ops.object.mode_set(mode=expected)
            if 'FINISHED' not in result or rig.mode != expected:
                raise RuntimeError('Blender could not restore the original Armature mode.')


def _restore_context(context, state, saved, rig_mode):
    paint = _paint()
    _restore_rig_mode(context, state['rig'], rig_mode)
    paint._restore_context_state(context, state['mesh'], state['rig'], saved)
    # A Mesh can be active in Object Mode while its selected rig remains in
    # Pose Mode. The generic context helper exits that rig's mode first.
    if (state['rig'].mode != rig_mode and saved['mode'] == 'OBJECT'
            and saved['active_object'] != state['rig']):
        _restore_rig_mode(context, state['rig'], rig_mode)
        paint._deselect_all_objects(context)
        for obj in saved['selected_objects']:
            if paint._object_is_available(context, obj):
                obj.select_set(True)
        context.view_layer.objects.active = saved['active_object']
        paint._set_pose_selection(state['rig'], saved['pose_selection'], saved['active_bone'])
    actual = paint._capture_context_state(context, state['mesh'], state['rig'])
    if actual != saved or state['rig'].mode != rig_mode:
        raise RuntimeError('Blender could not restore the original native mode and selection exactly.')


def _entered(context, mesh, rig, chosen):
    bone = rig.pose.bones.get(chosen)
    if (context.mode != 'PAINT_WEIGHT' or context.view_layer.objects.active != mesh
            or rig.mode != 'POSE' or rig.data.bones.active != rig.data.bones.get(chosen)
            or bone is None or not bone.select or mesh.vertex_groups.active is None
            or mesh.vertex_groups.active.name != chosen):
        raise RuntimeError('Blender did not enter the requested Mesh, bone and Vertex Group for painting.')


def enter(context, *, current_pose=False):
    """Show the selected Mesh's existing Deform groups and enter native painting."""
    global _STATE, _FINISH_STATUS
    if _STATE is not None and not _references_valid(_STATE):
        # A deleted native owner cannot be recovered. Drop only invalid RAM
        # references on an explicit entry, without touching any former objects.
        _STATE = None
    if _STATE is not None:
        raise ValueError('Finish the current painting display in its owning Scene first.')
    mesh, rig, names, chosen = _resolve(context)
    state = dict(scene=context.scene, layer=context.view_layer, mesh=mesh, rig=rig,
                 mesh_data=mesh.data, rig_data=rig.data, rig_mode=rig.mode,
                 mesh_flags=_mesh_flags(mesh), display=_display(rig),
                 context=_paint()._capture_context_state(context, mesh, rig),
                 session_version=_SESSION_VERSION, phase='PAINT', paint_group=chosen,
                 display_entry_version=2,
                 pose_baseline=None if current_pose else _pose_session().capture(context, mesh, rig),
                 auto_key=context.scene.tool_settings.use_keyframe_insert_auto,
                 baseline_scope='SESSION_ENTRY')
    _STATE = state
    try:
        context.scene.tool_settings.use_keyframe_insert_auto = False
        _show(rig, names)
        _set_mesh_flags(mesh, (True, False, False))
        _paint()._enter_weight_paint(context, mesh, rig, (chosen,))
        mesh.vertex_groups.active_index = mesh.vertex_groups[chosen].index
        _entered(context, mesh, rig, chosen)
        _FINISH_STATUS = None
    except Exception as original:
        try:
            _editable(mesh, rig)
            _restore_display(rig, state['display'])
            _set_mesh_flags(mesh, state['mesh_flags'])
            _restore_context(context, state, state['context'], state['rig_mode'])
        except Exception as recovery:
            raise RuntimeError(f'Painting entry failed: {original}. '
                               f'Native state recovery also failed: {recovery}') from original
        finally:
            context.scene.tool_settings.use_keyframe_insert_auto = state['auto_key']
        _STATE = None
        raise


def preview(context):
    """Keep the painted group while exposing existing IK/FK pose participants."""
    state = _session(context)
    if state.get('phase', 'PAINT') != 'PAINT':
        raise ValueError('Resume Painting before starting another pose preview.')
    _preview_into_pose(context, state, _paint_group(state))


def _preview_into_pose(context, state, chosen, *, repair_legacy=False):
    mesh, rig = state['mesh'], state['rig']
    targets = _controls_targets(state, allow_empty=True)
    previous = _transition_checkpoint(context, state)
    legacy = state.get('pose_baseline') is None
    baseline = _pose_session().capture(context, mesh, rig) if legacy else state['pose_baseline']
    try:
        context.scene.tool_settings.use_keyframe_insert_auto = False
        names = _normal_controls_display(context, state, repair_legacy=repair_legacy)
        targets = dict(targets, names=names,
            controls=tuple(name for name in targets['controls'] if name in names),
            sources=tuple(name for name in targets['sources'] if name in names),
            selection_unresolved=targets['unresolved'],
            selection_hint_status='UNRESOLVED' if targets['unresolved'] else 'AVAILABLE',
            display_scope='NORMAL_PUBLIC_BODY')
        selected, active_name = _posing_selection(rig, targets, chosen, previous['context'])
        _paint()._enter_pose_mode(context, rig, selected, active_name)
        mesh.vertex_groups.active_index = mesh.vertex_groups[chosen].index
        if (context.mode != 'POSE' or context.view_layer.objects.active != rig
                or mesh.vertex_groups.active.name != chosen):
            raise RuntimeError('Blender did not enter pose preview with the painted group retained.')
        if legacy:
            state.update(pose_baseline=baseline, auto_key=previous['auto'],
                         baseline_scope='FIRST_PREVIEW_LEGACY', session_version=_SESSION_VERSION)
        state.update(phase='POSE_PREVIEW', paint_group=chosen, preview_targets=targets)
    except Exception as original:
        try:
            _rollback(context, state, previous)
        except Exception as recovery:
            raise RuntimeError(f'Pose preview failed: {original}. Recovery also failed: {recovery}') from original
        raise


def resume(context):
    """Return to the saved painting group, independently of selected controls."""
    state = _session(context)
    if state.get('phase', 'PAINT') != 'POSE_PREVIEW':
        raise ValueError('Start Preview Pose before resuming painting.')
    _resume_into_paint(context, state, state['paint_group'])


def _resume_into_paint(context, state, chosen):
    mesh, rig = state['mesh'], state['rig']
    if mesh.vertex_groups.get(chosen) is None or rig.data.bones.get(chosen) is None:
        raise ValueError('The saved painting group changed; the current weights were kept.')
    previous = _transition_checkpoint(context, state)
    try:
        context.scene.tool_settings.use_keyframe_insert_auto = False
        if state.get('controls_display_version') == 2 and state.get('phase') == 'POSE_PREVIEW':
            state['controls_display'] = _display(rig)
        names = tuple(b.name for b in rig.data.bones
                      if b.use_deform and mesh.vertex_groups.get(b.name) is not None)
        _show(rig, names)
        _set_mesh_flags(mesh, (True, False, False))
        _paint()._enter_weight_paint(context, mesh, rig, (chosen,))
        mesh.vertex_groups.active_index = mesh.vertex_groups[chosen].index
        _entered(context, mesh, rig, chosen)
        state.update(phase='PAINT', paint_group=chosen)
    except Exception as original:
        try:
            _rollback(context, state, previous)
        except Exception as recovery:
            raise RuntimeError(f'Resume Painting failed: {original}. Recovery also failed: {recovery}') from original
        raise


def show_painting(context, rig=None):
    """Original UI route: start, resume or re-enter the same painting session."""
    if _STATE is None or not _references_valid(_STATE):
        if rig is not None and _resolve(context)[1] != rig:
            raise ValueError('The selected Mesh is not bound to this character Armature.')
        enter(context)
    else:
        state = _session(context)
        if rig is not None and state['rig'] != rig:
            raise ValueError('Finish the other Armature painting session first.')
        chosen = (_paint_group(state) if state.get('phase', 'PAINT') == 'PAINT'
                  else state['paint_group'])
        _resume_into_paint(context, state, chosen)
    return 'PAINT'


def show_controls(context, rig=None, *, repair_legacy_display=False):
    """Controls UI route: expose posing controls without ending the session."""
    state = _session(context)
    if rig is not None and state['rig'] != rig:
        raise ValueError('Finish the other Armature painting session first.')
    chosen = (_paint_group(state) if state.get('phase', 'PAINT') == 'PAINT'
              else state['paint_group'])
    _preview_into_pose(context, state, chosen, repair_legacy=repair_legacy_display)
    return 'POSE_PREVIEW'


def leave(context, *, repair_legacy_display=False, keep_pose=False):
    """Restore the test pose, then show verified current posing participants."""
    global _STATE, _FINISH_STATUS
    state = _session(context)
    mesh, rig = state['mesh'], state['rig']
    restore_pose = not keep_pose and state.get('pose_baseline') is not None
    previous_state = _transition_checkpoint(context, state, include_pose=restore_pose)
    chosen = state.get('paint_group')
    if state.get('phase', 'PAINT') == 'PAINT':
        try:
            chosen = _paint_group(state)
        except ValueError:
            # An unrelated active group must not prevent ending the session.
            # It is never converted into a group or a posing participant.
            pass
    try:
        context.scene.tool_settings.use_keyframe_insert_auto = False
        if restore_pose:
            _pose_session().restore(context, mesh, rig, state['pose_baseline'])
        targets = _controls_targets(state, allow_empty=True)
        from . import bone_collections
        ready = bone_collections.body_collection(rig) is not None
        if ready:
            if state.get('controls_display_version') == 2 and state.get('phase') == 'POSE_PREVIEW':
                state['controls_display'] = _display(rig)
            names = _normal_controls_display(context, state, repair_legacy=repair_legacy_display)
            targets = dict(targets, names=names,
                controls=tuple(name for name in targets['controls'] if name in names),
                sources=tuple(name for name in targets['sources'] if name in names),
                selection_unresolved=targets['unresolved'],
                selection_hint_status='UNRESOLVED' if targets['unresolved'] else 'AVAILABLE',
                display_scope='NORMAL_PUBLIC_BODY')
        else:
            # Finishing must remain possible even for unsupported native rigs.
            # The saved view is safe recovery, not a claim of posing capability.
            _restore_display(rig, state['display'])
        _set_mesh_flags(mesh, state['mesh_flags'])
        previous = state['context']
        selected, active_name = _posing_selection(rig, targets, chosen, previous,
                                                 previous_state['context'])
        _paint()._enter_pose_mode(context, rig, selected, active_name)
        for obj in previous['selected_objects']:
            if obj != rig and _paint()._object_is_available(context, obj):
                obj.select_set(True)
        if keep_pose and chosen and mesh.vertex_groups.get(chosen) is not None:
            mesh.vertex_groups.active_index = mesh.vertex_groups[chosen].index
        else:
            mesh.vertex_groups.active_index = previous['active_group_index']
        if (context.mode != 'POSE' or context.view_layer.objects.active != rig
                or rig.mode != 'POSE'
                or _mesh_flags(mesh) != state['mesh_flags']):
            raise RuntimeError('Blender could not finish painting in the requested posing view.')
        if ready and (not rig.data.show_bone_custom_shapes or any(
                rig.data.bones[name].hide or rig.data.bones[name].hide_select
                for name in targets['names'])):
            raise RuntimeError('Blender did not expose the verified posing participants.')
        if 'auto_key' in state:
            context.scene.tool_settings.use_keyframe_insert_auto = state['auto_key']
        else:
            # Old display-only sessions never owned the user's Auto Key flag.
            context.scene.tool_settings.use_keyframe_insert_auto = previous_state['auto']
        message = ('Painting finished; weights kept. Normal Body controls and saved foreign display are available.' if ready else
                   'Painting finished; weights kept. No managed Body controls display is available; the previous display was restored.')
        if ready and targets['unresolved']:
            message = 'Painting finished; weights kept. Normal Body controls are shown; automatic control selection is unresolved for some painted groups.'
        _FINISH_STATUS = dict(scene=state['scene'], layer=state['layer'], rig=rig,
                              rig_data=rig.data, mesh=mesh, mesh_data=mesh.data, paint_group=chosen,
                              ready=ready, message=message,
                              legacy_visibility_repair=state.get('legacy_controls_repair'))
    except Exception as original:
        try:
            _editable(mesh, rig)
            _rollback(context, state, previous_state)
        except Exception as recovery:
            raise RuntimeError(f'Finish Painting failed: {original}. '
                               f'Native state recovery also failed: {recovery}') from original
        raise
    _STATE = None


def end_native(context):
    """End native painting only; absent RAM recovery is never reconstructed."""
    if not can_end_native(context):
        raise ValueError('Finish this painting session in its owning Scene and View Layer first.')
    result = bpy.ops.object.mode_set(mode='OBJECT')
    if 'FINISHED' not in result or context.mode != 'OBJECT':
        raise RuntimeError('Blender could not leave native Weight Paint mode.')


class CHARACTERDESIGNER_OT_paint_weights(Operator):
    bl_idname = 'character_designer.paint_weights'
    bl_label = 'Paint Weights'
    bl_description = 'Paint existing groups with their real Deform bones; keep weights and rig unchanged'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        try:
            return ((_STATE is None or not _references_valid(_STATE))
                    and context.mode in _MODES and context.object is not None
                    and context.object.type in {'MESH', 'ARMATURE'})
        except ReferenceError:
            return False

    def execute(self, context):
        try:
            enter(context, current_pose=True)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_preview_weight_pose(Operator):
    bl_idname = 'character_designer.preview_weight_pose'
    bl_label = 'Return to Controls'
    bl_description = 'Keep painted weights and the current pose, then return to the character controls'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return ((active(context) and phase(context) in {'PAINT', 'POSE_PREVIEW'} and context.mode in _MODES)
                or can_end_native(context))

    def execute(self, context):
        try:
            if not active(context):
                enter(context, current_pose=True)
            leave(context, repair_legacy_display=True, keep_pose=True)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_resume_weight_paint(Operator):
    bl_idname = 'character_designer.resume_weight_paint'
    bl_label = 'Resume Painting'
    bl_description = 'Continue painting the saved vertex group after testing the pose'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return active(context) and phase(context) in {'PAINT', 'POSE_PREVIEW'} and context.mode in _MODES

    def execute(self, context):
        try:
            show_painting(context)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_finish_painting(Operator):
    bl_idname = 'character_designer.finish_painting'
    bl_label = 'Finish Painting'
    bl_description = 'Keep painted weights, restore the saved test pose and return to posing controls'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return active(context) or can_end_native(context)

    def execute(self, context):
        try:
            if active(context):
                legacy = _STATE.get('pose_baseline') is None
                leave(context, repair_legacy_display=True)
                if legacy:
                    self.report({'INFO'}, 'Finished the older display session; it had no saved test-pose baseline.')
                status = last_finish_status(context)
                if status is not None:
                    self.report({'INFO'}, status['message'])
            else:
                end_native(context)
                self.report({'INFO'}, 'Ended native painting. No previous session display or pose was restored.')
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


def draw(layout, context):
    current = phase(context)
    if active(context):
        if current == 'POSE_PREVIEW':
            painting_group = _STATE.get('paint_group')
        else:
            group = _STATE['mesh'].vertex_groups.active
            bone = _STATE['rig'].data.bones.get(group.name) if group is not None else None
            painting_group = group.name if bone is not None and bone.use_deform else None
        layout.label(text='Painting Group: ' + (painting_group or 'Choose a Deform group'))
    elif can_end_native(context):
        layout.operator('character_designer.finish_painting', text='End Native Painting', icon='CHECKMARK')
    elif _STATE is not None:
        layout.label(text='Return to the Scene and View Layer where painting started.', icon='INFO')
    else:
        status = last_finish_status(context)
        if status is not None:
            layout.label(text='Painting finished; weights kept.', icon='CHECKMARK')
            if status['ready']:
                layout.label(text='Normal Body controls display is shown.', icon='INFO')
            else:
                layout.label(text='No managed Body controls display is available.', icon='INFO')
                layout.label(text='Previous display restored.')


CLASSES = (CHARACTERDESIGNER_OT_paint_weights, CHARACTERDESIGNER_OT_preview_weight_pose,
           CHARACTERDESIGNER_OT_resume_weight_paint, CHARACTERDESIGNER_OT_finish_painting)
