"""Collider contracts recovered from the Unity Blender mirror into canonical tests.

The fixtures and 13 test methods retain the original run_exporter_contracts.py
assertions. Run with Blender --background --factory-startup --python-exit-code 1.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import bpy
from mathutils import Matrix, Vector


ADDONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "addons")
sys.path.insert(0, ADDONS_DIR)
import random_realm_builder_exporter as exporter

TEMP_IMAGE_PATHS = set()


def clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for curve in list(bpy.data.curves):
        bpy.data.curves.remove(curve)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for image in list(bpy.data.images):
        bpy.data.images.remove(image)
    for path in list(TEMP_IMAGE_PATHS):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        TEMP_IMAGE_PATHS.discard(path)


class ColliderAuthoringContractTests(unittest.TestCase):
    def setUp(self):
        clear_scene()

    def tearDown(self):
        clear_scene()

    def cube(self, name, matrix=None):
        bpy.ops.mesh.primitive_cube_add()
        obj = bpy.context.object
        obj.name = name
        if matrix is not None:
            obj.matrix_world = matrix
        bpy.context.view_layer.update()
        return obj

    def select_pair(self, collider, owner):
        bpy.ops.object.select_all(action="DESELECT")
        collider.select_set(True)
        owner.select_set(True)
        bpy.context.view_layer.objects.active = owner

    def geometry(self, obj):
        bpy.context.view_layer.update()
        return [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]

    def assert_geometry(self, obj, expected):
        actual = self.geometry(obj)
        self.assertEqual(len(actual), len(expected))
        for actual_point, expected_point in zip(actual, expected):
            self.assertLess((actual_point - expected_point).length, 0.00005)

    def test_create_box_rejects_name_matched_authored_collider_without_allocating_mesh(self):
        owner = self.cube("Stair_Authored")
        collider = self.cube("Stair_Authored_Collider")
        collider.data.vertices[6].co.z = 3.0
        before = self.geometry(collider)
        before_mesh = collider.data
        mesh_count = len(bpy.data.meshes)
        with self.assertRaisesRegex(RuntimeError, "already has collider"):
            exporter.create_or_update_bounding_box_collider(owner)
        self.assertIs(collider.data, before_mesh)
        self.assertEqual(len(bpy.data.meshes), mesh_count)
        self.assert_geometry(collider, before)

    def test_create_box_preserves_linked_custom_mesh_and_second_create_is_rejected(self):
        owner = self.cube("Stair_New", Matrix.Translation((3.0, -2.0, 1.0)))
        collider, created = exporter.create_or_update_bounding_box_collider(owner)
        self.assertTrue(created)
        self.assertIs(collider.parent, owner)
        before = self.geometry(collider)
        self.assertEqual(exporter.get_collision_meshes(owner), [collider])
        with self.assertRaisesRegex(RuntimeError, "already has collider"):
            exporter.create_or_update_bounding_box_collider(owner)
        self.assert_geometry(collider, before)
        self.assertIn("UNDO", exporter.RR_OT_create_bounding_box_collider.bl_options)
        self.assertIn("UNDO", exporter.RR_OT_use_selected_as_collider.bl_options)

    def test_association_preserves_shared_mesh_world_geometry_parent_scale_and_children(self):
        owner = self.cube("Stair_Owner", Matrix.Translation((8.0, -4.0, 2.0))
                          @ Matrix.Rotation(0.45, 4, "Z") @ Matrix.Diagonal((-2.0, 0.75, 1.5, 1.0)))
        old_parent = bpy.data.objects.new("OldParent", None)
        bpy.context.scene.collection.objects.link(old_parent)
        old_parent.matrix_world = Matrix.Translation((-7.0, 2.0, 4.0)) @ Matrix.Rotation(0.3, 4, "Y")
        collider = self.cube("HandmadeRamp", Matrix.Translation((1.0, 3.0, -2.0))
                             @ Matrix.Rotation(-0.4, 4, "Z") @ Matrix.Diagonal((-0.5, 2.0, 1.2, 1.0)))
        exporter.parent_object_keep_world(collider, old_parent)
        shared = bpy.data.objects.new("OtherSharedMesh", collider.data)
        bpy.context.scene.collection.objects.link(shared)
        child = bpy.data.objects.new("ChildMarker", None)
        bpy.context.scene.collection.objects.link(child)
        child.matrix_world = Matrix.Translation((2.0, 4.0, 6.0))
        exporter.parent_object_keep_world(child, collider)
        before, shared_before = self.geometry(collider), self.geometry(shared)
        child_before = child.matrix_world.copy()
        self.select_pair(collider, owner)
        actual_owner, actual_collider = exporter.associate_selected_collider(bpy.context)
        self.assertIs(actual_owner, owner)
        self.assertIs(actual_collider, collider)
        self.assertIs(collider.parent, owner)
        self.assertIsNot(collider.data, shared.data)
        self.assertEqual(collider["rr_collider_target"], owner.name)
        self.assertTrue(exporter.is_collision_helper_name(collider.name))
        self.assertEqual(exporter.get_collision_meshes(owner), [collider])
        self.assert_geometry(collider, before)
        self.assert_geometry(shared, shared_before)
        self.assertLess((child.matrix_world.translation - child_before.translation).length, 0.00005)
        self.assertEqual([collection.name for collection in collider.users_collection],
                         [exporter.COLLIDER_HELPER_COLLECTION_NAME])
        self.assertLess((collider.matrix_world.translation - owner.matrix_world.translation).length, 0.00001)
        exporter.associate_selected_collider(bpy.context)
        self.assert_geometry(collider, before)
        self.assertLess((child.matrix_world.translation - child_before.translation).length, 0.00005)

    def test_association_rejects_ambiguous_selection_wrong_owner_and_existing_collision(self):
        owner = self.cube("Stair_Owner")
        candidate = self.cube("AuthoredRamp")
        before = self.geometry(candidate)
        bpy.ops.object.select_all(action="DESELECT")
        candidate.select_set(True)
        with self.assertRaisesRegex(RuntimeError, "exactly two"):
            exporter.associate_selected_collider(bpy.context)
        self.select_pair(candidate, owner)
        candidate["rr_collider_target"] = "AnotherAsset"
        with self.assertRaisesRegex(RuntimeError, "already linked"):
            exporter.associate_selected_collider(bpy.context)
        del candidate["rr_collider_target"]
        existing = self.cube("Stair_Owner_Collider")
        self.select_pair(candidate, owner)
        with self.assertRaisesRegex(RuntimeError, "already has another collider"):
            exporter.associate_selected_collider(bpy.context)
        self.assert_geometry(candidate, before)
        self.assertNotIn("rr_collider_target", candidate)
        self.assertIsNone(candidate.parent)
        self.select_pair(owner, existing)
        with self.assertRaisesRegex(RuntimeError, "active object must be the asset owner"):
            exporter.associate_selected_collider(bpy.context)

    def test_association_rejects_shape_keys_and_zero_scale_before_mutation(self):
        owner = self.cube("Stair_Owner", Matrix.Translation((5.0, 0.0, 0.0)))
        candidate = self.cube("AuthoredRamp")
        candidate.shape_key_add(name="Basis")
        candidate.shape_key_add(name="Slope")
        self.select_pair(candidate, owner)
        before = self.geometry(candidate)
        with self.assertRaisesRegex(RuntimeError, "shape keys"):
            exporter.associate_selected_collider(bpy.context)
        self.assert_geometry(candidate, before)
        self.assertNotIn("rr_collider_target", candidate)
        candidate.shape_key_clear()
        candidate.scale.x = 0.0
        bpy.context.view_layer.update()
        with self.assertRaisesRegex(RuntimeError, "nonzero scale"):
            exporter.associate_selected_collider(bpy.context)
        self.assertIsNone(candidate.parent)

    def test_linked_data_is_rejected_before_any_origin_edit(self):
        linked = SimpleNamespace(type="MESH", data=SimpleNamespace(library=object()),
                                 library=None, is_editable=True, mode="OBJECT", name="LinkedCollider")
        with self.assertRaisesRegex(RuntimeError, "linked collider data"):
            exporter.validate_collider_origin_alignment(linked, object())

    def test_association_rejects_mesh_children_before_visual_membership_can_change(self):
        owner = self.cube("Stair_Owner")
        candidate = self.cube("HandmadeRamp")
        child = self.cube("ChildVisual")
        exporter.parent_object_keep_world(child, candidate)
        before = self.geometry(candidate)
        self.select_pair(candidate, owner)
        with self.assertRaisesRegex(RuntimeError, "without mesh children"):
            exporter.associate_selected_collider(bpy.context)
        self.assert_geometry(candidate, before)
        self.assertIsNone(candidate.parent)
        self.assertNotIn("rr_collider_target", candidate)

    def test_create_operator_does_not_fall_back_to_queue_for_unlinked_selected_collider(self):
        collider = self.cube("Stair_A_Collider")
        reports = []
        operator = SimpleNamespace(report=lambda level, message: reports.append(message))
        context = SimpleNamespace(scene=SimpleNamespace(rr_builder_export_settings=object()),
                                  object=collider, view_layer=bpy.context.view_layer)
        with mock.patch.object(exporter, "get_context_export_roots", side_effect=AssertionError("must not use queue fallback")):
            result = exporter.RR_OT_create_bounding_box_collider.execute(operator, context)
        self.assertEqual(result, {"CANCELLED"})
        self.assertIn("no linked owner", reports[0])

    def test_layout_origin_alignment_is_idempotent_and_restore_keeps_saved_geometry(self):
        owner = self.cube("Stair_Owner", Matrix.Translation((6.0, 3.0, 1.0)))
        collider = self.cube("Stair_Owner_Collider", Matrix.Translation((-2.0, 4.0, 3.0))
                             @ Matrix.Rotation(0.6, 4, "Y") @ Matrix.Diagonal((-1.0, 2.0, 0.75, 1.0)))
        exporter.parent_object_keep_world(collider, owner)
        collider["rr_collider_target"] = owner.name
        before = self.geometry(collider)
        self.assertTrue(exporter.sync_collider_origin_to_target(collider))
        self.assert_geometry(collider, before)
        self.assertFalse(exporter.sync_collider_origin_to_target(collider))
        exporter.snapshot_layout(None)
        owner.location += Vector((3.0, -4.0, 2.0))
        bpy.context.view_layer.update()
        exporter.restore_layout_snapshot()
        self.assert_geometry(collider, before)
        self.assertFalse(exporter.sync_collider_origin_to_target(collider))

    def test_layout_preflight_rejects_unsupported_collider_before_changing_others(self):
        owner = self.cube("Stair_Owner", Matrix.Translation((6.0, 0.0, 0.0)))
        valid = self.cube("First_Collider")
        invalid = self.cube("Second_Collider")
        valid["rr_collider_target"] = owner.name
        invalid["rr_collider_target"] = owner.name
        invalid.shape_key_add(name="Basis")
        before = self.geometry(valid)
        origin = valid.matrix_world.translation.copy()
        with self.assertRaisesRegex(RuntimeError, "shape keys"):
            exporter.snapshot_layout(None)
        self.assert_geometry(valid, before)
        self.assertEqual(valid.matrix_world.translation, origin)
        self.assertNotIn(exporter.LAYOUT_SNAPSHOT_MATRIX_PROP, owner)

    def test_legacy_unparented_collider_layout_restores_its_saved_transform(self):
        owner = self.cube("Stair_Legacy", Matrix.Translation((4.0, 3.0, 1.0)))
        collider = self.cube("Stair_Legacy_Collider", Matrix.Translation((4.0, 3.0, 1.0)))
        collider["rr_collider_target"] = owner.name
        before = self.geometry(collider)
        owner_before = self.geometry(owner)
        for _ in range(2):
            exporter.snapshot_layout(None)
            owner.location += Vector((4.0, 1.0, -2.0))
            collider.location += Vector((-2.0, 3.0, 5.0))
            collider.rotation_euler.z += 0.7
            bpy.context.view_layer.update()
            exporter.restore_layout_snapshot()
            self.assert_geometry(owner, owner_before)
            self.assert_geometry(collider, before)
            self.assertIsNone(collider.parent)

    def test_layout_saved_before_association_remains_valid_after_origin_rebase(self):
        owner = self.cube("Stair_Owner", Matrix.Translation((6.0, 3.0, 1.0))
                          @ Matrix.Rotation(0.25, 4, "Z") @ Matrix.Diagonal((-1.0, 2.0, 0.8, 1.0)))
        collider = self.cube("AuthoredRamp", Matrix.Translation((-2.0, 4.0, 3.0))
                             @ Matrix.Rotation(0.6, 4, "Y") @ Matrix.Diagonal((-0.5, 2.0, 0.75, 1.0)))
        before = self.geometry(collider)
        exporter.snapshot_layout(None)
        self.select_pair(collider, owner)
        exporter.associate_selected_collider(bpy.context)
        self.assert_geometry(collider, before)
        for _ in range(3):
            owner.location += Vector((3.0, -4.0, 2.0))
            bpy.context.view_layer.update()
            exporter.restore_layout_snapshot()
            self.assert_geometry(collider, before)

    def test_association_uses_assembly_root_and_rejects_empty_owner_with_only_candidate(self):
        owner = bpy.data.objects.new("Stair_Group", None)
        bpy.context.scene.collection.objects.link(owner)
        owner[exporter.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        owner[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "collider_test_group"
        visual = self.cube("Stair_Visual")
        exporter.parent_object_keep_world(visual, owner)
        visual[exporter.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = owner.name
        visual[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "collider_test_group"
        collider = self.cube("Ramp")
        self.select_pair(collider, visual)
        with self.assertRaisesRegex(RuntimeError, "export group root"):
            exporter.associate_selected_collider(bpy.context)
        self.select_pair(collider, owner)
        exporter.associate_selected_collider(bpy.context)
        self.assertEqual(exporter.get_collision_meshes(owner), [collider])
        self.assertNotIn(collider, exporter.get_export_asset_meshes(owner))
        empty = bpy.data.objects.new("EmptyOwner", None)
        bpy.context.scene.collection.objects.link(empty)
        candidate = self.cube("UnmarkedRamp")
        exporter.parent_object_keep_world(candidate, empty)
        self.select_pair(candidate, empty)
        with self.assertRaisesRegex(RuntimeError, "no visible export mesh"):
            exporter.associate_selected_collider(bpy.context)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ColliderAuthoringContractTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("RR_COLLIDER_AUTHORING_CONTRACTS_PASS")
    raise SystemExit(0 if result.wasSuccessful() else 1)

