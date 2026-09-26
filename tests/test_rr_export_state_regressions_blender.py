"""Run with Blender --background --factory-startup --python <this file>."""

import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import bpy


sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "addons"))
import random_realm_builder_exporter as exporter
from random_realm_builder_exporter import rr_surface_text


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for curve in list(bpy.data.curves):
        bpy.data.curves.remove(curve)


def make_cube(name):
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    obj.name = name
    return obj


def add_surface_text(root, suffix="First"):
    bpy.ops.mesh.primitive_plane_add()
    surface = bpy.context.object
    surface.name = "SamplingSurface_" + suffix
    surface.parent = root
    surface["rr_surface_role"] = "sampling_surface"
    surface["rr_surface_identity"] = "export_state_" + suffix
    surface.hide_render = True
    bpy.ops.object.text_add()
    source = bpy.context.object
    source.name = "SurfaceText_" + suffix
    source.data.body = "O"
    source["rr_surface_text_role"] = "surface_text"
    source["rr_surface_text_source_target"] = root.name
    source["rr_surface_text_source_target_full_name"] = root.name_full
    source["rr_surface_text_surface_object"] = surface.name
    solidify = source.modifiers.new("Solidify", "SOLIDIFY")
    solidify.thickness = 0.05
    bpy.context.view_layer.update()
    return source, surface


def scene_snapshot():
    active = bpy.context.view_layer.objects.active
    return {
        "objects": set(bpy.data.objects.keys()),
        "meshes": set(bpy.data.meshes.keys()),
        "active": active.name if active is not None else None,
        "states": {
            obj.name: (
                obj.hide_get(), obj.hide_viewport, obj.hide_render,
                obj.hide_select, obj.select_get(),
                tuple(sorted(collection.name for collection in obj.users_collection)),
            )
            for obj in bpy.context.view_layer.objects
        },
    }


