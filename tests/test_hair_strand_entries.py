"""Logical Hair rows retain proven physical membership without native access."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_strand_entries.py'
SPEC = importlib.util.spec_from_file_location('hair_strand_entries_pure', PATH)
entries = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entries)
BROWSER_PATH = PATH.with_name('hair_strand_browser.py')
ROWS_NODE = next(node for node in ast.parse(BROWSER_PATH.read_text(encoding='utf-8')).body
                 if isinstance(node, ast.FunctionDef) and node.name == '_rows')
ROWS_NAMESPACE = {}
exec(compile(ast.Module(body=[ROWS_NODE], type_ignores=[]), str(BROWSER_PATH), 'exec'),
     ROWS_NAMESPACE)
browser_rows = ROWS_NAMESPACE['_rows']


def row(name, order, *, side='U', count=4, group='FRONT'):
    bones = [f'{name} {index}' for index in range(count)]
    return {'signature': name, 'strand_id': 'id-' + name, 'bone_label': bones[0],
            'bones': bones, 'group': group, 'count': count, 'side': side,
            'order': order, 'pair_id': None, 'pair_proof': 'NONE',
            'mirror_id': None, 'mirror_signature': None, 'sync_mirror': True}


def paired(*, left_count=4, right_count=4):
    left = row('left', 0, side='L', count=left_count)
    right = row('right', 2, side='R', count=right_count)
    for own, other in ((left, right), (right, left)):
        own.update(pair_id='pair', pair_proof='MIRROR', mirror_id=other['strand_id'],
                   mirror_signature=other['signature'])
    return [left, right]


class HairStrandEntriesTests(unittest.TestCase):
    def test_proven_pair_is_one_entry_with_per_side_count(self):
        result = entries.build_entries(paired())
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item['key'], 'pair:pair')
        self.assertEqual(item['signatures'], ('left', 'right'))
        self.assertEqual(item['member_count'], 2)
        self.assertEqual(item['side'], 'L/R')
        self.assertEqual(item['count'], 4)
        self.assertEqual(item['counts'], (4, 4))
        self.assertEqual(len(item['bones']), 8)
        self.assertEqual(item['signature'], 'left')
        self.assertEqual(item['strand_id'], 'id-left')
        self.assertEqual(item['bone_label'], 'left 0')
        self.assertEqual(tuple(member['signature'] for member in item['members']), ('left', 'right'))

    def test_each_existing_proof_type_can_group(self):
        for proof in entries.PROVEN_PAIRS:
            with self.subTest(proof=proof):
                rows = paired()
                for item in rows:
                    item['pair_proof'] = proof
                self.assertEqual(len(entries.build_entries(rows)), 1)

    def test_center_self_pair_and_unpaired_stay_separate(self):
        center = row('center', 1, side='C')
        center.update(pair_id='center', mirror_id=center['strand_id'],
                      mirror_signature=center['signature'], pair_proof='MIRROR')
        result = entries.build_entries(paired() + [center, row('loose', 3)])
        self.assertEqual(tuple(item['signatures'] for item in result),
                         (('left', 'right'), ('center',), ('loose',)))
        self.assertEqual(result[1]['side'], 'C')
        self.assertEqual(result[1]['key'], 'strand:id-center')

    def test_partial_or_stale_links_do_not_infer_symmetry(self):
        changes = ({'mirror_id': None}, {'mirror_id': 'stale'},
                   {'mirror_signature': None}, {'mirror_signature': 'stale'},
                   {'pair_id': None}, {'pair_id': 'different'},
                   {'pair_proof': 'NONE'}, {'pair_proof': 'FUTURE'},
                   {'pair_proof': 'GRAPH'}, {'side': 'L'}, {'side': 'U'})
        for change in changes:
            with self.subTest(change=change):
                rows = paired()
                rows[1].update(change)
                self.assertEqual(tuple(item['signatures'] for item in entries.build_entries(rows)),
                                 (('left',), ('right',)))

    def test_two_unknown_equal_proofs_remain_separate(self):
        rows = paired()
        for item in rows:
            item['pair_proof'] = 'FUTURE'
        self.assertEqual(len(entries.build_entries(rows)), 2)

    def test_missing_proof_remains_separate(self):
        rows = paired()
        for item in rows:
            del item['pair_proof']
        self.assertEqual(len(entries.build_entries(rows)), 2)

    def test_names_and_matching_counts_do_not_establish_a_pair(self):
        rows = [row('Hair.L', 0, side='L'), row('Hair.R', 1, side='R')]
        self.assertEqual(tuple(item['signatures'] for item in entries.build_entries(rows)),
                         (('Hair.L',), ('Hair.R',)))

    def test_browser_projection_stays_flat_and_exposes_only_existing_proof(self):
        rows = paired()
        data = {'strands': rows}
        profile = {'strands': {item['strand_id']: {'group': item['group'],
                              'sync_mirror': item['sync_mirror']} for item in rows}}
        before = copy.deepcopy((data, profile))
        flat = browser_rows(data, profile)
        self.assertEqual(len(flat), 2)
        self.assertEqual(tuple(item['count'] for item in flat), (4, 4))
        self.assertEqual(tuple(item['pair_proof'] for item in flat), ('MIRROR', 'MIRROR'))
        self.assertEqual(len(entries.build_entries(flat)), 1)
        self.assertEqual((data, profile), before)
        for item in rows:
            del item['pair_proof']
        flat = browser_rows(data, profile)
        self.assertEqual(tuple(item['pair_proof'] for item in flat), ('NONE', 'NONE'))
        self.assertEqual(len(entries.build_entries(flat)), 2)

    def test_pair_id_collision_keeps_every_member_separate(self):
        rows = paired()
        rows.append(row('loose', 3))
        rows[2]['pair_id'] = 'pair'
        result = entries.build_entries(rows)
        self.assertEqual(len(result), 3)
        self.assertEqual(len({item['key'] for item in result}), 3)

    def test_mixed_groups_keep_each_category(self):
        rows = paired()
        rows[1]['group'] = 'BACK'
        item = entries.build_entries(rows)[0]
        self.assertEqual(item['group'], 'MIXED')
        self.assertEqual(item['groups'], ('FRONT', 'BACK'))
        self.assertEqual(tuple(member['group'] for member in item['members']), ('FRONT', 'BACK'))

    def test_mixed_counts_do_not_sum_or_hide_either_side(self):
        item = entries.build_entries(paired(left_count=3, right_count=5))[0]
        self.assertIsNone(item['count'])
        self.assertEqual(item['counts'], (3, 5))
        self.assertEqual(len(item['bones']), 8)
        self.assertEqual(tuple(member['count'] for member in item['members']), (3, 5))

    def test_pair_membership_is_independent_of_motion_sync_flags(self):
        rows = paired()
        rows[0]['sync_mirror'] = False
        rows[1]['sync_mirror'] = True
        result = entries.build_entries(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(tuple(item['sync_mirror'] for item in result[0]['members']), (False, True))
        self.assertEqual(tuple(item['sync_mirror'] for item in rows), (False, True))

    def test_one_checked_member_closes_to_both_for_every_action(self):
        logical = entries.build_entries(paired())
        for chosen in (['left'], ['right'], ['right', 'left'], {'left'}):
            with self.subTest(chosen=chosen):
                self.assertEqual(entries.normalize_signatures(logical, chosen), ('left', 'right'))
        self.assertEqual(entries.expand_keys(logical, ['pair:pair']), ('left', 'right'))

    def test_closure_preserves_whole_entry_order(self):
        rows = paired() + [row('center', 1), row('last', 4)]
        logical = entries.build_entries(rows)
        self.assertEqual(entries.normalize_signatures(logical, ['last', 'right', 'center']),
                         ('left', 'right', 'center', 'last'))
        self.assertEqual(entries.expand_keys(logical, ['strand:id-last', 'pair:pair']),
                         ('left', 'right', 'last'))

    def test_unpaired_selection_does_not_add_other_strands(self):
        rows = paired()
        rows[1]['pair_proof'] = 'NONE'
        logical = entries.build_entries(rows)
        self.assertEqual(entries.normalize_signatures(logical, ['left']), ('left',))

    def test_stale_member_or_entry_is_refused(self):
        logical = entries.build_entries(paired())
        for function, values in ((entries.normalize_signatures, ['stale']),
                                 (entries.expand_keys, ['strand:id-left'])):
            with self.subTest(function=function.__name__), self.assertRaisesRegex(
                    entries.HairStrandEntriesError, 'disappeared'):
                function(logical, values)

    def test_empty_selections_and_rows_stay_empty(self):
        self.assertEqual(entries.build_entries([]), ())
        self.assertEqual(entries.normalize_signatures([], []), ())
        self.assertEqual(entries.expand_keys([], []), ())

    def test_invalid_or_duplicate_selections_are_refused(self):
        logical = entries.build_entries(paired())
        for selected in ('left', None, ['left', 'left'], [None], [5], ['']):
            for function in (entries.normalize_signatures, entries.expand_keys):
                with self.subTest(selected=selected, function=function.__name__), self.assertRaises(
                        entries.HairStrandEntriesError):
                    function(logical, selected)

    def test_input_order_is_registry_order_not_call_order_or_side(self):
        rows = paired() + [row('center', 1)]
        rows[0]['order'], rows[1]['order'] = 2, 0
        first = entries.build_entries(rows)
        self.assertEqual(first, entries.build_entries(list(reversed(rows))))
        self.assertEqual(first[0]['signatures'], ('right', 'left'))
        self.assertEqual(first[0]['signature'], 'right')
        self.assertEqual(first[0]['order'], 0)

    def test_returned_records_are_detached_and_ignore_native_extras(self):
        rows = paired()
        before = copy.deepcopy(rows)
        native = object()
        rows[0]['native_pointer'] = native
        logical = entries.build_entries(rows)
        json.dumps(logical)
        logical[0]['members'][0]['group'] = 'BACK'
        logical[0]['members'][0]['bones'] = ('changed',)
        self.assertEqual({key: value for key, value in rows[0].items() if key != 'native_pointer'}, before[0])
        self.assertIs(rows[0]['native_pointer'], native)
        self.assertEqual(rows[1], before[1])
        rows[1]['bones'][0] = 'later change'
        self.assertEqual(logical[0]['members'][1]['bones'][0], 'right 0')

    def test_duplicate_identity_or_order_is_refused(self):
        for key in ('signature', 'strand_id', 'order'):
            rows = paired()
            rows[1][key] = rows[0][key]
            with self.subTest(key=key), self.assertRaisesRegex(entries.HairStrandEntriesError, 'duplicated'):
                entries.build_entries(rows)

    def test_overlapping_bone_ownership_is_refused(self):
        rows = paired()
        rows[1]['bones'][1] = rows[0]['bones'][1]
        with self.assertRaisesRegex(entries.HairStrandEntriesError, 'overlaps'):
            entries.build_entries(rows)

    def test_malformed_basic_membership_is_refused_without_mutation(self):
        changes = ({'signature': ''}, {'strand_id': object()}, {'side': '?'}, {'side': []},
                   {'count': True}, {'count': 8}, {'order': -1}, {'order': False},
                   {'bones': []}, {'bones': ['same'] * 4}, {'bone_label': 'wrong'},
                   {'group': None}, {'pair_proof': object()}, {'mirror_id': object()},
                   {'pair_id': 2}, {'sync_mirror': 1})
        for change in changes:
            with self.subTest(change=change):
                rows = paired()
                rows[0].update(change)
                original = rows[0].copy()
                with self.assertRaises(entries.HairStrandEntriesError):
                    entries.build_entries(rows)
                self.assertEqual(rows[0], original)

    def test_malformed_entry_membership_cannot_silently_drop_selection(self):
        variants = ([{'key': 'pair:pair', 'signatures': ()}],
                    [{'key': 'pair:pair', 'signatures': ('left', 'left')}],
                    [{'key': 'a', 'signatures': ('left',)}, {'key': 'b', 'signatures': ('left',)}],
                    [{'key': 'a', 'signatures': ('left',)}, {'key': 'a', 'signatures': ('right',)}])
        for logical in variants:
            with self.subTest(logical=logical), self.assertRaises(entries.HairStrandEntriesError):
                entries.normalize_signatures(logical, ['left'])


if __name__ == '__main__':
    unittest.main()
