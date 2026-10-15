"""Pure algebra/domain checks; not native Blender geometry or FPS acceptance."""

import importlib.util
import inspect
import gc
import json
import math
from pathlib import Path
from dataclasses import asdict, replace
import sys
import unittest
import weakref


SOURCE = Path(__file__).resolve().parents[1] / "addons/character_designer/forearm_twist_bone_preview.py"
SPEC = importlib.util.spec_from_file_location("forearm_bone_preview_pure", SOURCE)
preview = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preview
SPEC.loader.exec_module(preview)

RATIOS = (0.0, 0.16576982097838075, 0.44341503351860967,
          0.7119189541509029, 0.9001018243902039, 1.0)


def identity():
    return tuple(tuple(float(row == column) for column in range(4)) for row in range(4))


def multiply(left, right):
    return tuple(tuple(sum(left[row][at] * right[at][column] for at in range(4))
                       for column in range(4)) for row in range(4))


def rotation(axis, angle):
    cosine, sine = math.cos(angle), math.sin(angle)
    matrix = [list(row) for row in identity()]
    if axis == "X":
        matrix[1][1], matrix[1][2], matrix[2][1], matrix[2][2] = cosine, -sine, sine, cosine
    elif axis == "Y":
        matrix[0][0], matrix[0][2], matrix[2][0], matrix[2][2] = cosine, sine, -sine, cosine
    else:
        matrix[0][0], matrix[0][1], matrix[1][0], matrix[1][1] = cosine, -sine, sine, cosine
    return tuple(tuple(row) for row in matrix)


def translation(point):
    matrix = [list(row) for row in identity()]
    for index in range(3):
        matrix[index][3] = point[index]
    return tuple(tuple(row) for row in matrix)


def about_wrist(angle, pivot=(0.0, 1.0, 0.0)):
    return multiply(multiply(translation(pivot), rotation("Y", angle)),
                    translation(tuple(-value for value in pivot)))


def transform(matrix, point):
    return tuple(sum(matrix[row][column] * point[column] for column in range(3))
                 + matrix[row][3] for row in range(3))


def weighted(terms):
    terms = tuple(terms)
    return tuple(sum(weight * point[index] for weight, point in terms) for index in range(3))


def distance(left, right):
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


