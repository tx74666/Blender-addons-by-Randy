"""Append-only Surface Text topology regressions in an isolated Blender.

Run with --background --factory-startup --python and this file. Fixtures are
created in memory; no authored project is opened or saved.
"""

import os
from pathlib import Path
import sys
import tempfile
import unittest

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
sys.path.insert(0, str(ROOT / "tests"))
import random_realm_builder_exporter as exporter
from random_realm_builder_exporter import rr_surface_text as surface
from test_surface_text_editable_contract_blender import clear_scene, make_fixture


def freeze(value):
    if isinstance(value, bpy.types.ID):
        return (value.bl_rna.identifier, value.as_pointer(), value.name_full)
    if hasattr(value, "items"):
        return tuple(sorted((key, freeze(item)) for key, item in value.items()))
    if hasattr(value, "to_list"):
        return freeze(value.to_list())
    if isinstance(value, (tuple, list)):
        return tuple(freeze(item) for item in value)
    return value


def mesh_state(mesh):
    return (
        mesh.as_pointer(), mesh.name, freeze(dict(mesh.items())),
        tuple(tuple(vertex.co) for vertex in mesh.vertices),
        tuple((tuple(edge.vertices), edge.select, edge.hide) for edge in mesh.edges),
        tuple((tuple(face.vertices), face.material_index, face.use_smooth,
               face.select, face.hide) for face in mesh.polygons),
        tuple((attribute.name, attribute.domain, attribute.data_type, len(attribute.data))
              for attribute in mesh.attributes),
        tuple(material.as_pointer() if material else None for material in mesh.materials),
    )


def authored_state():
    """Detect scene writes, leaked evaluation clones and changed native bindings."""
    return (
        tuple(sorted((
            obj.name, obj.as_pointer(), obj.data.as_pointer() if obj.data else None,
            obj.parent.as_pointer() if obj.parent else None,
            tuple(tuple(row) for row in obj.matrix_world),
            obj.hide_get(), obj.hide_viewport, obj.hide_render, obj.hide_select,
            obj.select_get(), obj.mode, freeze(dict(obj.items())),
            tuple((modifier.name, modifier.type, modifier.show_viewport, modifier.show_render,
                   getattr(modifier, "target", None).as_pointer()
                   if getattr(modifier, "target", None) else None,
                   getattr(modifier, "thickness", None), getattr(modifier, "offset", None))
                  for modifier in obj.modifiers),
            tuple(sorted(collection.name for collection in obj.users_collection)),
        ) for obj in bpy.data.objects)),
        tuple(sorted(mesh_state(mesh) for mesh in bpy.data.meshes)),
        tuple(sorted((curve.name, curve.as_pointer(), curve.body, curve.size,
                      tuple(material.as_pointer() if material else None
                            for material in curve.materials))
                     for curve in bpy.data.curves if isinstance(curve, bpy.types.TextCurve))),
        bpy.context.view_layer.objects.active.as_pointer()
        if bpy.context.view_layer.objects.active else None,
    )


