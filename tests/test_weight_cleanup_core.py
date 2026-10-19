"""Behavioral tests for selected-bone island removal and topological gap fill.

This module imports the pure planner directly, without importing bpy, opening a
character file, or mutating a Blender installation.  Run with unittest, or pass
--addon-parent and --report to this file to verify a pinned release projection.
"""

import argparse
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SUBJECT = None


def load_subject(addon_parent):
    path = Path(addon_parent).resolve() / 'character_designer/weight_cleanup_core.py'
    spec = importlib.util.spec_from_file_location('weight_cleanup_core_subject', path)
    subject = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = subject
    spec.loader.exec_module(subject)
    return subject


def mesh(name, weights, *, positions=None, edges=None, triangles=()):
    if positions is None:
        positions = tuple((float(index), 0.0, 0.0) for index in range(len(weights)))
    if edges is None:
        edges = tuple((index, index + 1) for index in range(len(weights) - 1))
    return SUBJECT.MeshSnapshot(name, tuple(positions), tuple(edges),
                                tuple(dict(value) for value in weights), tuple(triangles))


def change_map(plan):
    return {(change.mesh_name, change.vertex_index): dict(change.new_weights)
            for change in plan.changes}


class FillTests(unittest.TestCase):
    def plan(self, source, **options):
        return SUBJECT.plan_fill(source, {'A', 'B', 'Locked'}, **options)

    def assert_rejected(self, plan):
        self.assertFalse(plan.ok, plan.to_dict())
        self.assertEqual(tuple(plan.changes), ())
        self.assertTrue(plan.errors)

    def test_fill_uses_nearest_graph_path_and_preserves_original_paint(self):
        # Vertex 2 is spatially next to B but only connected to A's component.
        source = mesh('Leg', ({'A': .375, 'B': .125, 'Mask': .7}, {}, {}, {'B': 1.0}),
                      positions=((0, 0, 0), (3, 0, 0), (10, 0, 0), (10, .001, 0)),
                      edges=((0, 1), (1, 2)))
        before = copy.deepcopy(source)
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(source, before)
        self.assertEqual(set(change_map(plan)), {('Leg', 1), ('Leg', 2)})
        for target in change_map(plan).values():
            self.assertAlmostEqual(target['A'], .75)
            self.assertAlmostEqual(target['B'], .25)
            self.assertNotIn('Mask', target, 'A donor helper group is not a deform influence')
        self.assertEqual(dict(plan.changes[0].old_weights), {})

    def test_equal_distance_seed_is_deterministic_without_averaging(self):
        source = mesh('Mesh', ({'A': 1.0}, {}, {'B': 1.0}))
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan)[('Mesh', 1)], {'A': 1.0})
        again = self.plan(source)
        self.assertEqual(plan.to_dict(), again.to_dict())

    def test_zero_length_edges_terminate_and_choose_the_same_source(self):
        source = mesh('Mesh', ({'A': 1.0}, {}, {}, {'B': 1.0}),
                      positions=((0, 0, 0),) * 4,
                      edges=((0, 1), (1, 2), (2, 3), (3, 0)))
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan), {('Mesh', 1): {'A': 1.0}, ('Mesh', 2): {'A': 1.0}})

    def test_disconnected_unweighted_component_refuses_all_changes(self):
        source = mesh('Mesh', ({'A': 1.0}, {}, {}, {}), edges=((0, 1), (2, 3)))
        before = copy.deepcopy(source)
        self.assert_rejected(self.plan(source))
        self.assertEqual(source, before)

    def test_target_subset_leaves_non_target_gaps_and_valid_paint_exact(self):
        source = mesh('Mesh', ({'A': .123456789, 'Mask': 0.0}, {}, {}, {'B': .812345678}))
        plan = self.plan(source, targets={1})
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan), {('Mesh', 1): {'A': 1.0}})
        self.assertEqual(source.weights[0], {'A': .123456789, 'Mask': 0.0})
        self.assertEqual(source.weights[2], {})

    def test_empty_target_set_is_noop_even_with_a_separate_missing_seed(self):
        source = mesh('Mesh', ({'A': 1.0}, {}, {}), edges=((0, 1),))
        plan = self.plan(source, targets=set())
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(tuple(plan.changes), ())

    def test_helper_only_vertex_is_a_gap_and_helper_membership_is_preserved(self):
        source = mesh('Mesh', ({'A': .2, 'B': .6}, {'Helper': .9375, 'Mask': 0.0}))
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        changed = change_map(plan)[('Mesh', 1)]
        self.assertEqual(changed['Helper'], .9375)
        self.assertEqual(changed['Mask'], 0.0)
        self.assertAlmostEqual(changed['A'], .25)
        self.assertAlmostEqual(changed['B'], .75)

    def test_helper_locks_do_not_consume_deform_budget(self):
        source = mesh('Mesh', ({'A': 1.0}, {'Helper': .875}))
        plan = self.plan(source, locked_names={'Helper'})
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan)[('Mesh', 1)], {'A': 1.0, 'Helper': .875})

    def test_locked_deform_membership_is_exact_and_donor_lock_does_not_transfer(self):
        source = mesh('Mesh', ({'A': .3, 'B': .1, 'Locked': .6}, {'Locked': 0.0, 'Helper': .8}))
        plan = self.plan(source, locked_names={'Locked'})
        self.assertTrue(plan.ok, plan.to_dict())
        changed = change_map(plan)[('Mesh', 1)]
        self.assertEqual(changed['Locked'], 0.0)
        self.assertEqual(changed['Helper'], .8)
        self.assertAlmostEqual(changed['A'], .75)
        self.assertAlmostEqual(changed['B'], .25)

    def test_nearest_seed_with_only_locked_influences_refuses_atomically(self):
        source = mesh('Mesh', ({'Locked': 1.0}, {}, {}, {'A': 1.0}))
        self.assert_rejected(self.plan(source, locked_names={'Locked'}))

    def test_explicit_tolerance_can_treat_a_small_positive_deform_total_as_gap(self):
        source = mesh('Mesh', ({'B': .4}, {'A': 1e-10, 'Helper': .5}))
        plan = self.plan(source, epsilon=1e-8)
        self.assertTrue(plan.ok, plan.to_dict())
        value = change_map(plan)[('Mesh', 1)]
        self.assertEqual(value['Helper'], .5)
        self.assertAlmostEqual(value['B'], 1.0)
        self.assertLessEqual(value.get('A', 0.0), 1e-8)

    def test_strict_zero_mode_preserves_even_a_tiny_existing_deform_influence(self):
        source = mesh('Mesh', ({'B': .4}, {'A': 1e-10, 'Helper': .5}))
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(tuple(plan.changes), ())
        self.assertEqual(source.weights[1], {'A': 1e-10, 'Helper': .5})

    def test_existing_valid_non_normalized_weights_are_not_recomputed(self):
        source = mesh('Mesh', ({'A': .2, 'B': .125}, {'A': .91, 'B': .83, 'Helper': .03125}))
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(tuple(plan.changes), ())

    def test_invalid_geometry_weight_and_index_inputs_are_rejected(self):
        cases = (
            mesh('NaN position', ({'A': 1.0}, {}), positions=((math.nan, 0, 0), (1, 0, 0))),
            mesh('Inf position', ({'A': 1.0}, {}), positions=((0, 0, 0), (math.inf, 0, 0))),
            mesh('NaN weight', ({'A': math.nan}, {})),
            mesh('Inf weight', ({'A': math.inf}, {})),
            mesh('Negative weight', ({'A': -.25}, {})),
            mesh('Bad edge', ({'A': 1.0}, {}), edges=((0, 2),)),
            mesh('Negative edge', ({'A': 1.0}, {}), edges=((-1, 1),)),
            mesh('Bad triangle', ({'A': 1.0}, {}), triangles=((0, 1, 2),)),
        )
        for source in cases:
            with self.subTest(case=source.name):
                before = repr(source)
                self.assert_rejected(self.plan(source))
                self.assertEqual(repr(source), before)
        source = mesh('Mesh', ({'A': 1.0}, {}))
        for options in ({'targets': {2}}, {'targets': {-1}}, {'epsilon': math.nan}, {'epsilon': -1.0}):
            with self.subTest(options=options):
                self.assert_rejected(self.plan(source, **options))