class BonePreviewPlanTests(unittest.TestCase):
    def side(self, samples=((0, 0.5, 1.0),), enabled=True, name="L"):
        return preview.Side(name, "lower", "hand", (0.0, 1.0, 0.0),
                            (0.0, 1.0, 0.0), enabled, samples)

    def plan(self, samples=((0, 0.5, 1.0),), weights=(("lower", 0.4), ("hand", 0.6)), enabled=True):
        return preview.build_plan((self.side(samples, enabled),),
                                  tuple((index, weights) for index, _r, _a in samples),
                                  vertex_count=max(index for index, _r, _a in samples) + 1)

    def test_identity_rest_experiment_keeps_current_default_and_rejects_unknown_modes(self):
        self.assertEqual(inspect.signature(preview.build_isolated_preview)
                         .parameters['helper_rest_mode'].default, 'current')
        for mode in ('', 'IDENTITY', 'approximate', None, False, 1):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(preview.BonePreviewError, 'Helper Rest mode'):
                    preview.build_isolated_preview(None, None, self.plan(), source_mesh=None,
                                                   source_rig=None, helper_rest_mode=mode)

    def test_identity_rest_requires_exact_native_matrix_without_tolerance_waiver(self):
        preview._check_identity_rest(identity())
        for row, column, value in ((0, 0, 1.0 + 1e-12), (0, 1, 1e-12),
                                    (2, 3, 0.001), (1, 2, math.nan)):
            matrix = [list(values) for values in identity()]
            matrix[row][column] = value
            with self.subTest(row=row, column=column, value=value):
                with self.assertRaisesRegex(preview.BonePreviewError, 'exact identity'):
                    preview._check_identity_rest(matrix)
        with self.assertRaises(preview.BonePreviewError):
            preview._check_identity_rest(((1.0,),))

    def test_endpoints_reuse_unchanged_original_contribution(self):
        plan = self.plan(((0, 0.0, 1.0), (1, 0.5, 1.0), (2, 1.0, 1.0)))
        self.assertEqual(len(plan.helpers), 4)
        self.assertEqual(plan.rewrites[0].target_weights[0], ("lower", 0.4))
        self.assertEqual(plan.rewrites[2].target_weights[1], ("hand", 0.6))
        self.assertEqual({helper.fraction for helper in plan.helpers}, {-1.0, -0.5, 0.5, 1.0})

    def test_identical_ratios_share_pair_and_close_ratios_do_not(self):
        same = self.plan(((0, 0.5, 1.0), (1, 0.5, 1.0)))
        close = self.plan(((0, 0.5, 1.0), (1, math.nextafter(0.5, 1.0), 1.0)))
        self.assertEqual(len(same.helpers), 2)
        self.assertEqual(len(close.helpers), 4)

    def test_current_six_ratio_profile_needs_at_most_ten_helpers_per_side(self):
        plan = self.plan(tuple((index, ratio, 1.0) for index, ratio in enumerate(RATIOS)))
        self.assertEqual(len(plan.helpers), 10)
        self.assertTrue(all(len(row.target_weights) == 2 for row in plan.rewrites))

    def test_zero_influence_disabled_side_and_unrelated_weights_need_no_helpers(self):
        for plan in (self.plan(((0, 0.5, 0.0),)), self.plan(enabled=False),
                     self.plan(weights=(("other", 1.0),))):
            self.assertEqual(plan.helpers, ())
            self.assertEqual(plan.rewrites, ())

    def test_partial_influence_splits_original_and_helper_terms(self):
        plan = self.plan(((0, 0.5, 0.3),), weights=(("lower", 0.3), ("hand", 0.5), ("other", 0.2)))
        targets = plan.rewrites[0].target_weights
        self.assertEqual(len(targets), 5)
        self.assertAlmostEqual(sum(amount for _ref, amount in targets), 1.0)
        self.assertIn(("other", 0.2), targets)
        self.assertIn(("lower", 0.3 * 0.7), targets)
        self.assertIn(("hand", 0.5 * 0.7), targets)

    def test_plan_owns_immutable_numeric_snapshots(self):
        samples = [[0, 0.5, 1.0]]
        weights = {0: {"lower": 0.4, "hand": 0.6}}
        plan = preview.build_plan((self.side(samples),), weights, vertex_count=1)
        samples[0][1] = 0.9
        weights[0]["lower"] = 0.1
        self.assertEqual(plan.sides[0].samples, ((0, 0.5, 1.0),))
        self.assertEqual(plan.rewrites[0].source_weights, (("lower", 0.4), ("hand", 0.6)))

    def test_samples_are_disjoint_and_in_range(self):
        for sides in ((self.side(((0, 0.5, 1.0), (0, 0.8, 1.0))),),
                      (self.side(), self.side(name="R")),
                      (self.side(((1, 0.5, 1.0),)),)):
            with self.assertRaises(preview.BonePreviewError):
                preview.build_plan(sides, ((0, (("lower", 1.0),)),), vertex_count=1)

    def test_invalid_numeric_and_weight_domains_reject(self):
        for samples in (((0, -0.1, 1.0),), ((0, 1.1, 1.0),), ((0, math.nan, 1.0),),
                        ((0, 0.5, math.inf),), ((0, 0.5, -0.1),), ((0, 0.5, 1.1),)):
            with self.assertRaises(preview.BonePreviewError):
                self.plan(samples)
        for weights in ((), (("lower", -0.1), ("hand", 1.1)), (("lower", 0.7),),
                        (("lower", 0.5), ("lower", 0.5)), (("lower", math.inf),)):
            with self.assertRaises(preview.BonePreviewError):
                self.plan(weights=weights)
        with self.assertRaises(preview.BonePreviewError):
            preview.build_plan((preview.Side("L", "lower", "hand", (0, 0, 0), (0, 0, 0), True,
                                                 ((0, 0.5, 1.0),)),),
                               ((0, (("lower", 1.0),)),), vertex_count=1)

    def test_weight_order_retained_for_source_guard(self):
        weights = (("hand", 0.6), ("lower", 0.4))
        self.assertEqual(self.plan(weights=weights).rewrites[0].source_weights, weights)

    def test_unissued_pure_plan_cannot_enter_native_builder(self):
        with self.assertRaises(preview.BonePreviewError):
            preview.build_isolated_preview(None, None, self.plan(), source_mesh=None, source_rig=None)

    def test_discard_requires_exact_issued_plan_and_releases_trusted_template(self):
        plan, template = self.plan(), object()
        preview._ISSUED_PLANS[id(plan)] = plan
        preview._PLAN_TEMPLATES[plan] = template
        preview.discard_plan(replace(plan))
        self.assertIs(preview._ISSUED_PLANS[id(plan)], plan)
        self.assertIs(preview._PLAN_TEMPLATES[plan], template)
        preview.discard_plan(plan)
        preview.discard_plan(plan)
        preview.discard_plan(None)
        self.assertNotIn(id(plan), preview._ISSUED_PLANS)
        self.assertNotIn(plan, preview._PLAN_TEMPLATES)

    def test_failed_admission_consumes_plan_before_any_native_import(self):
        plan, template = self.plan(), object()
        preview._ISSUED_PLANS[id(plan)] = plan
        preview._PLAN_TEMPLATES[plan] = template
        with self.assertRaises(preview.BonePreviewError):
            preview.build_isolated_preview(None, None, plan, source_mesh=None, source_rig=None, template=object())
        self.assertNotIn(id(plan), preview._ISSUED_PLANS)
        self.assertNotIn(plan, preview._PLAN_TEMPLATES)
        with self.assertRaises(preview.BonePreviewError):
            preview._consume_plan(plan)

    def test_equal_field_plans_keep_separate_certificates_through_discard_and_gc(self):
        first, second = self.plan(), self.plan()
        first_template, second_template = object(), object()
        self.assertEqual(asdict(first), asdict(second))
        self.assertIsNot(first, second)
        for plan, template in ((first, first_template), (second, second_template)):
            preview._ISSUED_PLANS[id(plan)] = plan
            preview._PLAN_TEMPLATES[plan] = template
        del plan, template
        self.assertIs(preview._PLAN_TEMPLATES[first], first_template)
        self.assertIs(preview._PLAN_TEMPLATES[second], second_template)
        preview.discard_plan(first)
        self.assertIs(preview._PLAN_TEMPLATES[second], second_template)
        first_ref = weakref.ref(first)
        del first
        gc.collect()
        self.assertIsNone(first_ref())
        self.assertIs(preview._consume_plan(second), second_template)

        # Weak-key cleanup of an undiscarded first capture also must not remove
        # a separate still-live equal-field capture.
        first, second = self.plan(), self.plan()
        for plan, template in ((first, first_template), (second, second_template)):
            preview._ISSUED_PLANS[id(plan)] = plan
            preview._PLAN_TEMPLATES[plan] = template
        del plan, template
        first_ref = weakref.ref(first)
        del first
        gc.collect()
        self.assertIsNone(first_ref())
        self.assertIs(preview._PLAN_TEMPLATES[second], second_template)
        self.assertIs(preview._consume_plan(second), second_template)

    def test_modifier_stack_rejects_downstream_group_consumers(self):
        for kinds in (("ARMATURE",), ("ARMATURE", "SUBSURF"), ("ARMATURE", "SUBSURF", "SUBSURF")):
            preview.check_modifier_types(kinds)
        for kinds in ((), ("SUBSURF", "ARMATURE"), ("ARMATURE", "MIRROR"),
                      ("ARMATURE", "ARMATURE"), ("ARMATURE", "NODES"), ("ARMATURE", "MASK")):
            with self.assertRaises(preview.BonePreviewError):
                preview.check_modifier_types(kinds)

    def test_helper_path_guard_handles_orphan_and_escaped_paths(self):
        names = ("Forearm Preview L Lower 01", 'A"B\\C')
        for path in ('pose.bones["Forearm Preview L Lower 01"].location',
                     "bones['Forearm Preview L Lower 01'].bbone_easein",
                     'pose.bones[' + json.dumps(names[1]) + '].rotation_quaternion'):
            with self.assertRaises(preview.BonePreviewError):
                preview.check_helper_paths(names, (path,))
        preview.check_helper_paths(names, ('pose.bones["forearm.L"].rotation_euler', 'location'))

    def test_scene_requires_exclusive_preview_ownership_and_no_originals(self):
        preview.check_scene_ownership(10, (10,), (10,), (101, 102), (201, 202))
        for mesh_scenes, rig_scenes, members in (((10, 11), (10,), (101, 102)),
                                                 ((10,), (11,), (101, 102)),
                                                 ((10,), (10,), (101, 201))):
            with self.assertRaises(preview.BonePreviewError):
                preview.check_scene_ownership(10, mesh_scenes, rig_scenes, members, (201, 202))

    def test_matrix_oracle_reports_small_native_roundoff_and_rejects_waiver(self):
        expected = identity()
        actual = [list(row) for row in expected]
        actual[0][0] += 1e-6
        report = preview.matrix_difference(actual, expected)
        self.assertFalse(report["exact"])
        self.assertTrue(report["within_tolerance"])
        self.assertAlmostEqual(report["rms"], report["max_abs"] / 4)
        actual[0][0] += 2e-6
        self.assertFalse(preview.matrix_difference(actual, expected)["within_tolerance"])
        with self.assertRaises(preview.BonePreviewError):
            preview.matrix_difference(actual, expected, tolerance=3e-6)
        actual[0][0] = math.nan
        with self.assertRaises(preview.BonePreviewError):
            preview.matrix_difference(actual, expected)


