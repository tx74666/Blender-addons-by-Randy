"""Control-bake barriers retain evaluated parent reads without extra updates."""

import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_animation_controls.py'
TREE = ast.parse(SOURCE.read_text(encoding='utf-8'))
HELPER = next(node for node in TREE.body
              if isinstance(node, ast.FunctionDef) and node.name == '_post_redirect_updates')
NAMESPACE = {}
exec(compile(ast.Module(body=[HELPER], type_ignores=[]), str(SOURCE), 'exec'), NAMESPACE)
schedule = NAMESPACE['_post_redirect_updates']


def fixture(names, *, redirects=(), parents=None, controls=(), mapped=None):
    parents = parents or {}
    all_names = set(names) | set(parents.values())
    bones = {name: NS(name=name, parent=None) for name in all_names}
    for name, parent in parents.items():
        bones[name].parent = bones[parent]
    target = NS(data=NS(bones=bones))
    plan = NS(redirects={name: 'control_' + name for name in redirects},
              mapped_names=frozenset(names if mapped is None else mapped),
              control_names=frozenset(controls))
    return target, plan


class BakeBarrierTests(unittest.TestCase):
    def test_final_barrier_and_next_redirect_preupdate_cover_writes(self):
        order = ['redirect_a', 'native', 'redirect_b']
        target, plan = fixture(order, redirects=['redirect_a', 'redirect_b'],
                               parents={'native': 'redirect_a'})
        self.assertEqual(schedule(target, plan, order), frozenset())

    def test_control_parent_reader_keeps_only_the_preceding_redirect_barrier(self):
        order = ['redirect_a', 'reader_a', 'redirect_b', 'reader_b', 'redirect_c']
        target, plan = fixture(order, redirects=['redirect_a', 'redirect_b', 'redirect_c'],
            parents={'reader_a': 'helper_a', 'reader_b': 'helper_b'},
            controls=['helper_a', 'helper_b'])
        self.assertEqual(schedule(target, plan, order), {'redirect_a', 'redirect_b'})

    def test_mapped_parent_uses_desired_matrix_even_when_also_a_control(self):
        order = ['redirect', 'reader']
        target, plan = fixture(order, redirects=['redirect'],
            parents={'reader': 'mapped_helper'}, controls=['mapped_helper'],
            mapped=[*order, 'mapped_helper'])
        self.assertEqual(schedule(target, plan, order), frozenset())

    def test_reader_after_last_redirect_retains_that_immediate_update(self):
        order = ['redirect', 'reader', 'leaf']
        target, plan = fixture(order, redirects=['redirect'],
            parents={'reader': 'helper', 'leaf': 'reader'}, controls=['helper'])
        self.assertEqual(schedule(target, plan, order), {'redirect'})

    def test_reader_before_first_redirect_cannot_add_a_new_update(self):
        order = ['reader', 'redirect']
        target, plan = fixture(order, redirects=['redirect'],
                               parents={'reader': 'helper'}, controls=['helper'])
        self.assertEqual(schedule(target, plan, order), frozenset())

    def test_parentless_and_adjacent_redirects_need_no_postupdate(self):
        order = ['redirect_a', 'redirect_b', 'root']
        target, plan = fixture(order, redirects=['redirect_a', 'redirect_b'])
        self.assertEqual(schedule(target, plan, order), frozenset())

    def test_parent_change_is_read_fresh_on_the_next_bake(self):
        order = ['redirect', 'reader', 'native_parent']
        target, plan = fixture(order, redirects=['redirect'],
                               parents={'reader': 'helper'}, controls=['helper'])
        self.assertEqual(schedule(target, plan, order), {'redirect'})
        target.data.bones['reader'].parent = target.data.bones['native_parent']
        self.assertEqual(schedule(target, plan, order), frozenset())
        self.assertEqual(order, ['redirect', 'reader', 'native_parent'])


if __name__ == '__main__':
    unittest.main()
