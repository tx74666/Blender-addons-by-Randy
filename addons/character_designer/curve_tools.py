"""Shared curve mode identity and non-destructive Curve to Mesh output."""

import bpy
from bpy.types import Operator


MODE_KEY = 'character_designer_curve_mode'
MODES = (
    ('GENERAL', 'General', 'General centerlines and supported Curve profiles'),
    ('HAIR', 'Hair', 'Hair width, tip shaping, and Half profile placement'),
)
_MODE_IDS = frozenset(item[0] for item in MODES)


def object_mode(obj):
    """Unmarked centerlines retain their original Hair behavior."""
    mode = obj.get(MODE_KEY, 'HAIR') if obj is not None else 'HAIR'
    return mode if isinstance(mode, str) and mode in _MODE_IDS else 'HAIR'


def settings_mode(settings):
    """Keep the old Hair page route compatible with saved scripts and state."""
    if getattr(settings, 'ui_page', None) == 'HAIR':
        return 'HAIR'
    mode = getattr(settings, 'curve_tools_mode', 'GENERAL')
    return mode if isinstance(mode, str) and mode in _MODE_IDS else 'GENERAL'


def _new_mesh(source, depsgraph):
    return bpy.data.meshes.new_from_object(
        source, preserve_all_data_layers=True, depsgraph=depsgraph,
    )


def _select_output(context, output):
    """Finish selection only after the complete output has been linked."""
    layer = context.view_layer
    if not output.visible_get(view_layer=layer):
        raise ValueError('The output Mesh is not visible in this View Layer.')
    for obj in layer.objects:
        if obj.select_get(view_layer=layer):
            obj.select_set(False, view_layer=layer)
    output.select_set(True, view_layer=layer)
    layer.objects.active = output
    if not output.select_get(view_layer=layer) or layer.objects.active is not output:
        raise ValueError('Blender could not select the output Mesh.')


def _restore_selection(context, selected, active):
    layer = context.view_layer
    for obj in layer.objects:
        wanted = obj in selected
        if obj.select_get(view_layer=layer) != wanted:
            obj.select_set(wanted, view_layer=layer)
    layer.objects.active = active


def mesh_copy(context):
    """Bake the active Curve's evaluated shape into a new Mesh; keep its source."""
    source = context.active_object
    if context.mode != 'OBJECT' or source is None or source.type != 'CURVE':
        raise ValueError('Select one Curve in Object Mode.')

    # Resolve the public package lazily: its CLASSES tuple imports this module.
    from . import _visible_source_collection

    collection = _visible_source_collection(context, source)
    layer = context.view_layer
    selected = frozenset(obj for obj in layer.objects if obj.select_get(view_layer=layer))
    active = layer.objects.active
    mesh = None
    output = None
    try:
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = source.evaluated_get(depsgraph)
        matrix = evaluated.matrix_world.copy()
        mesh = _new_mesh(evaluated, depsgraph)
        if mesh is None:
            raise ValueError('This Curve has no convertible geometry.')
        if not mesh.vertices:
            raise ValueError('This Curve has no convertible geometry.')
        for key in tuple(mesh.keys()):
            if key.startswith('character_designer_'):
                del mesh[key]
        mesh.name = f'{source.name}_Mesh'
        output = bpy.data.objects.new(f'{source.name}_Mesh', mesh)
        # New Object/Mesh IDs intentionally receive no generator or profile tags.
        output.matrix_world = matrix
        for group in source.vertex_groups:
            output.vertex_groups.new(name=group.name).lock_weight = group.lock_weight
        # Preserve effective Object-linked material overrides as Mesh slots.
        for index, slot in enumerate(evaluated.material_slots):
            # Evaluated IDs belong to the depsgraph. Never store those temporary
            # material pointers in the persistent output Mesh.
            material = slot.material.original if slot.material is not None else None
            if index < len(mesh.materials):
                mesh.materials[index] = material
            else:
                mesh.materials.append(material)
        collection.objects.link(output)
        context.view_layer.update()
        _select_output(context, output)
        return output
    except Exception:
        if output is not None:
            bpy.data.objects.remove(output, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        _restore_selection(context, selected, active)
        raise


class CHARACTERDESIGNER_OT_curve_to_mesh_copy(Operator):
    bl_idname = 'character_designer.curve_to_mesh_copy'
    bl_label = 'Curve to Mesh Copy'
    bl_description = (
        'Create a Mesh from this Curve and its current modifiers; keep the source Curve'
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == 'OBJECT' and obj is not None and obj.type == 'CURVE'

    def execute(self, context):
        try:
            output = mesh_copy(context)
        except Exception as exc:
            self.report({'ERROR'}, f'Curve to Mesh cancelled: {exc}')
            return {'CANCELLED'}
        self.report({'INFO'}, f'Created {output.name}; source Curve kept.')
        return {'FINISHED'}


CLASSES = (CHARACTERDESIGNER_OT_curve_to_mesh_copy,)
