"""Prepared native Dress/Body skeletal export check; not run by pure tests.

Run only in an isolated background Blender, after a coordinated resource window:
  blender --background --factory-startup --disable-autoexec --threads 1 \
    --python-exit-code 1 --python tests/test_dress_animation_export_blender.py -- \
    --input-blend <passing private actual-surface QA.blend> --output <new QA folder>

This performs one three-frame skeletal FBX export, without a child Blender,
renderer, Unity process or artist-file save. It exercises the public launcher
library snapshot with Popen intercepted, then reopens that real private snapshot.
No claim about physical skirt quality, Unity import or Magica follows from it.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import traceback
from unittest.mock import patch

import bpy
from mathutils import Matrix


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import animation_export as launcher
from character_designer import animation_export_worker as worker
from character_designer import dress_export_snapshot as boundary
from character_designer import skirt_original_mode as originals
from character_designer import skirt_rig as skirt
from character_designer import skirt_surface as surface


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def native_inputs(source, rig):
    keys = source.data.shape_keys
    ad = rig.animation_data
    action = ad.action if ad else None
    return {
        'record': source[skirt.RECORD_KEY],
        'corrections': source.get(originals.CORRECTIONS),
        'basis': [tuple(vertex.co) for vertex in source.data.vertices],
        'keys': None if keys is None else [(key.name, [tuple(vertex.co) for vertex in key.data])
                                         for key in keys.key_blocks],
        'groups': [[(entry.group, entry.weight) for entry in vertex.groups] for vertex in source.data.vertices],
        'rest': [(bone.name, [list(row) for row in bone.matrix_local]) for bone in rig.data.bones],
        'holder': skirt.physics_control(source)[0].get('physics_influence'),
        'mode_flags': [(modifier.name, modifier.show_viewport, modifier.show_render) for modifier in source.modifiers],
        'constraints': [(pb.name, [(constraint.name, constraint.mute) for constraint in pb.constraints])
                        for pb in rig.pose.bones],
        'selected_action': None if action is None else [(curve.data_path, curve.array_index, curve.mute,
            [(tuple(key.co), tuple(key.handle_left), tuple(key.handle_right), key.interpolation)
             for key in curve.keyframe_points]) for curve in worker._curves(action, ad.action_slot)],
        'drivers': [] if ad is None else [(curve.data_path, curve.mute, curve.driver.type,
            curve.driver.expression, [(variable.name, variable.type,
                [(target.id.name if target.id else None, target.data_path) for target in variable.targets])
                for variable in curve.driver.variables]) for curve in ad.drivers],
    }


def author_action(source, rig, record):
    name = '.QA Dress Body Skeletal Boundary'
    require(bpy.data.actions.get(name) is None, 'The isolated QA Action name is already occupied')
    action = bpy.data.actions.new(name)
    slot = action.slots.new(id_type='OBJECT', name=rig.name)
    ad = rig.animation_data_create()
    ad.action, ad.action_slot, ad.use_nla = action, slot, False
    ad.action_blend_type, ad.action_influence = 'REPLACE', 1.
    mid = rig.pose.bones[record['controls']['chains'][0]['mid']]
    original = rig.pose.bones[record['chains'][0]['def'][0]]
    correction = originals._corrections(source, record)
    if original.name not in correction['bones']:
        correction['bones'][original.name] = {'mix_mode': 'REPLACE', 'channels': originals._channels(original)}
    source[originals.CORRECTIONS] = json.dumps(correction)
    original.constraints['Skirt manual pose'].mix_mode = 'BEFORE_FULL'
    original.rotation_mode = 'XYZ'
    mid.rotation_mode = 'XYZ'
    body = rig.pose.bones.get('Hips')
    require(body is not None, 'The actual Cosha Hips bone is missing')
    body.rotation_mode = 'XYZ'
    for frame, bend in ((1, 0.), (2, .06), (3, -.035)):
        body.rotation_euler[2] = bend
        body.keyframe_insert('rotation_euler', index=2, frame=frame)
        mid.rotation_euler[0] = bend * .4
        mid.keyframe_insert('rotation_euler', index=0, frame=frame)
        original.rotation_euler[0] = bend * .25
        original.keyframe_insert('rotation_euler', index=0, frame=frame)
    return action, original.name, mid.name


def source_and_rig(name):
    source = bpy.data.objects.get(name)
    require(source is not None and source.type == 'MESH', 'The private Dress source is missing')
    rig = source.get(skirt.RIG_KEY)
    require(rig is not None and rig.type == 'ARMATURE', 'The private Dress rig is missing')
    return source, rig, skirt.read_record(source)


def physical_constraint_guard_cases(source, rig, record, selected, output, *, proofs=None):
    """Prepared regression: false mute keys cannot re-enable physics later."""
    deform = rig.pose.bones[record['chains'][0]['def'][0]]
    phys = rig.pose.bones[record['chains'][0]['phys'][0]]
    rotation, aim = deform.constraints['Skirt physics delta'], phys.constraints[0]
    paths = [rotation.path_from_id() + '.mute', rotation.path_from_id() + '.influence',
             aim.path_from_id() + '.mute', aim.path_from_id() + '.influence',
             (deform.path_from_id() + '.constraints[' + str(list(deform.constraints).index(rotation)) + '].mute').replace('"', "'")]
    facts = []

    def refused(action, label):
        before = native_inputs(source, rig)
        with patch.object(launcher.subprocess, 'Popen') as process, patch.object(launcher.tempfile, 'TemporaryDirectory') as temporary:
            try:
                if proofs is None:
                    launcher.begin_export(bpy.context, rig, action, output / 'GuardRefused.fbx',
                                          frame_start=1, frame_end=3)
                else:
                    boundary.prepare_animation_snapshot(rig, proofs, action, private_snapshot=bpy.data.filepath)
            except ValueError as error:
                message = str(error)
                require(any(word in message.lower() for word in ('constraint', 'driver', 'physics bone', 'physical')),
                        'An unrelated error hid the physical constraint guard: ' + message)
            else:
                raise AssertionError('A future physical constraint route was not refused: ' + label)
            require(not process.called and not temporary.called, 'A refused physical Action created a snapshot or process')
        require(native_inputs(source, rig) == before, 'The physical guard changed the author curve or native state')
        facts.append({'case': label, 'error': message, 'author_native_state_preserved': True})

    for index, path in enumerate(paths):
        action = bpy.data.actions.new('.QA Future Physical Constraint ' + str(index))
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        bag = action.layers.new('QA False Physical Mute').strips.new(type='KEYFRAME').channelbags.new(slot)
        curve = bag.fcurves.new(data_path=path, index=0)
        curve.keyframe_points.insert(1, 0.)
        curve.keyframe_points.insert(3, 0.)
        require(rig.animation_data.action != action, 'The selected conflict was accidentally made active')
        try:
            refused(action, 'nonactive_selected_' + str(index))
            if index == 0:
                ad = rig.animation_data
                previous, previous_slot = ad.action, ad.action_slot
                ad.action, ad.action_slot = action, slot
                try:
                    refused(action, 'currently_active_false_mute')
                finally:
                    ad.action, ad.action_slot = previous, previous_slot
        finally:
            bpy.data.actions.remove(action)
    # New mute / aim influence drivers, then an edited generated influence
    # driver. All are QA-only changes removed/restored after the refusal.
    for constraint, prop in ((rotation, 'mute'), (aim, 'influence')):
        curve = constraint.driver_add(prop)
        curve.driver.type, curve.driver.expression = 'SCRIPTED', '0'
        try:
            refused(selected, 'new_driver_' + constraint.name + '_' + prop)
        finally:
            require(constraint.driver_remove(prop), 'Could not remove a QA-only driver')
    curve = next(curve for curve in rig.animation_data.drivers
                 if curve.data_path == rotation.path_from_id() + '.influence')
    driver_type, expression = curve.driver.type, curve.driver.expression
    curve.driver.type, curve.driver.expression = 'SCRIPTED', '1'
    try:
        refused(selected, 'edited_generated_influence_driver')
    finally:
        curve.driver.expression, curve.driver.type = expression, driver_type
    return facts


def run(args, report):
    require(bpy.app.background, 'This prepared check is background-only')
    private_root = Path('D:/Blender/Projects/Character/X/Validation').resolve()
    require(args.input_blend.is_file() and args.input_blend.suffix == '.blend'
            and args.input_blend.is_relative_to(private_root), 'Use a private installed Validation .blend')
    require(args.output.is_relative_to(private_root) and not args.output.exists(), 'Use a new private Validation output folder')
    args.output.mkdir(parents=True)
    input_hash = sha(args.input_blend)
    bpy.ops.wm.open_mainfile(filepath=str(args.input_blend), load_ui=False)
    source, rig, record = source_and_rig(args.source)
    require(record['physics']['backend'] == boundary.BACKEND, 'The input must have the actual-surface backend installed')
    surface.validate(source, rig, record)
    holder, _identifier, holder_path = skirt.physics_control(source)
    require(holder['physics_influence'] == 1., 'The private input must initially use Automatic')
    action, original_name, mid_name = author_action(source, rig, record)
    surface.validate(source, rig, record)
    report['host_physical_constraint_guards'] = physical_constraint_guard_cases(
        source, rig, record, action, args.output)
    before = native_inputs(source, rig)
    # A selected Action containing even a dormant/muted property curve must be
    # preserved and refused; no snapshot or process is created for this request.
    holder.keyframe_insert('[' + json.dumps('physics_influence') + ']', frame=1)
    with patch.object(launcher.subprocess, 'Popen') as process:
        try:
            launcher.begin_export(bpy.context, rig, action, args.output / 'Rejected.fbx', frame_start=1, frame_end=3)
        except launcher.AnimationExportError as error:
            require('physics_influence' in str(error), 'Wrong rejection for author mode animation')
        else:
            raise AssertionError('Author physics mode animation was overwritten or exported silently')
        require(not process.called, 'A refused mode Action started a child process')
    require(holder.keyframe_delete('[' + json.dumps('physics_influence') + ']', frame=1), 'Could not remove the QA-only conflict curve')
    # keyframe_delete may leave an empty FCurve; remove that exact QA curve so
    # the original Action remains free of a physical mode path.
    for layer in action.layers:
        for strip in layer.strips:
            for bag in strip.channelbags:
                for curve in tuple(bag.fcurves):
                    if boundary._path_identity(curve.data_path) == boundary._path_identity(holder_path):
                        bag.fcurves.remove(curve)
    require(native_inputs(source, rig) == before, 'Host rejection changed a protected native input')
    with patch.object(launcher.subprocess, 'Popen') as process:
        job = launcher.begin_export(bpy.context, rig, action, args.output / 'DressBodyBoundary.fbx',
                                    frame_start=1, frame_end=3,
                                    sample_rate=bpy.context.scene.render.fps / bpy.context.scene.render.fps_base)
    try:
        require(process.call_count == 1, 'The public launcher did not reach its intercepted process boundary')
        require(native_inputs(source, rig) == before, 'Public library snapshot changed artist-style native inputs')
        spec = json.loads((job['root'] / 'job.json').read_text(encoding='utf-8'))
        require({proof['source'] for proof in spec['dress_surfaces']} == {source.name},
                'This focused native fixture requires one actual Dress surface on the selected rig')
        snapshot = job['root'] / 'animation.blend'
        report['snapshot_sha256'] = sha(snapshot)
        report['captured_surfaces'] = spec['dress_surfaces']
        source_name, rig_name, action_name = source.name, rig.name, action.name
        bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False)
        source, rig, record = source_and_rig(source_name)
        # This is the real public Object+Action+Scene library file. Do not
        # reconstruct missing memberships or relax the native ownership proof.
        surface.validate_snapshot(source, spec['dress_surfaces'][0])
        report['library_home_scene_proved'] = True
        report['worker_physical_constraint_guards'] = physical_constraint_guard_cases(
            source, rig, record, bpy.data.actions[action_name], args.output, proofs=spec['dress_surfaces'])
        # Independent native Manual endpoint is the expected skeletal output.
        # It retains author spline/Original channels, instead of fitting C to bones.
        holder, _identifier, _path = skirt.physics_control(source)
        holder['physics_influence'] = 0.
        surface.set_mode(source, record, 'MANUAL')
        cloth = bpy.data.objects[record['physics']['proxy']].modifiers[2]
        cloth.show_viewport = cloth.show_render = False
        bpy.context.view_layer.update()
        normalization = Matrix.Translation(-rig.matrix_world.translation)
        retained = [bone.name for bone in rig.data.bones if not worker._model_helpers()._is_control(bone)]
        expected = []
        for frame in (1, 2, 3):
            bpy.context.scene.frame_set(frame)
            bpy.context.view_layer.update()
            evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
            expected.append({name: normalization @ evaluated.matrix_world @ evaluated.pose.bones[name].matrix
                             for name in retained})
        require(max(abs(expected[1][original_name][i][j] - expected[0][original_name][i][j])
                    for i in range(4) for j in range(4)) > 1.e-5, 'Manual/Original QA motion was not observable')
        bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False)
        result = worker.export_job(spec)
        require(result['ok'] and result['samples'] == 3, 'The real three-frame skeletal worker did not finish')
        require(result['dress_surfaces'] and result['dress_surfaces'][0]['physics_omitted'] is True,
                'The worker did not attest its explicit physical omission')
        sampled = bpy.data.objects[result['rig']]
        worst = 0.
        for index, wanted in enumerate(expected):
            bpy.context.scene.frame_set(index + 1)
            bpy.context.view_layer.update()
            evaluated = sampled.evaluated_get(bpy.context.evaluated_depsgraph_get())
            for name in retained:
                actual = evaluated.matrix_world @ evaluated.pose.bones[name].matrix
                worst = max(worst, max(abs(actual[i][j] - wanted[name][i][j]) for i in range(4) for j in range(4)))
        require(worst <= 2.e-4, 'The skeletal output differs from the independent native Manual/Original endpoint')
        destination = args.output / result['filename']
        shutil.copy2(job['stage'] / result['filename'], destination)
        report.update(worker_result=result, maximum_manual_endpoint_world_error=worst,
                      observed_manual_bone=mid_name, observed_original_bone=original_name,
                      fbx_sha256=sha(destination), public_host_inputs_preserved=True)
    finally:
        launcher._dispose(job)
    require(sha(args.input_blend) == input_hash, 'The private input .blend changed on disk')
    report.update(success=True, input_sha256=input_hash, production_effect_accepted=False,
                  limitations='Three-frame real skeletal worker/private public library snapshot only; no render, Unity import, Magica, long motion or per-vertex cloth acceptance.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input-blend', type=lambda value: Path(value).resolve(), required=True)
    parser.add_argument('--output', type=lambda value: Path(value).resolve(), required=True)
    parser.add_argument('--source', default='Dress')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    report = {'success': False}
    try:
        run(args, report)
    except Exception:
        report['error'] = traceback.format_exc()
        if args.output.is_dir():
            (args.output / 'dress_animation_export.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        raise
    (args.output / 'dress_animation_export.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('DRESS_ANIMATION_EXPORT_BOUNDARY_OK', json.dumps(report))