class IslandTests(unittest.TestCase):
    def plan(self, meshes, targets=('Shoes',), *, bones=None, **options):
        if bones is None:
            bones = {'Pinky': ((0, 0, 0), (0, 1, 0))}
        return SUBJECT.plan_islands(tuple(meshes), tuple(targets), bones,
                                    {'Pinky', 'Foot', 'Other'}, **options)

    def references(self):
        hand = mesh('Hand', ({'Pinky': .625}, {'Pinky': .5}, {'Pinky': .875}),
                    positions=((0, 0, 0), (.3, 0, 0), (0, .5, 0)),
                    edges=((0, 1), (1, 2), (2, 0)), triangles=((0, 1, 2),))
        shoes = mesh('Shoes', ({'Pinky': .8, 'Foot': .2, 'Mask': 0.0},) * 3,
                     positions=((10, 0, 0), (10.3, 0, 0), (10, .5, 0)),
                     edges=((0, 1), (1, 2), (2, 0)), triangles=((0, 1, 2),))
        return hand, shoes

    def assert_rejected(self, plan):
        self.assertFalse(plan.ok, plan.to_dict())
        self.assertEqual(tuple(plan.changes), ())
        self.assertTrue(plan.errors)

    def test_character_reference_keeps_hand_but_writes_only_shoes(self):
        hand, shoes = self.references()
        before = copy.deepcopy((hand, shoes))
        plan = self.plan((hand, shoes), scope='CHARACTER')
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual((hand, shoes), before)
        self.assertEqual(set(change_map(plan)), {('Shoes', index) for index in range(3)})
        for value in change_map(plan).values():
            self.assertEqual(value, {'Foot': .2, 'Mask': 0.0},
                             'Existing surviving paint must not be normalized or replaced')

    def test_current_scope_does_not_use_another_mesh_as_authority(self):
        hand, shoes = self.references()
        plan = self.plan((hand, shoes), scope='CURRENT')
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(tuple(plan.changes), ())

    def test_distance_to_bone_wins_over_a_larger_far_weight_region(self):
        near = mesh('Near', ({'Pinky': .1}, {'Pinky': .1}, {'Pinky': .1}),
                    positions=((0, 0, 0), (.1, 0, 0), (0, .1, 0)),
                    edges=((0, 1), (1, 2), (2, 0)), triangles=((0, 1, 2),))
        far = mesh('Shoes', ({'Pinky': 1.0, 'Foot': .125},) * 3,
                   positions=((10, 0, 0), (30, 0, 0), (10, 20, 0)),
                   edges=((0, 1), (1, 2), (2, 0)), triangles=((0, 1, 2),))
        plan = self.plan((near, far))
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(len(plan.changes), 3)
        self.assertTrue(all('Pinky' not in value for value in change_map(plan).values()))

    def test_equally_near_identical_main_components_are_ambiguous(self):
        a = mesh('Left', ({'Pinky': 1.0}, {'Pinky': 1.0}),
                 positions=((-1, 0, 0), (-1, 1, 0)))
        b = mesh('Shoes', ({'Pinky': 1.0, 'Foot': .2}, {'Pinky': 1.0, 'Foot': .2}),
                 positions=((1, 0, 0), (1, 1, 0)))
        # Other deform influences do not resolve which Pinky region is primary.
        self.assert_rejected(self.plan((a, b)))

    def test_locked_selected_group_refuses_all_meshes_without_partial_cleanup(self):
        hand, shoes = self.references()
        before = copy.deepcopy((hand, shoes))
        self.assert_rejected(self.plan((hand, shoes), locked_names={'Shoes': {'Pinky'}}))
        self.assertEqual((hand, shoes), before)

    def test_cleanup_never_leaves_a_new_unweighted_disconnected_piece(self):
        hand, shoes = self.references()
        empty_after = mesh('Shoes', ({'Pinky': 1.0},) * 3,
                           positions=shoes.world_positions, edges=shoes.edges, triangles=shoes.triangles)
        before = copy.deepcopy((hand, empty_after))
        self.assert_rejected(self.plan((hand, empty_after), fill_holes=True))
        self.assertEqual((hand, empty_after), before)

    def test_cleanup_local_fill_preserves_helpers_and_existing_weighted_neighbors(self):
        hand, _ = self.references()
        shoes = mesh('Shoes', ({'Pinky': 1.0, 'Helper': .8}, {'Pinky': 1.0}, {'Foot': .375}),
                     positions=((10, 0, 0), (11, 0, 0), (12, 0, 0)))
        plan = self.plan((hand, shoes), fill_holes=True)
        self.assertTrue(plan.ok, plan.to_dict())
        values = change_map(plan)
        self.assertEqual(set(values), {('Shoes', 0), ('Shoes', 1)})
        self.assertEqual(values[('Shoes', 0)], {'Foot': 1.0, 'Helper': .8})
        self.assertEqual(values[('Shoes', 1)], {'Foot': 1.0})
        self.assertEqual(shoes.weights[2], {'Foot': .375})

    def test_preexisting_empty_bridge_is_not_a_weight_island_or_fill_target(self):
        # Mesh connectivity must not merge separated A-weight supports. The
        # previous empty bridge stays empty, and removed A must not leak back.
        source = mesh('Shoes', ({'Pinky': .75}, {'Pinky': .5}, {'Mask': .25},
                                {'Pinky': 1.0}, {'Pinky': 1.0}, {'Foot': .625}))
        plan = self.plan((source,), scope='CURRENT', fill_holes=True)
        self.assertTrue(plan.ok, plan.to_dict())
        values = change_map(plan)
        self.assertEqual(set(values), {('Shoes', 3), ('Shoes', 4)})
        self.assertEqual(values[('Shoes', 3)], {'Foot': 1.0})
        self.assertEqual(values[('Shoes', 4)], {'Foot': 1.0})
        self.assertEqual(source.weights[2], {'Mask': .25})

    def test_target_vertices_limits_writes_without_redefining_character_reference(self):
        hand, shoes = self.references()
        plan = self.plan((hand, shoes), target_vertices={'Shoes': {1}})
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan), {('Shoes', 1): {'Foot': .2, 'Mask': 0.0}})

    def test_unselected_bone_and_non_target_mesh_memberships_remain_exact(self):
        hand, shoes = self.references()
        weights = [dict(value, Other=.2734375) for value in shoes.weights]
        shoes = mesh('Shoes', weights, positions=shoes.world_positions,
                     edges=shoes.edges, triangles=shoes.triangles)
        plan = self.plan((hand, shoes))
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertTrue(all(change.mesh_name == 'Shoes' for change in plan.changes))
        self.assertTrue(all(dict(change.new_weights)['Other'] == .2734375 for change in plan.changes))
        self.assertEqual(hand.weights[0]['Pinky'], .625)

    def test_invalid_scope_target_bone_and_duplicate_mesh_identity_are_atomic(self):
        hand, shoes = self.references()
        cases = (
            lambda: self.plan((hand, shoes), scope='SPATIAL_NEAREST'),
            lambda: self.plan((hand, shoes), targets=('Missing',)),
            lambda: self.plan((hand, shoes), bones={'Helper': ((0, 0, 0), (0, 1, 0))}),
            lambda: self.plan((hand, shoes, shoes)),
            lambda: self.plan((hand, shoes), bones={'Pinky': ((math.nan, 0, 0), (0, 1, 0))}),
        )
        for index, invoke in enumerate(cases):
            with self.subTest(case=index):
                self.assert_rejected(invoke())


