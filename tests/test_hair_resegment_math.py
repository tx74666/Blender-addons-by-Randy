"""Current-paint conservation and fixed-Rest interpolation, independent of bpy."""
import importlib.util
import math
from pathlib import Path
import random
import unittest

PATH = Path(__file__).resolve().parents[1] / 'addons/character_designer/hair_resegment_math.py'
spec = importlib.util.spec_from_file_location('hair_resegment_math_under_test', PATH)
maths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maths)


class HairWeightMigrationTests(unittest.TestCase):
    def test_uniform_identity_keeps_each_painted_influence(self):
        for count in (1, 4, 6, 7, 12):
            with self.subTest(count=count):
                weights = tuple((i + 1) / 32. for i in range(count))
                actual = maths.remap_weights(weights, [1.] * count, [1.] * count)
                for a, b in zip(weights, actual):
                    self.assertAlmostEqual(a, b, places=15)

    def test_six_seven_and_reduction_keep_authored_total_without_normalizing(self):
        randomizer = random.Random(714)
        for old_count, new_count in ((4, 6), (4, 7), (6, 7), (7, 6), (12, 1), (1, 12)):
            for total in (0., .173, .73, 1., 1.8):
                weights = [randomizer.random() for _ in range(old_count)]
                weights = [value * total / math.fsum(weights) for value in weights]
                actual = maths.remap_weights(weights, [1.] * old_count, [1.] * new_count)
                self.assertEqual(len(actual), new_count)
                self.assertTrue(all(value >= 0. for value in actual))
                self.assertAlmostEqual(math.fsum(actual), math.fsum(weights), places=14)

    def test_nonuniform_rest_intervals_use_physical_centers(self):
        self.assertEqual(maths.influence_map([1., 3.], [2., 2.]),
                         (((0, 1.),), ((0, .25), (1, .75))))
        actual = maths.remap_weights([.2, .8], [1., 3.], [2., 2.])
        self.assertAlmostEqual(actual[0], .4)
        self.assertAlmostEqual(actual[1], .6)

    def test_each_source_row_is_local_sparse_and_conservative(self):
        for old_count in (1, 6, 7, 12):
            for new_count in (1, 6, 7, 12):
                for row in maths.influence_map([1.] * old_count, [1.] * new_count):
                    self.assertIn(len(row), (1, 2))
                    self.assertAlmostEqual(math.fsum(fraction for _i, fraction in row), 1.)
                    if len(row) == 2:
                        self.assertEqual(row[1][0], row[0][0] + 1)

    def test_first_and_last_centers_clamp_to_end_bones(self):
        rows = maths.influence_map([1.] * 12, [1.] * 2)
        self.assertEqual(rows[0], ((0, 1.),))
        self.assertEqual(rows[-1], ((1, 1.),))

    def test_unrelated_paint_and_input_mapping_are_untouched(self):
        sentinel = object()
        current = {'oldA': .1, 'oldB': .25, 'Head': .65, 'BodyPaint': sentinel}
        before = dict(current)
        result = maths.remap_vertex_groups(current, ('oldA', 'oldB'), ('newA', 'newB', 'newC'),
                                          (1., 1.), (1., 1., 1.), locked_names=('Head',))
        self.assertEqual(current, before)
        self.assertEqual(result['Head'], .65)
        self.assertIs(result['BodyPaint'], sentinel)
        self.assertNotIn('oldA', result)
        self.assertAlmostEqual(sum(result.get(name, 0.) for name in ('newA', 'newB', 'newC')), .35)

    def test_missing_strand_memberships_remain_zero_without_creating_influences(self):
        current = {'Head': .8, 'Elsewhere': .2}
        self.assertEqual(maths.remap_vertex_groups(current, ['old'], ['new'], [1], [1]), current)

    def test_new_paint_is_used_instead_of_any_prebind_template(self):
        current = {'old0': .17, 'old1': .24, 'Head': .59}
        result = maths.remap_vertex_groups(current, ['old0', 'old1'], ['new0', 'new1', 'new2'],
                                          [1, 1], [1, 1, 1])
        self.assertEqual(result['Head'], .59)
        self.assertAlmostEqual(sum(value for name, value in result.items() if name.startswith('new')), .41)

    def test_same_named_groups_can_remain_owned_but_foreign_collision_refuses(self):
        result = maths.remap_vertex_groups({'a': .4, 'b': .6}, ['a', 'b'], ['a', 'b', 'c'],
                                          [1, 1], [1, 1, 1])
        self.assertAlmostEqual(sum(result.values()), 1.)
        with self.assertRaisesRegex(ValueError, 'occupied'):
            maths.remap_vertex_groups({'old': 1., 'foreign': .1}, ['old'], ['foreign'], [1], [1])

    def test_affected_locks_refuse_and_noop_retains_locked_zero_membership(self):
        for locked in ('old', 'new'):
            with self.subTest(locked=locked), self.assertRaisesRegex(ValueError, 'Unlock'):
                maths.remap_vertex_groups({'old': .4}, ['old'], ['new'], [1], [1], locked_names=[locked])
        current = {'old': 0., 'Head': 1.}
        self.assertEqual(maths.remap_vertex_groups(current, ['old'], ['old'], [1], [1],
                                                 locked_names=['old']), current)

    def test_invalid_lengths_weights_and_names_fail_before_output(self):
        for values in ([], [0.], [-1.], [math.inf], [math.nan], [True], ['1'], [1e308, 1e308]):
            with self.subTest(lengths=values), self.assertRaises(ValueError):
                maths.influence_map(values, [1.])
            with self.subTest(new_lengths=values), self.assertRaises(ValueError):
                maths.influence_map([1.], values)
        for value in (-.1, math.nan, math.inf, True, '1', None):
            with self.subTest(weight=value), self.assertRaises(ValueError):
                maths.remap_weights([value], [1.], [1.])
        with self.assertRaises(ValueError):
            maths.remap_weights([.1, .2], [1.], [1.])
        for names in ([], [''], ['a', 'a'], [True], 'old'):
            with self.subTest(names=names), self.assertRaises(ValueError):
                maths.remap_vertex_groups({'a': 1.}, names, ['new'], [1], [1])
        with self.assertRaises(ValueError):
            maths.remap_vertex_groups({'a': 1.}, ['a'], ['x', 'y'], [1], [1])

    def test_weight_sum_overflow_and_unrepresentable_center_refuse(self):
        with self.assertRaises(ValueError):
            maths.remap_weights([1e308, 1e308], [1, 1], [1])
        with self.assertRaises(ValueError):
            maths.influence_map([1., 1e-30], [1.])


