"""Direct Bake admission and metadata-only buttons; no Blender import.

Execute the actual generator's first step and the actual provider guard. Native
sealed cache preservation still requires an isolated Blender verification.
"""
import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[1] / "addons/character_designer"


def load(filename, names, scope, constants=()):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                 and target.id in constants for target in node.targets)]
    found = {node.name for node in nodes if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert found == set(names), (filename, found, names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / filename), "exec"), scope)
    return scope


def provider(cloth):
    return load("skirt_surface_direct.py", {"SkirtDirectError", "_require", "_state", "_use_cloth", "require_bake_ready"},
                dict(json=json, profiles=NS(MODES=("AUTOMATIC", "MANUAL")),
                     _object=lambda *_args: NS(modifiers=[cloth])),
                {"STATE_KEY", "VERSION"})


class ContextTouched(RuntimeError):
    pass


class UnavailableContext:
    def __getattr__(self, name):
        raise ContextTouched("Native context accessed: " + name)


class BakeAdmission(unittest.TestCase):
    def setUp(self):
        self.cloth = NS(type="CLOTH", show_viewport=True, show_render=True)
        self.direct = provider(self.cloth)
        self.state = dict(version=self.direct["VERSION"], owner="owned", mode="AUTOMATIC",
                          editing=False, pending=False)
        self.source = {self.direct["STATE_KEY"]: json.dumps(self.state)}
        self.record = {"owner": "owned", "physics": {"backend": "DIRECT_MAIN_CLOTH_V1"}}
        self.calls = []

        def guard(source, record):
            self.calls.append("guard")
            return self.direct["require_bake_ready"](source, record)

        scope = dict(skirt_rig=NS(_require_controls_for_setup=lambda _source: self.calls.append("controls")),
                     _record=lambda _source: (self.record, object()),
                     _cloth=lambda _record: (object(), NS()),
                     _surface_module=lambda _record: NS(require_bake_ready=guard),
                     clear_cache=lambda *_args: self.fail("Cache was freed before admission"),
                     _set_object_mode=lambda *_args: self.fail("Context was edited before admission"))
        self.physics = load("skirt_physics.py", {"SkirtPhysicsError", "_record_backend", "backend", "bake_steps"},
                            scope, {"LEGACY_BACKEND", "ACTUAL_SURFACE_BACKEND",
                                    "DIRECT_SURFACE_BACKEND", "SURFACE_BACKENDS"})

    def step(self, kind="SIMULATION"):
        return next(self.physics["bake_steps"](UnavailableContext(), self.source, 1, 60, kind=kind))

    def test_manual_editing_pending_and_malformed_state_reject_before_context_or_free(self):
        for changes in ({"mode": "MANUAL"}, {"editing": True}, {"pending": True},
                        {"pending": 0}, {"owner": "foreign"}):
            with self.subTest(changes=changes):
                self.source[self.direct["STATE_KEY"]] = json.dumps({**self.state, **changes})
                before = copy.deepcopy(self.source)
                self.calls.clear()
                with self.assertRaises(self.direct["SkirtDirectError"]):
                    self.step()
                self.assertEqual(self.calls, ["controls", "guard"])
                self.assertEqual(self.source, before)

    def test_ready_automatic_reaches_original_context_boundary_without_state_write(self):
        before = copy.deepcopy(self.source)
        with self.assertRaises(ContextTouched):
            self.step()
        self.assertEqual(self.calls, ["controls", "guard"])
        self.assertEqual(self.source, before)

    def test_disabled_native_cloth_rejects_before_context_or_free(self):
        for field, value in (("type", "ARMATURE"), ("show_viewport", False), ("show_render", False)):
            with self.subTest(field=field):
                old = getattr(self.cloth, field)
                before = copy.deepcopy(self.source)
                setattr(self.cloth, field, value)
                try:
                    with self.assertRaises(self.direct["SkirtDirectError"]):
                        self.step()
                    self.assertEqual(self.source, before)
                finally:
                    setattr(self.cloth, field, old)

    def test_surface_bone_animation_rejection_precedes_direct_guard(self):
        for backend in ("DIRECT_MAIN_CLOTH_V1", "ACTUAL_SURFACE_DELTA_V1"):
            with self.subTest(backend=backend):
                self.record["physics"]["backend"] = backend
                self.calls.clear()
                with self.assertRaisesRegex(self.physics["SkirtPhysicsError"], "per-vertex Cloth"):
                    self.step(kind="ANIMATION")
                self.assertEqual(self.calls, ["controls"])

    def test_delta_and_legacy_simulation_do_not_call_direct_guard(self):
        for physics in ({"backend": "ACTUAL_SURFACE_DELTA_V1"}, {}):
            with self.subTest(physics=physics):
                self.record["physics"] = physics
                self.calls.clear()
                with self.assertRaises(ContextTouched):
                    self.step()
                self.assertEqual(self.calls, ["controls"])


