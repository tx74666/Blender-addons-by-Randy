"""Create an independent linked Walk example in an isolated Blender session.

--link character_animation_link.json --model Cosha.fbx --output artifacts
--prepare-only writes the .blend and one worker job without starting a child.
Run that worker later, then call finalize_prepared(finalize_json) in its process.
No existing X scene or production model is opened for editing or saved.
"""

import argparse
import json
from pathlib import Path
import sys
import time
import traceback
import uuid

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from character_designer import animation_link, animation_link_source, animation_export
from character_designer import unity_animation as ua
from prepare_animation_roundtrip_examples import edit_walk, prepare_job, sha256, update_frame


def collision_state(scene, rig):
    layer = scene.view_layers[0]
    return {'rig_pointer': rig.as_pointer(), 'data_pointer': rig.data.as_pointer(),
            'scene_pointer': scene.as_pointer(), 'rest': ua._rest_state(rig.data),
            'matrix': [float(value) for row in rig.matrix_world for value in row],
            'mode': rig.mode, 'rig_properties': dict(rig.items()), 'data_properties': dict(rig.data.items()),
            'active': layer.objects.active.as_pointer() if layer.objects.active else None,
            'selected': sorted(obj.name for obj in layer.objects if obj.select_get(view_layer=layer)),
            'scene_objects': sorted(scene.objects.keys()), 'scene_properties': dict(scene.items()),
            'frame': scene.frame_current + scene.frame_subframe,
            'range': (scene.frame_start, scene.frame_end, scene.frame_preview_start,
                      scene.frame_preview_end, scene.use_preview_range),
            'fps': (scene.render.fps, scene.render.fps_base),
            'units': (scene.unit_settings.system, scene.unit_settings.scale_length)}


def make_collision_fixture():
    scene = bpy.context.scene
    scene.name = 'Link Collision Original'
    data = bpy.data.armatures.new('Link Collision Fixture Data')
    rig = bpy.data.objects.new('CoshaRig', data)
    scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    bone = data.edit_bones.new('FixtureBone')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode='POSE')
    rig.location = (1.0, 2.0, 3.0)
    rig['collision_fixture'], data['collision_fixture'] = 'object', 'armature'
    scene['collision_fixture'] = 'original scene'
    scene.render.fps, scene.render.fps_base = 23, 1.001
    scene.frame_start, scene.frame_end = 4, 42
    scene.frame_preview_start, scene.frame_preview_end, scene.use_preview_range = 7, 19, True
    scene.frame_set(13, subframe=0.25)
    bpy.context.view_layer.update()
    return scene, rig, collision_state(scene, rig)


def check_and_remove_collision_fixture(fixture, result):
    scene, rig, before = fixture
    if result.rig is rig or result.rig.name == 'CoshaRig' or not result.rig.name.startswith('CoshaRig.'):
        raise AssertionError('The linked model did not retain a distinct suffixed rig name.')
    if result.scene is scene or result.export_rig_name != 'CoshaRig':
        raise AssertionError('The linked model did not preserve its independent scene and original export root.')
    if collision_state(scene, rig) != before:
        raise AssertionError('Importing the namesake changed the original scene, rig, Rest or settings.')
    report = {'passed': True, 'original_state_preserved': True, 'original_mode': before['mode'],
              'editing_rig_name': result.rig.name, 'export_rig_name': result.export_rig_name}
    data = rig.data
    bpy.context.window.scene = scene
    if rig.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.window.scene = result.scene
    bpy.data.batch_remove(ids=(scene, rig, data))
    report['fixture_removed_before_save'] = True
    return report


def verify_source_motion(result, packet):
    rig = result.rig
    mapping = ua._mapping(rig, packet, bpy.context.scene.unit_settings.scale_length)
    fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    maximum = 0.0
    for index, sample in enumerate(packet['frames']):
        update_frame(result.first_frame + sample['time'] * fps)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        desired = ua.expected_world_matrices(rig, packet, index, mapping=mapping)
        for name, matrix in desired.items():
            actual = rig.matrix_world @ evaluated.pose.bones[name].matrix
            maximum = max(maximum, max(abs(actual[i][j] - matrix[i][j]) for i in range(4) for j in range(4)))
    if maximum > 4.0e-4:
        raise AssertionError('The imported source rig does not reproduce the linked motion: ' + str(maximum))
    return {'bones': len(mapping.indices), 'samples': len(packet['frames']),
            'bind_error_metres': mapping.error_metres, 'maximum_pose_matrix_error': maximum}


