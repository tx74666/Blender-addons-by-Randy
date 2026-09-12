"""Pure profile/range checks; run with ordinary Python, without bpy."""
import copy
import importlib.util
import math
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[1] / "addons" / "character_designer" / "forearm_twist_profile.py"
SPEC = importlib.util.spec_from_file_location("forearm_twist_profile", PATH)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


def rings():
    positions = [0.0, .08, .21, .29, .62, .93, 1.0]
    ratios = [0.0, .16, .23, .36, .55, .78, 1.0]
    return [{"position": position, "ratio": ratio, "vertices": [index * 4 + value for value in range(4)], "note": str(index)}
            for index, (position, ratio) in enumerate(zip(positions, ratios))]


class ProfileTests(unittest.TestCase):
    def test_default_ease_uses_original_rest_position(self):
        self.assertEqual(profile.ease_ratio(-.2), 0.0)
        self.assertEqual(profile.ease_ratio(1.2), 1.0)
        for t in (0.0, .08, .21, .29, .62, .93, 1.0):
            self.assertAlmostEqual(profile.ease_ratio(t), .6 * t + .4 * t * t * (3 - 2 * t))
            self.assertAlmostEqual(profile.ease_ratio(t, 0), t)
            self.assertAlmostEqual(profile.ease_ratio(t, 1), t * t * (3 - 2 * t))

    def test_explicit_default_preserves_anchors_and_unselected_loops(self):
        captured = rings()
        original = copy.deepcopy(captured)
        softened = profile.apply_default(captured, 1, 5, .4)
        self.assertEqual(captured, original)
        self.assertEqual([ring["position"] for ring in softened], [ring["position"] for ring in captured])
        for index in (0, 1, 5, 6):
            self.assertEqual(softened[index], captured[index])
        for index in (2, 3, 4):
            expected_t = profile.ease_ratio((captured[index]["position"] - .08) / (.93 - .08))
            self.assertAlmostEqual(softened[index]["ratio"], .16 + expected_t * (.78 - .16))
            self.assertEqual(softened[index]["vertices"], captured[index]["vertices"])
        linear = profile.apply_default(captured, 1, 5, 0)
        self.assertAlmostEqual(linear[2]["ratio"], .16 + ((.21 - .08) / (.93 - .08)) * (.78 - .16))
        self.assertNotAlmostEqual(linear[2]["ratio"], .16 + .25 * (.78 - .16))
        # A later softness value has no observer and cannot edit prior data.
        profile.ease_ratio(.29, .9)
        self.assertEqual(captured, original)
        softened[2]["vertices"].append(999)
        self.assertEqual(captured, original)

    def test_default_and_interpolation_do_not_overshoot_authored_ratios(self):
        captured = rings()
        captured[1]["ratio"], captured[5]["ratio"] = .81, .24
        for k in (0, .4, 1):
            changed = profile.apply_default(captured, 1, 5, k)
            for ring in changed[1:6]:
                self.assertGreaterEqual(ring["ratio"], .24)
                self.assertLessEqual(ring["ratio"], .81)
            for index in range(1, 5):
                left, right = changed[index:index + 2]
                for fraction in (0, .1, .5, .9, 1):
                    p = left["position"] + fraction * (right["position"] - left["position"])
                    value = profile.interpolate_ratio(changed, p)
                    self.assertGreaterEqual(value, min(left["ratio"], right["ratio"]) - 1e-15)
                    self.assertLessEqual(value, max(left["ratio"], right["ratio"]) + 1e-15)

    def test_strict_bounded_fade_is_entirely_inside_selected_positions(self):
        captured = rings()
        start, end, transition = .21, .93, .1
        width = (end - start) * transition
        for t in (-10, 0, start - 1e-9, start, end, end + 1e-9, 10):
            self.assertEqual(profile.range_influence(t, captured, 2, 5, transition), 0.0)
        self.assertAlmostEqual(profile.range_influence(start + width * .5, captured, 2, 5), .5)
        self.assertAlmostEqual(profile.range_influence(end - width * .5, captured, 2, 5), .5)
        for t in (start + width, (start + end) / 2, end - width):
            self.assertAlmostEqual(profile.range_influence(t, captured, 2, 5), 1.0)
        self.assertEqual(profile.range_influence(start, captured, 2, 5, 0), 0.0)
        self.assertEqual(profile.range_influence(start + 1e-9, captured, 2, 5, 0), 1.0)
        self.assertAlmostEqual(profile.range_influence((start + end) / 2, captured, 2, 5, .5), 1.0)
        for step in range(101):
            value = profile.range_influence(start + (end - start) * step / 100, captured, 2, 5)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_negative_angles_multiply_the_physical_profile_once(self):
        captured = rings()
        p = .21 + (.29 - .21) * .25
        ratio = .23 + (.36 - .23) * .25
        for angle in (-math.tau, -math.pi / 2, 0, math.pi / 2, math.tau):
            self.assertAlmostEqual(profile.profile_angle(captured, p, angle), ratio * angle)
        self.assertEqual(profile.interpolate_ratio(captured, -.3), 0.0)
        self.assertEqual(profile.interpolate_ratio(captured, 1.2), 1.0)
        for ring in captured:
            self.assertEqual(profile.interpolate_ratio(captured, ring["position"]), ring["ratio"])

    def test_batch_indices_are_absolute_distinct_and_clamped(self):
        self.assertEqual(profile.batch_indices(2, 3, 2, 8), (2, 4, 6))
        self.assertEqual(profile.batch_indices(2, 99, 2, 8), (2, 4, 6, 8))
        self.assertEqual(profile.batch_indices(2, 99, 3, 8), (2, 5, 8))
        self.assertEqual(profile.batch_indices(8, 9, 2, 8), (8,))
        self.assertEqual(profile.batch_indices(3, 9, 9, 8), (3,))
        captured = rings()
        original = copy.deepcopy(captured)
        chosen = profile.batch_indices(1, 2, 2, 5)
        for index in chosen:
            captured[index]["ratio"] = .44
        # Repeating a batch writes the same absolute share, without accumulating.
        for index in profile.batch_indices(1, 2, 2, 5):
            captured[index]["ratio"] = .44
        self.assertEqual(captured[1]["ratio"], .44)
        self.assertEqual(captured[3]["ratio"], .44)
        for index in set(range(len(captured))) - set(chosen):
            self.assertEqual(captured[index], original[index])

    def test_explicit_record_range_preserves_current_and_legacy_input(self):
        record = {"rings": rings(), "current": 3, "other": "saved"}
        before = copy.deepcopy(record)
        with self.assertRaises(profile.ForearmProfileError):
            profile.record_range(record)
        changed = profile.with_range(record, 1, 5)
        self.assertEqual(profile.record_range(changed), (1, 5))
        self.assertEqual(changed["current"], 3)
        self.assertEqual(record, before)
        self.assertEqual(changed["rings"], record["rings"])

    def test_manual_positions_beyond_forearm_use_physical_spacing(self):
        captured = [{"position": t, "ratio": r} for t, r in ((1.1, .3), (1.12, .7), (1.3, .9))]
        changed = profile.apply_default(captured, 0, 2)
        self.assertAlmostEqual(changed[1]["ratio"], .3 + profile.ease_ratio(.1) * .6)
        self.assertEqual(profile.range_influence(1.31, captured, 0, 2), 0.0)

    def test_malformed_nonfinite_and_invalid_ranges_are_refused(self):
        for value in (float("nan"), float("inf"), -float("inf"), True, "0.4", None, 10 ** 500):
            with self.subTest(value=str(value)[:30]):
                with self.assertRaises(profile.ForearmProfileError):
                    profile.ease_ratio(value)
        for bad in ([], [{}], [{"position": 0, "ratio": 0}, {"position": 0, "ratio": 1}],
                    [{"position": 0, "ratio": 0}, {"position": 1, "ratio": float("nan")}],
                    [{"position": 0, "ratio": 0}, {"position": 1, "ratio": 1.1}]):
            with self.assertRaises(profile.ForearmProfileError):
                profile.profile_knots(bad)
        for start, end in ((-1, 2), (2, 2), (4, 2), (0, 7), (True, 2), (0, 2.0)):
            with self.assertRaises(profile.ForearmProfileError):
                profile.validate_range(rings(), start, end)
        for transition in (-.1, .5001, float("nan")):
            with self.assertRaises(profile.ForearmProfileError):
                profile.range_influence(.3, rings(), 0, 6, transition)
        for args in ((-1, 2, 1, 4), (4, 2, 1, 3), (1, 0, 1, 4), (1, 2, 0, 4), (1, 2.0, 1, 4)):
            with self.assertRaises(profile.ForearmProfileError):
                profile.batch_indices(*args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