class HairPolylineTests(unittest.TestCase):
    def test_physical_lengths_and_endpoint_samples(self):
        points = ((0., 0., 0.), (0., 3., 0.), (4., 3., 0.))
        self.assertEqual(maths.cumulative_lengths(points), (0., 3., 7.))
        self.assertEqual(maths.sample_polyline(points, [0., 3/7, 1.]), points)
        self.assertEqual(maths.sample_polyline(points, [5/7]), ((2., 3., 0.),))

    def test_fixed_rest_parameter_uses_old_segment_not_posed_length(self):
        posed = ((0., 0., 0.), (0., 2., 0.), (0., 10., 0.))
        self.assertEqual(maths.sample_polyline(posed, [.5], distances=[0., 1., 2.]), ((0., 2., 0.),))
        self.assertEqual(maths.sample_polyline(posed, [.5]), ((0., 5., 0.),))

    def test_six_and_seven_segment_boundary_sampling(self):
        for count in (1, 6, 7, 12):
            samples = maths.sample_polyline([(0., 0., 0.), (0., 7., 0.)],
                                            [i / count for i in range(count + 1)])
            self.assertEqual(len(samples), count + 1)
            self.assertEqual(samples[0], (0., 0., 0.))
            self.assertEqual(samples[-1], (0., 7., 0.))

    def test_unsorted_positions_and_supplied_collapsed_pose_are_supported(self):
        self.assertEqual(maths.sample_polyline([(0, 0, 0), (1, 0, 0)], [1, 0]),
                         ((1., 0., 0.), (0., 0., 0.)))
        self.assertEqual(maths.sample_polyline([(0, 0, 0), (0, 0, 0)], [.5], distances=[0, 1]),
                         ((0., 0., 0.),))

    def test_bad_points_distances_and_positions_refuse(self):
        for points in ([], [(0, 0, 0)], [(0, 0, 0), (0, 0, 0)],
                       [(0, 0, 0), (math.nan, 0, 0)], [(0, 0), (1, 0)], ['abc', 'def']):
            with self.subTest(points=points), self.assertRaises(ValueError):
                maths.cumulative_lengths(points)
        for distances in ([0], [1, 2], [0, 0], [0, math.inf], [0, True]):
            with self.subTest(distances=distances), self.assertRaises(ValueError):
                maths.sample_polyline([(0, 0, 0), (1, 0, 0)], [.5], distances=distances)
        for positions in ([-.1], [1.1], [math.nan], [True], ['.5'], None):
            with self.subTest(positions=positions), self.assertRaises(ValueError):
                maths.sample_polyline([(0, 0, 0), (1, 0, 0)], positions)


