"""Pure tests of the limited leg-Rest eligibility, without importing Blender."""
import copy
import importlib.util
import math
from pathlib import Path
import sys
import unittest


PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/body_rest_adoption_policy.py'
SPEC = importlib.util.spec_from_file_location('_rest_adoption_policy_tests', PATH)
policy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = policy
SPEC.loader.exec_module(policy)


def bone(parent=None):
    return {
        'parent': parent, 'head': [0.0, 0.0, 0.0], 'tail': [0.0, 1.0, 0.0],
        'matrix': [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                   0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        'use_connect': False, 'use_deform': True, 'use_inherit_rotation': True,
        'inherit_scale': 'FULL', 'use_local_location': True, 'use_relative_parent': False,
    }


CHAINS = {side: tuple(f'{part}.{side}' for part in ('thigh', 'shin', 'foot', 'toe'))
          for side in ('L', 'R')}
COMPANIONS = tuple(f'MCH_toe_{kind}_ref.{side}' for side in ('L', 'R') for kind in ('auto', 'FK'))


def fixture():
    result = {'Hips': bone(), 'arm': bone('Hips')}
    for side, chain in CHAINS.items():
        parent = 'Hips'
        for name in chain:
            result[name] = bone(parent)
            parent = name
        for name in (n for n in COMPANIONS if n.endswith('.' + side)):
            result[name] = bone(f'foot.{side}')
            result[name]['use_deform'] = False
    return result


class RestAdoptionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.old = fixture()
        self.now = copy.deepcopy(self.old)

    def classify(self, **options):
        return policy.classify_changes(self.old, self.now, leg_chains=CHAINS, **options)

    def rejects(self, text, **options):
        with self.assertRaisesRegex(policy.RestAdoptionPolicyError, text):
            self.classify(**options)

    def test_unchanged_returns_immutable_empty_delta(self):
        result = self.classify()
        self.assertEqual(result.native_changed, frozenset())
        self.assertEqual(result.companion_changed, frozenset())
        with self.assertRaises(AttributeError):
            result.native_changed = frozenset({'arm'})

    def test_all_eight_native_leg_and_toe_geometry_changes_are_allowed(self):
        for chain in CHAINS.values():
            for name in chain:
                self.now[name]['head'][2] -= 0.05
        result = self.classify()
        self.assertEqual(result.native_changed, frozenset(n for chain in CHAINS.values() for n in chain))
        self.assertFalse(result.companion_changed)

    def test_four_authenticated_companions_are_separate_from_native_scope(self):
        self.now['thigh.L']['tail'][0] += 0.02
        for name in COMPANIONS:
            self.now[name]['matrix'][0] += 2e-5
        result = self.classify(owned_companions=COMPANIONS)
        self.assertEqual(result.native_changed, frozenset({'thigh.L'}))
        self.assertEqual(result.companion_changed, frozenset(COMPANIONS))

    def test_companions_are_never_inferred_from_their_names(self):
        self.now[COMPANIONS[0]]['matrix'][0] += 2e-5
        self.rejects('outside the accepted leg scope')

    def test_foreign_native_geometry_change_rejected(self):
        self.now['arm']['head'][0] += 0.001
        self.rejects("outside.*'arm'")

    def test_add_delete_and_rename_inventory_rejected(self):
        for mode in ('add', 'delete', 'rename'):
            with self.subTest(mode=mode):
                self.now = copy.deepcopy(self.old)
                if mode == 'add':
                    self.now['extra'] = bone()
                elif mode == 'delete':
                    del self.now['arm']
                else:
                    self.now['new_arm'] = self.now.pop('arm')
                self.rejects('inventory changed')

    def test_every_relation_is_exact_including_unselected_bones(self):
        changes = {'parent': None, 'use_connect': True, 'use_deform': False,
                   'use_inherit_rotation': False, 'inherit_scale': 'NONE',
                   'use_local_location': False, 'use_relative_parent': True}
        for field, changed in changes.items():
            with self.subTest(field=field):
                self.now = copy.deepcopy(self.old)
                self.now['arm'][field] = changed
                self.rejects('structure changed')

    def test_nan_infinity_and_overflow_rejected_before_comparison(self):
        for field, index, value in (('head', 0, math.nan), ('tail', 2, math.inf),
                                    ('matrix', 5, -math.inf), ('matrix', 0, 10 ** 400)):
            with self.subTest(field=field, value=str(value)):
                self.now = copy.deepcopy(self.old)
                self.now['thigh.L'][field][index] = value
                self.rejects('finite numbers', same_rest_bone=lambda *_: True)

    def test_boolean_is_not_a_coordinate_or_matrix_number(self):
        for field in ('head', 'tail', 'matrix'):
            with self.subTest(field=field):
                self.now = copy.deepcopy(self.old)
                self.now['thigh.L'][field][0] = True
                self.rejects('not booleans')

    def test_relation_boolean_requires_exact_bool(self):
        for field in policy._BOOLEANS:
            with self.subTest(field=field):
                self.now = copy.deepcopy(self.old)
                self.now['thigh.L'][field] = int(self.now['thigh.L'][field])
                self.rejects('exact boolean')

    def test_missing_extra_fields_and_wrong_vector_shapes_rejected(self):
        cases = ('missing', 'extra', 'matrix15', 'matrix17', 'matrix_tuple', 'head2')
        for case in cases:
            with self.subTest(case=case):
                self.now = copy.deepcopy(self.old)
                row = self.now['thigh.L']
                if case == 'missing':
                    del row['use_connect']
                elif case == 'extra':
                    row['arbitrary'] = True
                elif case == 'matrix15':
                    row['matrix'].pop()
                elif case == 'matrix17':
                    row['matrix'].append(0.0)
                elif case == 'matrix_tuple':
                    row['matrix'] = tuple(row['matrix'])
                else:
                    row['head'].pop()
                self.rejects('field|components')

    def test_invalid_parent_and_cycles_are_rejected(self):
        for old, new in ((None, 'absent'), (None, 'Hips'), ('arm', 'arm')):
            with self.subTest(old=old, new=new):
                self.old = fixture()
                self.now = copy.deepcopy(self.old)
                if old is None:
                    self.now['Hips']['parent'] = new
                else:
                    self.old['Hips']['parent'] = old
                    self.now['Hips']['parent'] = new
                self.rejects('invalid parent|parent cycle')

    def test_invalid_inheritance_mode_is_rejected_even_when_unchanged(self):
        self.old['arm']['inherit_scale'] = self.now['arm']['inherit_scale'] = 'invented'
        self.rejects('inheritance mode')

    def test_exact_mathematical_tolerance_is_unchanged(self):
        self.now['thigh.L']['head'][0] = 1e-6
        self.now['thigh.L']['matrix'][1] = 1e-6
        self.assertFalse(self.classify().native_changed)

    def test_next_float_above_tolerance_is_changed(self):
        self.now['thigh.L']['head'][0] = math.nextafter(1e-6, math.inf)
        self.assertEqual(self.classify().native_changed, frozenset({'thigh.L'}))

    def test_head_and_tail_use_euclidean_not_per_axis_tolerance(self):
        self.now['thigh.L']['head'] = [0.8e-6, 0.8e-6, 0.0]
        self.assertEqual(self.classify().native_changed, frozenset({'thigh.L'}))

    def test_native_comparator_can_supply_its_own_last_bit_boundary(self):
        self.now['thigh.L']['head'][0] = math.nextafter(1e-6, math.inf)
        calls = []
        def comparator(old, new):
            calls.append((old, new))
            return True
        self.assertFalse(self.classify(same_rest_bone=comparator).native_changed)
        self.assertEqual(len(calls), len(self.old))

    def test_comparator_cannot_override_structure_or_schema(self):
        called = []
        self.now['arm']['use_connect'] = True
        self.rejects('structure changed', same_rest_bone=lambda *args: called.append(args) or True)
        self.assertFalse(called)

    def test_invalid_comparator_or_nonboolean_result_rejected(self):
        self.rejects('not callable', same_rest_bone=1)
        self.rejects('exact boolean', same_rest_bone=lambda *_: 1)

    def test_caller_inputs_remain_exact_and_are_detached_for_comparator(self):
        before = copy.deepcopy((self.old, self.now, CHAINS))
        self.classify()
        self.assertEqual((self.old, self.now, CHAINS), before)
        def comparator(old, new):
            old['head'][0] = 0.2
            new['tail'][0] = 0.3
            return True
        self.classify(same_rest_bone=comparator)
        self.assertEqual((self.old, self.now, CHAINS), before)

    def test_chain_mapping_requires_both_sides_only(self):
        for chains in ({'L': CHAINS['L']}, {**CHAINS, 'X': CHAINS['L']}, []):
            with self.subTest(chains=chains):
                with self.assertRaisesRegex(policy.RestAdoptionPolicyError, 'exactly'):
                    policy.classify_changes(self.old, self.now, leg_chains=chains)

    def test_chain_duplicates_missing_bones_overlap_and_invalid_length_rejected(self):
        for chain in (('thigh.L', 'thigh.L', 'foot.L'), ('absent', 'shin.L', 'foot.L'),
                      CHAINS['L'], ('thigh.R', 'shin.R')):
            with self.subTest(chain=chain):
                with self.assertRaises(policy.RestAdoptionPolicyError):
                    policy.classify_changes(self.old, self.now, leg_chains={'L': CHAINS['L'], 'R': chain})

    def test_chain_hierarchy_must_be_direct(self):
        self.old['shin.L']['parent'] = self.now['shin.L']['parent'] = 'Hips'
        self.rejects('direct native hierarchy')

    def test_three_bone_chain_is_supported_but_excludes_unsupplied_toe(self):
        chains = {k: v[:3] for k, v in CHAINS.items()}
        self.now['thigh.L']['head'][2] -= 0.05
        result = policy.classify_changes(self.old, self.now, leg_chains=chains)
        self.assertEqual(result.native_changed, frozenset({'thigh.L'}))
        self.now['toe.L']['tail'][0] += 0.02
        with self.assertRaisesRegex(policy.RestAdoptionPolicyError, 'outside'):
            policy.classify_changes(self.old, self.now, leg_chains=chains)

    def test_companion_collection_rejects_overlap_duplicates_and_unknown_names(self):
        for companions in (('thigh.L',), (COMPANIONS[0], COMPANIONS[0]), ('absent',), 'MCH'):
            with self.subTest(companions=companions):
                self.rejects('companion|Companion', owned_companions=companions)

    def test_empty_or_malformed_snapshot_domain_rejected(self):
        for malformed in ({}, [], {'': bone()}, {1: bone()}):
            with self.subTest(malformed=malformed):
                with self.assertRaises(policy.RestAdoptionPolicyError):
                    policy.classify_changes(malformed, self.now, leg_chains=CHAINS)


if __name__ == '__main__':
    unittest.main()
