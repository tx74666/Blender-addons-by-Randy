"""Make Assembly keeps existing queue entries coherent without exporting files.

Run in Blender --background --factory-startup --disable-autoexec --threads 2
--python-exit-code 1 --python tests/test_rr_assembly_queue_blender.py.
An optional unittest name after -- selects one case.
"""

import importlib
from pathlib import Path
import sys
import unittest

import bpy
from mathutils import Matrix


ADDONS = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDONS))
rr = importlib.import_module("random_realm_builder_exporter")
assert Path(rr.__file__).resolve().is_relative_to(ADDONS.resolve()), rr.__file__


class AssemblyQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
            raise RuntimeError("Use a separate --factory-startup Blender process")
        rr.register()

    @classmethod
    def tearDownClass(cls):
        rr.unregister()

    def setUp(self):
        self.settings = bpy.context.scene.rr_builder_export_settings
        self.settings.export_queue.clear()
        rr.clear_reference_object(bpy.context.scene)
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        rr.reset_object_manager_duplicate_guard()
        rr.reset_object_manager_name_sync_state()
        rr.reset_scene_selection_queue_lookup()
        self.settings.object_manager_assembly_type = "ASSEMBLY"
        self.settings.object_manager_assembly_name = ""

    def mesh(self, name, offset=0):
        mesh = bpy.data.meshes.new(name + "Mesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 1)], [], [(0, 1, 2)])
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = Matrix.Translation((20 + offset, 30, 4)) @ Matrix.Rotation(0.3, 4, "Z")
        return obj

    def queue(self, obj, zoom=1.0):
        item = self.settings.export_queue.add()
        item.object_name = obj.name
        item.icon_preview_root_name = obj.name
        rr.initialize_queue_item_framing(item, obj, self.settings)
        item.icon_zoom = zoom
        item.icon_offset_x = 0.25
        item.icon_offset_y = -0.5
        item.icon_view_yaw = 37
        item.icon_view_pitch = 20
        item.preview_path = "old-single-part-icon.png"
        return item

    def make(self, parts, group_type="ASSEMBLY"):
        for selected in list(bpy.context.selected_objects):
            selected.select_set(False)
        for part in parts:
            part.select_set(True)
        bpy.context.view_layer.objects.active = parts[0]
        self.settings.object_manager_assembly_type = group_type
        self.assertEqual({"FINISHED"}, bpy.ops.rr_builder.create_object_assembly())
        root = rr.object_manager_assembly_root_for_object(parts[0])
        self.assertIsNotNone(root)
        return root

    def names(self):
        return [item.object_name for item in self.settings.export_queue]

    def test_make_replaces_queued_parts_once_preserving_order_framing_and_origins(self):
        core = self.mesh("Hub_Floor_Circular_A")
        rr.mark_reference_object(core, bpy.context.scene)
        casing, door, platform = [self.mesh(name, index) for index, name in enumerate(
            ("Hub_Elevator_Casing", "Hub_Elevator_Door1.L", "Hub_Elevator_Platform"))]
        other = self.mesh("Unrelated_Wall")
        parts = [casing, door, platform]
        matrices = {part: part.matrix_world.copy() for part in parts}
        for part in parts:
            part[rr.EXPORT_STABLE_ID_PROP] = "copied-old-part-identity"
        self.queue(core, 1.25)
        first = self.queue(casing, 1.75)
        expected_framing = {name: getattr(first, name) for name in
                            ("icon_zoom", "icon_offset_x", "icon_offset_y", "icon_view_yaw", "icon_view_pitch")}
        self.queue(other, 1.5)
        self.queue(door, 2.0)
        self.queue(platform, 2.25)
        rr.set_queue_active_index_without_preview_sync(self.settings, 4)

        root = self.make(parts)

        self.assertEqual([core.name, root.name, other.name], self.names())
        self.assertEqual(1, self.settings.queue_active_index)
        self.assertEqual(core, rr.get_reference_object(bpy.context.scene))
        item = self.settings.export_queue[1]
        self.assertEqual(root.name, item.icon_preview_root_name)
        self.assertEqual("", item.preview_path)
        for name, value in expected_framing.items():
            self.assertAlmostEqual(value, getattr(item, name))
        self.assertAlmostEqual(1.75, root["rr_icon_zoom"])
        self.assertEqual(set(parts), set(rr.get_export_asset_meshes(root)))
        self.assertEqual([root], rr.expand_related_export_roots([rr.queue_item_object(item)]))
        self.assertNotEqual("copied-old-part-identity", rr.ensure_export_identity(root)[0])
        for part in parts:
            self.assertEqual(root, part.parent)
            self.assertEqual("copied-old-part-identity", part[rr.EXPORT_STABLE_ID_PROP])
            for row in range(4):
                for column in range(4):
                    self.assertAlmostEqual(matrices[part][row][column], part.matrix_world[row][column], places=5)

    def test_make_does_not_queue_a_selection_that_was_not_queued(self):
        other = self.mesh("Already_Queued")
        self.queue(other)
        root = self.make([self.mesh("New_Casing"), self.mesh("New_Door")])
        self.assertEqual([other.name], self.names())
        self.assertNotIn(root.name, self.names())

    def test_make_variants_leaves_existing_queue_semantics_unchanged(self):
        left, right = self.mesh("Alternative_A"), self.mesh("Alternative_B")
        self.queue(left, 1.25)
        self.queue(right, 1.5)
        before = [(item.object_name, item.icon_zoom, item.preview_path) for item in self.settings.export_queue]
        self.make([left, right], "VARIANTS")
        self.assertEqual(before, [(item.object_name, item.icon_zoom, item.preview_path)
                                  for item in self.settings.export_queue])

    def test_make_keeps_core_queue_entry_even_if_core_was_selected(self):
        core, casing = self.mesh("Core"), self.mesh("Casing")
        rr.mark_reference_object(core, bpy.context.scene)
        self.queue(core, 1.25)
        self.queue(casing, 1.75)
        root = self.make([core, casing])
        self.assertEqual([core.name, root.name], self.names())
        self.assertEqual(core, rr.get_reference_object(bpy.context.scene))
        self.assertAlmostEqual(1.25, self.settings.export_queue[0].icon_zoom)

    def test_make_preserves_unrelated_active_queue_item(self):
        casing, door, other = self.mesh("Casing"), self.mesh("Door"), self.mesh("Other")
        self.queue(casing)
        self.queue(door)
        self.queue(other)
        rr.set_queue_active_index_without_preview_sync(self.settings, 2)
        root = self.make([casing, door])
        self.assertEqual([root.name, other.name], self.names())
        self.assertEqual(1, self.settings.queue_active_index)

    def test_uninitialized_member_framing_uses_existing_object_settings(self):
        casing, door = self.mesh("Casing"), self.mesh("Door")
        casing["rr_icon_framing_initialized"] = True
        casing["rr_icon_zoom"] = 1.8
        casing["rr_icon_view_yaw"] = 41.0
        item = self.queue(casing)
        item.framing_initialized = False
        root = self.make([casing, door])
        self.assertEqual([root.name], self.names())
        self.assertTrue(self.settings.export_queue[0].framing_initialized)
        self.assertAlmostEqual(1.8, self.settings.export_queue[0].icon_zoom)
        self.assertAlmostEqual(41.0, self.settings.export_queue[0].icon_view_yaw)


if __name__ == "__main__":
    names = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    suite = (unittest.defaultTestLoader.loadTestsFromNames(names, sys.modules[__name__]) if names
             else unittest.defaultTestLoader.loadTestsFromTestCase(AssemblyQueueTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print(f"RR_ASSEMBLY_QUEUE_PASS tests={result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
