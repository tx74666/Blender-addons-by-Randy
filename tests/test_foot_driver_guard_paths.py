"""Differential tests for the same-call Foot Controls driver path guard.

No bpy import: execute the actual validation guard AST and compare its ordered
decisions with the previous guard. --baseline additionally authenticates that
reference against a frozen add-on source. No native validation is claimed.
"""
import argparse
import ast
import builtins
import hashlib
import json
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer/foot_controls.py'
BASELINE = None
REPORT = None
REFERENCE = '''
if armature.animation_data:
    owned_drivers = {(entry['path'], entry['index']) for entry in record['drivers']}
    for curve in armature.animation_data.drivers:
        if (_limb()._path_mentions_bone(curve.data_path, set(record['bones'].values()))
                and (curve.data_path, curve.array_index) not in owned_drivers):
            raise _error('Another driver writes to Foot Controls; keep that dependency intact.')
'''


def guard_node(source):
    tree = ast.parse(source.read_text(encoding='utf-8-sig'))
    validate = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'validate')
    return next(node for node in ast.walk(validate)
                if isinstance(node, ast.If) and ast.unparse(node.test) == 'armature.animation_data')


def execute(node, names, paths, owned, animated=True):
    events, reads = [], {'paths': 0, 'indices': []}
    state = {'index': None}

    class Curve:
        def __init__(self, index, path, array_index):
            self.index, self.path, self.index_value = index, path, array_index

        @property
        def data_path(self):
            reads['paths'] += 1
            return self.path

        @property
        def array_index(self):
            reads['indices'].append(self.index)
            if self.index_value is None:
                raise AssertionError('Irrelevant driver array_index must not be read')
            return self.index_value

    class Drivers:
        def __iter__(self):
            for index, (path, array_index) in enumerate(paths):
                state['index'] = index
                events.append(('visit', index))
                yield Curve(index, path, array_index)

    def decision(values):
        result = builtins.any(values)
        events.append(('mentions', state['index'], result))
        return result

    def previous_mentions(path, bone_names):
        # This deliberately retains the old quote-only escaping, including its
        # treatment of literal backslashes. It is the established predicate.
        return decision(f'pose.bones["{name.replace(chr(34), chr(92) + chr(34))}"]' in path
                        for name in bone_names)

    class GuardError(Exception):
        pass

    namespace = {'armature': SimpleNamespace(animation_data=SimpleNamespace(drivers=Drivers()) if animated else None),
                 'record': {'bones': dict(enumerate(names)),
                            'drivers': [{'path': path, 'index': index} for path, index in owned]},
                 '_limb': lambda: SimpleNamespace(_path_mentions_bone=previous_mentions),
                 '_error': GuardError, 'any': decision}
    outcome = None
    try:
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                     '<actual Foot Controls driver guard>', 'exec'), namespace)
    except GuardError as exc:
        outcome = (state['index'], str(exc))
    return {'events': events, 'array_reads': reads['indices'], 'error': outcome}, reads['paths']


