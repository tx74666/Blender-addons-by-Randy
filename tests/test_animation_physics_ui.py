"""Physics UI routing and paused-state behavior without launching Blender."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[1]


def load_ui():
    package = ModuleType("_animation_physics_ui_fixture")
    package.__path__ = [str(ROOT / "addons" / "character_designer")]
    properties, types = ModuleType("bpy.props"), ModuleType("bpy.types")
    properties.BoolProperty = properties.EnumProperty = properties.StringProperty = lambda **kwargs: kwargs
    types.Operator = type("Operator", (), {})
    bpy = ModuleType("bpy")
    bpy.props, bpy.types = properties, types
    modules = {package.__name__: package, "bpy": bpy, "bpy.props": properties, "bpy.types": types}
    name = package.__name__ + ".animation_physics_ui"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "addons" / "character_designer" / "animation_physics_ui.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


ui = load_ui()


class Layout:
    def __init__(self, events=None, parent=None):
        self.events = [] if events is None else events
        self.parent, self._enabled = parent, True
        self.alignment = "EXPAND"

    @property
    def enabled(self):
        return self._enabled and (self.parent is None or self.parent.enabled)

    @enabled.setter
    def enabled(self, value):
        self._enabled = value

    def row(self, **_kwargs):
        return Layout(self.events, self)

    def column(self, **_kwargs):
        return Layout(self.events, self)

    def box(self):
        return Layout(self.events, self)

    def label(self, **kwargs):
        self.events.append(("label", kwargs.get("text"), self.enabled, kwargs, None))

    def operator(self, operation, **kwargs):
        operator = SimpleNamespace()
        self.events.append((operation, kwargs.get("text"), self.enabled, kwargs, operator, self))
        return operator


class AnimationPhysicsUITests(unittest.TestCase):
    def setUp(self):
        self.rig = SimpleNamespace(name="Character A", as_pointer=lambda: 101)
        self.context = SimpleNamespace(mode="POSE", area=SimpleNamespace(tag_redraw=Mock()),
                                       region=SimpleNamespace(width=640))
        self.state = {
            "rig": self.rig, "valid": True, "enabled": True, "skirt_enabled": True, "hair_enabled": False,
            "control_mode": True, "control_error": "",
            "dresses": [{"source": "Dress", "name": "Dress", "owner": "dress-owner-1",
                         "mode": "AUTOMATIC", "backend": "WIGGLE", "available": True,
                         "stopped_confirmed": True,
                         "message": ""}],
            "hair": {"sources": ["Hair"], "active": False, "available": True,
                     "stopped_confirmed": True, "message": ""},
            "message": "",
        }
        self.service = SimpleNamespace(status=Mock(side_effect=lambda context: self.state),
                                       set_enabled=Mock(), set_component=Mock(), set_skirt_mode=Mock(),
                                       start_hair_preview=Mock(), stop_hair_preview=Mock(),
                                       dependency_status=Mock(), enable_dependency=Mock(),
                                       return_to_controls=Mock(), set_control_mode=Mock())

    def draw(self):
        layout = Layout()
        with patch.object(ui, "_service", return_value=self.service):
            ui.draw(layout, self.context)
        self.service.set_enabled.assert_not_called()
        self.service.set_component.assert_not_called()
        self.service.set_skirt_mode.assert_not_called()
        self.service.start_hair_preview.assert_not_called()
        self.service.stop_hair_preview.assert_not_called()
        self.service.enable_dependency.assert_not_called()
        self.service.dependency_status.assert_not_called()
        self.service.return_to_controls.assert_not_called()
        self.service.set_control_mode.assert_not_called()
        return layout.events

    def event(self, events, label):
        if label == "Enable Physics":
            return next(event for event in events if event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_set.bl_idname
                        and getattr(event[4], "component", None) == "ALL")
        return next(event for event in events if event[1] == label)

    def operator(self, cls):
        operator = cls()
        operator.target_name, operator.target_pointer = self.rig.name, "101"
        operator.report = Mock()
        return operator

    def test_master_uses_checkbox_and_inverse_target_value(self):
        event = self.event(self.draw(), "Enable Physics")
        self.assertEqual(event[3]["icon"], "CHECKBOX_HLT")
        self.assertFalse(event[4].enabled)
        self.assertEqual(event[4].component, "ALL")
        self.assertEqual((event[4].target_name, event[4].target_pointer), ("Character A", "101"))

    def test_master_checkbox_is_in_physics_header_without_duplicate_name_rows(self):
        header, body = Layout(), Layout()
        def panels(layout, identifier, **_kwargs):
            if identifier == "character_designer_animation_physics":
                return header, body
            return Layout(body.events), Layout(body.events)
        with patch.object(ui, "_panel", side_effect=panels), patch.object(ui, "_service", return_value=self.service):
            ui.draw(Layout(), self.context)
        self.assertEqual(header.events[0][1], "Physics")
        master = self.event(header.events, "Enable Physics")
        self.assertEqual(master[1], "")
        self.assertFalse(any(event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_set.bl_idname
                             and getattr(event[4], "component", None) == "ALL" for event in body.events))
        labels = [event[1] for event in header.events + body.events]
        self.assertNotIn("Enable Physics", labels)
        self.assertNotIn("Character: Character A", labels)

    def test_collapsed_physics_keeps_header_master_available(self):
        header = Layout()
        with patch.object(ui, "_panel", return_value=(header, None)), patch.object(ui, "_service", return_value=self.service):
            ui.draw(Layout(), self.context)
        self.assertTrue(self.event(header.events, "Enable Physics")[2])
        self.assertEqual(len(header.events), 2)
        self.service.set_enabled.assert_not_called()

    def test_components_are_direct_left_aligned_checkboxes_without_empty_folds(self):
        header, body = Layout(), Layout()
        with patch.object(ui, "_panel", return_value=(header, body)) as panels, patch.object(ui, "_service", return_value=self.service):
            ui.draw(Layout(), self.context)
        panels.assert_called_once()
        self.assertEqual(panels.call_args.args[1], "character_designer_animation_physics")
        for component in ("Skirt", "Hair"):
            checkbox = self.event(body.events, component)
            self.assertEqual(checkbox[5].alignment, "LEFT")
            self.assertEqual(checkbox[0], ui.CHARACTERDESIGNER_OT_animation_physics_set.bl_idname)
        self.assertNotIn("Dress", [event[1] for event in body.events])
        self.assertNotIn("CTL", [event[1] for event in body.events])
        modes = [event for event in body.events
                 if event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_skirt_mode.bl_idname]
        self.assertEqual(len(modes), 1)
        self.assertEqual(modes[0][1], "Control Mode")
        self.assertFalse(any(event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_controls.bl_idname
                             for event in body.events))

    def test_multiple_dresses_keep_source_names_and_source_scoped_modes(self):
        self.state["dresses"].append({"source": "Coat", "name": "Coat", "owner": "coat-owner",
                                      "mode": "MANUAL", "applied_mode": "AUTOMATIC", "mode_pending": True})
        events = self.draw()
        self.assertIn("Dress", [event[1] for event in events])
        self.assertIn("Coat", [event[1] for event in events])
        self.assertNotIn("CTL", [event[1] for event in events])
        modes = [event for event in events
                 if event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_skirt_mode.bl_idname]
        self.assertEqual([(event[4].source_name, event[4].source_owner, event[4].mode,
                           event[3]["depress"]) for event in modes],
                         [("Dress", "dress-owner-1", "MANUAL", False),
                          ("Coat", "coat-owner", "AUTOMATIC", True)])
        self.assertTrue(all(event[5].alignment == "RIGHT" for event in modes))

    def test_retained_mode_operator_routes_explicit_choice_even_when_original_and_master_off(self):
        self.state["enabled"] = False
        self.state["dresses"][0].update(mode="MANUAL", applied_mode="AUTOMATIC", mode_pending=True,
                                          requires_controls=True, context_blocked=True)
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_skirt_mode)
        operator.source_name, operator.source_owner, operator.mode = "Dress", "dress-owner-1", "AUTOMATIC"
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_skirt_mode.assert_called_once_with(
            self.context, "Dress", "AUTOMATIC", expected_owner="dress-owner-1")
        self.service.return_to_controls.assert_not_called()
        self.service.set_enabled.assert_not_called()

    def test_normal_manual_and_original_pause_messages_need_no_details(self):
        for message in ("Manual keeps the current skirt shape.",
                        "Manual keeps the current shape; pose the rings and handles.",
                        "Original editing paused automatic Dress motion.",
                        "Automatic Dress motion paused; the current shape is kept.",
                        "Scene editing paused automatic Dress motion; the current shape is kept.",
                        "Automatic is stopped. Choose Automatic to start."):
            self.state["dresses"][0]["message"] = message
            labels = [event[1] for event in self.draw()]
            self.assertNotIn(message, labels)
            self.assertNotIn("Details", labels)

    def test_normal_hair_stop_reason_is_hidden_but_guard_failure_stays_visible(self):
        self.state["hair"]["message"] = "Stopped"
        self.assertNotIn("Stopped", [event[1] for event in self.draw()])
        self.state["hair"]["message"] = "Hair preview guard stopped: source ownership changed."
        self.assertIn("Hair physics stopped: source ownership changed.", [event[1] for event in self.draw()])

    def test_master_off_retains_preferences_and_keeps_skirt_mode_selectable(self):
        self.state.update(enabled=False, hair_enabled=True)
        events = self.draw()
        for label in ("Skirt", "Hair"):
            self.assertFalse(self.event(events, label)[2])
            self.assertEqual(self.event(events, label)[3]["icon"], "CHECKBOX_HLT")
        self.assertNotIn("CTL", [event[1] for event in events])
        self.assertNotIn("Physics is off.", [event[1] for event in events])
        self.assertTrue(self.event(events, "Control Mode")[2])

    def test_saved_true_stopped_auto_skirt_is_unchecked_and_on_can_restart(self):
        self.state["skirt_enabled"] = True
        dress = self.state["dresses"][0]
        for state in ({"active": False, "running": False, "paused": False},
                      {"active": True, "running": False, "paused": True},
                      {"active": True, "running": False, "paused": False},
                      {"active": True, "running": True, "paused": False, "pending_recovery": True}):
            with self.subTest(state=state):
                dress.update(active=False, running=False, paused=False, pending_recovery=False)
                dress.update(state)
                checkbox = self.event(self.draw(), "Skirt")
                self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
                self.assertTrue(checkbox[2])
                self.assertEqual((checkbox[4].component, checkbox[4].enabled), ("SKIRT", True))

    def test_running_auto_skirt_checkbox_targets_off(self):
        self.state["dresses"][0].update(active=True, running=True, paused=False)
        checkbox = self.event(self.draw(), "Skirt")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_HLT")
        self.assertFalse(checkbox[4].enabled)
        self.state["skirt_enabled"] = False
        checkbox = self.event(self.draw(), "Skirt")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(checkbox[4].enabled)

    def test_manual_skirt_is_unchecked_and_on_requests_physics(self):
        self.state["dresses"][0].update(mode="MANUAL", active=False, running=False, paused=False)
        checkbox = self.event(self.draw(), "Skirt")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(checkbox[4].enabled)
        self.assertNotIn("CTL", [event[1] for event in self.draw()])

    def test_original_queued_auto_is_checked_and_can_be_cancelled(self):
        self.state["dresses"][0].update(mode="AUTOMATIC", active=False, running=False,
            requires_controls=True, context_blocked=True, mode_pending=True)
        events = self.draw()
        checkbox = self.event(events, "Skirt")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_HLT")
        self.assertTrue(checkbox[2])
        self.assertFalse(checkbox[4].enabled)
        self.assertNotIn("Skirt physics will start after returning to Controls.",
                         [event[1] for event in events])
        self.assertTrue(self.event(events, "Control Mode")[2])


    def test_queued_checkbox_never_hides_failure_or_recovery(self):
        dress = self.state["dresses"][0]
        dress.update(mode="AUTOMATIC", active=False, running=False, mode_pending=True,
                     context_blocked=True, requires_controls=True)
        for changes in ({"last_error": "The owned skirt changed."}, {"pending_recovery": True}):
            with self.subTest(changes=changes):
                dress.update(last_error="", pending_recovery=False)
                dress.update(changes)
                events = self.draw()
                checkbox = self.event(events, "Skirt")
                self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
                self.assertTrue(checkbox[4].enabled)
                self.assertNotIn("Skirt physics will start after returning to Controls.",
                                 [event[1] for event in events])
                self.assertIn(changes.get("last_error", "Skirt physics needs review."),
                              [event[1] for event in events])

    def test_unqueued_original_and_edit_context_are_not_shown_as_enabled_physics(self):
        dress = self.state["dresses"][0]
        for changes in ({"mode_pending": False, "requires_controls": True},
                        {"mode_pending": True, "requires_controls": False}):
            with self.subTest(changes=changes):
                dress.update(mode="AUTOMATIC", active=False, running=False, context_blocked=True)
                dress.update(changes)
                events = self.draw()
                checkbox = self.event(events, "Skirt")
                self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
                self.assertTrue(checkbox[4].enabled)
                self.assertNotIn("Skirt physics will start after returning to Controls.",
                                 [event[1] for event in events])

    def test_multiple_pending_skirts_have_independent_modes_without_waiting_notice(self):
        self.state["dresses"] = [dict(source=name, owner=name, mode="AUTOMATIC",
            mode_pending=True, context_blocked=True, requires_controls=True) for name in ("Dress", "Coat")]
        events = self.draw()
        self.assertEqual(self.event(events, "Skirt")[3]["icon"], "CHECKBOX_HLT")
        notice = "Skirt physics will start after returning to Controls."
        self.assertNotIn(notice, [event[1] for event in events])
        self.assertEqual([event[1] for event in events].count("Control Mode"), 2)
        self.state["skirt_enabled"] = False
        self.assertNotIn(notice, [event[1] for event in self.draw()])

    def test_known_unavailable_dependency_does_not_promise_queued_original_start(self):
        dress = self.state["dresses"][0]
        dress.update(mode="AUTOMATIC", active=False, running=False, mode_pending=True,
                     context_blocked=True, requires_controls=True)
        for kind in ("MISSING", "DISABLED", "INCOMPATIBLE", "CLEANUP_FAILED"):
            with self.subTest(kind=kind):
                dress["dependency"] = {"state": kind, "enable_available": kind == "DISABLED",
                                       "message": "The required backend is unavailable."}
                events = self.draw()
                checkbox = self.event(events, "Skirt")
                self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
                self.assertTrue(checkbox[4].enabled)
                self.assertNotIn("Skirt physics will start after returning to Controls.",
                                 [event[1] for event in events])
                self.assertIn("Get Wiggle 2" if kind == "MISSING" else
                              "The required backend is unavailable.", [event[1] for event in events])

    def test_multi_auto_skirt_header_requires_all_auto_sources_running(self):
        self.state["dresses"][0].update(active=True, running=True, paused=False)
        self.state["dresses"].append({"source": "Coat", "owner": "coat-owner", "mode": "AUTOMATIC",
                                      "active": False, "running": False, "paused": False})
        checkbox = self.event(self.draw(), "Skirt")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(checkbox[4].enabled)
        self.state["dresses"][1].update(active=True, running=True)
        checkbox = self.event(self.draw(), "Skirt")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_HLT")
        self.assertFalse(checkbox[4].enabled)
        self.state["dresses"][1].update(mode="MANUAL", active=False, running=False)
        self.assertEqual(self.event(self.draw(), "Skirt")[3]["icon"], "CHECKBOX_DEHLT")

    def test_master_off_shows_saved_skirt_permission_even_when_auto_stopped(self):
        self.state.update(enabled=False, skirt_enabled=True)
        self.state["dresses"][0].update(active=False, running=False, paused=True)
        checkbox = self.event(self.draw(), "Skirt")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_HLT")
        self.assertFalse(checkbox[2])
        self.state["skirt_enabled"] = False
        self.assertEqual(self.event(self.draw(), "Skirt")[3]["icon"], "CHECKBOX_DEHLT")

    def test_paused_manual_desired_mode_keeps_its_selected_skirt_mode(self):
        self.state["enabled"] = False
        self.state["dresses"][0].update(mode="MANUAL", applied_mode="AUTOMATIC", mode_pending=True)
        events = self.draw()
        self.assertNotIn("CTL", [event[1] for event in events])
        self.assertFalse(self.event(events, "Skirt")[2])
        mode = self.event(events, "Control Mode")
        self.assertTrue(mode[3]["depress"])
        self.assertTrue(mode[2])
        self.assertEqual(mode[4].mode, "AUTOMATIC")

    def test_unavailable_backend_keeps_skirt_checkbox_and_failure_visible(self):
        self.state["dresses"][0].update(available=False, message="Enable the supported Wiggle extension first.")
        events = self.draw()
        self.assertTrue(self.event(events, "Skirt")[2])
        self.assertNotIn("CTL", [event[1] for event in events])
        self.assertIn("Enable the supported Wiggle extension first.", [event[1] for event in events])
        self.assertNotIn("Hair preview running.", [event[1] for event in events])

    def test_missing_character_never_targets_current_selection(self):
        self.state["rig"] = None
        events = self.draw()
        self.assertFalse(self.event(events, "Enable Physics")[2])
        self.assertFalse(any(event[1] == "Automatic" for event in events))
        self.assertIn("Choose the character's Main Rig first.", [event[1] for event in events])

    def test_invalid_saved_state_does_not_draw_fresh_default_permissions(self):
        self.state.update(valid=False, message="Saved Physics permissions need review.")
        events = self.draw()
        self.assertTrue(all(event[0] == "label" for event in events))
        self.assertIn("Saved Physics permissions need review.", [event[1] for event in events])

    def test_edit_mode_disables_mutating_buttons(self):
        self.context.mode = "EDIT_ARMATURE"
        events = self.draw()
        self.assertTrue(all(not event[2] for event in events if event[0] != "label"))

    def test_empty_setups_are_explicit_and_component_controls_disabled(self):
        self.state.update(dresses=[], hair={"sources": [], "active": False})
        events = self.draw()
        self.assertFalse(self.event(events, "Skirt")[2])
        self.assertFalse(self.event(events, "Hair")[2])
        self.assertIn("No skirt setup on this character.", [event[1] for event in events])
        self.assertIn("No initialized Hair motion on this character.", [event[1] for event in events])

    def test_master_operator_routes_to_service_once(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "ALL", False
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_enabled.assert_called_once_with(self.context, False)
        self.service.set_component.assert_not_called()
        self.context.area.tag_redraw.assert_called_once()

    def test_explicit_on_checks_unknown_dependency_once_without_enabling_extension(self):
        for component, backend in (("ALL", "SKIRT"), ("SKIRT", "SKIRT"), ("HAIR", "HAIR")):
            with self.subTest(component=component):
                self.service.dependency_status.reset_mock()
                self.service.dependency_status.side_effect = [{"state": "UNCHECKED"}, {"state": "READY"}]
                operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
                operator.component, operator.enabled = component, True
                with patch.object(ui, "_service", return_value=self.service):
                    self.assertEqual(operator.execute(self.context), {"FINISHED"})
                self.assertEqual(self.service.dependency_status.call_args_list,
                                 [call(backend), call(backend, refresh=True)])
        self.service.enable_dependency.assert_not_called()

    def test_on_uses_cached_dependency_and_off_never_scans(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "HAIR", True
        self.service.dependency_status.return_value = {"state": "READY"}
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.dependency_status.assert_called_once_with("HAIR")
        self.service.dependency_status.reset_mock()
        operator.enabled = False
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.dependency_status.assert_not_called()

    def test_unknown_dependency_refresh_error_refuses_before_permission_write(self):
        self.service.dependency_status.side_effect = [{"state": "UNCHECKED"}, ValueError("Dependency inventory failed.")]
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "HAIR", True
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        operator.report.assert_called_once_with({"ERROR"}, "Dependency inventory failed.")
        self.service.set_component.assert_not_called()
        self.service.enable_dependency.assert_not_called()

    def test_missing_backend_after_explicit_on_exposes_download_without_false_start(self):
        def inspect(component, **kwargs):
            if kwargs.get("refresh"):
                dependency = {"state": "MISSING", "label": "Wiggle 2", "message": "Not installed."}
                self.state["hair"]["dependency"] = dependency
                return dependency
            return {"state": "UNCHECKED"}
        self.service.dependency_status.side_effect = inspect
        self.service.set_component.side_effect = ValueError("Wiggle 2 is not installed.")
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "HAIR", True
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        self.service.set_component.assert_called_once_with(self.context, "HAIR", True)
        self.service.set_component.reset_mock()
        self.service.dependency_status.reset_mock()
        self.event(self.draw(), "Get Wiggle 2")
        operator.report.assert_called_once_with({"ERROR"}, "Wiggle 2 is not installed.")
        self.service.enable_dependency.assert_not_called()

    def test_ctl_auto_checks_unknown_dependency_before_preserving_service_mode_rules(self):
        self.service.dependency_status.side_effect = [{"state": "UNCHECKED"}, {"state": "MISSING"}]
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_skirt_mode)
        operator.source_name, operator.source_owner, operator.mode = "Dress", "dress-owner-1", "AUTOMATIC"
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.assertEqual(self.service.dependency_status.call_args_list,
                         [call("SKIRT"), call("SKIRT", refresh=True)])
        self.service.set_skirt_mode.assert_called_once_with(self.context, "Dress", "AUTOMATIC", expected_owner="dress-owner-1")
        self.service.enable_dependency.assert_not_called()

    def test_original_mode_keeps_skirt_switch_and_master_click_routes_off(self):
        self.state["dresses"][0].update(
            requires_controls=True, context_blocked=True, can_change_mode=False,
            block_reason="Finish Original mode in Bone Display first.",
            dependency={"state": "READY", "label": "Wiggle 2 2.2.4", "available": True,
                        "message": "", "details": []})
        events = self.draw()
        master = self.event(events, "Enable Physics")
        self.assertTrue(master[2])
        self.assertEqual(master[3]["icon"], "CHECKBOX_HLT")
        self.assertFalse(master[4].enabled)
        self.assertTrue(self.event(events, "Skirt")[2])
        self.assertNotIn("CTL", [event[1] for event in events])
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        for field in ("component", "enabled", "target_name", "target_pointer"):
            setattr(operator, field, getattr(master[4], field))
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_enabled.assert_called_once_with(self.context, False)
        self.service.set_component.assert_not_called()
        self.service.set_skirt_mode.assert_not_called()
        self.service.return_to_controls.assert_not_called()
        operator.report.assert_not_called()

    def test_hair_component_routes_without_changing_skirt_mode(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "HAIR", True
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_component.assert_called_once_with(self.context, "HAIR", True)
        self.service.set_skirt_mode.assert_not_called()

    def test_character_switch_refuses_before_any_mutation(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "ALL", False
        self.rig.as_pointer = lambda: 202
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        self.service.set_enabled.assert_not_called()
        self.service.set_component.assert_not_called()
        operator.report.assert_called_once()

    def test_invalid_metadata_refuses_operator_before_any_mutation(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "ALL", False
        self.state.update(valid=False, message="An invalid source ownership record needs review.")
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        self.service.set_enabled.assert_not_called()
        operator.report.assert_called_once_with({"ERROR"}, "An invalid source ownership record needs review.")

    def test_skirt_mode_passes_exact_source_owner(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_skirt_mode)
        operator.source_name, operator.source_owner, operator.mode = "Dress", "dress-owner-1", "MANUAL"
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_skirt_mode.assert_called_once_with(
            self.context, "Dress", "MANUAL", expected_owner="dress-owner-1")

    def test_service_refusal_is_visible_and_not_a_false_success(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "ALL", True
        self.service.set_enabled.side_effect = ValueError("An unrelated simulation owns this backend.")
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        operator.report.assert_called_once_with({"ERROR"}, "An unrelated simulation owns this backend.")
        self.context.area.tag_redraw.assert_called_once()

    def test_master_off_genuine_refusal_is_cancelled_without_changing_modes(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "ALL", False
        reason = "An unrelated simulation owns this backend."
        self.service.set_enabled.side_effect = ValueError(reason)
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
            layout = Layout()
            ui.draw(layout, self.context)
        self.service.set_enabled.assert_called_once_with(self.context, False)
        self.service.return_to_controls.assert_not_called()
        self.service.set_skirt_mode.assert_not_called()
        self.service.set_component.assert_not_called()
        operator.report.assert_called_once_with({"ERROR"}, reason)
        master = self.event(layout.events, "Enable Physics")
        self.assertEqual(master[3]["icon"], "CHECKBOX_HLT")
        self.assertFalse(master[4].enabled)

    def test_invalid_status_draws_reason_without_action_buttons(self):
        self.service.status.side_effect = ValueError("Saved physics settings need review.")
        events = self.draw()
        self.assertIn("Saved physics settings need review.", [event[1] for event in events])
        self.assertTrue(all(event[0] == "label" for event in events))

    def test_saved_true_inactive_hair_checkbox_starts_with_one_component_click(self):
        self.state["hair_enabled"] = True
        events = self.draw()
        checkbox = self.event(events, "Hair")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(checkbox[2])
        self.assertTrue(checkbox[4].enabled)
        self.assertEqual(checkbox[4].component, "HAIR")
        self.assertEqual((checkbox[4].target_name, checkbox[4].target_pointer), ("Character A", "101"))
        self.assertFalse(any(event[1] in {"Start Hair Preview", "Resume Hair Preview", "Stop Hair Preview"}
                             for event in events))
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        for field in ("component", "enabled", "target_name", "target_pointer"):
            setattr(operator, field, getattr(checkbox[4], field))
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_component.assert_called_once_with(self.context, "HAIR", True)
        self.service.start_hair_preview.assert_not_called()
        self.service.stop_hair_preview.assert_not_called()
        self.service.set_skirt_mode.assert_not_called()

    def test_paused_hair_checkbox_reflects_permission_without_routine_status(self):
        self.state.update(enabled=False, hair_enabled=True)
        self.state["hair"].update(active=True, paused=True, running=False)
        events = self.draw()
        self.assertFalse(any(label.startswith("Hair preview ") for _op, label, *_ in events if label))
        self.assertEqual(self.event(events, "Hair")[3]["icon"], "CHECKBOX_HLT")
        self.assertFalse(self.event(events, "Hair")[2])
        self.state["enabled"] = True
        checkbox = self.event(self.draw(), "Hair")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(checkbox[2])
        self.assertTrue(checkbox[4].enabled)

    def test_unavailable_hair_shows_reason_without_extra_start_action(self):
        self.state["hair_enabled"] = True
        self.state["hair"].update(available=False, message="Enable Wiggle 2 2.2.4 first.")
        events = self.draw()
        self.assertEqual(self.event(events, "Hair")[3]["icon"], "CHECKBOX_DEHLT")
        self.assertNotIn("Start Hair Preview", [event[1] for event in events])
        self.assertIn("Enable Wiggle 2 2.2.4 first.", [event[1] for event in events])

    def test_running_hair_checkbox_turns_off_without_extra_preview_actions(self):
        self.state.update(playing=True, hair_enabled=True)
        self.state["hair"].update(active=True, paused=False, running=True)
        events = self.draw()
        checkbox = self.event(events, "Hair")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_HLT")
        self.assertTrue(checkbox[2])
        self.assertFalse(checkbox[4].enabled)
        self.assertFalse(any(event[1] in {"Start Hair Preview", "Resume Hair Preview", "Stop Hair Preview",
                                         "Hair preview running."} for event in events))
        self.state["hair_enabled"] = False
        checkbox = self.event(self.draw(), "Hair")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(checkbox[4].enabled)

    def test_ready_hair_keeps_single_checked_control_without_status_row(self):
        self.state["hair"].update(active=True, paused=False, running=True)
        self.state["hair_enabled"] = True
        self.state["playing"] = False
        events = self.draw()
        checkbox = self.event(events, "Hair")
        self.assertEqual(checkbox[3]["icon"], "CHECKBOX_HLT")
        self.assertFalse(checkbox[4].enabled)
        self.assertFalse(any(event[1] in {"Start Hair Preview", "Resume Hair Preview", "Stop Hair Preview",
                                         "Hair preview ready.", "Play animation to preview physics."} for event in events))

    def test_normal_stopped_previews_need_no_routine_status_labels(self):
        self.state.update(enabled=True, skirt_enabled=True, hair_enabled=True, playing=True)
        events = self.draw()
        labels = [event[1] for event in events]
        self.assertFalse(any(label and (label.startswith("Skirt physics ") or label.startswith("Hair preview "))
                             for label in labels))
        self.assertEqual(self.event(events, "Hair")[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(self.event(events, "Hair")[4].enabled)

    def test_skirt_ready_and_running_have_no_extra_status_or_play_help(self):
        self.state["dresses"][0].update(active=True, paused=False, running=True)
        for playing in (False, True):
            self.state["playing"] = playing
            labels = [event[1] for event in self.draw()]
            self.assertNotIn("Skirt physics ready.", labels)
            self.assertNotIn("Skirt physics running.", labels)
            self.assertNotIn("Play animation to preview physics.", labels)
            self.assertNotIn("CTL", [event[1] for event in self.draw()])

    def test_paused_skirt_has_only_the_unchecked_physics_switch(self):
        self.state["dresses"][0].update(active=True, paused=True, running=False)
        events = self.draw()
        self.assertNotIn("CTL", [event[1] for event in events])
        self.assertEqual(self.event(events, "Skirt")[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(self.event(events, "Skirt")[4].enabled)
        self.assertNotIn("Skirt physics paused.", [event[1] for event in events])

    def test_normal_hair_panel_omits_permanent_preview_help(self):
        events = self.draw()
        self.assertNotIn("Blender preview only; Unity physics is separate.", [event[1] for event in events])
        self.assertNotIn("Play animation to preview physics.", [event[1] for event in events])

    def test_stop_hair_is_scoped_undo_action_and_available_with_permission_off(self):
        self.state.update(enabled=False, hair_enabled=False)
        self.state['hair'].update(active=False, paused=False, running=False,
                                  pending_recovery=True, stopped_confirmed=False)
        event = self.event(self.draw(), "Recover Hair")
        self.assertTrue(event[2])
        self.assertEqual(event[4].target_pointer, "101")
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_stop_hair)
        self.assertIn("UNDO", operator.bl_options)
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.stop_hair_preview.assert_called_once_with(self.context)
        self.service.set_component.assert_not_called()

    def test_failed_cleanup_keeps_checkbox_retry_and_actionable_recovery_without_preview_help(self):
        self.state["hair_enabled"] = True
        self.state["hair"].update(active=True, paused=False, running=False, cleanup_pending=True,
                                  stopped_confirmed=False, message="Owned Hair cleanup failed.")
        events = self.draw()
        labels = [event[1] for event in events]
        self.assertNotIn("Hair physics needs review.", labels)
        self.assertIn("Owned Hair cleanup failed.", labels)
        self.assertNotIn("Hair preview stopped.", labels)
        self.assertEqual(self.event(events, "Hair")[3]["icon"], "CHECKBOX_DEHLT")
        self.assertTrue(self.event(events, "Hair")[2])
        self.assertNotIn("Start Hair Preview", labels)
        self.assertNotIn("Resume Hair Preview", labels)
        self.assertTrue(self.event(events, "Recover Hair")[2])

    def test_disabled_extension_has_one_click_enable_action(self):
        self.state["hair"]["dependency"] = {"state": "DISABLED", "label": "Wiggle 2 2.2.4",
            "enable_available": True, "message": "The installed extension is disabled.", "details": []}
        events = self.draw()
        event = self.event(events, "Enable Wiggle")
        self.assertEqual((event[4].component, event[4].action), ("HAIR", "ENABLE"))
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_dependency)
        operator.component, operator.action = "HAIR", "ENABLE"
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.enable_dependency.assert_called_once_with(self.context, "HAIR")
        self.service.dependency_status.assert_not_called()

    def test_explicit_check_is_an_action_not_draw_time_backend_scan(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_dependency)
        operator.component, operator.action = "SKIRT", "CHECK"
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.dependency_status.assert_called_once_with("SKIRT", refresh=True)
        self.service.enable_dependency.assert_not_called()

    def test_unknown_dependency_does_not_add_check_spam_or_scan_on_draw(self):
        self.state["hair"]["dependency"] = {"state": "UNCHECKED", "label": "Wiggle 2 2.2.4",
            "enable_available": False, "message": "Check this installed extension.", "details": []}
        events = self.draw()
        self.assertFalse(any(event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_dependency.bl_idname
                             for event in events))
        self.assertNotIn("Check this installed extension.", [event[1] for event in events])

    def test_already_enabled_incompatible_extension_keeps_readable_reason(self):
        self.state["hair"].update(available=False, dependency={"state": "INCOMPATIBLE",
            "label": "Wiggle 2 2.2.4", "enabled": True, "enable_available": False,
            "message": "The enabled extension has incompatible handlers.", "details": ["Expected exactly one frame callback."]})
        events = self.draw()
        self.assertNotIn("Enable Wiggle", [event[1] for event in events])
        self.assertIn("The enabled extension has incompatible handlers.", [event[1] for event in events])
        self.assertIn("Expected exactly one frame callback.", [event[1] for event in events])

    def test_missing_extension_offers_fixed_download_and_recheck_without_extra_start(self):
        self.state["hair_enabled"] = True
        self.state["hair"].update(available=True, dependency={"state": "MISSING",
            "label": "Wiggle 2 2.2.4", "enable_available": False,
            "message": "The required extension was not found.", "details": []})
        events = self.draw()
        download = self.event(events, "Get Wiggle 2")
        self.assertEqual(download[0], "wm.url_open")
        self.assertEqual(download[4].url, ui.WIGGLE_DOWNLOAD_URL)
        self.assertEqual(download[4].url, "https://github.com/shteeve3d/blender-wiggle-2/blob/02c4f0f1175cff5a6798229e0a5383dfe00f1221/wiggle_2.py")
        self.assertEqual(self.event(events, "Check Again")[4].action, "CHECK")
        self.assertNotIn("Start Hair Preview", [event[1] for event in events])
        self.assertNotIn("Enable Wiggle", [event[1] for event in events])

    def test_ready_dependency_has_no_status_or_refresh_row(self):
        self.state["hair"]["dependency"] = {"state": "READY", "label": "Wiggle 2 2.2.4",
            "enable_available": False, "message": "", "details": []}
        events = self.draw()
        self.assertFalse(any(event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_dependency.bl_idname
                             for event in events))
        self.assertNotIn("Wiggle 2 2.2.4 ready", [event[1] for event in events])

    def test_shared_ready_dependency_is_entirely_silent(self):
        shared = {"state": "READY", "label": "Wiggle 2 2.2.4", "available": True,
                  "message": "Backend success need not be repeated.", "details": ["Verbose success details."]}
        self.state["hair"]["dependency"] = dict(shared)
        self.state["dresses"][0]["dependency"] = dict(shared)
        events = self.draw()
        labels = [event[1] for event in events]
        for text in ("Wiggle 2 2.2.4 ready", shared["message"], shared["details"][0]):
            self.assertNotIn(text, labels)
        self.assertFalse(any(event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_dependency.bl_idname
                             for event in events))

    def test_unconfirmed_pending_flags_are_not_reported_as_stopped_or_paused(self):
        self.state["dresses"][0].update(stopped_confirmed=False, pending_recovery=True)
        self.state["hair"].update(stopped_confirmed=False, pending_recovery=True)
        events = self.draw()
        labels = [event[1] for event in events]
        self.assertIn("Skirt physics needs review.", labels)
        self.assertNotIn("Hair physics needs review.", labels)
        self.assertNotIn("Skirt physics stopped.", labels)
        self.assertNotIn("Hair preview stopped.", labels)
        self.assertNotIn("Skirt physics paused.", labels)
        self.assertTrue(self.event(events, "Hair")[2])
        self.assertEqual(self.event(events, "Hair")[3]["icon"], "CHECKBOX_DEHLT")
        self.assertNotIn("Recover Hair", labels)
        self.assertNotIn("Start Hair Preview", labels)
        self.assertNotIn("Resume Hair Preview", labels)

    def test_failed_action_matching_original_blocker_remains_visible(self):
        reason = "Finish Original mode in Bone Display first."
        self.state["dresses"][0].update(requires_controls=True, can_change_mode=False,
            block_reason=reason, message=reason, last_error=reason)
        events = self.draw()
        self.assertTrue(self.event(events, "Skirt")[2])
        self.assertIn(reason, [event[1] for event in events])
        self.assertEqual(self.event(events, reason)[3]["icon"], "ERROR")
        self.assertEqual(self.event(events, "Skirt")[3]["icon"], "CHECKBOX_DEHLT")
        self.assertNotIn("Details", [event[1] for event in events])

    def test_disabled_dependency_keeps_skirt_switch_and_enable_action(self):
        self.state["dresses"][0]["dependency"] = {"state": "DISABLED", "label": "Wiggle 2 2.2.4",
            "enable_available": True, "message": "Enable the installed extension.", "details": []}
        events = self.draw()
        self.assertTrue(self.event(events, "Skirt")[2])
        self.assertIn("Enable Wiggle", [event[1] for event in events])

    def test_partial_dependency_cleanup_failure_never_offers_start(self):
        self.state["hair_enabled"] = True
        self.state["hair"].update(available=True, dependency={"state": "CLEANUP_FAILED", "label": "Wiggle",
            "enable_available": False, "message": "Extension cleanup needs review.", "details": []})
        events = self.draw()
        self.assertNotIn("Start Hair Preview", [event[1] for event in events])
        self.assertNotIn("Enable Wiggle", [event[1] for event in events])
        self.assertIn("Extension cleanup needs review.", [event[1] for event in events])

    def test_original_display_keeps_skirt_switch_and_hides_return_and_normal_details(self):
        self.state["dresses"][0].update(available=True, requires_controls=True, can_change_mode=False,
            block_reason="Finish Original mode in Bone Display first.", dependency={"state": "READY",
            "label": "Wiggle 2 2.2.4", "available": True, "message": "", "details": []})
        events = self.draw()
        self.assertTrue(self.event(events, "Skirt")[2])
        labels = [event[1] for event in events]
        self.assertNotIn("Return to Controls", labels)
        self.assertNotIn("Finish Original mode in Bone Display first.", labels)
        self.assertNotIn("Wiggle 2 2.2.4 ready", labels)
        self.assertNotIn("Details", labels)

    def test_retained_character_mode_operator_is_not_drawn_but_keeps_its_undo_contract(self):
        self.state["dresses"][0].update(requires_controls=True, can_change_mode=False)
        self.state["control_mode"] = False
        events = self.draw()
        self.assertFalse(any(event[0] == ui.CHARACTERDESIGNER_OT_animation_physics_controls.bl_idname
                             for event in events))
        self.service.return_to_controls.assert_not_called()
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_controls)
        self.assertIn("UNDO", operator.bl_options)
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_control_mode.assert_called_once_with(self.context, True)
        self.service.return_to_controls.assert_not_called()

    def test_return_controls_refusal_keeps_original_gate_reason(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_controls)
        self.service.set_control_mode.side_effect = ValueError("The original display recovery proof changed.")
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        operator.report.assert_called_once_with({"ERROR"}, "The original display recovery proof changed.")
        self.service.set_skirt_mode.assert_not_called()

    def test_start_failure_detail_is_visible_beyond_dependency_readiness(self):
        self.state["hair"]["last_error"] = "Stop the active Dress simulation before starting Hair."
        events = self.draw()
        self.assertIn("Stop the active Dress simulation before starting Hair.", [event[1] for event in events])

    def test_multiple_hair_sources_retain_source_specific_failure_reason(self):
        self.state["hair"].update(sources=["Hair A", "Hair B"], source_errors={
            "Hair A": "The strand binding changed.", "Hair B": ""})
        events = self.draw()
        self.assertIn("Hair A: The strand binding changed.", [event[1] for event in events])

    def test_skirt_control_mode_is_right_aligned_own_mode_and_independent_of_master_permission(self):
        for enabled in (False, True):
            for manual in (False, True):
                with self.subTest(enabled=enabled, manual=manual):
                    self.state.update(enabled=enabled, control_mode=not manual)
                    self.state["dresses"][0]["mode"] = "MANUAL" if manual else "AUTOMATIC"
                    events = self.draw()
                    event = self.event(events, "Control Mode")
                    self.assertTrue(event[2])
                    self.assertEqual(event[3]["depress"], manual)
                    self.assertEqual(event[5].alignment, "RIGHT")
                    self.assertIs(event[5].parent, self.event(events, "Skirt")[5].parent)
                    self.assertEqual((event[4].target_name, event[4].target_pointer),
                                     ("Character A", "101"))
                    self.assertEqual((event[4].source_name, event[4].source_owner),
                                     ("Dress", "dress-owner-1"))
                    self.assertEqual(event[4].mode, "AUTOMATIC" if manual else "MANUAL")
        self.context.mode = "EDIT_ARMATURE"
        self.assertFalse(self.event(self.draw(), "Control Mode")[2])

    def test_skirt_control_mode_click_requests_only_its_exact_source_manual_mode(self):
        event = self.event(self.draw(), "Control Mode")
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_skirt_mode)
        for name in ("source_name", "source_owner", "mode", "target_name", "target_pointer"):
            setattr(operator, name, getattr(event[4], name))
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_skirt_mode.assert_called_once_with(
            self.context, "Dress", "MANUAL", expected_owner="dress-owner-1")
        self.service.set_control_mode.assert_not_called()
        self.service.return_to_controls.assert_not_called()
        self.service.set_component.assert_not_called()
        self.service.set_enabled.assert_not_called()
        self.service.start_hair_preview.assert_not_called()
        self.service.stop_hair_preview.assert_not_called()

    def test_releasing_skirt_control_mode_requests_automatic_with_existing_dependency_check(self):
        self.state["dresses"][0]["mode"] = "MANUAL"
        event = self.event(self.draw(), "Control Mode")
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_skirt_mode)
        for name in ("source_name", "source_owner", "mode", "target_name", "target_pointer"):
            setattr(operator, name, getattr(event[4], name))
        self.service.dependency_status.return_value = {"state": "UNCHECKED"}
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_skirt_mode.assert_called_once_with(
            self.context, "Dress", "AUTOMATIC", expected_owner="dress-owner-1")
        self.assertEqual(self.service.dependency_status.call_args_list,
                         [call("SKIRT"), call("SKIRT", refresh=True)])
        self.service.set_control_mode.assert_not_called()
        self.service.return_to_controls.assert_not_called()

    def test_no_skirt_has_no_control_mode_even_when_character_controls_are_on(self):
        self.state.update(dresses=[], control_mode=True, control_error="Old character mode failure.")
        events = self.draw()
        self.assertNotIn("Control Mode", [event[1] for event in events])
        self.assertNotIn(self.state["control_error"], [event[1] for event in events])
        self.assertFalse(self.event(events, "Skirt")[2])
        self.assertTrue(self.event(events, "Hair")[2])

    def test_noneditable_skirt_disables_only_its_mode_action(self):
        self.state["dresses"][0]["can_change_mode"] = False
        events = self.draw()
        self.assertFalse(self.event(events, "Control Mode")[2])
        self.assertTrue(self.event(events, "Hair")[2])

    def test_retained_character_mode_operator_toggles_fresh_state_when_invoked_directly(self):
        self.state["control_mode"] = False
        self.draw()
        self.state["control_mode"] = True
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_controls)
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_control_mode.assert_called_once_with(self.context, False)
        self.service.set_component.assert_not_called()
        self.service.set_enabled.assert_not_called()

    def test_skirt_mode_failure_stays_visible_without_old_character_mode_error(self):
        self.state.update(control_error="Old character Original error.",
                          message="Another simulation owns the shared backend.")
        self.state["dresses"][0]["last_error"] = "The edited skirt skeleton changed."
        events = self.draw()
        labels = [event[1] for event in events]
        mode_index = labels.index("Control Mode")
        self.assertEqual(labels[mode_index + 1], self.state["dresses"][0]["last_error"])
        self.assertNotIn(self.state["control_error"], labels)
        self.assertIn(self.state["message"], labels)
        self.assertNotIn("Details", labels)
        self.assertEqual(self.event(events, self.state["dresses"][0]["last_error"])[3]["icon"], "ERROR")

    def test_control_mode_character_switch_refuses_before_mode_action(self):
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_controls)
        self.state["rig"] = SimpleNamespace(name="Character B", as_pointer=lambda: 202)
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        self.service.set_control_mode.assert_not_called()
        self.service.set_component.assert_not_called()
        self.assertIn("current character changed", operator.report.call_args[0][1])

    def test_expected_history_recovery_uses_hair_checkbox_without_internal_status_or_extra_actions(self):
        self.state["hair_enabled"] = True
        self.state["hair"].update(active=False, pending_recovery=True, stopped_confirmed=False)
        for message in ("History/load ended the transient Hair lease; use explicit Physics controls to verify it again.",
                        "Save, undo, redo, load or render ended Hair preview.",
                        "Saved Hair preview flags need explicit recovery."):
            with self.subTest(message=message):
                self.state["hair"]["message"] = message
                events = self.draw()
                checkbox = self.event(events, "Hair")
                self.assertTrue(checkbox[2])
                self.assertEqual(checkbox[3]["icon"], "CHECKBOX_DEHLT")
                self.assertTrue(checkbox[4].enabled)
                labels = [event[1] for event in events]
                for hidden in (message, "Hair physics needs review.", "Start Hair Preview", "Stop Hair Preview",
                               "Recover Hair", "Details"):
                    self.assertNotIn(hidden, labels)
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "HAIR", True
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"FINISHED"})
        self.service.set_component.assert_called_once_with(self.context, "HAIR", True)
        self.service.stop_hair_preview.assert_not_called()

    def test_expected_recovery_does_not_hide_a_failed_owned_repair(self):
        self.state["hair"].update(active=False, pending_recovery=True, stopped_confirmed=False,
            message="History/load ended the transient Hair lease; use explicit Physics controls to verify it again.",
            last_error="The saved Hair binding no longer matches this rig.")
        events = self.draw()
        self.assertIn(self.state["hair"]["last_error"], [event[1] for event in events])
        self.assertTrue(self.event(events, "Recover Hair")[2])
        self.assertTrue(self.event(events, "Hair")[2])

    def test_multiple_pending_hair_sources_allow_guarded_action_instead_of_silent_disabled_switch(self):
        self.state["hair"].update(sources=["Hair A", "Hair B"], active=False, pending_recovery=True,
                                  message="Saved Hair preview flags need explicit recovery.")
        events = self.draw()
        self.assertTrue(self.event(events, "Hair")[2])
        self.assertNotIn("Recover Hair", [event[1] for event in events])
        self.service.set_component.side_effect = ValueError("Choose one Hair source in Rig before recovering physics.")
        operator = self.operator(ui.CHARACTERDESIGNER_OT_animation_physics_set)
        operator.component, operator.enabled = "HAIR", True
        with patch.object(ui, "_service", return_value=self.service):
            self.assertEqual(operator.execute(self.context), {"CANCELLED"})
        operator.report.assert_called_once_with({"ERROR"}, "Choose one Hair source in Rig before recovering physics.")
        self.service.stop_hair_preview.assert_not_called()

    def test_long_genuine_failure_retains_all_text_without_a_details_fold(self):
        self.context.region.width = 180
        text = "发生错误：" + "/非常长的路径/" * 14 + "physics_backend.py"
        details = "END_OF_ACTIONABLE_FAILURE"
        layout = Layout()
        with patch.object(ui, "section", side_effect=AssertionError("No secondary diagnostic fold")):
            ui._message(layout, self.context, text, "long_error", icon="ERROR", details=(details,))
        labels = [event[1] for event in layout.events]
        self.assertEqual("".join(labels), text + details)
        self.assertNotIn("Details", labels)
        self.assertTrue(all(event[0] == "label" for event in layout.events))

    def test_wrapping_preserves_long_path_and_full_width_reason(self):
        text = "已啟用但仍不可用：" + "/very/long/" * 12 + "physics_backend.py"
        lines = ui._wrapped(text, 22)
        self.assertTrue(all(ui._cells(line) <= 22 for line in lines))
        self.assertEqual("".join(lines), text)

    def test_narrow_region_wraps_more_than_wide_region(self):
        self.context.region.width = 180
        narrow = ui._line_width(self.context)
        self.context.region.width = 480
        self.assertGreater(ui._line_width(self.context), narrow)


if __name__ == "__main__":
    unittest.main()
