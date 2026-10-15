"""Pure contract checks for frozen playback; native equality is separate.

Load the relevant production definitions through AST without bpy/mathutils or
addon registration. These checks cover sampler compilation, all-side-before-
publish behavior and failure fallback, not native geometry equality or speed.
"""

import ast
from array import array
from dataclasses import asdict, dataclass, fields, replace, MISSING
import importlib.util
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def namespace():
    profile_path = ROOT / 'addons/character_designer/forearm_twist_profile.py'
    spec = importlib.util.spec_from_file_location('_playback_profile_pure', profile_path)
    profile = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profile)
    path = ROOT / 'addons/character_designer/forearm_twist_playback_core.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = {'PlaybackBindingError', '_Side', 'Evaluation', '_require', '_pointer', '_identity',
             '_profile_samples', 'FrozenPlaybackBinding', 'ValidatedPlaybackTemplate',
             '_consume_template', 'discard_template', 'check_template'}
    subset = ast.Module(body=[node for node in tree.body
                             if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                             and node.name in names], type_ignores=[])
    result = {'profile': profile, 'dataclass': dataclass, 'asdict': asdict,
              'perf_counter': perf_counter, 'array': array, '_ISSUED_TEMPLATES': {}, '__name__': __name__}
    exec(compile(subset, str(path), 'exec'), result)
    return result


class FakeData:
    def __init__(self, fail=False):
        self.writes = []
        self.fail = fail

    def foreach_set(self, name, value):
        if self.fail:
            raise RuntimeError('injected native publish failure')
        self.writes.append((name, tuple(value)))


class FakeKey:
    def __init__(self, fail=False):
        self.data = FakeData(fail)
        self.value = 0.0
        self.mute = False


class FakeNative:
    def __init__(self, pointer, data=None):
        self.pointer = pointer
        self.data = data
        self.session_uid = pointer + 1000

    def as_pointer(self):
        return self.pointer


