"""Create a separate motion-edit scene from the Unity link's exact model FBX.

The existing authoring character is never rebound or used as a retarget target.
Only the imported model receives the already target-evaluated Unity Action.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

import bpy

from . import animation_link, unity_animation


@dataclass
class SourceResult:
    rig: object
    action: object
    first_frame: float
    last_frame: float
    scene: object
    collection: object
    model_file: str
    model_sha256: str
    export_rig_name: str


def _error(message):
    return unity_animation.UnityAnimationError(message)


def _sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _source_model(link, model_file):
    declared = link.get('modelFile') or ''
    chosen = model_file or declared
    if not chosen:
        raise _error('Choose the model FBX currently used by this Unity character.')
    path = Path(bpy.path.abspath(str(chosen))).expanduser().resolve()
    if path.suffix.lower() != '.fbx' or not path.is_file():
        raise _error('Choose an existing character model FBX.')
    if declared and path != Path(bpy.path.abspath(str(declared))).expanduser().resolve():
        raise _error('This link identifies a different model FBX; use the linked model.')
    digest = _sha256(path)
    expected = link.get('modelSha256') or ''
    if expected and (not isinstance(expected, str) or digest != expected.lower()):
        raise _error('The linked model FBX changed. Export a fresh Unity link before importing it.')
    return path, digest


def _packet(link):
    packet = unity_animation.load_package(link['sourcePackage'])
    if packet['_sha256'] != link['sourcePackageSha256'].lower():
        raise _error('The source animation package changed while opening its link.')
    for key in ('targetGuid', 'clipGuid'):
        if str(packet.get(key, '')).lower() != str(link.get(key, '')).lower():
            raise _error('The linked animation package has a different ' + key + '.')
    if type(packet.get('clipLocalId')) is not int or packet['clipLocalId'] != link['clipLocalId']:
        raise _error('The linked animation package identifies a different animation clip.')
    roots = [bone for bone in packet['bones'] if bone['parent'] == -1]
    if (len(roots) != 1 or roots[0]['restSource'] != 'hierarchy'
            or roots[0].get('weighted') or roots[0]['name'] != roots[0].get('path')):
        raise _error('The linked packet needs one explicit, unweighted model object root.')
    name = roots[0]['name']
    if (not name or len(name) > 128 or name != name.strip()
            or any(ord(c) < 32 or ord(c) == 127 or c in '/\\' for c in name)):
        raise _error('The linked model root is not a valid object name.')
    names = [bone['name'] for bone in packet['bones']]
    if len(names) != len(set(names)):
        raise _error('The linked model contains ambiguous bone names.')
    return packet, name


def _context_state(context):
    return {'scene': context.window.scene, 'view_layer': context.window.view_layer,
            'active': context.view_layer.objects.active, 'selected': tuple(context.selected_objects),
            'mode': context.object.mode if context.object else 'OBJECT',
            'playing': unity_animation._playing(context)}


def _restore_context(context, saved, *, playing):
    context.window.scene = saved['scene']
    context.window.view_layer = saved['view_layer']
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in saved['selected']:
        obj.select_set(True)
    context.view_layer.objects.active = saved['active']
    if saved['active'] and saved['active'].mode != saved['mode']:
        bpy.ops.object.mode_set(mode=saved['mode'])
    if playing:
        unity_animation._set_playing(context, saved['playing'])


def import_source(context, manifest_path, model_file=None, start_frame=1):
    """Import one exact Unity-source model into a new scene and bind its Action.

    On success the new motion-edit scene is shown. On failure the original scene,
    selection and mode return, and only IDs created by this synchronous operation
    are removed. No .blend, FBX, character mesh or link manifest is saved here.
    """
    if context.window is None:
        raise _error('A Blender window is required to open a separate animation scene.')
    if context.object and context.object.mode == 'EDIT':
        raise _error('Leave Edit Mode before opening a linked animation character.')
    link = animation_link.load_link(manifest_path)
    packet, export_name = _packet(link)
    path, digest = _source_model(link, model_file)
    saved = _context_state(context)
    existing = set(bpy.data.user_map())
    # FBX images can be reused by filepath. Keep pre-existing display settings
    # intact if the importer selects a different colorspace for its textures.
    image_settings = [(image, image.colorspace_settings.name, image.alpha_mode) for image in bpy.data.images]
    fbx_enabled = 'io_scene_fbx' in context.preferences.addons
    scene = None
    try:
        unity_animation._set_playing(context, False)
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        scene = bpy.data.scenes.new('Unity Animation - ' + str(packet.get('clipName') or 'Motion'))
        scene.render.fps = saved['scene'].render.fps
        scene.render.fps_base = saved['scene'].render.fps_base
        scene.unit_settings.system, scene.unit_settings.scale_length = 'METRIC', 1.0
        collection = bpy.data.collections.new('Unity Source - ' + export_name)
        scene.collection.children.link(collection)
        context.window.scene = scene
        layer = context.view_layer.layer_collection.children[collection.name]
        context.view_layer.active_layer_collection = layer
        if not fbx_enabled:
            bpy.ops.preferences.addon_enable(module='io_scene_fbx')
        # Older model exports can carry authoring recovery registries after
        # their control bones were stripped. This is a native motion-edit rig,
        # so do not import FBX user properties as a partial Body Setup graph.
        imported = bpy.ops.import_scene.fbx(filepath=str(path), use_anim=False,
                                             automatic_bone_orientation=False, use_image_search=False,
                                             use_custom_props=False)
        if 'FINISHED' not in imported:
            raise _error('Blender did not finish importing the linked character FBX.')
        for image, colorspace, alpha_mode in image_settings:
            image.colorspace_settings.name, image.alpha_mode = colorspace, alpha_mode
        rigs = [obj for obj in scene.objects if obj.type == 'ARMATURE']
        if len(rigs) != 1:
            raise _error('The linked FBX must contain exactly one exported character armature.')
        rig = rigs[0]
        if rig.name != export_name and not re.fullmatch(re.escape(export_name) + r'\.\d{3,}', rig.name):
            raise _error('The imported armature does not match the linked model object root.')
        expected = {bone['name'] for bone in packet['bones'] if bone['name'] != export_name}
        if set(rig.data.bones.keys()) != expected:
            raise _error('The imported FBX does not contain the complete linked skeleton.')
        # Imported wrapper parenting is not animation. Detach only this new rig,
        # keeping the FBX world matrix and all skin bindings exactly as imported.
        if rig.parent:
            world = rig.matrix_world.copy()
            rig.parent = None
            rig.matrix_world = world
        rig.data.pose_position = 'POSE'
        for obj in context.selected_objects:
            obj.select_set(False)
        rig.select_set(True)
        context.view_layer.objects.active = rig
        context.view_layer.update()
        # _mapping checks every bind origin and parent; no tolerance relaxation,
        # body-only fallback or X-rest substitution is permitted in this route.
        result = unity_animation._import_package_action(context, rig, packet, start_frame=start_frame)
        if result.action[unity_animation.PACKAGE_HASH_KEY] != packet['_sha256'] or _sha256(path) != digest:
            raise _error('A linked input changed during import; the new editing scene was discarded.')
        current_link = animation_link.load_link(manifest_path)
        if animation_link._identity(current_link) != animation_link._identity(link):
            raise _error('The Link source identity changed during import; reopen the current Link.')
        animation_link.bind_action(rig, result.action, manifest_path, export_name, str(path))
        rig['character_designer_animation_source_model_sha256'] = digest
        # Restore the source context before showing the successful new scene, so
        # its prior active object/mode are preserved when the user switches back.
        _restore_context(context, saved, playing=False)
        context.window.scene = scene
        context.view_layer.objects.active = rig
        return SourceResult(rig, result.action, result.first_frame, result.last_frame,
                            scene, collection, str(path), digest, export_name)
    except Exception:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        _restore_context(context, saved, playing=True)
        for image, colorspace, alpha_mode in image_settings:
            image.colorspace_settings.name, image.alpha_mode = colorspace, alpha_mode
        created = {value for value in set(bpy.data.user_map()) - existing if not value.is_embedded_data}
        if created:
            bpy.data.batch_remove(ids=tuple(created))
        raise
    finally:
        if not fbx_enabled and 'io_scene_fbx' in context.preferences.addons:
            bpy.ops.preferences.addon_disable(module='io_scene_fbx')
