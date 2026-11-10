"""Explicit native Weight Paint entry; temporary display lives only in RAM."""

import bpy
from bpy.types import Operator

_STATE = None
_MODES = frozenset({'OBJECT', 'POSE', 'PAINT_WEIGHT'})


def _references_valid(state):
    try:
        return all(state[key].as_pointer() for key in
                   ('scene', 'layer', 'rig', 'rig_data', 'mesh', 'mesh_data'))
    except ReferenceError:
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
    if obj is not None and obj.type == 'MESH':
        mesh = obj
    elif obj is not None and obj.type == 'ARMATURE':
        meshes = tuple(item for item in context.selected_objects
                       if item.type == 'MESH' and obj in _bound_rigs(item))
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
    chosen = (group.name if group is not None and group.name in names else
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


def enter(context):
    """Show the selected Mesh's existing Deform groups and enter native painting."""
    global _STATE
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
                 context=_paint()._capture_context_state(context, mesh, rig))
    _STATE = state
    try:
        _show(rig, names)
        _set_mesh_flags(mesh, (True, False, False))
        _paint()._enter_weight_paint(context, mesh, rig, (chosen,))
        mesh.vertex_groups.active_index = mesh.vertex_groups[chosen].index
        _entered(context, mesh, rig, chosen)
    except Exception as original:
        try:
            _editable(mesh, rig)
            _restore_display(rig, state['display'])
            _set_mesh_flags(mesh, state['mesh_flags'])
            _restore_context(context, state, state['context'], state['rig_mode'])
        except Exception as recovery:
            raise RuntimeError(f'Painting entry failed: {original}. '
                               f'Native state recovery also failed: {recovery}') from original
        _STATE = None
        raise


def leave(context):
    """Restore display and return to Pose Mode; newly painted weights stay live."""
    global _STATE
    state = _STATE
    if state is None:
        raise ValueError('There is no active painting display.')
    _owner(context, state)
    mesh, rig = state['mesh'], state['rig']
    display = _display(rig)
    flags, rig_mode = _mesh_flags(mesh), rig.mode
    current = _paint()._capture_context_state(context, mesh, rig)
    try:
        _restore_display(rig, state['display'])
        _set_mesh_flags(mesh, state['mesh_flags'])
        previous = state['context']
        _paint()._enter_pose_mode(context, rig, previous['pose_selection'], previous['active_bone'])
        for obj in previous['selected_objects']:
            if obj != rig and _paint()._object_is_available(context, obj):
                obj.select_set(True)
        mesh.vertex_groups.active_index = previous['active_group_index']
        if (context.mode != 'POSE' or context.view_layer.objects.active != rig
                or rig.mode != 'POSE' or _display(rig) != state['display']
                or _mesh_flags(mesh) != state['mesh_flags']):
            raise RuntimeError('Blender could not finish painting with the original display and Pose Mode.')
    except Exception as original:
        try:
            _editable(mesh, rig)
            _restore_display(rig, display)
            _set_mesh_flags(mesh, flags)
            _restore_context(context, state, current, rig_mode)
        except Exception as recovery:
            raise RuntimeError(f'Finish Painting failed: {original}. '
                               f'Native state recovery also failed: {recovery}') from original
        raise
    _STATE = None


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
            enter(context)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class CHARACTERDESIGNER_OT_finish_painting(Operator):
    bl_idname = 'character_designer.finish_painting'
    bl_label = 'Finish Painting'
    bl_description = 'Restore the previous bone display and return to Pose Mode without restoring weights'
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return active(context)

    def execute(self, context):
        try:
            leave(context)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


def draw(layout, context):
    row = layout.row(align=True)
    row.operator('character_designer.paint_weights', text='Paint Weights', icon='WPAINT_HLT')
    row.operator('character_designer.finish_painting', text='Finish Painting', icon='CHECKMARK')
    if active(context):
        layout.label(text='Select a Deform bone to paint its existing group.', icon='INFO')


CLASSES = (CHARACTERDESIGNER_OT_paint_weights, CHARACTERDESIGNER_OT_finish_painting)
