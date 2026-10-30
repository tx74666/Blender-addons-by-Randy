"""Pure regional Pose dependency and zero-translation connection proofs."""
import ast
from copy import deepcopy
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/control_pose_assets.py'
tree = ast.parse(SOURCE.read_text(encoding='utf8'))
namespace = {'math': math}
exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                            and node.name in {'_pose_rest_scope', '_connected_pose_compatible'}],
                        type_ignores=[]), str(SOURCE), 'exec'), namespace)
scope = namespace['_pose_rest_scope']
connected = namespace['_connected_pose_compatible']


class RestScope(unittest.TestCase):
    def setUp(self):
        self.rest = {'root': {'parent': None}, 'hand': {'parent': 'root'},
                     'finger': {'parent': 'hand'}, 'neck': {'parent': 'root'}}

    def test_keyed_region_and_ancestors_exclude_siblings(self):
        self.assertEqual(scope(self.rest, self.rest, {'finger': {}}), {'root', 'hand', 'finger'})

    def test_both_parent_chains_remain_in_scope(self):
        current = deepcopy(self.rest)
        current['finger']['parent'] = 'neck'
        self.assertEqual(scope(self.rest, current, ['finger']), set(self.rest))

    def test_missing_parent_and_cycle_refuse(self):
        for parent in ('missing', 'finger'):
            with self.subTest(parent=parent):
                current = deepcopy(self.rest)
                current['hand']['parent'] = parent
                with self.assertRaisesRegex(ValueError, 'invalid native parent chain'):
                    scope(self.rest, current, ['finger'])


class ConnectedTranslation(unittest.TestCase):
    def setUp(self):
        self.rig = SimpleNamespace(pose=SimpleNamespace(bones={'finger': SimpleNamespace(location=(0., 0., 0.))}))
        self.before, self.after = {'parent': 'hand'}, {'parent': 'hand'}

    def check(self, values):
        return connected(self.rig, 'finger', values, self.before, self.after)

    def test_complete_zero_locations_and_float_capture_noise_are_safe(self):
        for values in ((0., 0., 0.), (2e-8, -8e-8, 0.)):
            self.assertTrue(self.check({'finger': {'location': dict(enumerate(values))}}))

    def test_partial_missing_or_nonzero_location_refuses(self):
        for entries in ({}, {0: 0., 1: 0.}, {0: 0., 1: .002, 2: 0.},
                        {0: 0., 1: float('nan'), 2: 0.}, {0: False, 1: 0., 2: 0.}):
            with self.subTest(entries=entries):
                self.assertFalse(self.check({'finger': {'location': entries}}))

    def test_unkeyed_ancestor_must_keep_zero_current_translation(self):
        self.assertTrue(self.check({}))
        self.rig.pose.bones['finger'].location = (.002, 0., 0.)
        self.assertFalse(self.check({}))

    def test_root_or_names_without_channel_proof_refuse(self):
        self.assertFalse(self.check(['finger']))
        self.before['parent'] = None
        self.assertFalse(self.check({'finger': {'location': {0: 0., 1: 0., 2: 0.}}}))


if __name__ == '__main__':
    unittest.main()
