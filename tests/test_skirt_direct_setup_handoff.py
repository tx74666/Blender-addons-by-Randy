"""Pure Setup/Add Physics focus handoff through actual extracted runtime code.

Reuse the existing selection fixture. Native build/install/remove are endpoints;
these checks do not evaluate Blender mode switching, Cloth or final geometry.
"""
import copy
import json
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from test_skirt_direct_control_selection import PROFILES, SelectionFixture, load


class SetupFixture(SelectionFixture):
    def __init__(self, *, fresh=False, capability="BOTH"):
        super().__init__("LEGACY_CAGE")
        self.objects.append(self.source)
        self.source.modifiers = [NS(is_active=True)]
        self.setup_exists = not fresh
        self.settings.capability = capability
        self.settings.physics = True
        self.settings.chain_count, self.settings.segment_count = 8, 4
        self.settings.is_property_set = lambda _name: True
        self.record["physics"] = None
        self.source["record"] = json.dumps(self.record, indent=1)
        self.direct = load("skirt_surface_direct.py", {"preview_status"}, self.direct)
        self.service.preview_status = self.direct["preview_status"]
        self.physics.LEGACY_BACKEND = "LEGACY_CAGE"
        self.physics.backend = lambda record: (record.get("physics") or {}).get("backend", "LEGACY_CAGE")
        self.physics._surface_module = lambda *args, **kwargs: self.service
        self.skirt["read_record"] = lambda source: json.loads(source["record"]) if self.setup_exists else None
        self.skirt["build_skirt"] = self.build
        self.skirt["remove_skirt"] = lambda *_args: self.calls.append(("remove-setup",))
        self.physics.add_physics = self.install
        self.service.preflight_remove = lambda *_args: self.calls.append(("preflight-remove",)) or self.record
        self.service.commit_remove = self.remove
        self.module.initialize = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Direct installation owns mode initialization"))
        self.package.skirt_motion_profiles = PROFILES
        self.before_install = None
        self.public = load("skirt.py", {"_finish_setup_selection", "_add_requested_physics",
            "CHARACTERDESIGNER_OT_create_skirt_setup", "CHARACTERDESIGNER_OT_skirt_add_physics"},
            dict(Operator=object, BoolProperty=lambda **_kwargs: None, __package__=self.package.__name__,
                 _rig=lambda: NS(**self.skirt), _physics=lambda: self.physics,
                 _source=lambda _context: self.source, _settings=lambda _context: self.settings,
                 _setup=lambda: NS(settings=lambda _context: NS(body=self.other),
                                   remember_asset=lambda *_args: self.calls.append(("remember",))),
                 _desired_attachment=lambda *_args: (None, ""),
                 _restore_preview=lambda *_args: self.calls.append(("restore-preview",)),
                 _report=lambda *args, **kwargs: self.calls.append(("report", args[2], kwargs))))

    def build(self, context, source, **_kwargs):
        self.setup_exists = True
        self.calls.append(("build",))
        self.skirt["select_controls"](context, source)  # Real build's terminal selection endpoint.
        return json.loads(source["record"])

    def install(self, _context, source, **kwargs):
        self.calls.append(("install", kwargs))
        self.before_install = {key: source[key] for key in
                               ("record", PROFILES.PROFILE_KEY, self.direct["STATE_KEY"])}
        record = json.loads(source["record"])
        record["physics"] = copy.deepcopy(self.record["physics"] or {})
        record["physics"].update(backend=self.direct["BACKEND"], baked_range=[1, 60],
                                 surface=dict(version=1, mode_socket="use_cloth"))
        self.record = record
        source["record"] = json.dumps(record, indent=1)
        # Native install is an endpoint; publish its established fresh Automatic result.
        PROFILES.write(source, PROFILES.fresh(record, capability=kwargs["capability"],
                                            mode="AUTOMATIC"), record)
        state = dict(version=1, owner=record["owner"], mode="AUTOMATIC", editing=False, pending=False)
        self.direct["_write_state"](source, state)
        self.direct["_sync"](source, record, state)
        return record

    def remove(self, source, _plan):
        self.calls.append(("remove-direct",))
        for key, value in self.before_install.items():
            source[key] = value
        self.record = json.loads(source["record"])

    def execute(self, name):
        operator = self.public[name]()
        operator.actual_surface = True
        with patch.dict(sys.modules, {self.package.__name__: self.package,
                                      self.module.__name__: self.module}):
            return operator.execute(self.context)

    def handoff(self):
        return self.public["_finish_setup_selection"](self.context, self.source)

    def direct_record(self):
        self.record["physics"] = dict(backend=self.direct["BACKEND"], baked_range=[1, 60],
                                     surface=dict(version=1, mode_socket="use_cloth"))
        self.source["record"] = json.dumps(self.record, indent=1)

    def select_waist(self):
        self.skirt["select_controls"](self.context, self.source)
        self.calls.clear()

    def fail_source_selection_once(self):
        actual = self.source.select_set
        failures = [True]

        def select(value):
            actual(value)
            if value and failures:
                failures.pop()
                raise RuntimeError("focus failed after source unhide and selection")
        self.source.select_set = select