class InspectionTests(unittest.TestCase):
    def scan(self, sources, targets=('Shoes',), *, segments=None, **options):
        if segments is None:
            segments = {'Pinky': ((0, 0, 0), (0, 1, 0)),
                        'Foot': ((10, 0, 0), (10, 1, 0)),
                        'Other': ((20, 0, 0), (20, 1, 0))}
        return SUBJECT.inspect_islands(tuple(sources), tuple(targets), {'Pinky', 'Foot', 'Other'},
                                       segments, **options)

    def references(self):
        # The inspector must discover Pinky from the actual weights. No bone
        # selection, active Vertex Group, or predeclared suspects are inputs.
        return IslandTests().references()

    def test_scan_discovers_unknown_actual_bone_without_any_weight_changes(self):
        hand, shoes = self.references()
        before = copy.deepcopy((hand, shoes))
        scan = self.scan((hand, shoes))
        self.assertTrue(scan.ok, scan.to_dict())
        records = {record.bone_name: record for record in scan.records}
        self.assertEqual(set(records), {'Pinky', 'Foot'})
        self.assertEqual(records['Pinky'].status, 'STRAY')
        self.assertEqual(records['Pinky'].suspect_vertex_count, 3)
        self.assertEqual(records['Pinky'].stray_vertex_count, 3)
        self.assertEqual(records['Pinky'].distant_vertex_count, 3)
        self.assertEqual(tuple(records['Pinky'].suspect_vertex_indices), (0, 1, 2))
        self.assertEqual(records['Pinky'].main_mesh_name, 'Hand')
        self.assertEqual(records['Foot'].status, 'OK')
        self.assertEqual(tuple(scan.changes), ())
        self.assertEqual((hand, shoes), before)

    def test_distant_points_in_one_continuous_main_region_are_not_hidden_by_near_points(self):
        source = mesh('Shoes', ({'Pinky': 1.0}, {'Pinky': 1.0}, {'Pinky': 1.0}),
                      positions=((0, 0, 0), (0, .5, 0), (4, .5, 0)))
        before = copy.deepcopy(source)
        scan = self.scan((source,))
        self.assertTrue(scan.ok, scan.to_dict())
        record = scan.records[0]
        self.assertEqual(record.status, 'DISTANT')
        self.assertEqual(tuple(record.distant_vertex_indices), (2,))
        self.assertEqual(tuple(record.stray_vertex_indices), ())
        self.assertEqual(tuple(record.suspect_vertex_indices), (2,))
        self.assertFalse(record.removable)
        self.assertEqual(tuple(scan.changes), ())
        self.assertEqual(source, before)

    def test_tiny_positive_weight_bridge_does_not_hide_a_far_wrong_influence(self):
        source = mesh('Shoes', ({'Pinky': .9375}, {'Pinky': 1e-12}, {'Pinky': .875}),
                      positions=((0, 0, 0), (1, 0, 0), (5, 0, 0)))
        scan = self.scan((source,))
        self.assertTrue(scan.ok, scan.to_dict())
        record = scan.records[0]
        self.assertEqual(record.status, 'DISTANT')
        self.assertEqual(record.target_vertex_count, 3)
        self.assertEqual(record.stray_vertex_count, 0)
        self.assertEqual(tuple(record.distant_vertex_indices), (2,))
        self.assertEqual(tuple(scan.changes), ())

    def test_distant_single_component_is_warning_and_never_a_cleanup_plan(self):
        shoes = mesh('Shoes', ({'Foot': 1.0}, {'Foot': 1.0}),
                     positions=((10, 0, 0), (10, 1, 0)))
        scan = self.scan((shoes,), segments={'Foot': ((0, 0, 0), (0, 1, 0))})
        self.assertTrue(scan.ok, scan.to_dict())
        self.assertEqual(len(scan.records), 1)
        self.assertEqual(scan.records[0].status, 'DISTANT')
        self.assertEqual(scan.records[0].suspect_vertex_count, 2)
        self.assertFalse(scan.records[0].removable)
        self.assertEqual(tuple(scan.changes), ())
        cleanup = SUBJECT.plan_islands((shoes,), ('Shoes',), {'Foot': ((0, 0, 0), (0, 1, 0))}, {'Foot'})
        self.assertTrue(cleanup.ok)
        self.assertEqual(tuple(cleanup.changes), (), 'Distance alone must not erase the only existing support')

    def test_helper_zero_membership_and_unused_deform_bones_are_not_false_candidates(self):
        source = mesh('Shoes', ({'Pinky': 0.0, 'Helper': 1.0}, {'Foot': 1e-10}))
        scan = self.scan((source,), segments={'Foot': ((0, 0, 0), (2, 0, 0))})
        self.assertTrue(scan.ok, scan.to_dict())
        self.assertEqual([record.bone_name for record in scan.records], ['Foot'])
        self.assertEqual(scan.records[0].target_vertex_count, 1)
        self.assertEqual(scan.records[0].status, 'OK')

    def test_missing_bone_segment_reports_unresolved_without_hiding_other_suspects(self):
        hand, shoes = self.references()
        scan = self.scan((hand, shoes), segments={'Pinky': ((0, 0, 0), (0, 1, 0))})
        self.assertTrue(scan.ok, scan.to_dict())
        records = {record.bone_name: record for record in scan.records}
        self.assertEqual(records['Pinky'].status, 'STRAY')
        self.assertEqual(records['Foot'].status, 'UNRESOLVED')
        self.assertEqual(tuple(scan.changes), ())

    def test_ambiguous_main_is_reported_without_arbitrary_removal(self):
        a = mesh('Hand', ({'Pinky': 1.0}, {'Pinky': 1.0}),
                 positions=((-1, 0, 0), (-1, 1, 0)))
        b = mesh('Shoes', ({'Pinky': 1.0, 'Foot': .2}, {'Pinky': 1.0, 'Foot': .2}),
                 positions=((1, 0, 0), (1, 1, 0)))
        scan = self.scan((a, b))
        self.assertTrue(scan.ok, scan.to_dict())
        records = {record.bone_name: record for record in scan.records}
        self.assertEqual(records['Pinky'].status, 'AMBIGUOUS')
        self.assertEqual(tuple(scan.changes), ())

    def test_zero_length_bone_uses_absolute_warning_but_is_not_removable(self):
        source = mesh('Shoes', ({'Foot': 1.0}, {'Foot': 1.0}),
                      positions=((10, 0, 0), (10, 1, 0)))
        scan = self.scan((source,), segments={'Foot': ((0, 0, 0), (0, 0, 0))})
        self.assertTrue(scan.ok, scan.to_dict())
        self.assertEqual(scan.records[0].status, 'DISTANT')
        self.assertIsNone(scan.records[0].distance_ratio)
        self.assertFalse(scan.records[0].removable)
        self.assertTrue(scan.records[0].reason)
        self.assertEqual(tuple(scan.changes), ())

    def test_invalid_scan_geometry_refuses_all_records_without_mutating_inputs(self):
        hand, shoes = self.references()
        corrupt = mesh('Shoes', shoes.weights, positions=shoes.world_positions,
                       edges=((0, 99),), triangles=shoes.triangles)
        before = repr((hand, corrupt))
        scan = self.scan((hand, corrupt))
        self.assertFalse(scan.ok)
        self.assertTrue(scan.errors)
        self.assertEqual(tuple(scan.changes), ())
        self.assertEqual(repr((hand, corrupt)), before)


