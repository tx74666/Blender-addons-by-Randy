"""Read-only acceptance/tamper guards on a disposable current-X snapshot.

blender --background --factory-startup --python this.py -- --blend snapshot.blend
    --package original.cdanim.json --report report.json

No Blender launches occur from this script. The supervising task runs it
serially after checking memory. It never saves the input or an artist file.
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('CHARACTER_DESIGNER_TEST_ADDON_ROOT', str(ROOT / 'addons')))
import character_designer as cd
from character_designer import body_original_mode as original
from character_designer import unity_animation as ua
from character_designer import unity_animation_controls as controls
from character_designer import unity_animation_dress as dress
from character_designer import skirt_rig

if os.environ.get('CHARACTER_DESIGNER_TEST_ADDON_ROOT'):
    expected_root = Path(os.environ['CHARACTER_DESIGNER_TEST_ADDON_ROOT']).resolve()
    if not Path(cd.__file__).resolve().is_relative_to(expected_root):
        raise RuntimeError('The requested frozen test add-on root was not loaded.')


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def block_names():
    return {kind: sorted(block.name for block in getattr(bpy.data, kind))
            for kind in ('objects', 'armatures', 'actions', 'curves')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--blend', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    input_path = Path(args.blend).resolve()
    if input_path.name.casefold() == 'x.blend':
        raise ValueError('Use a disposable snapshot, never artist X.blend.')
    input_sha, packet_sha = file_hash(input_path), file_hash(args.package)
    report = {'passed': False, 'source_sha256': input_sha, 'packet_sha256': packet_sha,
              'scope': 'Current shared Dress graph plus complete packet static routing; no skin-quality claim.'}
    report['runtime'] = {'version': list(cd.bl_info['version']), 'blender': bpy.app.version_string,
        'path': cd.__file__, 'module_sha256': {module.__name__: file_hash(module.__file__)
                                              for module in (cd, ua, controls, dress)}}
    try:
        cd.register()
        bpy.ops.wm.open_mainfile(filepath=str(input_path), use_scripts=False, load_ui=False)
        scene = bpy.data.scenes.get('Scene')
        rig = bpy.data.objects.get('CoshaRig')
        assert scene is not None and rig is not None and rig.name in scene.objects
        bpy.context.window.scene = scene
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        if original.active(rig):
            original._validate_session_rest(rig)
            original.leave(bpy.context, rig)
        package = ua.load_package(args.package)
        names = {bone['name'] for bone in package['bones'] if bone['name'] in rig.data.bones}
        plan = controls.build_plan(rig, names, package=package)
        assert plan and plan.delegated_names and plan.delegated_names < plan.mapped_names
        baseline_rest, baseline_blocks = ua._rest_state(rig.data), block_names()
        report['mapped_bind_bones'] = len(plan.mapped_names)
        report['authored_body_hair_bones'] = len(plan.mapped_names - plan.delegated_names)
        report['delegated_rest_local_dress_bones'] = len(plan.delegated_names)
        report['static_local_matrix_error'] = dress.static_packet_proof(package, plan.delegated_names)
        checks = []

        def rejection(label, test_package=package):
            blocks = block_names()
            try:
                controls.build_plan(rig, names, package=test_package)
                raise AssertionError('Unsafe Dress graph was accepted: ' + label)
            except ua.UnityAnimationError:
                pass
            assert ua._rest_state(rig.data) == baseline_rest and block_names() == blocks
            checks.append(label)

        source = skirt_rig.shared_sources(rig)[0]
        record = skirt_rig.read_record(source)
        chain = record['chains'][0]
        raw_record = source[skirt_rig.RECORD_KEY]
        unsupported = copy.deepcopy(record)
        unsupported['physics'] = {'backend': 'Unsupported graph; exact validation required'}
        try:
            source[skirt_rig.RECORD_KEY] = json.dumps(unsupported)
            rejection('unsupported_physics_record')
        finally:
            source[skirt_rig.RECORD_KEY] = raw_record
        terminal = rig.pose.bones[chain['manual'][-1]]
        spline = terminal.constraints['Skirt manual wire']
        old_mute = spline.mute
        try:
            spline.mute = True
            rejection('disabled_manual_spline')
        finally:
            spline.mute = old_mute
        for attribute, changed in (('use_chain_offset', True), ('use_original_scale', False)):
            saved = getattr(spline, attribute)
            try:
                setattr(spline, attribute, changed)
                rejection('edited_manual_spline_' + attribute)
            finally:
                setattr(spline, attribute, saved)
        wire = bpy.data.objects[chain['curve']]
        hook = wire.modifiers[0]
        old_target = hook.subtarget
        try:
            hook.subtarget = record['controls']['hem']
            rejection('edited_exact_hook_target')
        finally:
            hook.subtarget = old_target
        selection = {constraint.name: constraint.active for constraint in terminal.constraints}
        extra = terminal.constraints.new('COPY_ROTATION')
        extra.name = 'Artist foreign relation'
        try:
            extra.target, extra.subtarget = rig, chain['def'][0]
            rejection('foreign_manual_constraint')
        finally:
            terminal.constraints.remove(extra)
            for constraint in terminal.constraints:
                constraint.active = selection[constraint.name]
        rotation = rig.pose.bones[chain['def'][0]].constraints['Skirt physics delta']
        for relation in (rig.pose.bones[chain['def'][0]].constraints['Skirt manual pose'], rotation):
            saved = relation.mute
            try:
                relation.mute = not saved
                rejection('edited_deform_mute_' + relation.name)
            finally:
                relation.mute = saved
        curve = next(curve for curve in rig.animation_data.drivers
                     if curve.data_path == rotation.path_from_id('influence'))
        binding = curve.driver.variables[0].targets[0]
        old_path = binding.data_path
        try:
            binding.data_path = rig.pose.bones['Hips'].path_from_id('rotation_euler') + '[0]'
            rejection('foreign_physics_driver_binding')
        finally:
            binding.data_path = old_path
        modifier = curve.modifiers.new('NOISE')
        try:
            rejection('foreign_physics_driver_modifier')
        finally:
            curve.modifiers.remove(modifier)
        corrupted = copy.deepcopy(package)
        index = next(index for index, bone in enumerate(corrupted['bones']) if bone['name'] == chain['def'][0])
        corrupted['frames'][-1]['poses'][index]['matrix'][3] += .01
        rejection('last_frame_independent_dress_motion', corrupted)
        try:
            controls.build_plan(rig, names)
            raise AssertionError('Dress delegation accepted without full packet evidence.')
        except ua.UnityAnimationError:
            checks.append('missing_packet_static_proof')
        final = controls.build_plan(rig, names, package=package)
        assert final == plan and block_names() == baseline_blocks and ua._rest_state(rig.data) == baseline_rest
        report.update(passed=True, rejected_tampering=checks, original_rest_unchanged=True,
                      owned_graph_restored_after_each_fixture=True, temporary_data_ids_unchanged=True)
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        report['source_file_unchanged'] = file_hash(input_path) == input_sha
        report['packet_file_unchanged'] = file_hash(args.package) == packet_sha
        report['passed'] = bool(report['passed'] and report['source_file_unchanged'] and report['packet_file_unchanged'])
        Path(args.report).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('UNITY_ANIMATION_DRESS_RESULT ' + json.dumps(report))
    if not report['passed']:
        raise AssertionError(report.get('error', 'Input files changed.'))


if __name__ == '__main__':
    main()
