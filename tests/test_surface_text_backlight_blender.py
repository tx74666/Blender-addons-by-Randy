"""Rear emission regressions; run serially in a small isolated Blender process.

blender --background --factory-startup --python-exit-code 1 --python THIS_FILE
No render, source project, or application settings are changed by these tests.
"""

from pathlib import Path
import sys
import unittest
from unittest import mock

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
from random_realm_builder_exporter import rr_surface_text as surface
from random_realm_builder_exporter import rr_surface_text_backlight as backlight


def clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.curves, bpy.data.meshes, bpy.data.materials):
        for data in list(collection):
            if data.users == 0:
                collection.remove(data)
    bpy.context.scene.unit_settings.scale_length = 1.0


def fixture(curved=False):
    mesh = bpy.data.meshes.new("Backlight Wall Mesh")
    if curved:
        vertices = [(x * 0.4, y, 0.02 * (x * 0.4) ** 2)
                    for y in (-3.0, 3.0) for x in range(-6, 7)]
        faces = [(index, index + 1, index + 14, index + 13) for index in range(12)]
    else:
        vertices = [(-4, -3, 0), (4, -3, 0), (4, 3, 0), (-4, 3, 0)]
        faces = [(0, 1, 2, 3)]
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    wall = bpy.data.objects.new("Backlight Wall", mesh)
    bpy.context.scene.collection.objects.link(wall)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        region = surface._selected_surface_info(wall, list(bm.faces))
        region["candidate_label"] = "Original"
    finally:
        bm.free()
    sample, _mesh = surface._create_surface_mesh(bpy.context, wall, region)
    font, _curve = surface._create_text_object(bpy.context, wall, region, 0.002, 10.0)
    font.data.body = "OX"
    font.data.size = 0.6
    front = bpy.data.materials.new("Artist Front Finish")
    front.use_nodes = True
    front.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (0.1, 0.35, 0.7, 1.0)
    font.data.materials.append(front)
    surface._annotate_text_object(bpy.context, font, wall, region, sample)
    bpy.context.view_layer.update()
    return wall, font, front


def evaluated_faces(font):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = font.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        matrix = evaluated.matrix_world.copy()
        normal_matrix = matrix.to_3x3().inverted().transposed()
        return [
            (polygon.material_index,
             (normal_matrix @ polygon.normal).normalized(),
             [matrix @ mesh.vertices[index].co for index in polygon.vertices])
            for polygon in mesh.polygons
        ]
    finally:
        evaluated.to_mesh_clear()


def native_state(font):
    shrinkwrap, solidify = surface._surface_text_modifier_pair(font)
    return ({field: getattr(solidify, field) for field in backlight._SOLIDIFY_RESTORE_FIELDS},
            shrinkwrap.offset, shrinkwrap.wrap_mode, font.matrix_world.copy())


