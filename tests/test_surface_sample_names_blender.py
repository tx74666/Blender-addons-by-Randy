"""Readable Surface Text sampling names; run only in a disposable Blender process."""

import os
import sys
import unittest

import bmesh
import bpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "addons"))
from random_realm_builder_exporter import rr_surface_text as surface


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.curves):
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)


def fixture(name="Hub_EntranceFrame_Rounded_A"):
    mesh = bpy.data.meshes.new("SourceMesh")
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.update()
    target = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(target)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        region = surface._selected_surface_info(target, list(bm.faces))
        region["candidate_label"] = "Original"
    finally:
        bm.free()
    return target, region


def add_sample(target, region, text=True):
    sample, _mesh = surface._create_surface_mesh(bpy.context, target, region)
    source = None
    if text:
        source, _curve = surface._create_text_object(bpy.context, target, region, 0.002, 10.0)
        surface._annotate_text_object(bpy.context, source, target, region, sample)
    return sample, source


def restore_legacy_names(sample):
    identity = sample["rr_surface_identity"]
    sample.name = f"{surface.SURFACE_SAMPLE_PREFIX}_{identity}"
    sample.data.name = f"{surface.SURFACE_SAMPLE_PREFIX}Mesh_{identity}"


def geometry(sample):
    return (tuple(tuple(vertex.co) for vertex in sample.data.vertices),
            tuple(tuple(face.vertices) for face in sample.data.polygons),
            tuple(tuple(row) for row in sample.matrix_world))


class SurfaceSampleNames(unittest.TestCase):
    def setUp(self):
        clear_scene()

    def tearDown(self):
        clear_scene()

    def test_new_names_keep_unicode_side_and_number_within_byte_limit(self):
        target, region = fixture("门框🧱" * 25)
        first, _ = add_sample(target, region, text=False)
        second, _ = add_sample(target, region, text=False)
        for sample, number in ((first, "01"), (second, "02")):
            for name in (sample.name, sample.data.name):
                self.assertLessEqual(len(name.encode("utf-8")), 63)
                self.assertIn("门框🧱", name)
                self.assertTrue(name.endswith("_Original_" + number), name)
        region["candidate_label"] = "Mirror X"
        mirrored, _ = add_sample(target, region, text=False)
        self.assertTrue(mirrored.name.endswith("_Mirror X_01"))

    def test_migration_repairs_legacy_references_and_aliases_without_changing_identity(self):
        target, region = fixture()
        samples, sources = [], []
        for _ in range(2):
            sample, source = add_sample(target, region)
            restore_legacy_names(sample)
            source["rr_surface_text_surface_object"] = sample.name
            source["rr_surface_text_surface_mesh"] = sample.data.name
            samples.append(sample)
            sources.append(source)
        del sources[0]["rr_surface_text_surface_ref"]
        identities = [(sample["rr_surface_identity"], sample["rr_surface_export_id"]) for sample in samples]
        aliases_before = [surface.surface_sampling_export_name(sample) for sample in samples]
        geometry_before = [geometry(sample) for sample in samples]
        aliases = surface.create_surface_text_sampling_export_aliases(target)
        result = surface.migrate_surface_sample_display_names(samples)
        self.assertEqual(len(result["objects"]), 2)
        self.assertEqual(len(result["meshes"]), 2)
        self.assertFalse(result["skipped"])
        self.assertEqual([geometry(sample) for sample in samples], geometry_before)
        self.assertEqual([(sample["rr_surface_identity"], sample["rr_surface_export_id"]) for sample in samples], identities)
        self.assertEqual([surface.surface_sampling_export_name(sample) for sample in samples], aliases_before)
        self.assertEqual({sample.name[-2:] for sample in samples}, {"01", "02"})
        for source, sample in zip(sources, samples):
            self.assertIs(source["rr_surface_text_surface_ref"], sample)
            self.assertEqual(source["rr_surface_text_surface_object"], sample.name)
            self.assertEqual(source["rr_surface_text_surface_mesh"], sample.data.name)
        for alias in aliases:
            owner = next(sample for sample in samples if surface.surface_sampling_export_name(sample) == alias.name)
            self.assertTrue(surface._is_stale_sampling_alias(alias, owner))
        descriptors = surface.build_surface_text_manifest(target)
        self.assertEqual({item["samplingSurfaceObjectName"] for item in descriptors}, {sample.name for sample in samples})
        self.assertEqual({item["samplingSurfaceExportObjectName"] for item in descriptors}, set(aliases_before))
        self.assertEqual(surface.migrate_surface_sample_display_names(samples), {"objects": [], "meshes": [], "skipped": []})

    def test_native_legacy_truncation_and_copy_suffixes_are_migrated(self):
        target, region = fixture("LongTarget" * 20)
        sample, _ = add_sample(target, region, text=False)
        restore_legacy_names(sample)
        copied = sample.copy()
        copied.data = sample.data.copy()
        bpy.context.scene.collection.objects.link(copied)
        self.assertNotEqual(copied.name, sample.name)
        before = [surface.surface_sampling_export_name(item) for item in (sample, copied)]
        result = surface.migrate_surface_sample_display_names([sample, copied])
        self.assertEqual(len(result["objects"]), 2, result)
        self.assertEqual(len(result["meshes"]), 2, result)
        self.assertEqual([surface.surface_sampling_export_name(item) for item in (sample, copied)], before)
        for item in (sample, copied):
            self.assertLessEqual(len(item.name.encode("utf-8")), 63)
            self.assertTrue(item.name.endswith(("_Original_01", "_Original_02")))

    def test_custom_names_unmanaged_objects_and_shared_mesh_names_are_preserved(self):
        target, region = fixture()
        custom, _ = add_sample(target, region, text=False)
        custom.name = "My chosen region"
        custom.data.name = "My chosen geometry"
        managed, _ = add_sample(target, region, text=False)
        restore_legacy_names(managed)
        unowned = bpy.data.objects.new("RR_SurfaceSample_UserMade_0123456789", None)
        bpy.context.scene.collection.objects.link(unowned)
        shared = managed.copy()
        bpy.context.scene.collection.objects.link(shared)
        old_mesh_name = managed.data.name
        result = surface.migrate_surface_sample_display_names([custom, managed, shared, unowned])
        self.assertEqual(custom.name, "My chosen region")
        self.assertEqual(custom.data.name, "My chosen geometry")
        self.assertEqual(unowned.name, "RR_SurfaceSample_UserMade_0123456789")
        self.assertEqual(managed.data.name, old_mesh_name)
        self.assertIs(managed.data, shared.data)
        self.assertEqual(len(result["objects"]), 2)
        self.assertFalse(result["meshes"])
        self.assertEqual({entry["reason"] for entry in result["skipped"]}, {"shared mesh"})


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceSampleNames))
    if result.wasSuccessful():
        print("RR_SURFACE_SAMPLE_NAMES_PASS")
    raise SystemExit(0 if result.wasSuccessful() else 1)
