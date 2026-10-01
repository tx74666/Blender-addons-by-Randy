"""Prepare two explicit Action-export jobs in one isolated Blender process.

--real-blend X.blend --walk-package fresh-Walk.cdanim.json --output artifacts

Never saves the source .blend and never launches another process. The parent
runs animation_export_worker.py on each returned snapshot/job serially. These
are concrete roundtrip examples, not modifications of production animations.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback
import uuid

import bpy
from mathutils import Matrix, Quaternion

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import unity_animation as ua
from character_designer import unity_animation_controls as controls
from character_designer import animation_export, animation_export_worker, unity_export_worker


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def update_frame(frame):
    ua._set_frame(bpy.context.scene, frame)
    bpy.context.view_layer.update()


def endpoint_matrices(rig, names, frames):
    result = []
    for frame in frames:
        update_frame(frame)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        result.append({name: evaluated.pose.bones[name].matrix.copy() for name in names})
    return result


def edit_walk(rig, result, package, names):
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    endpoints = (result.first_frame, result.last_frame)
    before = endpoint_matrices(rig, names, endpoints)
    pb = rig.pose.bones['forearm.L']
    modified = []
    duration = package['duration']
    if duration <= 0:
        raise ValueError('Walk must contain a nonzero-duration clip.')
    for sample in package['frames']:
        phase = sample['time'] / duration
        if not 0.15 < phase < 0.85:
            continue
        frame = result.first_frame + sample['time'] * fps
        update_frame(frame)
        degrees = 35.0 * math.sin(math.pi * (phase - 0.15) / 0.7) ** 2
        delta = Quaternion((1, 0, 0), math.radians(degrees))
        rotation = pb.matrix_basis.to_quaternion() @ delta
        if pb.rotation_mode == 'QUATERNION':
            if rotation.dot(pb.rotation_quaternion) < 0:
                rotation.negate()
            pb.rotation_quaternion = rotation
            prop = 'rotation_quaternion'
        elif pb.rotation_mode == 'AXIS_ANGLE':
            axis, angle = rotation.to_axis_angle()
            pb.rotation_axis_angle = (angle, *axis)
            prop = 'rotation_axis_angle'
        else:
            pb.rotation_euler = rotation.to_euler(pb.rotation_mode, pb.rotation_euler)
            prop = 'rotation_euler'
        pb.keyframe_insert(prop, frame=frame, group='Edited Left Forearm')
        modified.append({'time_seconds': sample['time'], 'frame': frame, 'extra_local_x_degrees': degrees})
    if not modified or max(value['extra_local_x_degrees'] for value in modified) < 25:
        raise AssertionError('Walk did not have enough interior samples for the explicit forearm edit.')
    for curve in animation_export_worker._curves(result.action, rig.animation_data.action_slot):
        if curve.data_path.startswith(pb.path_from_id() + '.rotation_'):
            for point in curve.keyframe_points:
                point.interpolation = 'LINEAR'
    after = endpoint_matrices(rig, names, endpoints)
    maximum = max(abs(original[name][i][j] - edited[name][i][j])
                  for original, edited in zip(before, after) for name in names
                  for i in range(4) for j in range(4))
    if maximum > 1.0e-5:
        raise AssertionError('The edited Walk changed a loop endpoint: ' + str(maximum))
    return {'native_bone': 'forearm.L', 'edit': 'extra local X flex, smooth 15%-85% time envelope',
            'maximum_extra_degrees': max(value['extra_local_x_degrees'] for value in modified),
            'endpoint_maximum_matrix_change': maximum, 'modified_samples': modified}


def native_wave_samples(rig, plan, times):
    """Author original motion in this native rig's rest frames; no Unity input."""
    names = sorted(plan.mapped_names, key=lambda name: len(rig.data.bones[name].parent_recursive))
    for time in times:
        posed = {}
        envelope = math.sin(math.pi * time) ** 2
        for name in names:
            bone = rig.data.bones[name]
            basis = Matrix.Identity(4)
            if name == 'upper_arm.L':
                basis = Quaternion((0, 0, 1), 0.35 * envelope).to_matrix().to_4x4()
            elif name == 'forearm.L':
                angle = envelope * (0.70 + 0.25 * math.sin(4 * math.pi * time))
                basis = Quaternion((1, 0, 0), angle).to_matrix().to_4x4()
            elif name == 'hand.L':
                basis = Quaternion((0, 1, 0), 0.20 * envelope * math.sin(4 * math.pi * time)).to_matrix().to_4x4()
            parent = bone.parent
            while parent and parent.name not in posed:
                if parent.name not in plan.control_names:
                    raise ValueError('Unmapped native wave parent: ' + parent.name)
                parent = parent.parent
            if bone.parent:
                parent_pose = (posed[parent.name] @ parent.matrix_local.inverted() @ bone.parent.matrix_local
                               if parent else bone.parent.matrix_local)
                kwargs = {'parent_matrix': parent_pose, 'parent_matrix_local': bone.parent.matrix_local}
            else:
                kwargs = {}
            posed[name] = bone.convert_local_to_pose(basis, bone.matrix_local, **kwargs)
        yield posed


