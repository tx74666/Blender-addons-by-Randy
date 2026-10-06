"""Execute the public update operator and transport with fake native service."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1] / "addons/character_designer"
spec = importlib.util.spec_from_file_location("dress_update_profiles", ROOT / "skirt_motion_profiles.py")
profiles = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profiles)


def physics_module():
    tree = ast.parse((ROOT / "skirt_physics.py").read_text(encoding="utf-8"))
    selected = [node for node in tree.body
                if (isinstance(node, (ast.FunctionDef, ast.ClassDef))
                    and node.name in {"_record_backend", "backend", "SkirtPhysicsError"})
                or (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                    and target.id in {"LEGACY_BACKEND", "ACTUAL_SURFACE_BACKEND"} for target in node.targets))]
    space = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "skirt_physics.py", "exec"), space)
    return types.SimpleNamespace(**space)


class UpdateIntent(unittest.TestCase):
    def fixture(self, *, capability="MANUAL", mode="MANUAL", backend="LEGACY_CAGE", saved=True, fail=False):
        record = {"owner": "owned Dress", "chain_count": 8, "segment_count": 4,
                  "physics": {"backend": backend}, "author_setting": "retained"}
        source = {"record": json.dumps(record), "author_data": "preserved"}
        if saved:
            profiles.write(source, profiles.fresh(record, capability=capability, mode=mode), record)
        calls, reports = [], []
        physics = physics_module()
        body = object()

        def add(context, chosen, **kwargs):
            calls.append(("native install", chosen, kwargs))
            if fail: raise ValueError("Deliberate native installation failure")
            record["physics"]["backend"] = physics.ACTUAL_SURFACE_BACKEND
            return record

        physics.add_physics = add
        rig = types.SimpleNamespace(read_record=lambda chosen: record,
            _require_controls_for_setup=lambda chosen: calls.append(("controls proof", chosen)),
            build_skirt=lambda *args, **kwargs: self.fail("An update must not rebuild from creation settings"),
            select_controls=lambda *args: calls.append(("select",)),
            remove_skirt=lambda *args: self.fail("A failed update must not remove artist setup"))
        setup = types.SimpleNamespace(settings=lambda context: types.SimpleNamespace(body=body),
            remember_asset=lambda *args: calls.append(("remember",)))
        # These intentionally disagree with the installed dimensions/intent.
        settings = types.SimpleNamespace(capability="PHYSICS", physics=True, chain_count=17, segment_count=9,
                                         is_property_set=lambda name: True)
        package = types.ModuleType("dress_public_update_test")
        package.skirt_motion_profiles = profiles
        tuning = types.ModuleType(package.__name__ + ".skirt_motion_tuning")
        tuning.initialize = lambda *args, **kwargs: self.fail("Actual installation owns initialization")
        package.skirt_motion_tuning = tuning
        tree = ast.parse((ROOT / "skirt.py").read_text(encoding="utf-8"))
        selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                    and node.name in {"_add_requested_physics", "CHARACTERDESIGNER_OT_create_skirt_setup"}]
        space = {"__package__": package.__name__, "Operator": object, "BoolProperty": lambda **kwargs: None,
                 "_source": lambda context: source, "_settings": lambda context: settings,
                 "_physics": lambda: physics, "_rig": lambda: rig, "_setup": lambda: setup,
                 "_restore_preview": lambda *args: calls.append(("restore preview",)),
                 "_report": lambda *args, **kwargs: reports.append((args[2], kwargs))}
        exec(compile(ast.Module(body=selected, type_ignores=[]), "skirt.py", "exec"), space)
        operator = space["CHARACTERDESIGNER_OT_create_skirt_setup"]()
        operator.actual_surface = True
        modules = {package.__name__: package, package.__name__ + ".skirt_motion_profiles": profiles,
                   tuning.__name__: tuning}

        def execute():
            with mock.patch.dict(sys.modules, modules): return operator.execute(object())

        return types.SimpleNamespace(source=source, record=record, calls=calls, reports=reports,
                                     body=body, settings=settings, execute=execute)

    def test_public_legacy_manual_upgrade_passes_both_through_actual_transport(self):
        f = self.fixture()
        raw = f.source[profiles.PROFILE_KEY]
        self.assertEqual(f.execute(), {"FINISHED"})
        native = [call for call in f.calls if call[0] == "native install"]
        self.assertEqual(len(native), 1)
        self.assertIs(native[0][1], f.source)
        self.assertEqual(native[0][2], {"backend": "ACTUAL_SURFACE_DELTA_V1", "body": f.body, "capability": "BOTH"})
        # Caller passes intent; the tested native transaction owns any write.
        self.assertEqual(f.source[profiles.PROFILE_KEY], raw)
        self.assertEqual((f.record["chain_count"], f.record["segment_count"]), (8, 4))

    def test_existing_both_manual_physics_and_actual_semantics_pass_no_new_intent(self):
        for capability, mode, backend in (("BOTH", "MANUAL", "LEGACY_CAGE"),
                                         ("PHYSICS", "AUTOMATIC", "LEGACY_CAGE"),
                                         ("MANUAL", "MANUAL", "ACTUAL_SURFACE_DELTA_V1"),
                                         ("BOTH", "MANUAL", "ACTUAL_SURFACE_DELTA_V1")):
            with self.subTest(capability=capability, mode=mode, backend=backend):
                f = self.fixture(capability=capability, mode=mode, backend=backend)
                raw = f.source[profiles.PROFILE_KEY]
                self.assertEqual(f.execute(), {"FINISHED"})
                native = next(call for call in f.calls if call[0] == "native install")
                self.assertIsNone(native[2]["capability"])
                self.assertEqual(f.source[profiles.PROFILE_KEY], raw)

    def test_absent_saved_profile_does_not_invent_creation_intent(self):
        f = self.fixture(saved=False)
        self.assertEqual(f.execute(), {"FINISHED"})
        self.assertIsNone(next(call for call in f.calls if call[0] == "native install")[2]["capability"])
        self.assertNotIn(profiles.PROFILE_KEY, f.source)

    def test_unknown_backend_rejects_before_preview_or_native_changes(self):
        f = self.fixture(backend="UNKNOWN_BACKEND")
        before = copy.deepcopy(f.source)
        self.assertEqual(f.execute(), {"CANCELLED"})
        self.assertEqual(f.calls, [])
        self.assertEqual(f.source, before)
        self.assertFalse(hasattr(f.settings, "source"))

    def test_unknown_stale_and_invalid_saved_profiles_reject_before_preview(self):
        for issue in ("unknown capability", "stale owner", "invalid settings", "unreadable"):
            with self.subTest(issue=issue):
                f = self.fixture()
                value = json.loads(f.source[profiles.PROFILE_KEY])
                if issue == "unknown capability": value["capability"] = "OTHER"
                elif issue == "stale owner": value["identity"]["owner"] = "other Dress"
                elif issue == "invalid settings": value["settings"]["gravity"] = 999.
                f.source[profiles.PROFILE_KEY] = "unreadable" if issue == "unreadable" else json.dumps(value)
                before = copy.deepcopy(f.source)
                self.assertEqual(f.execute(), {"CANCELLED"})
                self.assertEqual(f.calls, [])
                self.assertEqual(f.source, before)
                self.assertFalse(hasattr(f.settings, "source"))

    def test_native_failure_does_not_overwrite_artist_profile_or_remove_existing_setup(self):
        f = self.fixture(fail=True)
        before_source, before_record = copy.deepcopy(f.source), copy.deepcopy(f.record)
        self.assertEqual(f.execute(), {"CANCELLED"})
        self.assertEqual(f.source, before_source)
        self.assertEqual(f.record, before_record)
        self.assertNotIn(("select",), f.calls)
        self.assertNotIn(("remember",), f.calls)


if __name__ == "__main__": unittest.main()
