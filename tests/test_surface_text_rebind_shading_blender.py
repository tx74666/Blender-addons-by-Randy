"""Font-only rebind and proven shading regressions in an isolated Blender.

Run with --background --factory-startup --python and this file. These tests
generate their own tiny meshes and never open or save an authored project.
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


def select_region(target, indices):
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
    chosen = set(indices)
    for face in bm.faces:
        face.select_set(face.index in chosen)
    bmesh.update_edit_mesh(target.data)


def selected_faces(target):
    bm = bmesh.from_edit_mesh(target.data)
    bm.faces.ensure_lookup_table()
    return tuple(face.index for face in bm.faces if face.select)


def make_fixture():
    xs = (0.0, 2.0, 4.0, 6.0, 8.0)
    vertices = [(x, 0.0, z) for z in (-2.0, 2.0) for x in xs]
    faces = [(i, i + 1, i + 6, i + 5) for i in range(4)]
    mesh = bpy.data.meshes.new("ExpandedSignSourceMesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    target = bpy.data.objects.new("ExpandedSignSource", mesh)
    bpy.context.scene.collection.objects.link(target)
    select_region(target, (0,))
    result = bpy.ops.rr_builder.add_surface_text("EXEC_DEFAULT")
    if result != {"FINISHED"}:
        raise AssertionError(result)
    text = bpy.context.object
    text.name = "Authored ENTRY Font"
    text.data.body = "ENTRY"
    text.data.size = 0.6
    text.data.space_character = 1.15
    text.location.x += 0.13
    text["artist_note"] = "Keep my authored lettering"
    text["artist_settings"] = {
        "color": [0.1, 0.4, 0.8],
        "layout": {"padding": [0.15, 0.25], "enabled": True},
    }
    surface._surface_text_id(text)
    shrinkwrap, solidify = surface._surface_text_modifier_pair(text)
    shrinkwrap.offset = 0.007
    shrinkwrap.project_limit = 20.0
    solidify.thickness = -0.17
    solidify.offset = 0.25
    material = bpy.data.materials.new("Authored Lettering Material")
    material.diffuse_color = (0.1, 0.4, 0.8, 1.0)
    text.data.materials.append(material)
    bpy.context.view_layer.update()
    return target, text, text["rr_surface_text_surface_ref"]


def make_nodes(target, kind="shading", name="Unrelated Shading Label"):
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input = group.nodes.new("NodeGroupInput")
    group_output = group.nodes.new("NodeGroupOutput")
    if kind == "shading":
        smooth = group.nodes.new("GeometryNodeSetShadeSmooth")
        sharp = group.nodes.new("GeometryNodeStoreNamedAttribute")
        sharp.data_type = "BOOLEAN"
        sharp.domain = "EDGE"
        sharp.inputs["Name"].default_value = "sharp_edge"
        sharp.inputs["Value"].default_value = True
        group.links.new(group_input.outputs["Geometry"], smooth.inputs["Geometry"])
        group.links.new(smooth.outputs["Geometry"], sharp.inputs["Geometry"])
        group.links.new(sharp.outputs["Geometry"], group_output.inputs["Geometry"])
    elif kind == "move":
        move = group.nodes.new("GeometryNodeSetPosition")
        move.inputs["Offset"].default_value = (0.25, 0.0, 0.0)
        group.links.new(group_input.outputs["Geometry"], move.inputs["Geometry"])
        group.links.new(move.outputs["Geometry"], group_output.inputs["Geometry"])
    elif kind == "delete":
        delete = group.nodes.new("GeometryNodeDeleteGeometry")
        delete.domain = "FACE"
        delete.inputs["Selection"].default_value = True
        group.links.new(group_input.outputs["Geometry"], delete.inputs["Geometry"])
        group.links.new(delete.outputs["Geometry"], group_output.inputs["Geometry"])
    elif kind == "stored_position":
        position = group.nodes.new("GeometryNodeInputPosition")
        offset = group.nodes.new("ShaderNodeVectorMath")
        offset.operation = "ADD"
        offset.inputs[1].default_value = (0.25, 0.0, 0.0)
        store = group.nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = "FLOAT_VECTOR"
        store.domain = "POINT"
        store.inputs["Name"].default_value = "position"
        group.links.new(position.outputs["Position"], offset.inputs[0])
        group.links.new(offset.outputs["Vector"], store.inputs["Value"])
        group.links.new(group_input.outputs["Geometry"], store.inputs["Geometry"])
        group.links.new(store.outputs["Geometry"], group_output.inputs["Geometry"])
    elif kind == "hidden_instances":
        instances = group.nodes.new("GeometryNodeGeometryToInstance")
        join = group.nodes.new("GeometryNodeJoinGeometry")
        group.links.new(group_input.outputs["Geometry"], instances.inputs["Geometry"])
        group.links.new(group_input.outputs["Geometry"], join.inputs["Geometry"])
        group.links.new(instances.outputs["Instances"], join.inputs["Geometry"])
        group.links.new(join.outputs["Geometry"], group_output.inputs["Geometry"])
    else:
        raise AssertionError(kind)
    modifier = target.modifiers.new(name, "NODES")
    modifier.node_group = group
    return modifier, group


def datablocks():
    return tuple(set(collection.keys()) for collection in
                 (bpy.data.objects, bpy.data.meshes, bpy.data.curves, bpy.data.materials, bpy.data.node_groups))


def normalize_property(value):
    if isinstance(value, bpy.types.ID):
        return ("ID", value.as_pointer())
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if isinstance(value, dict):
        return tuple(sorted((str(key), normalize_property(item)) for key, item in value.items()))
    if hasattr(value, "to_list"):
        value = value.to_list()
    if isinstance(value, (tuple, list)):
        return tuple(normalize_property(item) for item in value)
    return value


def authored_signature(text):
    shrinkwrap, solidify = surface._surface_text_modifier_pair(text)
    return (
        tuple(tuple(row) for row in text.matrix_world), text.data.as_pointer(),
        text.data.body, text.data.size, text.data.space_character, text.data.space_line,
        text.data.align_x, text.data.align_y, text.data.font.as_pointer(),
        tuple(material.as_pointer() for material in text.data.materials),
        text["artist_note"], normalize_property(text["artist_settings"]), text["rr_surface_text_id"],
        (shrinkwrap.as_pointer(), shrinkwrap.target.as_pointer(), shrinkwrap.wrap_method,
         shrinkwrap.wrap_mode, shrinkwrap.offset, shrinkwrap.project_limit,
         shrinkwrap.use_project_x, shrinkwrap.use_project_y, shrinkwrap.use_project_z,
         shrinkwrap.use_negative_direction, shrinkwrap.use_positive_direction),
        (solidify.as_pointer(), solidify.thickness, solidify.offset, solidify.use_rim),
    )


def target_signature(target):
    return (target.data.as_pointer(), tuple(tuple(vertex.co) for vertex in target.data.vertices),
            tuple(tuple(face.vertices) for face in target.data.polygons),
            tuple((modifier.as_pointer(), modifier.name, modifier.type,
                   modifier.show_viewport, modifier.show_render) for modifier in target.modifiers))


def bind_operator(target, font_name, indices=(0, 1, 2), side="ORIGINAL"):
    select_region(target, indices)
    try:
        return bpy.ops.rr_builder.bind_surface_text(
            "EXEC_DEFAULT", font_object_name=font_name, mirror_side=side)
    except RuntimeError:
        # Blender raises when a cancelled operator reports an ERROR.
        return {"CANCELLED"}


def object_mode():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


class SurfaceTextRebindShadingTests(unittest.TestCase):
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
        self.target, self.text, self.old_sample = make_fixture()

    def tearDown(self):
        clear_scene()

    def assert_expanded(self):
        sample = self.text["rr_surface_text_surface_ref"]
        self.assertIsNot(sample, self.old_sample)
        self.assertEqual(list(sample["rr_surface_source_face_indices"]), [0, 1, 2])
        self.assertEqual(len(sample.data.polygons), 3)
        object_mode()
        descriptors = surface.build_surface_text_manifest(self.target)
        self.assertEqual(len(descriptors), 1)
        descriptor = descriptors[0]
        self.assertEqual(descriptor["editableVersion"], 1)
        self.assertEqual(descriptor["text"], "ENTRY")
        self.assertAlmostEqual(descriptor["regionSize"][0], 6.0, places=5)
        self.assertAlmostEqual(descriptor["regionSize"][1], 4.0, places=5)
        self.assertAlmostEqual(descriptor["textCenter"][0], 1.13, places=5)
        return sample, descriptor

    def assert_rebind_rejected_and_rolled_back(self, name=None):
        select_region(self.target, (0, 1, 2))
        before_blocks = datablocks()
        before_authored = authored_signature(self.text)
        before_target = target_signature(self.target)
        before_properties = normalize_property(dict(self.text.items()))
        before_sample = (self.old_sample.data.as_pointer(),
                         tuple(tuple(vertex.co) for vertex in self.old_sample.data.vertices))
        self.assertEqual(bind_operator(self.target, name or self.text.name), {"CANCELLED"})
        self.assertEqual(bpy.context.mode, "EDIT_MESH")
        self.assertIs(bpy.context.object, self.target)
        self.assertEqual(selected_faces(self.target), (0, 1, 2))
        self.assertEqual(datablocks(), before_blocks)
        self.assertEqual(authored_signature(self.text), before_authored)
        self.assertEqual(target_signature(self.target), before_target)
        self.assertEqual(normalize_property(dict(self.text.items())), before_properties)
        self.assertIs(self.text["rr_surface_text_surface_ref"], self.old_sample)
        self.assertEqual((self.old_sample.data.as_pointer(),
                          tuple(tuple(vertex.co) for vertex in self.old_sample.data.vertices)), before_sample)

    def test_font_search_lists_only_editable_fonts_and_refreshes_current_names(self):
        font_data = bpy.data.curves.new("Ordinary Font Data", "FONT")
        ordinary = bpy.data.objects.new("普通标题 Font", font_data)
        bpy.context.scene.collection.objects.link(ordinary)
        curve_data = bpy.data.curves.new("Ordinary Curve Data", "CURVE")
        curve = bpy.data.objects.new("Not a Font Curve", curve_data)
        bpy.context.scene.collection.objects.link(curve)
        empty = bpy.data.objects.new("Not a Font Empty", None)
        bpy.context.scene.collection.objects.link(empty)
        converted = bpy.data.objects.new("Surface Text Converted Mesh", self.target.data.copy())
        bpy.context.scene.collection.objects.link(converted)

        def candidates():
            values = surface._search_bind_fonts(None, bpy.context, "")
            return {value if isinstance(value, str) else value[0] for value in values}

        self.assertEqual(candidates(), {self.text.name, ordinary.name})
        self.assertNotIn(self.target.name, candidates())
        self.assertNotIn(curve.name, candidates())
        self.assertNotIn(empty.name, candidates())
        self.assertNotIn(converted.name, candidates())
        self.assertEqual(list(surface._search_bind_fonts(None, bpy.context, "eNtRy")), [self.text.name])
        old_name = ordinary.name
        ordinary.name = "Renamed Ordinary Font"
        self.assertNotIn(old_name, candidates())
        self.assertIn(ordinary.name, candidates())
        bpy.data.objects.remove(ordinary, do_unlink=True)
        self.assertEqual(candidates(), {self.text.name})

    def test_manual_nonfont_name_is_rejected_without_changing_existing_binding(self):
        self.assert_rebind_rejected_and_rolled_back(name=self.target.name)

    def test_proven_shading_expands_region_and_preserves_font_pose_material_and_modifiers(self):
        make_nodes(self.target, name="Artist Shading With No Magic Name")
        before_authored = authored_signature(self.text)
        before_target = target_signature(self.target)
        self.assertEqual(bind_operator(self.target, self.text.name), {"FINISHED"})
        self.assertEqual(bpy.context.mode, "EDIT_MESH")
        self.assertEqual(authored_signature(self.text), before_authored)
        self.assertEqual(target_signature(self.target), before_target)
        self.assert_expanded()

    def test_official_smooth_by_angle_asset_allows_explicit_expansion(self):
        version = f"{bpy.app.version[0]}.{bpy.app.version[1]}"
        candidates = [Path(bpy.app.binary_path).parent / version / "datafiles" / "assets" / "nodes"
                      / "geometry_nodes_essentials.blend",
                      Path(r"D:\Blender5.2\5.2\datafiles\assets\nodes\geometry_nodes_essentials.blend")]
        asset = next((path for path in candidates if path.is_file()), None)
        if asset is None:
            self.skipTest("Blender Essentials Smooth by Angle asset is not installed.")
        with bpy.data.libraries.load(str(asset), link=False) as (data_from, data_to):
            names = [name for name in data_from.node_groups if name == "Smooth by Angle"]
            self.assertTrue(names, "The installed Essentials asset must expose Smooth by Angle.")
            data_to.node_groups = names[:1]
        group = data_to.node_groups[0]
        self.assertIsNotNone(group)
        modifier = self.target.modifiers.new("User's Official Smooth by Angle", "NODES")
        modifier.node_group = group
        before_authored = authored_signature(self.text)
        self.assertEqual(bind_operator(self.target, self.text.name), {"FINISHED"})
        self.assertEqual(authored_signature(self.text), before_authored)
        self.assert_expanded()

    def test_fake_smooth_by_angle_that_moves_vertices_is_rejected_with_full_rollback(self):
        make_nodes(self.target, kind="move", name="Smooth by Angle")
        self.assert_rebind_rejected_and_rolled_back()

    def test_fake_smooth_by_angle_that_changes_topology_is_rejected_with_full_rollback(self):
        make_nodes(self.target, kind="delete", name="Smooth by Angle")
        self.assert_rebind_rejected_and_rolled_back()

    def test_allowed_attribute_node_writing_position_is_rejected_by_geometry_proof(self):
        # Store Named Attribute is also used for sharp_edge shading. Its node
        # class alone cannot prove safety when it writes built-in positions.
        make_nodes(self.target, kind="stored_position", name="Smooth by Angle")
        self.assert_rebind_rejected_and_rolled_back()

    def test_original_mesh_plus_hidden_instances_is_rejected_with_full_rollback(self):
        # to_mesh() can retain the unchanged original mesh while omitting this
        # additional component; a mesh-only equality check would miss it.
        make_nodes(self.target, kind="hidden_instances", name="Smooth by Angle")
        self.assert_rebind_rejected_and_rolled_back()

    def test_proven_shading_after_mirror_does_not_block_expansion(self):
        mirror = self.target.modifiers.new("User Mirror", "MIRROR")
        mirror.use_axis = (True, False, False)
        make_nodes(self.target)
        before_authored = authored_signature(self.text)
        self.assertEqual(bind_operator(self.target, self.text.name), {"FINISHED"})
        self.assertEqual(authored_signature(self.text), before_authored)
        sample, _descriptor = self.assert_expanded()
        self.assertFalse(sample.get("rr_surface_mirror_joined", False))
        with surface._evaluated_sampling_mesh(self.text) as (points, faces):
            self.assertEqual(len(faces), 3)
            self.assertAlmostEqual(min(point[0] for point in points), 0.0, places=5)
            self.assertAlmostEqual(max(point[0] for point in points), 6.0, places=5)

    def test_two_position_changes_cancelling_each_other_are_still_rejected(self):
        make_nodes(self.target, kind="move", name="Smooth by Angle")
        _modifier, group = make_nodes(self.target, kind="move", name="Smooth by Angle Cancel Move")
        move = next(node for node in group.nodes if node.bl_idname == "GeometryNodeSetPosition")
        move.inputs["Offset"].default_value = (-0.25, 0.0, 0.0)
        self.assert_rebind_rejected_and_rolled_back()

    def test_expanded_shaded_region_survives_real_manifest_and_fbx_roundtrip(self):
        make_nodes(self.target)
        self.assertEqual(bind_operator(self.target, self.text.name), {"FINISHED"})
        sample, descriptor = self.assert_expanded()
        before_authored = authored_signature(self.text)
        before_target = target_signature(self.target)
        before_blocks = datablocks()
        with surface._evaluated_sampling_mesh(self.text) as (vertices, faces):
            self.assertEqual(len(faces), 3)
            expected_world = [sample.matrix_world @ Vector(point) for point in vertices]
        with tempfile.TemporaryDirectory(prefix="rr_rebind_shading_") as directory:
            path = os.path.join(directory, "expanded_title.fbx")
            exporter.export_fbx(self.target, path)
            self.assertGreater(os.path.getsize(path), 0)
            self.assertEqual(datablocks(), before_blocks)
            self.assertEqual(authored_signature(self.text), before_authored)
            self.assertEqual(target_signature(self.target), before_target)
            clear_scene()
            bpy.ops.import_scene.fbx(filepath=path)
            imported = bpy.data.objects.get(descriptor["samplingSurfaceExportObjectName"])
            self.assertIsNotNone(imported)
            points = [imported.matrix_world @ vertex.co for vertex in imported.data.vertices]
            self.assertAlmostEqual(min(point.x for point in points), 0.0, places=4)
            self.assertAlmostEqual(max(point.x for point in points), 6.0, places=4)
            for point in expected_world:
                self.assertLess(min((point - actual).length for actual in points), 1e-4)
            self.assertTrue(all(bpy.data.objects.get(name) is not None
                                for name in descriptor["frameObjectNames"]))
            self.assertIsNotNone(bpy.data.objects.get(descriptor["exportObjectName"]))

    def test_shading_proof_is_rechecked_after_node_graph_changes(self):
        _modifier, group = make_nodes(self.target)
        self.assertEqual(bind_operator(self.target, self.text.name), {"FINISHED"})
        sample, _descriptor = self.assert_expanded()
        before_sample = tuple(tuple(vertex.co) for vertex in sample.data.vertices)
        group_output = next(node for node in group.nodes if node.type == "GROUP_OUTPUT")
        group_input = next(node for node in group.nodes if node.type == "GROUP_INPUT")
        move = group.nodes.new("GeometryNodeSetPosition")
        move.inputs["Offset"].default_value = (0.25, 0.0, 0.0)
        group.links.new(group_input.outputs["Geometry"], move.inputs["Geometry"])
        group.links.new(move.outputs["Geometry"], group_output.inputs["Geometry"])
        bpy.context.view_layer.update()
        before_blocks = datablocks()
        with self.assertRaises(RuntimeError):
            surface.build_surface_text_manifest(self.target)
        self.assertEqual(datablocks(), before_blocks)
        self.assertEqual(tuple(tuple(vertex.co) for vertex in sample.data.vertices), before_sample)

    def test_shading_viewport_render_mismatch_is_rejected_with_full_rollback(self):
        modifier, _group = make_nodes(self.target)
        modifier.show_render = False
        self.assert_rebind_rejected_and_rolled_back()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceTextRebindShadingTests)
    print("SURFACE_TEXT_REBIND_SHADING_TEST_COUNT=" + str(suite.countTestCases()))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
