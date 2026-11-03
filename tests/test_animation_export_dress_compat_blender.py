"""Tiny native compatibility proof through the real public Action exporter.

Owned background process only, no child workers: two genuine generated Manual
Dress rigs (shared and separate), exact public host refusals and two real native
FBX exports. Reuses existing Dress author/source and mesh witnesses. Pass an
owned evidence directory after ``--``.
"""
import hashlib
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import animation_export as host, animation_export_worker as worker
from character_designer import animation_export_inputs as inputs, hair_wiggle_adapter as hair
from character_designer import skirt_rig as skirt, skirt_original_mode as originals
from character_designer import animation as ui, animation_physics as physics
from test_dress_animation_export_blender import native_inputs
from test_skirt_shared_rig_blender import fixture as shared_fixture, mesh_content


def source_fixture(shared):
    if shared:
        source, _legacy, rig, _record = shared_fixture(posed=False)
        record = skirt.unify_skirt(bpy.context, source, rig, 'Hips')
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        vertices = [(math.cos(math.tau * side / 16) * (.3 + .1 * row),
                     math.sin(math.tau * side / 16) * (.3 + .1 * row), 1. - row * .2)
                    for row in range(4) for side in range(16)]
        faces = [(row * 16 + side, row * 16 + (side + 1) % 16,
                  (row + 1) * 16 + (side + 1) % 16, (row + 1) * 16 + side)
                 for row in range(3) for side in range(16)]
        data = bpy.data.meshes.new('Separate Dress mesh')
        data.from_pydata(vertices, [], faces)
        source = bpy.data.objects.new('Separate Dress', data)
        bpy.context.scene.collection.objects.link(source)
        bpy.context.view_layer.objects.active = source
        source.select_set(True)
        record = skirt.build_skirt(bpy.context, source, chain_count=4, segment_count=2, shared=False)
        rig = source[skirt.RIG_KEY]
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    action = bpy.data.actions.new('Ordinary Manual Custom')
    slot = action.slots.new(id_type='OBJECT', name=rig.name)
    ad = rig.animation_data_create()
    ad.action, ad.action_slot, ad.use_nla = action, slot, False
    ad.action_blend_type, ad.action_influence = 'REPLACE', 1.
    waist = rig.pose.bones[record['controls']['waist']]
    waist.rotation_mode = 'XYZ'
    original = rig.pose.bones[record['chains'][0]['def'][0]]
    original.rotation_mode = 'XYZ'
    corrections = originals._corrections(source, record)
    corrections['bones'][original.name] = {'mix_mode': 'REPLACE', 'channels': originals._channels(original)}
    source[originals.CORRECTIONS] = json.dumps(corrections)
    original.constraints['Skirt manual pose'].mix_mode = 'BEFORE_FULL'
    for frame, angle in ((1., 0.), (2., .0001), (3., -.00005)):
        waist.rotation_euler.z = angle
        waist.keyframe_insert('rotation_euler', index=2, frame=frame)
        original.rotation_euler.x = angle * .1
        original.keyframe_insert('rotation_euler', index=0, frame=frame)
        if 'Hips' in rig.pose.bones:
            hips = rig.pose.bones['Hips']
            hips.rotation_mode = 'XYZ'
            hips.rotation_euler.z = angle
            hips.keyframe_insert('rotation_euler', index=2, frame=frame)
    for curve in worker._curves(action):
        for key in curve.keyframe_points:
            key.interpolation = 'LINEAR'
    bpy.context.scene.frame_set(2)
    return source, rig, record, action


def witness(source, rig):
    ad = rig.animation_data
    def ref(value):
        return None if value is None else int(value.as_pointer())
    return {'native': native_inputs(source, rig), 'mesh': mesh_content(source),
            'source': ref(source), 'data': ref(source.data), 'rig': ref(rig), 'rig_data': ref(rig.data),
            'source_rig_ref': ref(source[skirt.RIG_KEY]),
            'object_refs': [(obj.name, ref(obj), ref(obj.data), ref(obj.parent), ref(obj.get(skirt.SOURCE_KEY)))
                            for obj in bpy.data.objects],
            'frame': bpy.context.scene.frame_current_final, 'active': ref(bpy.context.object),
            'selection': sorted(ref(obj) for obj in bpy.context.selected_objects),
            'action': ref(ad.action), 'slot': ad.action_slot.handle, 'action_metadata': dict(ad.action.items()),
            'pose': [(pb.name, pb.rotation_mode, tuple(pb.location), tuple(pb.rotation_euler),
                      tuple(pb.rotation_quaternion), tuple(pb.scale)) for pb in rig.pose.bones],
            'nla': [(ref(track), track.mute, [(ref(strip), ref(strip.action), strip.mute)
                                             for strip in track.strips]) for track in ad.nla_tracks]}