class AllowedBoneTests(unittest.TestCase):
    def plan(self, source, allowed=('A', 'B'), **options):
        return SUBJECT.plan_allowed_bones(source, {'A', 'B', 'Bad', 'Locked'}, set(allowed), **options)

    def assert_rejected(self, plan):
        self.assertFalse(plan.ok, plan.to_dict())
        self.assertEqual(tuple(plan.changes), ())
        self.assertTrue(plan.errors)

    def test_all_bones_kept_is_exact_noop_without_normalizing_authored_paint(self):
        source = mesh('Mesh', ({'A': .1875, 'Bad': .625, 'Helper': .5},
                               {'B': .8125, 'Locked': .25, 'Bad': 0.0}, {'Helper': .75}))
        before = copy.deepcopy(source)
        plan = self.plan(source, {'A', 'B', 'Bad', 'Locked'})
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(plan.mode, 'ALLOWED_BONES')
        self.assertEqual(tuple(plan.changes), ())
        self.assertEqual(source, before)

    def test_remove_unchecked_bone_preserves_surviving_raw_values_and_helpers(self):
        source = mesh('Mesh', ({'A': .2, 'B': .1, 'Bad': .7, 'Locked': .125, 'Helper': .9375},
                               {'A': .125, 'Bad': .8125, 'Mask': 0.0}))
        before = copy.deepcopy(source)
        plan = self.plan(source, {'A', 'B', 'Locked'}, locked_names={'Locked', 'Helper'})
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan), {
            ('Mesh', 0): {'A': .2, 'B': .1, 'Locked': .125, 'Helper': .9375},
            ('Mesh', 1): {'A': .125, 'Mask': 0.0},
        })
        self.assertEqual(source, before)

    def test_tiny_allowed_positive_paint_remains_valid_and_is_not_filled_or_normalized(self):
        source = mesh('Mesh', ({'A': 1e-20, 'Bad': .875, 'Helper': .25}, {'A': .5}))
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan), {('Mesh', 0): {'A': 1e-20, 'Helper': .25}})
        self.assertEqual(plan.stats['filled_vertices'], 0)

    def test_newly_unbound_vertices_inherit_only_allowed_local_influences(self):
        source = mesh('Mesh', ({'A': .3, 'B': .1, 'Bad': .6}, {'Bad': 1.0, 'Helper': .75},
                               {'Bad': 1.0, 'A': 0.0}, {'B': .8125}))
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        values = change_map(plan)
        self.assertEqual(values[('Mesh', 0)], {'A': .3, 'B': .1})
        self.assertEqual(set(values[('Mesh', 1)]), {'A', 'B', 'Helper'})
        self.assertAlmostEqual(values[('Mesh', 1)]['A'], .75)
        self.assertAlmostEqual(values[('Mesh', 1)]['B'], .25)
        self.assertEqual(values[('Mesh', 1)]['Helper'], .75)
        self.assertEqual(values[('Mesh', 2)], {'A': 0.0, 'B': 1.0})
        self.assertTrue(all('Bad' not in weights for weights in values.values()))
        self.assertNotIn(('Mesh', 3), values)

    def test_separate_component_with_no_allowed_donor_refuses_every_write(self):
        source = mesh('Mesh', ({'A': .75}, {'Bad': 1.0}, {'Bad': 1.0}, {'Bad': 1.0}),
                      positions=((0, 0, 0), (1, 0, 0), (0, .001, 0), (1, .001, 0)),
                      edges=((0, 1), (2, 3)))
        before = copy.deepcopy(source)
        self.assert_rejected(self.plan(source))
        self.assertEqual(source, before)

    def test_locked_unchecked_positive_or_zero_membership_refuses_atomically(self):
        for value in (1.0, 0.0):
            with self.subTest(value=value):
                source = mesh('Mesh', ({'A': .5, 'Bad': .5}, {'A': .125, 'Bad': value}))
                before = copy.deepcopy(source)
                self.assert_rejected(self.plan(source, locked_names={'Bad'}))
                self.assertEqual(source, before)

    def test_selected_vertex_scope_does_not_clear_unchecked_groups_elsewhere(self):
        source = mesh('Mesh', ({'A': .5, 'Bad': .5}, {'Bad': 1.0}, {'B': .3, 'Bad': .7}))
        plan = self.plan(source, targets={1})
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan), {('Mesh', 1): {'A': 1.0}})
        self.assertEqual(source.weights[0], {'A': .5, 'Bad': .5})
        self.assertEqual(source.weights[2], {'B': .3, 'Bad': .7})

    def test_preexisting_empty_vertices_stay_empty_but_unchecked_zero_membership_can_be_removed(self):
        source = mesh('Mesh', ({'A': 1.0}, {'Helper': .5, 'Bad': 0.0}, {}, {'Bad': 1.0}))
        plan = self.plan(source)
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan), {('Mesh', 1): {'Helper': .5}, ('Mesh', 3): {'A': 1.0}})
        self.assertNotIn(('Mesh', 2), change_map(plan))

    def test_locked_only_nearer_seed_is_not_a_transferable_allowed_donor(self):
        source = mesh('Mesh', ({'Locked': 1.0}, {'Bad': 1.0, 'Locked': 0.0}, {'A': .625}))
        plan = self.plan(source, {'A', 'Locked'}, locked_names={'Locked'})
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(change_map(plan), {('Mesh', 1): {'A': 1.0, 'Locked': 0.0}})

    def test_empty_vertex_scope_is_noop_and_invalid_allowlist_is_rejected(self):
        source = mesh('Mesh', ({'Bad': 1.0}, {'Bad': 1.0}))
        plan = self.plan(source, targets=set())
        self.assertTrue(plan.ok, plan.to_dict())
        self.assertEqual(tuple(plan.changes), ())
        self.assert_rejected(self.plan(source, {'Missing'}))
        self.assert_rejected(self.plan(source, targets={2}))


def main():
    global SUBJECT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--addon-parent', type=Path, default=ROOT / 'addons')
    parser.add_argument('--report', type=Path)
    options = parser.parse_args()
    SUBJECT = load_subject(options.addon_parent)
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                               for cls in (FillTests, IslandTests, InspectionTests, AllowedBoneTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        'schema': 'character-designer.weight-cleanup.pure-tests/1',
        'module': str(SUBJECT.__file__),
        'module_sha256': hashlib.sha256(Path(SUBJECT.__file__).read_bytes()).hexdigest(),
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'blender_opened': False, 'unity_opened': False, 'artist_opened': False,
        'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
        'failures': [{'test': str(test), 'traceback': detail} for test, detail in result.failures],
        'errors': [{'test': str(test), 'traceback': detail} for test, detail in result.errors],
    }
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == '__main__':
    main()
else:
    SUBJECT = load_subject(ROOT / 'addons')
