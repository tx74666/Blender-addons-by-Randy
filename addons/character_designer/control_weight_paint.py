"""Reversible Weight Paint workspace; never changes rigging or weight values."""
import json

import bpy
from . import bone_display, bone_display_sync, selected_bone_weights as paint

SESSION = 'character_designer_weight_workspace_v1'


def active(context):
    return context.scene.get(SESSION)


def target(context):
    rig = bone_display.character_rig(context)
    if rig is None:
        raise ValueError('Select the character rig or a mesh bound to it.')
    return rig


def _bound(mesh, rig):
    return (mesh.type == 'MESH' and
            [m.object for m in mesh.modifiers if m.type == 'ARMATURE' and m.object] == [rig])


def mesh_for(context, rig):
    current = context.object
    if current and _bound(current, rig):
        return current
    # A single shared rig can pose several meshes. An explicitly selected Dress
    # bone identifies its own source, rather than silently painting the Body.
    from . import skirt_rig
    bone = rig.data.bones.active
    source = bone.get(skirt_rig.SOURCE_KEY) if bone and bone.get(skirt_rig.OWNER_KEY) else None
    if source is not None and _bound(source, rig) and source.name in context.view_layer.objects:
        return source
    setup = getattr(context.scene, 'character_designer_setup', None)
    body = getattr(setup, 'body', None)
    if body and _bound(body, rig) and body.name in context.view_layer.objects:
        return body
    candidates = [obj for obj in context.view_layer.objects if _bound(obj, rig)]
    if len(candidates) != 1:
        raise ValueError('Select the bound mesh to paint, or set Body in Character Setup.')
    return candidates[0]


def _paintable_names(rig, mesh):
    from . import limb_ik, skirt_rig
    record = skirt_rig.read_record(mesh) if mesh.get(skirt_rig.RIG_KEY) == rig else None
    if record and skirt_rig.is_shared(record):
        _controls, deform, _mechanism = skirt_rig._bone_collection_layout(record)
        names = deform | {record['controls']['waist']}
        return {name for name in names if rig.data.bones[name].use_deform}
    return {b.name for b in rig.data.bones if b.use_deform
            and b.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS
            and not b.get(skirt_rig.OWNER_KEY)}


def mapped_bone(rig):
    from . import limb_ik, torso_controls, eye_controls
    bone = rig.data.bones.active
    if bone is None:
        return ''
    if bone.use_deform and bone.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS:
        return bone.name
    if 'character_designer_body_original_mode_v1' in rig:
        return ''
    inventory = limb_ik._validate_inventory(rig)
    for entry in inventory['rigs'].values():
        if bone.name in {entry['target'].name, entry['solver_target'].name}:
            return entry['chain'][2]
        if bone.name == entry['pole'].name:
            return entry['chain'][1]
    torso = torso_controls.get_record(rig)
    if torso:
        for source, control in torso['controls'].items():
            if control == bone.name:
                return source
    eyes = eye_controls.get_record(rig)
    if eyes:
        for entry in eyes['constraints']:
            if entry['fields'].get('subtarget') == bone.name:
                return entry['owner']
    return ''


def _state(context, rig, mesh):
    value = paint._capture_context_state(context, mesh, rig)
    refs = {'rig': rig, 'mesh': mesh, 'active': value.pop('active_object'),
            'selected': {str(i): obj for i, obj in enumerate(value.pop('selected_objects'))}}
    value.update(display=bone_display._snapshot(rig), in_front=rig.show_in_front,
                 view_layer=context.view_layer.name,
                 flags={key: [obj.hide_get(), obj.hide_viewport, obj.hide_select]
                        for key, obj in (('rig', rig), ('mesh', mesh))})
    refs['state'] = json.dumps(value)
    return refs


def _restore(context, saved):
    rig, mesh = saved.get('rig'), saved.get('mesh')
    if rig is None or mesh is None:
        raise ValueError('The weight-editing rig or mesh was deleted; undo that deletion first.')
    state = json.loads(saved['state'])
    if context.view_layer.name != state['view_layer']:
        raise ValueError('Return to the view layer where Edit Weights was started.')
    if rig.name not in context.view_layer.objects or mesh.name not in context.view_layer.objects:
        raise ValueError('Restore the rig and mesh to this view layer first.')
    state['selected_objects'] = tuple(obj for obj in saved.get('selected', {}).values() if obj)
    state['active_object'] = saved.get('active')
    bone_display._restore(rig, state['display'])
    rig.show_in_front = state['in_front']
    paint._restore_context_state(context, mesh, rig, state)
    for key in ('rig', 'mesh'):
        obj = saved[key]
        hidden, viewport, select = state['flags'][key]
        obj.hide_set(hidden)
        obj.hide_viewport, obj.hide_select = viewport, select
    bone_display_sync.completed(rig)


