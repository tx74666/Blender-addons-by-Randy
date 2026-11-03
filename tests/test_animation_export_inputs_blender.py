"""Tiny native sparse-export baseline proof; disposable background Blender only.

One in-process exporter, two bones, three FBX outputs. No child process, live
artist session, preferences save, Unity or network. Pass an owned evidence
directory after ``--``. The source-preservation witness surrounds host capture
and snapshot preparation, before the test deliberately loads its private file.
"""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import animation_export as host
from character_designer import animation_export_inputs as inputs
from character_designer import animation_export_inputs_worker as wrapper
from character_designer import animation_export_worker as worker


def curves(action):
    return worker._curves(action)


def keys(action):
    return [(curve.data_path, curve.array_index, curve.mute,
             [(tuple(key.co), tuple(key.handle_left), tuple(key.handle_right), key.interpolation)
              for key in curve.keyframe_points]) for curve in curves(action)]


def witness(rig, action):
    ad, scene = rig.animation_data, bpy.context.scene
    return {'object': rig.as_pointer(), 'data': rig.data.as_pointer(), 'action': action.as_pointer(),
            'frame': scene.frame_current_final, 'keys': keys(action),
            'active': ad.action.as_pointer(), 'slot': ad.action_slot.handle,
            'nla_enabled': ad.use_nla, 'blend': ad.action_blend_type, 'influence': ad.action_influence,
            'nla': [(track.as_pointer(), track.mute, [(strip.as_pointer(), strip.action.as_pointer(),
                     strip.frame_start, strip.frame_end, strip.mute) for strip in track.strips]) for track in ad.nla_tracks],
            'pose': [(pb.name, tuple(pb.location), tuple(pb.rotation_euler), tuple(pb.scale)) for pb in rig.pose.bones],
            'object_pose': [list(rig.location), list(rig.rotation_euler), list(rig.scale)],
            'strength': rig.get('strength'),
            'rest': [(bone.name, list(v for row in bone.matrix_local for v in row)) for bone in rig.data.bones],
            'selection': sorted(obj.as_pointer() for obj in bpy.context.selected_objects),
            'scene': scene.as_pointer(), 'active_object': bpy.context.view_layer.objects.active.as_pointer()}


def fixture():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps = 24
    data = bpy.data.armatures.new('Input Baseline Skeleton')
    rig = bpy.data.objects.new('InputBaselineRig', data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    root = data.edit_bones.new('Hips')
    root.head, root.tail = (0, 0, 0), (0, 0, 1)
    hand = data.edit_bones.new('Hand')
    hand.head, hand.tail, hand.parent = (0, 0, 1), (1, 0, 1), root
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.rotation_mode = 'XYZ'
    for pb in rig.pose.bones:
        pb.rotation_mode = 'XYZ'
    for frame, value in ((1., 0.), (5., .5)):
        rig.pose.bones['Hips'].location.x = value
        rig.pose.bones['Hips'].keyframe_insert('location', index=0, frame=frame)
    action = rig.animation_data.action
    action.name = 'Sparse Custom'
    for curve in curves(action):
        for key in curve.keyframe_points:
            key.interpolation = 'LINEAR'
    rig['strength'] = .6
    driver = rig.pose.bones['Hand'].driver_add('location', 0).driver
    driver.type, driver.expression = 'SCRIPTED', 'strength'
    variable = driver.variables.new()
    variable.name, variable.type = 'strength', 'SINGLE_PROP'
    variable.targets[0].id, variable.targets[0].data_path = rig, '["strength"]'
    constraint = rig.pose.bones['Hand'].constraints.new('COPY_LOCATION')
    constraint.name = 'Same Rig Offset'
    constraint.target, constraint.subtarget = rig, 'Hips'
    constraint.owner_space, constraint.target_space, constraint.use_offset = 'LOCAL', 'LOCAL', True
    rig.pose.bones['Hand'].rotation_euler.z = .2
    bpy.context.scene.frame_set(2)
    bpy.context.view_layer.update()
    return rig, action


def expect_error(call, text):
    try:
        call()
    except (ValueError, KeyError, TypeError) as error:
        assert text.lower() in str(error).lower(), (text, str(error))
    else:
        raise AssertionError('Expected refusal: ' + text)


def host_snapshot(folder, changed=False, recapture=False):
    rig, action = fixture()
    expect_error(lambda: inputs.prepare(rig, action, frame_start=1.), 'Capture Export Base Pose')
    old = witness(rig, action)
    record = inputs.capture(rig, action)
    assert witness(rig, action) == old, 'Capture changed keys, pose, Source graph, frame or NLA'
    copied = action.copy()
    copied.name = 'Retained Comparison'
    track = rig.animation_data.nla_tracks.new()
    track.name = 'Retained Artist NLA'
    track.strips.new(copied.name, 1, copied)
    rig.animation_data.use_nla = True
    if changed:
        bpy.context.scene.frame_set(4)
        rig.pose.bones['Hand'].rotation_euler.z = .8
        rig['strength'] = 1.2
        bpy.context.view_layer.update()
    if recapture:
        record = inputs.capture(rig, action)
    before = witness(rig, action)
    from character_designer import hair_wiggle_adapter
    with patch.object(host.subprocess, 'Popen') as launch, \
            patch.object(hair_wiggle_adapter, 'status', return_value={'active': False}):
        job = host.begin_export(bpy.context, rig, action, folder / 'motion.fbx',
                                frame_start=1., frame_end=5., sample_rate=24.)
        assert launch.call_count == 1
        command = launch.call_args.args[0]
        assert any(str(value).endswith('animation_export_inputs_worker.py') for value in command)
    assert witness(rig, action) == before, 'Host snapshot changed author keys/pose/frame/NLA/source'
    spec = json.loads((job['root'] / 'job.json').read_text())
    assert spec[inputs.JOB_KEY]['sha256'] == record['sha256']
    snapshot = job['root'] / 'animation.blend'
    # No user file is loaded: this is the snapshot just written by this fixture.
    bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False, use_scripts=True)
    result = wrapper.export_job(spec)
    exported = bpy.data.objects['InputBaselineRig']
    payload = keys(exported.animation_data.action)
    published = host._publish(job, result)
    assert published[inputs.JOB_KEY]['sha256'] == record['sha256']
    assert Path(published['filepath']).is_file() and Path(published['metadata']).is_file()
    assert not any('snapshot values' in message for message in published['warnings'])
    host._dispose(job)
    return payload, {'sha256': record['sha256'], 'samples': result['samples'],
                     'host_preserved': True, 'fbx': published['filepath'], 'base_pose': published[inputs.JOB_KEY]}


