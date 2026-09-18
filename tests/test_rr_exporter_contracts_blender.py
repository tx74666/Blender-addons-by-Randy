import json
import hashlib
import inspect
import math
import os
import shutil
import sys
import tempfile
import unittest
import warnings
from types import SimpleNamespace
from unittest import mock

import bmesh
import bpy
from mathutils import Matrix, Vector


warnings.filterwarnings("ignore", category=DeprecationWarning)
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
BLENDER_ROOT = os.path.dirname(TEST_DIR)
ADDONS_DIR = os.path.join(BLENDER_ROOT, "addons")
if ADDONS_DIR not in sys.path:
    sys.path.insert(0, ADDONS_DIR)

import random_realm_builder_exporter as exporter
from random_realm_builder_exporter import rr_point_bookmarks, rr_unity_uv_export


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


def uv_values(layer):
    return [(round(loop.uv.x, 6), round(loop.uv.y, 6)) for loop in layer.data]


def make_material(name, rotation_degrees=90.0):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    texture_coordinate = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    image_texture = nodes.new("ShaderNodeTexImage")
    image_texture.name = name + "_BaseColor"
    image_texture.label = "Base Color"
    image_texture.image = bpy.data.images.new(name + "_BaseColor", width=2, height=2)
    descriptor, image_path = tempfile.mkstemp(prefix="rr_exporter_contract_", suffix=".png")
    os.close(descriptor)
    image_texture.image.filepath_raw = image_path
    image_texture.image.file_format = "PNG"
    image_texture.image.save()
    TEMP_IMAGE_PATHS.add(image_path)

    links = material.node_tree.links
    links.new(texture_coordinate.outputs["UV"], mapping.inputs["Vector"])
    links.new(mapping.outputs["Vector"], image_texture.inputs["Vector"])
    links.new(image_texture.outputs["Color"], principled.inputs["Base Color"])
    links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    mapping.inputs["Rotation"].default_value[2] = math.radians(rotation_degrees)
    return material, mapping


def make_surface_material(
    name,
    base_color=(0.92, 0.97, 1.0, 1.0),
    metallic=0.0,
    roughness=0.08,
    ior=1.45,
    transmission=1.0,
    alpha=0.25,
):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()

    output = nodes.new("ShaderNodeOutputMaterial")
    principled = nodes.new("ShaderNodeBsdfPrincipled")
    material.node_tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])
    principled.inputs["Base Color"].default_value = base_color
    principled.inputs["Metallic"].default_value = metallic
    principled.inputs["Roughness"].default_value = roughness
    principled.inputs["IOR"].default_value = ior
    transmission_socket = (
        principled.inputs.get("Transmission Weight")
        or principled.inputs.get("Transmission")
    )
    transmission_socket.default_value = transmission
    principled.inputs["Alpha"].default_value = alpha
    return material, principled


def make_quad(name, include_detail_uv=False):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)],
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)

    uv_map = mesh.uv_layers.new(name="UVMap")
    source_uvs = {
        0: (0.0, 0.0),
        1: (1.0, 0.0),
        2: (1.0, 1.0),
        3: (0.0, 1.0),
    }
    for loop in mesh.loops:
        uv_map.data[loop.index].uv = source_uvs[loop.vertex_index]
    uv_map.active_render = True
    mesh.uv_layers.active = uv_map

    detail_uv = None
    if include_detail_uv:
        detail_uv = mesh.uv_layers.new(name="DetailUV")
        for loop in mesh.loops:
            detail_uv.data[loop.index].uv = (
                source_uvs[loop.vertex_index][0] + 10.0,
                source_uvs[loop.vertex_index][1] + 20.0,
            )
        mesh.uv_layers.active = detail_uv

    material, mapping = make_material(name + "_Material")
    mesh.materials.append(material)
    return obj, uv_map, detail_uv, mapping


