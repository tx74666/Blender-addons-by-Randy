"""Independent native-FK geometry checks for common forearm plus wrist twist.

Run in an isolated Blender --background --factory-startup process.  This suite
imports only the math module, creates no scene data, and never opens an artist
file.  Expected pure-axial points use an analytic circular arc, not the tested
correction functions.
"""

import importlib.util
import math
from pathlib import Path
import unittest

from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "forearm_follow_math", ROOT / "addons/character_designer/forearm_twist_math.py")
geometry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(geometry)

AXIS = Vector((0.0, 1.0, 0.0))
PIVOT = Vector((0.0, 1.0, 0.0))
POINT = Vector((0.31, 0.74, -0.19))
EPSILON = 2.0e-6


def y_arc(point, angle):
    """Independent analytic rotation around the fixture's longitudinal axis."""
    cosine, sine = math.cos(angle), math.sin(angle)
    return Vector((cosine * point.x + sine * point.z, point.y,
                   -sine * point.x + cosine * point.z))


def axial_transforms(alpha, beta, whole=None):
    whole = Matrix.Identity(4) if whole is None else whole
    lower = whole @ Matrix.Rotation(alpha, 4, "Y")
    return {"upper": whole.copy(), "lower": lower,
            "hand": lower @ Matrix.Rotation(beta, 4, "Y")}


def skin_matrix(transforms, weights):
    # The independent actual LBS expression is used to verify inverse output.
    return Matrix(tuple(tuple(sum(weight * transforms[name][row][column]
                                  for name, weight in weights.items())
                              for column in range(4)) for row in range(4)))


