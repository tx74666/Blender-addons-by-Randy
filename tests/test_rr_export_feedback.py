"""Exercise exporter failure feedback and Group discovery without starting Blender.

Run: python -m unittest discover -s tests -p test_rr_export_feedback.py
The selected functions execute their real bodies against UI/export mocks, following
the AST-loading convention in test_standard_export_transaction.py.
"""

import ast
from pathlib import Path
import textwrap
from types import SimpleNamespace
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "addons/random_realm_builder_exporter/__init__.py"


class LayoutRecorder:
    def __init__(self, events=None, enabled=True):
        self.events = events if events is not None else []
        self.enabled = enabled

    def row(self, **_kwargs):
        return LayoutRecorder(self.events, self.enabled)

    column = row
    box = row

    def label(self, **kwargs):
        self.events.append(("label", kwargs))

    def prop(self, _data, name, **kwargs):
        self.events.append(("prop", dict(kwargs, name=name)))

    def operator(self, name, **kwargs):
        self.events.append(("operator", dict(kwargs, name=name, enabled=self.enabled)))
        return SimpleNamespace()


def load_functions(namespace):
    names = {"show_builder_popup", "export_objects", "draw_exporter_page", "draw_export_group_box"}
    tree = ast.parse(SOURCE.read_text(encoding="utf-8-sig"))
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == names
    queue_class = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                       and node.name == "RR_OT_export_queue")
    queue_execute = next(node for node in queue_class.body if isinstance(node, ast.FunctionDef)
                         and node.name == "execute")
    queue_execute.name = "execute_export_queue"
    functions.append(queue_execute)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), namespace)


class QueueCollection(list):
    def remove(self, index):
        del self[index]


class ExportFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.popups = []
        self.printed = []
        self.failures = {}
        self.roots = [SimpleNamespace(name=name, select_set=lambda _selected: None)
                      for name in ("Hub_Elevator_Casing", "Hub_Elevator_Door1.L")]

        def popup(draw, **kwargs):
            layout = LayoutRecorder()
            draw(SimpleNamespace(layout=layout), self.context)
            self.popups.append(dict(kwargs, lines=[entry[1]["text"] for entry in layout.events]))

        def export(root, *_args, **_kwargs):
            if root.name in self.failures:
                raise RuntimeError(self.failures[root.name])
            return root.name, "Prop", "exported"

        self.context = SimpleNamespace(
            scene=SimpleNamespace(), selected_objects=list(self.roots),
            view_layer=SimpleNamespace(objects=SimpleNamespace(active=self.roots[0])),
            window_manager=SimpleNamespace(popup_menu=popup))
        self.settings = SimpleNamespace(output_root="unused")
        self.namespace = {
            "bpy": SimpleNamespace(app=SimpleNamespace(background=False),
                                   data=SimpleNamespace(objects={root.name: root for root in self.roots})),
            "textwrap": textwrap, "time": SimpleNamespace(perf_counter=mock.Mock(side_effect=[1.0, 1.5])),
            "print": lambda *args: self.printed.append(" ".join(map(str, args))),
            "sync_object_manager_names": lambda: None,
            "migrate_reference_layout_scene": lambda _scene: None,
            "validate_standard_output_route": lambda _settings: None,
            "validate_reference_layout_settings": lambda _settings: None,
            "expand_related_export_roots": lambda roots: roots,
            "core_roots_for_export_batch": lambda roots, *_args: roots,
            "prepare_variant_export_transactions": lambda *_args: ([], {}, []),
            "shared_builder_icon_root": lambda _root: None,
            "export_builder_asset": export,
            "finalize_variant_export_transactions": lambda *_args: ([], []),
            "completed_ordinary_export_publication": lambda *_args: ([], []),
        }
        load_functions(self.namespace)

    def run_export(self):
        return self.namespace["export_objects"](
            self.roots, self.settings, self.context, "queue", True, False)

    def run_queue_export(self):
        self.settings.export_queue = QueueCollection(
            SimpleNamespace(object_name=root.name) for root in self.roots)
        self.settings.queue_active_index = 0
        for name, value in (("icon_zoom", 1.75), ("icon_offset_x", 0.25), ("icon_offset_y", -0.5),
                            ("icon_view_yaw", 37.0), ("icon_view_pitch", 20.0)):
            setattr(self.settings, name, value)
        self.context.scene.rr_builder_export_settings = self.settings
        self.namespace.update({
            "ensure_object_mode": lambda _context: True,
            "export_mode_uses_reference_layout": lambda _settings: False,
            "export_mode_is_standard": lambda _settings: True,
            "queue_item_object": lambda item: self.namespace["bpy"].data.objects.get(item.object_name),
            "ensure_export_identity": lambda root: (root.name, []),
            "queue_item_for_root": lambda settings, root: next(
                (item for item in settings.export_queue if item.object_name == root.name), None),
            "queue_index_for_root": lambda settings, root: next(
                (index for index, item in enumerate(settings.export_queue) if item.object_name == root.name), -1),
            "prepare_framing_for_root": lambda *_args: None,
            "finalize_variant_export_transactions": lambda *_args: (set(), []),
            "completed_ordinary_export_publication": lambda _roots, successful, _output: (
                ["unused/" + name for name in successful], set(successful)),
        })
        operator = SimpleNamespace(include_model=True, include_icon=False, report=mock.Mock())
        return self.namespace["execute_export_queue"](operator, self.context)

    def test_all_failed_still_shows_count_and_actual_reasons(self):
        self.failures = {root.name: "Cannot write model.fbx" for root in self.roots}
        self.assertEqual({"CANCELLED"}, self.run_export())
        self.assertEqual(1, len(self.popups))
        self.assertEqual("ERROR", self.popups[0]["icon"])
        self.assertIn("Exported 0 from queue; failed 2", self.popups[0]["lines"][0])
        for root in self.roots:
            self.assertIn(f"{root.name}: Cannot write model.fbx", " ".join(self.popups[0]["lines"]))

    def test_partial_failure_is_not_reported_as_success(self):
        self.failures[self.roots[1].name] = "Missing UV map"
        self.assertEqual({"CANCELLED"}, self.run_export())
        self.assertIn("Exported 1 from queue; failed 1", self.popups[0]["lines"][0])
        self.assertIn("Missing UV map", " ".join(self.popups[0]["lines"]))

    def test_failure_popup_is_bounded_and_console_keeps_full_details(self):
        root = SimpleNamespace(name="Third_Part", select_set=lambda _selected: None)
        self.roots.append(root)
        self.failures = {item.name: "Long error detail " * 100 for item in self.roots}
        self.assertEqual({"CANCELLED"}, self.run_export())
        lines = self.popups[0]["lines"]
        self.assertLessEqual(len(lines), 5)
        self.assertTrue(all(len(line) <= 96 for line in lines[1:]))
        self.assertNotIn(root.name, " ".join(lines))
        for item in self.roots:
            self.assertTrue(any(self.failures[item.name] in message and item.name in message
                                for message in self.printed))

    def test_background_failure_keeps_console_and_does_not_open_ui(self):
        self.namespace["bpy"].app.background = True
        self.failures[self.roots[0].name] = "Cannot write model.fbx"
        self.assertEqual({"CANCELLED"}, self.run_export())
        self.assertEqual([], self.popups)
        self.assertTrue(any("Cannot write model.fbx" in message for message in self.printed))
        self.assertTrue(any("failed 1" in message for message in self.printed))

    def test_success_feedback_is_unchanged(self):
        self.assertEqual({"FINISHED"}, self.run_export())
        self.assertEqual("INFO", self.popups[0]["icon"])
        self.assertIn("Exported 2 queue assets (model only)", self.popups[0]["lines"][0])

    def test_queue_all_failed_shows_reasons_and_keeps_both_items(self):
        self.failures = {root.name: "Cannot write model.fbx" for root in self.roots}
        self.assertEqual({"CANCELLED"}, self.run_queue_export())
        self.assertEqual([root.name for root in self.roots],
                         [item.object_name for item in self.settings.export_queue])
        self.assertEqual(1, len(self.popups))
        self.assertEqual("ERROR", self.popups[0]["icon"])
        self.assertIn("Exported 0, cleared 0; 2 failed items remain", self.popups[0]["lines"][0])
        for root in self.roots:
            self.assertIn(f"{root.name}: Cannot write model.fbx", " ".join(self.popups[0]["lines"]))

    def test_queue_partial_failure_clears_only_published_item_and_keeps_reason(self):
        self.failures[self.roots[1].name] = "Missing UV map"
        self.assertEqual({"FINISHED"}, self.run_queue_export())
        self.assertEqual([self.roots[1].name], [item.object_name for item in self.settings.export_queue])
        self.assertEqual(0, self.settings.queue_active_index)
        self.assertAlmostEqual(1.75, self.settings.icon_zoom)
        self.assertAlmostEqual(37.0, self.settings.icon_view_yaw)
        self.assertEqual(1, len(self.popups))
        self.assertEqual("ERROR", self.popups[0]["icon"])
        self.assertIn("Exported 1, cleared 1; 1 failed items remain", self.popups[0]["lines"][0])
        self.assertIn("Missing UV map", " ".join(self.popups[0]["lines"]))


