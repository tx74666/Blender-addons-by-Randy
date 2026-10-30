"""Lightweight Dress UI routing and visibility checks; no Blender process."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_ui():
    package_name = "_dress_wiggle_ui_fixture"
    package = ModuleType(package_name)
    package.__path__ = [str(ROOT / "addons" / "character_designer")]
    rig = ModuleType(package_name + ".skirt_rig")
    bpy = ModuleType("bpy")
    properties, types = ModuleType("bpy.props"), ModuleType("bpy.types")
    properties.BoolProperty = properties.EnumProperty = lambda **kwargs: kwargs
    types.Operator = type("Operator", (), {})
    types.WindowManager = type("WindowManager", (), {})
    bpy.props, bpy.types = properties, types
    modules = {package_name: package, package_name + ".skirt_rig": rig,
               "bpy": bpy, "bpy.props": properties, "bpy.types": types}
    name = package_name + ".skirt_wiggle_ui"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "addons" / "character_designer" / "skirt_wiggle_ui.py")
    ui = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(ui)
    return ui


ui = load_ui()


class Layout:
    def __init__(self, events=None, parent=None):
        self.events = [] if events is None else events
        self.parent, self._enabled = parent, True

    @property
    def enabled(self):
        return self._enabled and (self.parent is None or self.parent.enabled)

    @enabled.setter
    def enabled(self, value):
        self._enabled = value

    def row(self, **kwargs):
        return Layout(self.events, self)

    def label(self, **kwargs):
        self.events.append(("label", kwargs.get("text"), self.enabled, kwargs))

    def operator(self, operation, **kwargs):
        values = SimpleNamespace()
        self.events.append((operation, kwargs.get("text"), self.enabled, kwargs, values))
        return values


class DressWiggleUITests(unittest.TestCase):
    def setUp(self):
        self.source = SimpleNamespace(name="Dress")
        self.context = SimpleNamespace(mode="POSE", window_manager=SimpleNamespace())
        self.record = {"chains": [], "owner": "owned Dress"}
        self.workflow = SimpleNamespace(_idle=lambda context: True,
                                        _has_setup=lambda context: True)

    def draw(self, *, available=True, original=False, mode="MANUAL",
             original_supported=True, controls_visible=True, desired=None):
        layout = Layout()
        state = {"available": available, "mode": mode, "message": "Current Dress mode.",
                 "running": mode == "AUTOMATIC"}
        with patch.object(ui, "_motion", return_value=SimpleNamespace(status=lambda *args: state)), \
             patch.object(ui, "_physics_choices", return_value=SimpleNamespace(
                 _skirt_choice=lambda *args: {'mode': mode if desired is None else desired})), \
             patch.object(ui, "_workflow", return_value=self.workflow), \
             patch.object(ui, "_original", return_value=original), \
             patch.object(ui, "_original_supported", return_value=original_supported), \
             patch.object(ui, "_controls_visible", return_value=controls_visible), \
             patch.object(ui, "_layer_visible", return_value=False):
            self.assertTrue(ui.draw_wiggle_motion(layout, self.context, self.source, self.record))
        return layout.events

    def test_unavailable_backend_disables_only_automatic_and_keeps_manual(self):
        events = self.draw(available=False)
        automatic = next(event for event in events if event[1] == "Automatic · Wiggle Bones")
        manual = next(event for event in events if event[1] == "Manual")
        self.assertFalse(automatic[2])
        self.assertTrue(manual[2])
        self.assertEqual(automatic[4].mode, "AUTOMATIC")
        self.assertEqual(manual[4].mode, "MANUAL")
        self.assertTrue(manual[3]["depress"])

    def test_default_ui_keeps_helper_layers_collapsed_and_exposes_display_choices(self):
        events = self.draw()
        labels = [event[1] for event in events]
        self.assertIn("Controls", labels)
        self.assertIn("Original", labels)
        self.assertNotIn("MCH", labels)
        self.assertNotIn("PHYS", labels)
        self.assertNotIn("Tuning", labels)
        self.assertNotIn("Bake", labels)
        self.assertFalse(hasattr(self.context.window_manager, ui.ADVANCED_PROPERTY))

    def test_advanced_inspection_routes_visibility_only(self):
        setattr(self.context.window_manager, ui.ADVANCED_PROPERTY, True)
        events = self.draw(mode="AUTOMATIC")
        for layer in ("MCH", "PHYS"):
            event = next(event for event in events if event[1] == layer)
            self.assertEqual(event[0], "character_designer.dress_wiggle_display")
            self.assertEqual(event[4].layer, layer)
            self.assertTrue(event[2])

    def test_original_disables_mode_changes_but_keeps_controls_return(self):
        events = self.draw(original=True)
        for name in ("Automatic · Wiggle Bones", "Manual"):
            self.assertFalse(next(event for event in events if event[1] == name)[2])
        self.assertTrue(next(event for event in events if event[1] == "Controls")[2])
        self.assertTrue(next(event for event in events if event[1] == "Original")[3]["depress"])

    def test_helper_inspection_does_not_mark_controls_as_current(self):
        events = self.draw(controls_visible=False)
        self.assertFalse(next(event for event in events if event[1] == "Controls")[3]["depress"])

    def test_unattached_original_is_disabled_with_clear_status(self):
        events = self.draw(original_supported=False)
        self.assertFalse(next(event for event in events if event[1] == "Original")[2])
        self.assertTrue(next(event for event in events if event[1] == "Controls")[2])
        self.assertIn("Original needs organized Main Rig bones.", [event[1] for event in events])

    def test_pending_surface_record_is_not_migrated_or_drawn(self):
        self.record["physics"] = {"backend": "DIRECT_MAIN_CLOTH_V1"}
        layout = Layout()
        with patch.object(ui, "_motion", side_effect=AssertionError("Pending route imported")):
            self.assertFalse(ui.draw_wiggle_motion(layout, self.context, self.source, self.record))
        self.assertEqual(layout.events, [])

    def test_manual_freeze_success_survives_optional_display_failure(self):
        operator = ui.CHARACTERDESIGNER_OT_dress_wiggle_mode()
        operator.mode = "MANUAL"
        reports = []
        operator.report = lambda level, message: reports.append((level, message))
        workflow = SimpleNamespace(_source=lambda context: self.source,
                                   _report=lambda *args, **kwargs: None)
        calls = []
        choices = SimpleNamespace(set_skirt_mode=lambda *args, **kwargs: calls.append((args, kwargs)))
        with patch.object(ui, "_workflow", return_value=workflow), \
             patch.object(ui, "_physics_choices", return_value=choices), \
             patch.object(ui.skirt_rig, "read_record", return_value=self.record, create=True), \
             patch.object(ui, "_show_layer", side_effect=ValueError("Display unavailable")):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.assertEqual(calls, [((self.context, "Dress", "MANUAL"), {"expected_owner": "owned Dress"})])
        self.assertEqual(reports[0][0], {"WARNING"})

    def test_desired_choice_is_displayed_even_when_native_mode_is_suspended(self):
        events = self.draw(mode="MANUAL", desired="AUTOMATIC")
        self.assertTrue(next(event for event in events if event[1] == "Automatic · Wiggle Bones")[3]["depress"])
        self.assertFalse(next(event for event in events if event[1] == "Manual")[3]["depress"])

    def test_rejected_service_mode_does_not_change_optional_display(self):
        operator = ui.CHARACTERDESIGNER_OT_dress_wiggle_mode()
        operator.mode = "MANUAL"
        reports = []
        workflow = SimpleNamespace(_source=lambda context: self.source,
            _report=lambda *args, **kwargs: reports.append((args, kwargs)))
        choices = SimpleNamespace(set_skirt_mode=lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("owner changed")))
        with patch.object(ui, "_workflow", return_value=workflow), \
             patch.object(ui, "_physics_choices", return_value=choices), \
             patch.object(ui.skirt_rig, "read_record", return_value=self.record, create=True), \
             patch.object(ui, "_show_layer") as display:
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        display.assert_not_called()
        self.assertIn("owner changed", reports[0][0][2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
