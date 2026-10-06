"""Focused pure boundaries and an explicit prepared native rollback check.

Run this file with ordinary Python for pure tests only. No Blender is imported,
started, registered, or simulated. native_upgrade_checks is an opt-in function
for a caller's exact isolated/sealed real-Dress fixture, not a native PASS here.
"""
import ast
import copy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "addons/character_designer/skirt_surface.py"


def scope():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    declarations = [item for item in tree.body if isinstance(item, (ast.Assign, ast.FunctionDef, ast.ClassDef))]
    space = {"copy": copy, "hashlib": hashlib, "json": json, "math": math}
    exec(compile(ast.Module(body=declarations, type_ignores=[]), str(SOURCE), "exec"), space)
    return space


def identity():
    return [[float(row == column) for column in range(4)] for row in range(4)]


class NeutralReferenceBoundary(unittest.TestCase):
    def setUp(self):
        self.s = scope()

    def expression(self):
        return {"version": 1, "strategy": self.s["NEUTRAL_MANUAL_FK"],
                "chart": self.s["NEUTRAL_MANUAL_CHART"], "bases": {"Manual": identity()},
                "rest": "exact-rest", "parents": {"Manual": "Waist"}}

    def test_old_marker_absent_is_legacy_without_mutation(self):
        surface = {"version": 1}
        before = copy.deepcopy(surface)
        self.assertEqual(self.s["_neutral_manual_record"](surface), self.s["NEUTRAL_MANUAL_LEGACY"])
        self.assertEqual(surface, before)

    def test_known_rest_chart_is_explicit_and_read_only(self):
        surface = {"neutral_manual": self.expression()}
        before = copy.deepcopy(surface)
        self.assertEqual(self.s["_neutral_manual_record"](surface), self.s["NEUTRAL_MANUAL_FK"])
        self.assertEqual(surface, before)

    def test_unknown_empty_or_current_pose_marker_cannot_become_rest_fk(self):
        cases = [None, {}, True, "REST_FK"]
        for field, value in (("version", True), ("version", 2), ("strategy", "CURRENT_POSE_FK"),
                             ("strategy", "LEGACY_SPLINE"), ("chart", "CURRENT_BODY"), ("bases", [])):
            candidate = self.expression(); candidate[field] = value; cases.append(candidate)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.s["_neutral_manual_record"]({"neutral_manual": value})

    def test_nonfinite_boolean_ragged_or_extra_basis_metadata_is_rejected(self):
        cases = []
        for bad in (float("nan"), float("inf"), True):
            item = self.expression(); item["bases"]["Manual"][0][0] = bad; cases.append(item)
        item = self.expression(); item["bases"]["Manual"].pop(); cases.append(item)
        item = self.expression(); item["bases"]["Manual"][0].pop(); cases.append(item)
        item = self.expression(); item["accepted"] = True; cases.append(item)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.s["_neutral_manual_record"]({"neutral_manual": value})

    def cache(self, **changes):
        return SimpleNamespace(**({"is_baked": True, "is_baking": False, "use_external": False,
                                  "frame_start": 1, "frame_end": 60} | changes))

    def test_sealed_cache_gate_does_not_write_or_bake(self):
        cache = self.cache(); physics = {"baked_range": [1, 60]}
        before = copy.deepcopy(vars(cache)), copy.deepcopy(physics)
        self.s["_sealed_reference_upgrade"](physics, cache)
        self.assertEqual((vars(cache), physics), before)

    def test_empty_unsealed_external_busy_mismatched_cache_refuses_before_mutation(self):
        cases = [({}, self.cache()), ({"baked_range": []}, self.cache()),
                 ({"baked_range": [True, 60]}, self.cache()), ({"baked_range": [1, 59]}, self.cache()),
                 ({"baked_range": [1, 60]}, self.cache(is_baked=False)),
                 ({"baked_range": [1, 60]}, self.cache(is_baking=True)),
                 ({"baked_range": [1, 60]}, self.cache(use_external=True))]
        for physics, cache in cases:
            before = copy.deepcopy(physics), copy.deepcopy(vars(cache))
            with self.subTest(physics=physics, cache=vars(cache)), self.assertRaisesRegex(ValueError, "cannot be rolled back"):
                self.s["_sealed_reference_upgrade"](physics, cache)
            self.assertEqual((physics, vars(cache)), before)

    def points(self, h0=0., output=0., cloth=0.):
        return {"H0": [(h0, 0., 0.)], "O": [(output, 0., 0.)], "C": [(cloth, 0., 0.)]}

    def test_actual_world_units_keep_50um_and_zero_cloth_budget(self):
        result = self.s["_reference_upgrade_errors"](self.points(), self.points(.049, .048), .001)
        self.assertAlmostEqual(result["H0"], 49e-6)
        self.assertAlmostEqual(result["O"], 48e-6)
        self.assertEqual(result["C"], 0.)

    def test_inward_error_above_50um_or_any_cloth_change_is_rejected(self):
        for candidate in (self.points(h0=50.001e-6), self.points(output=50.001e-6), self.points(cloth=1e-15)):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                self.s["_reference_upgrade_errors"](self.points(), candidate, 1.)

    def test_invalid_units_nonfinite_or_vertex_count_change_is_rejected(self):
        for unit in (0., -1., float("nan"), float("inf"), True):
            with self.subTest(unit=unit), self.assertRaises(ValueError):
                self.s["_reference_upgrade_errors"](self.points(), self.points(), unit)
        for candidate in (self.points(h0=float("nan")), self.points() | {"O": []}):
            with self.assertRaises(ValueError):
                self.s["_reference_upgrade_errors"](self.points(), candidate, 1.)

    def test_rest_chart_order_and_explicit_upgrade_do_not_touch_frame_or_cloth(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        functions = {item.name: item for item in tree.body if isinstance(item, ast.FunctionDef)}
        fixed = ast.unparse(functions["_fixed_neutral_manual"])
        self.assertLess(fixed.index("reference.parent = None"), fixed.index("reference.evaluated_get(graph)"))
        self.assertLess(fixed.index("_neutral(bone)"), fixed.index("reference.evaluated_get(graph)"))
        self.assertLess(fixed.index("desired ="), fixed.index("bone.constraints.remove(spline)"))
        calls = [item for item in ast.walk(functions["_fixed_neutral_manual"]) if isinstance(item, ast.Call)
                 and isinstance(item.func, ast.Attribute) and item.func.attr == "convert_local_to_pose"]
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(item.arg == "invert" and isinstance(item.value, ast.Constant) and item.value.value is True
                            for item in calls[0].keywords))
        self.assertIn("parent_matrix_local", fixed)
        upgrade = ast.unparse(functions["upgrade_neutral_manual"])
        self.assertLess(upgrade.index("_sealed_reference_upgrade("), upgrade.index("tx = _Transaction("))
        self.assertLess(upgrade.index("_clear_reference_upgrade(tx, source, preview)"), upgrade.index("changed = True"))
        self.assertLess(upgrade.index("validate(source, rig, updated)"), upgrade.index("success = True"))
        self.assertIn("if not success:", upgrade[upgrade.index("finally:"):])
        self.assertNotIn("frame_set", upgrade)
        self.assertNotIn("restore_context", upgrade)
        self.assertNotIn("bake", " ".join(ast.unparse(call.func) for call in ast.walk(functions["upgrade_neutral_manual"])
                                           if isinstance(call, ast.Call)))
        for name in ("validate", "_surface_record", "_neutral_manual_proof"):
            self.assertNotIn("upgrade_neutral_manual(", ast.unparse(functions[name]))


