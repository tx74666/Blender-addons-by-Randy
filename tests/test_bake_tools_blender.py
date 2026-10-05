"""Native bake-helper contracts; serial disposable factory-startup Blender only.

No bake is run. Fixtures use tiny images; Recommend must allocate no images.
"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
import random_realm_builder_exporter as rr
from random_realm_builder_exporter import rr_bake_tools_ui as tools


def select_only(*objects, active=None):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or (objects[0] if objects else None)
    bpy.context.view_layer.update()


def material(name):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    return result


def quad(name, source, *, width=1, height=1, x=0, uv=True):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata([(x, 0, 0), (x + width, 0, 0),
                      (x + width, height, 0), (x, height, 0)], [], [(0, 1, 2, 3)])
    mesh.materials.append(source)
    mesh.update()
    if uv:
        layer = mesh.uv_layers.new(name="BakeUV")
        for loop, value in zip(layer.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
            loop.uv = value
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def graph_state(source):
    def value(socket):
        default = socket.default_value
        return tuple(default) if hasattr(default, "__len__") else default
    return (
        tuple((node.name, node.bl_idname, node.label, tuple(node.location), node.select,
               node.image.as_pointer() if node.bl_idname == "ShaderNodeTexImage" and node.image else None,
               tuple((socket.identifier, value(socket)) for socket in node.inputs
                     if hasattr(socket, "default_value"))) for node in source.node_tree.nodes),
        tuple((link.from_node.name, link.from_socket.identifier,
               link.to_node.name, link.to_socket.identifier) for link in source.node_tree.links),
        source.node_tree.nodes.active.name if source.node_tree.nodes.active else None,
    )


def scene_state():
    objects = []
    for obj in bpy.data.objects:
        geometry = None
        if obj.type == "MESH":
            geometry = (tuple(tuple(v.co) for v in obj.data.vertices),
                        tuple((tuple(face.vertices), face.material_index) for face in obj.data.polygons),
                        tuple((layer.name, tuple(tuple(loop.uv) for loop in layer.data))
                              for layer in obj.data.uv_layers))
        objects.append((obj.name, obj.type, obj.data.as_pointer() if obj.data else None,
                        obj.parent, tuple(tuple(row) for row in obj.matrix_world), geometry,
                        tuple((slot.link, slot.material) for slot in obj.material_slots)))
    return (tuple(objects), tuple(obj.name for obj in bpy.context.selected_objects),
            bpy.context.view_layer.objects.active, bpy.context.mode)


def image_state(image):
    return (image.name, tuple(image.size), tuple(image.pixels[:]),
            image.colorspace_settings.name, image.filepath_raw, image.file_format, image.is_dirty)


class BakeToolsNativeTests(unittest.TestCase):
    def setUp(self):
        for scene in bpy.data.scenes:
            rr.clear_reference_object(scene)
            scene.rr_builder_export_settings.export_queue.clear()
        for collection in (bpy.data.objects, bpy.data.materials, bpy.data.meshes, bpy.data.images):
            for item in list(collection):
                collection.remove(item, do_unlink=True)
        rr.reset_object_manager_duplicate_guard()
        rr.reset_object_manager_name_sync_state()
        rr.reset_scene_selection_queue_lookup()
        bpy.context.scene.unit_settings.scale_length = 1
        self.settings = bpy.context.scene.rr_builder_export_settings
        self.settings.pbr_bake_texel_density = 128
        self.settings.pbr_bake_uv_utilization = 1
        self.settings.pbr_framework_use_material_filter = False
        self.settings.pbr_framework_use_role_filter = True
        rr.set_pbr_framework_selected_role_keys(self.settings, {"BaseColor", "Roughness", "Metallic"})
        self.directory = tempfile.TemporaryDirectory(prefix="rr_native_bake_tools_")
        self.addCleanup(self.directory.cleanup)

    def filter_materials(self, *sources):
        self.settings.pbr_framework_use_material_filter = True
        rr.set_pbr_framework_selected_material_names(self.settings, [source.name for source in sources])

    def saved_target(self, source, role_key="BaseColor"):
        role = next(role for role in rr.PBR_BAKE_ROLES if role["key"] == role_key)
        node = rr.find_or_create_pbr_bake_image_node(source, role)
        image = bpy.data.images.new(source.name + "Saved" + role_key, width=2, height=2, alpha=True)
        image.colorspace_settings.name = role["colorspace"]
        color = {"BaseColor": [0.2, 0.4, 0.6, 1], "Roughness": [0.23, 0.23, 0.23, 1],
                 "Metallic": [0.67, 0.67, 0.67, 1], "Normal": [0.5, 0.5, 1, 1]}[role_key]
        image.pixels[:] = color * 4
        image.filepath_raw = str(Path(self.directory.name) / (source.name + "_" + role_key + ".png"))
        image.file_format = "PNG"
        image.save()
        node.image = image
        return node, image, role

    def complete_saved_targets(self, source):
        return {key: self.saved_target(source, key) for key in ("BaseColor", "Roughness", "Metallic")}

    def test_evaluated_mirror_nonuniform_scale_and_scene_units_use_actual_world_area(self):
        source = material("Floor")
        obj = quad("Floor", source, x=1)
        obj.scale = (2, 3, 0.5)
        obj.rotation_euler.z = 0.37
        modifier = obj.modifiers.new("Mirrored surface", "MIRROR")
        modifier.use_axis[0] = True
        bpy.context.scene.unit_settings.scale_length = 0.1
        select_only(obj)
        before, images = scene_state(), set(bpy.data.images)
        result = tools.analyze_bake_selection(bpy.context, self.settings)
        self.assertEqual(1, result["mesh_count"])
        self.assertEqual("Floor", result["records"][0]["material"])
        self.assertAlmostEqual(0.12, result["records"][0]["area_m2"], places=7)
        self.assertEqual(before, scene_state())
        self.assertEqual(images, set(bpy.data.images))

    def test_shared_mesh_instances_and_object_material_overrides_are_counted_separately(self):
        first_source, overridden_source = material("Shared"), material("ObjectOverride")
        first = quad("First", first_source)
        second = bpy.data.objects.new("Second", first.data)
        bpy.context.scene.collection.objects.link(second)
        second.scale = (2, 2, 1)
        second.location.x = 3
        second.material_slots[0].link = "OBJECT"
        second.material_slots[0].material = overridden_source
        unselected = bpy.data.objects.new("Unselected shared user", first.data)
        bpy.context.scene.collection.objects.link(unselected)
        unselected.scale = (10, 10, 1)
        select_only(first, second)
        before = scene_state()
        result = tools.analyze_bake_selection(bpy.context, self.settings)
        by_material = {row["material"]: row["area_m2"] for row in result["records"]}
        self.assertEqual(2, result["mesh_count"])
        self.assertAlmostEqual(1, by_material["Shared"])
        self.assertAlmostEqual(4, by_material["ObjectOverride"])
        self.assertEqual(before, scene_state())
        self.filter_materials(overridden_source)
        filtered = tools.analyze_bake_selection(bpy.context, self.settings)
        self.assertEqual(["ObjectOverride"], [row["material"] for row in filtered["records"]])
        self.assertAlmostEqual(4, filtered["records"][0]["area_m2"])

    def test_selected_empty_asset_children_respect_the_material_filter(self):
        first_source, second_source = material("Small"), material("Large")
        asset = bpy.data.objects.new("Asset", None)
        bpy.context.scene.collection.objects.link(asset)
        first = quad("SmallPart", first_source)
        second = quad("LargePart", second_source, width=10, height=10)
        first.parent = second.parent = asset
        select_only(asset)
        self.filter_materials(first_source)
        before = scene_state()
        result = tools.analyze_bake_selection(bpy.context, self.settings)
        self.assertEqual(["Small"], [row["material"] for row in result["records"]])
        self.assertAlmostEqual(1, result["records"][0]["area_m2"])
        self.assertEqual(2, result["mesh_count"])
        self.assertEqual(before, scene_state())

    def test_missing_uv_is_reported_without_unwrap_or_image_allocation(self):
        source = material("NoUV")
        obj = quad("NoUV", source, uv=False)
        select_only(obj)
        before, source_graph, images = scene_state(), graph_state(source), set(bpy.data.images)
        result = tools.analyze_bake_selection(bpy.context, self.settings)
        self.assertEqual([obj.name], result["missing_uv"])
        self.assertTrue(result["records"][0]["uv_utilization_estimated"])
        self.assertEqual(before, scene_state())
        self.assertEqual(source_graph, graph_state(source))
        self.assertEqual(images, set(bpy.data.images))

    def test_recommend_operator_changes_size_and_summary_only_and_keeps_existing_targets(self):
        source = material("Floor")
        obj = quad("Floor", source, width=10, height=10)
        node, image, _role = self.saved_target(source)
        select_only(obj)
        self.settings.pbr_bake_resolution = 512
        before, source_graph, images = scene_state(), graph_state(source), set(bpy.data.images)
        original_image = image_state(image)
        result = bpy.ops.rr_builder.recommend_pbr_bake_size("EXEC_DEFAULT")
        self.assertEqual({"FINISHED"}, result)
        self.assertEqual(2048, self.settings.pbr_bake_resolution)
        analysis = json.loads(self.settings.pbr_bake_size_analysis)
        self.assertEqual(2048, analysis["recommended_resolution"])
        self.assertAlmostEqual(100, analysis["records"][0]["area_m2"])
        self.assertIn("Last estimate", self.settings.pbr_bake_size_summary)
        self.assertEqual(before, scene_state())
        self.assertEqual(source_graph, graph_state(source))
        self.assertEqual(images, set(bpy.data.images))
        self.assertEqual(original_image, image_state(image))
        self.assertEqual(image, node.image)

    def test_different_target_size_creates_new_image_and_preserves_old_shared_pixels(self):
        source, other = material("Source"), material("OtherUser")
        node, image, role = self.saved_target(source)
        other_node = other.node_tree.nodes.new("ShaderNodeTexImage")
        other_node.image = image
        before, other_graph = image_state(image), graph_state(other)
        old_image_count = len(bpy.data.images)
        # Use tiny dimensions to test the replacement path without allocating
        # full bake textures; ensure_unique does not clamp this low-level input.
        replacement = rr.ensure_unique_pbr_framework_node_image(
            source, role, node, 4, self.directory.name)
        self.assertNotEqual(image, replacement)
        self.assertEqual((4, 4), tuple(replacement.size))
        self.assertEqual(replacement, node.image)
        self.assertEqual(image, other_node.image)
        self.assertEqual(before, image_state(image))
        self.assertEqual(other_graph, graph_state(other))
        self.assertEqual(old_image_count + 1, len(bpy.data.images))

    def test_create_baked_material_default_keeps_source_graph_and_every_object_slot(self):
        source = material("ArtistMaterial")
        selected = quad("Selected", source)
        other = bpy.data.objects.new("Other shared user", selected.data)
        bpy.context.scene.collection.objects.link(other)
        targets = self.complete_saved_targets(source)
        _node, image, _role = targets["BaseColor"]
        select_only(selected)
        before, source_graph = scene_state(), graph_state(source)
        old_images, saved_image = set(bpy.data.images), image_state(image)
        old_materials = set(bpy.data.materials)
        result = bpy.ops.rr_builder.create_baked_pbr_material("EXEC_DEFAULT")
        self.assertEqual({"FINISHED"}, result)
        created = set(bpy.data.materials) - old_materials
        self.assertEqual(1, len(created))
        baked = next(iter(created))
        self.assertEqual(source.name + "_baked", baked.name)
        self.assertEqual("preview_required", baked["rr_pbr_baked_quality"])
        self.assertEqual(before, scene_state())
        self.assertEqual(source_graph, graph_state(source))
        self.assertEqual(saved_image, image_state(image))
        self.assertEqual(old_images, set(bpy.data.images))
        self.assertTrue(all(obj.material_slots[0].material == source for obj in (selected, other)))

    def test_opt_in_assigns_object_slots_only_on_selected_users_of_shared_mesh(self):
        source = material("ArtistMaterial")
        selected = quad("Selected", source)
        other = bpy.data.objects.new("Other shared user", selected.data)
        bpy.context.scene.collection.objects.link(other)
        targets = self.complete_saved_targets(source)
        _node, image, _role = targets["BaseColor"]
        # The active object is deliberately an unselected shared-mesh user.
        # Explicit opt-in assignment still must follow the selected scope.
        select_only(selected, active=other)
        entries = tools.manual_baked_inputs(bpy.context, self.settings)
        preview_signature = tools.baked_preview_signature(bpy.context, entries)
        select_only(selected, other, active=other)
        self.assertNotEqual(preview_signature, tools.baked_preview_signature(bpy.context, entries))
        select_only(selected, active=other)
        source_graph, saved_image = graph_state(source), image_state(image)
        old_materials = set(bpy.data.materials)
        result = bpy.ops.rr_builder.create_baked_pbr_material("EXEC_DEFAULT", use_on_selected=True)
        self.assertEqual({"FINISHED"}, result)
        created = set(bpy.data.materials) - old_materials
        self.assertEqual(1, len(created))
        baked = next(iter(created))
        self.assertEqual("OBJECT", selected.material_slots[0].link)
        self.assertEqual(baked, selected.material_slots[0].material)
        self.assertEqual("DATA", other.material_slots[0].link)
        self.assertEqual(source, other.material_slots[0].material)
        self.assertEqual(source, selected.data.materials[0])
        self.assertEqual(selected.data, other.data)
        self.assertEqual([selected.name], [obj.name for obj in bpy.context.selected_objects])
        self.assertEqual(other, bpy.context.view_layer.objects.active)
        self.assertEqual(source_graph, graph_state(source))
        self.assertEqual(saved_image, image_state(image))

    def test_automatic_mixed_shader_is_refused_before_image_allocation_or_source_changes(self):
        source = material("MixedSource")
        obj = quad("MixedAsset", source)
        tree = source.node_tree
        shader = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
        output = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial")
        mix = tree.nodes.new("ShaderNodeMixShader")
        mix.inputs[0].default_value = 0.35
        tree.links.new(shader.outputs["BSDF"], mix.inputs[1])
        tree.links.new(shader.outputs["BSDF"], mix.inputs[2])
        tree.links.new(mix.outputs[0], output.inputs["Surface"])
        select_only(obj)
        before, source_graph = scene_state(), graph_state(source)
        images, materials = set(bpy.data.images), set(bpy.data.materials)
        with mock.patch.object(rr, "create_bake_image") as allocate, \
                mock.patch.object(rr, "bake_active_meshes") as bake:
            with self.assertRaisesRegex(RuntimeError, "mixed shader"):
                rr.bake_selected_to_pbr(bpy.context, self.settings)
            allocate.assert_not_called()
            bake.assert_not_called()
        self.assertEqual(before, scene_state())
        self.assertEqual(source_graph, graph_state(source))
        self.assertEqual(images, set(bpy.data.images))
        self.assertEqual(materials, set(bpy.data.materials))

    def test_automatic_direct_shader_bakes_active_inputs_and_restores_source_before_object_assignment(self):
        source = material("ProceduralSource")
        selected = quad("SelectedAutoAsset", source)
        other = bpy.data.objects.new("Other auto shared user", selected.data)
        bpy.context.scene.collection.objects.link(other)
        tree = source.node_tree
        shader = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
        output = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial")
        shader.inputs["Roughness"].default_value = 0.23
        shader.inputs["Metallic"].default_value = 0.67
        noise = tree.nodes.new("ShaderNodeTexNoise")
        tree.links.new(noise.outputs["Color"], shader.inputs["Base Color"])
        # A legacy Source frame may contain another shader. Automatic bake's
        # preflight and channel extraction must agree on the active Surface.
        frame = tree.nodes.new("NodeFrame")
        frame.label = rr.PBR_BAKE_FRAME_LABEL
        decoy = tree.nodes.new("ShaderNodeBsdfPrincipled")
        decoy.parent = frame
        decoy.inputs["Roughness"].default_value = 0.99
        decoy.inputs["Metallic"].default_value = 0.01
        select_only(selected)
        self.settings.pbr_bake_output_root = self.directory.name
        source_links = {(link.from_node.name, link.from_socket.identifier,
                         link.to_node.name, link.to_socket.identifier) for link in tree.links}
        original_values = (shader.inputs["Roughness"].default_value,
                           shader.inputs["Metallic"].default_value)
        before = scene_state()
        engine = bpy.context.scene.render.engine
        samples = bpy.context.scene.cycles.samples
        old_materials = set(bpy.data.materials)
        visited = []

        def fake_bake(role, _settings):
            key = role["key"]
            visited.append(key)
            active_surface = output.inputs["Surface"].links[0].from_node
            if key == "Normal":
                self.assertEqual(shader, active_surface)
            else:
                self.assertEqual("ShaderNodeEmission", active_surface.bl_idname)
                color_input = active_surface.inputs["Color"]
                if key == "BaseColor":
                    self.assertEqual(noise, color_input.links[0].from_node)
                else:
                    self.assertFalse(color_input.links)
                    expected = original_values[0 if key == "Roughness" else 1]
                    self.assertAlmostEqual(expected, color_input.default_value[0])
            image = tree.nodes.active.image
            color = {"BaseColor": [0.2, 0.4, 0.6, 1], "Roughness": [0.23, 0.23, 0.23, 1],
                     "Metallic": [0.67, 0.67, 0.67, 1], "Normal": [0.5, 0.5, 1, 1]}[key]
            image.pixels[:] = color * 4

        with mock.patch.object(rr, "clamp_pbr_bake_size", return_value=2), \
                mock.patch.object(rr, "bake_active_meshes", side_effect=fake_bake):
            result = rr.bake_selected_to_pbr(bpy.context, self.settings)
        self.assertEqual([role["key"] for role in rr.PBR_BAKE_ROLES], visited)
        self.assertEqual(1, result["material_count"])
        self.assertEqual(1, result["relinked_count"])
        self.assertEqual(4, result["image_count"])
        self.assertTrue(all(Path(path).is_file() for path in result["files"]))
        created = set(bpy.data.materials) - old_materials
        self.assertEqual(1, len(created))
        baked = next(iter(created))
        self.assertEqual(source.name + "_baked", baked.name)
        self.assertEqual("preview_required", baked["rr_pbr_baked_quality"])
        self.assertEqual("OBJECT", selected.material_slots[0].link)
        self.assertEqual(baked, selected.material_slots[0].material)
        self.assertEqual("DATA", other.material_slots[0].link)
        self.assertEqual(source, other.material_slots[0].material)
        self.assertEqual(source, selected.data.materials[0])
        after = scene_state()
        self.assertEqual(before[1:], after[1:])
        self.assertEqual(tuple(row[:-1] for row in before[0]), tuple(row[:-1] for row in after[0]))
        self.assertEqual(source_links, {(link.from_node.name, link.from_socket.identifier,
                                        link.to_node.name, link.to_socket.identifier) for link in tree.links})
        self.assertEqual(original_values, (shader.inputs["Roughness"].default_value,
                                          shader.inputs["Metallic"].default_value))
        self.assertFalse(any(node.bl_idname == "ShaderNodeEmission" for node in tree.nodes))
        self.assertEqual(engine, bpy.context.scene.render.engine)
        self.assertEqual(samples, bpy.context.scene.cycles.samples)


def main():
    if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
        raise RuntimeError("Use a disposable --factory-startup Blender process.")
    if Path(rr.__file__).resolve() != (ROOT / "addons" / "random_realm_builder_exporter" / "__init__.py").resolve():
        raise RuntimeError("Import the canonical repository package for these checks.")
    rr.register()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(BakeToolsNativeTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise RuntimeError("Native bake tools checks failed.")
        print(f"RR_BAKE_TOOLS_BLENDER_PASS tests={result.testsRun}")
    finally:
        rr.unregister()


if __name__ == "__main__":
    main()