def refusals():
    rig, action = fixture()
    record = inputs.capture(rig, action)
    raw = action[inputs.RECORD]
    damaged = json.loads(raw)
    damaged['inputs'][0]['value'] = 123.
    action[inputs.RECORD] = json.dumps(damaged)
    expect_error(lambda: inputs.prepare(rig, action, frame_start=1.), 'invalid')
    action[inputs.RECORD] = raw
    sealed = inputs.prepare(rig, action, frame_start=1.)
    damaged_job = dict(sealed, action='Another Action')
    expect_error(lambda: inputs.apply(rig, action, damaged_job), 'another')
    # A structural channel addition is not silently assigned an old base.
    rig.pose.bones['Hand'].keyframe_insert('rotation_euler', index=2, frame=1.)
    expect_error(lambda: inputs.prepare(rig, action, frame_start=1.), 'changed')
    rig, action = fixture()
    external = bpy.data.objects.new('External Driver Input', None)
    bpy.context.scene.collection.objects.link(external)
    external['strength'] = .4
    rig.animation_data.drivers[0].driver.variables[0].targets[0].id = external
    expect_error(lambda: inputs.capture(rig, action), 'same-rig')
    assert inputs.RECORD not in action
    return 5


def fully_keyed():
    rig, action = fixture()
    rig.driver_remove('pose.bones["Hand"].location', 0)
    for constraint in tuple(rig.pose.bones['Hand'].constraints):
        rig.pose.bones['Hand'].constraints.remove(constraint)
    del rig['strength']
    for frame in (1., 5.):
        for block in (rig, *rig.pose.bones):
            for field in ('location', 'rotation_euler', 'scale'):
                block.keyframe_insert(field, frame=frame)
    value = inputs.prepare(rig, action, frame_start=1.)
    assert not value['retained'] and inputs.RECORD not in action
    assert inputs.apply(rig, action, value)['policy'] == 'fully-keyed-neutral-deltas'
    return True


def main():
    directory = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    first, a = host_snapshot(directory / 'first')
    second, b = host_snapshot(directory / 'changed', changed=True)
    third, c = host_snapshot(directory / 'recaptured', changed=True, recapture=True)
    assert first == second, 'Unkeyed artist posing changed an export after baseline capture'
    assert first != third, 'Deliberate recapture did not affect exported numeric motion'
    assert a['sha256'] == b['sha256'] and a['sha256'] != c['sha256']
    result = {'ok': True, 'cases': [a, b, c], 'same_baseline_identical_numeric_export': True,
              'recapture_changes_numeric_export': True, 'refusals': refusals(),
              'fully_keyed_without_capture': fully_keyed(), 'worker_sha256': hashlib.sha256(
                  (ROOT / 'addons/character_designer/animation_export_worker.py').read_bytes()).hexdigest()}
    (directory / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('EXPORT_BASE_POSE_NATIVE_OK', json.dumps(result))


if __name__ == '__main__':
    main()
