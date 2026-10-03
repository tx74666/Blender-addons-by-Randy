"""Pure topology correspondence checks; no Blender process or scene writes."""

import importlib.util
from pathlib import Path
import random
import sys
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "addons" / "character_designer" / "symmetry_pairs.py"
SPEC = importlib.util.spec_from_file_location("character_designer_symmetry_pairs_test", SOURCE)
symmetry = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = symmetry
SPEC.loader.exec_module(symmetry)


def fixture():
    """Quad strip with different bilateral topology at two longitudinal rows."""

    coordinates = [(x, y, 0.0) for y in range(4) for x in (-2.0, -1.0, 0.0, 1.0, 2.0)]
    faces = [
        (5 * y + x, 5 * y + x + 1, 5 * (y + 1) + x + 1, 5 * (y + 1) + x)
        for y in range(3) for x in range(4)
    ]
    coordinates.extend([(-2.5, 0.5, 0.0), (2.5, 0.5, 0.0)])
    faces.extend([(0, 5, 20), (4, 21, 9)])
    edges = {tuple(sorted((a, b))) for face in faces for a, b in zip(face, face[1:] + face[:1])}
    coordinates.extend([(-3.0, 1.0, 0.0), (-4.0, 1.0, 0.0), (3.0, 1.0, 0.0), (4.0, 1.0, 0.0)])
    edges.update({(5, 22), (22, 23), (9, 24), (24, 25)})
    pairs = ((0, 4), (1, 3), (5, 9), (6, 8), (10, 14), (11, 13), (15, 19), (16, 18), (20, 21), (22, 24), (23, 25))
    return coordinates, sorted(edges), faces, pairs, (2, 7, 12, 17)


