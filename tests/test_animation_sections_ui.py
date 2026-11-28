"""Animation section folding and long-message accessibility without Blender."""

import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_animation_physics_ui import Layout, ui


ROOT = Path(__file__).resolve().parents[1]


def panel_draw(scope):
    """Compile the actual panel and its extracted draw helpers, without registering any RNA."""
    scope.setdefault("animation_preview_ui", SimpleNamespace(
        draw=Mock(), session=SimpleNamespace(status=Mock(return_value=None))))
    scope.setdefault("equipment_ui", SimpleNamespace(_target=lambda context: None))
    scope.setdefault("animation_composition_local_ui", SimpleNamespace(
        composition=SimpleNamespace(session=lambda rig: None)))
    scope.setdefault("animation_export_queue", SimpleNamespace(running=lambda: False))
    tree = ast.parse((ROOT / "addons/character_designer/animation.py").read_text(encoding="utf8"))
    panel = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "CHARACTERDESIGNER_PT_animation")
    draw = next(node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "draw")
    helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in
               {"_draw_single_action", "_draw_single_files", "_draw_local_fbx", "_draw_local_motion", "_draw_unity_preview", "_draw_pose_resets"}]
    exec(compile(ast.Module(body=[*helpers, draw], type_ignores=[]), "animation.py", "exec"), scope)


class PanelLayout(Layout):
    def __init__(self, events=None, parent=None, *, opened=None):
        super().__init__(events, parent)
        self.opened = {} if opened is None else opened

    def row(self, **_kwargs):
        return PanelLayout(self.events, self, opened=self.opened)

    column = box = row

    def panel(self, identifier, *, default_closed=False):
        self.events.append(("panel", identifier, self.enabled, {"default_closed": default_closed}, None))
        return self.row(), self.column() if self.opened.get(identifier, not default_closed) else None


class FullWidthLayout(PanelLayout):
    def __init__(self, events=None, parent=None, *, opened=None, full_width=True):
        super().__init__(events, parent, opened=opened)
        self.full_width = full_width

    def row(self, **_kwargs):
        return FullWidthLayout(self.events, self, opened=self.opened, full_width=False)

    column = box = row

    def panel(self, identifier, *, default_closed=False):
        if not self.full_width:
            raise AssertionError("Native panels require full width, not a row/box/column.")
        self.events.append(("panel", identifier, self.enabled, {"default_closed": default_closed}, None))
        body = FullWidthLayout(self.events, self, opened=self.opened) if self.opened.get(identifier, not default_closed) else None
        return self.row(), body


class NativeActionLayout(FullWidthLayout):
    def row(self, **_kwargs):
        return NativeActionLayout(self.events, self, opened=self.opened, full_width=False)

    column = box = row

    def panel(self, identifier, *, default_closed=False):
        if not self.full_width:
            raise AssertionError("Native panels require full width, not a row/box/column.")
        self.events.append(("panel", identifier, self.enabled, {"default_closed": default_closed}, None))
        body = NativeActionLayout(self.events, self, opened=self.opened) if self.opened.get(identifier, not default_closed) else None
        return self.row(), body

    def prop(self, data, name, **kwargs):
        getattr(data, name)
        self.events.append(("prop", name, self.enabled, kwargs, data))

    def template_action(self, animated_id, **kwargs):
        self.events.append(("template_action", None, self.enabled, kwargs, animated_id))

    def context_pointer_set(self, name, value):
        self.events.append(("context_pointer", name, self.enabled, {}, value))

    def context_string_set(self, name, value):
        self.events.append(("context_string", name, self.enabled, {}, value))

    def template_search(self, *args, **kwargs):
        self.events.append(("template_search", None, self.enabled, kwargs, args))


class AnimationSectionsTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(mode="POSE", region=SimpleNamespace(width=180))

    def test_pose_resets_follow_visible_control_selection(self):
        package = ModuleType("_pose_reset_fixture")
        owned = ModuleType(package.__name__ + ".limb_ik_wrist_rotation_ui")
        owned._owned_wrist = lambda bone: bone.wrist
        package.natural_pose_clear = SimpleNamespace(body_selected=lambda context: context.whole)
        scope = {"__package__": package.__name__}
        panel_draw(scope)
        rig = object()
        wrist = SimpleNamespace(id_data=rig, wrist=True)
        pole = SimpleNamespace(id_data=rig, wrist=False)
        foreign_wrist = SimpleNamespace(id_data=object(), wrist=True)
        cases = [
            ("OBJECT", [wrist], False, []),
            ("POSE", [], False, []),
            ("POSE", [wrist], False, ["Match Wrist to Edit Pose"]),
            ("POSE", [wrist, wrist], False, ["Match Wrists to Edit Pose"]),
            ("POSE", [wrist, pole], False, []),
            ("POSE", [wrist, foreign_wrist], False, []),
            ("POSE", [pole] * 9, False, []),
            ("POSE", [pole] * 9, True, []),
        ]
        with patch.dict(sys.modules, {package.__name__: package, owned.__name__: owned}):
            for mode, selection, whole, expected in cases:
                with self.subTest(mode=mode, selection=selection, whole=whole):
                    context = SimpleNamespace(mode=mode, object=rig, selected_pose_bones=selection, whole=whole)
                    layout = PanelLayout()
                    scope["_draw_pose_resets"](layout, context)
                    self.assertEqual([event[1] for event in layout.events], expected)

    def test_native_folded_physics_does_not_read_backend_state(self):
        layout = PanelLayout(opened={"character_designer_animation_physics": False})
        service = SimpleNamespace(status=Mock(side_effect=AssertionError("Collapsed section read backend")))
        with patch.object(ui, "_service", return_value=service):
            ui.draw(layout, self.context)
        service.status.assert_not_called()
        self.assertIn("Physics", [event[1] for event in layout.events])

    def test_skirt_and_hair_folds_are_independent(self):
        rig = SimpleNamespace(name="Rig", as_pointer=lambda: 1)
        state = {"rig": rig, "valid": True, "enabled": True, "skirt_enabled": True,
                 "hair_enabled": True, "dresses": [{"source": "Dress", "owner": "owner",
                     "mode": "MANUAL", "available": True, "backend": "WIGGLE", "message": ""}],
                 "hair": {"sources": ["Hair"], "active": False, "available": True, "message": ""}}
        layout = PanelLayout(opened={"character_designer_animation_physics_skirt": False,
                                      "character_designer_animation_physics_hair": True})
        with patch.object(ui, "_service", return_value=SimpleNamespace(status=lambda context: state)):
            ui.draw(layout, self.context)
        self.assertNotIn("Automatic", [event[1] for event in layout.events])
        self.assertIn("Skirt", [event[1] for event in layout.events])
        self.assertIn("Start Hair Preview", [event[1] for event in layout.events])

    def test_long_message_details_default_closed_and_expand_without_losing_tail(self):
        message = "已啟用但不相容：" + "/long_backend_path/" * 8 + "END_OF_REASON"
        layout = PanelLayout()
        ui._message(layout, self.context, message, "physics_reason")
        self.assertNotIn("END_OF_REASON", "".join(event[1] for event in layout.events if event[0] == "label"))
        self.assertEqual(next(event for event in layout.events if event[0] == "panel")[3], {"default_closed": True})
        expanded = PanelLayout(opened={"physics_reason": True})
        ui._message(expanded, self.context, message, "physics_reason")
        labels = [event[1] for event in expanded.events if event[0] == "label"]
        self.assertIn(message, "".join(labels))
        self.assertTrue(all(ui._cells(label) <= ui._line_width(self.context)
                            for label in labels if label != "Details"))

    def test_skirt_dependency_details_never_create_panels_in_columns(self):
        rig = SimpleNamespace(name="Rig", as_pointer=lambda: 1)
        dependency = {"state": "READY", "label": "Wiggle 2", "message": "",
                      "details": ["Keep this long exact explanation available in the full-width native panel."]}
        state = {"rig": rig, "valid": True, "enabled": True, "skirt_enabled": True, "hair_enabled": True,
                 "dresses": [{"source": "Dress", "owner": "owner", "mode": "MANUAL",
                              "available": True, "backend": "WIGGLE", "dependency": dependency,
                              "last_error": "Actual contextual refusal. " * 8}],
                 "hair": {"sources": [], "active": False}}
        with patch.object(ui, "_service", return_value=SimpleNamespace(status=lambda context: state)):
            ui.draw(FullWidthLayout(), self.context)

    def draw_animation(self, opened, *, retained_inputs=True):
        package = "_animation_section_draw_fixture"
        physics = ModuleType(package + ".animation_physics_ui")
        physics.draw, physics.section, physics.wrapped_label = Mock(), ui.section, ui.wrapped_label
        original = ModuleType(package + ".animation_original_inputs_ui")
        original.draw_original_inputs = Mock()
        original._rig = Mock(return_value=object())
        original.has_original_inputs = Mock(return_value=retained_inputs)
        pose = ModuleType(package + ".control_pose_capture")
        pose.draw = Mock()
        worklist = ModuleType(package + ".animation_worklist_ui")
        for name in ('draw_worklist', 'draw_worklist_export', 'draw_worklist_unity',
                     'draw_worklist_motion_tools'):
            setattr(worklist, name, Mock())
        worklist.scan_pending = Mock(return_value=False)
        collection = ModuleType(package + ".animation_worklist_collection")
        collection.running = Mock(return_value=False)
        export = ModuleType(package + ".animation_export")
        export.active_job = Mock(return_value={"ordinary_export": True})
        scope = {"__package__": package, "_settings": lambda context: SimpleNamespace(),
                 "_main_rig": Mock(side_effect=AssertionError("Connected worklist entered legacy UI"))}
        panel_draw(scope)
        self.context.scene = SimpleNamespace(character_designer_animation_worklist=SimpleNamespace(workspace_path="linked"))
        layout = PanelLayout(opened=opened)
        modules = (ModuleType(package), physics, original, pose, worklist, collection, export)
        with patch.dict(sys.modules, {module.__name__: module for module in modules}):
            scope["draw"](SimpleNamespace(layout=layout), self.context)
        return layout, physics, original, pose, worklist, export

    def test_original_inputs_default_fold_does_not_draw_duplicate_body(self):
        layout, physics, original, pose, worklist, export = self.draw_animation({})
        original.draw_original_inputs.assert_not_called()
        worklist.draw_worklist.assert_called_once()
        self.assertEqual(worklist.draw_worklist.call_args.kwargs, {"include_original_inputs": False})
        pose.draw.assert_called_once()
        worklist.draw_worklist_export.assert_not_called()
        worklist.draw_worklist_unity.assert_not_called()
        worklist.draw_worklist_motion_tools.assert_not_called()
        self.assertIn("character_designer.animation_export_cancel", [event[0] for event in layout.events])
        original_panel = next(event for event in layout.events if event[1] == "character_designer_animation_original_inputs")
        self.assertTrue(original_panel[3]["default_closed"])

    def test_original_inputs_expands_independently_of_animations(self):
        layout, physics, original, pose, worklist, export = self.draw_animation({
            "character_designer_animation_original_inputs": True,
            "character_designer_animation_animations": False})
        original.draw_original_inputs.assert_called_once()
        worklist.draw_worklist.assert_not_called()
        pose.draw.assert_not_called()
        export.active_job.assert_not_called()

    def test_animation_panel_has_no_recovery_fold_or_unused_original_inputs(self):
        layout, _physics, original, _pose, _worklist, _export = self.draw_animation({}, retained_inputs=False)
        self.assertNotIn("Editing & Recovery", repr(layout.events))
        self.assertNotIn("character_designer_animation_original_inputs", repr(layout.events))
        original.draw_original_inputs.assert_not_called()

    def draw_unmanaged_animation(self, *, opened=None, has_animation_data=True, has_action=True,
                                 tweak=False, preview=False, generation_job=None, export_job=False, advanced=False):
        package = "_unmanaged_animation_section_fixture"
        physics = ModuleType(package + ".animation_physics_ui")
        physics.draw, physics.section, physics.wrapped_label = Mock(), ui.section, ui.wrapped_label
        original = ModuleType(package + ".animation_original_inputs_ui")
        original.draw_original_inputs = Mock()
        original._rig = Mock(return_value=None)
        original.has_original_inputs = Mock(return_value=False)
        pose = ModuleType(package + ".control_pose_capture")
        pose.draw = Mock()
        worklist = ModuleType(package + ".animation_worklist_ui")
        for name in ('draw_worklist', 'draw_worklist_export', 'draw_worklist_unity',
                     'draw_worklist_motion_tools'):
            setattr(worklist, name, Mock())
        worklist.scan_pending = Mock(return_value=False)
        collection = ModuleType(package + ".animation_worklist_collection")
        collection.running = Mock(return_value=False)
        export = ModuleType(package + ".animation_export")
        export.active_job = Mock(return_value={"ordinary_export": True} if export_job else None)
        unity = ModuleType(package + ".unity_animation")
        unity.active_preview = Mock(return_value=SimpleNamespace(name="Unity Preview") if preview else None)
        unity.preview_time_seconds = Mock(return_value=0.25)
        link = ModuleType(package + ".animation_link")
        link.ACTION_KEY = "character_designer_animation_link_action"
        link.get_link = Mock(return_value=None)
        forearm = ModuleType(package + ".forearm_twist")
        forearm._ERRORS = {}
        action = SimpleNamespace(name="Local motion") if has_action else None
        animation_data = SimpleNamespace(action=action, use_tweak_mode=tweak,
                                         action_slot=SimpleNamespace(handle=1), action_suitable_slots=()) if has_animation_data else None
        rig = SimpleNamespace(name="CoshaRig", animation_data=animation_data,
                              animation_data_create=Mock(side_effect=AssertionError("Draw created animation data")))
        settings = SimpleNamespace(status="", has_error=False, show_local_motion=False, show_setup=False,
                                   show_unity_files=False, start_frame=1, result_path="", log_path="", source=None,
                                   prompt="walk", duration=1., seed=1)
        folder = Mock(side_effect=AssertionError("Draw resolved an exchange folder"))
        scope = {"__package__": package, "_settings": lambda context: settings,
                 "_main_rig": lambda context: rig, "_main_rig_error": lambda context, target: "",
                 "_target": lambda context: rig, "_job": generation_job, "_can_restore": lambda context: False,
                 "_link_idle": Mock(return_value=not export_job and not generation_job), "unity_exchange_folder": folder}
        panel_draw(scope)
        self.context.screen = SimpleNamespace(is_animation_playing=False)
        self.context.scene = SimpleNamespace(frame_current=6, frame_subframe=.25,
            character_designer_animation_worklist=SimpleNamespace(workspace_path="", show_advanced=advanced))
        layout = NativeActionLayout(opened=opened)
        modules = (ModuleType(package), physics, original, pose, worklist, collection, export, unity, link, forearm)
        with patch.dict(sys.modules, {module.__name__: module for module in modules}), \
                patch.object(Path, "open", side_effect=AssertionError("Draw read a file")), \
                patch.object(Path, "stat", side_effect=AssertionError("Draw inspected file metadata")):
            scope["draw"](SimpleNamespace(layout=layout), self.context)
        return SimpleNamespace(layout=layout, rig=rig, worklist=worklist, link=link, unity=unity, folder=folder)

    def test_unmanaged_native_action_and_slot_remain_visible_with_unity_collapsed(self):
        result = self.draw_unmanaged_animation()
        action = next(event for event in result.layout.events if event[0] == "template_action")
        self.assertTrue(action[2])
        self.assertIs(action[4], result.rig)
        self.assertEqual(action[3], dict(new="action.new", unlink="action.unlink", text="Action"))
        slot = next(event for event in result.layout.events if event[0] == "template_search")
        self.assertEqual(slot[4], (result.rig.animation_data, "action_slot",
                                   result.rig.animation_data, "action_suitable_slots"))
        self.assertEqual(slot[3], dict(new="anim.slot_new_for_id", unlink="anim.slot_unassign_from_id"))
        pointer = next(event for event in result.layout.events if event[0] == "context_pointer")
        self.assertEqual(pointer[1], "animated_id")
        self.assertIs(pointer[4], result.rig)
        # Reading the saved link is how the primary panel determines the correct action.
        # Filesystem access remains forbidden by the draw harness.
        result.link.get_link.assert_called_once_with(result.rig)
        result.worklist.draw_worklist_unity.assert_not_called()
        result.unity.preview_time_seconds.assert_not_called()
        result.folder.assert_not_called()
        result.rig.animation_data_create.assert_not_called()
        result.worklist.draw_worklist.assert_called_once()
        self.assertEqual(result.worklist.draw_worklist.call_args.kwargs, {"include_original_inputs": False})
        self.assertFalse(any(event[0] == "character_designer.animation_export" for event in result.layout.events))
        self.assertFalse(any(event[0] in {"character_designer.animation_unity_import",
                                          "character_designer.animation_link_import",
                                          "character_designer.animation_link_sync"} for event in result.layout.events))

    def test_unmanaged_empty_action_selector_does_not_create_animation_data_or_slots(self):
        for has_animation_data in (False, True):
            with self.subTest(has_animation_data=has_animation_data):
                result = self.draw_unmanaged_animation(has_animation_data=has_animation_data, has_action=False)
                actions = [event for event in result.layout.events if event[0] == "template_action"]
                self.assertEqual(len(actions), 1)
                self.assertTrue(actions[0][2])
                self.assertFalse(any(event[0] == "character_designer.animation_export" for event in result.layout.events))
                self.assertFalse(any(event[0] in {"template_search", "context_pointer"} for event in result.layout.events))
                result.rig.animation_data_create.assert_not_called()
                if has_animation_data:
                    self.assertIsNone(result.rig.animation_data.action)
                else:
                    self.assertIsNone(result.rig.animation_data)

    def test_unmanaged_picker_guards_and_collapsed_preview_export_cancels(self):
        for name, options, enabled, cancel in (
                ("preview", dict(preview=True), False, "character_designer.animation_unity_cancel"),
                ("generation", dict(generation_job=object()), False, "character_designer.animation_cancel"),
                ("tweak", dict(tweak=True), False, None),
                ("export", dict(export_job=True), True, "character_designer.animation_export_cancel")):
            with self.subTest(state=name):
                result = self.draw_unmanaged_animation(**options)
                pickers = [event for event in result.layout.events if event[0] in {"template_action", "template_search"}]
                self.assertEqual([event[2] for event in pickers], [enabled, enabled])
                if cancel is not None:
                    [event] = [event for event in result.layout.events if event[0] == cancel]
                    self.assertTrue(event[2])
                manual = [event for event in result.layout.events if event[0] == "character_designer.animation_export"]
                self.assertEqual(manual, [])
                result.link.get_link.assert_called_once_with(result.rig)
                self.assertEqual(result.unity.preview_time_seconds.call_count, int(name == "preview"))
                result.folder.assert_not_called()

    def test_import_animates_from_unity_without_showing_export_or_equipment_tools(self):
        result = self.draw_unmanaged_animation(opened={"character_designer_animation_export": True,
                                                      "character_designer_animation_unity": True})
        result.link.get_link.assert_called_once_with(result.rig)
        result.folder.assert_not_called()
        result.unity.preview_time_seconds.assert_not_called()
        operations = [event[0] for event in result.layout.events]
        labels = [event[1] for event in result.layout.events]
        self.assertIn("Import Animations", labels)
        self.assertNotIn("Export Animations", labels)
        self.assertNotIn("Upper-Body Composition", labels)
        self.assertIn("character_designer.animation_link_import", operations)
        self.assertIn("character_designer.animation_unity_import", operations)
        self.assertNotIn("character_designer.animation_export", operations)


if __name__ == "__main__":
    unittest.main()
