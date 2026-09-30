"""Reference-layout coordinates versus real FBX, in an isolated factory scene.

Run with Blender --background --factory-startup --disable-autoexec --threads 2
--python-exit-code 1 --python tests/test_rr_reference_layout_coordinates_blender.py.
All exports are temporary; this never touches a production package or scene.
"""

import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import bpy
from mathutils import Euler, Matrix, Vector


ADDONS_DIR = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDONS_DIR))
rr = importlib.import_module("random_realm_builder_exporter")
assert Path(rr.__file__).resolve().is_relative_to(ADDONS_DIR.resolve()), rr.__file__


def matrix_from_list(values):
    return Matrix([values[index:index + 4] for index in range(0, 16, 4)])


def unity_point(point, scale=1.0):
    # Independent expected values, not the production conversion helper.
    return Vector((-point.x * scale, point.z * scale, -point.y * scale))


def empty(name, matrix):
    obj = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(obj)
    obj.matrix_world = matrix
    return obj


def mesh(name, parent, world):
    data = bpy.data.meshes.new(name + "Mesh")
    data.from_pydata([(0.2, -0.4, 0.1), (1.1, -0.2, 0.5), (-0.3, 0.7, 1.3)], [], [(0, 1, 2)])
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    obj.parent = parent
    obj.matrix_parent_inverse = parent.matrix_world.inverted()
    obj.matrix_world = world
    return obj


def read_fbx_models(path):
    """Read actual FBX matrices/vertices without importing back into Blender.

    FBX stores centimeters in its Y-up right-handed system. Unity reflects X
    and reads centimeters as meters. No RR conversion helper is used here.
    The fixture deliberately uses ordinary XYZ transforms and no FBX pivots.
    """
    from io_scene_fbx import parse_fbx

    root, _version = parse_fbx.parse(str(path))
    sections = {element.id: element for element in root.elems}
    models, geometry, parents, geometries = {}, {}, {}, {}
    for element in sections[b"Objects"].elems:
        if element.id == b"Geometry":
            vertices = next(item.props[0] for item in element.elems if item.id == b"Vertices")
            geometry[element.props[0]] = [Vector(vertices[index:index + 3]) for index in range(0, len(vertices), 3)]
        elif element.id == b"Model":
            properties = {}
            for item in element.elems:
                if item.id == b"Properties70":
                    properties = {prop.props[0]: prop.props[4:] for prop in item.elems}
            for unsupported in (b"PreRotation", b"PostRotation", b"RotationPivot", b"ScalingPivot",
                                b"GeometricTranslation", b"GeometricRotation"):
                if any(properties.get(unsupported, ())):
                    raise AssertionError("Unexpected FBX pivot transform: " + repr(unsupported))
            if properties.get(b"RotationOrder", [0])[0] != 0:
                raise AssertionError("The FBX fixture must use XYZ Euler rotation")
            translation = properties.get(b"Lcl Translation", (0, 0, 0))
            rotation = properties.get(b"Lcl Rotation", (0, 0, 0))
            scale = properties.get(b"Lcl Scaling", (1, 1, 1))
            local = Matrix.Translation(translation) @ Euler(
                tuple(math.radians(value) for value in rotation), "XYZ"
            ).to_matrix().to_4x4() @ Matrix.Diagonal((*scale, 1.0))
            models[element.props[0]] = {
                "name": element.props[1].split(b"\x00", 1)[0].decode("utf-8"),
                "local": local,
            }
    for connection in sections[b"Connections"].elems:
        if connection.props[0] != b"OO":
            continue
        child, parent = connection.props[1:3]
        if child in models:
            parents[child] = parent
        elif child in geometry and parent in models:
            geometries[parent] = child

    def world(model_id):
        item = models[model_id]
        if "world" not in item:
            parent = parents.get(model_id)
            item["world"] = (world(parent) if parent in models else Matrix.Identity(4)) @ item["local"]
        return item["world"]

    unity_from_fbx = Matrix.Diagonal((-0.01, 0.01, 0.01, 1.0))
    result = {}
    for model_id, item in models.items():
        model_world = world(model_id)
        result[item["name"]] = {
            "world": unity_from_fbx @ model_world @ unity_from_fbx.inverted(),
            "vertices": [unity_from_fbx @ model_world @ vertex
                         for vertex in geometry.get(geometries.get(model_id), ())],
        }
    return result


class ReferenceLayoutCoordinateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
            raise RuntimeError("Run in a separate factory-startup Blender process")
        rr.register()

    @classmethod
    def tearDownClass(cls):
        rr.unregister()

    def setUp(self):
        rr.clear_reference_object()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for data in list(bpy.data.meshes):
            bpy.data.meshes.remove(data)
        bpy.context.scene.unit_settings.system = "NONE"
        bpy.context.scene.unit_settings.scale_length = 1.0
        self.temporary = tempfile.TemporaryDirectory(prefix="rr_reference_coordinates_")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        rr.reset_object_manager_duplicate_guard()
        rr.reset_object_manager_name_sync_state()

    def tearDown(self):
        bpy.context.scene.unit_settings.system = "NONE"
        bpy.context.scene.unit_settings.scale_length = 1.0

    def assert_matrix_close(self, actual, expected, places=4):
        for row in range(4):
            for column in range(4):
                self.assertAlmostEqual(actual[row][column], expected[row][column], places=places)

    def assert_vector_close(self, actual, expected, places=4):
        for index in range(3):
            self.assertAlmostEqual(actual[index], expected[index], places=places)

    def test_translation_maps_horizontal_depth_and_vertical_height(self):
        source = Matrix.Translation((2, 31, 8.1))
        actual = rr.unity_reference_layout_matrix(source)
        self.assert_vector_close(actual.translation, (-2, 8.1, -31))
        self.assert_matrix_close(actual.to_3x3().to_4x4(), Matrix.Identity(4))

    def test_unit_conversion_matches_fbx_none_and_metric_rules(self):
        units = bpy.context.scene.unit_settings
        for system, length, expected in (("NONE", 0.01, (-2, 8.1, -31)),
                                          ("METRIC", 0.01, (-0.02, 0.081, -0.31)),
                                          ("IMPERIAL", 0.3048, (-0.6096, 2.46888, -9.4488))):
            with self.subTest(system=system):
                units.system, units.scale_length = system, length
                actual = rr.unity_reference_layout_matrix(Matrix.Translation((2, 31, 8.1)))
                self.assert_vector_close(actual.translation, expected)

    def test_rotation_converts_all_axes_and_keeps_composition(self):
        point = Vector((0.3, 2.1, -4.7))
        for axis in ("X", "Y", "Z"):
            with self.subTest(axis=axis):
                authored = Matrix.Translation((2, 31, 8.1)) @ Matrix.Rotation(0.71, 4, axis)
                converted = rr.unity_reference_layout_matrix(authored)
                self.assert_vector_close(converted @ unity_point(point), unity_point(authored @ point))
                self.assertAlmostEqual(converted.determinant(), 1.0, places=5)

    def test_manifest_uses_parented_world_pose_and_explicit_unity_space(self):
        parent = empty("LayoutParent", Matrix.Translation((17, 8, -4)) @ Matrix.Rotation(0.31, 4, "Y"))
        core = empty("Core", Matrix.Translation((12, -9, 3)) @ Euler((0.4, -0.3, 0.8)).to_matrix().to_4x4())
        member = empty("Member", Matrix.Identity(4))
        member.parent = parent
        member.matrix_parent_inverse = Matrix.Translation((-3, 5, 7))
        member.matrix_basis = Matrix.Translation((2, 31, 8.1)) @ Matrix.Rotation(-0.46, 4, "X")
        bpy.context.view_layer.update()
        rr.mark_reference_object(core)
        raw = rr.authoring_relative_matrix(core, member)
        self.assert_matrix_close(raw, core.matrix_world.inverted() @ member.matrix_world)
        block = rr.build_reference_layout_for_export(member)
        self.assertEqual(block["version"], 1)
        self.assertEqual(block["coordinateSpace"], "UNITY")
        relative = matrix_from_list(block["relativeAuthoringMatrix"])
        core_unity = rr.unity_reference_layout_matrix(core.matrix_world)
        point = Vector((0.5, -1.2, 4.7))
        self.assert_vector_close(core_unity @ relative @ unity_point(point),
                                 unity_point(member.matrix_world @ point))
        for key in ("authoringFrameInRoot", "referenceAuthoringFrameInRoot"):
            self.assert_matrix_close(matrix_from_list(block[key]), Matrix.Identity(4))

    def test_invalid_transform_is_rejected(self):
        bad = Matrix.Identity(4)
        bad[0][3] = float("nan")
        with self.assertRaisesRegex(ValueError, "non-finite"):
            rr.unity_reference_layout_matrix(bad)

    def test_real_fbx_and_manifest_preserve_core_empty_and_every_part_vertex(self):
        # This exercises the elevator hierarchy and parent-inverse compensation,
        # using independent FBX values so matching bugs in helper and test cannot pass.
        for system, length, meters in (("NONE", 0.01, 1.0), ("METRIC", 0.01, 0.01)):
            with self.subTest(system=system):
                units = bpy.context.scene.unit_settings
                units.system, units.scale_length = system, length
                for obj in list(bpy.data.objects):
                    bpy.data.objects.remove(obj, do_unlink=True)
                core_world = Matrix.Translation((12, -9, 3)) @ Euler((0.19, -0.23, 0.37)).to_matrix().to_4x4()
                core = empty("AxisCore_400x10x400", core_world)
                mesh("CoreMesh", core, core_world)
                member = empty("AxisElevator_100x220x100", Matrix.Translation((14, 22, 11.1)))
                parts = []
                for index in range(3):
                    world = Matrix.Translation((14 + index * 1.3, 22 - index * 0.9, 3 + index * 8.1))
                    world @= Euler((0.1 * index, -0.17 * index, 0.23 * index)).to_matrix().to_4x4()
                    parts.append(mesh("ElevatorPart" + str(index), member, world))
                bpy.context.view_layer.update()
                before_world = {obj: obj.matrix_world.copy() for obj in parts}
                old_root = member.matrix_world.copy()
                member.location.z = 3.0
                bpy.context.view_layer.update()
                for obj in parts:
                    obj.matrix_parent_inverse = member.matrix_world.inverted() @ old_root @ obj.matrix_parent_inverse
                bpy.context.view_layer.update()
                for obj in parts:
                    self.assert_matrix_close(obj.matrix_world, before_world[obj])
                rr.mark_reference_object(core)
                settings = bpy.context.scene.rr_builder_export_settings
                settings.output_root = str(self.directory / system)
                settings.export_mode = rr.EXPORT_MODE_GENERAL
                for obj in (core, member):
                    rr.export_builder_asset(obj, settings, export_model=True, include_icon=False, queue_import=False)
                core_models = read_fbx_models(Path(settings.output_root) / rr.export_asset_id(core) / "model.fbx")
                member_models = read_fbx_models(Path(settings.output_root) / rr.export_asset_id(member) / "model.fbx")
                manifest_path = Path(settings.output_root) / rr.export_asset_id(member) / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["referenceLayout"]
                self.assertEqual(manifest["coordinateSpace"], "UNITY")
                relative = matrix_from_list(manifest["relativeAuthoringMatrix"])
                core_unity = core_models[core.name]["world"]
                member_unity = member_models[member.name]["world"]
                self.assert_matrix_close(core_unity @ relative, member_unity)
                for obj in parts:
                    imported = member_models[obj.name]
                    self.assertEqual(len(imported["vertices"]), len(obj.data.vertices))
                    self.assert_matrix_close(obj.matrix_world, before_world[obj])
                    for point, vertex in zip(imported["vertices"], obj.data.vertices):
                        self.assert_vector_close(point, unity_point(obj.matrix_world @ vertex.co, meters))
                        # Place each child through Core -> Empty -> child, as Unity does.
                        in_member = member_unity.inverted() @ point
                        self.assert_vector_close(core_unity @ relative @ in_member, point)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ReferenceLayoutCoordinateTests)
    )
    if result.wasSuccessful():
        print(f"RR_REFERENCE_COORDINATES_PASS tests={result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
