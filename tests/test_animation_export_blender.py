"""Bounded native FBX roundtrip checks; run only in disposable background Blender.

blender --background --factory-startup --threads 2 --python-exit-code 1 \
    --python tests/test_animation_export_blender.py

Three small animation exports, no real character, renderer, child Blender process,
Unity process, add-on registration, or writes outside an owned temporary folder.
"""

import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import animation_export as launcher
from character_designer import animation_export_worker as worker


def error(a, b):
    return max(abs(a[i][j] - b[i][j]) for i in range(4) for j in range(4))


def flat(matrix):
    return [float(value) for row in matrix for value in row]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def action_state(action):
    return [(curve.data_path, curve.array_index, curve.mute,
             [(tuple(key.co), key.interpolation, tuple(key.handle_left), tuple(key.handle_right))
              for key in curve.keyframe_points]) for curve in worker._curves(action)]


def skeleton_state(rig):
    return [(bone.name, bone.parent.name if bone.parent else None, bone.use_connect,
             bone.use_deform, flat(bone.matrix_local), tuple(bone.head_local), tuple(bone.tail_local),
             dict(bone.items())) for bone in rig.data.bones]


def constraints_state(rig):
    return [(bone.name, [(constraint.as_pointer(), constraint.type, constraint.name,
                         constraint.target.as_pointer() if getattr(constraint, 'target', None) else None,
                         getattr(constraint, 'subtarget', ''), constraint.influence, constraint.mute)
                        for constraint in bone.constraints]) for bone in rig.pose.bones]


def nla_state(rig):
    return [(track.as_pointer(), track.name, track.mute,
             [(strip.as_pointer(), strip.name, strip.action.as_pointer(), strip.mute,
               strip.frame_start, strip.frame_end, strip.scale, strip.influence)
              for strip in track.strips]) for track in rig.animation_data.nla_tracks]


def live_state(rig):
    scene, ad = bpy.context.scene, rig.animation_data
    return (rig.name, rig.data.as_pointer(), digest(skeleton_state(rig)), constraints_state(rig),
            nla_state(rig), ad.action.as_pointer(), ad.action_slot.handle, ad.use_nla,
            ad.action_blend_type, ad.action_influence, ad.action_extrapolation,
            digest(action_state(ad.action)), flat(rig.matrix_basis), scene.as_pointer(),
            scene.frame_current_final, scene.render.fps, scene.render.fps_base,
            scene.frame_start, scene.frame_end, scene.use_preview_range,
            scene.tool_settings.use_keyframe_insert_auto,
            bpy.context.view_layer.objects.active.as_pointer(),
            sorted(obj.as_pointer() for obj in bpy.context.selected_objects))