def make_object_manager_asset_root(name, assembly_id):
    root, _, _, _ = make_quad(name)
    root[exporter.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
    root[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = assembly_id
    root[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = name
    root[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "ASSEMBLY"
    root[exporter.OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = root.name
    return root


def make_curve_object(name, spline_type, coordinates):
    curve = bpy.data.curves.new(name + "_Curve", type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new(spline_type)
    if spline_type == "BEZIER":
        spline.bezier_points.add(len(coordinates) - 1)
        for point, coordinate in zip(spline.bezier_points, coordinates):
            point.co = coordinate[:3]
            point.handle_left_type = "FREE"
            point.handle_right_type = "FREE"
            point.handle_left = Vector(coordinate[:3]) + Vector((-0.5, 0.25, 0.0))
            point.handle_right = Vector(coordinate[:3]) + Vector((0.75, -0.2, 0.0))
    else:
        spline.points.add(len(coordinates) - 1)
        for point, coordinate in zip(spline.points, coordinates):
            point.co = coordinate

    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def select_only_object_for_edit(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")


def curve_world_geometry(obj):
    values = []
    for spline in obj.data.splines:
        for point in spline.bezier_points:
            values.extend(
                (
                    obj.matrix_world @ point.co,
                    obj.matrix_world @ point.handle_left,
                    obj.matrix_world @ point.handle_right,
                )
            )
        for point in spline.points:
            values.append(obj.matrix_world @ Vector(point.co[:3]))
    return [value.copy() for value in values]


class TexturePathContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="rr_texture_paths_")
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = self.temporary_directory.name

    def texture_directory(self, absolute_length):
        path = self.root
        self.assertLess(len(os.path.abspath(path)), absolute_length)
        while len(os.path.abspath(path)) < absolute_length:
            remaining = absolute_length - len(os.path.abspath(path))
            self.assertGreater(remaining, 1)
            component_length = min(48, remaining - 1)
            if remaining - component_length - 1 == 1:
                component_length -= 1
            path = os.path.join(path, "d" * component_length)
        return path

    def source_image(self, filename, payload=b"source texture bytes\x00\xff"):
        path = os.path.join(self.root, filename)
        with open(path, "wb") as handle:
            handle.write(payload)
        return SimpleNamespace(name=filename, filepath=path, packed_file=None), payload

    def test_long_texture_is_copied_under_windows_path_budget(self):
        texture_dir = self.texture_directory(208)
        material = SimpleNamespace(name="BuilderMat_OuterWall_Stone_Exterior_Image2_PBR")
        image, payload = self.source_image("Stone_Exterior_" + "pattern_" * 10 + "BaseColor.png")
        old_name = exporter.sanitize_id(
            material.name + "_BaseColor_" + os.path.splitext(image.name)[0]
        ) + ".png"
        self.assertGreater(len(os.path.join(texture_dir, old_name)), 260)
        export_warnings = []

        relative_path = exporter.copy_image_for_manifest(
            "StoneWall_Straight", material, "BaseColor", image,
            texture_dir, set(), export_warnings,
        )

        self.assertTrue(relative_path.startswith("textures/"))
        destination = os.path.join(texture_dir, os.path.basename(relative_path))
        self.assertLessEqual(len(os.path.abspath(destination)), 240)
        self.assertLessEqual(len(os.path.basename(destination)), 64)
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(), payload)
        self.assertEqual(export_warnings, [])

    def test_long_shared_prefixes_remain_distinct_and_deterministic(self):
        material = SimpleNamespace(name="Material_" + "shared_" * 16)
        first, first_payload = self.source_image("same_prefix_" * 8 + "A.png", b"first")
        second, second_payload = self.source_image("same_prefix_" * 8 + "B.png", b"second")
        names = [exporter.unique_texture_filename(set(), material, "BaseColor", image)
                 for image in (first, second)]
        self.assertNotEqual(names[0].casefold(), names[1].casefold())
        self.assertEqual(names, [exporter.unique_texture_filename(set(), material, "BaseColor", image)
                                 for image in (first, second)])
        texture_dir = os.path.join(self.root, "textures")
        used_names = set()
        outputs = [exporter.copy_image_for_manifest(
            "StoneWall", material, "BaseColor", image, texture_dir, used_names, [],
        ) for image in (first, second)]
        for relative_path, payload in zip(outputs, (first_payload, second_payload)):
            with open(os.path.join(texture_dir, os.path.basename(relative_path)), "rb") as handle:
                self.assertEqual(handle.read(), payload)

    def test_short_names_keep_existing_spelling(self):
        material = SimpleNamespace(name="Stone")
        image = SimpleNamespace(name="Brick.PNG", filepath="")
        self.assertEqual(
            exporter.unique_texture_filename(set(), material, "BaseColor", image),
            "Stone_BaseColor_Brick.png",
        )

    def test_used_names_add_suffix_without_exceeding_budget(self):
        material = SimpleNamespace(name="Stone")
        image = SimpleNamespace(name="Brick.png", filepath="")
        used_names = {"stone_basecolor_brick.png"}
        self.assertEqual(exporter.unique_texture_filename(used_names, material, "BaseColor", image),
                         "Stone_BaseColor_Brick_2.png")
        self.assertEqual(exporter.unique_texture_filename(used_names, material, "BaseColor", image),
                         "Stone_BaseColor_Brick_3.png")
        material.name = "Stone_" * 30
        used_names = set()
        outputs = [exporter.unique_texture_filename(used_names, material, "BaseColor", image, 31)
                   for _ in range(12)]
        self.assertEqual(len(set(name.casefold() for name in outputs)), 12)
        self.assertTrue(all(len(name) <= 31 for name in outputs))
        self.assertTrue(outputs[-1].endswith("_12.png"))

    def test_packed_image_save_restores_original_filepath(self):
        image = bpy.data.images.new("PackedTexturePathContract", width=2, height=2)
        self.addCleanup(lambda: bpy.data.images.remove(image))
        image.file_format = "PNG"
        original_path = os.path.join(self.root, "missing_packed_source.png")
        image.filepath_raw = original_path
        image.save()
        image.pack()
        self.assertIsNotNone(image.packed_file)
        os.remove(original_path)
        texture_dir = self.texture_directory(208)
        material = SimpleNamespace(name="PackedMaterial_" * 8)

        relative_path = exporter.copy_image_for_manifest(
            "PackedWall", material, "BaseColor", image, texture_dir, set(), [],
        )

        self.assertEqual(image.filepath_raw, original_path)
        self.assertFalse(os.path.exists(original_path))
        destination = os.path.join(texture_dir, os.path.basename(relative_path))
        self.assertLessEqual(len(os.path.abspath(destination)), 240)
        with open(destination, "rb") as handle:
            self.assertEqual(handle.read(8), b"\x89PNG\r\n\x1a\n")

    def test_failed_packed_save_restores_filepath_and_reports_destination(self):
        original_path = os.path.join(self.root, "missing_packed_source.png")
        image = SimpleNamespace(
            name="PackedFailure", filepath=original_path, filepath_raw=original_path,
            packed_file=object(), save=mock.Mock(side_effect=RuntimeError("save failed")),
        )
        texture_dir = os.path.join(self.root, "textures")
        with self.assertRaisesRegex(RuntimeError, "PackedWall: could not write BaseColor texture") as caught:
            exporter.copy_image_for_manifest(
                "PackedWall", SimpleNamespace(name="Stone"), "BaseColor", image,
                texture_dir, set(), [],
            )
        self.assertEqual(image.filepath_raw, original_path)
        self.assertIn(texture_dir, str(caught.exception))
        self.assertIn("save failed", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, RuntimeError)
        self.assertEqual(os.listdir(texture_dir), [])

    def test_copy_failure_reports_root_map_destination_and_original_error(self):
        image, _payload = self.source_image("Brick.png")
        texture_dir = os.path.join(self.root, "textures")
        with mock.patch.object(exporter.shutil, "copy2", side_effect=PermissionError("copy denied")):
            with self.assertRaisesRegex(RuntimeError, "StoneWall: could not write Normal texture") as caught:
                exporter.copy_image_for_manifest(
                    "StoneWall", SimpleNamespace(name="Stone"), "Normal", image,
                    texture_dir, set(), [],
                )
        self.assertIn(texture_dir, str(caught.exception))
        self.assertIn("copy denied", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, PermissionError)

    def test_too_deep_folder_fails_before_creating_any_output(self):
        texture_dir = self.texture_directory(221)
        image = SimpleNamespace(name="Packed", filepath="", filepath_raw="", packed_file=object(),
                                save=mock.Mock())
        before = os.listdir(self.root)
        used_names = set()
        with self.assertRaisesRegex(RuntimeError, "export texture folder is too long") as caught:
            exporter.copy_image_for_manifest(
                "StoneWall", SimpleNamespace(name="Stone"), "BaseColor", image,
                texture_dir, used_names, [],
            )
        self.assertIn("Choose a shorter output folder", str(caught.exception))
        self.assertEqual(os.listdir(self.root), before)
        self.assertFalse(os.path.exists(texture_dir))
        self.assertEqual(used_names, set())
        image.save.assert_not_called()


class ExporterUvContractTests(unittest.TestCase):
    def setUp(self):
        clear_scene()

    def tearDown(self):
        clear_scene()

    def assert_uvs(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for actual_uv, expected_uv in zip(actual, expected):
            self.assertAlmostEqual(actual_uv[0], expected_uv[0], places=5)
            self.assertAlmostEqual(actual_uv[1], expected_uv[1], places=5)

    def assert_vectors_close(self, actual, expected, places=5):
        self.assertEqual(len(actual), len(expected))
        for actual_value, expected_value in zip(actual, expected):
            for component in range(3):
                self.assertAlmostEqual(
                    actual_value[component],
                    expected_value[component],
                    places=places,
                )

    def isolate_variant_claim_root(self, _temp_root):
        original_cache_root = exporter.UNITY_BUILDER_CACHE_ROOT
        self.addCleanup(
            setattr,
            exporter,
            "UNITY_BUILDER_CACHE_ROOT",
            original_cache_root,
        )
        isolated_temp_root = tempfile.mkdtemp(prefix="rr_variant_claim_cache_")
        self.addCleanup(shutil.rmtree, isolated_temp_root, ignore_errors=True)
        isolated_cache_root = os.path.join(
            isolated_temp_root,
            "FakeUnityProject",
            "Library",
            "RandomRealmBuilder",
        )
        exporter.UNITY_BUILDER_CACHE_ROOT = isolated_cache_root
        return isolated_cache_root

    def test_origin_selection_uses_bezier_control_point_without_moving_curve(self):
        obj = make_curve_object(
            "BezierOrigin",
            "BEZIER",
            ((0.0, 0.0, 0.0), (3.0, 2.0, 1.0)),
        )
        obj.matrix_world = (
            Matrix.Translation((7.0, -3.0, 2.0))
            @ Matrix.Rotation(math.radians(37.0), 4, "Z")
            @ Matrix.Diagonal((1.5, -0.75, 2.0, 1.0))
        )
        select_only_object_for_edit(obj)
        point = obj.data.splines[0].bezier_points[1]
        point.select_control_point = True
        point.select_left_handle = True
        point.select_right_handle = True

        expected_origin = obj.matrix_world @ point.co.copy()
        before = curve_world_geometry(obj)
        self.assertEqual(exporter.modeling_origin_selection_label(bpy.context), "1 curve point selected")
        applied = exporter.apply_modeling_origin(
            bpy.context,
            SimpleNamespace(modeling_origin_mode="SELECTION"),
        )

        self.assertEqual(applied, 1)
        self.assertEqual(bpy.context.mode, "EDIT_CURVE")
        self.assert_vectors_close([obj.matrix_world.translation], [expected_origin])
        self.assert_vectors_close(curve_world_geometry(obj), before)

    def test_origin_handle_only_selection_resolves_to_bezier_control_point_once(self):
        obj = make_curve_object(
            "BezierHandleOrigin",
            "BEZIER",
            ((1.0, 0.0, 0.0), (5.0, 3.0, 0.0)),
        )
        select_only_object_for_edit(obj)
        point = obj.data.splines[0].bezier_points[1]
        point.select_control_point = False
        point.select_left_handle = True
        point.select_right_handle = False
        expected_origin = obj.matrix_world @ point.co.copy()

        exporter.apply_modeling_origin(
            bpy.context,
            SimpleNamespace(modeling_origin_mode="SELECTION"),
        )

        self.assert_vectors_close([obj.matrix_world.translation], [expected_origin])
        self.assertEqual(exporter.modeling_origin_selection_label(bpy.context), "1 curve point selected")

    def test_origin_nurbs_selection_uses_xyz_ignores_hidden_and_preserves_weights(self):
        obj = make_curve_object(
            "NurbsOrigin",
            "NURBS",
            (
                (0.0, 1.0, 2.0, 0.5),
                (40.0, 50.0, 60.0, 3.0),
                (4.0, 5.0, 6.0, 2.0),
            ),
        )
        select_only_object_for_edit(obj)
        points = obj.data.splines[0].points
        for point in points:
            point.select = True
        points[1].hide = True
        expected_origin = obj.matrix_world @ Vector((2.0, 3.0, 4.0))
        before = curve_world_geometry(obj)
        weights_before = [point.co.w for point in points]

        exporter.apply_modeling_origin(
            bpy.context,
            SimpleNamespace(modeling_origin_mode="SELECTION"),
        )

        self.assert_vectors_close([obj.matrix_world.translation], [expected_origin])
        self.assert_vectors_close(curve_world_geometry(obj), before)
        weights_after = [point.co.w for point in obj.data.splines[0].points]
        self.assertEqual(weights_after, weights_before)

    def test_origin_curve_makes_shared_data_single_user_and_preserves_children(self):
        obj = make_curve_object(
            "SharedCurveOrigin",
            "POLY",
            ((0.0, 0.0, 0.0, 1.0), (2.0, 2.0, 0.0, 1.0)),
        )
        linked = bpy.data.objects.new("SharedCurveLinked", obj.data)
        linked.location = (10.0, 0.0, 0.0)
        bpy.context.scene.collection.objects.link(linked)
        child = bpy.data.objects.new("CurveChild", None)
        bpy.context.scene.collection.objects.link(child)
        child.parent = obj
        child.matrix_world = Matrix.Translation((8.0, 9.0, 10.0))
        bpy.context.view_layer.update()
        linked_before = curve_world_geometry(linked)
        child_before = child.matrix_world.copy()

        select_only_object_for_edit(obj)
        obj.data.splines[0].points[1].select = True
        exporter.apply_modeling_origin(
            bpy.context,
            SimpleNamespace(modeling_origin_mode="SELECTION"),
        )

        self.assertIsNot(obj.data, linked.data)
        self.assert_vectors_close(curve_world_geometry(linked), linked_before)
        self.assert_vectors_close(
            [child.matrix_world.translation],
            [child_before.translation],
        )

    def test_origin_curve_requires_a_visible_selected_point_and_bottom_stays_mesh_only(self):
        obj = make_curve_object(
            "EmptyCurveOrigin",
            "POLY",
            ((0.0, 0.0, 0.0, 1.0), (1.0, 0.0, 0.0, 1.0)),
        )
        select_only_object_for_edit(obj)
        with self.assertRaisesRegex(RuntimeError, "curve point"):
            exporter.apply_modeling_origin(
                bpy.context,
                SimpleNamespace(modeling_origin_mode="SELECTION"),
            )
        with self.assertRaisesRegex(RuntimeError, "mesh object"):
            exporter.apply_modeling_origin(
                bpy.context,
                SimpleNamespace(modeling_origin_mode="BOTTOM"),
            )

    def test_origin_mesh_selection_behavior_is_preserved(self):
        mesh = bpy.data.meshes.new("MeshOriginRegression_Mesh")
        mesh.from_pydata(((0.0, 0.0, 0.0), (4.0, 2.0, 1.0)), (), ())
        obj = bpy.data.objects.new("MeshOriginRegression", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world = Matrix.Translation((3.0, 5.0, 7.0))
        select_only_object_for_edit(obj)
        edit_mesh = bmesh.from_edit_mesh(mesh)
        edit_mesh.verts.ensure_lookup_table()
        for vertex in edit_mesh.verts:
            vertex.select = False
        edit_mesh.verts[1].select = True
        bmesh.update_edit_mesh(mesh)
        expected_origin = obj.matrix_world @ edit_mesh.verts[1].co.copy()
        before = [obj.matrix_world @ vertex.co.copy() for vertex in edit_mesh.verts]

        exporter.apply_modeling_origin(
            bpy.context,
            SimpleNamespace(modeling_origin_mode="SELECTION"),
        )

        self.assertEqual(bpy.context.mode, "EDIT_MESH")
        self.assert_vectors_close([obj.matrix_world.translation], [expected_origin])
        updated_mesh = bmesh.from_edit_mesh(obj.data)
        after = [obj.matrix_world @ vertex.co.copy() for vertex in updated_mesh.verts]
        self.assert_vectors_close(after, before)

    def test_icon_brightness_slider_range_does_not_cap_typed_value(self):
        for owner_type in (
            exporter.RRBuilderExportQueueItem,
            exporter.RRBuilderExportSettings,
        ):
            prop = owner_type.__annotations__["icon_light_brightness"]
            self.assertEqual(prop.keywords["min"], exporter.ICON_LIGHT_BRIGHTNESS_MIN)
            self.assertEqual(prop.keywords["soft_min"], exporter.ICON_LIGHT_BRIGHTNESS_MIN)
            self.assertEqual(prop.keywords["soft_max"], exporter.ICON_LIGHT_BRIGHTNESS_SOFT_MAX)
            self.assertNotIn("max", prop.keywords)

        key_spec = exporter.ICON_PREVIEW_LIGHT_SPECS[0]
        settings = SimpleNamespace(icon_light_brightness=25.0, icon_key_light_ratio=1.0)
        self.assertEqual(
            exporter.icon_light_energy(key_spec, settings),
            exporter.ICON_LIGHT_BASE_ENERGY * 25.0,
        )

    def test_builder_reference_cache_lives_in_library_outside_assets(self):
        expected_root = os.path.join(
            exporter.UNITY_PROJECT_ROOT,
            "Library",
            "RandomRealmBuilder",
        )
        expected_icon_cache = os.path.join(expected_root, "IconSources")
        self.assertEqual(
            os.path.normcase(os.path.normpath(exporter.UNITY_BUILDER_CACHE_ROOT)),
            os.path.normcase(os.path.normpath(expected_root)),
        )
        self.assertEqual(
            os.path.normcase(os.path.normpath(exporter.UNITY_BUILDER_ICON_SOURCE_CACHE)),
            os.path.normcase(os.path.normpath(expected_icon_cache)),
        )
        self.assertEqual(
            os.path.normcase(os.path.normpath(exporter.UNITY_BUILDER_REFERENCE_INDEX)),
            os.path.normcase(os.path.normpath(os.path.join(expected_root, "builder_reference_index.json"))),
        )

        first_icon = os.path.join(
            exporter.UNITY_TEMP_OUTPUT_ROOT,
            "Wall_200x20x200",
            "icon.png",
        )
        second_icon = os.path.join(
            exporter.UNITY_TEMP_OUTPUT_ROOT,
            "Wall_400x20x400",
            "icon.png",
        )
        first_cache_path = exporter.icon_outline_source_path(first_icon)
        self.assertEqual(
            os.path.normcase(os.path.normpath(os.path.dirname(first_cache_path))),
            os.path.normcase(os.path.normpath(expected_icon_cache)),
        )
        self.assertEqual(first_cache_path, exporter.icon_outline_source_path(first_icon))
        self.assertNotEqual(first_cache_path, exporter.icon_outline_source_path(second_icon))
        self.assertFalse(
            os.path.normcase(os.path.normpath(first_cache_path)).startswith(
                os.path.normcase(os.path.normpath(exporter.UNITY_TEMP_OUTPUT_ROOT)) + os.sep
            )
        )

    def test_renaming_same_asset_preserves_stable_identity_and_tracks_old_id(self):
        root, _, _, _ = make_quad("GlassWall_400x10x400")
        stable_id, previous_ids = exporter.snapshot_export_identity(root)
        self.assertTrue(stable_id.startswith("rr_asset_"))
        self.assertEqual(previous_ids, [])

        renamed = exporter.rename_export_asset_preserving_identity(
            root,
            "GlassWall_400x10x390",
        )
        renamed_stable_id, renamed_previous_ids = exporter.snapshot_export_identity(root)

        self.assertEqual(renamed, "GlassWall_400x10x390")
        self.assertEqual(renamed_stable_id, stable_id)
        self.assertEqual(renamed_previous_ids, ["GlassWall_400x10x400"])

    def test_duplicate_variant_action_clears_copied_export_identity(self):
        root = make_object_manager_asset_root(
            "InnerWall_Wood_Full_200x10x200",
            "assembly_original",
        )
        stable_id, _ = exporter.snapshot_export_identity(root)
        bpy.ops.object.select_all(action="DESELECT")
        root.select_set(True)
        bpy.context.view_layer.objects.active = root

        copied_root, copied_objects = exporter.duplicate_object_manager_group(
            bpy.context,
            root,
        )
        variants_root, _ = exporter.ensure_object_manager_variants_parent(
            bpy.context,
            root,
            copied_root,
        )

        self.assertEqual(root[exporter.EXPORT_STABLE_ID_PROP], stable_id)
        self.assertEqual(exporter.object_manager_assembly_type(variants_root), "VARIANTS")
        self.assertIn(copied_root, copied_objects)
        for copied in copied_objects:
            self.assertNotIn(exporter.EXPORT_STABLE_ID_PROP, copied)
            self.assertNotIn(exporter.EXPORT_LAST_ID_PROP, copied)
            self.assertNotIn(exporter.EXPORT_PREVIOUS_IDS_PROP, copied)

    def test_native_copy_with_shared_stable_id_is_rejected_before_manifest(self):
        root, _, _, _ = make_quad("NativeCopy_200x10x200")
        stable_id, _ = exporter.snapshot_export_identity(root)
        copied = root.copy()
        copied.data = root.data.copy()
        copied.name = "NativeCopyVariant_200x10x200"
        bpy.context.scene.collection.objects.link(copied)

        self.assertEqual(copied[exporter.EXPORT_STABLE_ID_PROP], stable_id)
        with self.assertRaises(RuntimeError) as validation_error:
            exporter.validate_export_identity(root)
        message = str(validation_error.exception)
        self.assertIn(stable_id, message)
        self.assertIn(root.name, message)
        self.assertIn(copied.name, message)

        with tempfile.TemporaryDirectory(prefix="rr_exporter_identity_") as asset_dir:
            output_root = os.path.join(asset_dir, "bridge")
            with self.assertRaises(RuntimeError):
                exporter.export_builder_asset(
                    root,
                    SimpleNamespace(output_root=output_root),
                    export_model=True,
                    include_icon=False,
                )
            self.assertFalse(os.path.exists(output_root))

            manifest_path = os.path.join(asset_dir, "manifest.json")
            with self.assertRaises(RuntimeError):
                exporter.write_manifest(
                    root,
                    manifest_path,
                    exporter.export_asset_id(root),
                    "Wall",
                    "",
                    "Default",
                    "",
                    "",
                    [],
                )
            self.assertFalse(os.path.exists(manifest_path))

    def test_sanitized_current_id_collision_is_rejected_before_files_or_queue(self):
        first, _, _, _ = make_quad("Wall A")
        second, _, _, _ = make_quad("Wall-A")
        self.assertEqual(exporter.export_asset_id(first), exporter.export_asset_id(second))

        queued = []
        original_queue = exporter.queue_unity_builder_import
        exporter.queue_unity_builder_import = lambda paths, **_kwargs: queued.extend(paths) or paths
        try:
            with tempfile.TemporaryDirectory(prefix="rr_export_id_collision_") as temp_root:
                output_root = os.path.join(temp_root, "bridge")
                with self.assertRaises(RuntimeError) as collision_error:
                    exporter.export_builder_asset(
                        first,
                        SimpleNamespace(output_root=output_root),
                        export_model=True,
                        include_icon=False,
                    )
                self.assertIn("current export ID", str(collision_error.exception))
                self.assertIn(first.name, str(collision_error.exception))
                self.assertIn(second.name, str(collision_error.exception))
                self.assertFalse(os.path.exists(output_root))
                self.assertEqual(queued, [])
        finally:
            exporter.queue_unity_builder_import = original_queue

    def test_variant_previous_id_cannot_claim_live_sibling_id(self):
        first = make_object_manager_asset_root(
            "InnerWall_Wood_Low_200x10x190",
            "assembly_low",
        )
        second = make_object_manager_asset_root(
            "InnerWall_Wood_Full_200x10x200",
            "assembly_full",
        )
        variants_root = bpy.data.objects.new("InnerWall_Wood_Variants", None)
        bpy.context.scene.collection.objects.link(variants_root)
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_variants"
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = "InnerWall_Wood_Variants"
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
        for member in (first, second):
            member.parent = variants_root
            member[exporter.OBJECT_MANAGER_PARENT_ASSEMBLY_ROOT_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_PARENT_ASSEMBLY_ID_PROP] = "assembly_variants"
            exporter.snapshot_export_identity(member)

        first[exporter.EXPORT_PREVIOUS_IDS_PROP] = json.dumps(
            [exporter.export_asset_id(second)]
        )
        with self.assertRaises(RuntimeError) as validation_error:
            exporter.validate_export_identity(first)

        message = str(validation_error.exception)
        self.assertIn("previous ID", message)
        self.assertIn(first.name, message)
        self.assertIn(second.name, message)

    def test_variant_manifest_has_complete_deterministic_membership_certificate(self):
        first, _, _, _ = make_quad("GlassWall_400x10x390")
        second, _, _, _ = make_quad("GlassWall_400x10x400")
        variants_root = bpy.data.objects.new("GlassWall_Variants", None)
        bpy.context.scene.collection.objects.link(variants_root)
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_glass_variants"
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = variants_root.name
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
        for member in (first, second):
            member.parent = variants_root
            member[exporter.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_glass_variants"
            member[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"

        first_group = exporter.build_group_manifest(first)
        second_group = exporter.build_group_manifest(second)
        identity_lines = sorted(
            f"{exporter.export_asset_id(member).strip().lower()}\t"
            f"{member[exporter.EXPORT_STABLE_ID_PROP].strip().lower()}"
            for member in (first, second)
        )
        expected_revision = hashlib.sha256(
            "\n".join(identity_lines).encode("utf-8")
        ).hexdigest()

        for group in (first_group, second_group):
            self.assertEqual(group["members"], [first.name, second.name])
            self.assertEqual(
                group["membershipContractVersion"],
                exporter.VARIANT_MEMBERSHIP_CONTRACT_VERSION,
            )
            self.assertTrue(group["membershipComplete"])
            self.assertEqual(group["membershipRevision"], expected_revision)

        reversed_revision = exporter.variant_membership_revision(
            [
                (second.name.upper(), second[exporter.EXPORT_STABLE_ID_PROP].upper()),
                (first.name, first[exporter.EXPORT_STABLE_ID_PROP]),
            ]
        )
        self.assertEqual(reversed_revision, expected_revision)
        for invalid_pairs in (
            [("", first[exporter.EXPORT_STABLE_ID_PROP])],
            [("Wall A", first[exporter.EXPORT_STABLE_ID_PROP])],
            [(first.name, "rr_asset_非ascii")],
        ):
            with self.assertRaises(RuntimeError):
                exporter.variant_membership_revision(invalid_pairs)

        with tempfile.TemporaryDirectory(prefix="rr_variant_membership_") as output_root:
            manifest_path = os.path.join(output_root, first.name, "manifest.json")
            exporter.write_manifest(
                first,
                manifest_path,
                first.name,
                "InnerWall",
                "",
                "Default",
                "",
                "",
                [],
                group_manifest=first_group,
            )
            written = exporter.read_existing_manifest(manifest_path)
            self.assertEqual(written["schemaVersion"], 1)
            self.assertEqual(written["group"]["membershipRevision"], expected_revision)
            self.assertTrue(written["group"]["membershipComplete"])

    def test_variant_staging_failure_and_publish_rollback_preserve_formal_packages(self):
        first, _, _, _ = make_quad("GlassWall_400x10x390")
        second, _, _, _ = make_quad("GlassWall_400x10x400")
        variants_root = bpy.data.objects.new("GlassWall_Variants", None)
        bpy.context.scene.collection.objects.link(variants_root)
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_glass_variants"
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = variants_root.name
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
        for member in (first, second):
            member.parent = variants_root
            member[exporter.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_glass_variants"
            member[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
            exporter.snapshot_export_identity(member)

        roots = exporter.expand_related_export_roots([first])
        self.assertEqual(roots, [first, second])
        with tempfile.TemporaryDirectory(prefix="rr_variant_publication_") as output_root:
            isolated_cache_root = self.isolate_variant_claim_root(output_root)
            settings = SimpleNamespace(output_root=output_root)
            old_timestamp = 1_700_000_000_123_456_700
            for index, member in enumerate(roots):
                member_folder = os.path.join(output_root, exporter.export_asset_id(member))
                os.makedirs(member_folder, exist_ok=True)
                for file_name, content in (
                    ("manifest.json", f"old manifest {index}".encode("ascii")),
                    ("model.fbx", f"old model {index}".encode("ascii")),
                ):
                    path = os.path.join(member_folder, file_name)
                    with open(path, "wb") as handle:
                        handle.write(content)
                    os.utime(path, ns=(old_timestamp + index, old_timestamp + index))

            def capture_formal_files():
                captured = {}
                for member in roots:
                    member_id = exporter.export_asset_id(member)
                    for file_name in ("manifest.json", "model.fbx"):
                        path = os.path.join(output_root, member_id, file_name)
                        with open(path, "rb") as handle:
                            content = handle.read()
                        captured[(member_id, file_name)] = (
                            content,
                            os.stat(path).st_mtime_ns,
                        )
                return captured

            def write_certified_staging(staging_root, marker):
                revisions = []
                for member in roots:
                    member_id = exporter.export_asset_id(member)
                    member_folder = os.path.join(staging_root, member_id)
                    os.makedirs(member_folder, exist_ok=True)
                    model_path = os.path.join(member_folder, "model.fbx")
                    with open(model_path, "wb") as handle:
                        handle.write(f"{marker}:{member_id}".encode("ascii"))
                    group = exporter.build_group_manifest(member)
                    revisions.append(group["membershipRevision"])
                    exporter.write_manifest(
                        member,
                        os.path.join(member_folder, "manifest.json"),
                        member_id,
                        "InnerWall",
                        "",
                        "Default",
                        "model.fbx",
                        "",
                        ["model"],
                        group_manifest=group,
                    )
                self.assertEqual(len(set(revisions)), 1)
                return revisions[0]

            before = capture_formal_files()
            transactions, _by_member, failures = exporter.prepare_variant_export_transactions(
                roots,
                settings,
            )
            self.assertEqual(failures, [])
            partial_stage = transactions[0]["staging_root"]
            first_stage_folder = os.path.join(partial_stage, exporter.export_asset_id(first))
            with open(os.path.join(first_stage_folder, "model.fbx"), "wb") as handle:
                handle.write(b"partial new model")
            with open(os.path.join(first_stage_folder, "manifest.json"), "wb") as handle:
                handle.write(b"partial new manifest")

            queued = []
            original_queue = exporter.queue_unity_builder_import
            exporter.queue_unity_builder_import = lambda paths, **_kwargs: queued.extend(paths) or paths
            try:
                published_names, publish_failures = exporter.finalize_variant_export_transactions(
                    transactions,
                    {first.name},
                )
            finally:
                exporter.queue_unity_builder_import = original_queue
            self.assertEqual(published_names, set())
            self.assertEqual(publish_failures, [])
            self.assertEqual(queued, [])
            self.assertFalse(os.path.exists(partial_stage))
            self.assertEqual(capture_formal_files(), before)

            transactions, _by_member, failures = exporter.prepare_variant_export_transactions(
                roots,
                settings,
            )
            self.assertEqual(failures, [])
            rollback_stage = transactions[0]["staging_root"]
            rollback_revision = write_certified_staging(rollback_stage, "rollback-new")
            rollback_queue = []
            claim_path = exporter.variant_publish_claim_path(
                output_root,
                "assembly_glass_variants",
            )

            def fail_mid_publish(index, _member_id, _target_folder):
                if index == 0:
                    self.assertTrue(os.path.isfile(claim_path))
                    with open(claim_path, "r", encoding="utf-8") as handle:
                        claim = json.load(handle)
                    self.assertEqual(
                        claim["claimContractVersion"],
                        exporter.VARIANT_PUBLISH_CLAIM_CONTRACT_VERSION,
                    )
                    self.assertEqual(claim["state"], "incomplete")
                    self.assertEqual(claim["groupId"], "assembly_glass_variants")
                    self.assertEqual(claim["membershipRevision"], rollback_revision)
                    self.assertEqual(
                        claim["members"],
                        [exporter.export_asset_id(member) for member in roots],
                    )
                    self.assertTrue(claim["writerLeaseId"])
                    self.assertFalse(
                        os.path.exists(
                            exporter.variant_publish_gate_path(
                                output_root,
                                "assembly_glass_variants",
                            )
                        )
                    )
                    raise RuntimeError("simulated directory publish failure")

            with self.assertRaises(RuntimeError):
                exporter.publish_staged_variant_group(
                    rollback_stage,
                    output_root,
                    roots,
                    queue_callback=lambda paths: rollback_queue.extend(paths),
                    after_member_published=fail_mid_publish,
                )
            self.assertEqual(rollback_queue, [])
            self.assertFalse(os.path.exists(rollback_stage))
            self.assertFalse(os.path.exists(claim_path))
            self.assertEqual(capture_formal_files(), before)

            transactions, _by_member, failures = exporter.prepare_variant_export_transactions(
                roots,
                settings,
            )
            self.assertEqual(failures, [])
            blocked_stage = transactions[0]["staging_root"]
            blocked_revision = write_certified_staging(blocked_stage, "blocked-new")
            claim_path, writer_lease_id = exporter.create_variant_publish_claim(
                output_root,
                "assembly_glass_variants",
                blocked_revision,
                [exporter.export_asset_id(member) for member in roots],
            )
            blocked_queue = []
            with self.assertRaises(FileExistsError):
                exporter.publish_staged_variant_group(
                    blocked_stage,
                    output_root,
                    roots,
                    queue_callback=lambda paths: blocked_queue.extend(paths),
                )
            self.assertEqual(blocked_queue, [])
            self.assertFalse(os.path.exists(blocked_stage))
            self.assertTrue(os.path.isfile(claim_path))
            self.assertEqual(capture_formal_files(), before)
            exporter.remove_variant_publish_claim(
                output_root,
                "assembly_glass_variants",
                claim_path,
                writer_lease_id,
            )

            transactions, _by_member, failures = exporter.prepare_variant_export_transactions(
                roots,
                settings,
            )
            self.assertEqual(failures, [])
            reader_blocked_stage = transactions[0]["staging_root"]
            write_certified_staging(reader_blocked_stage, "reader-blocked-new")
            reader_lease_path = exporter.variant_publish_reader_lease_path(
                output_root,
                "assembly_glass_variants",
                "reader_test",
            )
            exporter.write_exclusive_json(
                reader_lease_path,
                {
                    "readerLeaseContractVersion": exporter.VARIANT_READER_LEASE_CONTRACT_VERSION,
                    "groupId": "assembly_glass_variants",
                    "readerLeaseId": "reader_test",
                    "processId": os.getpid(),
                },
            )
            reader_blocked_queue = []
            try:
                with self.assertRaises(RuntimeError):
                    exporter.publish_staged_variant_group(
                        reader_blocked_stage,
                        output_root,
                        roots,
                        queue_callback=lambda paths: reader_blocked_queue.extend(paths),
                    )
                self.assertEqual(reader_blocked_queue, [])
                self.assertFalse(os.path.exists(reader_blocked_stage))
                self.assertTrue(os.path.isfile(reader_lease_path))
                self.assertFalse(os.path.exists(claim_path))
                self.assertEqual(capture_formal_files(), before)
            finally:
                if os.path.exists(reader_lease_path):
                    os.remove(reader_lease_path)
                reader_directory = os.path.dirname(reader_lease_path)
                if os.path.isdir(reader_directory) and not os.listdir(reader_directory):
                    os.rmdir(reader_directory)

            transactions, _by_member, failures = exporter.prepare_variant_export_transactions(
                roots,
                settings,
            )
            self.assertEqual(failures, [])
            gate_blocked_stage = transactions[0]["staging_root"]
            write_certified_staging(gate_blocked_stage, "gate-blocked-new")
            gate_path = exporter.variant_publish_gate_path(
                output_root,
                "assembly_glass_variants",
            )
            exporter.write_exclusive_json(
                gate_path,
                {
                    "gateContractVersion": exporter.VARIANT_PUBLISH_GATE_CONTRACT_VERSION,
                    "role": "reader",
                    "groupId": "assembly_glass_variants",
                    "gateLeaseId": "foreign_reader_gate",
                    "processId": os.getpid(),
                },
            )
            gate_blocked_queue = []
            try:
                with self.assertRaises(FileExistsError):
                    exporter.publish_staged_variant_group(
                        gate_blocked_stage,
                        output_root,
                        roots,
                        queue_callback=lambda paths: gate_blocked_queue.extend(paths),
                    )
                self.assertEqual(gate_blocked_queue, [])
                self.assertFalse(os.path.exists(gate_blocked_stage))
                self.assertTrue(os.path.isfile(gate_path))
                self.assertFalse(os.path.exists(claim_path))
                self.assertEqual(capture_formal_files(), before)
            finally:
                if os.path.exists(gate_path):
                    os.remove(gate_path)

            expected_claim_root = os.path.join(
                isolated_cache_root,
                "ExportTransactions",
                "Claims",
            )
            for coordination_path in (
                claim_path,
                gate_path,
                exporter.variant_publish_reader_directory(
                    output_root,
                    "assembly_glass_variants",
                ),
            ):
                self.assertEqual(
                    os.path.commonpath(
                        [
                            os.path.normcase(expected_claim_root),
                            os.path.normcase(coordination_path),
                        ]
                    ),
                    os.path.normcase(expected_claim_root),
                )
                self.assertNotEqual(
                    os.path.commonpath(
                        [
                            os.path.normcase(output_root),
                            os.path.normcase(coordination_path),
                        ]
                    ),
                    os.path.normcase(output_root),
                )

    def test_successful_variant_publish_commits_one_revision_before_queue(self):
        first, _, _, _ = make_quad("GlassWall_400x10x390")
        second, _, _, _ = make_quad("GlassWall_400x10x400")
        variants_root = bpy.data.objects.new("GlassWall_Variants", None)
        bpy.context.scene.collection.objects.link(variants_root)
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_glass_variants"
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = variants_root.name
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
        for member in (first, second):
            member.parent = variants_root
            member[exporter.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_glass_variants"
            member[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
            exporter.snapshot_export_identity(member)
        roots = exporter.expand_related_export_roots([first])

        with tempfile.TemporaryDirectory(prefix="rr_variant_commit_") as output_root:
            self.isolate_variant_claim_root(output_root)
            settings = SimpleNamespace(output_root=output_root)
            transactions, _by_member, failures = exporter.prepare_variant_export_transactions(
                roots,
                settings,
            )
            self.assertEqual(failures, [])
            staging_root = transactions[0]["staging_root"]
            staged_revision = None
            for member in roots:
                member_id = exporter.export_asset_id(member)
                member_folder = os.path.join(staging_root, member_id)
                os.makedirs(member_folder, exist_ok=True)
                with open(os.path.join(member_folder, "model.fbx"), "wb") as handle:
                    handle.write(f"new:{member_id}".encode("ascii"))
                group = exporter.build_group_manifest(member)
                staged_revision = staged_revision or group["membershipRevision"]
                self.assertEqual(group["membershipRevision"], staged_revision)
                exporter.write_manifest(
                    member,
                    os.path.join(member_folder, "manifest.json"),
                    member_id,
                    "InnerWall",
                    "",
                    "Default",
                    "model.fbx",
                    "",
                    ["model"],
                    group_manifest=group,
                )

            queue_observations = []

            def observe_queue(paths):
                paths = list(paths)
                claim_path = exporter.variant_publish_claim_path(
                    output_root,
                    "assembly_glass_variants",
                )
                self.assertFalse(
                    os.path.exists(claim_path),
                    "The complete formal revision must be unlocked before Unity is queued.",
                )
                self.assertFalse(
                    os.path.exists(
                        exporter.variant_publish_gate_path(
                            output_root,
                            "assembly_glass_variants",
                        )
                    ),
                    "The writer gate must be released before Unity is queued.",
                )
                revisions = []
                for member in roots:
                    member_id = exporter.export_asset_id(member)
                    formal_manifest = os.path.join(output_root, member_id, "manifest.json")
                    self.assertIn(os.path.normpath(formal_manifest), paths)
                    manifest = exporter.read_existing_manifest(formal_manifest)
                    revisions.append(manifest["group"]["membershipRevision"])
                    with open(os.path.join(output_root, member_id, "model.fbx"), "rb") as handle:
                        self.assertEqual(handle.read(), f"new:{member_id}".encode("ascii"))
                self.assertEqual(set(revisions), {staged_revision})
                queue_observations.append(paths)
                return paths

            manifest_paths, published_revision = exporter.publish_staged_variant_group(
                staging_root,
                output_root,
                roots,
                queue_callback=observe_queue,
            )
            self.assertEqual(published_revision, staged_revision)
            self.assertEqual(queue_observations, [manifest_paths])
            self.assertFalse(os.path.exists(staging_root))

        self.assertIs(
            inspect.signature(exporter.export_builder_asset)
            .parameters["queue_import"]
            .default,
            True,
        )
        export_objects_source = inspect.getsource(exporter.export_objects)
        export_queue_source = inspect.getsource(exporter.RR_OT_export_queue.execute)
        render_icons_source = inspect.getsource(exporter.render_icon_objects)
        for source in (export_objects_source, export_queue_source):
            self.assertIn("queue_import=False", source)
        for source in (export_objects_source, export_queue_source, render_icons_source):
            self.assertIn("prepare_variant_export_transactions", source)
            self.assertIn("finalize_variant_export_transactions", source)

    def test_last_rendered_variant_member_becomes_the_shared_group_icon_source(self):
        first, _, _, _ = make_quad("GlassWall_400x10x390")
        second, _, _, _ = make_quad("GlassWall_400x10x400")
        variants_root = bpy.data.objects.new("GlassWall_400x10x400_Variants", None)
        bpy.context.scene.collection.objects.link(variants_root)
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_glass_variants"
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = variants_root.name
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
        variants_root[exporter.OBJECT_MANAGER_ASSEMBLY_ACTIVE_MEMBER_PROP] = second.name
        for member in (first, second):
            member.parent = variants_root
            member[exporter.OBJECT_MANAGER_ASSEMBLY_MEMBER_ROOT_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_glass_variants"
            member[exporter.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = variants_root.name
            member[exporter.OBJECT_MANAGER_ASSEMBLY_TYPE_PROP] = "VARIANTS"
            exporter.snapshot_export_identity(member)

        bpy.ops.object.select_all(action="DESELECT")
        second.select_set(True)
        bpy.context.view_layer.objects.active = second
        self.assertEqual(exporter.get_context_export_roots(bpy.context), [variants_root])

        with tempfile.TemporaryDirectory(prefix="rr_variant_group_icon_") as output_root:
            settings = SimpleNamespace(
                output_root=output_root,
                profile_name="Default",
                icon_resolution=64,
                icon_zoom=1.0,
                icon_offset_x=0.0,
                icon_offset_y=0.0,
                icon_view_yaw=0.0,
                icon_view_pitch=0.0,
                icon_light_brightness=1.0,
                icon_key_light_ratio=1.0,
                icon_fill_light_ratio=0.5,
                icon_back_light_ratio=0.5,
                icon_outline_enabled=False,
                icon_outline_color=(1.0, 1.0, 1.0, 1.0),
                icon_outline_pixels=2,
                export_queue=[],
                preview_image_path="",
                preview_image=None,
            )
            render_calls = []
            queued_batches = []
            source_pixels = {}
            fail_after_write = {"root_name": ""}
            original_render = exporter.render_or_copy_shared_icon
            original_queue = exporter.queue_unity_builder_import

            def fake_render(root, _settings, destination, shared_icon_root=None, force_render=False, **_kwargs):
                source = shared_icon_root or root
                render_calls.append((root.name, source.name, force_render))
                if force_render:
                    source_pixels[source.name] = ("rendered:" + source.name).encode("utf-8")
                content = source_pixels[source.name]
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with open(destination, "wb") as handle:
                    handle.write(content)
                source_path = os.path.join(_settings.output_root, exporter.export_asset_id(source), "icon.png")
                os.makedirs(os.path.dirname(source_path), exist_ok=True)
                with open(source_path, "wb") as handle:
                    handle.write(content)
                if root.name == fail_after_write["root_name"]:
                    raise RuntimeError("simulated member copy failure")
                return destination

            def record_queue(paths, **_kwargs):
                paths = list(paths)
                if paths:
                    queued_batches.append(paths)
                return paths

            exporter.render_or_copy_shared_icon = fake_render
            exporter.queue_unity_builder_import = record_queue
            try:
                result = exporter.render_icon_objects(
                    exporter.get_context_export_roots(bpy.context),
                    settings,
                    bpy.context,
                    "selected",
                )
                self.assertEqual(result, {"FINISHED"})
                self.assertEqual(
                    render_calls,
                    [
                        (first.name, second.name, True),
                    ],
                )
                first_icon = os.path.join(output_root, exporter.export_asset_id(first), "icon.png")
                second_icon = os.path.join(output_root, exporter.export_asset_id(second), "icon.png")
                with open(first_icon, "rb") as handle:
                    first_bytes = handle.read()
                with open(second_icon, "rb") as handle:
                    second_bytes = handle.read()
                self.assertEqual(first_bytes, second_bytes)
                self.assertEqual(second_bytes, b"rendered:GlassWall_400x10x400")
                self.assertEqual(
                    variants_root[exporter.OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP],
                    second.name,
                )
                self.assertEqual(
                    variants_root[exporter.OBJECT_MANAGER_VARIANT_ICON_SOURCE_STABLE_ID_PROP],
                    second[exporter.EXPORT_STABLE_ID_PROP],
                )
                self.assertEqual(exporter.shared_builder_icon_root(first), second)
                self.assertEqual(len(queued_batches), 1)
                self.assertEqual(len(queued_batches[0]), 2)
                for member in (first, second):
                    manifest_path = os.path.join(output_root, exporter.export_asset_id(member), "manifest.json")
                    manifest = exporter.read_existing_manifest(manifest_path)
                    self.assertEqual(manifest["group"]["iconSourceMemberId"], exporter.export_asset_id(second))
                    self.assertEqual(manifest["group"]["iconSourceStableId"], second[exporter.EXPORT_STABLE_ID_PROP])

                bpy.ops.object.select_all(action="DESELECT")
                first.select_set(True)
                bpy.context.view_layer.objects.active = first
                self.assertEqual(
                    exporter.shared_builder_icon_root(first),
                    second,
                    "Ordinary selection must not replace the last successfully rendered source.",
                )

                fail_after_write["root_name"] = first.name
                render_calls.clear()
                queued_batches.clear()
                result = exporter.render_icon_objects(
                    exporter.get_context_export_roots(bpy.context),
                    settings,
                    bpy.context,
                    "selected",
                )
                self.assertEqual(result, {"CANCELLED"})
                self.assertFalse(queued_batches)
                with open(first_icon, "rb") as handle:
                    first_bytes = handle.read()
                with open(second_icon, "rb") as handle:
                    second_bytes = handle.read()
                self.assertEqual(first_bytes, b"rendered:GlassWall_400x10x400")
                self.assertEqual(second_bytes, first_bytes)
                self.assertEqual(
                    variants_root[exporter.OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP],
                    second.name,
                    "A partial Variant render must not replace the last successful source.",
                )

                fail_after_write["root_name"] = ""
                render_calls.clear()
                queued_batches.clear()
                result = exporter.render_icon_objects(
                    exporter.get_context_export_roots(bpy.context),
                    settings,
                    bpy.context,
                    "selected",
                )
                self.assertEqual(result, {"FINISHED"})
                self.assertEqual(
                    render_calls,
                    [
                        (first.name, first.name, True),
                        (second.name, first.name, False),
                    ],
                )
                with open(first_icon, "rb") as handle:
                    first_bytes = handle.read()
                with open(second_icon, "rb") as handle:
                    second_bytes = handle.read()
                self.assertEqual(first_bytes, second_bytes)
                self.assertEqual(first_bytes, b"rendered:GlassWall_400x10x390")
                self.assertEqual(
                    variants_root[exporter.OBJECT_MANAGER_VARIANT_ICON_SOURCE_NAME_PROP],
                    first.name,
                )
            finally:
                exporter.render_or_copy_shared_icon = original_render
                exporter.queue_unity_builder_import = original_queue

    def test_innerwall_pair_renders_its_shared_low_icon_only_once(self):
        low, _, _, _ = make_quad("InnerWall_Wood_Low_200x10x190")
        full, _, _, _ = make_quad("InnerWall_Wood_Full_200x10x200")
        exporter.snapshot_export_identity(low)
        exporter.snapshot_export_identity(full)

        with tempfile.TemporaryDirectory(prefix="rr_innerwall_shared_icon_") as output_root:
            settings = SimpleNamespace(
                output_root=output_root,
                profile_name="Default",
                icon_resolution=64,
                icon_zoom=1.0,
                icon_offset_x=0.0,
                icon_offset_y=0.0,
                icon_view_yaw=0.0,
                icon_view_pitch=0.0,
                icon_light_brightness=1.0,
                icon_key_light_ratio=1.0,
                icon_fill_light_ratio=0.5,
                icon_back_light_ratio=0.5,
                icon_outline_enabled=False,
                icon_outline_color=(1.0, 1.0, 1.0, 1.0),
                icon_outline_pixels=2,
                export_queue=[],
                preview_image_path="",
                preview_image=None,
            )
            calls = []
            source_pixels = {}
            original_render = exporter.render_or_copy_shared_icon
            original_queue = exporter.queue_unity_builder_import

            def fake_render(root, _settings, destination, shared_icon_root=None, force_render=False, **_kwargs):
                source = shared_icon_root or root
                calls.append((root.name, source.name, force_render))
                if root == source or force_render:
                    source_pixels[source.name] = ("rendered:" + source.name).encode("utf-8")
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                with open(destination, "wb") as handle:
                    handle.write(source_pixels[source.name])
                return destination

            exporter.render_or_copy_shared_icon = fake_render
            exporter.queue_unity_builder_import = lambda paths, **_kwargs: list(paths)
            try:
                result = exporter.render_icon_objects([full], settings, bpy.context, "selected")
            finally:
                exporter.render_or_copy_shared_icon = original_render
                exporter.queue_unity_builder_import = original_queue

            self.assertEqual(result, {"FINISHED"})
            self.assertEqual(
                calls,
                [
                    (low.name, low.name, False),
                    (full.name, low.name, False),
                ],
            )
            for member in (low, full):
                icon_path = os.path.join(output_root, exporter.export_asset_id(member), "icon.png")
                with open(icon_path, "rb") as handle:
                    self.assertEqual(handle.read(), ("rendered:" + low.name).encode("utf-8"))

    def test_mapping_bakes_directly_to_uv0_and_restores_source(self):
        obj, uv_map, detail_uv, mapping = make_quad("ContractQuad", include_detail_uv=True)
        shared = bpy.data.objects.new("ContractQuadShared", obj.data)
        bpy.context.scene.collection.objects.link(shared)

        original_uv0 = uv_values(uv_map)
        original_detail = uv_values(detail_uv)
        original_rotation = tuple(mapping.inputs["Rotation"].default_value)
        self.assertEqual(obj.data.uv_layers.active.name, "DetailUV")
        self.assertTrue(uv_map.active_render)

        restores, warnings = rr_unity_uv_export.prepare_unity_uvs_for_export(
            obj,
            [obj, shared],
            exporter.principled_base_image_node,
        )
        self.assertFalse(warnings)
        self.assertEqual([layer.name for layer in obj.data.uv_layers], ["UVMap", "DetailUV"])
        self.assertEqual(obj.data.uv_layers.active.name, "UVMap")
        self.assertTrue(uv_map.active_render)
        self.assert_uvs(
            uv_values(uv_map),
            [(0.0, 0.0), (0.0, 1.0), (-1.0, 1.0), (-1.0, 0.0)],
        )
        self.assertAlmostEqual(mapping.inputs["Rotation"].default_value[2], 0.0, places=6)

        cleanup_errors = rr_unity_uv_export.restore_actions_best_effort(restores, "contract test")
        self.assertFalse(cleanup_errors)
        self.assertEqual(obj.data.uv_layers.active.name, "DetailUV")
        self.assertTrue(uv_map.active_render)
        self.assertEqual(uv_values(uv_map), original_uv0)
        self.assertEqual(uv_values(detail_uv), original_detail)
        self.assertEqual(tuple(mapping.inputs["Rotation"].default_value), original_rotation)

    def test_unconnected_image_texture_uses_active_render_uv(self):
        obj, uv_map, detail_uv, _ = make_quad(
            "UnconnectedImageVector",
            include_detail_uv=True,
        )
        material = obj.material_slots[0].material
        image_node = exporter.principled_base_image_node(material)
        for link in list(image_node.inputs["Vector"].links):
            material.node_tree.links.remove(link)
        uv_map.active_render = False
        detail_uv.active_render = True

        original_uv0 = uv_values(uv_map)
        expected_baked = uv_values(detail_uv)
        restores, warnings = rr_unity_uv_export.prepare_unity_uvs_for_export(
            obj,
            [obj],
            exporter.principled_base_image_node,
        )
        self.assertFalse(warnings)
        self.assertEqual(uv_values(uv_map), expected_baked)

        cleanup_errors = rr_unity_uv_export.restore_actions_best_effort(
            restores,
            "unconnected image contract test",
        )
        self.assertFalse(cleanup_errors)
        self.assertEqual(uv_values(uv_map), original_uv0)
        self.assertTrue(detail_uv.active_render)

    def test_custom_object_coordinates_are_rejected_without_mutation(self):
        obj, uv_map, _, mapping = make_quad("CustomObjectCoordinates")
        material = obj.material_slots[0].material
        texture_coordinate = next(
            node
            for node in material.node_tree.nodes
            if node.bl_idname == "ShaderNodeTexCoord"
        )
        target = bpy.data.objects.new("CoordinateTarget", None)
        bpy.context.scene.collection.objects.link(target)
        texture_coordinate.object = target
        vector_input = mapping.inputs["Vector"]
        for link in list(vector_input.links):
            material.node_tree.links.remove(link)
        material.node_tree.links.new(texture_coordinate.outputs["Object"], vector_input)

        original_uv0 = uv_values(uv_map)
        original_rotation = tuple(mapping.inputs["Rotation"].default_value)
        with self.assertRaises(rr_unity_uv_export.UVExportContractError):
            rr_unity_uv_export.prepare_unity_uvs_for_export(
                obj,
                [obj],
                exporter.principled_base_image_node,
            )
        self.assertEqual(uv_values(uv_map), original_uv0)
        self.assertEqual(tuple(mapping.inputs["Rotation"].default_value), original_rotation)

    def test_unconnected_mapping_input_is_rejected_without_mutation(self):
        obj, uv_map, _, mapping = make_quad("UnconnectedMappingInput")
        for link in list(mapping.inputs["Vector"].links):
            obj.material_slots[0].material.node_tree.links.remove(link)

        original_uv0 = uv_values(uv_map)
        original_rotation = tuple(mapping.inputs["Rotation"].default_value)
        with self.assertRaises(rr_unity_uv_export.UVExportContractError):
            rr_unity_uv_export.prepare_unity_uvs_for_export(
                obj,
                [obj],
                exporter.principled_base_image_node,
            )
        self.assertEqual(uv_values(uv_map), original_uv0)
        self.assertEqual(tuple(mapping.inputs["Rotation"].default_value), original_rotation)

    def test_shared_mesh_with_conflicting_material_overrides_is_rejected_without_mutation(self):
        obj, uv_map, _, mapping = make_quad("ConflictingSharedMesh")
        shared = bpy.data.objects.new("ConflictingSharedMeshOther", obj.data)
        bpy.context.scene.collection.objects.link(shared)
        alternate_material, _ = make_material("AlternateMaterial", rotation_degrees=0.0)
        shared.material_slots[0].link = "OBJECT"
        shared.material_slots[0].material = alternate_material

        original_uv0 = uv_values(uv_map)
        original_rotation = tuple(mapping.inputs["Rotation"].default_value)
        with self.assertRaises(rr_unity_uv_export.UVExportContractError):
            rr_unity_uv_export.prepare_unity_uvs_for_export(
                obj,
                [obj, shared],
                exporter.principled_base_image_node,
            )
        self.assertEqual(uv_values(uv_map), original_uv0)
        self.assertEqual(tuple(mapping.inputs["Rotation"].default_value), original_rotation)

    def test_material_link_prepare_rolls_back_its_own_exception(self):
        obj, _, _, _ = make_quad("MaterialLinkRollback")
        material = obj.material_slots[0].material
        principled = exporter.find_principled_node(material)
        base_socket = principled.inputs["Base Color"]
        for link in list(base_socket.links):
            material.node_tree.links.remove(link)
        rgb = material.node_tree.nodes.new("ShaderNodeRGB")
        material.node_tree.links.new(rgb.outputs["Color"], base_socket)

        original_replace = exporter.replace_socket_links_temporarily

        def replace_then_fail(*args, **kwargs):
            original_replace(*args, **kwargs)
            raise RuntimeError("synthetic material-link failure")

        exporter.replace_socket_links_temporarily = replace_then_fail
        try:
            with self.assertRaisesRegex(RuntimeError, "synthetic material-link failure"):
                exporter.prepare_material_maps_for_unity(obj)
        finally:
            exporter.replace_socket_links_temporarily = original_replace

        self.assertEqual(len(base_socket.links), 1)
        self.assertEqual(
            base_socket.links[0].from_node.as_pointer(),
            rgb.as_pointer(),
        )

    def test_existing_model_skip_requires_matching_contract_hash(self):
        obj, _, _, _ = make_quad("ContractHash")
        with tempfile.TemporaryDirectory(prefix="rr_exporter_manifest_") as asset_dir:
            model_path = os.path.join(asset_dir, "model.fbx")
            manifest_path = os.path.join(asset_dir, "manifest.json")
            with open(model_path, "wb") as handle:
                handle.write(b"current model")
            contract = exporter.current_uv_export_contract(model_path)
            exporter.write_manifest(
                obj,
                manifest_path,
                "ContractHash",
                "Cube",
                "",
                "Default",
                "model.fbx",
                "",
                ["model"],
                uv_export_contract=contract,
            )
            self.assertTrue(
                exporter.output_has_requested_resources(
                    asset_dir,
                    export_model=True,
                    include_icon=False,
                )
            )

            existing_manifest = exporter.read_existing_manifest(manifest_path)
            with open(model_path, "rb") as handle:
                before_model_bytes = handle.read()
            preserved_contract = exporter.existing_uv_export_contract(
                existing_manifest,
                model_path,
            )
            exporter.write_manifest(
                obj,
                manifest_path,
                "ContractHash",
                "Cube",
                "",
                "Default",
                "model.fbx",
                "icon.png",
                ["icon"],
                uv_export_contract=preserved_contract,
            )
            icon_only_manifest = exporter.read_existing_manifest(manifest_path)
            self.assertEqual(icon_only_manifest["uvExport"], contract)
            with open(model_path, "rb") as handle:
                self.assertEqual(handle.read(), before_model_bytes)

            with open(model_path, "wb") as handle:
                handle.write(b"stale or replaced model")
            self.assertFalse(
                exporter.output_has_requested_resources(
                    asset_dir,
                    export_model=True,
                    include_icon=False,
                )
            )

    def test_skip_existing_reuses_model_but_always_renders_requested_icon(self):
        obj, _, _, _ = make_quad("Cube_200x200x200_Wood")
        with tempfile.TemporaryDirectory(prefix="rr_exporter_icon_refresh_") as output_root:
            asset_id = exporter.export_asset_id(obj)
            asset_dir = os.path.join(output_root, asset_id)
            os.makedirs(asset_dir, exist_ok=True)
            model_path = os.path.join(asset_dir, "model.fbx")
            icon_path = os.path.join(asset_dir, "icon.png")
            manifest_path = os.path.join(asset_dir, "manifest.json")
            with open(model_path, "wb") as handle:
                handle.write(b"verified model")
            with open(icon_path, "wb") as handle:
                handle.write(b"old icon")

            current_surfaces = exporter.build_material_surface_contracts(obj)
            exporter.write_manifest(
                obj,
                manifest_path,
                asset_id,
                "Cube",
                "",
                "Default",
                "model.fbx",
                "icon.png",
                ["model", "icon"],
                material_maps=[
                    {"material": name, "surface": surface}
                    for name, surface in current_surfaces.items()
                ],
                uv_export_contract=exporter.current_uv_export_contract(model_path),
            )
            original_bounds = exporter.read_existing_manifest(manifest_path)["bounds"]
            for vertex in obj.data.vertices:
                vertex.co.x *= 2.0
            current_center, current_size = exporter.mesh_world_bounds(obj)
            current_bounds = {
                "center": [round(current_center.x, 5), round(current_center.y, 5), round(current_center.z, 5)],
                "size": [round(current_size.x, 5), round(current_size.y, 5), round(current_size.z, 5)],
            }
            self.assertNotEqual(current_bounds, original_bounds)

            rendered = []
            queued = []
            original_render = exporter.render_or_copy_shared_icon
            original_export_fbx = exporter.export_fbx
            original_queue = exporter.queue_unity_builder_import

            def render_fresh_icon(root, settings, destination, **kwargs):
                rendered.append((root.name, kwargs.get("force_render", False)))
                with open(destination, "wb") as handle:
                    handle.write(b"fresh icon")
                return destination

            def reject_model_export(*_args, **_kwargs):
                raise AssertionError("A verified existing model must be reused.")

            exporter.render_or_copy_shared_icon = render_fresh_icon
            exporter.export_fbx = reject_model_export
            exporter.queue_unity_builder_import = lambda paths, **_kwargs: queued.extend(paths) or paths
            try:
                result = exporter.export_builder_asset(
                    obj,
                    SimpleNamespace(
                        output_root=output_root,
                        profile_name="Default",
                        icon_resolution=64,
                        skip_existing_exports=True,
                    ),
                    export_model=True,
                    include_icon=True,
                )
            finally:
                exporter.render_or_copy_shared_icon = original_render
                exporter.export_fbx = original_export_fbx
                exporter.queue_unity_builder_import = original_queue

            self.assertEqual(result[2], "exported")
            self.assertEqual(rendered, [(obj.name, True)])
            self.assertEqual(queued, [manifest_path])
            with open(model_path, "rb") as handle:
                self.assertEqual(handle.read(), b"verified model")
            with open(icon_path, "rb") as handle:
                self.assertEqual(handle.read(), b"fresh icon")
            refreshed_manifest = exporter.read_existing_manifest(manifest_path)
            self.assertEqual(refreshed_manifest["modelFile"], "model.fbx")
            self.assertEqual(refreshed_manifest["iconFile"], "icon.png")
            self.assertEqual(refreshed_manifest["exportedResources"], ["icon"])
            self.assertEqual(refreshed_manifest["bounds"], original_bounds)
            self.assertTrue(
                exporter.uv_export_contract_matches_model(
                    refreshed_manifest,
                    model_path,
                )
            )

    def test_model_only_skip_normalizes_a_previous_icon_only_manifest(self):
        obj, _, _, _ = make_quad("Cube_200x200x200_Wood")
        with tempfile.TemporaryDirectory(prefix="rr_exporter_model_reuse_") as output_root:
            asset_id = exporter.export_asset_id(obj)
            asset_dir = os.path.join(output_root, asset_id)
            os.makedirs(asset_dir, exist_ok=True)
            model_path = os.path.join(asset_dir, "model.fbx")
            icon_path = os.path.join(asset_dir, "icon.png")
            manifest_path = os.path.join(asset_dir, "manifest.json")
            with open(model_path, "wb") as handle:
                handle.write(b"verified model")
            with open(icon_path, "wb") as handle:
                handle.write(b"existing icon")

            current_surfaces = exporter.build_material_surface_contracts(obj)
            exporter.write_manifest(
                obj,
                manifest_path,
                asset_id,
                "Cube",
                "",
                "Default",
                "model.fbx",
                "icon.png",
                ["icon"],
                material_maps=[
                    {"material": name, "surface": surface}
                    for name, surface in current_surfaces.items()
                ],
                uv_export_contract=exporter.current_uv_export_contract(model_path),
            )
            original_bounds = exporter.read_existing_manifest(manifest_path)["bounds"]
            for vertex in obj.data.vertices:
                vertex.co.x *= 2.0

            queued = []
            original_export_fbx = exporter.export_fbx
            original_queue = exporter.queue_unity_builder_import

            def reject_model_export(*_args, **_kwargs):
                raise AssertionError("A verified existing model must be reused.")

            exporter.export_fbx = reject_model_export
            exporter.queue_unity_builder_import = lambda paths, **_kwargs: queued.extend(paths) or paths
            try:
                result = exporter.export_builder_asset(
                    obj,
                    SimpleNamespace(
                        output_root=output_root,
                        profile_name="Default",
                        skip_existing_exports=True,
                    ),
                    export_model=True,
                    include_icon=False,
                )
            finally:
                exporter.export_fbx = original_export_fbx
                exporter.queue_unity_builder_import = original_queue

            self.assertEqual(result[2], "skipped")
            self.assertEqual(queued, [manifest_path])
            normalized_manifest = exporter.read_existing_manifest(manifest_path)
            self.assertEqual(normalized_manifest["modelFile"], "model.fbx")
            self.assertEqual(normalized_manifest["iconFile"], "icon.png")
            self.assertEqual(normalized_manifest["exportedResources"], ["model"])
            self.assertEqual(normalized_manifest["bounds"], original_bounds)
            self.assertTrue(
                exporter.uv_export_contract_matches_model(
                    normalized_manifest,
                    model_path,
                )
            )

    def test_scalar_principled_surface_is_exported_without_textures(self):
        obj, _, _, _ = make_quad("GlassWall_400x10x400")
        glass, _ = make_surface_material("Glass")
        obj.data.materials[0] = glass

        with tempfile.TemporaryDirectory(prefix="rr_exporter_surface_") as asset_dir:
            material_maps, warnings = exporter.build_material_map_manifest(obj, asset_dir)

        self.assertFalse(warnings)
        self.assertEqual(len(material_maps), 1)
        self.assertEqual(material_maps[0]["material"], "Glass")
        self.assertNotIn("baseColor", material_maps[0])
        surface = material_maps[0]["surface"]
        self.assertEqual(surface["contractVersion"], 1)
        self.assertEqual(surface["baseColor"], [0.92, 0.97, 1.0, 1.0])
        self.assertEqual(surface["metallic"], 0.0)
        self.assertEqual(surface["roughness"], 0.08)
        self.assertEqual(surface["ior"], 1.45)
        self.assertEqual(surface["transmissionWeight"], 1.0)
        self.assertEqual(surface["alpha"], 0.25)
        self.assertTrue(surface["transparent"])

    def test_opaque_surface_contract_is_emitted_for_reverse_reimport(self):
        material, _ = make_surface_material(
            "OpaqueAgain",
            transmission=0.0,
            alpha=1.0,
        )

        surface = exporter.build_material_surface_contract(material)

        self.assertIsNotNone(surface)
        self.assertFalse(surface["transparent"])
        self.assertEqual(surface["alpha"], 1.0)
        self.assertEqual(surface["transmissionWeight"], 0.0)

    def test_linked_scalar_socket_rejects_surface_contract(self):
        material, principled = make_surface_material("LinkedRoughness")
        value = material.node_tree.nodes.new("ShaderNodeValue")
        material.node_tree.links.new(value.outputs["Value"], principled.inputs["Roughness"])

        self.assertIsNone(exporter.build_material_surface_contract(material))

    def test_existing_model_skip_detects_changed_surface_contract(self):
        obj, _, _, _ = make_quad("SurfaceContractHash")
        glass, principled = make_surface_material("Glass")
        obj.data.materials[0] = glass

        with tempfile.TemporaryDirectory(prefix="rr_exporter_surface_skip_") as asset_dir:
            model_path = os.path.join(asset_dir, "model.fbx")
            manifest_path = os.path.join(asset_dir, "manifest.json")
            with open(model_path, "wb") as handle:
                handle.write(b"current model")

            current_surfaces = exporter.build_material_surface_contracts(obj)
            material_maps = [
                {"material": name, "surface": surface}
                for name, surface in current_surfaces.items()
            ]
            exporter.write_manifest(
                obj,
                manifest_path,
                "SurfaceContractHash",
                "InnerWall",
                "",
                "Default",
                "model.fbx",
                "",
                ["model"],
                material_maps=material_maps,
                uv_export_contract=exporter.current_uv_export_contract(model_path),
            )
            self.assertTrue(
                exporter.output_has_requested_resources(
                    asset_dir,
                    export_model=True,
                    include_icon=False,
                    expected_surface_contracts=current_surfaces,
                )
            )

            principled.inputs["Alpha"].default_value = 0.5
            changed_surfaces = exporter.build_material_surface_contracts(obj)
            self.assertFalse(
                exporter.output_has_requested_resources(
                    asset_dir,
                    export_model=True,
                    include_icon=False,
                    expected_surface_contracts=changed_surfaces,
                )
            )

    def test_glasswall_compatibility_name_exports_as_innerwall(self):
        obj, _, _, _ = make_quad("GlassWall_400x10x400")
        self.assertEqual(
            exporter.infer_export_asset_type(obj, "GlassWall_400x10x400"),
            "InnerWall",
        )

    def test_completed_package_atomically_queues_unity_import(self):
        with tempfile.TemporaryDirectory(prefix="rr_builder_queue_") as temp_dir:
            bridge_root = os.path.join(temp_dir, "Assets", "~Temp", "BlenderBridge")
            request_path = os.path.join(
                temp_dir,
                "Temp",
                "ImportSelectedBuilderGeneratedAssets.request",
            )
            first_manifest = os.path.join(bridge_root, "Cube_200x200x200_Wood", "manifest.json")
            second_manifest = os.path.join(bridge_root, "GlassWall_400x10x400", "manifest.json")
            protected_manifest = os.path.join(bridge_root, "_icon_sources", "manifest.json")
            outside_manifest = os.path.join(temp_dir, "Outside", "manifest.json")
            for path in (
                first_manifest,
                second_manifest,
                protected_manifest,
                outside_manifest,
            ):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("{}")

            queued_first = exporter.queue_unity_builder_import(
                [first_manifest, protected_manifest, outside_manifest],
                request_path=request_path,
                bridge_root=bridge_root,
            )
            queued_second = exporter.queue_unity_builder_import(
                [second_manifest, first_manifest],
                request_path=request_path,
                bridge_root=bridge_root,
            )

            self.assertEqual(queued_first, [os.path.normpath(first_manifest)])
            self.assertEqual(
                queued_second,
                sorted(
                    {
                        os.path.normpath(first_manifest),
                        os.path.normpath(second_manifest),
                    },
                    key=str.casefold,
                ),
            )
            with open(request_path, "r", encoding="utf-8") as handle:
                queued_lines = [line.strip() for line in handle if line.strip()]
            self.assertEqual(
                queued_lines,
                sorted(
                    {
                        os.path.normpath(first_manifest),
                        os.path.normpath(second_manifest),
                    },
                    key=str.casefold,
                ),
            )
            self.assertFalse(
                any(
                    name.endswith(".tmp")
                    for name in os.listdir(os.path.dirname(request_path))
                )
            )

    def test_export_rejects_cleanup_failure_after_restoring_source(self):
        obj, uv_map, _, mapping = make_quad("CleanupFailure")
        original_uv0 = uv_values(uv_map)
        original_rotation = tuple(mapping.inputs["Rotation"].default_value)
        original_restore = rr_unity_uv_export.restore_actions_best_effort

        def restore_and_report_failure(actions, context="Unity UV export"):
            errors = original_restore(actions, context)
            errors.append(RuntimeError("synthetic cleanup failure"))
            return errors

        rr_unity_uv_export.restore_actions_best_effort = restore_and_report_failure
        try:
            with tempfile.TemporaryDirectory(prefix="rr_exporter_cleanup_") as temp_dir:
                with self.assertRaisesRegex(
                    rr_unity_uv_export.UVExportContractError,
                    "export is rejected",
                ):
                    exporter.export_fbx(obj, os.path.join(temp_dir, "cleanup_failure.fbx"))
        finally:
            rr_unity_uv_export.restore_actions_best_effort = original_restore

        self.assertEqual(uv_values(uv_map), original_uv0)
        self.assertEqual(tuple(mapping.inputs["Rotation"].default_value), original_rotation)

    def test_real_fbx_contains_only_baked_uv0_and_export_restores_editor_state(self):
        obj, uv_map, _, mapping = make_quad("Cube_200x200x200_Wood")
        sentinel_mesh = bpy.data.meshes.new("SelectionSentinelMesh")
        sentinel = bpy.data.objects.new("SelectionSentinel", sentinel_mesh)
        bpy.context.scene.collection.objects.link(sentinel)
        sentinel.select_set(True)
        bpy.context.view_layer.objects.active = sentinel
        obj.hide_set(True)
        obj.hide_viewport = True
        obj.hide_render = True
        obj.hide_select = True

        original_uv0 = uv_values(uv_map)
        original_rotation = tuple(mapping.inputs["Rotation"].default_value)
        with tempfile.TemporaryDirectory(prefix="rr_exporter_contract_") as temp_dir:
            fbx_path = os.path.join(temp_dir, "mapping_contract.fbx")
            warnings = exporter.export_fbx(obj, fbx_path)
            self.assertFalse(warnings)
            self.assertTrue(os.path.isfile(fbx_path))

            self.assertEqual(uv_values(uv_map), original_uv0)
            self.assertEqual(tuple(mapping.inputs["Rotation"].default_value), original_rotation)
            self.assertEqual([layer.name for layer in obj.data.uv_layers], ["UVMap"])
            self.assertTrue(obj.hide_get())
            self.assertTrue(obj.hide_viewport)
            self.assertTrue(obj.hide_render)
            self.assertTrue(obj.hide_select)
            self.assertIs(bpy.context.view_layer.objects.active, sentinel)
            self.assertTrue(sentinel.select_get())
            self.assertFalse(obj.select_get())

            clear_scene()
            bpy.ops.import_scene.fbx(filepath=fbx_path)
            imported_meshes = [
                imported.data
                for imported in bpy.context.scene.objects
                if imported.type == "MESH"
            ]
            self.assertEqual(len(imported_meshes), 1)
            imported_mesh = imported_meshes[0]
            self.assertEqual(len(imported_mesh.uv_layers), 1)
            self.assertEqual(imported_mesh.uv_layers[0].name, "UVMap")
            imported_uvs = sorted(set(uv_values(imported_mesh.uv_layers[0])))
            expected_uvs = sorted({(0.0, 0.0), (0.0, 1.0), (-1.0, 1.0), (-1.0, 0.0)})
            self.assertEqual(imported_uvs, expected_uvs)


class ModelingUiSourceContractTests(unittest.TestCase):
    def test_origin_rules_and_point_bookmarks_have_distinct_layout_and_icons(self):
        source = inspect.getsource(exporter.RR_PT_builder_exporter.draw_modeling_page)

        origin_index = source.index("origin_box = layout.box()")
        rules_index = source.index("special_row = origin_box.row(align=True)")
        rules_end_index = source.index('bottom_op.mode = "BOTTOM"')
        bookmarks_index = source.index("bookmarks_box = layout.box()")

        self.assertLess(origin_index, rules_index)
        self.assertLess(rules_index, rules_end_index)
        self.assertLess(rules_end_index, bookmarks_index)
        self.assertNotIn("bookmarks_box = origin_box.box()", source)

        expected_snippets = (
            'origin_box.label(text="Origin", icon="PIVOT_CURSOR")',
            'text="Apply to Selection",\n            icon="OBJECT_ORIGIN"',
            'bookmarks_box.label(text="Point Bookmarks", icon="DOT")',
            '"rr_builder.store_point_bookmark",\n                text="",\n                icon="REC"',
            '"rr_builder.point_bookmark_to_cursor",\n                text="",\n                icon="PIVOT_CURSOR"',
            '"rr_builder.clear_point_bookmark",\n                text="",\n                icon="X"',
        )
        for snippet in expected_snippets:
            self.assertIn(snippet, source)

        icon_items = bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items
        valid_icons = {item.identifier for item in icon_items}
        for icon in {"PIVOT_CURSOR", "OBJECT_ORIGIN", "DOT", "REC", "X"}:
            self.assertIn(icon, valid_icons)


class PointBookmarkContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registered_here = not hasattr(bpy.types.Scene, "rr_builder_export_settings")
        if cls.registered_here:
            exporter.register()

    @classmethod
    def tearDownClass(cls):
        clear_scene()
        if cls.registered_here:
            exporter.unregister()

    def setUp(self):
        clear_scene()
        self.settings = bpy.context.scene.rr_builder_export_settings
        self.settings.point_bookmark_source = "SELECTION"
        self.settings.point_bookmark_space = "WORLD"
        for group in ("G1", "G2", "G3"):
            for point in ("P1", "P2", "P3"):
                slot = self.slot(group, point)
                slot.is_set = False
                slot.alias = ""
                slot.location = (0.0, 0.0, 0.0)
                slot.space = "WORLD"
                slot.source = "SELECTION"
                slot.anchor = None
                slot.anchor_name = ""

    def tearDown(self):
        clear_scene()

    def slot(self, group, point):
        slot = rr_point_bookmarks.point_bookmark_slot(self.settings, group, point)
        self.assertIsNotNone(slot)
        return slot

    def assert_vector_close(self, actual, expected, places=5):
        for component in range(3):
            self.assertAlmostEqual(actual[component], expected[component], places=places)

    def make_edit_mesh(self, name, coordinates, selected_indices=()):
        mesh = bpy.data.meshes.new(name + "_Mesh")
        mesh.from_pydata(coordinates, (), ())
        mesh.update()
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        select_only_object_for_edit(obj)

        edit_mesh = bmesh.from_edit_mesh(mesh)
        edit_mesh.verts.ensure_lookup_table()
        for vertex in edit_mesh.verts:
            vertex.select = vertex.index in selected_indices
        bmesh.update_edit_mesh(mesh)
        return obj

    def store(self, group="G1", point="P1"):
        return bpy.ops.rr_builder.store_point_bookmark(group=group, point=point)

    def to_cursor(self, group="G1", point="P1"):
        return bpy.ops.rr_builder.point_bookmark_to_cursor(group=group, point=point)

    def clear(self, group="G1", point="P1"):
        return bpy.ops.rr_builder.clear_point_bookmark(group=group, point=point)

    def test_point_bookmark_stores_transformed_mesh_edit_selection_in_world_space(self):
        obj = self.make_edit_mesh(
            "BookmarkSelection",
            ((-1.0, 0.0, 2.0), (2.0, 3.0, -1.0), (5.0, -2.0, 4.0)),
            selected_indices=(0, 2),
        )
        obj.matrix_world = (
            Matrix.Translation((7.0, -4.0, 3.0))
            @ Matrix.Rotation(math.radians(31.0), 4, "Z")
            @ Matrix.Diagonal((1.5, -0.75, 2.25, 1.0))
        )
        selected_local = (Vector((-1.0, 0.0, 2.0)) + Vector((5.0, -2.0, 4.0))) * 0.5
        expected_world = obj.matrix_world @ selected_local
        object_names_before = set(bpy.data.objects.keys())

        self.settings.point_bookmark_source = "SELECTION"
        self.settings.point_bookmark_space = "WORLD"
        result = self.store("G1", "P1")

        slot = self.slot("G1", "P1")
        self.assertEqual(result, {"FINISHED"})
        self.assertTrue(slot.is_set)
        self.assertEqual(slot.space, "WORLD")
        self.assertEqual(slot.source, "SELECTION")
        self.assertIsNone(slot.anchor)
        self.assert_vector_close(slot.location, expected_world)
        self.assertEqual(set(bpy.data.objects.keys()), object_names_before)

    def test_cursor_world_roundtrip_preserves_rotation_selection_active_mode_and_objects(self):
        obj = self.make_edit_mesh(
            "BookmarkCursorState",
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            selected_indices=(1,),
        )
        scene = bpy.context.scene
        stored_location = Vector((8.25, -3.5, 1.75))
        stored_rotation = (0.31, -0.72, 1.19)
        scene.cursor.location = stored_location
        scene.cursor.rotation_euler = stored_rotation
        object_names_before = set(bpy.data.objects.keys())
        selected_before = {item.name for item in bpy.context.selected_objects}
        active_before = bpy.context.view_layer.objects.active
        mode_before = bpy.context.mode

        self.settings.point_bookmark_source = "CURSOR"
        self.settings.point_bookmark_space = "WORLD"
        self.assertEqual(self.store("G1", "P2"), {"FINISHED"})
        scene.cursor.location = (-9.0, 4.0, 12.0)
        self.assertEqual(self.to_cursor("G1", "P2"), {"FINISHED"})

        self.assert_vector_close(scene.cursor.location, stored_location)
        self.assert_vector_close(scene.cursor.rotation_euler, stored_rotation)
        self.assertEqual({item.name for item in bpy.context.selected_objects}, selected_before)
        self.assertIs(bpy.context.view_layer.objects.active, active_before)
        self.assertIs(active_before, obj)
        self.assertEqual(bpy.context.mode, mode_before)
        self.assertEqual(set(bpy.data.objects.keys()), object_names_before)

    def test_point_bookmark_slots_isolate_alias_overwrite_and_clear(self):
        scene = bpy.context.scene
        self.settings.point_bookmark_source = "CURSOR"
        self.settings.point_bookmark_space = "WORLD"
        slot_g1_p1 = self.slot("G1", "P1")
        slot_g1_p2 = self.slot("G1", "P2")
        slot_g2_p1 = self.slot("G2", "P1")
        slot_g1_p1.alias = "Soft Start"
        slot_g1_p2.alias = "Soft Center"
        slot_g2_p1.alias = "Hard Start"

        scene.cursor.location = (1.0, 2.0, 3.0)
        self.assertEqual(self.store("G1", "P1"), {"FINISHED"})
        scene.cursor.location = (4.0, 5.0, 6.0)
        self.assertEqual(self.store("G1", "P2"), {"FINISHED"})
        scene.cursor.location = (7.0, 8.0, 9.0)
        self.assertEqual(self.store("G2", "P1"), {"FINISHED"})

        self.assert_vector_close(slot_g1_p1.location, (1.0, 2.0, 3.0))
        self.assert_vector_close(slot_g1_p2.location, (4.0, 5.0, 6.0))
        self.assert_vector_close(slot_g2_p1.location, (7.0, 8.0, 9.0))

        scene.cursor.location = (-3.0, -2.0, -1.0)
        self.assertEqual(self.store("G1", "P1"), {"FINISHED"})
        self.assert_vector_close(slot_g1_p1.location, (-3.0, -2.0, -1.0))
        self.assertEqual(slot_g1_p1.alias, "Soft Start")
        self.assert_vector_close(slot_g1_p2.location, (4.0, 5.0, 6.0))
        self.assert_vector_close(slot_g2_p1.location, (7.0, 8.0, 9.0))

        self.assertEqual(self.clear("G1", "P1"), {"FINISHED"})
        self.assertFalse(slot_g1_p1.is_set)
        self.assertEqual(slot_g1_p1.alias, "Soft Start")
        self.assert_vector_close(slot_g1_p1.location, (0.0, 0.0, 0.0))
        self.assertTrue(slot_g1_p2.is_set)
        self.assertTrue(slot_g2_p1.is_set)
        self.assertEqual(slot_g1_p2.alias, "Soft Center")
        self.assertEqual(slot_g2_p1.alias, "Hard Start")

    def test_local_bookmark_follows_anchor_and_missing_anchor_does_not_move_cursor(self):
        mesh = bpy.data.meshes.new("BookmarkAnchor_Mesh")
        mesh.from_pydata(((0.0, 0.0, 0.0),), (), ())
        anchor = bpy.data.objects.new("BookmarkAnchor", mesh)
        bpy.context.scene.collection.objects.link(anchor)
        anchor.matrix_world = (
            Matrix.Translation((2.0, -5.0, 1.0))
            @ Matrix.Rotation(math.radians(18.0), 4, "Y")
            @ Matrix.Diagonal((2.0, 0.5, 1.25, 1.0))
        )
        anchor.select_set(True)
        bpy.context.view_layer.objects.active = anchor
        original_world = Vector((4.0, -1.5, 6.0))
        bpy.context.scene.cursor.location = original_world
        self.settings.point_bookmark_source = "CURSOR"
        self.settings.point_bookmark_space = "LOCAL"

        self.assertEqual(self.store("G3", "P2"), {"FINISHED"})
        slot = self.slot("G3", "P2")
        expected_local = anchor.matrix_world.inverted() @ original_world
        self.assertEqual(slot.space, "LOCAL")
        self.assertIs(slot.anchor, anchor)
        self.assertEqual(slot.anchor_name, anchor.name)
        self.assert_vector_close(slot.location, expected_local)

        anchor.matrix_world = (
            Matrix.Translation((-7.0, 3.0, 9.0))
            @ Matrix.Rotation(math.radians(-43.0), 4, "Z")
            @ Matrix.Diagonal((0.75, 1.8, 0.6, 1.0))
        )
        expected_followed_world = anchor.matrix_world @ expected_local
        bpy.context.scene.cursor.location = (99.0, 98.0, 97.0)
        self.assertEqual(self.to_cursor("G3", "P2"), {"FINISHED"})
        self.assert_vector_close(bpy.context.scene.cursor.location, expected_followed_world)

        bpy.data.objects.remove(anchor, do_unlink=True)
        sentinel = Vector((-11.0, 22.0, -33.0))
        bpy.context.scene.cursor.location = sentinel
        self.assertEqual(self.to_cursor("G3", "P2"), {"CANCELLED"})
        self.assert_vector_close(bpy.context.scene.cursor.location, sentinel)

    def test_selection_store_failure_does_not_overwrite_existing_slot(self):
        scene = bpy.context.scene
        slot = self.slot("G2", "P3")
        slot.alias = "Keep Me"
        self.settings.point_bookmark_source = "CURSOR"
        self.settings.point_bookmark_space = "WORLD"
        scene.cursor.location = (3.5, -8.25, 13.0)
        self.assertEqual(self.store("G2", "P3"), {"FINISHED"})
        before = {
            "is_set": slot.is_set,
            "alias": slot.alias,
            "location": tuple(slot.location),
            "space": slot.space,
            "source": slot.source,
            "anchor": slot.anchor,
            "anchor_name": slot.anchor_name,
        }

        self.make_edit_mesh(
            "BookmarkNoSelection",
            ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
            selected_indices=(),
        )
        self.settings.point_bookmark_source = "SELECTION"
        self.settings.point_bookmark_space = "LOCAL"
        self.assertEqual(self.store("G2", "P3"), {"CANCELLED"})

        self.assertEqual(slot.is_set, before["is_set"])
        self.assertEqual(slot.alias, before["alias"])
        self.assertEqual(tuple(slot.location), before["location"])
        self.assertEqual(slot.space, before["space"])
        self.assertEqual(slot.source, before["source"])
        self.assertIs(slot.anchor, before["anchor"])
        self.assertEqual(slot.anchor_name, before["anchor_name"])


if __name__ == "__main__":
    suite = unittest.TestSuite(
        (
            unittest.defaultTestLoader.loadTestsFromTestCase(TexturePathContractTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(ExporterUvContractTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(ModelingUiSourceContractTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(PointBookmarkContractTests),
        )
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("RR_EXPORTER_CONTRACTS_PASS")
    raise SystemExit(0 if result.wasSuccessful() else 1)
