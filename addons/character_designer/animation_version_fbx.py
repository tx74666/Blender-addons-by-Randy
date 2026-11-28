"""Read one baked FBX take for comparison on the original character."""
from pathlib import Path
import math

import bpy

from .animation_export_inputs import _curves
from .animation_link_source import _restore_image_settings
from .unity_export_worker import _is_control
from . import unity_animation


def _matrix_values(matrix):
    return [value for row in matrix for value in row]


def _take(scene, source, target, skip_reference):
    ad = source.animation_data
    if not ad or not ad.action or len(ad.action.slots) != 1:
        raise ValueError('The FBX must contain one animation for one character.')
    bones = list(source.data.bones)
    if {bone.name for bone in bones} != {bone.name for bone in target.data.bones if not _is_control(bone)}:
        raise ValueError('The FBX skeleton differs from this character. Export the take from the same character rig.')
    times = sorted({round(float(key.co.x), 5)
                    for curve in _curves(ad.action, ad.action_slot_handle)
                    for key in curve.keyframe_points})
    if not times or any(not math.isfinite(frame) for frame in times):
        raise ValueError('The FBX has no usable keyframes.')
    if len(times) * len(bones) > 1_000_000:
        raise ValueError('Export a shorter animation take for comparison.')
    indices = {bone.name: i for i, bone in enumerate(bones)}
    fps = scene.render.fps  # The FBX importer encodes seconds using the integer FPS field.
    frame = times[0]
    base = math.floor(frame)
    scene.frame_set(base, subframe=frame - base)
    rest_world = source.matrix_world.copy()
    if skip_reference:
        if len(times) < 2 or any(
                (source.pose.bones[bone.name].matrix.translation - bone.head_local).length > 0.0002
                or source.pose.bones[bone.name].matrix.to_quaternion().rotation_difference(
                    bone.matrix_local.to_quaternion()).angle % (2 * math.pi) > 0.001
                for bone in bones):
            raise ValueError('No rest reference at the first key. Uncheck Skip Export Reference Frame for this FBX.')
        times = times[1:]
    rest = [{'name': bone.name, 'parent': indices[bone.parent.name] if bone.parent else -1,
             'rest': _matrix_values(rest_world @ bone.matrix_local), 'restSource': 'bindpose'}
            for bone in bones]
    frames = []
    for frame in times:
        base = math.floor(frame)
        scene.frame_set(base, subframe=frame - base)
        world = source.matrix_world
        frames.append({'time': (frame - times[0]) / fps, 'poses': [
            {'matrix': _matrix_values(world @ source.pose.bones[bone.name].matrix)} for bone in bones]})
    return {'bones': rest, 'frames': frames, 'duration': frames[-1]['time']}


def read(context, target, filepath, skip_reference):
    path = Path(bpy.path.abspath(filepath))
    if path.suffix.lower() != '.fbx' or not path.is_file():
        raise ValueError('Choose an existing animation FBX.')
    existing = set(bpy.data.user_map())
    image_settings = [(image, image.colorspace_settings.name, image.alpha_mode) for image in bpy.data.images]
    fbx_enabled = 'io_scene_fbx' in context.preferences.addons
    scene = bpy.data.scenes.new('.Version FBX Import')
    try:
        scene.unit_settings.system = 'METRIC'
        # FBX import may change the scene's FPS. Its objects, selection and time
        # also belong exclusively to this scene.
        with context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            if not fbx_enabled:
                bpy.ops.preferences.addon_enable(module='io_scene_fbx')
            # Call the importer directly: the file-browser operator checks the
            # window's active object/mode even when importing into another scene.
            import io_scene_fbx.import_fbx as importer
            from bpy_extras.io_utils import axis_conversion

            class ImportReport:
                @staticmethod
                def report(levels, message):
                    if 'ERROR' in levels:
                        raise ValueError(message)

            result = importer.load(ImportReport(), context, filepath=str(path),
                                   global_matrix=axis_conversion(from_forward='-Z', from_up='Y').to_4x4(),
                                   use_manual_orientation=False, use_image_search=False,
                                   use_custom_props=False, anim_offset=0.)
            rigs = [obj for obj in scene.objects if obj.type == 'ARMATURE']
            actions = [action for action in bpy.data.actions if action not in existing]
            if ('FINISHED' not in result or len(rigs) != 1 or len(actions) != 1
                    or not rigs[0].animation_data or rigs[0].animation_data.action != actions[0]):
                raise ValueError('The FBX must contain one character and one animation take.')
            packet = _take(scene, rigs[0], target, skip_reference)
    finally:
        try:
            _restore_image_settings(image_settings)
            bpy.data.batch_remove(ids=[block for block in set(bpy.data.user_map()) - existing
                                       if not block.is_embedded_data])
        finally:
            if not fbx_enabled and 'io_scene_fbx' in context.preferences.addons:
                bpy.ops.preferences.addon_disable(module='io_scene_fbx')
    packet['clipName'] = path.stem
    return packet


def bake(context, rig, packet, start_frame, loop):
    packet['loopTime'] = loop
    result = unity_animation._import_package_action(context, rig, packet, start_frame=start_frame, attach=False)
    return result.action, result.action.slots[0].handle