def native_upgrade_checks(context, source):
    """Prepared, never auto-run: caller owns exact private installed/sealed fixture.

    Inject a failure at the committed geometry guard, require its actual reach,
    prove rollback, then do the real upgrade and validated idempotent no-op.
    This does not prove arbitrary Body shear, animation, or artistic clearance.
    """
    import bpy
    from character_designer import skirt_surface as surface
    if not bpy.app.background:
        raise RuntimeError("Native reference checks require an isolated background fixture")
    record = surface.skirt.read_record(source)
    rig = source[surface.skirt.RIG_KEY]
    actual, cloth = surface.validate(source, rig, record)
    surface._sealed_reference_upgrade(record["physics"], cloth.point_cache)
    reference = surface._object(record, "NEUTRAL_RIG", source)
    raw = source[surface.skirt.RECORD_KEY]
    contract = surface._helper_contract(reference)
    protected = surface._upgrade_inputs(context, source, rig, record, cloth)
    original, calls = surface._reference_upgrade_errors, []

    def fail_after_commit(*values):
        result = original(*values)
        calls.append(result)
        if len(calls) == 2:
            raise surface.SkirtSurfaceError("injected committed reference failure")
        return result

    surface._reference_upgrade_errors = fail_after_commit
    try:
        try:
            surface.upgrade_neutral_manual(context, source)
        except surface.SkirtSurfaceError as error:
            if "injected committed reference failure" not in str(error):
                raise
        else:
            raise AssertionError("The prepared post-commit rollback injection did not fail")
    finally:
        surface._reference_upgrade_errors = original
    assert len(calls) == 2, "Candidate did not actually reach the committed guard"
    assert source[surface.skirt.RECORD_KEY] == raw and surface._helper_contract(reference) == contract
    assert surface._upgrade_inputs(context, source, rig, record, cloth) == protected
    upgraded = surface.upgrade_neutral_manual(context, source)
    assert surface._neutral_manual_record(upgraded["physics"]["surface"]) == surface.NEUTRAL_MANUAL_FK
    stable = source[surface.skirt.RECORD_KEY], surface._helper_contract(reference)
    surface.upgrade_neutral_manual(context, source)
    assert stable == (source[surface.skirt.RECORD_KEY], surface._helper_contract(reference))
    assert surface._upgrade_inputs(context, source, rig, upgraded, cloth) == protected
    return {"explicit_upgrade": True, "post_commit_rollback": True, "idempotent_validated_noop": True,
            "current_pose_guard_m": surface._REFERENCE_UPGRADE_LIMIT_M,
            "arbitrary_body_shear_proved": False, "artist_effect_accepted": False}


if __name__ == "__main__":
    unittest.main()