class HairVertexSpatialMigrationTests(unittest.TestCase):
    def test_straight_projection_and_endpoint_clamp(self):
        chain = [(0., 0., 0.), (10., 0., 0.)]
        self.assertAlmostEqual(maths.project_parameter((2., 3., 0.), chain), .2)
        self.assertEqual(maths.project_parameter((-1., 0., 0.), chain), 0.)
        self.assertEqual(maths.project_parameter((11., 0., 0.), chain), 1.)

    def test_nonuniform_rest_projection_uses_physical_arc(self):
        chain = [(0., 0., 0.), (0., 3., 0.), (4., 3., 0.)]
        self.assertAlmostEqual(maths.project_parameter((2., 4., 0.), chain), 5 / 7)
        self.assertAlmostEqual(maths.project_parameter((-1., 1.5, 0.), chain), 1.5 / 7)

    def test_adjacent_shared_corner_has_one_parameter(self):
        chain = [(0., 0., 0.), (0., 3., 0.), (4., 3., 0.)]
        self.assertAlmostEqual(maths.project_parameter((0., 3., 0.), chain), 3 / 7)

    def test_nonadjacent_self_intersection_and_equal_parallel_sections_refuse(self):
        crossed = [(0., 0., 0.), (2., 2., 0.), (0., 2., 0.), (2., 0., 0.)]
        folded = [(0., 0., 0.), (0., 2., 0.), (2., 2., 0.), (2., 0., 0.)]
        for chain, point in ((crossed, (1., 1., 0.)), (folded, (1., .5, 0.))):
            with self.subTest(chain=chain), self.assertRaisesRegex(ValueError, 'Ambiguous'):
                maths.project_parameter(point, chain)
        self.assertAlmostEqual(maths.project_parameter((.99, .5, 0.), folded), .5 / 6)

    def test_folded_back_adjacent_overlap_also_refuses_different_parameters(self):
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            maths.project_parameter((1., 0., 0.), [(0., 0., 0.), (2., 0., 0.), (0., 0., 0.)])

    def test_projection_stays_on_supplied_strand(self):
        point = (.5, .01, 0.)
        own_chain = [(0., 0., 0.), (1., 0., 0.)]
        other_chain = [(1., .01, 0.), (0., .01, 0.)]
        self.assertEqual(maths.project_parameter(point, own_chain), .5)
        self.assertEqual(maths.project_parameter(point, other_chain), .5)
        self.assertAlmostEqual(maths.project_parameter((.1, .01, 0.), own_chain), .1)
        self.assertAlmostEqual(maths.project_parameter((.1, .01, 0.), other_chain), .9)

    def test_single_bone_paint_distributes_along_all_six_and_seven_new_bones(self):
        for count in (6, 7, 12):
            covered = set()
            for i in range(101):
                t = maths.project_parameter((0., i / 100., 0.), [(0., 0., 0.), (0., 1., 0.)])
                actual = maths.remap_vertex_influence(.37, t, [1.] * count)
                self.assertEqual(len(actual), count)
                self.assertAlmostEqual(math.fsum(actual), .37, places=15)
                self.assertLessEqual(sum(value > 0. for value in actual), 2)
                covered.update(i for i, value in enumerate(actual) if value > 0.)
            self.assertEqual(covered, set(range(count)))

    def test_spatial_total_not_normalized_and_nonuniform_new_centers(self):
        result = maths.remap_vertex_influence(.37, .375, [1., 3.])
        self.assertEqual(result, (.185, .185))
        for total in (0., .41, 1., 1.5):
            for t in (0., .1, .3, .5, .7, .9, 1.):
                self.assertAlmostEqual(math.fsum(maths.remap_vertex_influence(total, t, [1.] * 7)), total)
        self.assertEqual(maths.remap_vertex_influence(.41, 0., [1.] * 7), (.41, 0., 0., 0., 0., 0., 0.))
        self.assertEqual(maths.remap_vertex_influence(.41, 1., [1.] * 7), (0., 0., 0., 0., 0., 0., .41))

    def test_spatial_invalid_inputs_refuse(self):
        for total in (-1., math.nan, math.inf, True, '1', None):
            with self.subTest(total=total), self.assertRaises(ValueError):
                maths.remap_vertex_influence(total, .5, [1., 1.])
        for t in (-.1, 1.1, math.nan, math.inf, True, '.5', None):
            with self.subTest(t=t), self.assertRaises(ValueError):
                maths.remap_vertex_influence(.4, t, [1., 1.])
        for point in ((0, 0), (0, 0, 0, 0), (math.nan, 0, 0), (True, 0, 0), None):
            with self.subTest(point=point), self.assertRaises(ValueError):
                maths.project_parameter(point, [(0, 0, 0), (1, 0, 0)])
        for chain in ([], [(0, 0, 0)], [(0, 0, 0), (0, 0, 0)]):
            with self.subTest(chain=chain), self.assertRaises(ValueError):
                maths.project_parameter((0, 0, 0), chain)


if __name__ == '__main__':
    unittest.main()
