"""Core authoring, migration and real export regressions in a factory scene.

Run serially with Blender --background --factory-startup --disable-autoexec
--threads 2 --python-exit-code 1 --python tests/test_rr_core_object_blender.py.
All saved scenes and exported FBX files belong to a temporary directory.
"""

import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy
from mathutils import Matrix


ADDONS_DIR = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDONS_DIR))
rr = importlib.import_module("random_realm_builder_exporter")
assert Path(rr.__file__).resolve().is_relative_to(ADDONS_DIR.resolve()), rr.__file__


def make_mesh(name):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(
        [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)]
    )
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for loop, coordinate in zip(mesh.loops, ((0, 0), (1, 0), (1, 1), (0, 1))):
        uv.data[loop.index].uv = coordinate
    uv.active_render = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def select_only(obj):
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def directory_hashes(directory):
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*") if path.is_file()
    }


class LayoutRecorder:
    """Record rendered controls and order without requiring a visible window."""

    def __init__(self, events=None):
        self.events = events if events is not None else []

    def row(self, **_kwargs):
        return LayoutRecorder(self.events)

    column = row
    box = row
    split = row

    def label(self, **kwargs):
        self.events.append(("label", kwargs))

    def prop(self, data, name, **kwargs):
        self.events.append(("prop", dict(kwargs, property=name, data=data)))

    def operator(self, name, **kwargs):
        operator = SimpleNamespace()
        self.events.append(("operator", dict(kwargs, name=name, operator=operator)))
        return operator

    def template_list(self, *_args, **kwargs):
        self.events.append(("queue", kwargs))

    def separator(self, **_kwargs):
        pass

    separator_spacer = separator


class CoreObjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
            raise RuntimeError("Run this test in a separate factory-startup Blender process")
        rr.register()

    @classmethod
    def tearDownClass(cls):
        rr.unregister()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="rr_core_object_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.queue_sync = mock.patch.object(rr, "SYNCING_QUEUE_ACTIVE_INDEX", True)
        self.queue_sync.start()
        self.addCleanup(self.queue_sync.stop)
        rr.clear_reference_object(bpy.context.scene)
        bpy.context.scene.rr_builder_export_settings.export_queue.clear()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            bpy.data.meshes.remove(mesh)
        rr.reset_object_manager_duplicate_guard()
        rr.reset_object_manager_name_sync_state()
        rr.reset_scene_selection_queue_lookup()
        settings = self.settings
        settings.output_root = str(self.directory / "exports")
        settings.export_mode = rr.EXPORT_MODE_GENERAL
        settings.skip_existing_exports = False
        settings.include_model_with_export = True
        settings.include_icon_with_export = False
        settings.use_reference_layout = False
        settings.reference_layout_state_initialized = True
        self.reference_settings.include_reference_mesh = False
        self.reference_settings.core_ui_version = 0
        self.reference_settings.legacy_layout_pending = False

    @property
    def settings(self):
        return bpy.context.scene.rr_builder_export_settings

    @property
    def reference_settings(self):
        return bpy.context.scene.rr_builder_reference_layout

    def queue(self, *objects):
        self.settings.export_queue.clear()
        for obj in objects:
            self.settings.export_queue.add().object_name = obj.name

    def export_queue(self):
        # Standard exports never need a Unity import request. Guard against
        # accidental production-facing publication if the route regresses.
        with mock.patch.object(
            rr, "queue_unity_builder_import", side_effect=AssertionError("Unexpected Unity import")
        ):
            self.assertEqual(
                bpy.ops.rr_builder.export_queue(include_model=True, include_icon=False),
                {"FINISHED"},
            )

    def test_excluded_core_cannot_leak_through_exported_parent(self):
        parent = bpy.data.objects.new("CoreParent_400x10x400", None)
        bpy.context.scene.collection.objects.link(parent)
        core = make_mesh("NestedCore_400x10x400")
        core.parent = parent
        part = make_mesh("OtherPart_100x10x100")
        part.parent = parent
        rr.mark_reference_object(core, bpy.context.scene)
        self.reference_settings.include_reference_mesh = False
        self.queue(parent)
        with mock.patch.object(rr, "export_builder_asset") as export:
            with self.assertRaisesRegex(RuntimeError, "contains the Core Object"):
                bpy.ops.rr_builder.export_queue(include_model=True, include_icon=False)
            export.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "contains the Core Object"):
            rr.core_roots_for_export_batch([parent], self.settings, bpy.context.scene)
        self.assertEqual(rr.core_roots_for_export_batch([part], self.settings), [part])
        self.assertFalse(Path(self.settings.output_root).exists())

    def manifest(self, obj):
        path = Path(self.settings.output_root) / rr.export_asset_id(obj) / "manifest.json"
        self.assertTrue(path.is_file(), str(path))
        return json.loads(path.read_text(encoding="utf-8"))

    def assert_relative_layout(self, obj, core):
        block = self.manifest(obj)["referenceLayout"]
        self.assertEqual(block["role"], "reference" if obj == core else "member")
        self.assertEqual(block["referenceStableId"], core[rr.REFERENCE_STABLE_ID_PROP])
        self.assertEqual(block["sourceStableId"], obj[rr.EXPORT_STABLE_ID_PROP])
        expected = core.matrix_world.inverted() @ obj.matrix_world
        expected = rr.unity_reference_layout_matrix(expected)
        self.assertEqual(block["coordinateSpace"], "UNITY")
        actual = block["relativeAuthoringMatrix"]
        self.assertEqual(len(actual), 16)
        for row in range(4):
            for column in range(4):
                self.assertAlmostEqual(actual[row * 4 + column], expected[row][column], places=5)

    def test_toggle_switch_clear_preserve_identity_and_transforms(self):
        first = make_mesh("CoreFirst_400x10x400")
        second = make_mesh("CoreSecond_400x10x400")
        second.parent = first
        second.matrix_world = Matrix.Translation((7, -3, 2))
        original = second.matrix_world.copy()
        select_only(first)
        self.assertEqual(bpy.ops.rr_builder.toggle_core(), {"FINISHED"})
        first_id = first[rr.EXPORT_STABLE_ID_PROP]
        self.assertIs(rr.get_reference_object(), first)
        self.assertTrue(rr.reference_layout_is_active())
        self.assertTrue(rr.export_mode_uses_reference_layout(self.settings))
        # Explicit row actions address the row's object, regardless of selection.
        self.assertEqual(bpy.ops.rr_builder.toggle_core(object_name=second.name), {"FINISHED"})
        second_id = second[rr.EXPORT_STABLE_ID_PROP]
        self.assertIs(rr.get_reference_object(), second)
        self.assertFalse(rr.is_reference_object(first))
        self.assertEqual(first[rr.EXPORT_STABLE_ID_PROP], first_id)
        self.assertIs(second.parent, first)
        self.assertEqual(second.matrix_world, original)
        self.assertEqual(bpy.ops.rr_builder.toggle_core(object_name=second.name), {"FINISHED"})
        self.assertIsNone(rr.get_reference_object())
        self.assertFalse(rr.reference_layout_is_active())
        self.assertFalse(rr.export_mode_uses_reference_layout(self.settings))
        self.assertEqual(second[rr.EXPORT_STABLE_ID_PROP], second_id)
        self.assertEqual(rr.mark_reference_object(second), second_id)
        second.name = "RenamedCore_400x10x400"
        self.assertIs(rr.get_reference_object(), second)
        self.assertEqual(second[rr.EXPORT_STABLE_ID_PROP], second_id)

    def test_core_and_export_choice_survive_saved_scene(self):
        core = make_mesh("PersistentCore_400x10x400")
        other = make_mesh("PersistentOther_100x10x100")
        stable_id = rr.mark_reference_object(core)
        self.reference_settings.include_reference_mesh = False
        select_only(other)
        filepath = self.directory / "core_state.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(filepath))
        for _ in range(2):
            bpy.ops.wm.open_mainfile(filepath=str(filepath), load_ui=False, use_scripts=False)
            core = bpy.data.objects["PersistentCore_400x10x400"]
            self.assertIs(rr.get_reference_object(), core)
            self.assertTrue(rr.reference_layout_is_active())
            self.assertEqual(core[rr.REFERENCE_STABLE_ID_PROP], stable_id)
            self.assertFalse(self.reference_settings.include_reference_mesh)
            self.assertFalse(rr.legacy_reference_layout_pending())

    def legacy_fixture(self, initialized, enabled, *, include=False, queued=False):
        core = make_mesh("LegacyCore_400x10x400")
        stable_id = rr.ensure_export_identity(core)[0]
        core[rr.REFERENCE_MARK_PROP] = True
        core[rr.REFERENCE_STABLE_ID_PROP] = stable_id
        self.reference_settings.reference_object = core
        self.reference_settings.core_ui_version = 0
        self.reference_settings.legacy_layout_pending = False
        self.reference_settings.include_reference_mesh = include
        self.settings.reference_layout_state_initialized = initialized
        self.settings.use_reference_layout = enabled
        if queued:
            self.queue(core)
        return core, stable_id

    def test_legacy_implicit_layout_migrates_without_changing_identity(self):
        core, stable_id = self.legacy_fixture(False, False, include=True)
        rr.migrate_reference_layout_usage_on_load()
        self.assertTrue(rr.reference_layout_is_active())
        self.assertFalse(rr.legacy_reference_layout_pending())
        self.assertTrue(self.reference_settings.include_reference_mesh)
        self.assertEqual(core[rr.EXPORT_STABLE_ID_PROP], stable_id)

    def test_legacy_explicitly_disabled_layout_remains_pending_until_marked(self):
        core, stable_id = self.legacy_fixture(True, False, include=True)
        path = self.directory / "legacy_disabled.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
        core = bpy.data.objects["LegacyCore_400x10x400"]
        self.assertIs(rr.get_reference_object(), core)
        self.assertTrue(rr.legacy_reference_layout_pending())
        self.assertFalse(rr.reference_layout_is_active())
        self.assertFalse(rr.export_mode_uses_reference_layout(self.settings))
        self.assertTrue(self.reference_settings.include_reference_mesh)
        self.assertEqual(core[rr.EXPORT_STABLE_ID_PROP], stable_id)
        rr.migrate_reference_layout_usage_on_load()
        self.assertTrue(rr.legacy_reference_layout_pending(), "Migration must be idempotent")
        self.assertEqual(bpy.ops.rr_builder.toggle_core(object_name=core.name), {"FINISHED"})
        self.assertTrue(rr.reference_layout_is_active())
        self.assertFalse(rr.legacy_reference_layout_pending())
        self.assertEqual(core[rr.EXPORT_STABLE_ID_PROP], stable_id)

    def test_legacy_queued_core_keeps_previous_export_scope(self):
        core, stable_id = self.legacy_fixture(True, True, include=False, queued=True)
        rr.migrate_reference_layout_usage_on_load()
        self.assertTrue(rr.reference_layout_is_active())
        self.assertTrue(self.reference_settings.include_reference_mesh)
        self.assertEqual(core[rr.EXPORT_STABLE_ID_PROP], stable_id)
        self.reference_settings.include_reference_mesh = False
        rr.migrate_reference_layout_usage_on_load()
        self.assertFalse(self.reference_settings.include_reference_mesh, "Later choices must survive migration")

    def test_real_two_batch_export_uses_core_without_replacing_core_files(self):
        core = make_mesh("CoreFloor_400x10x400")
        part = make_mesh("CorePart_100x10x100")
        core.matrix_world = Matrix.Translation((12, -7, 3)) @ Matrix.Rotation(math.radians(37), 4, "Z")
        part.matrix_world = Matrix.Translation((16, -2, 4)) @ Matrix.Rotation(math.radians(-18), 4, "Z")
        rr.mark_reference_object(core)
        self.reference_settings.include_reference_mesh = True
        self.queue(part)
        with mock.patch.object(rr, "export_builder_asset", wraps=rr.export_builder_asset) as exported:
            self.export_queue()
        self.assertCountEqual([call.args[0].name for call in exported.call_args_list], [core.name, part.name])
        self.assert_relative_layout(core, core)
        self.assert_relative_layout(part, core)
        core_directory = Path(self.settings.output_root) / rr.export_asset_id(core)
        before = directory_hashes(core_directory)
        self.assertIn("model.fbx", before)
        self.assertIn("manifest.json", before)
        later = make_mesh("CoreLaterPart_100x10x100")
        later.matrix_world = Matrix.Translation((9, 3, 5))
        self.reference_settings.include_reference_mesh = False
        # Existing queue membership must not override Export Core Object = off.
        self.queue(core, later)
        with mock.patch.object(rr, "export_builder_asset", wraps=rr.export_builder_asset) as exported:
            self.export_queue()
        self.assertEqual([call.args[0].name for call in exported.call_args_list], [later.name])
        self.assertEqual(directory_hashes(core_directory), before)
        self.assert_relative_layout(later, core)
        self.assertIs(rr.get_reference_object(), core)
        self.assertTrue(rr.reference_layout_is_active())

    def test_core_only_export_needs_no_queue_and_explicit_queue_does_not_duplicate(self):
        core = make_mesh("OnlyCore_400x10x400")
        rr.mark_reference_object(core)
        self.reference_settings.include_reference_mesh = True
        for explicitly_queued in (False, True):
            with self.subTest(explicitly_queued=explicitly_queued):
                self.queue(*(core,) if explicitly_queued else ())
                with mock.patch.object(rr, "export_builder_asset", wraps=rr.export_builder_asset) as exported:
                    self.export_queue()
                self.assertEqual(exported.call_count, 1)
                self.assertIs(exported.call_args.args[0], core)
                self.assert_relative_layout(core, core)

    def test_no_core_exports_ordinary_manifest(self):
        part = make_mesh("OrdinaryPart_100x10x100")
        self.queue(part)
        self.export_queue()
        self.assertNotIn("referenceLayout", self.manifest(part))

    def test_pinned_core_ui_exposes_persistent_marker_and_export_choice(self):
        core = make_mesh("VisibleCore_400x10x400")
        other = make_mesh("SelectedOther_100x10x100")
        rr.mark_reference_object(core)
        self.queue(other)
        self.settings.export_reference_expanded = False
        select_only(other)
        for include in (False, True):
            with self.subTest(include=include):
                self.reference_settings.include_reference_mesh = include
                layout = LayoutRecorder()
                probe = SimpleNamespace(
                    draw_fold_panel=lambda target, *_args, **_kwargs: target,
                    draw_export_output_row=lambda *_args: None,
                )
                rr.RR_PT_builder_exporter.draw_export_queue_box(probe, layout, bpy.context, self.settings)
                queue_index = next(index for index, event in enumerate(layout.events) if event[0] == "queue")
                pinned = layout.events[:queue_index]
                self.assertTrue(any(
                    core.name in event.get("text", "")
                    for kind, event in pinned if kind in {"label", "operator"}
                ), "The Core name must stay above the queue while another object is selected")
                self.assertTrue(any(
                    kind == "operator" and event.get("name") == "rr_builder.toggle_core"
                    and event.get("depress") is True
                    for kind, event in pinned
                ), "The marked Core star must retain its pressed state")
                controls = [event for kind, event in layout.events if kind == "prop"]
                self.assertFalse(any(event["property"] == "use_reference_layout" for event in controls))
                self.assertTrue(any(
                    event["property"] == "include_reference_mesh" and event.get("text") == "Export Core Object"
                    for event in controls
                ))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(CoreObjectTests)
    )
    if result.wasSuccessful():
        print(f"RR_CORE_OBJECT_PASS tests={result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