def enter(context):
    from . import limb_ik
    if active(context):
        raise ValueError('Use Back to Controls before starting another weight session.')
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Start Edit Weights in Object or Pose Mode.')
    rig = target(context)
    mesh = mesh_for(context, rig)
    if any(obj.library or obj.override_library or not obj.is_editable or obj.data.library
           for obj in (rig, mesh)) or rig.data.users != 1 or mesh.data.users != 1:
        raise ValueError('Edit Weights needs a local, single-user rig and mesh.')
    names = _paintable_names(rig, mesh)
    if not names:
        raise ValueError('This rig has no native deform bones to paint.')
    mapped = mapped_bone(rig)
    selection = [mapped] if mapped in names else []
    saved = _state(context, rig, mesh)
    previous_busy = bone_display_sync._BUSY
    bone_display_sync._BUSY = True
    try:
        for obj in (rig, mesh):
            obj.hide_viewport = obj.hide_select = False
            obj.hide_set(False)
            if not obj.visible_get(view_layer=context.view_layer):
                raise ValueError('Show the rig and mesh collections in this view layer first.')
        rig.show_in_front = True
        rig.data.show_bone_custom_shapes = False
        rig.data.display_type = 'OCTAHEDRAL'
        for collection in rig.data.collections_all:
            collection.is_solo = False
            collection.is_visible = True
        for bone in rig.data.bones:
            hidden = bone.name not in names
            bone_display._set_hidden(rig, bone, hidden)
            bone.hide_select = hidden
        paint._enter_pose_mode(context, rig, selection, mapped)
        mesh.select_set(True)
        context.view_layer.objects.active = mesh
        if bpy.ops.object.mode_set(mode='WEIGHT_PAINT') != {'FINISHED'}:
            raise ValueError('Blender could not enter Weight Paint.')
        if mapped in mesh.vertex_groups:
            mesh.vertex_groups.active_index = mesh.vertex_groups[mapped].index
        context.scene[SESSION] = saved
    except Exception as original:
        try:
            _restore(context, saved)
        except Exception as recovery:
            context.scene[SESSION] = saved
            raise RuntimeError(f'Edit Weights failed: {original}. Workspace recovery needs Back to Controls: {recovery}') from original
        else:
            context.scene.pop(SESSION, None)
        raise
    finally:
        bone_display_sync._BUSY = previous_busy
        bone_display_sync.completed(rig)
    return rig, mesh


def leave(context):
    saved = active(context)
    if not saved:
        raise ValueError('There is no active Edit Weights session.')
    rig = saved.get('rig')
    mesh = saved.get('mesh')
    if rig is None or mesh is None:
        raise ValueError('The weight-editing rig or mesh was deleted; undo that deletion first.')
    current = _state(context, rig, mesh)
    # Keep the recoverable session if Blender cannot restore the context.
    previous_busy = bone_display_sync._BUSY
    bone_display_sync._BUSY = True
    try:
        _restore(context, saved)
        context.scene.pop(SESSION, None)
    except Exception:
        _restore(context, current)
        raise
    finally:
        bone_display_sync._BUSY = previous_busy
        if rig:
            bone_display_sync.completed(rig)


class CHARACTERDESIGNER_OT_control_weights(bpy.types.Operator):
    bl_idname = 'character_designer.control_weights'
    bl_label = 'Edit Weights'
    bl_description = 'Keep this pose while painting native bone weights, then restore the control workspace'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            if active(context):
                leave(context)
            else:
                enter(context)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def draw(layout, context):
    saved = active(context)
    back_label = 'Back to Original' if saved and saved.get('rig') and 'character_designer_body_original_mode_v1' in saved['rig'] else 'Back to Controls'
    layout.operator('character_designer.control_weights',
                    text=back_label if active(context) else 'Edit Weights',
                    icon='LOOP_BACK' if active(context) else 'WPAINT_HLT')


class CHARACTERDESIGNER_PT_control_weights(bpy.types.Panel):
    bl_label = 'Controls / Weights'
    bl_idname = 'CHARACTERDESIGNER_PT_control_weights'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Character Designer'

    @classmethod
    def poll(cls, context):
        from .ui_constants import active_ui_page
        return bool(active(context)) or active_ui_page(context) == 'WEIGHT'

    def draw(self, context):
        draw(self.layout, context)


CLASSES = (CHARACTERDESIGNER_OT_control_weights, CHARACTERDESIGNER_PT_control_weights)


def register():
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)


def unregister():
    if active(bpy.context):
        saved = active(bpy.context)
        layer = bpy.context.scene.view_layers.get(json.loads(saved['state'])['view_layer'])
        try:
            if layer:
                window = bpy.context.window
                if window is None:
                    raise ValueError('Open a window on the original scene to restore the workspace.')
                previous = window.view_layer
                try:
                    window.view_layer = layer
                    leave(bpy.context)
                finally:
                    window.view_layer = previous
            else:
                raise ValueError('The original view layer was removed.')
        except (ValueError, RuntimeError, ReferenceError, KeyError, TypeError) as exc:
            # Keep the saved state recoverable after re-enable without leaving
            # half of Character Designer registered if a context was removed.
            print('Character Designer: weight workspace retained for recovery: ' + str(exc))
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