def reopen_saved_example(result, blend_file, manifest):
    """Reopen only this newly created editing file and discard all old ID refs."""
    saved = {'rig': result.rig.name, 'action': result.action.name,
             'scene': result.scene.name, 'collection': result.collection.name,
             'first_frame': float(result.first_frame), 'last_frame': float(result.last_frame),
             'model_file': result.model_file, 'model_sha256': result.model_sha256,
             'export_rig_name': result.export_rig_name,
             'slot_handle': int(result.rig.animation_data.action_slot.handle),
             'association': animation_link.get_link(result.rig)}
    digest = sha256(blend_file)
    opened = bpy.ops.wm.open_mainfile(filepath=str(blend_file), use_scripts=False, load_ui=False)
    if 'FINISHED' not in opened or Path(bpy.data.filepath).resolve() != blend_file.resolve():
        raise AssertionError('The independent linked editing file did not reopen.')
    rig = bpy.data.objects.get(saved['rig'])
    action = bpy.data.actions.get(saved['action'])
    scene = bpy.data.scenes.get(saved['scene'])
    collection = bpy.data.collections.get(saved['collection'])
    if rig is None or action is None or scene is None or collection is None:
        raise AssertionError('The saved editing scene lost its rig, Action or collection.')
    if scene.objects.get(rig.name) is not rig or collection.objects.get(rig.name) is not rig:
        raise AssertionError('The saved rig no longer belongs to its independent editing scene.')
    association = animation_link.get_link(rig)
    if association != saved['association'] or animation_link._identity(association['identity']) != animation_link._identity(manifest):
        raise AssertionError('The saved Link association or complete source identity changed after reopening.')
    ad = rig.animation_data
    if ad is None or ad.action is not action or rig.get(animation_link.ACTION_KEY) is not action:
        raise AssertionError('The linked Action pointer did not survive saving and reopening.')
    if (ad.action_slot is None or ad.action_slot.handle != saved['slot_handle']
            or association['action_slot'] != saved['slot_handle']):
        raise AssertionError('The exact linked Action slot did not survive saving and reopening.')
    if sha256(blend_file) != digest:
        raise AssertionError('Reopening changed the independent editing file on disk.')
    bpy.context.window.scene = scene
    bpy.context.view_layer.objects.active = rig
    result = animation_link_source.SourceResult(
        rig, action, saved['first_frame'], saved['last_frame'], scene, collection,
        saved['model_file'], saved['model_sha256'], saved['export_rig_name'])
    return result, {'passed': True, 'link_id': association['link_id'],
                    'complete_identity_preserved': True, 'association_preserved': True,
                    'active_linked_action_pointer_preserved': True,
                    'action_slot_handle': saved['slot_handle'], 'blend_sha256': digest,
                    'reopened_file_unchanged': True}


def prepare_serial(result, manifest, manifest_path, blend_file, token):
    revision = manifest_path.parent / 'blender_revisions' / uuid.UUID(manifest['linkId']).hex / token
    revision.mkdir(parents=True)
    job = prepare_job(revision, result.rig, result.action, result.first_frame, result.last_frame,
                      bool(result.action.get('unity_loop_time', False)), blend_file, 'worker')
    job_path = Path(job['job'])
    spec = json.loads(job_path.read_text(encoding='utf-8'))
    spec['export_rig_name'] = result.export_rig_name
    spec['sample_rate'] = float(result.action['unity_sample_rate'])
    job_path.write_text(json.dumps(spec, indent=2), encoding='utf-8')
    finalize_path = job_path.with_name('finalize.json')
    finalize = {'manifest_path': str(manifest_path),
                'expected_identity': {key: manifest[key] for key in
                                      (*animation_link.IDENTITY_FIELDS, *animation_link.OPTIONAL_IDENTITY_FIELDS)
                                      if key in manifest},
                'publish_job': job['publish'], 'blend_file': str(blend_file),
                'source_action': result.action.name, 'model_file': result.model_file,
                'model_sha256': result.model_sha256,
                'published_result': str(job_path.with_name('published_result.json'))}
    finalize_path.write_text(json.dumps(finalize, indent=2), encoding='utf-8')
    return {**job, 'export_rig_name': result.export_rig_name, 'finalize': str(finalize_path)}