class SymmetryPairsTests(unittest.TestCase):
    def test_topology_pairs_ignore_geometric_drift(self):
        coordinates, edges, faces, expected_pairs, expected_centers = fixture()
        for index, point in enumerate(coordinates):
            if point[0] > 0.0:
                coordinates[index] = (point[0] + 0.8, point[1] - 0.71, point[2] + 2.0)
        result = symmetry.build_vertex_pairs(coordinates, edges, faces)
        self.assertEqual(result.pairs, expected_pairs)
        self.assertEqual(result.centerline, expected_centers)
        self.assertFalse(result.unmatched)
        self.assertTrue(result.converged)
        self.assertEqual(result.matched_vertices, len(coordinates))

    def test_isolated_nearest_candidate_is_never_a_fallback(self):
        coordinates, edges, faces, expected_pairs, _ = fixture()
        coordinates.extend([coordinates[0], coordinates[4]])
        result = symmetry.build_vertex_pairs(coordinates, edges, faces)
        self.assertEqual(result.pairs, expected_pairs)
        self.assertEqual(result.unmatched, (26, 27))

    def test_ambiguous_grid_stays_unmatched_at_exact_geometry(self):
        coordinates = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)]
        result = symmetry.build_vertex_pairs(coordinates, ((0, 1), (1, 2), (2, 3), (3, 0)), ((0, 1, 2, 3),))
        self.assertEqual(result.pairs, ())
        self.assertEqual(result.centerline, ())
        self.assertEqual(result.ambiguous, (0, 1, 2, 3))
        self.assertEqual(result.unmatched, (0, 1, 2, 3))

    def test_fixed_seam_resolves_repeated_topology_without_nearest_matching(self):
        coordinates, _, faces, _, centers = fixture()
        coordinates = coordinates[:20]
        faces = faces[:12]
        edges = sorted({tuple(sorted((a, b))) for face in faces for a, b in zip(face, face[1:] + face[:1])})
        for index, point in enumerate(coordinates):
            if point[0] > 0.0:
                coordinates[index] = (point[0] + 0.31, point[1] - 0.6, point[2] + 0.4)
        result = symmetry.build_vertex_pairs(coordinates, edges, faces, centerline_tolerance=0.0)
        self.assertEqual(len(result.pairs), 8)
        self.assertEqual(result.centerline, centers)
        self.assertFalse(result.unmatched)
        self.assertFalse(result.ambiguous)

    def test_local_topology_damage_does_not_invalidate_the_entire_surface(self):
        coordinates, edges, faces, expected_pairs, centers = fixture()
        coordinates.extend([(-3.0, -1.0, 0.0), (-3.0, -2.0, 0.0)])
        edges.extend([(0, 26), (26, 27)])
        result = symmetry.build_vertex_pairs(coordinates, edges, faces)
        expected_surface = {pair for pair in expected_pairs if pair[0] < 22 and pair != (0, 4)}
        self.assertEqual(set(result.pairs), expected_surface)
        self.assertEqual(result.centerline, centers)
        self.assertTrue({0, 4, 26, 27}.issubset(result.unmatched))

    def test_center_edge_with_same_side_faces_is_not_a_seam_seed(self):
        coordinates = [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.2, 0.0), (-1.0, 0.8, 0.0)]
        faces = [(0, 1, 2), (1, 0, 3)]
        edges = [(0, 1), (1, 2), (0, 2), (0, 3), (1, 3)]
        result = symmetry.build_vertex_pairs(coordinates, edges, faces)
        self.assertEqual(result.pairs, ())
        self.assertEqual(result.centerline, ())
        self.assertEqual(result.unmatched, (0, 1, 2, 3))

    def test_conflicting_walks_reject_all_claims_independent_of_indices(self):
        coordinates, edges, faces, expected_pairs, _ = fixture()
        faces[7] = (8, 9, 19, 18)
        edges = sorted(set(edges) | {tuple(sorted((a, b))) for face in faces for a, b in zip(face, face[1:] + face[:1])})
        result = symmetry.build_vertex_pairs(coordinates, edges, faces)
        self.assertTrue(set(result.pairs).issubset(expected_pairs))
        self.assertTrue(any("failed" in message for message in result.diagnostics))
        permutation = list(range(len(coordinates)))
        random.Random(927).shuffle(permutation)
        changed_coordinates = [None] * len(coordinates)
        for index, target in enumerate(permutation):
            changed_coordinates[target] = coordinates[index]
        changed_edges = [(permutation[b], permutation[a]) for a, b in reversed(edges)]
        changed_faces = [tuple(permutation[index] for index in reversed(face)) for face in reversed(faces)]
        changed = symmetry.build_vertex_pairs(changed_coordinates, changed_edges, changed_faces)
        self.assertEqual(set(changed.pairs), {(permutation[a], permutation[b]) for a, b in result.pairs})
        self.assertEqual(set(changed.unmatched), {permutation[index] for index in result.unmatched})

    def test_centerline_requires_unique_topology_and_tiny_tolerance(self):
        coordinates, edges, faces, _, expected_centers = fixture()
        coordinates[2] = (2.0e-7, coordinates[2][1], coordinates[2][2])
        coordinates[7] = (0.02, coordinates[7][1], coordinates[7][2])
        result = symmetry.build_vertex_pairs(coordinates, edges, faces, centerline_tolerance=1.0e-6)
        self.assertEqual(result.centerline, tuple(index for index in expected_centers if index != 7))
        self.assertEqual(result.unmatched, (7,))

    def test_same_x_side_candidate_is_not_qualified(self):
        coordinates, edges, faces, expected_pairs, _ = fixture()
        coordinates[4] = (-0.5, 0.0, 0.0)
        result = symmetry.build_vertex_pairs(coordinates, edges, faces)
        self.assertEqual(result.pairs, expected_pairs[1:])
        self.assertEqual(result.unmatched, (0, 4))

    def test_vertex_edge_and_face_order_do_not_choose_pairs(self):
        coordinates, edges, faces, expected_pairs, expected_centers = fixture()
        randomizer = random.Random(7943)
        permutation = list(range(len(coordinates)))
        randomizer.shuffle(permutation)
        changed_coordinates = [None] * len(coordinates)
        for index, target in enumerate(permutation):
            changed_coordinates[target] = coordinates[index]
        changed_edges = [(permutation[b], permutation[a]) for a, b in reversed(edges)]
        changed_faces = [tuple(permutation[index] for index in reversed(face)) for face in reversed(faces)]
        result = symmetry.build_vertex_pairs(changed_coordinates, changed_edges, changed_faces)
        self.assertEqual(set(result.pairs), {(permutation[a], permutation[b]) for a, b in expected_pairs})
        self.assertEqual(set(result.centerline), {permutation[index] for index in expected_centers})
        self.assertFalse(result.unmatched)

    def test_broken_face_relation_never_invents_a_new_pair(self):
        coordinates, edges, faces, expected_pairs, _ = fixture()
        result = symmetry.build_vertex_pairs(coordinates, edges, faces[:-1])
        self.assertTrue(set(result.pairs).issubset(set(expected_pairs)))
        self.assertIn(20, result.unmatched)
        self.assertIn(21, result.unmatched)

    def test_iteration_safety_limit_fails_closed(self):
        coordinates, edges, faces, _, _ = fixture()
        original = symmetry.MAX_REFINEMENT_ITERATIONS
        try:
            symmetry.MAX_REFINEMENT_ITERATIONS = 1
            result = symmetry.build_vertex_pairs(coordinates, edges)
        finally:
            symmetry.MAX_REFINEMENT_ITERATIONS = original
        self.assertFalse(result.converged)
        self.assertEqual(result.pairs, ())
        self.assertEqual(result.unmatched, tuple(range(len(coordinates))))

    def test_invalid_topology_and_coordinates_are_rejected(self):
        coordinates, edges, faces, _, _ = fixture()
        for changed_edges, changed_faces in ((edges + [edges[0]], faces), (edges[1:], faces), (edges, faces + [faces[0]])):
            with self.subTest(changed_edges=changed_edges[-1], face_count=len(changed_faces)):
                with self.assertRaises(symmetry.SymmetryPairError):
                    symmetry.build_vertex_pairs(coordinates, changed_edges, changed_faces)
        coordinates[0] = (float("nan"), 0.0, 0.0)
        with self.assertRaises(symmetry.SymmetryPairError):
            symmetry.build_vertex_pairs(coordinates, edges, faces)
        with self.assertRaises(symmetry.SymmetryPairError):
            symmetry.build_vertex_pairs([], [], centerline_tolerance=-1.0)


if __name__ == "__main__":
    unittest.main()
