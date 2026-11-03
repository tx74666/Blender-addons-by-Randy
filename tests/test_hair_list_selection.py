"""List selection follows visible ranges and never widens malformed scope."""
import copy
import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_list_selection.py'
SPEC = importlib.util.spec_from_file_location('hair_list_selection_under_test', PATH)
selection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selection)

KEYS = ('left', 'middle', 'right', 'hidden')
VISIBLE = KEYS[:3]


class HairListSelectionTests(unittest.TestCase):
    def test_single_click_replaces_visible_and_hidden_scope(self):
        self.assertEqual(selection.plan(KEYS, VISIBLE, ('left', 'hidden'), 'right', 'left'),
                         (('right',), 'right'))

    def test_ctrl_adds_and_removes_one_row_and_retains_hidden_scope(self):
        self.assertEqual(selection.plan(KEYS, VISIBLE, ('hidden',), 'middle', toggle=True),
                         (('middle', 'hidden'), 'middle'))
        self.assertEqual(selection.plan(KEYS, VISIBLE, ('middle', 'hidden'), 'middle', toggle=True),
                         (('hidden',), 'middle'))

    def test_ctrl_can_remove_the_final_row_without_selecting_all(self):
        self.assertEqual(selection.plan(KEYS, VISIBLE, ('middle',), 'middle', toggle=True),
                         ((), 'middle'))

    def test_shift_selects_inclusive_forward_and_reverse_ranges(self):
        for anchor, target in (('left', 'right'), ('right', 'left')):
            with self.subTest(anchor=anchor):
                self.assertEqual(selection.plan(KEYS, VISIBLE, ('hidden',), target, anchor, extend=True),
                                 (VISIBLE, anchor))

    def test_shift_keeps_its_anchor_for_successive_range_clicks(self):
        selected, anchor = selection.plan(KEYS, VISIBLE, (), 'right', 'left', extend=True)
        self.assertEqual(selection.plan(KEYS, VISIBLE, selected, 'middle', anchor, extend=True),
                         (('left', 'middle'), 'left'))

    def test_ctrl_shift_adds_a_range_without_toggling_existing_rows_off(self):
        self.assertEqual(selection.plan(KEYS, VISIBLE, ('middle', 'hidden'), 'right', 'left',
                                        extend=True, toggle=True),
                         (KEYS, 'left'))

    def test_range_follows_visible_order_and_skips_filtered_rows(self):
        visible = ('right', 'left')
        self.assertEqual(selection.plan(KEYS, visible, ('middle',), 'left', 'right', extend=True),
                         (('left', 'right'), 'right'))

    def test_absent_or_filtered_anchor_starts_at_target(self):
        for anchor in ('', 'hidden', 'removed-entry'):
            with self.subTest(anchor=anchor):
                self.assertEqual(selection.plan(KEYS, VISIBLE, ('hidden',), 'middle', anchor,
                                                extend=True), (('middle',), 'middle'))
                self.assertEqual(selection.plan(KEYS, VISIBLE, ('hidden',), 'middle', anchor,
                                                extend=True, toggle=True),
                                 (('middle', 'hidden'), 'middle'))

    def test_invalid_inventory_scope_target_and_modifiers_are_refused(self):
        cases = (
            dict(keys=('left', 'left')),
            dict(keys=('left', None)),
            dict(keys='left'),
            dict(keys=None),
            dict(keys={'left', 'middle'}),
            dict(visible_keys=('left', 'left')),
            dict(visible_keys=('unknown',)),
            dict(visible_keys='left'),
            dict(selected_keys=('left', 'left')),
            dict(selected_keys=('unknown',)),
            dict(selected_keys={'left': True}),
            dict(selected_keys=(' ',)),
            dict(target_key='hidden'),
            dict(target_key=''),
            dict(target_key=None),
            dict(anchor_key=None),
            dict(anchor_key=' '),
            dict(extend=1),
            dict(toggle='False'),
        )
        valid = dict(keys=KEYS, visible_keys=VISIBLE, selected_keys=(), target_key='middle')
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                selection.plan(**(valid | changed))

    def test_unicode_identity_order_and_input_values_are_preserved(self):
        keys = ['右侧 "A"', '左侧', 'hidden']
        visible = keys[:2]
        selected = ['hidden']
        before = copy.deepcopy((keys, visible, selected))
        self.assertEqual(selection.plan(keys, visible, selected, '左侧', toggle=True),
                         (('左侧', 'hidden'), '左侧'))
        self.assertEqual((keys, visible, selected), before)


if __name__ == '__main__':
    unittest.main()