class FollowTwistMathTests(unittest.TestCase):
    def close(self, actual, expected, message=""):
        self.assertLess((Vector(actual) - Vector(expected)).length, EPSILON, message)

    def desired(self, transforms, weights, ratio, **kwargs):
        return geometry.desired_vertex(POINT, transforms, weights, "lower", "hand",
                                       AXIS, PIVOT, ratio, **kwargs)

    def corrected(self, transforms, weights, ratio, **kwargs):
        return geometry.corrected_vertex(POINT, transforms, weights, "lower", "hand",
                                         AXIS, PIVOT, ratio, **kwargs)

    def test_wrist_twist_uses_weight_independent_circular_arc(self):
        beta = math.radians(72.0)
        transforms = axial_transforms(0.0, beta)
        for share in (0.0, 0.17, 0.5, 0.83, 1.0):
            weights = {"lower": 1.0 - share, "hand": share}
            for ratio in (0.0, 0.21, 0.64, 1.0):
                with self.subTest(share=share, ratio=ratio):
                    expected = y_arc(POINT, ratio * beta)
                    self.close(self.desired(transforms, weights, ratio), expected)
                    self.close(self.desired(transforms, weights, ratio, angle=beta), expected)
                    self.close(self.corrected(transforms, weights, ratio, angle=beta),
                               self.corrected(transforms, weights, ratio))

    def test_forearm_and_hand_common_roll_is_kept_rigidly(self):
        alpha = math.radians(86.0)
        transforms = axial_transforms(alpha, 0.0)
        weights = {"lower": 0.62, "hand": 0.38}
        # All ring ratios must retain the same common roll. Redistributing it
        # would manufacture a reverse twist near the first captured boundary.
        for ratio in (0.0, 0.25, 0.75, 1.0):
            expected = y_arc(POINT, alpha)
            self.close(self.desired(transforms, weights, ratio), expected)
            corrected = self.corrected(transforms, weights, ratio)
            self.close(corrected, POINT)
            self.close(skin_matrix(transforms, weights) @ corrected, expected)

    def test_combined_rolls_have_weight_independent_circular_arc(self):
        whole = (Matrix.Translation((0.3, -0.8, 0.6))
                 @ Matrix.Rotation(0.43, 4, "Z") @ Matrix.Rotation(-0.29, 4, "X"))
        for alpha, beta in ((38.0, 47.0), (-64.0, 22.0), (51.0, -51.0)):
            alpha, beta = math.radians(alpha), math.radians(beta)
            transforms = axial_transforms(alpha, beta, whole)
            for share in (0.0, 0.13, 0.5, 0.79, 1.0):
                weights = {"lower": 1.0 - share, "hand": share}
                for ratio in (0.0, 0.32, 0.81, 1.0):
                    with self.subTest(alpha=alpha, beta=beta, share=share, ratio=ratio):
                        expected = whole @ y_arc(POINT, alpha + ratio * beta)
                        self.close(self.desired(transforms, weights, ratio), expected)
                        self.close(self.desired(transforms, weights, ratio, angle=beta), expected)
                        corrected = self.corrected(transforms, weights, ratio, angle=beta)
                        self.close(skin_matrix(transforms, weights) @ corrected, expected)

    def test_whole_arm_rigid_motion_and_uniform_scale_remain(self):
        whole = (Matrix.Translation((-0.4, 0.7, 0.2))
                 @ Matrix.Rotation(1.1, 4, "X") @ Matrix.Rotation(-0.68, 4, "Z")
                 @ Matrix.Scale(1.7, 4))
        transforms = {name: whole.copy() for name in ("upper", "lower", "hand")}
        weights = {"lower": 0.4, "hand": 0.6}
        for ratio in (0.0, 0.5, 1.0):
            self.close(self.desired(transforms, weights, ratio), whole @ POINT)
            self.close(self.corrected(transforms, weights, ratio), POINT)

    def test_pure_wrist_and_elbow_swing_do_not_become_twist(self):
        whole = Matrix.Translation((0.2, -0.1, 0.3)) @ Matrix.Rotation(0.4, 4, "Z")
        lower = whole @ Matrix.Rotation(math.radians(52.0), 4, "X")
        hand = lower @ Matrix.Rotation(math.radians(-33.0), 4, "Z")
        transforms = {"upper": whole, "lower": lower, "hand": hand}
        weights = {"lower": 0.3, "hand": 0.7}
        actual = skin_matrix(transforms, weights) @ POINT
        for ratio in (0.0, 0.35, 1.0):
            self.close(self.desired(transforms, weights, ratio), actual)
            self.close(self.corrected(transforms, weights, ratio), POINT)

    def test_wrist_swing_and_other_influences_are_retained(self):
        alpha, beta = math.radians(31.0), math.radians(-18.0)
        whole = Matrix.Translation((0.2, -0.1, 0.3)) @ Matrix.Rotation(0.4, 4, "Z")
        lower = whole @ Matrix.Rotation(0.37, 4, "X") @ Matrix.Rotation(alpha, 4, "Y")
        hand = lower @ Matrix.Rotation(-0.28, 4, "X") @ Matrix.Rotation(beta, 4, "Y")
        other = Matrix.Translation((-0.3, 0.2, 0.1)) @ Matrix.Rotation(0.51, 4, "Z")
        transforms = {"upper": whole, "lower": lower, "hand": hand, "other": other}
        weights = {"lower": 0.33, "hand": 0.47, "other": 0.2}
        original_matrices = {name: tuple(value for row in matrix for value in row)
                             for name, matrix in transforms.items()}
        for ratio in (0.0, 0.46, 1.0):
            # Evaluate each articulated contribution independently. In particular
            # hand's complete swing/translation is unchanged at the wrist end.
            expected = (weights["lower"] * (lower @ y_arc(POINT, ratio * beta))
                        + weights["hand"] * (hand @ y_arc(POINT, (ratio - 1.0) * beta))
                        + weights["other"] * (other @ POINT))
            self.close(self.desired(transforms, weights, ratio), expected)
            corrected = self.corrected(transforms, weights, ratio)
            self.close(skin_matrix(transforms, weights) @ corrected, expected)
        self.assertEqual(original_matrices,
                         {name: tuple(value for row in matrix for value in row)
                          for name, matrix in transforms.items()})

    def test_repeated_evaluation_starts_from_unchanged_input(self):
        transforms = axial_transforms(math.radians(45.0), math.radians(23.0))
        weights = {"lower": 0.37, "hand": 0.63}
        first = self.corrected(transforms, weights, 0.42)
        before = tuple(POINT)
        for _ in range(20):
            self.close(self.corrected(transforms, weights, 0.42), first)
        self.assertEqual(tuple(POINT), before)
        self.assertEqual(weights, {"lower": 0.37, "hand": 0.63})

    def test_invalid_inputs_keep_existing_guards(self):
        transforms = axial_transforms(0.2, 0.3)
        weights = {"lower": 0.5, "hand": 0.5}
        for kwargs in ({"angle": math.inf}, {"angle": math.nan}):
            with self.subTest(kwargs=kwargs), self.assertRaises(geometry.TwistMathError):
                self.desired(transforms, weights, 0.5, **kwargs)
        for lower in (Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0)),
                      Matrix.Diagonal((2.0, 1.0, 1.0, 1.0))):
            with self.assertRaises(geometry.TwistMathError):
                self.desired({**transforms, "lower": lower}, weights, 0.5)

    def test_undefined_half_turn_swing_and_singular_skinning_are_rejected(self):
        weights = {"lower": 0.5, "hand": 0.5}
        perpendicular = {"upper": Matrix.Identity(4),
                         "lower": Matrix.Identity(4),
                         "hand": Matrix.Rotation(math.pi, 4, "X")}
        with self.assertRaises(geometry.TwistMathError):
            self.desired(perpendicular, weights, 0.5)
        singular = axial_transforms(0.0, math.pi)
        with self.assertRaises(geometry.TwistMathError):
            self.corrected(singular, weights, 0.5)
        # A common 180-degree axial turn has no opposing LBS transforms. The
        # correction is zero; there is no relative wrist angle to redistribute.
        common = axial_transforms(math.pi, 0.0)
        output = self.corrected(common, weights, 0.5)
        self.close(output, POINT)
        self.close(skin_matrix(common, weights) @ output, y_arc(POINT, math.pi))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FollowTwistMathTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