class FootDriverGuardPaths(unittest.TestCase):
    def pair(self, names, paths, owned=(), animated=True):
        expected, old_reads = execute(ast.parse(REFERENCE).body[0], names, paths, owned, animated)
        actual, new_reads = execute(guard_node(SOURCE), names, paths, owned, animated)
        self.assertEqual(actual, expected)
        self.assertLessEqual(new_reads, old_reads)
        self.assertEqual(new_reads, sum(event[0] == 'visit' for event in actual['events']))
        return actual, old_reads, new_reads

    def test_reference_matches_frozen_baseline_when_supplied(self):
        if BASELINE is None:
            self.skipTest('Supply --baseline to authenticate the frozen reference')
        self.assertEqual(ast.dump(guard_node(BASELINE), include_attributes=False),
                         ast.dump(ast.parse(REFERENCE).body[0], include_attributes=False))

    def test_plain_owned_driver_reads_path_once(self):
        path = 'pose.bones["Toe.L"].rotation_euler'
        result, old, new = self.pair(['Toe.L'], [(path, 0)], [(path, 0)])
        self.assertIsNone(result['error'])
        self.assertEqual((old, new), (2, 1))

    def test_quoted_unicode_and_literal_backslash_names(self):
        cases = [('Toe"L', 'pose.bones["Toe\\"L"].rotation_euler'),
                 ('Toe\\L', 'pose.bones["Toe\\L"].rotation_euler'),
                 ('足趾🦶.L', 'pose.bones["足趾🦶.L"].rotation_euler'),
                 ('A\\"B', 'pose.bones["A\\\\"B"].rotation_euler')]
        for name, path in cases:
            with self.subTest(name=name):
                result, _, _ = self.pair([name], [(path, 2)])
                self.assertIsNotNone(result['error'])

    def test_escaped_backslash_near_miss_keeps_old_semantics(self):
        result, _, _ = self.pair(['Toe\\L'], [('pose.bones["Toe\\\\L"].rotation_euler', None)])
        self.assertIsNone(result['error'])
        self.assertEqual(result['array_reads'], [])

    def test_unrelated_paths_do_not_read_array_index(self):
        result, _, _ = self.pair(['Toe.L'], [('pose.bones["Other"].scale', None), ('location', None)])
        self.assertEqual(result['array_reads'], [])

    def test_duplicate_owned_paths_and_indices_remain_allowed_here(self):
        path = 'pose.bones["Toe.L"].rotation_euler'
        result, _, _ = self.pair(['Toe.L', 'Toe.L'], [(path, 0), (path, 0), (path, 1)],
                                 [(path, 0), (path, 0), (path, 1)])
        self.assertIsNone(result['error'])

    def test_same_path_wrong_array_index_rejected_in_original_order(self):
        path = 'pose.bones["Toe.L"].rotation_euler'
        result, _, _ = self.pair(['Toe.L'], [(path, 0), (path, 1), ('location', None)], [(path, 0)])
        self.assertEqual(result['error'][0], 1)
        self.assertEqual(result['array_reads'], [0, 1])
        self.assertEqual(sum(event[0] == 'visit' for event in result['events']), 2)

    def test_prefix_suffix_matching_is_unchanged(self):
        result, _, _ = self.pair(['Toe'], [('prefix.pose.bones["Toe"].scale.suffix', 0)])
        self.assertEqual(result['error'][0], 0)

    def test_empty_or_inactive_animation(self):
        for names, animated in (([], True), (['Toe.L'], True), (['Toe.L'], False)):
            with self.subTest(names=names, animated=animated):
                self.pair(names, [], animated=animated)
        self.pair([], [('location', None)])

    def test_deterministic_mixed_driver_sequences(self):
        rng = random.Random(20261021)
        names = ['Toe.L', '足趾.R', 'A"B', 'C\\D', 'E\\"F']
        paths = ['location', 'pose.bones["Other"].scale', 'pose.bones["Toe.L"].rotation_euler',
                 'pose.bones["足趾.R"].scale', 'pose.bones["A\\"B"].location',
                 'pose.bones["C\\D"].rotation_euler', 'pose.bones["E\\\\"F"].scale']
        for index in range(100):
            with self.subTest(sequence=index):
                selected = rng.sample(names, rng.randrange(len(names) + 1))
                drivers = [(rng.choice(paths), rng.randrange(3)) for _ in range(rng.randrange(18))]
                owned = [driver for driver in drivers if rng.randrange(2)]
                self.pair(selected, drivers, owned)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--report', type=Path)
    args, remaining = parser.parse_known_args()
    BASELINE = args.baseline
    if BASELINE and BASELINE.is_dir():
        BASELINE /= 'foot_controls.py'
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FootDriverGuardPaths)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if args.report:
        pins = {label: {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                for label, path in [('candidate', SOURCE), ('test', Path(__file__))]
                + ([('baseline', BASELINE)] if BASELINE else [])}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'schema': 'x.foot-driver-path-guard-differential/1',
            'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
            'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
            'pins': pins, 'blender_launched': False, 'native_validation_claimed': False}, indent=2) + '\n', encoding='utf8')
    sys.exit(not result.wasSuccessful())