class SurfaceTextBacklightTests(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.wall, self.font, self.front = fixture()

    def tearDown(self):
        clear_scene()

    def configure(self, **kwargs):
        return backlight.configure_backlight(bpy.context, self.font, **kwargs)

    def test_old_font_has_no_backlight_and_keeps_placement(self):
        state = native_state(self.font)
        descriptor = surface.build_surface_text_manifest(self.wall)[0]
        self.assertNotIn("backlight", descriptor)
        self.assertFalse(backlight.read_backlight_settings(self.font)["enabled"])
        self.assertEqual(native_state(self.font), state)

    def test_only_wall_facing_rear_faces_emit_and_front_and_rims_keep_artist_material(self):
        engine = bpy.context.scene.render.engine
        front_color = tuple(self.front.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value)
        settings = self.configure(back_gap_meters=0.05, thickness_meters=0.12,
                                  color=(0.2, 0.5, 0.9), strength=7.0)
        index = settings["backMaterialIndex"]
        rear_faces = [face for face in evaluated_faces(self.font) if face[0] == index]
        other_faces = [face for face in evaluated_faces(self.font) if face[0] != index]
        self.assertTrue(rear_faces)
        self.assertTrue(any(normal.z > 0.9 for _index, normal, _points in other_faces))
        self.assertTrue(any(abs(normal.z) < 0.1 for _index, normal, _points in other_faces))
        for _index, normal, points in rear_faces:
            self.assertLess(normal.z, -0.9)
            for point in points:
                self.assertAlmostEqual(point.z, 0.05, places=5)
        self.assertEqual({face[0] for face in other_faces}, {0})
        self.assertAlmostEqual(max(point.z for _index, _normal, points in other_faces for point in points), 0.17, places=5)
        self.assertIs(self.font.material_slots[0].material, self.front)
        self.assertEqual(tuple(self.front.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value), front_color)
        rear = self.font.material_slots[index].material
        self.assertIsNot(rear, self.front)
        self.assertAlmostEqual(rear.node_tree.nodes.get("Principled BSDF").inputs["Emission Strength"].default_value, 7.0)
        self.assertEqual(bpy.context.scene.render.engine, engine)

    def test_positive_negative_thickness_offsets_and_flip_normalize_and_restore(self):
        shrinkwrap, solidify = surface._surface_text_modifier_pair(self.font)
        for thickness, offset, flip in ((0.08, 1.0, True), (-0.08, 0.0, False),
                                        (0.08, -1.0, False), (-0.08, 0.7, True)):
            with self.subTest(thickness=thickness, offset=offset, flip=flip):
                solidify.thickness, solidify.offset, solidify.use_flip_normals = thickness, offset, flip
                shrinkwrap.offset = 0.013
                state = native_state(self.font)
                settings = self.configure(thickness_meters=0.08)
                for index, normal, points in evaluated_faces(self.font):
                    if index == settings["backMaterialIndex"]:
                        self.assertLess(normal.z, -0.9)
                        self.assertTrue(all(abs(point.z - 0.05) < 1e-5 for point in points))
                self.configure(enabled=False)
                self.assertEqual(native_state(self.font), state)
                self.assertEqual(list(self.font.data.materials), [self.front])

    def test_body_edits_keep_native_rear_material_and_disable_keeps_edited_text(self):
        before = native_state(self.font)
        settings = self.configure()
        self.font.data.body = "BUILDING 02"
        bpy.context.view_layer.update()
        self.assertTrue(any(face[0] == settings["backMaterialIndex"] for face in evaluated_faces(self.font)))
        self.configure(enabled=False, back_gap_meters=0.08, color=(0.7, 0.3, 0.1),
                       strength=9.0, halo_width_meters=0.13)
        self.assertEqual(self.font.data.body, "BUILDING 02")
        self.assertEqual(native_state(self.font), before)
        disabled = surface.build_surface_text_manifest(self.wall)[0]["backlight"]
        self.assertFalse(disabled["enabled"])
        self.assertAlmostEqual(disabled["backGapMeters"], 0.08)
        self.assertAlmostEqual(disabled["haloWidthMeters"], 0.13)
        self.assertAlmostEqual(disabled["strength"], 9.0)

    def test_shared_font_data_and_shared_rear_material_are_never_modified(self):
        other = self.font.copy()
        bpy.context.scene.collection.objects.link(other)
        shared_data = self.font.data
        self.configure()
        self.assertIsNot(self.font.data, shared_data)
        self.assertIs(other.data, shared_data)
        self.assertEqual(list(other.data.materials), [self.front])
        duplicate = self.font.copy()
        bpy.context.scene.collection.objects.link(duplicate)
        old_rear = duplicate.material_slots[-1].material
        old_strength = old_rear.node_tree.nodes.get("Principled BSDF").inputs["Emission Strength"].default_value
        self.configure(color=(0.1, 0.2, 0.3), strength=11.0)
        self.assertIsNot(self.font.data, duplicate.data)
        self.assertIs(duplicate.material_slots[-1].material, old_rear)
        self.assertEqual(old_rear.node_tree.nodes.get("Principled BSDF").inputs["Emission Strength"].default_value, old_strength)
        self.assertIsNot(self.font.material_slots[-1].material, old_rear)

    def test_object_linked_front_material_survives_private_curve_copy(self):
        other = self.font.copy()
        bpy.context.scene.collection.objects.link(other)
        override = bpy.data.materials.new("Artist Object Finish")
        self.font.material_slots[0].link = "OBJECT"
        self.font.material_slots[0].material = override
        self.configure()
        self.assertEqual(self.font.material_slots[0].link, "OBJECT")
        self.assertIs(self.font.material_slots[0].material, override)
        self.assertIs(other.material_slots[0].material, self.front)
        self.configure(enabled=False)
        self.assertEqual(self.font.material_slots[0].link, "OBJECT")
        self.assertIs(self.font.material_slots[0].material, override)

    def test_scene_units_and_text_depth_scale_keep_gap_and_thickness_in_metres(self):
        bpy.context.scene.unit_settings.scale_length = 0.01
        self.font.scale = (1.2, 0.9, 2.0)
        bpy.context.view_layer.update()
        settings = self.configure(back_gap_meters=0.05, thickness_meters=0.10)
        faces = evaluated_faces(self.font)
        rear = [point.z * 0.01 for index, _normal, points in faces if index == settings["backMaterialIndex"] for point in points]
        all_depths = [point.z * 0.01 for _index, _normal, points in faces for point in points]
        self.assertTrue(rear)
        self.assertTrue(all(abs(depth - 0.05) < 1e-5 for depth in rear))
        self.assertAlmostEqual(max(all_depths), 0.15, places=5)
        self.assertAlmostEqual(surface.build_surface_text_manifest(self.wall)[0]["surfaceOffsetMeters"], 0.05)

    def test_scaled_font_enable_disable_exports_actual_restored_world_offset(self):
        self.font.scale = (1.0, 1.0, 2.0)
        shrinkwrap, solidify = surface._surface_text_modifier_pair(self.font)
        shrinkwrap.offset = 0.013
        solidify.offset = 0.0
        bpy.context.view_layer.update()
        state = native_state(self.font)
        # Preserve the pre-opt-in descriptor contract for existing Fonts.
        self.assertAlmostEqual(surface.build_surface_text_manifest(self.wall)[0]["surfaceOffsetMeters"], 0.013)
        self.configure(back_gap_meters=0.05, thickness_meters=0.1)
        enabled = surface.build_surface_text_manifest(self.wall)[0]
        self.assertAlmostEqual(enabled["surfaceOffsetMeters"], 0.05)
        self.assertAlmostEqual(enabled["backlight"]["backGapMeters"], 0.05)
        self.configure(enabled=False)
        self.assertEqual(native_state(self.font), state)
        disabled = surface.build_surface_text_manifest(self.wall)[0]
        self.assertFalse(disabled["backlight"]["enabled"])
        self.assertAlmostEqual(disabled["surfaceOffsetMeters"], 0.026)
        depths = [point.z for _index, _normal, points in evaluated_faces(self.font) for point in points]
        self.assertAlmostEqual((min(depths) + max(depths)) * 0.5, disabled["surfaceOffsetMeters"], places=5)

    def test_manifest_exports_opt_in_contract_and_detects_later_native_gap_edits(self):
        requested = self.configure(color=(0.1, 0.4, 0.8), strength=6.0, halo_width_meters=0.09)
        descriptor = surface.build_surface_text_manifest(self.wall)[0]
        self.assertEqual(descriptor["backlight"], requested)
        self.assertAlmostEqual(descriptor["thicknessMeters"], 0.1)
        self.assertAlmostEqual(descriptor["solidFrontFraction"], 1.0)
        shrinkwrap, _solidify = surface._surface_text_modifier_pair(self.font)
        shrinkwrap.offset += 0.01
        with self.assertRaisesRegex(RuntimeError, "rear gap changed"):
            surface.build_surface_text_manifest(self.wall)

    def test_invalid_setting_rejection_leaves_authored_state_and_datablocks_unchanged(self):
        state = native_state(self.font)
        materials = set(bpy.data.materials.keys())
        curves = set(bpy.data.curves.keys())
        with self.assertRaises(ValueError):
            self.configure(back_gap_meters=float("nan"))
        self.assertEqual(native_state(self.font), state)
        self.assertEqual(set(bpy.data.materials.keys()), materials)
        self.assertEqual(set(bpy.data.curves.keys()), curves)

    def test_failed_configuration_restores_per_character_front_materials(self):
        second = bpy.data.materials.new("Second Artist Front Finish")
        self.font.data.materials.append(second)
        self.font.data.body_format[0].material_index = 0
        self.font.data.body_format[1].material_index = 1
        state = native_state(self.font)
        materials = set(bpy.data.materials.keys())
        with mock.patch.object(backlight, "validate_backlight_settings", side_effect=RuntimeError("forced validation failure")):
            with self.assertRaisesRegex(RuntimeError, "forced validation failure"):
                self.configure()
        self.assertEqual(list(self.font.data.materials), [self.front, second])
        self.assertEqual([character.material_index for character in self.font.data.body_format], [0, 1])
        self.assertEqual(native_state(self.font), state)
        self.assertEqual(set(bpy.data.materials.keys()), materials)
        self.assertFalse(backlight.read_backlight_settings(self.font)["enabled"])
        self.configure()
        self.configure(color=(0.3, 0.6, 0.9))
        self.assertEqual([character.material_index for character in self.font.data.body_format], [0, 1])
        self.configure(enabled=False)
        self.assertEqual(list(self.font.data.materials), [self.front, second])
        self.assertEqual([character.material_index for character in self.font.data.body_format], [0, 1])

    def test_curved_wall_rear_stays_the_projected_surface_independent_of_thickness(self):
        clear_scene()
        self.wall, self.font, self.front = fixture(curved=True)
        first = self.configure(back_gap_meters=0.05, thickness_meters=0.08)
        rear_before = [tuple(point) for index, _normal, points in evaluated_faces(self.font)
                       if index == first["backMaterialIndex"] for point in points]
        second = self.configure(back_gap_meters=0.05, thickness_meters=0.18)
        rear_after = [tuple(point) for index, _normal, points in evaluated_faces(self.font)
                      if index == second["backMaterialIndex"] for point in points]
        self.assertEqual(len(rear_before), len(rear_after))
        for first_point, second_point in zip(rear_before, rear_after):
            self.assertLess((Vector(first_point) - Vector(second_point)).length, 1e-6)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceTextBacklightTests)
    print("SURFACE_TEXT_BACKLIGHT_TEST_COUNT=" + str(suite.countTestCases()))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
