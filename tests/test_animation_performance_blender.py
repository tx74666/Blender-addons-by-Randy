"""Bounded numerical/count checks and a descriptive timing sample, in factory Blender.

blender --background --factory-startup --threads 2 --python-exit-code 1 \
    --python tests/test_animation_performance_blender.py

No render, subprocess, real character, or persistent cache. Timing is reported,
never used as a pass threshold. Existing skin/controls/rollback suites remain
required: this fixture checks the new sample-preparation boundary specifically.
"""

from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_unity_animation_blender as native

ua = native.ua


def main():
    native.reset()
    rig = native.make_rig()
    before = native.rest_hash(rig), ua._snapshot(bpy.context, rig)
    with tempfile.TemporaryDirectory(prefix='animation-performance-native-') as folder:
        path, expected, _raw = native.package(rig, folder)
        data = ua.load_package(path)
        mapping = ua._mapping(rig, data, 1.0)
        bone_count = len(mapping.indices)
        rest_ids = {id(bone['rest']) for bone in data['bones']}
        counts = Counter()
        parse_matrix = ua._matrix

        def counted_matrix(values, name):
            counts['rest' if id(values) in rest_ids else 'motion'] += 1
            return parse_matrix(values, name)

        with patch.object(ua, '_matrix', side_effect=counted_matrix):
            prepared = list(ua._world_samples(data, mapping))
        assert counts['rest'] == bone_count, counts
        assert counts['motion'] == bone_count * len(data['frames']), counts
        reference = [ua.expected_world_matrices(rig, data, index, mapping=mapping)
                     for index in range(len(data['frames']))]
        maximum = max(native.max_matrix_error(actual[name], wanted[name])
                      for actual, wanted in zip(prepared, reference) for name in mapping.indices)
        assert maximum < 1e-7, maximum
        assert (native.rest_hash(rig), ua._snapshot(bpy.context, rig)) == before

        # A later bad sample still reaches the original finite-matrix validator.
        values = data['frames'][-1]['poses'][0]['matrix']
        old = values[0]
        values[0] = float('nan')
        try:
            list(ua._world_samples(data, mapping))
        except ua.UnityAnimationError:
            pass
        else:
            raise AssertionError('The optimized path skipped a non-finite motion sample.')
        finally:
            values[0] = old

        # Include fractional timing, Euler/axis-angle bones and actual evaluated
        # output. The importer must not fall back to its reference oracle.
        with patch.object(ua, 'expected_world_matrices', side_effect=AssertionError('Reference used in bake')):
            result = ua._import_package_action(bpy.context, rig, data, start_frame=7.25)
        maximum_output = 0.0
        for index, frame in enumerate(data['frames']):
            ua._set_frame(bpy.context.scene, 7.25 + frame['time'] * 24 / 1.001)
            bpy.context.view_layer.update()
            evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
            for name, desired in expected[index].items():
                maximum_output = max(maximum_output,
                    native.max_matrix_error(rig.matrix_world @ evaluated.pose.bones[name].matrix, desired))
        assert maximum_output < 5e-5, maximum_output
        assert result.action[ua.PACKAGE_HASH_KEY] == data['_sha256']
        ua.restore_preview(bpy.context, rig)
        assert (native.rest_hash(rig), ua._snapshot(bpy.context, rig)) == before

        # Time an identical, bounded 240-sample matrix workload, excluding parse
        # and setup. This describes the kernel only, not end-to-end import speed.
        repeated = {**data, 'frames': data['frames'] * 60}
        started = perf_counter()
        old_result = [ua.expected_world_matrices(rig, repeated, i, mapping=mapping)
                      for i in range(len(repeated['frames']))]
        reference_seconds = perf_counter() - started
        started = perf_counter()
        new_result = list(ua._world_samples(repeated, mapping))
        prepared_seconds = perf_counter() - started
        assert all(native.max_matrix_error(actual[name], wanted[name]) < 1e-7
                   for actual, wanted in zip(new_result, old_result) for name in mapping.indices)
        report = {'passed': True, 'bones': bone_count, 'samples': len(repeated['frames']),
            'rest_validations_per_import': counts['rest'], 'maximum_reference_error': maximum,
            'maximum_evaluated_error': maximum_output, 'reference_seconds': reference_seconds,
            'prepared_seconds': prepared_seconds, 'invalid_motion_rejected': True,
            'state_preserved': True, 'timing_scope': 'matrix kernel only; descriptive, no speed threshold'}
        print('ANIMATION_PERFORMANCE_RESULT ' + json.dumps(report))


if __name__ == '__main__':
    main()