class PairedBoneAlgebraTests(unittest.TestCase):
    side = BonePreviewPlanTests.side
    plan = BonePreviewPlanTests.plan

    def test_target_weights_match_current_additive_desired_formula(self):
        max_delta, cases = 0.0, 0
        whole = multiply(multiply(translation((0.3, -0.8, 0.6)), rotation("Z", 0.43)), rotation("X", -0.29))
        lower = multiply(whole, rotation("X", 0.37))
        other = multiply(translation((-0.3, 0.2, 0.1)), rotation("Z", 0.51))
        for ratio in RATIOS:
            for amounts in ((1.0, 0.0, 0.0), (0.5, 0.5, 0.0), (0.33, 0.47, 0.2)):
                weights = tuple((name, amount) for name, amount in zip(("lower", "hand", "other"), amounts) if amount)
                for influence in (0.0, 0.3, 1.0):
                    plan = self.plan(((0, ratio, influence),), weights=weights)
                    targets = plan.rewrites[0].target_weights if plan.rewrites else weights
                    for degrees in (-120, -90, -47, 0, 47, 90, 120):
                        angle = math.radians(degrees)
                        for swing in (-80, -33, 0, 33, 80):
                            hand = multiply(multiply(lower, rotation("X", math.radians(swing))), about_wrist(angle))
                            matrices = {"lower": lower, "hand": hand, "other": other}
                            for helper in plan.helpers:
                                matrices[helper.key] = multiply(matrices[helper.source_bone], about_wrist(helper.fraction * angle))
                            for point in ((0.04, 0.74, 0.03), (-0.08, 1.2, 0.07)):
                                baseline = weighted((amount, transform(matrices[name], point)) for name, amount in weights)
                                lower_original, hand_original = transform(lower, point), transform(hand, point)
                                lower_desired = transform(multiply(lower, about_wrist(ratio * angle)), point)
                                hand_desired = transform(multiply(hand, about_wrist((ratio - 1.0) * angle)), point)
                                expected = tuple(baseline[i] + influence * (
                                    amounts[0] * (lower_desired[i] - lower_original[i])
                                    + amounts[1] * (hand_desired[i] - hand_original[i])) for i in range(3))
                                candidate = weighted((amount, transform(matrices[ref], point)) for ref, amount in targets)
                                max_delta = max(max_delta, distance(expected, candidate))
                                cases += 1
        self.assertEqual(cases, 3780)
        self.assertLess(max_delta, 1e-14)

    def test_pure_axial_full_influence_retains_exact_circular_arc(self):
        point, angle = (0.04, 0.7, 0.03), math.radians(120)
        for ratio in RATIOS:
            for hand_weight in (0.0, 0.13, 0.5, 0.79, 1.0):
                lower = about_wrist(ratio * angle)
                hand = multiply(about_wrist(angle), about_wrist((ratio - 1.0) * angle))
                candidate = weighted(((1 - hand_weight, transform(lower, point)),
                                      (hand_weight, transform(hand, point))))
                self.assertLess(distance(candidate, transform(about_wrist(ratio * angle), point)), 1e-14)

    def test_single_lower_follow_bone_loses_wrist_swing(self):
        point, angle = (1.0, 0.0, 0.0), math.radians(90)
        lower = rotation("Y", angle * 0.5)
        hand = multiply(multiply(rotation("X", math.radians(60)), rotation("Y", angle)), rotation("Y", -angle * 0.5))
        desired = weighted(((0.5, transform(lower, point)), (0.5, transform(hand, point))))
        self.assertAlmostEqual(distance(desired, transform(lower, point)), math.sqrt(0.125), places=14)

    def test_deformation_multiplication_order_matters(self):
        source, rotate = rotation("X", 0.7), about_wrist(0.8)
        point = (0.3, 0.7, -0.2)
        self.assertGreater(distance(transform(multiply(source, rotate), point),
                                    transform(multiply(rotate, source), point)), 0.01)