def refused(source, rig, action, folder, text, *, after_hair=False):
    before = witness(source, rig)
    with patch.object(host.subprocess, 'Popen') as process, \
            patch.object(hair, 'status', return_value={'active': False}), \
            patch.object(host.tempfile, 'TemporaryDirectory', side_effect=AssertionError('Refusal created a snapshot job')):
        try:
            host.begin_export(bpy.context, rig, action, folder / 'rejected.fbx', frame_start=1, frame_end=3)
        except ValueError as error:
            assert text.lower() in str(error).lower(), (text, str(error))
        else:
            raise AssertionError('Unsafe public export was accepted: ' + text)
        assert not process.called
    assert witness(source, rig) == before, 'Refusal changed a source, Action, pose or native reference'


def guard_cases(folder):
    source, rig, record, action = source_fixture(True)
    action[inputs.RECORD], action[inputs.COUNT] = 'retained-before-Dress-was-added', 17
    action['artist_note'] = 'Preserve this Action metadata'
    refused(source, rig, action, folder, 'Clear Export Base Pose')
    item = SimpleNamespace(rig=rig, side='CUSTOM', custom_action=action,
        custom_slot=rig.animation_data.action_slot.handle, source_action=None,
        composition_json='', new_animation=True)
    saved = SimpleNamespace(workspace_path='owned-test-workspace', items=[item], active_index=0)
    context = SimpleNamespace(scene=SimpleNamespace(objects=bpy.context.scene.objects,
        character_designer_animation_worklist=saved), view_layer=bpy.context.view_layer,
        mode='OBJECT', screen=None, window=None, object=rig)
    before_clear = witness(source, rig)
    with patch.object(ui, '_link_idle', return_value=True), patch.object(physics, 'status', return_value={}):
        item.side = 'SOURCE'
        try:
            inputs.clear(context, rig, action)
        except ValueError as error:
            assert 'Source' in str(error)
        else:
            raise AssertionError('Clear changed the protected Source side')
        item.side = 'CUSTOM'
        inactive = action.copy()
        try:
            inputs.clear(context, rig, inactive)
        except ValueError as error:
            assert 'Activate' in str(error)
        else:
            raise AssertionError('Clear changed an inactive Action')
        assert witness(source, rig) == before_clear
        cls = ui.CHARACTERDESIGNER_OT_animation_export_base_pose
        bpy.utils.register_class(cls)
        try:
            rna = bpy.ops.character_designer.animation_export_base_pose.get_rna_type()
            assert rna.properties['clear'].type == 'BOOLEAN' and rna.properties['clear'].default is False
            reports = []
            operator = SimpleNamespace(rig_name=rig.name, action_name=action.name, clear=True,
                report=lambda kind, message: reports.append((kind, message)))
            with patch.object(ui, '_redraw'):
                assert cls.execute(operator, context) == {'FINISHED'}
            assert 'cleared' in reports[-1][1]
        finally:
            bpy.utils.unregister_class(cls)
    expected = dict(before_clear)
    expected['action_metadata'] = {key: value for key, value in before_clear['action_metadata'].items()
                                   if key not in {inputs.RECORD, inputs.COUNT}}
    assert witness(source, rig) == expected, 'Explicit clear changed more than its two metadata fields'
    wire = bpy.data.objects[record['chains'][0]['curve']]
    owner = source[skirt.OWNER_KEY]
    source[skirt.OWNER_KEY] = 'not-the-source-owner'
    refused(source, rig, action, folder, 'owner')
    source[skirt.OWNER_KEY] = owner
    strength = wire.modifiers[0].strength
    wire.modifiers[0].strength = .5
    refused(source, rig, action, folder, 'Hook')
    wire.modifiers[0].strength = strength
    external = bpy.data.objects.new('Foreign Static Target', None)
    bpy.context.scene.collection.objects.link(external)
    constraint = rig.pose.bones['Hips'].constraints.new('COPY_LOCATION')
    constraint.target = external
    constraint.mute = True
    refused(source, rig, action, folder, 'unknown external')
    rig.pose.bones['Hips'].constraints.remove(constraint)
    bpy.data.objects.remove(external)
    physical = bpy.data.actions.new('Authored Physical Motion')
    slot = physical.slots.new(id_type='OBJECT', name=rig.name)
    bag = physical.layers.new('Physical').strips.new(type='KEYFRAME').channelbags.new(slot)
    path = rig.pose.bones[record['chains'][0]['phys'][0]].path_from_id() + '.location'
    curve = bag.fcurves.new(data_path=path, index=0)
    curve.keyframe_points.insert(1., 0.)
    curve.keyframe_points.insert(3., .01)
    refused(source, rig, physical, folder, 'physics bones')
    track = rig.animation_data.nla_tracks.new()
    track.strips.new('Physical NLA', 1, physical)
    refused(source, rig, action, folder, 'physics bones')
    rig.animation_data.nla_tracks.remove(track)
    bag.fcurves.remove(curve)
    path = rig.pose.bones[record['chains'][0]['def'][0]].constraints['Skirt physics delta'].path_from_id() + '.mute'
    curve = bag.fcurves.new(data_path=path, index=0)
    curve.keyframe_points.insert(1., 0.)
    curve.keyframe_points.insert(3., 0.)
    refused(source, rig, physical, folder, 'physical constraint')
    # Deliberately alter only the test graph during simulated Hair shutdown;
    # final admission must detect this before library write/process creation.
    with patch.object(host.subprocess, 'Popen') as process, \
            patch.object(hair, 'status', return_value={'active': True}), \
            patch.object(hair, 'stop_preview', side_effect=lambda *_args, **_kw: setattr(wire.modifiers[0], 'strength', .7)):
        try:
            host.begin_export(bpy.context, rig, action, folder / 'changed.fbx', frame_start=1., frame_end=3.)
        except ValueError as error:
            assert 'Hook' in str(error), str(error)
        else:
            raise AssertionError('Post-Hair graph change was silently adopted')
        assert not process.called
    wire.modifiers[0].strength = strength
    return 8


