"""Selection scope must be explicit, deterministic and never widen silently."""
import copy
import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_resegment_selection.py'
SPEC = importlib.util.spec_from_file_location('hair_selection_under_test', PATH)
selection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selection)


def chains():
    return [
        dict(signature='left', bones=['Hair L 01', 'Hair L 02', 'Hair L 03'], count=3),
        dict(signature='right', bones=['Hair R 01', 'Hair R 02', 'Hair R 03'], count=3),
        dict(signature='asymmetric', bones=['侧束 "A" 01', '侧束 "A" 02'], count=2),
    ]


class HairStrandSelectionTests(unittest.TestCase):
    def test_any_one_bone_resolves_its_whole_strand(self):
        for name in chains()[0]['bones']:
            with self.subTest(name=name):
                self.assertEqual(selection.resolve_names(chains(), [name]), ('left',))

    def test_many_bones_from_one_strand_do_not_duplicate_target(self):
        self.assertEqual(selection.resolve_names(chains(),
            ['Hair L 03', 'Hair L 01', 'Hair L 03']), ('left',))

    def test_explicit_pair_is_record_ordered_not_selection_order(self):
        self.assertEqual(selection.resolve_names(chains(),
            ['Hair R 02', 'Hair L 03', 'Hair R 01']), ('left', 'right'))

    def test_single_side_does_not_guess_or_include_mirror_partner(self):
        self.assertEqual(selection.resolve_names(chains(), ['Hair R 01']), ('right',))

    def test_asymmetric_unicode_strand_remains_independent(self):
        self.assertEqual(selection.resolve_names(chains(), ['侧束 "A" 02']), ('asymmetric',))

    def test_empty_nonhair_or_mixed_selection_never_falls_back_to_all(self):
        for names in ([], (), ['Head'], ['Hair L 01', 'Other source Hair'], 'Hair L 01', None):
            with self.subTest(names=names), self.assertRaises(selection.HairSelectionError):
                selection.resolve_names(chains(), names)

    def test_duplicate_signature_or_bone_inventory_is_ambiguous(self):
        cases = []
        duplicate_signature = chains()
        duplicate_signature[1]['signature'] = 'left'
        cases.append(duplicate_signature)
        duplicate_name = chains()
        duplicate_name[1]['bones'][0] = 'Hair L 01'
        cases.append(duplicate_name)
        invalid_count = chains()
        invalid_count[0]['count'] = True
        cases.append(invalid_count)
        mismatch_count = chains()
        mismatch_count[0]['count'] = 7
        cases.append(mismatch_count)
        cases.append([])
        for records in cases:
            with self.subTest(records=records), self.assertRaises(selection.HairSelectionError):
                selection.resolve_names(records, ['Hair L 01'])

    def test_summary_reports_mixed_current_counts_without_changing_input(self):
        records = chains()
        before = copy.deepcopy(records)
        summary = selection.summarize(records, ('asymmetric', 'left'))
        self.assertEqual(summary['strands'], 2)
        self.assertEqual(summary['current_counts'], (2, 3))
        self.assertEqual(tuple(row['signature'] for row in summary['chains']), ('left', 'asymmetric'))
        self.assertEqual(records, before)

    def test_all_is_explicit_and_stale_or_duplicate_targets_are_refused(self):
        self.assertEqual(selection.summarize(chains())['strands'], 3)
        for signatures in ((), ['missing'], ['left', 'missing'], ['left', 'left'], 'left', None):
            if signatures is None:
                continue  # None is the explicit all-scope API.
            with self.subTest(signatures=signatures), self.assertRaises(selection.HairSelectionError):
                selection.summarize(chains(), signatures)


if __name__ == '__main__':
    unittest.main()
