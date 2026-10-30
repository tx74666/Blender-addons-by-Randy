"""Animation section folding and long-message accessibility without Blender."""

import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_animation_physics_ui import Layout, ui


ROOT = Path(__file__).resolve().parents[1]


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


class AnimationSectionsTests(unittest.TestCase):
    def setUp(self):
        self.context = SimpleNamespace(mode="POSE", region=SimpleNamespace(width=180))

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

    def draw_animation(self, opened):
        tree = ast.parse((ROOT / "addons/character_designer/animation.py").read_text(encoding="utf8"))
        panel = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                     and node.name == "CHARACTERDESIGNER_PT_animation")
        draw = next(node for node in panel.body if isinstance(node, ast.FunctionDef) and node.name == "draw")
        package = "_animation_section_draw_fixture"
        physics = ModuleType(package + ".animation_physics_ui")
        physics.draw, physics.section = Mock(), ui.section
        original = ModuleType(package + ".animation_original_inputs_ui")
        original.draw_original_inputs = Mock()
        pose = ModuleType(package + ".control_pose_capture")
        pose.draw = Mock()
        worklist = ModuleType(package + ".animation_worklist_ui")
        worklist.draw_worklist = Mock()
        export = ModuleType(package + ".animation_export")
        export.active_job = Mock(return_value=True)
        scope = {"__package__": package, "_settings": lambda context: SimpleNamespace(),
                 "_main_rig": Mock(side_effect=AssertionError("Connected worklist entered legacy UI"))}
        exec(compile(ast.Module(body=[draw], type_ignores=[]), "animation.py", "exec"), scope)
        self.context.scene = SimpleNamespace(character_designer_animation_worklist=SimpleNamespace(workspace_path="linked"))
        layout = PanelLayout(opened=opened)
        with patch.dict(sys.modules, {module.__name__: module for module in
                                     (physics, original, pose, worklist, export)}):
            scope["draw"](SimpleNamespace(layout=layout), self.context)
        return layout, physics, original, pose, worklist, export

    def test_original_inputs_default_fold_does_not_draw_duplicate_body(self):
        layout, physics, original, pose, worklist, export = self.draw_animation({})
        original.draw_original_inputs.assert_not_called()
        worklist.draw_worklist.assert_called_once()
        self.assertEqual(worklist.draw_worklist.call_args.kwargs, {"include_original_inputs": False})
        pose.draw.assert_called_once()
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


if __name__ == "__main__":
    unittest.main()
