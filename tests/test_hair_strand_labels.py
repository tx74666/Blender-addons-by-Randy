"""Display ordinals never rename, merge or address physical Hair data."""
import copy
import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_strand_labels.py'
SPEC = importlib.util.spec_from_file_location('hair_strand_labels_under_test', PATH)
labels = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(labels)


def entry(key, name, **extra):
    return dict(key=key, bone_label=name, **extra)


class HairStrandLabelsTests(unittest.TestCase):
    def test_generated_names_use_contiguous_logical_numbers_without_gaps(self):
        source = [entry('pair:first', 'Hair Hair 03.01.L'),
                  entry('pair:second', 'Hair Hair 09.01.R'),
                  entry('strand:last', 'Hair 012 Segment 01')]
        result = labels.logical_labels(source)
        self.assertEqual(tuple(row['key'] for row in result), tuple(row['key'] for row in source))
        self.assertEqual(tuple(row['number'] for row in result), (1, 2, 3))
        self.assertEqual(tuple(row['label'] for row in result), ('Hair 1', 'Hair 2', 'Hair 3'))

    def test_generated_source_labels_and_native_side_suffixes_are_display_only(self):
        names = ('Hair Hair 12.01', 'Hair Long Braid 09.01.L',
                 'Hair 002 Segment 01.R', 'Hair 003 Segment 01.C', 'Hair 髪 08.01')
        result = labels.logical_labels([entry(str(index), name) for index, name in enumerate(names)])
        self.assertEqual(tuple(row['label'] for row in result),
                         tuple('Hair ' + str(index) for index in range(1, len(names) + 1)))

    def test_paired_entry_has_one_number_and_preserves_every_member(self):
        source = [entry('pair:left-right', 'Hair Hair 05.01.L', member_count=2,
                        signatures=('left', 'right'), members=[{'bone': 'Hair Hair 05.01.L'},
                                                             {'bone': 'Hair Hair 05.01.R'}]),
                  entry('strand:center', 'Hair Hair 09.01.C', member_count=1)]
        before = copy.deepcopy(source)
        result = labels.logical_labels(source)
        self.assertEqual(tuple(row['number'] for row in result), (1, 2))
        self.assertEqual(result[0]['key'], 'pair:left-right')
        self.assertEqual(source, before)

    def test_custom_labels_keep_verbatim_unicode_and_spacing_after_ordinal(self):
        names = ('Side braid', '髪束 "A"', 'Hair Clip 12', '  Artist braid  ')
        result = labels.logical_labels([entry(str(index), name) for index, name in enumerate(names)])
        self.assertEqual(tuple(row['label'] for row in result),
                         tuple(str(index) + ' · ' + name for index, name in enumerate(names, 1)))

    def test_equal_names_with_different_identities_are_not_dropped(self):
        source = [entry('strand:first', 'Hair Hair 09.01'),
                  entry('strand:second', 'Hair Hair 09.01')]
        result = labels.logical_labels(source)
        self.assertEqual(len(result), 2)
        self.assertEqual(tuple(row['key'] for row in result), ('strand:first', 'strand:second'))
        self.assertEqual(tuple(row['label'] for row in result), ('Hair 1', 'Hair 2'))

    def test_complete_list_mapping_keeps_numbers_stable_when_filtering(self):
        source = [entry('strand:first', 'Hair Hair 09.01'),
                  entry('strand:middle', 'Hair Hair 03.01'),
                  entry('strand:last', 'Hair Hair 12.01')]
        mapping = {row['key']: row['label'] for row in labels.logical_labels(source)}
        visible_keys = ('strand:first', 'strand:last')
        self.assertEqual(tuple(mapping[key] for key in visible_keys), ('Hair 1', 'Hair 3'))

    def test_labels_follow_existing_logical_order_instead_of_old_numeric_suffix(self):
        source = [entry('strand:old-twenty', 'Hair Hair 20.01', order=2),
                  entry('strand:old-three', 'Hair Hair 03.01', order=7)]
        result = labels.logical_labels(source)
        self.assertEqual(tuple(row['key'] for row in result),
                         ('strand:old-twenty', 'strand:old-three'))
        self.assertEqual(tuple(row['label'] for row in result), ('Hair 1', 'Hair 2'))

    def test_empty_input_stays_empty_and_result_is_detached(self):
        self.assertEqual(labels.logical_labels([]), ())
        source = [entry('strand:one', 'Hair Hair 03.01')]
        result = labels.logical_labels(source)
        result[0]['key'] = 'changed-result'
        self.assertEqual(source[0]['key'], 'strand:one')

    def test_duplicate_keys_and_malformed_inputs_are_refused(self):
        cases = ('Hair Hair 03.01', None, {'key': 'one'},
                 [entry('same', 'Hair Hair 03.01'), entry('same', 'Hair Hair 04.01')],
                 [None], [{}], [entry('', 'name')], [entry(' ', 'name')],
                 [entry('key', '')], [entry('key', ' ')],
                 [entry(None, 'name')], [entry('key', None)])
        for source in cases:
            with self.subTest(source=source), self.assertRaises(ValueError):
                labels.logical_labels(source)


if __name__ == '__main__':
    unittest.main()