class DirectSetupHandoffTests(unittest.TestCase):
    def assert_automatic_object_focus(self, f):
        self.assertIs(f.objects.active, f.source)
        self.assertEqual(f.source.mode, "OBJECT")
        self.assertEqual(f.rig.mode, "OBJECT")
        self.assertEqual([obj for obj in f.objects if obj.select_get()], [f.source])
        state = f.direct["_state"](f.source, f.record)
        self.assertEqual(state, dict(version=1, owner="owned", mode="AUTOMATIC", editing=False, pending=False))
        self.assertTrue(f.direct["_use_cloth"](state))
        f.direct["require_bake_ready"](f.source, f.record)

    def test_automatic_handoff_exits_inherited_pose_without_mode_cache_or_parameter_writes(self):
        f = SetupFixture()
        f.direct_record()
        f.select_waist()
        before = f.state()
        self.assertIs(f.handoff(), f.source)
        self.assert_automatic_object_focus(f)
        after = f.state()
        for key in ("record", "profile", "direct", "influence", "endpoint", "cache", "bone_active",
                    "selected", "bone_hidden", "pose_hidden", "collections", "settings_source"):
            self.assertEqual(after[key], before[key], key)
        self.assertFalse(f.calls)  # No ordinary selector, mode apply or restore path.
        self.assertEqual(f.snapshots, [])

    def test_public_setup_and_add_physics_finish_in_automatic_object_mode(self):
        cases = (("CHARACTERDESIGNER_OT_create_skirt_setup", True, "BOTH"),
                 ("CHARACTERDESIGNER_OT_create_skirt_setup", True, "PHYSICS"),
                 ("CHARACTERDESIGNER_OT_skirt_add_physics", False, "BOTH"))
        for name, fresh, capability in cases:
            with self.subTest(operator=name, capability=capability):
                f = SetupFixture(fresh=fresh, capability=capability)
                if not fresh:
                    f.select_waist()  # Existing Manual setup can also arrive in Pose Mode.
                cache = f.state()["cache"]
                self.assertEqual(f.execute(name), {"FINISHED"})
                self.assert_automatic_object_focus(f)
                self.assertEqual(f.state()["cache"], cache)
                self.assertEqual(PROFILES.read(f.source, f.record)["capability"], capability)
                selections = [call for call in f.calls if call[0] == "select"]
                self.assertEqual(len(selections), int(fresh))  # Build only; none after install.
                native = next(call for call in f.calls if call[0] == "install")
                self.assertEqual(native[1]["capability"], capability)
                self.assertFalse(any(call[0] in {"apply", "remove-direct", "remove-setup"} for call in f.calls))

    def test_partial_focus_failure_restores_context_visibility_and_all_authored_state(self):
        f = SetupFixture()
        f.direct_record()
        f.select_waist()
        before = f.state()
        f.fail_source_selection_once()
        with self.assertRaisesRegex(RuntimeError, "focus failed"):
            f.handoff()
        self.assertEqual(f.state(), before)
        self.assertEqual(f.calls, [("restore-context",)])

    def test_manual_delta_and_legacy_keep_control_selection_without_changing_saved_intent(self):
        for backend in ("DIRECT_MAIN_CLOTH_V1", "ACTUAL_SURFACE_DELTA_V1", "LEGACY_CAGE"):
            with self.subTest(backend=backend):
                f = SetupFixture()
                f.direct_record()
                f.record["physics"]["backend"] = backend
                f.source["record"] = json.dumps(f.record, indent=1)
                if backend == f.direct["BACKEND"]:
                    f.direct["set_mode"](f.source, f.record, "MANUAL")
                before = f.state()
                f.handoff()
                self.assertIs(f.objects.active, f.rig)
                self.assertEqual(f.rig.mode, "POSE")
                self.assertEqual(len([call for call in f.calls if call[0] == "select"]), 1)
                for key in ("record", "profile", "direct", "influence", "endpoint", "cache", "settings_source"):
                    self.assertEqual(f.state()[key], before[key], key)

    def test_malformed_direct_state_refuses_before_any_focus_change(self):
        f = SetupFixture()
        f.direct_record()
        f.source[f.direct["STATE_KEY"]] = json.dumps(dict(version=1, owner="foreign", mode="AUTOMATIC",
                                                        editing=False, pending=False))
        before = f.state()
        with self.assertRaises(f.direct["SkirtDirectError"]):
            f.handoff()
        self.assertEqual(f.state(), before)
        self.assertEqual(f.calls, [])

    def test_public_add_focus_failure_uses_existing_install_rollback_and_restores_ui(self):
        f = SetupFixture()
        f.select_waist()
        before = f.state()
        f.fail_source_selection_once()
        self.assertEqual(f.execute("CHARACTERDESIGNER_OT_skirt_add_physics"), {"CANCELLED"})
        self.assertEqual(f.state(), before)
        self.assertEqual(f.calls.count(("preflight-remove",)), 1)
        self.assertEqual(f.calls.count(("remove-direct",)), 1)
        self.assertEqual(f.calls.count(("restore-context",)), 2)  # Focus then public install transaction.


if __name__ == "__main__":
    unittest.main()