class Layout:
    def __init__(self, parent=None):
        self.parent = parent
        self.enabled = True
        self.buttons = [] if parent is None else parent.buttons
        self.labels = [] if parent is None else parent.labels

    def child(self, **_kwargs):
        return Layout(self)

    box = row = column = child

    def allowed(self):
        return self.enabled and (self.parent is None or self.parent.allowed())

    def operator(self, name, **_kwargs):
        self.buttons.append((name, self.allowed()))
        return NS()

    def label(self, *, text, **_kwargs):
        self.labels.append(text)


class BakeButtons(unittest.TestCase):
    def setUp(self):
        self.ui = load("skirt_motion_ui.py", {"draw_motion", "_metadata_mode"}, dict(json=json,
            _skirt=lambda: NS(_idle=lambda _context: True), _rig=lambda: NS(RIG_KEY="rig"),
            _metadata_controls=lambda _source: True))

    def draw(self, backend, state, *, mode="AUTOMATIC"):
        source = {"character_designer_dress_direct_state_v1": json.dumps(state)}
        record = {"physics": {"backend": backend, "baked_range": [1, 60]}}
        self.ui["_profiles"] = lambda: NS(read=lambda *_args: {"mode": mode, "capability": "BOTH"})
        layout = Layout()
        self.ui["draw_motion"](layout, object(), source, record)
        return dict(layout.buttons), layout.labels

    def test_unready_direct_disables_only_bake_and_retains_reset(self):
        states = [{"mode": "MANUAL", "editing": False, "pending": False},
                  {"mode": "AUTOMATIC", "editing": True, "pending": False},
                  {"mode": "AUTOMATIC", "editing": False, "pending": True},
                  {"mode": "AUTOMATIC", "editing": False, "pending": 0}, {}, None]
        for state in states:
            with self.subTest(state=state):
                buttons, labels = self.draw("DIRECT_MAIN_CLOTH_V1", state)
                self.assertFalse(buttons["character_designer.skirt_bake_physics"])
                self.assertTrue(buttons["character_designer.dress_motion_reset"])
                self.assertIn("After manual changes, Reset and bake again.", labels)

    def test_ready_direct_and_existing_delta_buttons_remain_available(self):
        state = {"mode": "AUTOMATIC", "editing": False, "pending": False}
        buttons, _labels = self.draw("DIRECT_MAIN_CLOTH_V1", state)
        self.assertTrue(buttons["character_designer.skirt_bake_physics"])
        for backend in ("ACTUAL_SURFACE_DELTA_V1", "LEGACY_CAGE"):
            buttons, labels = self.draw(backend, None, mode="MANUAL")
            self.assertTrue(buttons["character_designer.skirt_bake_physics"])
            self.assertTrue(buttons["character_designer.dress_motion_reset"])
            self.assertIn("Baked motion. Reset before changing tuning.", labels)

    def test_profile_manual_also_disables_bake_without_native_validation(self):
        state = {"mode": "AUTOMATIC", "editing": False, "pending": False}
        buttons, _labels = self.draw("DIRECT_MAIN_CLOTH_V1", state, mode="MANUAL")
        self.assertFalse(buttons["character_designer.skirt_bake_physics"])
        self.assertTrue(buttons["character_designer.dress_motion_reset"])


if __name__ == "__main__":
    unittest.main()