class BuiltPreviewIssuanceTests(unittest.TestCase):
    """Lifecycle-only private issuance stand-ins; no native build acceptance."""

    def controller(self, kind=None):
        controller = object.__new__(kind or preview.IsolatedBonePreview)
        controller._closed = controller._failed = False
        controller._desired_deformations = None
        return controller

    def test_handcrafted_controller_cannot_pass_public_or_native_guard(self):
        controller = self.controller()
        with self.assertRaises(preview.BonePreviewError):
            preview.check_built_preview(controller)
        with self.assertRaises(preview.BonePreviewError):
            controller._guard()  # issuance fails before any absent native fields

    def test_membership_cannot_certify_subclasses(self):
        class OtherController(preview.IsolatedBonePreview):
            pass
        controller = self.controller(OtherController)
        preview._BUILT_PREVIEWS.add(controller)
        try:
            with self.assertRaises(preview.BonePreviewError):
                preview.check_built_preview(controller)
        finally:
            controller.close()

    def test_close_and_failed_state_revoke_controller_admission(self):
        controller = self.controller()
        preview._BUILT_PREVIEWS.add(controller)
        self.assertIs(preview.check_built_preview(controller), controller)
        controller._failed = True
        with self.assertRaises(preview.BonePreviewError):
            preview.check_built_preview(controller)
        controller.close()
        controller.close()
        self.assertNotIn(controller, preview._BUILT_PREVIEWS)
        with self.assertRaises(preview.BonePreviewError):
            preview.check_built_preview(controller)

    def test_gc_removes_only_the_discarded_controller_issuance(self):
        first, second = self.controller(), self.controller()
        preview._BUILT_PREVIEWS.add(first)
        preview._BUILT_PREVIEWS.add(second)
        reference = weakref.ref(first)
        del first
        gc.collect()
        self.assertIsNone(reference())
        self.assertIs(preview.check_built_preview(second), second)
        second.close()


if __name__ == "__main__":
    unittest.main()