class PlaybackContractTests(unittest.TestCase):
    def setUp(self):
        self.scope = namespace()

    def test_bounded_and_legacy_sampler_compilation_retains_canonical_values(self):
        rings = [{'position': -.2, 'ratio': .1}, {'position': .3, 'ratio': .5},
                 {'position': 1.2, 'ratio': .9}]
        indices = [8, 3, 19, 2, 12]
        positions = [-.2, 0.0, .3, .7, 1.2]
        record = {'rings': rings, 'vertices': indices, 'positions': positions,
                  'range_start': 0, 'range_end': 2, 'transition': .17}
        sampler = self.scope['profile'].compile_sampler(rings, start=0, end=2, transition=.17)
        expected = tuple((index, *sampler(position)) for index, position in zip(indices, positions))
        self.assertEqual(self.scope['_profile_samples'](record), expected)
        legacy = {key: value for key, value in record.items()
                  if key not in {'range_start', 'range_end', 'transition'}}
        knots = [(0.0, 0.0), (.3, .5), (1.0, 1.0)]
        sampler = self.scope['profile'].compile_sampler(
            [{'position': position, 'ratio': ratio} for position, ratio in knots])
        self.assertEqual(self.scope['_profile_samples'](legacy),
                         tuple((index, *sampler(position)) for index, position in zip(indices, positions)))

    def test_continuous_profile_preserves_canonical_first_ring_gate(self):
        rings = [{'position': .1, 'ratio': .0}, {'position': .4, 'ratio': .3},
                 {'position': .8, 'ratio': 1.0}]
        record = {'rings': rings, 'vertices': [9, 7, 5, 3], 'positions': [.05, .1, .4, .9],
                  'range_start': 0, 'range_end': 2, 'distribution': 'WRIST_CONTINUOUS'}
        actual = self.scope['_profile_samples'](record)
        sampler = self.scope['profile'].compile_sampler(rings)
        expected = tuple((index, sampler(position)[0],
                          0.0 if position < .1 else sampler(position)[1])
                         for index, position in zip(record['vertices'], record['positions']))
        self.assertEqual(actual, expected)

    def test_mismatched_support_counts_are_rejected(self):
        record = {'rings': [{'position': 0.0, 'ratio': 0.0}, {'position': 1.0, 'ratio': 1.0}],
                  'vertices': [1, 2], 'positions': [.2]}
        with self.assertRaisesRegex(self.scope['PlaybackBindingError'], 'counts differ'):
            self.scope['_profile_samples'](record)

    def template_fixture(self):
        keys = FakeNative(5)
        mesh_data = FakeNative(2)
        mesh_data.shape_keys = keys
        mesh = FakeNative(1, mesh_data)
        rig_data = FakeNative(4)
        rig = FakeNative(3, rig_data)
        template_type = self.scope['ValidatedPlaybackTemplate']
        values = {field.name: () for field in fields(template_type) if field.default is MISSING}
        values.update(source_ids=tuple(self.scope['_identity'](item)
                                       for item in (mesh, mesh_data, rig, rig_data, keys)),
                      record_text='{}', counts=(6, 0, 0, 0), basis_name='Basis', basis_bytes=b'',
                      snapshot_sha256='pure-test')
        return template_type(**values), mesh, rig

    def test_template_type_or_copied_fields_do_not_forge_issuance(self):
        template, mesh, rig = self.template_fixture()
        with self.assertRaisesRegex(self.scope['PlaybackBindingError'], 'exact unused template'):
            self.scope['_consume_template'](template, mesh, rig)
        self.scope['_ISSUED_TEMPLATES'][id(template)] = template
        copied = replace(template)
        with self.assertRaisesRegex(self.scope['PlaybackBindingError'], 'exact unused template'):
            self.scope['_consume_template'](copied, mesh, rig)
        self.assertIs(self.scope['_ISSUED_TEMPLATES'].get(id(template)), template)

    def test_issued_template_is_consumed_once_and_checks_source_ids(self):
        template, mesh, rig = self.template_fixture()
        self.scope['_ISSUED_TEMPLATES'][id(template)] = template
        self.assertIs(self.scope['_consume_template'](template, mesh, rig), template)
        self.assertNotIn(id(template), self.scope['_ISSUED_TEMPLATES'])
        with self.assertRaisesRegex(self.scope['PlaybackBindingError'], 'exact unused template'):
            self.scope['_consume_template'](template, mesh, rig)
        self.scope['_ISSUED_TEMPLATES'][id(template)] = template
        wrong_rig = FakeNative(30, rig.data)
        with self.assertRaisesRegex(self.scope['PlaybackBindingError'], 'exclusion identities differ'):
            self.scope['_consume_template'](template, mesh, wrong_rig)
        self.assertNotIn(id(template), self.scope['_ISSUED_TEMPLATES'])

    def test_discard_unused_template_is_idempotent_and_has_no_native_mutation(self):
        template, mesh, rig = self.template_fixture()
        self.assertEqual(template.vertex_count, 6)
        self.scope['_ISSUED_TEMPLATES'][id(template)] = template
        self.scope['discard_template'](template)
        self.scope['discard_template'](template)
        self.assertFalse(self.scope['_ISSUED_TEMPLATES'])
        self.assertEqual(mesh.as_pointer(), 1)
        self.assertEqual(rig.as_pointer(), 3)

    def test_read_only_template_check_preserves_issuance_and_requires_both_ids(self):
        template, mesh, rig = self.template_fixture()
        self.scope['_ISSUED_TEMPLATES'][id(template)] = template
        self.assertIs(self.scope['check_template'](template, source_mesh=mesh, source_rig=rig), template)
        with self.assertRaisesRegex(self.scope['PlaybackBindingError'], 'both source exclusion'):
            self.scope['check_template'](template, source_mesh=mesh)
        self.assertIs(self.scope['_ISSUED_TEMPLATES'].get(id(template)), template)

    def fixture(self):
        base = self.scope['FrozenPlaybackBinding']

        class Binding(base):
            def _read_frame(self, graph):
                return ()

            def _guard(self, graph):
                self.guard_count += 1

            def _mute_private_outputs(self):
                for key in self.keys:
                    key.mute = True

        binding = Binding.__new__(Binding)
        binding.keys = [FakeKey(), FakeKey()]
        binding._failed = False
        binding._last_error = None
        binding.guard_count = 0
        binding._keys = SimpleNamespace(update_tag=lambda **kwargs: None)
        binding._mesh = SimpleNamespace(update=lambda: None)
        return binding

    def test_later_side_solve_failure_publishes_no_coordinates(self):
        binding = self.fixture()

        def solve():
            # A first side could already be fully computed; none may publish
            # until the remaining side has passed the canonical math checks.
            prepared_left = array('f', [1, 2, 3])
            self.assertEqual(len(prepared_left), 3)
            raise ValueError('later side invalid')

        binding._solve_frame = solve
        with self.assertRaisesRegex(ValueError, 'later side invalid'):
            binding.evaluate(None)
        self.assertTrue(binding._failed)
        self.assertTrue(all(key.mute for key in binding.keys))
        self.assertTrue(all(not key.data.writes for key in binding.keys))

    def test_native_publish_failure_mutes_both_private_outputs(self):
        binding = self.fixture()
        binding.keys[1].data.fail = True
        binding._solve_frame = lambda: ([(key, True, array('f', [1, 2, 3]))
                                        for key in binding.keys], 2)
        with self.assertRaisesRegex(RuntimeError, 'native publish failure'):
            binding.evaluate(None)
        self.assertTrue(binding._failed)
        self.assertTrue(all(key.mute for key in binding.keys))
        self.assertEqual(len(binding.keys[0].data.writes), 1)
        self.assertEqual(binding.guard_count, 1)

    def test_measure_only_evaluation_does_not_publish_or_update_keys(self):
        binding = self.fixture()
        binding._solve_frame = lambda: ([(key, True, array('f', [1, 2, 3]))
                                        for key in binding.keys], 2)
        result = binding.evaluate(None, publish=False)
        self.assertFalse(result['published'])
        self.assertEqual(result['key_count'], 2)
        self.assertEqual(result['corrected_vertex_count'], 2)
        self.assertTrue(all(not key.data.writes for key in binding.keys))
        self.assertEqual(binding.guard_count, 0)


if __name__ == '__main__':
    unittest.main()