class GroupDiscoveryTests(unittest.TestCase):
    def make_panel(self, selected_count, *, visible=True, expanded=True):
        context = SimpleNamespace(selected_objects=[object() for _ in range(selected_count)])
        namespace = {
            "selected_object_manager_assembly_root": lambda _context: None,
            "object_manager_group_from_settings": lambda _settings: None,
            "selected_objects_for_object_manager_assembly": lambda context: context.selected_objects,
        }
        load_functions(namespace)
        settings = SimpleNamespace(show_export_queue_section=False, show_export_group_section=visible,
                                   show_export_icon_section=False, object_manager_assembly_type="ASSEMBLY")
        layout = LayoutRecorder()
        panel = SimpleNamespace(draw_export_section_filter=lambda *_args: None,
                                draw_fold_panel=lambda layout, *_args: layout if expanded else None)
        panel.draw_export_group_box = lambda *args: namespace["draw_export_group_box"](panel, *args)
        namespace["draw_exporter_page"](panel, layout, context, settings)
        return layout.events

    def test_group_remains_visible_and_make_requires_two_parts(self):
        for count in (0, 1, 2):
            with self.subTest(selected=count):
                events = self.make_panel(count)
                make = [entry[1] for entry in events if entry[0] == "operator"
                        and entry[1]["name"] == "rr_builder.create_object_assembly"]
                self.assertEqual(1, len(make))
                self.assertEqual(count >= 2, make[0]["enabled"])
                labels = [entry[1]["text"] for entry in events if entry[0] == "label"]
                self.assertIn("2 selected" if count >= 2 else "Select 2+ parts to make a group", labels)

    def test_explicit_section_visibility_and_fold_still_respected(self):
        self.assertEqual([], self.make_panel(1, visible=False))
        self.assertEqual([], self.make_panel(1, expanded=False))


if __name__ == "__main__":
    unittest.main()
