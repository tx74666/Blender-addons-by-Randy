"""Reusable local weapon assets and their mount on the Animation rig."""

from pathlib import Path
import math
import uuid

import bpy
from mathutils import Euler, Matrix

from . import animation_composition_weapon as display

PROFILE = 'character_designer_weapon_profile_v1'
LOCAL = 'local_weapon_profile'
IMPORTERS = {
    '.fbx': lambda path: bpy.ops.import_scene.fbx(filepath=path, use_anim=False),
    '.obj': lambda path: bpy.ops.wm.obj_import(filepath=path),
    '.gltf': lambda path: bpy.ops.import_scene.gltf(filepath=path),
    '.glb': lambda path: bpy.ops.import_scene.gltf(filepath=path),
}
CALIBRATION = ('mount_location', 'mount_rotation', 'grip_location', 'grip_rotation')


def is_profile(collection):
    return (collection is not None and collection.library is None
            and isinstance(collection.get(PROFILE), str) and bool(collection.objects))


def attachment(rig):
    record = rig.get(display.RECORD) if rig is not None else None
    return record if hasattr(record, 'keys') and LOCAL in record else None


def require_idle(context, rig=None):
    from . import animation, animation_preview_session, animation_worklist_ui, unity_animation

    if not animation._link_idle() or animation_preview_session.status(context):
        raise ValueError('Finish the current preview or export before editing weapons.')
    if context.mode not in {'OBJECT', 'POSE'} or context.window is None:
        raise ValueError('Use Object or Pose Mode to edit weapons.')
    if rig is None:
        return
    if rig.type != 'ARMATURE' or rig.library or context.view_layer.objects.get(rig.name) != rig:
        raise ValueError('Choose a local character rig in this view layer.')
    if (animation_worklist_ui.original_inputs_live(context)
            or unity_animation.active_preview(rig)
            or 'character_designer_rigging_session_v1' in rig
            or 'character_designer_composition_session_v1' in rig
            or (rig.animation_data and rig.animation_data.use_tweak_mode)):
        raise ValueError('Close the current rigging or Composition edit session before editing weapons.')


def import_profile(context, filepath):
    """Keep the imported meshes in a persistent Blender collection asset."""
    require_idle(context)
    source = Path(bpy.path.abspath(filepath))
    importer = IMPORTERS.get(source.suffix.lower())
    if importer is None or not source.is_file():
        raise ValueError('Choose an existing FBX, OBJ, glTF or GLB file.')
    saved = display._context_capture(context)
    old_objects = set(bpy.data.objects)
    old_collections = set(bpy.data.collections)
    old_assets = display._asset_snapshot()
    profile = None
    try:
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if 'FINISHED' not in importer(str(source)):
            raise ValueError('Blender did not finish importing the weapon.')
        context.view_layer.update()
        imported = set(bpy.data.objects) - old_objects
        meshes = sorted((obj for obj in imported if obj.type == 'MESH'), key=lambda obj: obj.name)
        if not any(obj.data.polygons for obj in meshes):
            raise ValueError('The weapon file contains no mesh surfaces.')
        profile = bpy.data.collections.new(source.stem)
        profile[PROFILE] = str(uuid.uuid4())
        for field in CALIBRATION:
            profile[field] = (0.0, 0.0, 0.0)
        profile['two_handed'] = False
        matrices = [(obj, obj.matrix_world.copy()) for obj in meshes]
        for obj, matrix in matrices:
            obj.animation_data_clear()
            obj.constraints.clear()
            obj.modifiers.clear()
            obj.parent = None
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_basis = matrix
            profile.objects.link(obj)
            for collection in tuple(obj.users_collection):
                if collection != profile:
                    collection.objects.unlink(obj)
        for obj in imported - set(meshes):
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in set(bpy.data.collections) - old_collections - {profile}:
            bpy.data.collections.remove(collection)
        assets = display._new_assets(old_assets)
        for image in assets['images']:
            if image.users and image.source == 'FILE' and not image.packed_file:
                image.pack()
        display._remove_unused_assets(assets)
        profile.asset_mark()
        profile.use_fake_user = True
        display._restore_context(context, saved)
        return profile
    except Exception:
        for obj in set(bpy.data.objects) - old_objects:
            bpy.data.objects.remove(obj, do_unlink=True)
        for collection in set(bpy.data.collections) - old_collections:
            bpy.data.collections.remove(collection)
        for key, blocks in display._new_assets(old_assets).items():
            for block in blocks:
                if block.use_fake_user and block.users == 1:
                    block.use_fake_user = False
        display._remove_unused_assets(display._new_assets(old_assets))
        display._restore_context(context, saved)
        raise