def fixture(constrained=False):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = 24, 1.001
    scene.unit_settings.system, scene.unit_settings.scale_length = 'METRIC', 1.0
    scene.frame_start, scene.frame_end = 1, 80
    scene.use_preview_range = True
    scene.tool_settings.use_keyframe_insert_auto = True
    data = bpy.data.armatures.new('Fixture Skeleton')
    rig = bpy.data.objects.new('CharacterRig', data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    specs = [
        ('Hips', None, (0, 0, 1), (0, 0, 1.2), False),
        ('Spine', 'Hips', (0, 0, 1.2), (0, 0, 1.5), True),
        ('Head', 'Spine', (0, 0, 1.5), (0, 0, 1.75), True),
        ('UpperLeg.L', 'Hips', (.12, 0, 1), (.12, .035, .57), False),
        ('LowerLeg.L', 'UpperLeg.L', (.12, .035, .57), (.12, 0, .13), True),
        ('Foot.L', 'LowerLeg.L', (.12, 0, .13), (.12, -.2, .08), True),
        ('Foot.R', 'Hips', (-.12, 0, .13), (-.12, -.2, .08), False),
    ]
    for index, (name, parent, head, tail, connected) in enumerate(specs):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail, bone.roll = head, tail, .07 * index
        bone.parent = data.edit_bones.get(parent) if parent else None
        bone.use_connect = connected
    if constrained:
        master = data.edit_bones.new('CTRL_master')
        master.head, master.tail, master.use_deform = (0, 0, 0), (0, 0, .2), False
        data.edit_bones['Hips'].parent = master
        foot = data.edit_bones.new('CTRL_foot')
        foot.head, foot.tail, foot.use_deform = (.12, 0, .13), (.12, -.2, .08), False
    bpy.ops.object.mode_set(mode='OBJECT')
    if constrained:
        for name in ('CTRL_master', 'CTRL_foot'):
            data.bones[name]['character_designer_owner'] = 'root_control'
        constraint = rig.pose.bones['Foot.L'].constraints.new('COPY_LOCATION')
        constraint.name = 'CDesigner Fixture Foot Target'
        constraint.target, constraint.subtarget = rig, 'CTRL_foot'
        constraint.owner_space, constraint.target_space = 'POSE', 'POSE'
        rig['foot_turn'] = .0
        driver = rig.pose.bones['Foot.R'].driver_add('rotation_euler', 1).driver
        driver.type, driver.expression = 'SCRIPTED', 'turn'
        variable = driver.variables.new()
        variable.name, variable.type = 'turn', 'SINGLE_PROP'
        variable.targets[0].id = rig
        variable.targets[0].data_path = '["foot_turn"]'
    rig.rotation_mode = 'QUATERNION'
    rig.rotation_quaternion = Quaternion((0, 0, 1), .23)
    rig.scale = (.83,) * 3
    for bone in rig.pose.bones:
        bone.rotation_mode = 'XYZ'
    start, end = 2.25, 13.7
    for frame, t in ((start, 0.0), ((start + end) / 2, .5), (end, 1.0)):
        rig.location = (3 + .7 * t, -2 + .16 * t, .4)
        rig.keyframe_insert('location', frame=frame)
        hip = rig.pose.bones['Hips']
        hip.location = (.13 * t, .05 * t, .02 * t)
        hip.keyframe_insert('location', frame=frame)
        for name, multiplier in (('Spine', .3), ('UpperLeg.L', -.45), ('LowerLeg.L', .8)):
            bone = rig.pose.bones[name]
            bone.rotation_euler = (multiplier * (.4 + t), .12 * t, .07 * t)
            bone.keyframe_insert('rotation_euler', frame=frame)
        if constrained:
            root = rig.pose.bones['CTRL_master']
            root.location = (.2 * t, -.1 * t, 0)
            root.keyframe_insert('location', frame=frame)
            foot = rig.pose.bones['CTRL_foot']
            foot.location = (.05 * t, -.03 * t, .04 * t)
            foot.keyframe_insert('location', frame=frame)
            rig['foot_turn'] = .3 * t
            rig.keyframe_insert('["foot_turn"]', frame=frame)
    action = rig.animation_data.action
    action.name = 'Selected Motion'
    for curve in worker._curves(action):
        for key in curve.keyframe_points:
            key.interpolation = 'LINEAR'
    # A different take must not accidentally appear in the FBX or its evaluation.
    other = action.copy()
    other.name = 'NLA Motion Must Not Export'
    track = rig.animation_data.nla_tracks.new()
    track.strips.new('Existing NLA', 1, other)
    rig.animation_data.use_nla = False
    worker._frame(scene, 7.375)
    bpy.context.view_layer.update()
    return rig, action, start, end


def spec(rig, action, start, end, folder):
    scene = bpy.context.scene
    return {'rig': rig.name, 'action': action.name, 'action_slot': rig.animation_data.action_slot.handle,
            'frame_start': start, 'frame_end': end, 'fps': scene.render.fps,
            'fps_base': scene.render.fps_base, 'sample_rate': 30, 'loop': True,
            'name': 'Roundtrip', 'filename': 'Roundtrip.fbx', 'stage': str(folder), 'unit_scale': 1.0,
            'unsupported_channels': ['Shape Key animation: Live Scene Only']}


def roundtrip(folder, constrained, reload_snapshot=False):
    rig, action, start, end = fixture(constrained)
    if reload_snapshot:
        rig.scale = (.782315731,) * 3
        # A library containing only rig/Action IDs opens in a default scene at
        # frame 1. Use the same authored reference frame for this regression.
        worker._frame(bpy.context.scene, 1)
        bpy.context.view_layer.update()
    job = spec(rig, action, start, end, folder)
    duration = (end - start) / (job['fps'] / job['fps_base'])
    intervals = math.ceil(duration * job['sample_rate'] - 1e-9)
    normalization = Matrix.Translation(-rig.matrix_world.translation)
    reference_world = normalization @ rig.matrix_world
    snapshot_frame = bpy.context.scene.frame_current_final
    assert snapshot_frame != start
    helpers = worker._model_helpers()
    retained = [bone.name for bone in rig.data.bones if not helpers._is_control(bone)]
    rest = {name: reference_world @ rig.data.bones[name].matrix_local for name in retained}
    expected = []
    for index in range(intervals + 1):
        worker._frame(bpy.context.scene, start + (end - start) * index / intervals)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        expected.append({name: normalization @ evaluated.matrix_world @ evaluated.pose.bones[name].matrix
                         for name in retained})
    worker._frame(bpy.context.scene, snapshot_frame)
    bpy.context.view_layer.update()
    if reload_snapshot:
        snapshot = folder / 'rig-action-only.blend'
        bpy.data.libraries.write(str(snapshot), {rig, action}, path_remap='ABSOLUTE')
        bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False)
        rig, action = bpy.data.objects[job['rig']], bpy.data.actions[job['action']]
        assert all(rig.name not in scene.objects for scene in bpy.data.scenes)
        assert abs(rig.scale.x - .782315731) < 1e-7
        # Do not link, frame_set or update the reloaded rig here: production
        # workers must initialize the unevaluated library snapshot themselves.
    before = (rig.data.as_pointer(), digest(skeleton_state(rig)), constraints_state(rig), nla_state(rig),
              action.as_pointer(), digest(action_state(action)),
              [(curve.as_pointer(), curve.data_path, curve.driver.expression)
               for curve in rig.animation_data.drivers])
    result = worker.export_job(job)
    after = (rig.data.as_pointer(), digest(skeleton_state(rig)), constraints_state(rig), nla_state(rig),
             action.as_pointer(), digest(action_state(action)),
             [(curve.as_pointer(), curve.data_path, curve.driver.expression)
              for curve in rig.animation_data.drivers])
    assert before == after, 'Worker cleanup modified the sampled source skeleton, Action or control graph'
    assert result['ok'] and result['samples'] == intervals + 1 and result['loop']
    assert result['has_reference_frame'] is True and result['reference_frame'] == 0
    assert result['playable_first_frame'] == 1 and result['playable_last_frame'] == intervals + 1
    assert result['unsupported_channels'] == job['unsupported_channels']
    assert set(result['bones']) == set(retained)
    assert abs(result['duration'] - duration) < 1e-10
    assert abs(result['effective_sample_rate'] * duration - intervals) < 1e-10
    assert result['maximum_bake_matrix_error'] < 2e-4
    filename = folder / result['filename']
    assert hashlib.sha256(filename.read_bytes()).hexdigest() == result['sha256']

    # Separate static Rest import is essential: checking only poses can conceal
    # an FBX whose first animated pose has accidentally become its bind skeleton.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    bpy.ops.import_scene.fbx(filepath=str(filename), use_anim=False, automatic_bone_orientation=False)
    imported = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
    bpy.context.view_layer.update()
    assert set(imported.data.bones.keys()) == set(retained)
    assert imported.data.bones['Hips'].parent is None
    assert all(obj.type != 'MESH' for obj in bpy.data.objects)
    assert error(imported.matrix_world, reference_world) < 2e-4, 'FBX static root transform changed'
    max_rest = max(error(imported.matrix_world @ imported.data.bones[name].matrix_local, rest[name])
                   for name in retained)
    assert max_rest < 2e-4, ('FBX Rest mismatch', max_rest)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.preferences.addon_enable(module='io_scene_fbx')
    bpy.ops.import_scene.fbx(filepath=str(filename), use_anim=True, anim_offset=0,
                             automatic_bone_orientation=False)
    imported = next(obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE')
    scene = bpy.context.scene
    assert len(bpy.data.actions) == 1, [(item.name, len(item.slots)) for item in bpy.data.actions]
    imported_action = imported.animation_data.action
    assert imported_action is not None and not imported.animation_data.nla_tracks
    assert abs(scene.render.fps / scene.render.fps_base - result['effective_sample_rate']) < 2e-5
    # Blender 5.2's native importer uses integer scene.render.fps (not fps/base)
    # when converting FBX seconds to key frames. Account for that importer rule
    # explicitly; the FBX's real rate and duration are independently checked.
    frame_rate = scene.render.fps
    keys = [key.co.x for curve in worker._curves(imported_action) for key in curve.keyframe_points]
    reference_time = 1.0 / result['effective_sample_rate']
    assert abs(min(keys)) < 2e-4, ('Reference frame is missing or negative', min(keys))
    assert abs(max(keys) / frame_rate - (duration + reference_time)) < 2e-5, ('Full Take endpoint changed', max(keys), duration)
    assert abs((result['playable_last_frame'] - result['playable_first_frame']) /
               result['effective_sample_rate'] - duration) < 1e-10
    worker._frame(scene, 0)
    evaluated = imported.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert error(evaluated.matrix_world, reference_world) < 2e-4, 'Animated reference root transform changed'
    reference_error = max(error(evaluated.matrix_world @ evaluated.pose.bones[name].matrix, rest[name])
                          for name in retained)
    assert reference_error < 2e-4, ('Animated reference frame is not Rest', reference_error)
    max_pose, max_foot = 0., 0.
    for index, desired in enumerate(expected):
        worker._frame(scene, (index + result['playable_first_frame']) / result['effective_sample_rate'] * frame_rate)
        evaluated = imported.evaluated_get(bpy.context.evaluated_depsgraph_get())
        for name in retained:
            actual = evaluated.matrix_world @ evaluated.pose.bones[name].matrix
            difference = error(actual, desired[name])
            max_pose = max(max_pose, difference)
            if name.startswith('Foot.'):
                max_foot = max(max_foot, (actual.translation - desired[name].translation).length)
            assert difference < 4e-4, ('FBX sampled transform differs', constrained, index, name, difference)
    assert max_foot < 4e-4, ('Foot drift', max_foot)
    # Both object motion and Hips/control motion exist; this comparison detects
    # applying packet root motion or the object transform a second time.
    actual_delta = (evaluated.matrix_world @ evaluated.pose.bones['Hips'].matrix).translation - expected[0]['Hips'].translation
    desired_delta = expected[-1]['Hips'].translation - expected[0]['Hips'].translation
    assert desired_delta.length > .5 and (actual_delta - desired_delta).length < 4e-4
    return {'constrained': constrained, 'reloaded_library_snapshot': reload_snapshot,
            'samples': len(expected), 'rest_error': max_rest, 'reference_frame_error': reference_error,
            'pose_error': max_pose, 'foot_error': max_foot, 'duration': duration,
            'effective_sample_rate': result['effective_sample_rate']}


def test_launcher_preserves_live_state_and_refuses_existing(folder):
    rig, action, start, end = fixture(True)
    mesh = bpy.data.meshes.new('Face Data')
    mesh.from_pydata([(0, 0, 0), (.1, 0, 0), (0, .1, 0)], [], [(0, 1, 2)])
    face = bpy.data.objects.new('Face With Animated Shape', mesh)
    bpy.context.scene.collection.objects.link(face)
    face.modifiers.new('Bound To Character', 'ARMATURE').object = rig
    face.shape_key_add(name='Basis')
    smile = face.shape_key_add(name='Smile')
    for frame, value in ((start, 0.), (end, .7)):
        smile.value = value
        smile.keyframe_insert('value', frame=frame)
    packet = folder / 'source.cdanim.json'
    packet.write_bytes(b'Original imported source packet')
    fixed_hash = hashlib.sha256(packet.read_bytes()).hexdigest()
    action['character_designer_unity_animation_package'] = str(packet)
    action['character_designer_unity_animation_package_sha256'] = fixed_hash
    rig.animation_data.use_nla = True
    rig.animation_data.action_blend_type, rig.animation_data.action_influence = 'ADD', .35
    worker._frame(bpy.context.scene, 7.375)
    bpy.context.view_layer.update()
    before = live_state(rig)
    destination = folder / 'LiveSnapshot.fbx'
    # Snapshot serialization really runs; the child process is deliberately
    # mocked so this bounded suite never launches a concurrent Blender process.
    with patch.object(launcher.subprocess, 'Popen') as process:
        job = launcher.begin_export(bpy.context, rig, action, destination,
                                    frame_start=start, frame_end=end, loop=False, sample_rate=30)
        try:
            assert process.call_count == 1 and (job['root'] / 'animation.blend').is_file()
            assert live_state(rig) == before
            written = json.loads((job['root'] / 'job.json').read_text(encoding='utf8'))
            assert written['frame_start'] == start and written['frame_end'] == end
            assert 'Shape Key animation: ' + face.name in written['unsupported_channels']
            assert job['source_package_sha256'] == fixed_hash
            # Replacing the packet while the worker runs must not rewrite the
            # provenance of the Action that was actually exported.
            packet.write_bytes(b'A different packet later reused this path')
            (job['stage'] / destination.name).write_bytes(b'Complete staged FBX placeholder')
            published = launcher._publish(job, {'ok': True})
            metadata = Path(published['metadata'])
            assert json.loads(metadata.read_text(encoding='utf8'))['source_package_sha256'] == fixed_hash
            assert published['source_package_sha256'] != hashlib.sha256(packet.read_bytes()).hexdigest()
            destination.unlink()
            metadata.unlink()
        finally:
            launcher._dispose(job)
    for existing in (destination, destination.with_suffix('.animation.json')):
        existing.write_bytes(b'Existing artist data')
        with patch.object(launcher.subprocess, 'Popen') as process:
            try:
                launcher.begin_export(bpy.context, rig, action, destination, frame_start=start, frame_end=end)
                raise AssertionError('Existing output was overwritten')
            except launcher.AnimationExportError as exc:
                assert 'exists' in str(exc)
            assert not process.called and existing.read_bytes() == b'Existing artist data'
        existing.unlink()
    assert live_state(rig) == before


def test_invalid_jobs_and_external_animation(folder):
    rig, action, start, end = fixture()
    job = spec(rig, action, start, end, folder)
    for change in ({'frame_end': start}, {'sample_rate': float('nan')}, {'loop': 1},
                   {'frame_end': 1000000, 'sample_rate': 240}, {'filename': '../escape.fbx'}):
        try:
            worker.export_job({**job, **change})
            raise AssertionError('Invalid job accepted: ' + repr(change))
        except worker.AnimationExportError:
            pass
    target = bpy.data.objects.new('Animated External Target', None)
    bpy.context.scene.collection.objects.link(target)
    for frame, x in ((1, 0), (20, .4)):
        target.location.x = x
        target.keyframe_insert('location', frame=frame)
    constraint = rig.pose.bones['Foot.L'].constraints.new('COPY_LOCATION')
    constraint.target = target
    try:
        worker.export_job(job)
        raise AssertionError('External animated constraint was silently sampled')
    except worker.AnimationExportError as exc:
        assert 'External animated dependency' in str(exc)
    assert not (folder / job['filename']).exists()


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix='cdesigner-animation-export-test-') as temporary:
        folder = Path(temporary)
        test_launcher_preserves_live_state_and_refuses_existing(folder)
        print('PASS live Action/NLA/Rest/scene preservation and existing-file refusal')
        test_invalid_jobs_and_external_animation(folder)
        print('PASS finite/range/sample/path validation and external-dependency refusal')
        for constrained in (False, True):
            case = folder / ('controls' if constrained else 'native')
            case.mkdir()
            results.append(roundtrip(case, constrained))
            print('PASS', json.dumps(results[-1], sort_keys=True))
        case = folder / 'reloaded-library-snapshot'
        case.mkdir()
        results.append(roundtrip(case, False, reload_snapshot=True))
        print('PASS', json.dumps(results[-1], sort_keys=True))
    print('ANIMATION_EXPORT_TESTS_PASSED', json.dumps(results, sort_keys=True))


if __name__ == '__main__':
    main()
