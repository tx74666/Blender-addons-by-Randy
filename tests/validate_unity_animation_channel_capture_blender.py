"""Tiny factory-only native check; run with the trusted isolated launcher.

No artist file, addon registration, build_plan, Action creation, save or export.
Four bones, four samples, six Euler modes. Run only after the owning chat checks
current resources; this file never launches Blender itself.
"""

import argparse
import hashlib
import importlib.util
import importlib
import json
from pathlib import Path
import sys
from time import perf_counter, process_time
import traceback
from types import ModuleType
from unittest.mock import patch
import uuid

import bpy
from mathutils import Matrix, Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    assert bpy.app.background and not bpy.data.filepath, 'Use a fresh factory background process.'
    assert bpy.context.mode == 'OBJECT', 'Factory startup must remain in Object Mode.'
    report = {'schema': 'cd.channel-capture-tiny-native/1', 'passed': False,
              'blender': bpy.app.version_string, 'bones': 4, 'samples_per_bake': 4,
              'source_sha256': hashlib.sha256((SOURCE / 'unity_animation_controls.py').read_bytes()).hexdigest(),
              'scope': 'Factory-only private four-bone channel comparison; no artist/control graph/FPS claim.',
              'artist_loaded': False, 'scene_saved': False, 'preferences_saved': False, 'cases': []}
    helper_spec = importlib.util.spec_from_file_location(
        '_capture_reference_helper', ROOT / 'tests/test_unity_animation_channel_capture.py')
    helper = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper)
    package_name = '_tiny_channel_capture_' + uuid.uuid4().hex
    package = ModuleType(package_name)
    package.__path__ = [str(SOURCE)]
    sys.modules[package_name] = package
    controls = importlib.import_module(package_name + '.unity_animation_controls')
    reference = helper.reference_capture(controls, Matrix)
    report['approved_module_sha256'] = helper.APPROVED_MODULE_SHA256
    report['approved_capture_sha256'] = helper.APPROVED_CAPTURE_SHA256
    armature, target = None, None
    try:
        armature = bpy.data.armatures.new('Channel capture fixture')
        target = bpy.data.objects.new('Channel capture fixture', armature)
        bpy.context.scene.collection.objects.link(target)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        target.select_set(True)
        bpy.context.view_layer.objects.active = target
        bpy.ops.object.mode_set(mode='EDIT')
        for index, name in enumerate(('Quaternion', 'Euler', 'Axis angle', '切替')):
            bone = armature.edit_bones.new(name)
            bone.head, bone.tail = (index, 0, 0), (index, 1, 0)
        connected = armature.edit_bones['Euler']
        connected.parent = armature.edit_bones['Quaternion']
        connected.head, connected.tail, connected.use_connect = (0, 1, 0), (0, 2, 0), True
        bpy.ops.object.mode_set(mode='OBJECT')
        target.pose.bones['Quaternion'].rotation_mode = 'QUATERNION'
        target.pose.bones['Axis angle'].rotation_mode = 'AXIS_ANGLE'
        target.pose.bones['切替'].rotation_mode = 'ZYX'
        switch = '切替"\\値'
        target.pose.bones['切替'][switch] = 0.75
        names = tuple(bone.name for bone in target.data.bones)
        path = target.pose.bones['Quaternion'].path_from_id('scale')
        plan = controls.ControlPlan(frozenset(names), frozenset(), frozenset(),
            frozenset({(path, 0)}), {}, {('切替', switch): 0.75}, frozenset(), {})
        context = bpy.context

        def authored_state():
            return (tuple((pb.name, pb.rotation_mode, tuple(value for row in pb.matrix_basis for value in row))
                          for pb in target.pose.bones),
                    tuple((bone.name, bone.use_connect,
                           tuple(value for row in bone.matrix_local for value in row))
                          for bone in target.data.bones),
                    target.pose.bones['切替'][switch], context.scene.frame_current)

        def samples():
            result = []
            for sample in range(4):
                desired = {}
                for index, name in enumerate(names):
                    bone = target.data.bones[name]
                    location = Vector((0.012 * sample, -0.003 * index, 0.004 * sample))
                    rotation = Quaternion(Vector((1 + index, 2, 3)).normalized(), 0.12 * sample)
                    basis = Matrix.LocRotScale(location, rotation, Vector((1, 1, 1)))
                    parent = bone.parent
                    kwargs = ({'parent_matrix': desired[parent.name],
                               'parent_matrix_local': parent.matrix_local} if parent else {})
                    desired[name] = bone.convert_local_to_pose(basis, bone.matrix_local, **kwargs)
                result.append(desired)
            return result

        def old_capture(*args, **_options):
            return reference(*args)

        for mode in ('XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'):
            target.pose.bones['Euler'].rotation_mode = mode
            before = authored_state()
            block_counts = (len(bpy.data.objects), len(bpy.data.armatures), len(bpy.data.actions))
            desired = samples()
            with patch.object(controls, '_capture_channels', old_capture):
                old = controls._bake_channels(context, target, plan, desired)
            new = controls._bake_channels(context, target, plan, desired)
            assert old.channels == new.channels, ('channel mismatch', mode)
            assert old.needs_joint_translation == new.needs_joint_translation is True
            assert old.maximum_error == new.maximum_error
            assert old.sample_count == new.sample_count == 4
            assert old.keyed_bones == new.keyed_bones
            assert (path, 0) not in new.channels
            assert tuple(new.channels) == tuple(old.channels)
            assert before == authored_state(), ('author change', mode)
            assert block_counts == (len(bpy.data.objects), len(bpy.data.armatures), len(bpy.data.actions))
            report['cases'].append({'mode': mode, 'channels': len(new.channels),
                                    'maximum_error': new.maximum_error, 'exact': True})

        blocks = (len(bpy.data.objects), len(bpy.data.armatures), len(bpy.data.actions))
        before = authored_state()
        bad = samples()
        bad[1][names[0]][0][0] = float('nan')
        try:
            controls._bake_channels(context, target, plan, bad)
            raise AssertionError('Non-finite sample accepted.')
        except ValueError as exc:
            assert 'non-finite' in str(exc)
        assert blocks == (len(bpy.data.objects), len(bpy.data.armatures), len(bpy.data.actions))
        assert before == authored_state()
        report['failure_cleanup_exact'] = True
        # Read-only capture kernel only. Each candidate round includes compile.
        # Stop within one second instead of extending a slow shared-machine run.
        measurements, previous_outputs = [], {}
        deadline = perf_counter() + 1.0
        for round_index in range(4):
            order = ('old', 'new') if round_index % 2 == 0 else ('new', 'old')
            for variant in order:
                wall, cpu = perf_counter(), process_time()
                metadata = (controls._compile_channel_capture(target, plan, names)
                            if variant == 'new' else None)
                channels, previous_q, previous_e = {}, {}, {}
                completed = 0
                for _sample in range(100):
                    if perf_counter() >= deadline:
                        break
                    if variant == 'new':
                        controls._capture_channels(target, target, plan, names, channels, previous_q,
                                                   previous_e, capture_plan=metadata)
                    else:
                        reference(target, target, plan, names, channels, previous_q, previous_e)
                    completed += 1
                measurements.append({'variant': variant, 'round': round_index, 'samples': completed,
                                     'wall_seconds': perf_counter() - wall,
                                     'process_seconds': process_time() - cpu})
                if completed == 100:
                    if previous_outputs:
                        assert channels == next(iter(previous_outputs.values()))
                    previous_outputs[variant] = channels
                else:
                    break
            if completed < 100:
                break
        report['capture_only_measurement'] = {
            'scope': 'Four fixed native identity poses; excludes dependency graph, Action/packet I/O and import.',
            'candidate_compile_included': True, 'budget_seconds': 1.0,
            'completed': len(measurements) == 8 and all(row['samples'] == 100 for row in measurements),
            'complete_outputs_exact': set(previous_outputs) == {'old', 'new'}, 'runs': measurements}
        assert before == authored_state()
        report['passed'] = True
    except Exception:
        report['error'] = traceback.format_exc()
        raise
    finally:
        if target is not None:
            if target.mode != 'OBJECT':
                bpy.context.view_layer.objects.active = target
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.data.objects.remove(target, do_unlink=True)
        if armature is not None and armature.users == 0:
            bpy.data.armatures.remove(armature)
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(package_name + '.'):
                sys.modules.pop(name, None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'passed': report['passed'], 'output': str(args.output)}, ensure_ascii=False))
    if not report['passed']:
        raise RuntimeError('Tiny channel-capture validation failed.')


if __name__ == '__main__':
    main()