def _transform(location, rotation):
    return Matrix.Translation(location) @ Euler(rotation, 'XYZ').to_matrix().to_4x4()


def _calibration(profile):
    values = {field: tuple(profile[field]) for field in CALIBRATION}
    if any(len(vector) != 3 or not all(math.isfinite(value) for value in vector)
           for vector in values.values()):
        raise ValueError('Weapon mount and grip offsets must be finite XYZ vectors.')
    return values


def arm_chain(rig, side):
    from .limb_ik import _candidate_chains

    candidates = _candidate_chains(rig, 'ARM', side)
    if candidates and (len(candidates) == 1 or candidates[0].score > candidates[1].score):
        return candidates[0]
    return None


def right_hand(rig):
    chain = arm_chain(rig, 'R')
    return chain.end if chain else ''


def attach(context, rig, profile, hand):
    require_idle(context, rig)
    if not is_profile(profile):
        raise ValueError('Select a Weapon Profile.')
    if not rig.pose.bones.get(hand):
        raise ValueError('Choose the right-hand bone.')
    if rig.get(display.RECORD) is not None:
        raise ValueError('Remove the current attachment before mounting another profile.')
    values = _calibration(profile)
    owner = str(uuid.uuid4())
    collection = bpy.data.collections.new('Weapon · ' + profile.name)
    collection[display.OWNER] = owner
    created = []
    try:
        context.scene.collection.children.link(collection)
        anchor = display._bone_anchor(collection, created, owner, rig, hand, 'Weapon Mount')
        weapon = bpy.data.objects.new(profile.name, None)
        created.append(weapon)
        weapon[display.OWNER] = owner
        collection.objects.link(weapon)
        weapon.parent = anchor
        weapon.instance_type, weapon.instance_collection = 'COLLECTION', profile
        weapon.matrix_basis = _transform(values['mount_location'], values['mount_rotation'])
        grip = bpy.data.objects.new('Left Grip · ' + profile.name, None)
        created.append(grip)
        grip[display.OWNER] = owner
        collection.objects.link(grip)
        grip.parent = weapon
        grip.empty_display_type, grip.empty_display_size = 'ARROWS', 0.04
        grip.matrix_basis = _transform(values['grip_location'], values['grip_rotation'])
        grip.hide_render = True
        grip.hide_set(not bool(profile.get('two_handed')))
        rig[display.RECORD] = {
            'schema': display.SCHEMA, 'owner': owner, 'identity': profile[PROFILE],
            'collection': collection, 'scene': context.scene, 'anchor': anchor,
            'geometry_root': weapon, 'grip': grip, LOCAL: profile, 'hand': hand,
            'objects': {str(index): obj for index, obj in enumerate(created)},
        }
        display.validate_owned(rig)
        context.view_layer.update()
    except Exception:
        rig.pop(display.RECORD, None)
        for obj in reversed(created):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(collection)
        raise
    return weapon, grip


def update_profile(context, rig, profile, values, *, two_handed):
    require_idle(context, rig)
    if not is_profile(profile):
        raise ValueError('Select a Weapon Profile.')
    if any(len(values[field]) != 3 or not all(math.isfinite(value) for value in values[field])
           for field in CALIBRATION):
        raise ValueError('Weapon mount and grip offsets must be finite XYZ vectors.')
    from . import animation_weapon_grip

    mounts = []
    for obj in bpy.data.objects:
        record = attachment(obj)
        if record is None or record[LOCAL] != profile:
            continue
        display.validate_owned(obj)
        mounts.append((record['geometry_root'], record['grip'],
                       animation_weapon_grip.constraints(obj, record)))
    mount_matrix = _transform(values['mount_location'], values['mount_rotation'])
    grip_matrix = _transform(values['grip_location'], values['grip_rotation'])
    for field in CALIBRATION:
        profile[field] = tuple(values[field])
    profile['two_handed'] = bool(two_handed)
    for weapon, grip, bindings in mounts:
        weapon.matrix_basis, grip.matrix_basis = mount_matrix, grip_matrix
        for layer in record_view_layers(grip):
            grip.hide_set(not two_handed, view_layer=layer)
        for _bone, constraint in bindings:
            constraint.mute = not two_handed
    context.view_layer.update()


def record_view_layers(obj):
    return (layer for scene in obj.users_scene for layer in scene.view_layers
            if layer.objects.get(obj.name) == obj)


def detach(context, rig):
    require_idle(context, rig)
    record = attachment(rig)
    if record is None or record['scene'] != context.scene:
        raise ValueError('This rig has no local Weapon Profile attachment in this scene.')
    from . import animation_weapon_grip

    display.validate_removal(rig)
    animation_weapon_grip.disable(rig, record)
    display.remove_context(context, rig)
