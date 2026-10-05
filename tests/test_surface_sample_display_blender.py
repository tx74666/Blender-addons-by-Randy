"""Wire-only Surface Text helper regressions in a disposable Blender process.

Run with --background --factory-startup. All fixtures and the temporary FBX /
linked-library files are generated here; no authored project is opened or saved.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
import random_realm_builder_exporter as exporter
from random_realm_builder_exporter import rr_surface_text as surface
from test_surface_sample_names_blender import add_sample, fixture


RAY_FLAGS = (
    "visible_camera", "visible_diffuse", "visible_glossy",
    "visible_transmission", "visible_shadow", "visible_volume_scatter",
    "visible_raycast",
)
PROBE_FLAGS = ("hide_probe_volume", "hide_probe_sphere", "hide_probe_plane")


def clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.curves, bpy.data.materials):
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)
    bpy.context.scene.unit_settings.scale_length = 1.0


def matrix_values(matrix):
    return tuple(tuple(row) for row in matrix)


def owned_value(value):
    if isinstance(value, bpy.types.ID):
        return ("ID", value.as_pointer())
    if hasattr(value, "items"):
        return tuple(sorted((key, owned_value(item)) for key, item in value.items()))
    if hasattr(value, "to_list"):
        return tuple(owned_value(item) for item in value.to_list())
    if isinstance(value, (tuple, list)):
        return tuple(owned_value(item) for item in value)
    return value


def mesh_values(mesh):
    return (
        tuple(tuple(vertex.co) for vertex in mesh.vertices),
        tuple(tuple(edge.vertices) for edge in mesh.edges),
        tuple((tuple(face.vertices), face.material_index, face.use_smooth)
              for face in mesh.polygons),
    )


def display_values(obj):
    return (
        obj.display_type, obj.show_wire, obj.show_all_edges, obj.hide_render,
        tuple((name, getattr(obj, name)) for name in RAY_FLAGS + PROBE_FLAGS
              if hasattr(obj, name)),
    )


def persistent_values(obj):
    """Data and user state that the display repair has no reason to alter."""
    data = getattr(obj, "data", None)
    return (
        obj.name, obj.as_pointer(), data.as_pointer() if data else None,
        data.name if data else None,
        obj.parent.as_pointer() if obj.parent else None,
        matrix_values(obj.matrix_parent_inverse), matrix_values(obj.matrix_basis),
        matrix_values(obj.matrix_world),
        obj.hide_viewport, obj.hide_get(), obj.hide_select, obj.select_get(),
        owned_value(dict(obj.items())), owned_value(dict(data.items())) if data else None,
        mesh_values(data) if obj.type == "MESH" else None,
        tuple((slot.link, slot.material.as_pointer() if slot.material else None)
              for slot in obj.material_slots),
    )


def evaluated_world_mesh(obj):
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    try:
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        return (
            tuple(tuple(evaluated.matrix_world @ vertex.co) for vertex in mesh.vertices),
            tuple(tuple(face.vertices) for face in mesh.polygons),
        )
    finally:
        evaluated.to_mesh_clear()


def legacy_display(obj):
    # This is a deliberately visible old helper, not the expected new result.
    obj.display_type = "SOLID"
    obj.show_wire = False
    obj.show_all_edges = False
    obj.hide_render = False
    for name in RAY_FLAGS:
        if hasattr(obj, name):
            setattr(obj, name, True)
    for name in PROBE_FLAGS:
        if hasattr(obj, name):
            setattr(obj, name, False)


def datablocks():
    return (set(bpy.data.objects.keys()), set(bpy.data.meshes.keys()),
            set(bpy.data.curves.keys()), set(bpy.data.materials.keys()))


class SurfaceSampleDisplayTests(unittest.TestCase):
    def setUp(self):
        clear_scene()

    def tearDown(self):
        clear_scene()

    def assert_wire_only(self, sample):
        self.assertEqual(sample.display_type, "WIRE")
        self.assertTrue(sample.show_wire)
        self.assertTrue(sample.show_all_edges)
        self.assertTrue(sample.hide_render)
        for name in RAY_FLAGS:
            if hasattr(sample, name):
                self.assertFalse(getattr(sample, name), name)
        for name in PROBE_FLAGS:
            if hasattr(sample, name):
                self.assertTrue(getattr(sample, name), name)

    def test_new_sample_is_wire_only_but_still_available_in_the_view_layer(self):
        target, region = fixture()
        sample, text = add_sample(target, region)
        self.assert_wire_only(sample)
        self.assertFalse(sample.hide_viewport)
        self.assertFalse(sample.hide_get())
        self.assertIs(sample.parent, target)
        self.assertIs(text["rr_surface_text_surface_ref"], sample)
        self.assertIn(sample, list(bpy.context.view_layer.objects))
        self.assertEqual(len(sample.data.polygons), 1)

    def test_classification_and_repair_use_the_mesh_role_not_its_name(self):
        target, region = fixture()
        sample, _ = add_sample(target, region, text=False)
        sample.name = "Artist chosen sampling region"
        legacy_display(sample)
        ordinary = bpy.data.objects.new("RR_SurfaceSample_ArtistMesh", sample.data)
        empty = bpy.data.objects.new("Tagged empty", None)
        font_data = bpy.data.curves.new("Tagged FontData", "FONT")
        font = bpy.data.objects.new("Tagged Font", font_data)
        alias = bpy.data.objects.new("RR_SurfaceSample_Export_fake", sample.data)
        empty["rr_surface_role"] = "sampling_surface"
        font["rr_surface_role"] = "sampling_surface"
        alias["rr_surface_role"] = "sampling_surface_export_alias"
        for obj in (ordinary, empty, font, alias):
            bpy.context.scene.collection.objects.link(obj)
            legacy_display(obj)
        untouched = {obj.as_pointer(): (display_values(obj), persistent_values(obj))
                     for obj in (target, ordinary, empty, font, alias)}
        self.assertTrue(surface.is_surface_sampling_helper(sample))
        self.assertFalse(surface.is_surface_sampling_helper(None))
        for obj in (target, ordinary, empty, font, alias):
            self.assertFalse(surface.is_surface_sampling_helper(obj), obj.name)
            self.assertFalse(surface.configure_surface_sample_display(obj), obj.name)
        result = surface.repair_surface_sample_display()
        self.assertEqual(result, {"objects": [sample.name], "skipped": []})
        self.assert_wire_only(sample)
        for obj in (target, ordinary, empty, font, alias):
            self.assertEqual((display_values(obj), persistent_values(obj)),
                             untouched[obj.as_pointer()], obj.name)

    def test_existing_helper_repair_is_idempotent_and_preserves_authored_data(self):
        target, region = fixture()
        sample, text = add_sample(target, region)
        shared = bpy.data.materials.new("Shared wall material")
        shared.diffuse_color = (0.2, 0.4, 0.6, 0.8)
        override = bpy.data.materials.new("Artist helper override")
        override.diffuse_color = (0.8, 0.3, 0.1, 1.0)
        target.data.materials.append(shared)
        sample.data.materials.append(shared)
        sample.material_slots[0].link = "OBJECT"
        sample.material_slots[0].material = override
        sample.name = "Custom wall region"
        sample.data.name = "Custom region geometry"
        sample["artist"] = {"annotation": "keep me", "weights": [0.1, 0.4, 0.9]}
        sample.data["artist_mesh_note"] = "keep geometry metadata"
        target.matrix_world = (Matrix.Translation((3.0, -2.0, 1.0))
                               @ Matrix.Rotation(0.3, 4, "Z"))
        bpy.context.view_layer.objects.active = text
        target.select_set(True)
        text.select_set(True)
        legacy_display(sample)
        bpy.context.view_layer.update()
        before = [persistent_values(obj) for obj in (target, sample, text)]
        refs = (text["rr_surface_text_surface_ref"],
                text["rr_surface_text_target_ref"],
                text["rr_surface_text_surface_object"],
                text["rr_surface_text_surface_mesh"])
        material_values = [(item.as_pointer(), tuple(item.diffuse_color), item.users)
                           for item in (shared, override)]
        active = bpy.context.view_layer.objects.active
        before_blocks = datablocks()
        self.assertEqual(surface.repair_surface_sample_display([sample]),
                         {"objects": [sample.name], "skipped": []})
        self.assert_wire_only(sample)
        self.assertEqual([persistent_values(obj) for obj in (target, sample, text)], before)
        self.assertEqual((text["rr_surface_text_surface_ref"],
                          text["rr_surface_text_target_ref"],
                          text["rr_surface_text_surface_object"],
                          text["rr_surface_text_surface_mesh"]), refs)
        self.assertEqual([(item.as_pointer(), tuple(item.diffuse_color), item.users)
                          for item in (shared, override)], material_values)
        self.assertIs(bpy.context.view_layer.objects.active, active)
        self.assertEqual(datablocks(), before_blocks)
        self.assertFalse(surface.configure_surface_sample_display(sample))
        self.assertEqual(surface.repair_surface_sample_display([sample]),
                         {"objects": [], "skipped": []})
        self.assertEqual([persistent_values(obj) for obj in (target, sample, text)], before)

    def test_repair_preserves_existing_hidden_and_selectability_choices(self):
        target, region = fixture()
        sample, _ = add_sample(target, region, text=False)
        for hidden_viewport, hidden_layer, hide_select in (
            (False, False, False), (True, False, True), (False, True, True),
        ):
            with self.subTest(viewport=hidden_viewport, layer=hidden_layer):
                sample.hide_viewport = hidden_viewport
                sample.hide_set(hidden_layer)
                sample.hide_select = hide_select
                legacy_display(sample)
                before = persistent_values(sample)
                surface.repair_surface_sample_display([sample])
                self.assert_wire_only(sample)
                self.assertEqual(persistent_values(sample), before)

    def test_ray_invisibility_does_not_disable_a_real_shrinkwrap_target(self):
        target, region = fixture()
        sample, text = add_sample(target, region)
        text.data.body = "O"
        text.data.size = 0.5
        text.location.z = 0.5
        shrinkwrap, solidify = surface._surface_text_modifier_pair(text)
        shrinkwrap.target = sample
        legacy_display(sample)
        before_mesh = evaluated_world_mesh(text)
        self.assertGreater(len(before_mesh[0]), 0)
        # Projection really reaches the helper, rather than leaving the Font
        # at z=.5. Solidify adds the independent .1 native text thickness.
        self.assertLess(min(point[2] for point in before_mesh[0]), 0.01)
        self.assertLess(max(point[2] for point in before_mesh[0]), 0.12)
        before_sample = persistent_values(sample)
        before_text = persistent_values(text)
        modifier_values = (shrinkwrap.offset, shrinkwrap.project_limit,
                           solidify.thickness, solidify.offset)
        self.assertTrue(surface.configure_surface_sample_display(sample))
        self.assert_wire_only(sample)
        self.assertEqual(evaluated_world_mesh(text), before_mesh)
        self.assertIs(shrinkwrap.target, sample)
        self.assertIs(text["rr_surface_text_surface_ref"], sample)
        self.assertEqual(persistent_values(sample), before_sample)
        self.assertEqual(persistent_values(text), before_text)
        self.assertEqual((shrinkwrap.offset, shrinkwrap.project_limit,
                          solidify.thickness, solidify.offset), modifier_values)

    def test_linked_uneditable_helper_is_reported_and_left_intact(self):
        target, region = fixture("Linked source fixture")
        sample, _ = add_sample(target, region, text=False)
        legacy_display(sample)
        with tempfile.TemporaryDirectory(prefix="rr_wire_library_") as directory:
            path = os.path.join(directory, "sampling_fixture.blend")
            bpy.data.libraries.write(path, {sample})
            with bpy.data.libraries.load(path, link=True) as (_available, requested):
                requested.objects = [sample.name]
            linked = requested.objects[0]
            self.assertIsNotNone(linked)
            bpy.context.scene.collection.objects.link(linked)
            bpy.context.view_layer.update()
            self.assertFalse(linked.is_editable)
            before = (display_values(linked), persistent_values(linked))
            result = surface.repair_surface_sample_display([linked])
            self.assertEqual(result, {"objects": [], "skipped": [linked.name]})
            self.assertEqual((display_values(linked), persistent_values(linked)), before)

    def test_asset_mesh_queries_exclude_persistent_helpers_only(self):
        target, region = fixture()
        sample, _ = add_sample(target, region, text=False)
        ordinary = bpy.data.objects.new("RR_SurfaceSample_UserGeometry", sample.data)
        alias = bpy.data.objects.new("Sampling export fixture", sample.data)
        alias["rr_surface_role"] = "sampling_surface_export_alias"
        for obj in (ordinary, alias):
            bpy.context.scene.collection.objects.link(obj)
            obj.parent = target
        # A role on the mesh datablock is not permission to drop an ordinary
        # object sharing that data from an asset preview/export enumeration.
        self.assertEqual(sample.data["rr_surface_role"], "sampling_surface_mesh")
        expected = {target, ordinary, alias}
        for query in (exporter.get_asset_meshes, exporter.get_standalone_asset_meshes):
            self.assertEqual(set(query(target)), expected, query.__name__)
            self.assertNotIn(sample, query(target), query.__name__)

    def test_real_sampling_alias_stays_exportable_and_is_not_repaired(self):
        target, region = fixture()
        sample, _text = add_sample(target, region)
        before_blocks = datablocks()
        before_sample = persistent_values(sample)
        aliases = []
        try:
            aliases = surface.create_surface_text_sampling_export_aliases(target)
            self.assertEqual(len(aliases), 1)
            alias = aliases[0]
            self.assertEqual(alias.display_type, "TEXTURED")
            self.assertFalse(alias.hide_render)
            self.assertFalse(alias.hide_viewport)
            self.assertFalse(surface.is_surface_sampling_helper(alias))
            self.assertEqual(len(alias.data.polygons), len(sample.data.polygons))
            if hasattr(alias, "visible_camera"):
                self.assertTrue(alias.visible_camera)
            before_alias = (display_values(alias), persistent_values(alias))
            self.assertEqual(surface.repair_surface_sample_display([sample, alias]),
                             {"objects": [], "skipped": []})
            self.assertEqual((display_values(alias), persistent_values(alias)), before_alias)
            self.assertEqual(persistent_values(sample), before_sample)
        finally:
            for alias in aliases:
                mesh = alias.data
                bpy.data.objects.remove(alias, do_unlink=True)
                if mesh.users == 0:
                    bpy.data.meshes.remove(mesh)
        self.assertEqual(datablocks(), before_blocks)

    def test_helper_using_its_reserved_alias_name_is_rejected_without_data_loss(self):
        target, region = fixture()
        sample, text = add_sample(target, region)
        sample.name = surface.surface_sampling_export_name(sample)
        # Establish the renamed artist state before the operation under test;
        # ordinary legacy-name synchronization is not a display change.
        surface._sync_surface_text_references(text, target)
        before_blocks = datablocks()
        before_sample = (display_values(sample), persistent_values(sample))
        before_text = persistent_values(text)
        with self.assertRaisesRegex(RuntimeError, "reserved export name"):
            surface.create_surface_text_sampling_export_aliases(target)
        self.assertEqual(datablocks(), before_blocks)
        self.assertEqual((display_values(sample), persistent_values(sample)), before_sample)
        self.assertEqual(persistent_values(text), before_text)
        self.assertIs(text["rr_surface_text_surface_ref"], sample)
        self.assertIs(bpy.data.objects.get(sample.name), sample)

    def test_fbx_exports_sampling_alias_but_not_persistent_helper_and_cleans_up(self):
        target, region = fixture()
        sample, text = add_sample(target, region)
        descriptor = surface.build_surface_text_manifest(target)[0]
        sample_name = sample.name
        expected_points = [sample.matrix_world @ vertex.co for vertex in sample.data.vertices]
        self.assert_wire_only(sample)
        target.select_set(True)
        text.select_set(True)
        bpy.context.view_layer.objects.active = text
        before_blocks = datablocks()
        before_sample = (display_values(sample), persistent_values(sample))
        before_text = persistent_values(text)
        with tempfile.TemporaryDirectory(prefix="rr_wire_fbx_") as directory:
            path = os.path.join(directory, "wire_sampling.fbx")
            exporter.export_fbx(target, path)
            self.assertGreater(os.path.getsize(path), 0)
            self.assertEqual(datablocks(), before_blocks)
            self.assertEqual((display_values(sample), persistent_values(sample)), before_sample)
            self.assertEqual(persistent_values(text), before_text)
            self.assertIs(bpy.context.view_layer.objects.active, text)
            self.assertIs(text["rr_surface_text_surface_ref"], sample)
            self.assertIsNone(bpy.data.objects.get(descriptor["samplingSurfaceExportObjectName"]))
            clear_scene()
            self.assertEqual(bpy.ops.import_scene.fbx(filepath=path), {"FINISHED"})
            imported = bpy.data.objects.get(descriptor["samplingSurfaceExportObjectName"])
            self.assertIsNotNone(imported)
            self.assertEqual(imported.type, "MESH")
            self.assertIsNone(bpy.data.objects.get(sample_name))
            actual_points = [imported.matrix_world @ vertex.co for vertex in imported.data.vertices]
            self.assertEqual(len(imported.data.polygons), 1)
            for point in expected_points:
                self.assertLess(min((point - actual).length for actual in actual_points), 1.0e-4)
            self.assertIsNotNone(bpy.data.objects.get(descriptor["exportObjectName"]))
            self.assertTrue(all(bpy.data.objects.get(name) is not None
                                for name in descriptor["frameObjectNames"]))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceSampleDisplayTests)
    print("SURFACE_SAMPLE_DISPLAY_TEST_COUNT=" + str(suite.countTestCases()))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("RR_SURFACE_SAMPLE_DISPLAY_PASS")
    raise SystemExit(0 if result.wasSuccessful() else 1)