def author_wave(rig, plan, name):
    scene = bpy.context.scene
    times = [index / 30.0 for index in range(31)]
    baked = controls.bake_channels(bpy.context, rig, plan, native_wave_samples(rig, plan, times))
    if baked.needs_joint_translation:
        # The solver can preserve tiny recovered joint offsets as well as real
        # translations. Relax connectivity only on this unsaved session copy.
        data = rig.data.copy()
        data.name = rig.data.name + ' Example Wave'
        data.use_fake_user = False
        ua._swap_armature_data(bpy.context, rig, data, allow_joint_translation=True)
    fps = scene.render.fps / scene.render.fps_base
    frames = [1.0 + time * fps for time in times]
    action = bpy.data.actions.new(name)
    action.use_fake_user = True
    slot, bag = ua._new_channelbag(action, rig)
    for (path, index), values in baked.channels.items():
        ua._write_curve(bag, path, index, frames, values)
    action['animation_origin'] = 'blender-original'
    action['animation_description'] = 'One second native left forearm wave, returning to the original rest pose.'
    ad = rig.animation_data_create()
    ad.action, ad.action_slot = action, slot
    ad.action_blend_type, ad.action_influence, ad.use_nla = 'REPLACE', 1.0, False
    maximum = 0.0
    for frame, desired in zip(frames, native_wave_samples(rig, plan, times)):
        update_frame(frame)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        maximum = max(maximum, max(abs(evaluated.pose.bones[bone].matrix[i][j] - matrix[i][j])
                                  for bone, matrix in desired.items() for i in range(4) for j in range(4)))
    if maximum > 3.0e-4:
        raise AssertionError('The authored wave did not reproduce its native samples: ' + str(maximum))
    update_frame(frames[0])
    return action, frames[0], frames[-1], {'duration_seconds': 1.0, 'samples': len(times),
                                        'maximum_control_bake_error': baked.maximum_error,
                                        'maximum_evaluated_pose_error': maximum,
                                        'disposable_joint_translation_data': baked.needs_joint_translation,
                                        'bones_authored': ['upper_arm.L', 'forearm.L', 'hand.L'],
                                        'source_package': None}


