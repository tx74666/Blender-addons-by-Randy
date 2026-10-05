"""Area/density recommendation checks; no Blender process or image allocation."""

import importlib.util
import math
from pathlib import Path
import unittest


MODULE = Path(__file__).resolve().parents[1] / "addons" / "random_realm_builder_exporter" / "rr_bake_resolution.py"
SPEC = importlib.util.spec_from_file_location("rr_bake_resolution", MODULE)
resolution = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolution)


class BakeResolutionTests(unittest.TestCase):
    def test_default_is_adjustable_balanced_density_with_estimated_utilization(self):
        result = resolution.recommend_bake_resolution(1)
        self.assertEqual(128.0, result["target_texels_per_meter"])
        self.assertEqual("BALANCED", result["density_preset"])
        self.assertEqual(0.7, result["uv_utilization"])
        self.assertTrue(result["uv_utilization_estimated"])
        self.assertEqual(math.ceil(math.sqrt(1 / 0.7) * 128), result["required_resolution"])
        self.assertEqual(512, result["recommended_resolution"])
        self.assertTrue(result["meets_target"])
        self.assertFalse(result["capped"])

    def test_required_density_rounds_up_to_a_supported_tier(self):
        result = resolution.recommend_bake_resolution(100, 128, uv_utilization=1)
        self.assertEqual(1280, result["required_resolution"])
        self.assertEqual(2048, result["recommended_resolution"])
        self.assertAlmostEqual(204.8, result["achieved_texels_per_meter"])
        self.assertTrue(result["meets_target"])

    def test_exact_boundaries_fit_and_real_shortfall_uses_the_next_tier(self):
        for size in resolution.SUPPORTED_SIZES:
            with self.subTest(size=size):
                area = 0.7 * (size / 128) ** 2
                result = resolution.recommend_bake_resolution(area)
                self.assertEqual(size, result["required_resolution"])
                self.assertEqual(size, result["recommended_resolution"])
                self.assertAlmostEqual(128, result["achieved_texels_per_meter"])
                self.assertTrue(result["meets_target"])
        result = resolution.recommend_bake_resolution((512.01 / 128) ** 2, 128, uv_utilization=1)
        self.assertEqual(513, result["required_resolution"])
        self.assertEqual(1024, result["recommended_resolution"])

    def test_large_floor_is_explicitly_capped_and_does_not_claim_target_reached(self):
        result = resolution.recommend_bake_resolution(10000)
        self.assertGreater(result["required_resolution"], 4096)
        self.assertEqual(4096, result["recommended_resolution"])
        self.assertTrue(result["capped"])
        self.assertFalse(result["meets_target"])
        self.assertLess(result["achieved_texels_per_meter"], result["target_texels_per_meter"])
        self.assertIn("Split", result["guidance"])
        self.assertIn("does not reach", result["guidance"])

    def test_one_supported_tier_can_fit_or_report_its_own_limit(self):
        fits = resolution.recommend_bake_resolution(1, supported_sizes=(1024,))
        self.assertEqual(1024, fits["recommended_resolution"])
        self.assertTrue(fits["meets_target"])
        capped = resolution.recommend_bake_resolution(100, supported_sizes=(1024,))
        self.assertEqual(1024, capped["recommended_resolution"])
        self.assertFalse(capped["meets_target"])
        self.assertTrue(capped["capped"])
        self.assertIn("1024px limit", capped["guidance"])

    def test_density_presets_and_explicit_density_change_the_recommendation(self):
        low = resolution.recommend_bake_resolution(100, preset="low")
        high = resolution.recommend_bake_resolution(100, preset="high")
        self.assertEqual(64, low["target_texels_per_meter"])
        self.assertEqual(256, high["target_texels_per_meter"])
        self.assertLess(low["recommended_resolution"], high["recommended_resolution"])
        explicit = resolution.recommend_bake_resolution(100, 32, preset="ignored")
        self.assertEqual(32, explicit["target_texels_per_meter"])
        self.assertIsNone(explicit["density_preset"])
        self.assertEqual(512, explicit["recommended_resolution"])

    def test_measured_uv_utilization_changes_density_and_remains_labeled(self):
        packed = resolution.recommend_bake_resolution(16, 128, uv_utilization=1,
                                                     uv_utilization_estimated=False)
        sparse = resolution.recommend_bake_resolution(16, 128, uv_utilization=0.25)
        self.assertEqual(packed["required_resolution"] * 2, sparse["required_resolution"])
        self.assertGreater(sparse["recommended_resolution"], packed["recommended_resolution"])
        self.assertFalse(packed["uv_utilization_estimated"])
        self.assertTrue(sparse["uv_utilization_estimated"])

    def test_world_unit_changes_preserve_physical_area_and_result(self):
        meters = resolution.area_in_square_meters(100, 1)
        centimeters = resolution.area_in_square_meters(1000000, 0.01)
        self.assertEqual(meters, centimeters)
        self.assertEqual(resolution.recommend_bake_resolution(meters),
                         resolution.recommend_bake_resolution(centimeters))
        self.assertEqual(25, resolution.area_in_square_meters(100, 0.5))

    def test_invalid_empty_nonfinite_and_negative_inputs_are_rejected(self):
        for value in (None, "", "invalid", True, False, 0, -1, float("nan"), float("inf"), -float("inf")):
            for argument in ("area", "density", "utilization", "world_area", "unit_scale"):
                with self.subTest(value=value, argument=argument), self.assertRaises(ValueError):
                    if argument == "area":
                        resolution.recommend_bake_resolution(value)
                    elif argument == "density":
                        resolution.recommend_bake_resolution(1, value if value is not None else "")
                    elif argument == "utilization":
                        resolution.recommend_bake_resolution(1, uv_utilization=value)
                    elif argument == "world_area":
                        resolution.area_in_square_meters(value)
                    else:
                        resolution.area_in_square_meters(1, value)
        with self.assertRaises(ValueError):
            resolution.recommend_bake_resolution(1, uv_utilization=1.01)
        with self.assertRaises(ValueError):
            resolution.recommend_bake_resolution(1, uv_utilization_estimated="yes")
        with self.assertRaises(ValueError):
            resolution.recommend_bake_resolution(1, preset="unknown")

    def test_supported_tiers_are_validated_and_normalized(self):
        for sizes in (None, (), (256,), (8192,), (True,), (512.0,), (512, "1024")):
            with self.subTest(sizes=sizes), self.assertRaises(ValueError):
                resolution.recommend_bake_resolution(1, supported_sizes=sizes)
        result = resolution.recommend_bake_resolution(1, supported_sizes=(2048, 512, 512))
        self.assertEqual((512, 2048), result["supported_sizes"])
        self.assertEqual(512, result["recommended_resolution"])

    def test_extreme_finite_inputs_avoid_intermediate_overflow_or_fail_clearly(self):
        result = resolution.recommend_bake_resolution(1e308, 128, uv_utilization=0.7)
        self.assertTrue(result["capped"])
        self.assertFalse(result["meets_target"])
        self.assertTrue(math.isfinite(result["achieved_texels_per_meter"]))
        with self.assertRaisesRegex(ValueError, "numeric range"):
            resolution.recommend_bake_resolution(1e308, 1e308, uv_utilization=0.7)
        with self.assertRaisesRegex(ValueError, "numeric range"):
            resolution.area_in_square_meters(1e308, 1e308)


if __name__ == "__main__":
    unittest.main()
