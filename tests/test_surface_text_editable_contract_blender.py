"""Focused Surface Text contract checks. Run only in an isolated Blender process.

-- --fixture-dir ABSOLUTE_DIR writes a small curved, rotated, emissive fixture
instead of running tests. It never opens or overwrites an authored blend.
"""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
import random_realm_builder_exporter as exporter
from random_realm_builder_exporter import rr_surface_text as surface


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


def read_all_faces(target):
    bm = bmesh.new()
    try:
        bm.from_mesh(target.data)
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        region = surface._selected_surface_info(target, list(bm.faces))
        region["candidate_label"] = "Original"
        return region
    finally:
        bm.free()


def make_fixture():
    mesh = bpy.data.meshes.new("CurvedSignSource")
    vertices = [(x * 0.5, y, 0.04 * (x * 0.5) ** 2) for y in (-1.0, 1.0) for x in range(-6, 7)]
    faces = [(index, index + 1, index + 14, index + 13) for index in range(12)]
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    target = bpy.data.objects.new("SurfaceText_Rotated_Curved_Emissive_Fixture", mesh)
    bpy.context.scene.collection.objects.link(target)
    target.location = (4.0, -3.0, 2.0)
    target.rotation_euler = (0.2, -0.35, 0.55)
    target.scale = (1.1, 0.9, 1.2)
    bpy.context.view_layer.update()
    region = read_all_faces(target)
    sample, _ = surface._create_surface_mesh(bpy.context, target, region)
    text, _ = surface._create_text_object(bpy.context, target, region, 0.002, 10.0)
    text.name = "Authored Editable ENTRY"
    text.data.body = "ENTRY"
    text.data.size = 0.45
    # Authored offset is deliberate: importing must not re-center it automatically.
    text.location += text.matrix_world.to_3x3().col[0].normalized() * 0.12
    material = bpy.data.materials.new("SurfaceText_Emissive_OnlyOnFont")
    material.use_nodes = True
    principled = material.node_tree.nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = (0.12, 0.35, 0.8, 1.0)
    principled.inputs["Emission Color"].default_value = (0.1, 0.6, 1.0, 1.0)
    principled.inputs["Emission Strength"].default_value = 2.5
    text.data.materials.append(material)
    surface._annotate_text_object(bpy.context, text, target, region, sample)
    bpy.context.view_layer.update()
    return target, text, sample


def write_fixture(directory, root=None):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if root is None:
        clear_scene()
        root, _text, _sample = make_fixture()
    model = directory / "model.fbx"
    if model.exists() or (directory / "manifest.json").exists():
        raise RuntimeError("Choose a fresh fixture directory; existing artifacts are not overwritten.")
    contracts = exporter.build_material_surface_contracts(root)
    warnings = exporter.export_fbx(root, str(model))
    maps, material_warnings = exporter.build_material_map_manifest(root, str(directory), contracts)
    snapshot = exporter.write_surface_text_source_snapshot(root, str(directory / "surface_text_source.rrblend"))
    exporter.write_manifest(root, str(directory / "manifest.json"), root.name, "Prop", "Props", "Default",
                            "model.fbx", "", ["model"], warnings + material_warnings, maps,
                            uv_export_contract=exporter.current_uv_export_contract(str(model)),
                            source_blend_override=snapshot)
    return directory