def finalize_prepared(finalize_json):
    """Publish the completed pair and use the production Link finalizer once."""
    state = json.loads(Path(finalize_json).read_text(encoding='utf-8'))
    published_path = Path(state['published_result'])
    if published_path.exists():
        # A prior finalization may have published the immutable pair before a
        # concurrent Link update rejected its pointer. Revalidate that same pair.
        result = json.loads(published_path.read_text(encoding='utf-8'))
    else:
        job = json.loads(Path(state['publish_job']).read_text(encoding='utf-8'))
        for key in ('stage', 'destination', 'metadata'):
            job[key] = Path(job[key])
        completed = json.loads((job['stage'] / 'result.json').read_text(encoding='utf-8'))
        if not completed.get('ok'):
            raise AssertionError('The prepared animation worker did not succeed.')
        result = animation_export._publish(job, completed)
        published_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    result = animation_link.publish_linked_revision(
        state['manifest_path'], state['expected_identity'], result,
        blend_file=state['blend_file'], source_action=state['source_action'],
        model_file=state['model_file'], model_sha256=state['model_sha256'])
    print('ANIMATION_LINK_PUBLISHED ' + json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--link', required=True)
    parser.add_argument('--model')
    parser.add_argument('--output', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--check-name-collision', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    if not bpy.app.background or bpy.data.filepath:
        raise AssertionError('Run this example in a fresh --background --factory-startup process.')
    manifest_path = Path(args.link).resolve()
    manifest = animation_link.load_link(manifest_path)
    manifest_before = sha256(manifest_path)
    token = uuid.uuid4().hex
    folder = Path(args.output).resolve() / ('linked-walk-' + token[:10])
    folder.mkdir(parents=True)
    blend_file = folder / 'Cosha_Walk_Link.blend'
    report = {'ok': False, 'manifest': str(manifest_path), 'prepare_only': args.prepare_only,
              'input_package_sha256': manifest['sourcePackageSha256'], 'blend_file': str(blend_file)}
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        fixture = make_collision_fixture() if args.check_name_collision else None
        result = animation_link_source.import_source(bpy.context, manifest_path, args.model)
        if fixture:
            report['name_collision'] = check_and_remove_collision_fixture(fixture, result)
        packet = ua.load_package(manifest['sourcePackage'])
        report['source_motion'] = verify_source_motion(result, packet)
        result.action.name = 'Cosha_Walk_Link_Edit_' + token[:10]
        report['walk_edit'] = edit_walk(result.rig, result, packet, set(result.rig.data.bones.keys()))
        animation_link.bind_action(result.rig, result.action, manifest_path, result.export_rig_name, result.model_file)
        update_frame(result.first_frame)
        saved = bpy.ops.wm.save_as_mainfile(filepath=str(blend_file), check_existing=False)
        if 'FINISHED' not in saved or not blend_file.is_file():
            raise AssertionError('The independent linked editing file was not saved.')
        result, report['save_reopen_persistence'] = reopen_saved_example(result, blend_file, manifest)
        report.update(action=result.action.name, rig=result.rig.name, export_rig_name=result.export_rig_name,
                      model_file=result.model_file, model_sha256=result.model_sha256,
                      blend_sha256=sha256(blend_file))
        if args.prepare_only:
            report['job'] = prepare_serial(result, manifest, manifest_path, blend_file, token)
            if sha256(manifest_path) != manifest_before:
                raise AssertionError('Preparing an unexported example changed its Link.')
        else:
            job = animation_link.begin_linked_export(bpy.context, result.rig)
            published = None
            while published is None:
                published = animation_export.poll_export(job)
                if published is None:
                    time.sleep(0.1)
            report['published'] = published
        if sha256(result.model_file) != result.model_sha256:
            raise AssertionError('The production model FBX changed during example preparation.')
        if sha256(manifest['sourcePackage']) != manifest['sourcePackageSha256'].lower():
            raise AssertionError('The Unity source packet changed during example preparation.')
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        report_path = folder / 'preparation.json'
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('ANIMATION_LINK_EXAMPLE ' + json.dumps(report))
    if not report['ok']:
        raise AssertionError(report.get('error', 'Linked example preparation failed.'))


if __name__ == '__main__':
    main()