def prepare_job(root, rig, action, start, end, loop, source_path, label):
    """Use the existing worker contract without spawning it or publishing files."""
    scene = bpy.context.scene
    directory = root / label
    directory.mkdir()
    stage = directory / 'stage'
    stage.mkdir()
    filename = action.name + '.fbx'
    slot_handle = animation_export._slot(rig, action)
    slot = next(value for value in action.slots if value.handle == slot_handle)
    omitted = animation_export_worker._omitted_channels(rig, action, slot)
    snapshot = directory / 'animation.blend'
    spec = {'rig': rig.name, 'action': action.name, 'action_slot': slot_handle,
            'frame_start': float(start), 'frame_end': float(end),
            'fps': scene.render.fps, 'fps_base': scene.render.fps_base,
            'sample_rate': 30.0, 'unit_scale': scene.unit_settings.scale_length,
            'loop': bool(loop), 'name': action.name, 'stage': str(stage), 'filename': filename,
            'unsupported_channels': omitted,
            'source_package': str(action.get(ua.PACKAGE_KEY, '')),
            'source_package_sha256': str(action.get(ua.PACKAGE_HASH_KEY, ''))}
    # Only this already isolated process is modified, and every removed preview
    # reference is restored immediately. Keep the actual rig name CoshaRig.
    # Do not let Action recovery Scene/previous-Action links pull the scene into
    # what should be a skeletal worker snapshot, including through NLA Actions.
    candidates = {action}
    ad = rig.animation_data
    if ad:
        candidates.update(strip.action for track in ad.nla_tracks for strip in track.strips if strip.action)
    removed = []
    if ua.ACTIVE_KEY in rig:
        removed.append((rig, ua.ACTIVE_KEY, rig[ua.ACTIVE_KEY]))
        del rig[ua.ACTIVE_KEY]
    try:
        for candidate in candidates:
            for key in list(candidate.keys()):
                value = candidate[key]
                if key.startswith('character_designer_unity_animation_') and isinstance(value, bpy.types.ID):
                    removed.append((candidate, key, value))
                    del candidate[key]
        bpy.data.libraries.write(str(snapshot), {rig, action}, path_remap='ABSOLUTE', compress=True)
    finally:
        for owner, key, value in reversed(removed):
            owner[key] = value
    job_path = directory / 'job.json'
    job_path.write_text(json.dumps(spec, indent=2), encoding='utf-8')
    destination = root / filename
    publish = {'stage': str(stage), 'destination': str(destination),
               'metadata': str(destination.with_suffix('.animation.json')),
               'source_blend': str(source_path), 'action': action.name,
               'source_package': spec['source_package'],
               'source_package_sha256': spec['source_package_sha256']}
    publish_path = directory / 'publish.json'
    publish_path.write_text(json.dumps(publish, indent=2), encoding='utf-8')
    return {'label': label, 'snapshot': str(snapshot), 'snapshot_sha256': sha256(snapshot),
            'snapshot_bytes': snapshot.stat().st_size, 'job': str(job_path),
            'publish': str(publish_path), 'expected_rig_name': rig.name,
            'worker': str(ROOT / 'addons/character_designer/animation_export_worker.py'),
            'action': action.name, 'destination': str(destination)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--real-blend', required=True)
    parser.add_argument('--walk-package', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    source_path, package_path = Path(args.real_blend).resolve(), Path(args.walk_package).resolve()
    source_hash, packet_hash = sha256(source_path), sha256(package_path)
    token = uuid.uuid4().hex[:10]
    root = Path(args.output).resolve() / ('examples-' + token)
    root.mkdir(parents=True)
    report = {'prepared': False, 'source_blend': str(source_path), 'source_sha256': source_hash,
              'walk_package': str(package_path), 'walk_package_sha256': packet_hash, 'jobs': [],
              'scope': 'Isolated example preparation only. Workers, FBX publishing and Unity import have not run.'}
    try:
        bpy.ops.wm.open_mainfile(filepath=str(source_path), use_scripts=False, load_ui=False)
        rig = bpy.data.objects['CoshaRig']
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        rig.hide_set(False)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        names = {bone.name for bone in rig.data.bones if not unity_export_worker._is_control(bone)}
        plan = controls.build_plan(rig, names)
        if plan is None:
            raise ValueError('Expected the current Cosha owned controls.')
        package = ua.load_package(package_path)
        result = ua.import_test_action(bpy.context, rig, package_path)
        result.action.name = 'Cosha_Walk_Forearm_Edit_' + token
        report['walk_edit'] = edit_walk(rig, result, package, names)
        report['jobs'].append(prepare_job(root, rig, result.action, result.first_frame, result.last_frame,
                                          package.get('loopTime', False), source_path, 'edited-walk'))
        ua.restore_preview(bpy.context, rig)
        plan = controls.build_plan(rig, names)
        action, start, end, report['original_wave'] = author_wave(rig, plan, 'Cosha_Forearm_Wave_1s_' + token)
        report['jobs'].append(prepare_job(root, rig, action, start, end, True, source_path, 'original-wave'))
        report['prepared'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        report['input_blend_unchanged'] = sha256(source_path) == source_hash
        report['input_packet_unchanged'] = sha256(package_path) == packet_hash
        report['prepared'] &= report['input_blend_unchanged'] and report['input_packet_unchanged']
        (root / 'preparation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('ANIMATION_ROUNDTRIP_EXAMPLES ' + json.dumps(report))
    if not report['prepared']:
        raise AssertionError(report.get('error', 'Input file preservation failed.'))


if __name__ == '__main__':
    main()
