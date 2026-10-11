"""Lightweight policy checks for persisted Quick Bind scope after rig additions."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/quick_bind.py'
tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
namespace = {'RIG_KEY': 'owner', 'QuickBindError': ValueError}
definitions = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                              and n.name in {'_has_group_weights', '_rebind_scope_names'}], type_ignores=[])
exec(compile(definitions, str(SOURCE), 'exec'), namespace)
scope_names = namespace['_rebind_scope_names']


class Mesh(dict):
    def __init__(self, rig, weights=()):
        super().__init__(owner=rig)
        self.name = 'Mesh'
        self.vertex_groups = [SimpleNamespace(index=i, name=n) for i, (n, _) in enumerate(weights)]
        self.data = SimpleNamespace(vertices=[SimpleNamespace(groups=[SimpleNamespace(group=i, weight=w)
                                                                       for i, (_, w) in enumerate(weights)])])


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.rig = object()
        self.target = Mesh(self.rig, [('A', .8), ('Hair', 0.0)])
        self.body = Mesh(self.rig, [('A', 1.0)])
        self.record = {'names': ['A', 'B']}

    def call(self, *, target=None, rig=None, names=('A', 'B', 'Hair', 'Skirt'), mode='TRANSFER'):
        return scope_names(target if target is not None else self.target,
                           self.rig if rig is None else rig, self.record, names, body=self.body, mode=mode)

    def test_unweighted_accessory_additions_keep_original_owned_scope(self):
        self.assertEqual(self.call(), ('A', 'B'))
        self.assertEqual(self.record, {'names': ['A', 'B']})
        self.assertEqual(self.target.data.vertices[0].groups[0].weight, .8)

    def test_no_additions_keep_current_order(self):
        self.assertEqual(self.call(names=('B', 'A')), ('B', 'A'))

    def test_weighted_addition_on_target_refused(self):
        with self.assertRaisesRegex(ValueError, 'outside the Previous Weights backup'):
            self.call(target=Mesh(self.rig, [('A', .7), ('Hair', .3)]))

    def test_weighted_addition_on_transfer_body_refused(self):
        self.body = Mesh(self.rig, [('A', .5), ('Skirt', .5)])
        with self.assertRaisesRegex(ValueError, 'outside the Previous Weights backup'):
            self.call()

    def test_automatic_weights_do_not_sample_body(self):
        self.body = Mesh(self.rig, [('Skirt', 1.0)])
        self.assertEqual(self.call(mode='AUTO'), ('A', 'B'))

    def test_foreign_rig_removed_or_renamed_saved_bone_refused(self):
        for rig, names in ((object(), ('A', 'B', 'Hair')), (self.rig, ('A', 'Hair')),
                           (self.rig, ('A', 'Renamed_B', 'Hair'))):
            with self.subTest(names=names), self.assertRaisesRegex(ValueError, 'saved deform bones changed'):
                self.call(rig=rig, names=names)


if __name__ == '__main__':
    unittest.main()