class SurfaceTextAppendedTopologyTests(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.root, self.text, self.sample = make_fixture()
        bm = bmesh.new()
        try:
            bm.from_mesh(self.root.data)
            bm.faces.ensure_lookup_table()
            bm.normal_update()
            region = surface._selected_surface_info(
                self.root, [bm.faces[index] for index in (4, 5, 6)])
        finally:
            bm.free()
        self.sample = surface.bind_existing_surface_text(
            bpy.context, self.text, self.root, region)
        self.original_polygons = tuple(tuple(face.vertices) for face in self.root.data.polygons)
        self.original_count = len(self.original_polygons)
        self.expected_topology = self.sample["rr_surface_source_topology"]
        bpy.context.view_layer.update()

    def tearDown(self):
        clear_scene()

    def append_roof(self):
        """Exercise the BMesh duplicate/translate operations behind Shift+D."""
        bm = bmesh.new()
        try:
            bm.from_mesh(self.root.data)
            duplicated = bmesh.ops.duplicate(bm, geom=list(bm.faces))
            roof_vertices = [element for element in duplicated["geom"]
                             if isinstance(element, bmesh.types.BMVert)]
            self.assertTrue(roof_vertices)
            bmesh.ops.translate(bm, verts=roof_vertices, vec=Vector((0.0, 0.0, 4.0)))
            bm.to_mesh(self.root.data)
        finally:
            bm.free()
        self.root.data.update()
        bpy.context.view_layer.update()
        self.assertEqual(len(self.root.data.polygons), self.original_count * 2)
        self.assertEqual(tuple(tuple(face.vertices) for face in self.root.data.polygons)
                         [:self.original_count], self.original_polygons)
        self.assertNotEqual(surface._source_topology_fingerprint(self.root.data),
                            self.expected_topology)

    def rewrite_faces(self, faces):
        vertices = [tuple(vertex.co) for vertex in self.root.data.vertices]
        self.root.data.clear_geometry()
        self.root.data.from_pydata(vertices, [], faces)
        self.root.data.update()
        bpy.context.view_layer.update()

    def assert_sampling_rejected_without_writes(self, message="topology|indices|sampling region"):
        before = authored_state()
        with self.assertRaisesRegex(RuntimeError, message):
            with surface._evaluated_sampling_mesh(self.text):
                self.fail("Changed original topology was accepted.")
        self.assertEqual(authored_state(), before)

    def read_sampling_without_writes(self):
        before = authored_state()
        with surface._evaluated_sampling_mesh(self.text) as (vertices, faces):
            result = tuple(tuple(point) for point in vertices), tuple(tuple(face) for face in faces)
        self.assertEqual(authored_state(), before)
        self.assertEqual(self.sample["rr_surface_source_topology"], self.expected_topology)
        return result

    def test_unchanged_full_topology_remains_valid_without_scene_writes(self):
        self.assertEqual(surface._source_topology_fingerprint(self.root.data),
                         self.expected_topology)
        _vertices, faces = self.read_sampling_without_writes()
        self.assertEqual(len(faces), 3)

    def test_shift_d_roof_accepts_original_prefix_and_samples_only_bound_faces(self):
        expected_vertices, expected_faces = self.read_sampling_without_writes()
        self.append_roof()
        vertices, faces = self.read_sampling_without_writes()
        self.assertEqual(faces, expected_faces)
        self.assertEqual(vertices, expected_vertices)
        self.assertEqual(len(faces), 3)
        self.assertLess(max(point[2] for point in vertices), 0.5)
        self.assertGreater(max(vertex.co.z for vertex in self.root.data.vertices), 4.0)

    def test_changes_to_only_appended_roof_do_not_invalidate_original_binding(self):
        expected = self.read_sampling_without_writes()
        self.append_roof()
        faces = [tuple(face.vertices) for face in self.root.data.polygons]
        faces[-1] = tuple(reversed(faces[-1]))
        self.rewrite_faces(faces)
        self.assertEqual(self.read_sampling_without_writes(), expected)

    def test_original_selected_face_connectivity_change_is_rejected(self):
        self.append_roof()
        faces = [tuple(face.vertices) for face in self.root.data.polygons]
        faces[4] = faces[4][:-1]
        self.rewrite_faces(faces)
        self.assert_sampling_rejected_without_writes()

    def test_original_unselected_face_connectivity_change_is_rejected(self):
        self.append_roof()
        faces = [tuple(face.vertices) for face in self.root.data.polygons]
        faces[0] = faces[0][:-1]
        self.rewrite_faces(faces)
        self.assert_sampling_rejected_without_writes()

    def test_original_face_winding_change_is_rejected(self):
        self.append_roof()
        faces = [tuple(face.vertices) for face in self.root.data.polygons]
        faces[4] = tuple(reversed(faces[4]))
        self.rewrite_faces(faces)
        self.assert_sampling_rejected_without_writes()

    def test_original_unselected_face_reordering_is_rejected(self):
        self.append_roof()
        faces = [tuple(face.vertices) for face in self.root.data.polygons]
        faces[0], faces[1] = faces[1], faces[0]
        self.rewrite_faces(faces)
        self.assert_sampling_rejected_without_writes()

    def test_original_face_deletion_is_rejected(self):
        self.append_roof()
        faces = [tuple(face.vertices) for face in self.root.data.polygons]
        del faces[0]
        self.rewrite_faces(faces)
        self.assert_sampling_rejected_without_writes()

    def test_invalid_or_missing_topology_metadata_is_rejected(self):
        self.append_roof()
        for expected in ("", "old-topology", "0" * 64):
            with self.subTest(expected=expected):
                self.sample["rr_surface_source_topology"] = expected
                self.assert_sampling_rejected_without_writes()
        del self.sample["rr_surface_source_topology"]
        self.assert_sampling_rejected_without_writes()

    def test_binding_cannot_claim_a_face_added_after_its_saved_prefix(self):
        self.append_roof()
        self.sample["rr_surface_source_face_indices"] = [4, 5, 6, self.original_count]
        self.assert_sampling_rejected_without_writes()

    def test_negative_and_out_of_range_bound_indices_are_rejected(self):
        for indices in ((-1, 4, 5), (4, 5, self.original_count)):
            with self.subTest(indices=indices):
                self.sample["rr_surface_source_face_indices"] = list(indices)
                self.assert_sampling_rejected_without_writes()

    def test_shift_d_roof_survives_fbx_and_does_not_enter_sampling_alias(self):
        self.append_roof()
        descriptor = surface.build_surface_text_manifest(self.root)[0]
        vertices, _faces = self.read_sampling_without_writes()
        expected_world = [self.sample.matrix_world @ Vector(point) for point in vertices]
        root_inverse = self.root.matrix_world.inverted()
        root_name = self.root.name
        before = authored_state()
        with tempfile.TemporaryDirectory(prefix="rr_surface_appended_roof_") as directory:
            path = os.path.join(directory, "appended_roof.fbx")
            exporter.export_fbx(self.root, path)
            self.assertEqual(authored_state(), before)
            clear_scene()
            bpy.ops.import_scene.fbx(filepath=path)
            imported_root = bpy.data.objects.get(root_name)
            imported_sample = bpy.data.objects.get(descriptor["samplingSurfaceExportObjectName"])
            self.assertIsNotNone(imported_root)
            self.assertIsNotNone(imported_sample)
            imported_world = [imported_sample.matrix_world @ vertex.co
                              for vertex in imported_sample.data.vertices]
            self.assertTrue(imported_world)
            for point in expected_world:
                self.assertLess(min((point - candidate).length for candidate in imported_world), 1e-4)
            for point in imported_world:
                self.assertLess(min((point - candidate).length for candidate in expected_world), 1e-4)
            self.assertLess(max((root_inverse @ point).z for point in imported_world), 0.5)
            root_vertices = [root_inverse @ imported_root.matrix_world @ vertex.co
                             for vertex in imported_root.data.vertices]
            self.assertGreater(max(point.z for point in root_vertices), 3.9)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceTextAppendedTopologyTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
