"""Surface Text and coordinate export regressions; run in a fresh Blender process."""

import math
import os
import sys
import tempfile
import time
import unittest
import warnings
from types import SimpleNamespace
from unittest import mock

import bmesh
import bpy
from mathutils import Matrix, Vector

warnings.filterwarnings("ignore", category=DeprecationWarning)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "addons"))
import random_realm_builder_exporter as exporter
from random_realm_builder_exporter import rr_surface_text as surface
from random_realm_builder_exporter import rr_unity_uv_export as uv


def clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.curves, bpy.data.materials):
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)


def make_target():
    bpy.ops.mesh.primitive_cube_add(size=4)
    target = bpy.context.object
    target.name = "SurfaceSource"
    return target


def add_text(target):
    bpy.ops.object.select_all(action="DESELECT")
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(target.data)
    for face in bm.faces:
        face.select = face.normal.z > 0.9
    bmesh.update_edit_mesh(target.data)
    result = bpy.ops.rr_builder.add_surface_text()
    if result != {"FINISHED"}:
        raise AssertionError(result)
    return bpy.context.object


def datablock_names():
    return set(bpy.data.objects.keys()), set(bpy.data.meshes.keys())


def uv_values(mesh):
    return [tuple(loop.uv) for loop in mesh.uv_layers[0].data]


def make_mapping_material(obj, name="Mapped", modes=("POINT",)):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    coordinate = nodes.new("ShaderNodeTexCoord")
    image = nodes.new("ShaderNodeTexImage")
    previous = coordinate.outputs["UV"]
    mappings = []
    for index, mode in enumerate(modes):
        mapping = nodes.new("ShaderNodeMapping")
        mapping.vector_type = mode
        if mode == "POINT":
            mapping.inputs["Location"].default_value = (0.3 + index, -0.6, 0.2)
        mapping.inputs["Rotation"].default_value = (0.4, -0.7 + index, 0.9)
        mapping.inputs["Scale"].default_value = (2.0, 0.7, 1.3)
        material.node_tree.links.new(previous, mapping.inputs["Vector"])
        previous = mapping.outputs["Vector"]
        mappings.append(mapping)
    material.node_tree.links.new(previous, image.inputs["Vector"])
    obj.data.materials.clear()
    obj.data.materials.append(material)
    return material, image, mappings


def scalar_mapping(vector, mapping):
    """Independent scalar reference, including all XYZ rotations and Z carry."""
    location_socket = mapping.inputs.get("Location")
    location = tuple(location_socket.default_value) if location_socket is not None else (0, 0, 0)
    rotation = tuple(mapping.inputs["Rotation"].default_value)
    scale = tuple(mapping.inputs["Scale"].default_value)
    x, y, z = (vector[index] * scale[index] for index in range(3))
    cosine, sine = math.cos(rotation[0]), math.sin(rotation[0])
    y, z = cosine * y - sine * z, sine * y + cosine * z
    cosine, sine = math.cos(rotation[1]), math.sin(rotation[1])
    x, z = cosine * x + sine * z, -sine * x + cosine * z
    cosine, sine = math.cos(rotation[2]), math.sin(rotation[2])
    x, y = cosine * x - sine * y, sine * x + cosine * y
    result = (x, y, z)
    if mapping.vector_type == "POINT":
        result = tuple(result[index] + location[index] for index in range(3))
    return result


class SurfaceUvRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registered = not hasattr(bpy.types, surface.RR_OT_add_surface_text.__name__)
        if cls.registered:
            bpy.utils.register_class(surface.RR_OT_add_surface_text)

    @classmethod
    def tearDownClass(cls):
        if cls.registered:
            bpy.utils.unregister_class(surface.RR_OT_add_surface_text)

    def setUp(self):
        clear_scene()

    def tearDown(self):
        clear_scene()

    def test_formal_rename_preserves_text_and_current_manifest_names(self):
        target = make_target()
        text = add_text(target)
        sampling = text["rr_surface_text_surface_ref"]
        alias_before = surface.surface_sampling_export_name(sampling)
        exporter.rename_export_asset_preserving_identity(target, "RenamedSource")
        sampling.name = "RenamedSampling"
        sampling.data.name = "RenamedSamplingMesh"
        self.assertEqual(surface.find_surface_text_objects_for_export(target), [text])
        descriptor = surface.build_surface_text_manifest(target)[0]
        self.assertEqual(descriptor["targetObjectName"], target.name)
        self.assertEqual(descriptor["samplingSurfaceObjectName"], sampling.name)
        self.assertEqual(descriptor["samplingSurfaceMeshName"], sampling.data.name)
        self.assertEqual(descriptor["samplingSurfaceExportObjectName"], alias_before)
        self.assertIn(sampling, exporter._surface_text_snapshot_objects(target))

    def test_legacy_text_uses_shrinkwrap_after_rename_and_name_reuse(self):
        target = make_target()
        text = add_text(target)
        del text["rr_surface_text_target_ref"]
        del text["rr_surface_text_surface_ref"]
        old_name = target.name
        exporter.rename_export_asset_preserving_identity(target, "RenamedSource")
        unrelated = bpy.data.objects.new(old_name, None)
        bpy.context.scene.collection.objects.link(unrelated)
        self.assertEqual(surface.find_surface_text_objects_for_export(unrelated), [])
        self.assertEqual(surface.find_surface_text_objects_for_export(target), [text])
        self.assertEqual(surface.build_surface_text_manifest(target)[0]["targetObjectName"], target.name)

    def test_two_texts_on_same_region_export_twice_and_round_trip(self):
        target = make_target()
        texts = [add_text(target), add_text(target)]
        descriptors = surface.build_surface_text_manifest(target)
        aliases = {item["samplingSurfaceExportObjectName"] for item in descriptors}
        self.assertEqual(len(aliases), 2)
        self.assertTrue(all(len(name) <= 63 for name in aliases))
        before = datablock_names()
        with tempfile.TemporaryDirectory(prefix="rr_surface_regression_") as directory:
            path = os.path.join(directory, "surface.fbx")
            for _ in range(2):
                exporter.export_fbx(target, path)
                self.assertGreater(os.path.getsize(path), 0)
                self.assertEqual(datablock_names(), before)
                self.assertEqual(surface.find_surface_text_objects_for_export(target), texts)
            clear_scene()
            bpy.ops.import_scene.fbx(filepath=path)
            self.assertTrue(aliases.issubset(set(bpy.data.objects.keys())))
            for item in descriptors:
                self.assertIn(item["exportObjectName"], bpy.data.objects)

    def test_legacy_duplicate_region_ids_migrate_without_alias_collision(self):
        target = make_target()
        texts = [add_text(target), add_text(target)]
        samplings = [text["rr_surface_text_surface_ref"] for text in texts]
        for sampling in samplings:
            del sampling["rr_surface_export_id"]
        legacy_alias = surface.SURFACE_SAMPLE_EXPORT_PREFIX + "_" + surface._legacy_sampling_export_identity(samplings[0])
        self.assertEqual(samplings[0]["rr_surface_identity"], samplings[1]["rr_surface_identity"])
        aliases = [surface.surface_sampling_export_name(item) for item in samplings]
        self.assertEqual(len(set(aliases)), 2)
        self.assertIn(legacy_alias, aliases)
        samplings[0].name = "RenamedLegacySampling"
        self.assertEqual([surface.surface_sampling_export_name(item) for item in samplings], aliases)
        before = datablock_names()
        with tempfile.TemporaryDirectory(prefix="rr_surface_legacy_") as directory:
            exporter.export_fbx(target, os.path.join(directory, "legacy.fbx"))
        self.assertEqual(datablock_names(), before)

    def test_partial_alias_creation_removes_objects_and_unassigned_mesh(self):
        target = make_target()
        texts = [add_text(target), add_text(target)]
        surface.build_surface_text_manifest(target)
        before = datablock_names()
        real_objects = bpy.data.objects

        class FailingObjects:
            calls = 0

            def __iter__(self):
                return iter(real_objects)

            def __getattr__(self, name):
                return getattr(real_objects, name)

            def new(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("injected object allocation failure")
                return real_objects.new(*args, **kwargs)

        proxy = SimpleNamespace(
            data=SimpleNamespace(objects=FailingObjects(), meshes=bpy.data.meshes),
            context=bpy.context,
            types=bpy.types,
        )
        with mock.patch.object(surface, "bpy", proxy):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                surface.create_surface_text_sampling_export_aliases(target)
        self.assertEqual(datablock_names(), before)
        self.assertEqual(surface.find_surface_text_objects_for_export(target), texts)

    def test_native_copy_and_append_repair_copied_export_ids(self):
        target = make_target()
        text = add_text(target)
        sampling = text["rr_surface_text_surface_ref"]
        original_alias = surface.surface_sampling_export_name(sampling)
        copied = sampling.copy()
        copied.data = sampling.data.copy()
        copied.name = "A Copied Sampling"
        bpy.context.scene.collection.objects.link(copied)
        self.assertEqual(copied["rr_surface_export_id"], sampling["rr_surface_export_id"])
        copied_alias = surface.surface_sampling_export_name(copied)
        self.assertNotEqual(copied_alias, original_alias)
        self.assertEqual(surface.surface_sampling_export_name(sampling), original_alias)
        with tempfile.TemporaryDirectory(prefix="rr_surface_append_") as directory:
            path = os.path.join(directory, "sampling.blend")
            bpy.data.libraries.write(path, {sampling})
            with bpy.data.libraries.load(path, link=False) as (_source, destination):
                destination.objects = [sampling.name]
            appended = destination.objects[0]
            bpy.context.scene.collection.objects.link(appended)
            self.assertEqual(appended["rr_surface_export_id"], sampling["rr_surface_export_id"])
            aliases = [surface.surface_sampling_export_name(obj) for obj in (sampling, copied, appended)]
            self.assertEqual(len(set(aliases)), 3)
            appended.name = "RenamedAppendedSampling"
            self.assertEqual([surface.surface_sampling_export_name(obj) for obj in (sampling, copied, appended)], aliases)

    def test_legacy_leaked_alias_is_reclaimed_for_next_export(self):
        target = make_target()
        text = add_text(target)
        before = datablock_names()
        leaked = surface.create_surface_text_sampling_export_aliases(target)
        self.assertEqual(len(leaked), 1)
        bpy.ops.object.select_all(action="DESELECT")
        text.select_set(True)
        leaked[0].select_set(True)
        bpy.context.view_layer.objects.active = leaked[0]
        with tempfile.TemporaryDirectory(prefix="rr_surface_recover_") as directory:
            path = os.path.join(directory, "recovered.fbx")
            exporter.export_fbx(target, path)
            self.assertGreater(os.path.getsize(path), 0)
        self.assertEqual(datablock_names(), before)
        self.assertEqual(list(bpy.context.selected_objects), [text])
        self.assertIsNone(bpy.context.view_layer.objects.active)

    def test_same_name_objects_without_exact_leak_markers_are_preserved(self):
        target = make_target()
        text = add_text(target)
        sampling = text["rr_surface_text_surface_ref"]
        alias_name = surface.surface_sampling_export_name(sampling)
        for kind in ("ordinary", "wrong_source", "wrong_identity"):
            with self.subTest(kind=kind):
                mesh = sampling.data.copy()
                blocker = bpy.data.objects.new(alias_name, mesh)
                bpy.context.scene.collection.objects.link(blocker)
                if kind != "ordinary":
                    blocker["rr_surface_role"] = "sampling_surface_export_alias"
                    blocker["rr_surface_source_object"] = "Unrelated" if kind == "wrong_source" else sampling.name
                    blocker["rr_surface_identity"] = "different" if kind == "wrong_identity" else sampling["rr_surface_identity"]
                before = datablock_names()
                with self.assertRaisesRegex(RuntimeError, "already used"):
                    surface.create_surface_text_sampling_export_aliases(target)
                self.assertEqual(datablock_names(), before)
                self.assertEqual(bpy.data.objects.get(alias_name), blocker)
                bpy.data.objects.remove(blocker, do_unlink=True)
                bpy.data.meshes.remove(mesh)

    def test_partial_solid_creation_removes_first_solid_and_second_mesh(self):
        target = make_target()
        add_text(target)
        invalid = add_text(target)
        invalid.modifiers["Solidify"].thickness = 0
        bpy.context.view_layer.update()
        before = datablock_names()
        with self.assertRaisesRegex(RuntimeError, "thickness"):
            surface.create_surface_text_export_meshes(target)
        self.assertEqual(datablock_names(), before)

    def test_linked_mapping_parameters_reject_before_any_mesh_mutation(self):
        valid = make_target()
        valid_material, valid_image, valid_mappings = make_mapping_material(valid, "Valid")
        invalid = make_target()
        invalid_material, invalid_image, invalid_mappings = make_mapping_material(invalid, "Invalid")
        resolver = lambda material: valid_image if material == valid_material else invalid_image
        value = invalid_material.node_tree.nodes.new("ShaderNodeValue")
        value.outputs[0].default_value = 2
        before_uvs = [uv_values(obj.data) for obj in (valid, invalid)]
        before_mapping = [tuple(item.default_value) for item in valid_mappings[0].inputs if item.name in {"Location", "Rotation", "Scale"}]
        for socket_name in ("Location", "Rotation", "Scale"):
            with self.subTest(socket=socket_name):
                link = invalid_material.node_tree.links.new(value.outputs[0], invalid_mappings[0].inputs[socket_name])
                with mock.patch.object(uv, "_restore_mesh_uv0", wraps=uv._restore_mesh_uv0) as restore:
                    with self.assertRaisesRegex(uv.UVExportContractError, "unlinked Location"):
                        uv.prepare_unity_uvs_for_export(valid, [valid, invalid], resolver)
                    restore.assert_not_called()
                self.assertEqual([uv_values(obj.data) for obj in (valid, invalid)], before_uvs)
                self.assertEqual([tuple(item.default_value) for item in valid_mappings[0].inputs if item.name in {"Location", "Rotation", "Scale"}], before_mapping)
                invalid_material.node_tree.links.remove(link)

    def test_compiled_point_vector_chain_matches_independent_rotation_math(self):
        for modes in (("POINT",), ("VECTOR",), ("POINT", "VECTOR"), ("VECTOR", "POINT")):
            with self.subTest(modes=modes):
                obj = make_target()
                material, image, mappings = make_mapping_material(obj, modes=modes)
                original = uv_values(obj.data)
                expected = []
                for value in original:
                    coordinate = (*value, 0)
                    for mapping in mappings:
                        coordinate = scalar_mapping(coordinate, mapping)
                    expected.append(coordinate[:2])
                with mock.patch.object(uv, "_mapping_node_transform", wraps=uv._mapping_node_transform) as compile_transform:
                    actions, _warnings = uv.prepare_unity_uvs_for_export(obj, [obj], lambda _material: image)
                    self.assertEqual(compile_transform.call_count, len(mappings))
                try:
                    for actual, reference in zip(uv_values(obj.data), expected):
                        for component, correct in zip(actual, reference):
                            self.assertAlmostEqual(component, correct, places=5)
                finally:
                    self.assertEqual(uv.restore_actions_best_effort(actions), [])
                self.assertEqual(uv_values(obj.data), original)


def benchmark():
    clear_scene()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=200, y_subdivisions=200, size=20, calc_uvs=True)
    obj = bpy.context.object
    _material, image, _mappings = make_mapping_material(obj)
    original_evaluate = uv._evaluate_loop_uv

    def uncached(mesh, loop_index, vertex_index, recipe, fallback, minimum, size):
        plain_recipe = dict(recipe, mapping_transform=None)
        coordinate = original_evaluate(mesh, loop_index, vertex_index, plain_recipe, fallback, minimum, size)
        vector = Vector((coordinate.x, coordinate.y, 0))
        for mapping in recipe["mappings"]:
            location, rotation, scale = uv._mapping_node_values(mapping)
            vector = Vector((vector.x * scale.x, vector.y * scale.y, vector.z * scale.z))
            for axis, angle in zip("XYZ", rotation):
                vector = Matrix.Rotation(angle, 4, axis) @ vector
            if mapping.vector_type == "POINT":
                vector += location
        return vector.to_2d()

    results = {}
    values = {}
    for label, evaluator in (("uncached", uncached), ("compiled", original_evaluate)):
        with mock.patch.object(uv, "_evaluate_loop_uv", evaluator):
            started = time.perf_counter()
            actions, _warnings = uv.prepare_unity_uvs_for_export(obj, [obj], lambda _material: image)
            results[label] = time.perf_counter() - started
            values[label] = uv_values(obj.data)
            uv.restore_actions_best_effort(actions)
    maximum_error = max(abs(a - b) for actual, expected in zip(values["compiled"], values["uncached"]) for a, b in zip(actual, expected))
    if maximum_error > 0.00001:
        raise AssertionError(maximum_error)
    print("RR_UV_BENCHMARK", {"loops": len(obj.data.loops), "seconds": results, "maximum_error": maximum_error})


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SurfaceUvRegressions))
    if result.wasSuccessful():
        print("RR_SURFACE_UV_REGRESSIONS_PASS")
        if "--benchmark" in sys.argv:
            benchmark()
    raise SystemExit(0 if result.wasSuccessful() else 1)
