"""Mirror-seam Surface Text regressions; run in a fresh, isolated Blender.

Example:
    blender --background --factory-startup --python tests/test_surface_text_mirror_join_blender.py

Fixtures are generated from scratch. No authored project is opened or saved.
"""

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
    for collection in (bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.node_groups):
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)
    bpy.context.scene.unit_settings.scale_length = 1.0


def select_region(target, indices=None):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.context.scene.tool_settings.mesh_select_mode = (False, False, True)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(target.data)
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vertex in bm.verts:
        vertex.select_set(False)
    chosen = set(range(len(bm.faces))) if indices is None else set(indices)
    for face in bm.faces:
        face.select_set(face.index in chosen)
    bmesh.update_edit_mesh(target.data)


def make_target(xs=(0.0, 2.0, 4.0), merge=True, threshold=0.001,
                target_matrix=None, mirror_matrix=None, depth=None,
                selected_indices=None):
    target_matrix = target_matrix.copy() if target_matrix is not None else Matrix.Identity(4)
    plane_matrix = mirror_matrix.copy() if mirror_matrix is not None else target_matrix
    source_from_plane = target_matrix.inverted() @ plane_matrix
    depth = depth or (lambda _x: 0.0)
    vertices = [tuple(source_from_plane @ Vector((x, depth(x), z)))
                for z in (-2.0, 2.0) for x in xs]
    width = len(xs)
    faces = [(index, index + 1, index + 1 + width, index + width)
             for index in range(width - 1)]
    mesh = bpy.data.meshes.new("MirrorSignSourceMesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    target = bpy.data.objects.new("MirrorSignSource", mesh)
    bpy.context.scene.collection.objects.link(target)
    target.matrix_world = target_matrix
    mirror = target.modifiers.new("Authored Mirror", "MIRROR")
    mirror.use_axis = (True, False, False)
    mirror.use_mirror_merge = merge
    mirror.merge_threshold = threshold
    mirror.use_clip = True
    mirror.show_in_editmode = True
    mirror.show_on_cage = True
    if mirror_matrix is not None:
        plane = bpy.data.objects.new("Authored Mirror Plane", None)
        bpy.context.scene.collection.objects.link(plane)
        plane.matrix_world = mirror_matrix
        mirror.mirror_object = plane
    bpy.context.view_layer.update()
    select_region(target, selected_indices)
    return target, mirror, plane_matrix


def source_signature(target):
    return (
        target.data.as_pointer(),
        tuple(tuple(vertex.co) for vertex in target.data.vertices),
        tuple(tuple(face.vertices) for face in target.data.polygons),
        tuple(tuple(row) for row in target.matrix_world),
        tuple((modifier.name, modifier.type, tuple(modifier.use_axis),
               tuple(modifier.use_bisect_axis), tuple(modifier.use_bisect_flip_axis),
               modifier.use_mirror_merge, modifier.merge_threshold, modifier.use_clip,
               modifier.show_viewport, modifier.show_render,
               modifier.show_in_editmode, modifier.show_on_cage,
               modifier.mirror_object.as_pointer() if modifier.mirror_object else None)
              for modifier in target.modifiers),
    )


def datablocks():
    return set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())


def mesh_components(mesh):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        pending_faces = set(bm.faces)
        components = 0
        while pending_faces:
            components += 1
            pending = [pending_faces.pop()]
            while pending:
                face = pending.pop()
                for edge in face.edges:
                    for neighbor in edge.link_faces:
                        if neighbor in pending_faces:
                            pending_faces.remove(neighbor)
                            pending.append(neighbor)
        return components
    finally:
        bm.free()


def sample_world_points(sample):
    return [sample.matrix_world @ vertex.co for vertex in sample.data.vertices]


class SurfaceTextMirrorJoinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registered = []
        for operator in (surface.RR_OT_add_surface_text, surface.RR_OT_bind_surface_text):
            if not hasattr(bpy.types, operator.__name__):
                bpy.utils.register_class(operator)
                cls.registered.append(operator)

    @classmethod
    def tearDownClass(cls):
        for operator in reversed(cls.registered):
            bpy.utils.unregister_class(operator)

    def setUp(self):
        clear_scene()

    def tearDown(self):
        clear_scene()

    def assert_joined(self, request, expected_center=Vector((0.0, 0.0, 0.0)), source_faces=2):
        self.assertIsNone(request["mirror_candidates"])
        region = request["surface"]
        self.assertTrue(region.get("mirror_joined"))
        self.assertEqual(region["connected_components"], 1)
        self.assertEqual(region["source_face_count"], source_faces)
        self.assertEqual(region["source_face_indices"], list(range(source_faces)))
        self.assertEqual(len(region["records"]), source_faces * 2)
        self.assertLess((region["center"] - expected_center).length, 1e-5)

    def assert_separate(self, request):
        self.assertFalse(request["surface"].get("mirror_joined", False))
        candidates = request["mirror_candidates"]
        self.assertIsNotNone(candidates)
        self.assertEqual(len(candidates), 2)
        self.assertGreater(candidates[0]["center"].x, 0.0)
        self.assertLess(candidates[1]["center"].x, 0.0)

    def add_text(self):
        self.assertEqual(bpy.ops.rr_builder.add_surface_text("EXEC_DEFAULT"), {"FINISHED"})
        text = bpy.context.object
        self.assertEqual(text.type, "FONT")
        return text, text["rr_surface_text_surface_ref"]

    def test_request_joins_continuous_seam_and_keeps_source_face_identity(self):
        target, _mirror, _plane = make_target()
        before = source_signature(target)
        request = surface._read_surface_request(bpy.context)
        self.assert_joined(request)
        self.assertEqual(source_signature(target), before)
        # The side picker and explicit Bind workflow still need both candidates.
        side_request = surface._read_surface_request(bpy.context, join_connected_mirror=False)
        self.assert_separate(side_request)
        self.assertEqual(len(surface._mirror_candidates(target, side_request["surface"])), 2)

    def test_real_operator_creates_one_centered_font_and_connected_sampling_mesh(self):
        target, _mirror, plane = make_target()
        before = source_signature(target)
        text, sample = self.add_text()
        self.assertEqual(len([obj for obj in bpy.data.objects if obj.type == "FONT"]), 1)
        self.assertEqual(len([obj for obj in bpy.data.objects
                              if obj.get("rr_surface_role") == "sampling_surface"]), 1)
        self.assertTrue(text["rr_surface_text_mirror_joined"])
        self.assertTrue(sample["rr_surface_mirror_joined"])
        self.assertEqual(list(sample["rr_surface_source_face_indices"]), [0, 1])
        self.assertEqual(len(sample.data.polygons), 4)
        self.assertEqual(mesh_components(sample.data), 1,
                         "A joined title must have welded shared seam edges, not touching islands.")
        points = [plane.inverted() @ point for point in sample_world_points(sample)]
        self.assertAlmostEqual(min(point.x for point in points), -4.0, places=5)
        self.assertAlmostEqual(max(point.x for point in points), 4.0, places=5)
        self.assertAlmostEqual(text.matrix_world.translation.x, 0.0, places=5)
        self.assertAlmostEqual(text.matrix_world.translation.z, 0.0, places=5)
        evaluated_points, _inverse = surface._evaluated_world_points(bpy.context, text)
        self.assertLess(min(point.x for point in evaluated_points), 0.0)
        self.assertGreater(max(point.x for point in evaluated_points), 0.0)
        self.assertEqual(source_signature(target), before)

    def test_merge_disabled_keeps_original_and_mirrored_candidates(self):
        make_target(merge=False)
        self.assert_separate(surface._read_surface_request(bpy.context))

    def test_seam_gap_outside_merge_distance_keeps_sides_separate(self):
        make_target(xs=(0.06, 2.0, 4.0), threshold=0.1)
        self.assert_separate(surface._read_surface_request(bpy.context))

    def test_exact_merge_distance_does_not_join(self):
        # Blender compares source-to-reflection distance strictly below the threshold.
        make_target(xs=(0.0625, 2.0, 4.0), threshold=0.125)
        self.assert_separate(surface._read_surface_request(bpy.context))

    def test_seam_within_merge_distance_is_welded_without_moving_source(self):
        target, _mirror, _plane = make_target(xs=(0.049, 2.0, 4.0), threshold=0.1)
        before = source_signature(target)
        self.assert_joined(surface._read_surface_request(bpy.context))
        _text, sample = self.add_text()
        self.assertEqual(mesh_components(sample.data), 1)
        self.assertEqual(len(sample.data.vertices), 10,
                         "Each seam pair must share one sampling vertex after Mirror Merge.")
        self.assertEqual(len([vertex for vertex in sample.data.vertices if abs(vertex.co.x) < 1e-6]), 2)
        self.assertEqual(source_signature(target), before)

    def test_only_one_vertex_touching_is_not_a_connected_title_region(self):
        mesh = bpy.data.meshes.new("PointTouchSourceMesh")
        mesh.from_pydata([(0, 0, 0), (4, 0, -2), (4, 0, 2)], [], [(0, 1, 2)])
        mesh.update()
        target = bpy.data.objects.new("PointTouchSource", mesh)
        bpy.context.scene.collection.objects.link(target)
        mirror = target.modifiers.new("PointTouch Mirror", "MIRROR")
        mirror.use_axis = (True, False, False)
        mirror.use_mirror_merge = True
        select_region(target)
        self.assert_separate(surface._read_surface_request(bpy.context))

    def test_gently_curved_continuous_region_joins_across_seam(self):
        make_target(depth=lambda x: 0.05 * x * x)
        request = surface._read_surface_request(bpy.context)
        self.assertIsNone(request["mirror_candidates"])
        self.assertTrue(request["surface"].get("mirror_joined"))
        self.assertAlmostEqual(request["surface"]["center"].x, 0.0, places=5)
        self.assertGreater(request["surface"]["normal"].dot(Vector((0.0, -1.0, 0.0))), 0.99)

    def test_unselected_cap_on_source_seam_does_not_block_selected_wall_join(self):
        target, _mirror, _plane = make_target()
        bpy.ops.object.mode_set(mode="OBJECT")
        # The cap shares the wall's center boundary but is deliberately not
        # selected; only the smooth wall should define the title's region.
        bm = bmesh.new()
        try:
            bm.from_mesh(target.data)
            bm.verts.ensure_lookup_table()
            seam_bottom, seam_top = bm.verts[0], bm.verts[3]
            bottom = bm.verts.new((0.0, 2.0, -2.0))
            top = bm.verts.new((0.0, 2.0, 2.0))
            bm.faces.new((seam_bottom, seam_top, top, bottom))
            bm.to_mesh(target.data)
            target.data.update()
        finally:
            bm.free()
        select_region(target, (0, 1))
        self.assert_joined(surface._read_surface_request(bpy.context))
        _text, sample = self.add_text()
        self.assertEqual(len(sample.data.polygons), 4)
        self.assertEqual(mesh_components(sample.data), 1)

    def test_sharp_seam_fold_does_not_claim_a_joined_surface(self):
        make_target(depth=lambda x: 5.0 * x)
        before = datablocks()
        self.assert_separate(surface._read_surface_request(bpy.context))
        self.assertEqual(datablocks(), before)

    def test_unsupported_bisect_and_multiple_axes_fail_before_creating_data(self):
        for unsupported in ("bisect", "multiple_axes"):
            with self.subTest(unsupported=unsupported):
                clear_scene()
                _target, mirror, _plane = make_target()
                if unsupported == "bisect":
                    mirror.use_bisect_axis[0] = True
                else:
                    mirror.use_axis[1] = True
                before = datablocks()
                with self.assertRaises(RuntimeError):
                    surface._read_surface_request(bpy.context)
                self.assertEqual(datablocks(), before)
                self.assertEqual(bpy.context.mode, "EDIT_MESH")

    def test_object_rotation_and_nonuniform_scale_center_on_transformed_seam(self):
        transform = (Matrix.Translation((3.0, -2.0, 5.0))
                     @ Matrix.Rotation(0.7, 4, "Z")
                     @ Matrix.Diagonal((1.4, 0.7, 1.1, 1.0)))
        target, _mirror, _plane = make_target(target_matrix=transform)
        before = source_signature(target)
        self.assert_joined(surface._read_surface_request(bpy.context), transform.translation)
        text, sample = self.add_text()
        sample_center = Vector(sample["rr_surface_center_world"])
        self.assertLess((sample_center - transform.translation).length, 1e-5)
        self.assertEqual(mesh_components(sample.data), 1)
        self.assertEqual(source_signature(target), before)
        self.assertTrue(text["rr_surface_text_mirror_joined"])

    def test_negative_scale_preserves_outward_sampling_and_export_normals(self):
        transform = (Matrix.Translation((3.0, -2.0, 5.0))
                     @ Matrix.Rotation(0.7, 4, "Z")
                     @ Matrix.Diagonal((-1.4, 0.7, 1.1, 1.0)))
        target, _mirror, _plane = make_target(target_matrix=transform)
        before_source = source_signature(target)
        expected_normal = (transform.to_3x3().inverted().transposed()
                           @ Vector((0.0, -1.0, 0.0))).normalized()
        request = surface._read_surface_request(bpy.context)
        self.assert_joined(request, transform.translation)
        self.assertGreater(request["surface"]["normal"].dot(expected_normal), 0.99999)
        text, sample = self.add_text()
        self.assertEqual(mesh_components(sample.data), 1)
        sample_normal_matrix = sample.matrix_world.to_3x3().inverted().transposed()
        for polygon in sample.data.polygons:
            normal = (sample_normal_matrix @ polygon.normal).normalized()
            self.assertGreater(normal.dot(expected_normal), 0.99999)
        self.assertGreater(text.matrix_world.to_3x3().col[2].normalized().dot(expected_normal), 0.99999)
        before_blocks = datablocks()
        aliases = []
        try:
            aliases = surface.create_surface_text_sampling_export_aliases(target)
            self.assertEqual(len(aliases), 1)
            alias = aliases[0]
            self.assertGreater(alias.matrix_world.to_3x3().determinant(), 0.0)
            alias_normal_matrix = alias.matrix_world.to_3x3().inverted().transposed()
            for polygon in alias.data.polygons:
                normal = (alias_normal_matrix @ polygon.normal).normalized()
                self.assertGreater(normal.dot(expected_normal), 0.99999)
        finally:
            for alias in aliases:
                mesh = alias.data
                bpy.data.objects.remove(alias, do_unlink=True)
                bpy.data.meshes.remove(mesh)
        self.assertEqual(datablocks(), before_blocks)
        self.assertEqual(source_signature(target), before_source)

    def test_rotated_mirror_object_uses_its_plane_with_independent_source_transform(self):
        target_transform = (Matrix.Translation((5.0, -3.0, 2.0))
                            @ Matrix.Rotation(-0.4, 4, "Z")
                            @ Matrix.Diagonal((0.7, 1.3, 1.1, 1.0)))
        mirror_transform = (Matrix.Translation((-1.0, 2.0, 3.0))
                            @ Matrix.Rotation(0.6, 4, "Z")
                            @ Matrix.Rotation(-0.2, 4, "X")
                            @ Matrix.Diagonal((1.6, 0.8, 1.2, 1.0)))
        target, mirror, plane = make_target(target_matrix=target_transform, mirror_matrix=mirror_transform)
        before = source_signature(target)
        self.assert_joined(surface._read_surface_request(bpy.context), plane.translation)
        _text, sample = self.add_text()
        self.assertEqual(sample["rr_surface_mirror_object"], mirror.mirror_object.name)
        self.assertEqual(mesh_components(sample.data), 1)
        points = [plane.inverted() @ point for point in sample_world_points(sample)]
        self.assertAlmostEqual(min(point.x for point in points), -4.0, places=4)
        self.assertAlmostEqual(max(point.x for point in points), 4.0, places=4)
        self.assertEqual(source_signature(target), before)

    def test_bind_existing_mirrored_side_remains_explicit_and_preserves_authored_pose(self):
        target, _mirror, _plane = make_target()
        text, _joined_sample = self.add_text()
        text.data.body = "ENTRY"
        text.location.x += 0.2
        bpy.context.view_layer.update()
        pose = text.matrix_world.copy()
        select_region(target)
        self.assertEqual(bpy.ops.rr_builder.bind_surface_text(
            "EXEC_DEFAULT", font_object_name=text.name, mirror_side="MIRRORED"), {"FINISHED"})
        self.assertEqual(text.data.body, "ENTRY")
        self.assertEqual(text.matrix_world, pose)
        sample = text["rr_surface_text_surface_ref"]
        self.assertFalse(sample.get("rr_surface_mirror_joined", False))
        self.assertEqual(len(sample.data.polygons), 2)
        self.assertLessEqual(max(point.x for point in sample_world_points(sample)), 1e-6)
        self.assertEqual(bpy.context.mode, "EDIT_MESH")

    def test_saved_joined_region_rejects_removed_merge_without_leaks_or_sample_changes(self):
        target, mirror, _plane = make_target()
        text, sample = self.add_text()
        before_geometry = tuple(tuple(vertex.co) for vertex in sample.data.vertices)
        before_blocks = datablocks()
        mirror.use_mirror_merge = False
        with self.assertRaises(RuntimeError):
            surface.build_surface_text_manifest(target)
        self.assertEqual(datablocks(), before_blocks)
        self.assertEqual(tuple(tuple(vertex.co) for vertex in sample.data.vertices), before_geometry)
        self.assertIs(text["rr_surface_text_surface_ref"], sample)

    def test_shape_key_breaking_actual_mirror_seam_rejects_export(self):
        target, _mirror, _plane = make_target()
        text, sample = self.add_text()
        target.shape_key_add(name="Basis")
        separated = target.shape_key_add(name="Separated Center")
        for index in (0, 3):
            separated.data[index].co.x = 0.2
        separated.value = 1.0
        bpy.context.view_layer.update()
        # Base mesh topology and center-edge positions remain unchanged; only
        # checking authored data would incorrectly accept this broken seam.
        self.assertAlmostEqual(target.data.vertices[0].co.x, 0.0, places=6)
        before_geometry = tuple(tuple(vertex.co) for vertex in sample.data.vertices)
        before_blocks = datablocks()
        with self.assertRaises(RuntimeError):
            surface.build_surface_text_manifest(target)
        self.assertEqual(datablocks(), before_blocks)
        self.assertEqual(tuple(tuple(vertex.co) for vertex in sample.data.vertices), before_geometry)
        self.assertIs(text["rr_surface_text_surface_ref"], sample)

    def test_shape_key_breaking_actual_seam_cancels_add_without_partial_objects(self):
        target, _mirror, _plane = make_target()
        bpy.ops.object.mode_set(mode="OBJECT")
        target.shape_key_add(name="Basis")
        separated = target.shape_key_add(name="Separated Center")
        for index in (0, 3):
            separated.data[index].co.x = 0.2
        separated.value = 1.0
        target.active_shape_key_index = 0
        select_region(target)
        self.assertTrue(surface._read_surface_request(bpy.context)["surface"].get("mirror_joined"))
        before_blocks = datablocks()
        try:
            result = bpy.ops.rr_builder.add_surface_text("EXEC_DEFAULT")
        except RuntimeError:
            # Blender raises when a cancelled operator reports an ERROR.
            result = {"CANCELLED"}
        self.assertEqual(result, {"CANCELLED"})
        self.assertEqual(datablocks(), before_blocks)
        self.assertEqual(bpy.context.mode, "EDIT_MESH")

    def test_add_and_export_allow_geometry_preserving_nodes_shading(self):
        target, _mirror, _plane = make_target()
        group = bpy.data.node_groups.new("Fixture Set Shade Smooth", "GeometryNodeTree")
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group_input = group.nodes.new("NodeGroupInput")
        group_output = group.nodes.new("NodeGroupOutput")
        shade_smooth = group.nodes.new("GeometryNodeSetShadeSmooth")
        group.links.new(group_input.outputs["Geometry"], shade_smooth.inputs["Geometry"])
        group.links.new(shade_smooth.outputs["Geometry"], group_output.inputs["Geometry"])
        modifier = target.modifiers.new("Authored Shading Nodes", "NODES")
        modifier.node_group = group
        _text, sample = self.add_text()
        self.assertTrue(sample["rr_surface_mirror_joined"])
        self.assertEqual(mesh_components(sample.data), 1)
        descriptor = surface.build_surface_text_manifest(target)[0]
        self.assertEqual(descriptor["editableVersion"], 1)

    def test_complete_selected_region_survives_manifest_and_real_fbx_roundtrip(self):
        # An unselected third source face checks that export keeps both selected
        # halves without accidentally expanding to the full evaluated target.
        target, _mirror, _plane = make_target(xs=(0.0, 2.0, 4.0, 6.0), selected_indices=(0, 1))
        text, sample = self.add_text()
        before_source = source_signature(target)
        before_blocks = datablocks()
        descriptors = surface.build_surface_text_manifest(target)
        self.assertEqual(len(descriptors), 1)
        descriptor = descriptors[0]
        self.assertEqual(descriptor["editableVersion"], 1)
        self.assertEqual(descriptor["text"], text.data.body)
        self.assertAlmostEqual(descriptor["regionSize"][0], 8.0, places=5)
        self.assertAlmostEqual(descriptor["regionSize"][1], 4.0, places=5)
        self.assertEqual(len(descriptor["frameObjectNames"]), 4)
        with surface._evaluated_sampling_mesh(text) as (vertices, faces):
            self.assertEqual(len(faces), 4)
            expected_world = [sample.matrix_world @ Vector(point) for point in vertices]
        self.assertEqual(datablocks(), before_blocks)
        self.assertEqual(source_signature(target), before_source)
        with tempfile.TemporaryDirectory(prefix="rr_surface_mirror_join_") as directory:
            path = os.path.join(directory, "joined_title.fbx")
            exporter.export_fbx(target, path)
            self.assertGreater(os.path.getsize(path), 0)
            self.assertEqual(datablocks(), before_blocks)
            self.assertEqual(source_signature(target), before_source)
            clear_scene()
            bpy.ops.import_scene.fbx(filepath=path)
            imported = bpy.data.objects.get(descriptor["samplingSurfaceExportObjectName"])
            self.assertIsNotNone(imported)
            points = sample_world_points(imported)
            self.assertAlmostEqual(min(point.x for point in points), -4.0, places=4)
            self.assertAlmostEqual(max(point.x for point in points), 4.0, places=4)
            for point in expected_world:
                self.assertLess(min((point - actual).length for actual in points), 1e-4)
            self.assertTrue(all(bpy.data.objects.get(name) is not None
                                for name in descriptor["frameObjectNames"]))
            self.assertIsNotNone(bpy.data.objects.get(descriptor["exportObjectName"]))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceTextMirrorJoinTests)
    print("SURFACE_TEXT_MIRROR_JOIN_TEST_COUNT=" + str(suite.countTestCases()))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