class ExportSourceStateTests(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.temp = tempfile.TemporaryDirectory(prefix="rr_export_state_")
        self.addCleanup(self.temp.cleanup)
        self.root = make_cube("ExportStateRoot")
        self.source, self.surface = add_surface_text(self.root)
        self.sentinel = make_cube("RR_SurfaceText_Geometry_AuthoredSentinel")
        self.sentinel["rr_surface_text_export_role"] = "solid_geometry"
        collection = bpy.data.collections.new("ExportStateNested")
        bpy.context.scene.collection.children.link(collection)
        original_collections = list(self.root.users_collection)
        collection.objects.link(self.root)
        for original_collection in original_collections:
            original_collection.objects.unlink(self.root)
        bpy.ops.object.select_all(action="DESELECT")
        self.sentinel.select_set(True)
        bpy.context.view_layer.objects.active = self.sentinel
        for obj in (self.root, self.surface):
            obj.hide_set(True)
            obj.hide_viewport = True
            obj.hide_render = True
            obj.hide_select = True

    def tearDown(self):
        clear_scene()

    def test_repeated_real_fbx_cleans_owned_meshes_and_preserves_source(self):
        before = scene_snapshot()
        model_path = os.path.join(self.temp.name, "model.fbx")
        for _ in range(2):
            exporter.export_fbx(self.root, model_path)
            self.assertGreater(os.path.getsize(model_path), 0)
            self.assertEqual(scene_snapshot(), before)

        # Existing objects with an exporter-like prefix are neither selected
        # for this export nor removed by the transaction's cleanup.
        sentinel_name = self.sentinel.name
        expected_alias = rr_surface_text.surface_sampling_export_name(self.surface)
        clear_scene()
        bpy.ops.import_scene.fbx(filepath=model_path)
        imported_names = set(bpy.data.objects.keys())
        self.assertNotIn(sentinel_name, imported_names)
        self.assertIn(expected_alias, imported_names)
        self.assertTrue(any(name.startswith("RR_SurfaceText_Geometry_SurfaceText")
                            for name in imported_names))

    def test_real_fbx_write_failure_restores_source_and_cleans_owned_meshes(self):
        before = scene_snapshot()
        with self.assertRaises(Exception):
            exporter.export_fbx(
                self.root, os.path.join(self.temp.name, "missing", "model.fbx")
            )
        self.assertEqual(scene_snapshot(), before)

    def test_prepare_failure_restores_source_and_cleans_owned_meshes(self):
        before = scene_snapshot()
        with mock.patch.object(exporter, "prepare_unity_export_maps",
                               side_effect=RuntimeError("injected prepare failure")):
            with self.assertRaisesRegex(RuntimeError, "injected prepare failure"):
                exporter.export_fbx(self.root, os.path.join(self.temp.name, "model.fbx"))
        self.assertEqual(scene_snapshot(), before)

    def test_partial_alias_failure_cleans_both_helper_and_caller_resources(self):
        _, second_surface = add_surface_text(self.root, "Second")
        collision = make_cube(rr_surface_text.surface_sampling_export_name(second_surface))
        before = scene_snapshot()
        with self.assertRaisesRegex(RuntimeError, "already used"):
            exporter.export_fbx(self.root, os.path.join(self.temp.name, "model.fbx"))
        self.assertEqual(scene_snapshot(), before)
        self.assertIs(bpy.data.objects.get(collision.name), collision)

    def test_partial_solid_failure_preserves_all_original_datablocks(self):
        invalid_source, _ = add_surface_text(self.root, "Second")
        invalid_source.modifiers.clear()
        bpy.context.view_layer.update()
        before = scene_snapshot()
        with self.assertRaisesRegex(RuntimeError, "Solidify thickness"):
            exporter.export_fbx(self.root, os.path.join(self.temp.name, "model.fbx"))
        self.assertEqual(scene_snapshot(), before)

    def test_export_restores_no_active_object(self):
        bpy.context.view_layer.objects.active = None
        before = scene_snapshot()
        exporter.export_fbx(self.root, os.path.join(self.temp.name, "model.fbx"))
        self.assertEqual(scene_snapshot(), before)

    def test_cancelled_fbx_is_not_reported_as_success(self):
        before = scene_snapshot()
        operations = SimpleNamespace(
            object=bpy.ops.object,
            export_scene=SimpleNamespace(fbx=mock.Mock(return_value={"CANCELLED"})),
        )
        with mock.patch.object(exporter.bpy, "ops", operations):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                exporter.export_fbx(self.root, os.path.join(self.temp.name, "model.fbx"))
        self.assertEqual(scene_snapshot(), before)


class ExportQueue(list):
    def remove(self, index):
        self.pop(index)


class ExportQueueCompletionTests(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.temp = tempfile.TemporaryDirectory(prefix="rr_queue_completion_")
        self.addCleanup(self.temp.cleanup)
        self.root = make_cube("QueueCompletionAsset")
        self.settings = SimpleNamespace(
            export_mode=exporter.EXPORT_MODE_GENERAL,
            output_root=self.temp.name,
            use_reference_layout=False,
            export_queue=ExportQueue([SimpleNamespace(object_name=self.root.name)]),
            queue_active_index=0,
            icon_zoom=1.0, icon_offset_x=0.0, icon_offset_y=0.0,
            icon_view_yaw=0.0, icon_view_pitch=0.0,
            profile_name="Default", skip_existing_exports=False,
        )
        self.context = SimpleNamespace(
            scene=SimpleNamespace(rr_builder_export_settings=self.settings)
        )
        self.reports = []
        self.operator = SimpleNamespace(
            include_model=True, include_icon=False,
            report=lambda level, message: self.reports.append(message),
        )

    def tearDown(self):
        clear_scene()

    def execute(self):
        # Exercise the real queue, FBX, manifest, and publication paths. The
        # stand-in UI settings need no viewport mode/framing callbacks.
        with mock.patch.object(exporter, "sync_object_manager_names"), \
             mock.patch.object(exporter, "ensure_object_mode", return_value=True), \
             mock.patch.object(exporter, "prepare_framing_for_root"):
            return exporter.RR_OT_export_queue.execute(self.operator, self.context)

    def test_standard_success_clears_queue_without_unity_request(self):
        with mock.patch.object(exporter, "queue_unity_builder_import") as request:
            self.assertEqual(self.execute(), {"FINISHED"})
        request.assert_not_called()
        self.assertEqual(self.settings.export_queue, [])
        package = os.path.join(self.temp.name, exporter.export_asset_id(self.root))
        self.assertTrue(os.path.isfile(os.path.join(package, "model.fbx")))
        with open(os.path.join(package, "manifest.json"), encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["exportedResources"], ["model"])
        self.assertIn("cleared 1", self.reports[-1])

    def test_standard_failure_keeps_queue_and_skips_unity_request(self):
        with mock.patch.object(exporter, "export_fbx", side_effect=RuntimeError("write failed")), \
             mock.patch.object(exporter, "queue_unity_builder_import") as request:
            self.assertEqual(self.execute(), {"CANCELLED"})
        request.assert_not_called()
        self.assertEqual(len(self.settings.export_queue), 1)
        self.assertIn("cleared 0", self.reports[-1])

    def test_modular_request_failure_keeps_successfully_exported_item_queued(self):
        self.settings.export_mode = exporter.EXPORT_MODE_BUILDING
        with mock.patch.object(exporter, "queue_unity_builder_import",
                               side_effect=OSError("bridge request failed")) as request:
            self.assertEqual(self.execute(), {"FINISHED"})
        request.assert_called_once()
        self.assertEqual(len(self.settings.export_queue), 1)
        self.assertIn("cleared 0", self.reports[-1])

    def test_modular_request_success_clears_queue(self):
        self.settings.export_mode = exporter.EXPORT_MODE_BUILDING
        with mock.patch.object(exporter, "queue_unity_builder_import", return_value=[]) as request:
            self.assertEqual(self.execute(), {"FINISHED"})
        request.assert_called_once()
        self.assertEqual(self.settings.export_queue, [])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
