"""Prepare a real, independent two-clip worklist example in disposable Blender.

Run --background --factory-startup --python this_file.py --
    --workspace character_animation_workspace.json --output NEW_FOLDER
    [--clips Walk_N Idle] [--prepare-only | --sync]

The default and --prepare-only never launch a worker. --sync calls the actual
worklist Sync route for each edited Custom serially; it does not operate Unity.
Only NEW_FOLDER/example.blend is saved. Production model/packet/Source files
remain unchanged, and no existing authoring .blend is opened.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback

import bpy
from mathutils import Quaternion


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
import character_designer
from character_designer import animation, animation_export, animation_export_worker
from character_designer import animation_link as links, animation_workspace as workspace
from character_designer import animation_worklist as worklist, unity_animation as ua


def require(value, message):
    if not value:
        raise AssertionError(message)


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def clip_key(record):
    guid, local_id = workspace.clip_identity(record)
    return guid + ':' + str(local_id)


def get_item(item_id):
    return next(item for item in worklist.state(bpy.context).items if item.item_id == item_id)


def curves(action, handle):
    slots = [slot for slot in action.slots if slot.handle == handle and slot.target_id_type == 'OBJECT']
    require(len(slots) == 1, 'The saved Object Action slot is missing or ambiguous.')
    return animation_export_worker._curves(action, slots[0])


def curve_content(action, handle):
    return sorted((curve.data_path, curve.array_index, curve.mute, curve.extrapolation,
                   [(list(point.co), list(point.handle_left), list(point.handle_right),
                     point.interpolation, point.handle_left_type, point.handle_right_type)
                    for point in curve.keyframe_points]) for curve in curves(action, handle))


def curve_hash(action, handle):
    data = json.dumps(curve_content(action, handle), allow_nan=False, separators=(',', ':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def compare_unedited(item):
    left, right = curve_content(item.source_action, item.source_slot), curve_content(item.custom_action, item.custom_slot)
    require(bool(left) and len(left) == len(right), 'Source and Custom curve counts differ or are empty.')
    maximum, keys = 0.0, 0
    for original, custom in zip(left, right):
        require(original[:4] == custom[:4] and len(original[4]) == len(custom[4]),
                'Source and Custom curve channels or key counts differ.')
        for a, b in zip(original[4], custom[4]):
            require(a[3:] == b[3:], 'Source and Custom interpolation differs.')
            for av, bv in zip(a[:3], b[:3]):
                maximum = max(maximum, *(abs(x - y) for x, y in zip(av, bv)))
            keys += 1
    require(maximum <= 1e-7, 'Unedited Source and Custom values differ: ' + str(maximum))
    return {'curves': len(left), 'keys': keys, 'maximum_numeric_difference': maximum,
            'source_curve_sha256': curve_hash(item.source_action, item.source_slot),
            'custom_curve_sha256': curve_hash(item.custom_action, item.custom_slot)}


def update_frame(value):
    ua._set_frame(bpy.context.scene, value)
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    graph.update()  # Explicit after open_mainfile in factory-startup processes.
    return graph


def poses(rig, frames):
    sampled = []
    for frame in frames:
        evaluated = rig.evaluated_get(update_frame(frame))
        values = {bone.name: [float(v) for row in evaluated.matrix_world @ bone.matrix for v in row]
                  for bone in evaluated.pose.bones}
        values['__object_world__'] = [float(v) for row in evaluated.matrix_world for v in row]
        require(all(math.isfinite(v) for matrix in values.values() for v in matrix), 'A sampled pose is not finite.')
        sampled.append(values)
    return sampled


def pose_difference(before, after):
    require(len(before) == len(after), 'Pose sample counts differ.')
    maximum = 0.0
    for a, b in zip(before, after):
        require(a.keys() == b.keys(), 'Pose sample bone sets differ.')
        for name in a:
            maximum = max(maximum, *(abs(x - y) for x, y in zip(a[name], b[name])))
    return maximum


def edit_forearm(item, bone_name):
    worklist.activate(bpy.context, item.item_id, 'CUSTOM')
    rig, action = item.rig, item.custom_action
    require(bone_name in rig.pose.bones, 'The actual model is missing ' + bone_name + '.')
    bone = rig.pose.bones[bone_name]
    first, last = map(float, action.frame_range)
    require(last > first, 'The selected clip has no positive duration.')
    endpoints_before = poses(rig, (first, last))
    prefix = bone.path_from_id() + '.rotation_'
    times = sorted({float(point.co.x) for curve in curves(action, item.custom_slot)
                    if curve.data_path.startswith(prefix) for point in curve.keyframe_points})
    interior = []
    for frame in times:
        phase = (frame - first) / (last - first)
        if .15 < phase < .85:
            update_frame(frame)
            # Capture unedited values before inserting any new keys.
            interior.append((frame, phase, bone.matrix_basis.to_quaternion().copy()))
    require(interior, 'The clip has no interior forearm rotation keys to edit.')
    changes = []
    for frame, phase, original in interior:
        update_frame(frame)
        degrees = 12.0 * math.sin(math.pi * (phase - .15) / .7) ** 2
        rotation = original @ Quaternion((1, 0, 0), math.radians(degrees))
        if bone.rotation_mode == 'QUATERNION':
            if rotation.dot(bone.rotation_quaternion) < 0:
                rotation.negate()
            bone.rotation_quaternion, prop = rotation, 'rotation_quaternion'
        elif bone.rotation_mode == 'AXIS_ANGLE':
            axis, angle = rotation.to_axis_angle()
            bone.rotation_axis_angle, prop = (angle, *axis), 'rotation_axis_angle'
        else:
            bone.rotation_euler, prop = rotation.to_euler(bone.rotation_mode, bone.rotation_euler), 'rotation_euler'
        require(bone.keyframe_insert(prop, frame=frame, group='Worklist Example ' + bone_name),
                'Blender did not insert the forearm rotation key.')
        changes.append({'frame': frame, 'phase': phase, 'extra_local_x_degrees': degrees})
    for curve in curves(action, item.custom_slot):
        if curve.data_path.startswith(prefix):
            for point in curve.keyframe_points:
                point.interpolation = 'LINEAR'
            curve.update()
    maximum = pose_difference(endpoints_before, poses(rig, (first, last)))
    require(maximum <= 1e-5, 'The forearm edit changed a loop endpoint: ' + str(maximum))
    require(max(change['extra_local_x_degrees'] for change in changes) >= 8,
            'The clip lacks enough samples for a measurable modest edit.')
    fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    duration = (last - first) / fps
    require(abs(duration - float(action['unity_duration'])) <= 1e-5,
            'Action frames and the Unity duration disagree at the scene frame rate.')
    return {'bone': bone_name, 'frame_start': first, 'frame_end': last, 'duration_seconds': duration,
            'scene_fps': bpy.context.scene.render.fps, 'scene_fps_base': bpy.context.scene.render.fps_base,
            'sample_rate': action['unity_sample_rate'], 'loop': bool(action.get('unity_loop_time', False)),
            'endpoint_maximum_matrix_change': maximum, 'changed_samples': changes}


def item_record(item):
    return {'item_id': item.item_id, 'clip_key': item.clip_key, 'name': item.name,
            'source_action': item.source_action.name, 'custom_action': item.custom_action.name,
            'source_slot': item.source_slot, 'custom_slot': item.custom_slot,
            'source_curve_sha256': curve_hash(item.source_action, item.source_slot),
            'custom_curve_sha256': curve_hash(item.custom_action, item.custom_slot),
            'source_file': item.source_file, 'source_hash': item.source_hash,
            'manifest_path': item.manifest_path, 'link_identity': json.loads(item.link_identity),
            'export_rig_name': item.export_rig_name, 'model_file': item.model_file,
            'model_sha256': item.model_sha256}


def check_ownership():
    state = worklist.state(bpy.context)
    require(len(state.items) == 2 and state.rig is not None, 'Expected two rows and one shared rig.')
    require(sum(obj.type == 'ARMATURE' for obj in bpy.context.scene.objects) == 1,
            'The editing scene contains more than one imported character rig.')
    pointers = set()
    for item in state.items:
        require(item.rig is state.rig, 'A worklist row does not use the shared rig.')
        require(item.source_action.library is not None and not item.source_action.is_editable,
                'Source is not a read-only linked Action.')
        require(item.custom_action.library is None and item.custom_action.is_editable,
                'Custom is not a local editable Action.')
        worklist._check_baseline(item)
        pointers.update((item.source_action.as_pointer(), item.custom_action.as_pointer()))
    require(len(pointers) == 4, 'Rows share an Action instead of independent Source/Custom pairs.')


def sample_rows():
    values = {}
    ids = [item.item_id for item in worklist.state(bpy.context).items]
    for item_id in ids:
        for side in ('SOURCE', 'CUSTOM'):
            action = worklist.activate(bpy.context, item_id, side)
            first, last = map(float, action.frame_range)
            values[(item_id, side)] = poses(get_item(item_id).rig, (first, (first + last) / 2, last))
    return values


def save_reopen(blend_file, active_id):
    state = worklist.state(bpy.context)
    samples = sample_rows()
    worklist.activate(bpy.context, active_id, 'CUSTOM')
    expected = [item_record(item) for item in state.items]
    scene_name, rig_name = bpy.context.scene.name, state.rig.name
    fps = (bpy.context.scene.render.fps, bpy.context.scene.render.fps_base)
    rest = ua._rest_state(state.rig.data)
    association = links.get_link(state.rig)
    saved = bpy.ops.wm.save_as_mainfile(filepath=str(blend_file), check_existing=False)
    require('FINISHED' in saved and blend_file.is_file(), 'The isolated worklist example was not saved.')
    blend_hash = sha256(blend_file)
    opened = bpy.ops.wm.open_mainfile(filepath=str(blend_file), use_scripts=False, load_ui=False)
    require('FINISHED' in opened and Path(bpy.data.filepath).resolve() == blend_file,
            'The newly saved worklist example did not reopen.')
    character_designer._validate_registration_integrity()
    # No old Blender datablock references are used below this point.
    bpy.context.window.scene = bpy.data.scenes[scene_name]
    state = worklist.state(bpy.context)
    require(state.rig and state.rig.name == rig_name, 'The reopened worklist lost its shared rig.')
    bpy.context.view_layer.objects.active = state.rig
    state.rig.select_set(True)
    update_frame(bpy.context.scene.frame_current + bpy.context.scene.frame_subframe)
    require([item_record(item) for item in state.items] == expected, 'Saved rows, Actions, slots or identities changed.')
    require((bpy.context.scene.render.fps, bpy.context.scene.render.fps_base) == fps, 'Scene FPS changed after reopening.')
    require(ua._rest_state(state.rig.data) == rest, 'The rest skeleton changed after reopening.')
    require(links.get_link(state.rig) == association, 'The active Link association changed after reopening.')
    require(worklist.current(bpy.context).item_id == active_id, 'The highlighted row changed after reopening.')
    active = get_item(active_id)
    require(state.rig.animation_data.action is active.custom_action
            and state.rig.animation_data.action_slot.handle == active.custom_slot,
            'The active Custom Action and Object slot did not survive reopening.')
    check_ownership()
    after = sample_rows()
    maximum = max(pose_difference(samples[key], after[key]) for key in samples)
    require(maximum <= 1e-5, 'Evaluated animation changed after reopening: ' + str(maximum))
    worklist.activate(bpy.context, active_id, 'CUSTOM')
    require(sha256(blend_file) == blend_hash, 'Reopening altered the saved example on disk.')
    return {'passed': True, 'blend_sha256': blend_hash, 'maximum_pose_matrix_change': maximum,
            'identity_slots_order_curves_preserved': True, 'fps_preserved': True, 'rest_preserved': True}


def sync_one(item_id):
    item = get_item(item_id)
    worklist.activate(bpy.context, item_id, 'CUSTOM')
    path = Path(item.manifest_path)
    before = links.load_link(path)
    previous_revision = before.get('revision', 0)
    require(type(previous_revision) is int and previous_revision >= 0, 'The initial Link revision is invalid.')
    job = None
    started = time.monotonic()
    try:
        job = worklist.sync(bpy.context)
        require(job is not None and animation_export.active_job() is job, 'Sync did not start its expected export job.')
        # A background script does not service the UI event loop. Poll the same
        # production callback explicitly, without leaving a second timer owner.
        if bpy.app.timers.is_registered(animation._poll_action_export):
            bpy.app.timers.unregister(animation._poll_action_export)
        while animation_export.active_job() is job:
            require(time.monotonic() - started <= 660, 'The serial worklist Sync exceeded its timeout.')
            animation._poll_action_export()
            if animation_export.active_job() is job:
                time.sleep(.1)
        require(animation_export.active_job() is None, 'A foreign export job replaced the owned job.')
        wm = bpy.context.window_manager.character_designer_animation
        saved = worklist.state(bpy.context)
        require(not wm.has_error and not saved.has_error, 'Sync failed: ' + wm.status + ' / ' + saved.status)
        require(wm.status.startswith('Synced ') and saved.status == wm.status,
                'The worklist did not receive a successful Sync completion status.')
        current = links.load_link(path)
        require(links._identity(current) == links._identity(before), 'Sync changed the Link source identity.')
        require(current.get('revision') == previous_revision + 1, 'Sync did not advance exactly one Link revision.')
        fbx, metadata = Path(current['fbxFile']), Path(current['metadataFile'])
        require(str(fbx.resolve()) == str(Path(wm.export_result_path).resolve()), 'Sync reported another FBX file.')
        require(sha256(fbx) == current['fbxSha256'] and sha256(metadata) == current['metadataSha256'],
                'The completed FBX/metadata pair does not match the published Link hashes.')
        result = json.loads(metadata.read_text(encoding='utf-8'))
        require(result.get('fbx_sha256') == current['fbxSha256'], 'The FBX metadata hash disagrees with the Link.')
        require(not result.get('unsupported_channels'), 'The acceptance Action contains omitted export channels.')
        return {'item_id': item_id, 'clipGuid': current['clipGuid'], 'clipLocalId': current['clipLocalId'],
                'link_manifest': str(path), 'revision_before': previous_revision, 'revision': current['revision'],
                'fbxFile': str(fbx), 'fbxSha256': current['fbxSha256'], 'metadataFile': str(metadata),
                'metadataSha256': current['metadataSha256'], 'seconds': time.monotonic() - started,
                'status': saved.status}
    finally:
        if job is not None and animation_export.active_job() is job:
            animation_export.cancel_export(job)
        if bpy.app.timers.is_registered(animation._poll_action_export):
            bpy.app.timers.unregister(animation._poll_action_export)
        animation._export_window_manager = None


def select_clips(record, names):
    if not names:
        require(len(record['clips']) >= 2, 'The workspace must contain at least two clips.')
        selected = record['clips'][:2]
    else:
        selected = []
        for name in names:
            matches = [item for item in record['clips'] if item['clipName'] == name]
            require(len(matches) == 1, 'Choose one exact, unambiguous workspace clip name: ' + name)
            selected.append(matches[0])
    require(len({clip_key(item) for item in selected}) == 2, 'Select two different clip identities.')
    require(all(item.get('linkManifest') and not item.get('needsRebuild') for item in selected),
            'Both selected clips must have fresh prepared Links from Unity.')
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--output', required=True, help='A new persistent folder; existing folders are never reused.')
    parser.add_argument('--clips', nargs=2, metavar=('FIRST', 'SECOND'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--sync', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    require(bpy.app.background and '--factory-startup' in sys.argv and not bpy.data.filepath,
            'Run in a fresh --background --factory-startup process with no input .blend.')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    blend_file, report_file = output / 'example.blend', output / 'prep_report.json'
    report = {'ok': False, 'workspace': str(Path(args.workspace).resolve()), 'output': str(output),
              'blend_file': str(blend_file), 'prepare_only': not args.sync, 'synced': [], 'steps': []}
    protected, manifest_hashes = {}, {}

    def checkpoint(step):
        report['steps'].append(step)
        report_file.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        print('ANIMATION_WORKLIST_PROGRESS ' + step, flush=True)

    try:
        record = workspace.load_workspace(args.workspace)
        selected = select_clips(record, args.clips)
        protected[record['_manifest_path']] = sha256(record['_manifest_path'])
        protected[record['modelFile']] = sha256(record['modelFile'])
        require(protected[record['modelFile']] == record['modelSha256'].lower(), 'The workspace model checksum is stale.')
        report['workspace_identity'] = list(workspace.workspace_identity(record))
        report['clips'] = []
        for clip in selected:
            link = links.load_link(clip['linkManifest'])
            require(clip_key(link) == clip_key(clip) and link['targetGuid'].lower() == record['targetGuid'].lower(),
                    'A prepared Link belongs to another target or clip slot.')
            protected[link['sourcePackage']] = link['sourcePackageSha256'].lower()
            manifest_hashes[link['_manifest_path']] = sha256(link['_manifest_path'])
            report['clips'].append({'clipGuid': clip['clipGuid'], 'clipLocalId': clip['clipLocalId'],
                                    'clipName': clip['clipName'], 'link_manifest': link['_manifest_path'],
                                    'source_package': link['sourcePackage'],
                                    'source_package_sha256': link['sourcePackageSha256']})
        checkpoint('Inputs verified; no source model or animation packet will be edited.')
        bpy.ops.wm.read_factory_settings(use_empty=True)
        character_designer.register()
        character_designer._validate_registration_integrity()
        worklist.connect(bpy.context, record['_manifest_path'])
        ids = []
        for clip in selected:
            ids.append(worklist.add(bpy.context, clip_key(clip)).item_id)
        check_ownership()
        report['rig'] = worklist.state(bpy.context).rig.name
        report['scene'] = bpy.context.scene.name
        report['unedited_comparison'] = {item_id: compare_unedited(get_item(item_id)) for item_id in ids}
        for item_id in ids:
            item = get_item(item_id)
            protected[item.source_file] = item.source_hash
        checkpoint('Two independent Source/Custom pairs share one real imported rig.')
        source_hashes = {item_id: curve_hash(get_item(item_id).source_action, get_item(item_id).source_slot) for item_id in ids}
        report['edits'] = {}
        for index, bone_name in enumerate(('forearm.L', 'forearm.R')):
            other = get_item(ids[1 - index])
            other_hash = curve_hash(other.custom_action, other.custom_slot)
            item = get_item(ids[index])
            prior = curve_hash(item.custom_action, item.custom_slot)
            report['edits'][item.item_id] = edit_forearm(item, bone_name)
            require(curve_hash(item.custom_action, item.custom_slot) != prior, 'The requested Custom edit had no effect.')
            require(curve_hash(other.custom_action, other.custom_slot) == other_hash, 'Editing one clip changed the other Custom.')
            for source_id in ids:
                source_item = get_item(source_id)
                require(curve_hash(source_item.source_action, source_item.source_slot) == source_hashes[source_id],
                        'Editing Custom changed a read-only Source Action.')
        state = worklist.state(bpy.context)
        active_id, action = worklist.current(bpy.context).item_id, state.rig.animation_data.action
        worklist.move(bpy.context, ids[0], 1)
        require([item.item_id for item in state.items] == list(reversed(ids)), 'Reorder did not retain the intended row identities.')
        require(worklist.current(bpy.context).item_id == active_id and state.rig.animation_data.action is action,
                'Reordering changed the highlighted identity or active Action.')
        report['order_after_move'] = [item.item_id for item in state.items]
        checkpoint('Left/right Custom edits and loop endpoints verified; reorder preserves active Action.')
        report['save_reopen'] = save_reopen(blend_file, ids[0])
        report['items'] = [item_record(item) for item in worklist.state(bpy.context).items]
        checkpoint('Saved example.blend reopened with persistent identities, slots, curves and evaluated poses.')
        if args.sync:
            for item_id in ids:
                report['synced'].append(sync_one(item_id))
                checkpoint('Synced one Custom through the production callback: ' + get_item(item_id).name)
        else:
            require(all(sha256(path) == digest for path, digest in manifest_hashes.items()),
                    'Preparing without Sync changed an input Link manifest.')
        require(sha256(blend_file) == report['save_reopen']['blend_sha256'], 'Sync changed the saved example on disk.')
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        preserved = {}
        for path, expected in protected.items():
            try:
                actual = sha256(path)
                preserved[path] = {'expected_sha256': expected, 'actual_sha256': actual, 'unchanged': actual == expected}
            except Exception as exc:
                preserved[path] = {'expected_sha256': expected, 'unchanged': False, 'error': str(exc)}
        report['protected_files'] = preserved
        report['source_files_preserved'] = bool(preserved) and all(value['unchanged'] for value in preserved.values())
        if not report['source_files_preserved']:
            report['ok'] = False
            report.setdefault('error', 'A protected workspace, packet, model or Source cache changed or disappeared.')
        report_file.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        print('ANIMATION_WORKLIST_EXAMPLE ' + json.dumps({'ok': report['ok'], 'report': str(report_file),
                                                       'blend_file': str(blend_file), 'synced': len(report['synced'])}), flush=True)
    require(report['ok'], report.get('error', 'Worklist example preparation failed.'))


if __name__ == '__main__':
    main()