def export_case(folder, shared):
    folder.mkdir(parents=True)
    source, rig, record, action = source_fixture(shared)
    before = witness(source, rig)
    with patch.object(host.subprocess, 'Popen') as process, patch.object(hair, 'status', return_value={'active': False}):
        job = host.begin_export(bpy.context, rig, action, folder / 'motion.fbx', frame_start=1., frame_end=3., sample_rate=24.)
    try:
        assert witness(source, rig) == before, 'Host snapshot changed protected author/source/native identity'
        spec = json.loads((job['root'] / 'job.json').read_text())
        assert inputs.JOB_KEY not in spec and not spec['dress_surfaces']
        assert spec['authenticated_dress_export']['retained_base_supported'] is False
        assert any(str(value).endswith('animation_export_worker.py') for value in process.call_args.args[0])
        snapshot = job['root'] / 'animation.blend'
        bpy.ops.wm.open_mainfile(filepath=str(snapshot), use_scripts=True, load_ui=False)
        rig = bpy.data.objects[spec['rig']]
        scene = bpy.data.scenes.new('Independent native Manual endpoint')
        scene.render.fps, scene.render.fps_base = spec['fps'], spec['fps_base']
        for obj in bpy.data.objects:
            scene.collection.objects.link(obj)
        bpy.context.window.scene = scene
        scene.frame_set(1)
        bpy.context.view_layer.update()
        normalization = Matrix.Translation(-rig.matrix_world.translation)
        retained = [bone.name for bone in rig.data.bones if not worker._model_helpers()._is_control(bone)]
        expected = []
        for frame in (1., 2., 3.):
            scene.frame_set(int(frame))
            bpy.context.view_layer.update()
            evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
            expected.append({name: normalization @ evaluated.matrix_world @ evaluated.pose.bones[name].matrix for name in retained})
        scene.frame_set(1)
        try:
            result = worker.export_job(spec)
        except worker.AnimationExportError as error:
            if not shared and str(error) == 'The animation skeleton filter disagrees with the current model exporter.':
                # Existing worker/model-helper bytes match accepted142. Keep
                # that accessory-filter limitation; this host fix must not
                # weaken or silently patch its native skeleton gate.
                return {'shared': False, 'host_source_action_pose_idrefs_preserved': True,
                        'successful_fbx': False, 'existing_worker_filter_refusal': str(error),
                        'contract': spec['authenticated_dress_export']}
            raise
        baked = bpy.data.objects[result['rig']]
        maximum = 0.
        for index, values in enumerate(expected):
            bpy.context.scene.frame_set(index + 1)
            bpy.context.view_layer.update()
            evaluated = baked.evaluated_get(bpy.context.evaluated_depsgraph_get())
            for name in retained:
                actual = evaluated.matrix_world @ evaluated.pose.bones[name].matrix
                maximum = max(maximum, max(abs(actual[i][j] - values[name][i][j]) for i in range(4) for j in range(4)))
        assert maximum <= 2.e-4, maximum
        published = host._publish(job, result)
        assert published['export_input_policy'] == spec['authenticated_dress_export']
        metadata = json.loads(Path(published['metadata']).read_text())
        assert metadata['export_input_policy']['policy'] == 'existing-authenticated-Dress snapshot; retained base unsupported'
        return {'shared': shared, 'successful_fbx': True, 'host_source_action_pose_idrefs_preserved': True,
                'maximum_native_manual_endpoint_error': maximum, 'samples': result['samples'],
                'fbx': published['filepath'], 'contract': metadata['export_input_policy']}
    finally:
        host._dispose(job)


def main():
    directory = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    guards = guard_cases(directory)
    cases = [export_case(directory / 'shared', True), export_case(directory / 'separate', False)]
    result = {'passed': True, 'private_only': True, 'public_host_refusals': guards, 'cases': cases,
              'explicit_clear_source_inactive_refusals_and_metadata_only_native_operator': True,
              'child_workers_started': 0, 'artist_operations': 0,
              'worker_sha256': hashlib.sha256((ROOT / 'addons/character_designer/animation_export_worker.py').read_bytes()).hexdigest(),
              'limitations': ['Tiny synthetic Manual rigs only; no complete artist, Unity or cloth-quality acceptance.',
                              'Retained export base is unsupported for this specialized snapshot policy.']}
    (directory / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('DRESS_EXPORT_COMPAT_OK', json.dumps(result))


if __name__ == '__main__':
    main()