class SurfaceTextEditableContractTests(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.root, self.text, self.sample = make_fixture()

    def tearDown(self):
        clear_scene()

    def descriptor(self):
        return surface.build_surface_text_manifest(self.root)[0]

    def test_author_pose_and_text_survive_descriptor_with_stable_identity(self):
        pose = self.text.matrix_world.copy()
        self.text.data.align_y = "TOP_BASELINE"
        first = self.descriptor()
        self.text.name = "Renamed Font"
        second = self.descriptor()
        self.assertEqual(first["editableVersion"], 1)
        self.assertEqual(first["textId"], second["textId"])
        self.assertEqual(first["frameObjectNames"], second["frameObjectNames"])
        self.assertEqual(first["text"], "ENTRY")
        self.assertEqual(first["verticalAlignment"], "TOP_BASELINE")
        self.assertEqual(first["coordinateSpace"], "sampling-local-blender")
        self.assertLess((Vector(first["textCenter"]) - self.sample.matrix_world.inverted() @ pose.translation).length, 1e-5)
        self.assertEqual(self.text.matrix_world, pose)

    def test_current_solidify_overrides_stale_authored_thickness(self):
        self.text.modifiers["Solidify"].thickness = -0.23
        self.text["rr_surface_text_thickness_meters"] = 0.1
        self.assertAlmostEqual(self.descriptor()["thicknessMeters"], 0.23, places=5)

    def test_solid_front_fraction_matches_evaluated_signed_thickness_and_offset(self):
        # A planar Font isolates Solidify's split from curved Shrinkwrap normals.
        self.text.modifiers["Shrinkwrap"].show_viewport = False
        solidify = self.text.modifiers["Solidify"]
        cases = ((-0.1, -1.0, 1.0), (-0.1, 0.0, 0.5), (-0.1, 1.0, 0.0),
                 (0.1, -1.0, 0.0), (0.1, 0.0, 0.5), (0.1, 1.0, 1.0))
        for thickness, offset, expected_fraction in cases:
            for flip_normals in (False, True):
                with self.subTest(thickness=thickness, offset=offset, flip_normals=flip_normals):
                    solidify.thickness = thickness
                    solidify.offset = offset
                    solidify.use_flip_normals = flip_normals
                    bpy.context.view_layer.update()
                    descriptor = self.descriptor()
                    self.assertAlmostEqual(descriptor["solidFrontFraction"], expected_fraction)
                    evaluated = self.text.evaluated_get(bpy.context.evaluated_depsgraph_get())
                    try:
                        mesh = evaluated.to_mesh()
                        self.assertGreater(len(mesh.vertices), 0)
                        front = abs(thickness) * descriptor["solidFrontFraction"]
                        self.assertAlmostEqual(max(vertex.co.z for vertex in mesh.vertices), front, places=5)
                        self.assertAlmostEqual(min(vertex.co.z for vertex in mesh.vertices),
                                               front - abs(thickness), places=5)
                    finally:
                        evaluated.to_mesh_clear()

    def test_scene_units_are_explicit_and_thickness_is_in_meters(self):
        bpy.context.scene.unit_settings.scale_length = 0.01
        self.text.modifiers["Solidify"].thickness = -12.0
        descriptor = self.descriptor()
        self.assertAlmostEqual(descriptor["sceneUnitMeters"], 0.01)
        self.assertAlmostEqual(descriptor["thicknessMeters"], 0.12)

    def test_font_only_material_and_emission_are_in_contract(self):
        contracts = exporter.build_material_surface_contracts(self.root)
        contract = contracts[self.text.active_material.name]
        self.assertEqual(contract["contractVersion"], 1)
        self.assertAlmostEqual(contract["emissionStrength"], 2.5)
        with tempfile.TemporaryDirectory() as directory:
            maps, warnings = exporter.build_material_map_manifest(self.root, directory, contracts)
        self.assertEqual(maps[0]["material"], self.text.active_material.name)
        self.assertFalse(warnings)

    def test_linked_emission_is_reported_not_faked_as_a_constant(self):
        material = self.text.active_material
        nodes = material.node_tree.nodes
        value = nodes.new("ShaderNodeValue")
        material.node_tree.links.new(value.outputs[0], nodes.get("Principled BSDF").inputs["Emission Strength"])
        contract = exporter.build_material_surface_contract(material)
        self.assertNotIn("emissionStrength", contract)
        with tempfile.TemporaryDirectory() as directory:
            _maps, warnings = exporter.build_material_map_manifest(self.root, directory)
        self.assertTrue(any("linked emission" in warning for warning in warnings))

    def test_evaluated_deformation_updates_sampling_without_mutating_authored_mesh(self):
        before = [vertex.co.copy() for vertex in self.sample.data.vertices]
        modifier = self.root.modifiers.new("MoveEvaluatedSurface", "DISPLACE")
        modifier.strength = 0.4
        modifier.mid_level = 0.0
        with surface._evaluated_sampling_mesh(self.text) as (points, _faces):
            self.assertGreater(max(point[2] for point in points), max(point.z for point in before) + 0.1)
        self.assertEqual([vertex.co.copy() for vertex in self.sample.data.vertices], before)

    def test_subdivision_keeps_face_region_mapping(self):
        bm = bmesh.new()
        try:
            bm.from_mesh(self.root.data)
            bm.faces.ensure_lookup_table()
            bm.normal_update()
            region = surface._selected_surface_info(self.root, [bm.faces[index] for index in (4, 5, 6)])
        finally:
            bm.free()
        surface.bind_existing_surface_text(bpy.context, self.text, self.root, region)
        modifier = self.root.modifiers.new("SurfaceSubdivision", "SUBSURF")
        modifier.subdivision_type = "SIMPLE"
        modifier.levels = modifier.render_levels = 1
        with surface._evaluated_sampling_mesh(self.text) as (_points, faces):
            self.assertEqual(len(faces), 12, "Only the three selected source faces may subdivide into this region.")

    def test_snapshot_preserves_nondefault_scene_units(self):
        bpy.context.scene.unit_settings.scale_length = 0.01
        bpy.context.scene.unit_settings.system = "METRIC"
        with tempfile.TemporaryDirectory() as directory:
            snapshot = exporter.write_surface_text_source_snapshot(self.root, os.path.join(directory, "source.rrblend"))
            with bpy.data.libraries.load(snapshot, link=False) as (data_from, data_to):
                data_to.scenes = list(data_from.scenes)
            loaded = list(data_to.scenes)
            try:
                self.assertEqual(len(loaded), 1)
                self.assertAlmostEqual(loaded[0].unit_settings.scale_length, 0.01)
                self.assertEqual(loaded[0].unit_settings.system, "METRIC")
            finally:
                for scene in loaded:
                    bpy.data.scenes.remove(scene)

    def test_unproven_modifier_rejects_without_changing_sample(self):
        before = self.sample.data.as_pointer()
        self.root.modifiers.new("UnverifiedBevel", "BEVEL")
        with self.assertRaisesRegex(RuntimeError, "cannot verify"):
            self.descriptor()
        self.assertEqual(self.sample.data.as_pointer(), before)

    def test_topology_change_requires_explicit_rebind(self):
        self.sample["rr_surface_source_topology"] = "old-topology"
        with self.assertRaisesRegex(RuntimeError, "topology"):
            self.descriptor()

    def test_legacy_binding_is_not_misreported_as_editable(self):
        del self.sample["rr_surface_source_topology"]
        descriptor = self.descriptor()
        self.assertEqual(descriptor["editableVersion"], 0)
        self.assertIn("Rebind", descriptor["editableError"])

    def test_explicit_rebind_preserves_body_pose_material_and_modifier_settings(self):
        pose = self.text.matrix_world.copy()
        body = self.text.data.body
        material = self.text.active_material
        self.text.modifiers["Solidify"].thickness = -0.17
        sample = surface.bind_existing_surface_text(bpy.context, self.text, self.root, read_all_faces(self.root))
        self.assertEqual(self.text.matrix_world, pose)
        self.assertEqual(self.text.data.body, body)
        self.assertEqual(self.text.active_material, material)
        self.assertAlmostEqual(self.text.modifiers["Solidify"].thickness, -0.17)
        self.assertIs(self.text["rr_surface_text_surface_ref"], sample)

    def test_fbx_marker_roundtrip_and_transient_cleanup(self):
        descriptor = self.descriptor()
        authored_origin = self.text.matrix_world.translation.copy()
        with surface._evaluated_sampling_mesh(self.text) as (vertices, _faces):
            expected_world = [self.sample.matrix_world @ Vector(point) for point in vertices]
        before = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "fixture.fbx")
            exporter.export_fbx(self.root, path)
            self.assertEqual((set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())), before)
            clear_scene()
            bpy.ops.import_scene.fbx(filepath=path)
            sample = bpy.data.objects.get(descriptor["samplingSurfaceExportObjectName"])
            self.assertIsNotNone(sample)
            self.assertGreater(sample.matrix_world.to_3x3().determinant(), 0.0)
            imported_scale = sample.matrix_world.to_scale()
            self.assertGreater(min(imported_scale), 0.0)
            for value in imported_scale:
                self.assertAlmostEqual(value / imported_scale.x, 1.0, places=4)
            imported_world = [sample.matrix_world @ vertex.co for vertex in sample.data.vertices]
            for point in expected_world:
                self.assertLess(min((point - candidate).length for candidate in imported_world), 1e-4)
            markers = [bpy.data.objects.get(name) for name in descriptor["frameObjectNames"]]
            self.assertTrue(all(marker is not None for marker in markers))
            points = [sample.matrix_world.inverted() @ marker.matrix_world.translation for marker in markers]
            basis = Matrix(tuple(points[index] - points[0] for index in (1, 2, 3))).transposed()
            self.assertGreater(abs(basis.determinant()), 1e-6)
            read = (basis @ Vector(descriptor["readDirection"])).normalized()
            up = (basis @ Vector(descriptor["upDirection"])).normalized()
            normal = (basis.inverted().transposed() @ Vector(descriptor["surfaceNormal"])).normalized()
            self.assertAlmostEqual(read.dot(up), 0.0, places=5)
            self.assertAlmostEqual(read.dot(normal), 0.0, places=5)
            self.assertAlmostEqual(up.dot(normal), 0.0, places=5)
            imported_origin = sample.matrix_world @ (points[0] + basis @ Vector(descriptor["textCenter"]))
            self.assertLess((imported_origin - authored_origin).length, 1e-4)

    def test_sampling_alias_bakes_parent_shear_and_reflection_without_source_changes(self):
        parent = bpy.data.objects.new("ShearedSamplingParent", None)
        bpy.context.scene.collection.objects.link(parent)
        parent.rotation_euler = (0.1, 0.2, -0.3)
        self.root.parent = parent
        self.root.matrix_parent_inverse = Matrix.Identity(4)
        for reflected in (False, True):
            with self.subTest(reflected=reflected):
                parent.scale = (-1.7 if reflected else 1.7, 0.6, 1.3)
                bpy.context.view_layer.update()
                sample_pose = self.sample.matrix_world.copy()
                text_pose = self.text.matrix_world.copy()
                original_vertices = [vertex.co.copy() for vertex in self.sample.data.vertices]
                descriptor = self.descriptor()
                with surface._evaluated_sampling_mesh(self.text) as (vertices, faces):
                    expected_world = [sample_pose @ Vector(point) for point in vertices]
                    a, b, c = (Vector(vertices[index]) for index in faces[0][:3])
                    local_normal = (b - a).cross(c - a).normalized()
                    expected_normal = (sample_pose.to_3x3().inverted().transposed() @ local_normal).normalized()
                before = set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())
                aliases, markers = [], []
                try:
                    aliases = surface.create_surface_text_sampling_export_aliases(self.root)
                    markers = surface.create_surface_text_frame_export_aliases(self.root)
                    bpy.context.view_layer.update()
                    alias = aliases[0]
                    self.assertIsNone(alias.parent)
                    self.assertLess((alias.matrix_world.to_scale() - Vector((1, 1, 1))).length, 1e-6)
                    self.assertGreater(alias.matrix_world.to_3x3().determinant(), 0.0)
                    for vertex, expected in zip(alias.data.vertices, expected_world):
                        self.assertLess((alias.matrix_world @ vertex.co - expected).length, 1e-5)
                    self.assertGreater(alias.data.polygons[0].normal.dot(expected_normal), 0.9999)
                    points = [alias.matrix_world.inverted() @ marker.matrix_world.translation for marker in markers]
                    basis = Matrix(tuple(points[index] - points[0] for index in (1, 2, 3))).transposed()
                    read = (basis @ Vector(descriptor["readDirection"])).normalized()
                    up = (basis @ Vector(descriptor["upDirection"])).normalized()
                    normal = (basis.inverted().transposed() @ Vector(descriptor["surfaceNormal"])).normalized()
                    self.assertAlmostEqual(read.dot(up), 0.0, places=5)
                    self.assertAlmostEqual(read.dot(normal), 0.0, places=5)
                    self.assertAlmostEqual(up.dot(normal), 0.0, places=5)
                finally:
                    for marker in markers:
                        bpy.data.objects.remove(marker, do_unlink=True)
                    for alias in aliases:
                        mesh = alias.data
                        bpy.data.objects.remove(alias, do_unlink=True)
                        bpy.data.meshes.remove(mesh)
                self.assertEqual((set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())), before)
                self.assertEqual(self.sample.matrix_world, sample_pose)
                self.assertEqual(self.text.matrix_world, text_pose)
                self.assertEqual([vertex.co.copy() for vertex in self.sample.data.vertices], original_vertices)


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if "--fixture-dir" in args:
        print("SURFACE_TEXT_FIXTURE=" + str(write_fixture(args[args.index("--fixture-dir") + 1])))
    else:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceTextEditableContractTests))
        if not result.wasSuccessful():
            raise SystemExit(1)
