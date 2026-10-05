"""Bake one selected Action to an animation-only FBX in an isolated snapshot.

Run Blender on a disposable .blend with ``--python this_file -- job.json``.
This worker never saves the .blend. The full source control rig evaluates before
any export skeleton is cleaned; only the independent export copy loses controls.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from array import array
from pathlib import Path
import sys
import traceback
import uuid

import bpy
from mathutils import Matrix


MAX_SAMPLES = 20000
MAX_BONES = 1024
MAX_BONE_SAMPLES = 1000000
MATRIX_TOLERANCE = 2e-5
TRANSFORMS = {
    'location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale',
    'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale',
}


class AnimationExportError(ValueError):
    pass


def _sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AnimationExportError(f'{name} must be a finite number.')
    value = float(value)
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise AnimationExportError(f'{name} is outside the supported range.')
    return value


def _export_rig_name(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 128 or any(ord(character) < 32 or ord(character) == 127
                                       or character in '/\\' for character in value)):
        raise AnimationExportError('export_rig_name must be one nonempty object name without path separators or control characters.')
    return value


def _curves(action, slot=None):
    if action.is_action_layered:
        return [curve for layer in action.layers for strip in layer.strips
                for bag in getattr(strip, 'channelbags', ())
                if slot is None or bag.slot_handle == slot.handle for curve in bag.fcurves]
    return list(getattr(action, 'fcurves', ()))


def _slot(rig, action, handle):
    if not action.is_action_layered:
        if handle not in (None, 0):
            raise AnimationExportError('The selected legacy Action has no Action slot.')
        return None
    slots = [slot for slot in action.slots if slot.target_id_type == 'OBJECT']
    if handle is not None:
        if isinstance(handle, bool) or not isinstance(handle, int):
            raise AnimationExportError('action_slot must be an integer Action slot handle.')
        result = next((slot for slot in slots if slot.handle == handle), None)
    else:
        active = rig.animation_data
        result = (active.action_slot if active and active.action == action else None)
        if result is None and len(slots) == 1:
            result = slots[0]
    if result is None:
        raise AnimationExportError('Choose the character Object slot from the selected Action explicitly.')
    return result


def _validate_action(rig, action, slot):
    curves = [curve for curve in _curves(action, slot) if not curve.mute]
    if not curves:
        raise AnimationExportError('The selected Action/slot contains no enabled animation curves.')
    prefixes = tuple(bone.path_from_id() for bone in rig.pose.bones)
    unsupported = []
    for curve in curves:
        path = curve.data_path
        valid = path in TRANSFORMS or path.startswith('["') or path.startswith("['") or path.startswith('constraints[')
        for prefix in prefixes:
            if not path.startswith(prefix):
                continue
            suffix = path[len(prefix):]
            valid = (suffix.removeprefix('.') in TRANSFORMS or suffix.startswith('["')
                     or suffix.startswith("['") or suffix.startswith('.constraints[') or suffix.startswith('.ik_'))
            break
        try:
            rig.path_resolve(path)
        except (ValueError, AttributeError, KeyError):
            valid = False
        if not valid:
            unsupported.append(path)
    if unsupported:
        raise AnimationExportError('Unsupported or missing selected-Action channels: ' + ', '.join(sorted(set(unsupported))) +
                                   '. Animation-only export supports rig transforms and rig control properties.')
    return len(curves)


def _animated(block):
    ad = getattr(block, 'animation_data', None)
    return bool(ad and ((ad.action and _curves(ad.action, getattr(ad, 'action_slot', None)))
                        or any(not driver.mute for driver in ad.drivers)
                        or (ad.use_nla and any(not track.mute and any(not strip.mute for strip in track.strips)
                                              for track in ad.nla_tracks))))


def _dependencies(obj):
    if obj.parent is not None:
        yield obj.parent
    constraints = list(obj.constraints)
    if obj.type == 'ARMATURE':
        constraints += [constraint for bone in obj.pose.bones for constraint in bone.constraints]
    for constraint in constraints:
        if constraint.mute:
            continue
        for name in ('target', 'pole_target', 'space_object'):
            target = getattr(constraint, name, None)
            if target is not None:
                yield target
    for block in (obj, obj.data):
        ad = getattr(block, 'animation_data', None)
        for curve in ad.drivers if ad else ():
            if curve.mute:
                continue
            for variable in curve.driver.variables:
                for target in variable.targets:
                    if target.id is not None:
                        yield target.id


def _check_external_dependencies(rig):
    pending, seen = list(_dependencies(rig)), {rig.as_pointer(), rig.data.as_pointer()}
    while pending:
        block = pending.pop()
        if block.as_pointer() in seen:
            continue
        seen.add(block.as_pointer())
        if _animated(block):
            raise AnimationExportError(f'External animated dependency "{block.name}" drives this rig. '
                                       'Bake that dependency into the selected rig Action before exporting one Action.')
        if isinstance(block, bpy.types.Object):
            pending.extend(_dependencies(block))


def _omitted_channels(rig, action, slot):
    omitted = []
    if action.is_action_layered:
        for other in action.slots:
            if other != slot and _curves(action, other):
                omitted.append('Other Action slot: ' + other.identifier)
    related = {rig, *rig.children_recursive}
    related.update(obj for obj in bpy.context.scene.objects if obj.type == 'MESH' and
                   any(modifier.type == 'ARMATURE' and modifier.object == rig for modifier in obj.modifiers))
    for obj in sorted(related, key=lambda item: item.name):
        if obj != rig and _animated(obj):
            omitted.append('Other character object animation: ' + obj.name)
        if obj.data is not None and _animated(obj.data):
            omitted.append('Object-data animation: ' + obj.name)
        keys = getattr(obj.data, 'shape_keys', None)
        if keys is not None and _animated(keys):
            omitted.append('Shape Key animation: ' + obj.name)
        for material in getattr(obj.data, 'materials', ()):
            if material is not None and (_animated(material) or _animated(material.node_tree)):
                omitted.append('Material animation: ' + material.name)
    return sorted(set(omitted))


def _trs(matrix, label):
    if any(not math.isfinite(value) for row in matrix for value in row):
        raise AnimationExportError(label + ': non-finite transform.')
    if abs(matrix.to_3x3().determinant()) < 1e-10:
        raise AnimationExportError(label + ': singular transform.')
    location, rotation, scale = matrix.decompose()
    if min(scale) <= 0:
        raise AnimationExportError(label + ': mirrored or non-positive scale is unsupported.')
    restored = Matrix.LocRotScale(location, rotation, scale)
    error = max(abs(matrix[row][column] - restored[row][column]) for row in range(4) for column in range(4))
    if error > MATRIX_TOLERANCE:
        raise AnimationExportError(f'{label}: shear cannot be represented by FBX TRS channels (error {error:.6g}).')
    return location, rotation, scale


def _frame(scene, frame):
    integer = math.floor(frame)
    scene.frame_set(integer, subframe=frame - integer)


def _model_helpers():
    spec = importlib.util.spec_from_file_location('cdesigner_model_export_helpers', Path(__file__).with_name('unity_export_worker.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_curve(bag, path, component, frames, values):
    curve = bag.fcurves.new(data_path=path, index=component)
    curve.keyframe_points.add(len(frames))
    curve.keyframe_points.foreach_set('co', [value for pair in zip(frames, values) for value in pair])
    for key in curve.keyframe_points:
        key.interpolation = 'LINEAR'
    curve.update()


def export_job(job):
    # This disposable worker samples the explicit Action. Transient Wiggle
    # motion must never become animation keys through background frame events.
    for scene in bpy.data.scenes:
        settings = getattr(scene, 'wiggle', None)
        if settings is not None:
            settings.enable = False
    for obj in bpy.data.objects:
        settings = getattr(obj, 'wiggle', None)
        if obj.type == 'ARMATURE' and settings is not None:
            settings.freeze = True
    """Mutate only this disposable worker session and publish one staged FBX."""
    source_scene = bpy.context.scene
    rig = bpy.data.objects.get(job.get('rig', ''))
    action = bpy.data.actions.get(job.get('action', ''))
    if rig is None or rig.type != 'ARMATURE':
        raise AnimationExportError('The selected armature is missing from the snapshot.')
    if action is None or rig.mode != 'OBJECT' and rig.mode != 'POSE':
        raise AnimationExportError('Choose an existing Action and finish armature Edit Mode before export.')
    if rig.library or rig.data.library or rig.parent or rig.data.pose_position != 'POSE':
        raise AnimationExportError('Animation export needs a local, unparented armature in Pose Position.')
    export_name = _export_rig_name(job.get('export_rig_name', rig.name))
    snapshot_frame = source_scene.frame_current
    snapshot_subframe = source_scene.frame_subframe
    if rig.data.animation_data:
        raise AnimationExportError('Animated armature data is unsupported; only pose/control animation can be exported.')
    start = _number(job.get('frame_start'), 'frame_start', -1048574, 1048574)
    end = _number(job.get('frame_end'), 'frame_end', -1048574, 1048574)
    if end <= start:
        raise AnimationExportError('frame_end must be greater than frame_start.')
    fps = _number(job.get('fps', source_scene.render.fps), 'fps', 1, 32767)
    fps_base = _number(job.get('fps_base', source_scene.render.fps_base), 'fps_base', 1e-5, 1e6)
    if fps != int(fps):
        raise AnimationExportError('fps must be an integer; represent fractional frame rates with fps_base.')
    unit_scale = _number(job.get('unit_scale', source_scene.unit_settings.scale_length), 'unit_scale', 1e-8, 1e8)
    requested_rate = _number(job.get('sample_rate', 60), 'sample_rate', 1, 240)
    duration = (end - start) / (fps / fps_base)
    intervals = max(1, math.ceil(duration * requested_rate - 1e-9))
    if intervals + 1 > MAX_SAMPLES:
        raise AnimationExportError(f'This clip needs {intervals + 1} samples; the limit is {MAX_SAMPLES}.')
    effective_rate = intervals / duration
    export_fps = max(1, int(round(requested_rate)))
    export_fps_base = export_fps / effective_rate
    if not 1e-5 <= export_fps_base <= 1e6:
        raise AnimationExportError('The requested duration cannot be represented safely by Blender frame timing.')
    loop = job.get('loop', False)
    if not isinstance(loop, bool):
        raise AnimationExportError('loop must be true or false.')
    name = str(job.get('name') or action.name).strip()
    if not name or len(name) > 128 or any(ord(character) < 32 for character in name):
        raise AnimationExportError('Choose a clip name between 1 and 128 characters without control characters.')
    filename = str(job.get('filename') or name + '.fbx')
    if Path(filename).name != filename or not filename.lower().endswith('.fbx') or any(c in filename for c in '<>:"/\\|?*'):
        raise AnimationExportError('filename must be one valid .fbx filename, without directories.')
    stage = Path(job['stage']).resolve()
    stage.mkdir(parents=True, exist_ok=True)
    # libraries.write({rig, action}) snapshots include forward dependencies but
    # need not include a Scene. Reconstruct only the worker evaluation context.
    source_scene = bpy.data.scenes.new('CDesigner Animation Evaluation')
    source_scene.render.fps, source_scene.render.fps_base = int(fps), fps_base
    source_scene.unit_settings.system = 'METRIC'
    source_scene.unit_settings.scale_length = unit_scale
    for obj in tuple(bpy.data.objects):
        source_scene.collection.objects.link(obj)
    bpy.context.window.scene = source_scene
    source_scene.frame_set(snapshot_frame, subframe=snapshot_subframe)
    bpy.context.view_layer.update()
    # A rig/Action-only library snapshot has no evaluated object world matrix
    # until it belongs to a scene. Capture its reference after evaluation, before
    # changing the selected Action, NLA settings or animation sampling frame.
    snapshot_world = rig.matrix_world.copy()
    _trs(snapshot_world, 'Snapshot rig object')
    slot = _slot(rig, action, job.get('action_slot'))
    curve_count = _validate_action(rig, action, slot)
    _check_external_dependencies(rig)
    omitted = sorted(set(_omitted_channels(rig, action, slot) + job.get('unsupported_channels', [])))
    warnings = ['Animation-only export contains skeleton motion; meshes, materials, Shape Keys, cloth simulation, and animation events are not exported.',
                'Only the selected Action/slot is evaluated; rig NLA is disabled. Unkeyed controls use the snapshot values.']
    if omitted:
        warnings.append('Additional animated channels were omitted from this bones-only clip; see unsupported_channels.')
    helpers = _model_helpers()
    retained = [bone.name for bone in rig.data.bones if not helpers._is_control(bone)]
    if not retained or len(retained) > MAX_BONES or not any(rig.data.bones[name].use_deform for name in retained):
        raise AnimationExportError(f'The retained skeleton must contain deform bones and no more than {MAX_BONES} bones.')
    if len(retained) * (intervals + 1) > MAX_BONE_SAMPLES:
        raise AnimationExportError('The selected range exceeds one million bone samples; choose a shorter range or lower sampling rate.')
    discarded = {bone.name for bone in rig.data.bones if bone.name not in retained and bone.use_deform}
    if discarded:
        for mesh in source_scene.objects:
            if mesh.type != 'MESH' or not any(mod.type == 'ARMATURE' and mod.object == rig for mod in mesh.modifiers):
                continue
            indices = {group.index for group in mesh.vertex_groups if group.name in discarded}
            if any(entry.group in indices and entry.weight > 1e-8 for vertex in mesh.data.vertices for entry in vertex.groups):
                raise AnimationExportError(mesh.name + ': a generated control has real skin weights and cannot be omitted safely.')

    source_name, action_name = rig.name, action.name
    bpy.context.view_layer.update()
    origin = snapshot_world.translation.copy()
    normalization = Matrix.Translation(-origin)
    reference_world = normalization @ snapshot_world
    rest = {bone.name: bone.matrix_local.copy() for bone in rig.data.bones if bone.name in retained}
    ad = rig.animation_data_create()
    if ad.use_tweak_mode:
        raise AnimationExportError('Finish NLA Tweak Mode before exporting one Action.')
    ad.action = action
    if slot is not None:
        ad.action_slot = slot
    ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.0
    ad.action_extrapolation = 'HOLD'
    samples = []
    for index in range(intervals + 1):
        frame = start + (end - start) * index / intervals
        _frame(source_scene, frame)
        bpy.context.view_layer.update()
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        world = normalization @ evaluated.matrix_world
        _trs(world, f'Rig object at sample {index}')
        poses = {bone: evaluated.pose.bones[bone].matrix.copy() for bone in retained}
        for bone, pose in poses.items():
            if any(not math.isfinite(value) for row in pose for value in row):
                raise AnimationExportError(f'{bone}: non-finite evaluated pose at sample {index}.')
        samples.append((world, poses))

    # No source constraints, drivers, Rest or Body Setup are removed. Their
    # already evaluated matrices now belong to a separate FK export skeleton.
    export_rig = rig.copy()
    export_rig.data = rig.data.copy()
    export_rig.animation_data_clear()
    export_rig.parent = None
    for constraint in tuple(export_rig.constraints):
        export_rig.constraints.remove(constraint)
    for bone in export_rig.pose.bones:
        for constraint in tuple(bone.constraints):
            bone.constraints.remove(constraint)
    export_scene = bpy.data.scenes.new('CDesigner Animation Export')
    export_scene.unit_settings.system = 'METRIC'
    export_scene.unit_settings.scale_length = unit_scale
    export_scene.collection.objects.link(export_rig)
    bpy.context.window.scene = export_scene
    bpy.context.view_layer.objects.active = export_rig
    export_rig.select_set(True)
    if export_rig.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    cleaned = helpers._clean_skeleton(bpy.context, export_rig, [export_rig])
    if set(cleaned) != set(retained):
        raise AnimationExportError('The animation skeleton filter disagrees with the current model exporter.')
    for bone in export_rig.data.bones:
        if max(abs(bone.matrix_local[i][j] - rest[bone.name][i][j]) for i in range(4) for j in range(4)) > 2e-5:
            raise AnimationExportError(bone.name + ': export skeleton cleanup changed its Rest matrix.')
    for block in (export_rig, export_rig.data, *export_rig.data.bones):
        for key in tuple(block.keys()):
            if key.startswith('character_designer_'):
                del block[key]
    # Keep the exact object path used by the separate model FBX. This rename is
    # confined to the unsaved worker session and happens after all source sampling.
    rig.name = '.CDesigner Sampled Rig ' + uuid.uuid4().hex[:8]
    occupied = bpy.data.objects.get(export_name)
    if occupied is not None and occupied is not export_rig:
        occupied.name = '.CDesigner Snapshot Object ' + uuid.uuid4().hex[:8]
    export_rig.name = export_name
    if export_rig.name != export_name:
        raise AnimationExportError('Blender could not preserve the linked model root name exactly.')
    export_rig.data.pose_position = 'POSE'
    export_rig.rotation_mode = 'QUATERNION'
    export_rig.delta_location = (0, 0, 0)
    export_rig.delta_rotation_euler = (0, 0, 0)
    export_rig.delta_rotation_quaternion = (1, 0, 0, 0)
    export_rig.delta_scale = (1, 1, 1)
    for bone in export_rig.pose.bones:
        bone.rotation_mode = 'QUATERNION'
    channels, previous = {}, {}

    def add_channels(prefix, matrix, label):
        location, rotation, scale = _trs(matrix, label)
        if prefix in previous and rotation.dot(previous[prefix]) < 0:
            rotation.negate()
        previous[prefix] = rotation.copy()
        for property_name, values in (('location', location), ('rotation_quaternion', rotation), ('scale', scale)):
            for component, value in enumerate(values):
                channels.setdefault((prefix + property_name, component), array('f')).append(value)

    # Both the static FBX hierarchy and the first Take sample must contain real
    # Rest: Unity can use that first sample while creating a copied Avatar.
    # Motion starts at frame 1; the companion importer trims the reference frame.
    add_channels('', reference_world, 'Reference rig object')
    for bone in export_rig.pose.bones:
        add_channels(bone.path_from_id() + '.', Matrix.Identity(4), bone.name + ' reference pose')
    for index, (world, poses) in enumerate(samples):
        add_channels('', world, f'Rig object at sample {index}')
        for bone in export_rig.data.bones:
            parent = {'parent_matrix': poses[bone.parent.name], 'parent_matrix_local': bone.parent.matrix_local} if bone.parent else {}
            basis = bone.convert_local_to_pose(poses[bone.name], bone.matrix_local, invert=True, **parent)
            add_channels(export_rig.pose.bones[bone.name].path_from_id() + '.', basis, f'{bone.name} at sample {index}')
    baked = bpy.data.actions.new(name)
    baked_slot = baked.slots.new(id_type='OBJECT', name=export_rig.name)
    bag = baked.layers.new('Baked Skeleton Motion').strips.new(type='KEYFRAME').channelbags.new(baked_slot)
    frames = list(range(intervals + 2))
    for (path, component), values in channels.items():
        _write_curve(bag, path, component, frames, values)
    export_ad = export_rig.animation_data_create()
    export_ad.action, export_ad.action_slot, export_ad.use_nla = baked, baked_slot, False
    export_scene.frame_start, export_scene.frame_end = 0, intervals + 1
    export_scene.render.fps, export_scene.render.fps_base = export_fps, export_fps_base
    maximum_error = 0.0
    for index, (world, poses) in enumerate(samples):
        export_scene.frame_set(index + 1)
        bpy.context.view_layer.update()
        evaluated = export_rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        pairs = [(evaluated.matrix_world, world)] + [(evaluated.pose.bones[bone].matrix, poses[bone]) for bone in retained]
        error = max(abs(actual[i][j] - expected[i][j]) for actual, expected in pairs for i in range(4) for j in range(4))
        maximum_error = max(maximum_error, error)
        if error > 2e-4:
            raise AnimationExportError(f'The independent FK skeleton cannot reproduce sample {index} (matrix error {error:.6g}).')
    export_scene.frame_set(0)
    bpy.context.view_layer.update()
    if max(abs(export_rig.matrix_world[i][j] - reference_world[i][j])
           for i in range(4) for j in range(4)) > MATRIX_TOLERANCE:
        raise AnimationExportError('The FBX reference frame did not restore the export object transform.')
    if any(max(abs(bone.matrix[i][j] - bone.bone.matrix_local[i][j]) for i in range(4) for j in range(4)) > 2e-5
           for bone in export_rig.pose.bones):
        raise AnimationExportError('The FBX reference frame did not restore the export skeleton to Rest.')
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    temporary = stage / ('.' + uuid.uuid4().hex + '.fbx')
    destination = stage / filename
    try:
        exported = bpy.ops.export_scene.fbx(
            filepath=str(temporary), use_selection=True, object_types={'ARMATURE'},
            use_mesh_modifiers=False, use_armature_deform_only=False, add_leaf_bones=False,
            bake_anim=True, bake_anim_use_all_bones=True, bake_anim_use_nla_strips=False,
            bake_anim_use_all_actions=False, bake_anim_force_startend_keying=True,
            bake_anim_step=1.0, bake_anim_simplify_factor=0.0,
            axis_forward='-Z', axis_up='Y', primary_bone_axis='Y', secondary_bone_axis='X',
            apply_unit_scale=True, apply_scale_options='FBX_SCALE_UNITS', global_scale=1.0,
            bake_space_transform=False, armature_nodetype='NULL',
            path_mode='AUTO', embed_textures=False, use_custom_props=False)
        if 'FINISHED' not in exported or not temporary.is_file() or temporary.stat().st_size == 0:
            raise AnimationExportError('Blender did not produce the animation FBX.')
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        'ok': True, 'filename': filename, 'files': [filename], 'name': name, 'action': action_name,
        'action_slot': slot.handle if slot is not None else 0, 'rig': export_name, 'source_rig': source_name,
        'frame_start': start, 'frame_end': end, 'frameRange': [start, end], 'duration': duration,
        'fps': effective_rate, 'effective_sample_rate': effective_rate, 'sample_rate': requested_rate,
        'source_fps': fps, 'source_fps_base': fps_base, 'loop': loop, 'samples': len(samples),
        'has_reference_frame': True, 'reference_frame': 0,
        'playable_first_frame': 1, 'playable_last_frame': intervals + 1,
        'bones': cleaned, 'root_bones': [bone.name for bone in export_rig.data.bones if bone.parent is None],
        'origin': list(origin), 'unit_scale': unit_scale, 'maximum_bake_matrix_error': maximum_error,
        'source_curve_count': curve_count, 'unsupported_channels': omitted, 'warnings': warnings,
        'limitation': 'One selected Action, baked skeleton motion only. No meshes, shape animation, physics or events.',
        'sha256': _sha256(destination),
    }


def main():
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if len(arguments) != 1:
        raise AnimationExportError('Expected one animation export job JSON path after --.')
    job = json.loads(Path(arguments[0]).read_text(encoding='utf8'))
    result_path = Path(job['stage']) / 'result.json'
    try:
        result = export_job(job)
    except Exception as error:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({'ok': False, 'error': str(error), 'traceback': traceback.format_exc()}, indent=2), encoding='utf8')
        raise
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print('CDESIGNER_ANIMATION_EXPORT_OK', json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
