"""Native Mix Shaders contracts in an isolated factory Blender process.

Run serially: blender --background --factory-startup --disable-autoexec
--threads 2 --python-exit-code 1 --python tests/test_rr_shader_mixer_blender.py.
One tiny CPU render checks the actual shader math, including empty vs black.
"""

import importlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
mixer = importlib.import_module("random_realm_builder_exporter.rr_shader_mixer")


def material(name="Mixer Target"):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    result.use_fake_user = True
    result.node_tree.nodes.clear()
    base = emission(result.node_tree, "Original Base", (0, 0, 1, 1))
    output = result.node_tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Original Output"
    output.is_active_output = True
    result.node_tree.links.new(base.outputs[0], output.inputs["Surface"])
    return result


def emission(tree, name, color):
    node = tree.nodes.new("ShaderNodeEmission")
    node.name = name
    node.inputs["Color"].default_value = color
    node.inputs["Strength"].default_value = 1
    return node


def links(tree):
    return tuple(sorted((item.from_node.name, item.from_socket.identifier,
                         item.to_node.name, item.to_socket.identifier) for item in tree.links))


def interface(group):
    return tuple((item.identifier, item.name, item.in_out, item.socket_type)
                 for item in group.interface.items_tree if item.item_type == "SOCKET")


def node_inputs(node):
    return tuple((item.identifier, item.default_value)
                 for item in node.inputs if hasattr(item, "default_value"))


class ShaderMixerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mixer.register()

    @classmethod
    def tearDownClass(cls):
        mixer.unregister()

    def setUp(self):
        mixer._OWNER_TREES.clear()
        for item in list(bpy.data.materials):
            bpy.data.materials.remove(item)
        for item in list(bpy.data.node_groups):
            bpy.data.node_groups.remove(item)
        self.target = material()
        self.tree = self.target.node_tree

    def add(self):
        return mixer.add_mix_shaders(self.tree, location=(120, -35))

    def test_disconnected_native_shader_group_leaves_original_graph_unchanged(self):
        before = links(self.tree)
        nodes = list(self.tree.nodes)
        node = self.add()
        self.assertEqual(links(self.tree), before)
        self.assertEqual([item for item in self.tree.nodes if item != node], nodes)
        self.assertEqual(node.bl_idname, "ShaderNodeGroup")
        self.assertEqual(node.node_tree.bl_idname, "ShaderNodeTree")
        self.assertEqual(node.node_tree.color_tag, "SHADER")
        self.assertEqual(tuple(node.location), (120, -35))
        self.assertEqual([item.name for item in node.inputs if not item.hide],
                         ["Base Shader", "Mask 1", "Shader 1"])
        self.assertEqual([(item.name, item.type) for item in node.outputs], [("Shader", "SHADER")])
        self.assertIsNone(node.node_tree.asset_data)

    def test_separate_additions_have_independent_groups(self):
        first, second = self.add(), self.add()
        self.assertNotEqual(first.node_tree, second.node_tree)
        mixer.add_shader_slot(first)
        self.assertEqual(len(mixer._pairs(first.node_tree)), 2)
        self.assertEqual(len(mixer._pairs(second.node_tree)), 1)

    def test_extend_preserves_identifiers_links_mask_values_and_node_identity(self):
        node = self.add()
        base = self.tree.nodes["Original Base"]
        overlay = emission(self.tree, "Red Overlay", (1, 0, 0, 1))
        self.tree.links.new(base.outputs[0], node.inputs["Base Shader"])
        self.tree.links.new(overlay.outputs[0], node.inputs["Shader 1"])
        self.tree.links.new(node.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        node.inputs["Mask 1"].default_value = .37
        mixer.sync_tree(self.tree)
        old_interface = interface(node.node_tree)
        old_links = links(self.tree)
        old_values = node_inputs(node)
        pointer = node.as_pointer()
        self.assertEqual(mixer.add_shader_slot(node), 2)
        self.assertEqual(node.as_pointer(), pointer)
        self.assertTrue(set(old_interface) <= set(interface(node.node_tree)))
        self.assertEqual(links(self.tree), old_links)
        self.assertTrue(set(old_values) <= set(node_inputs(node)))
        self.assertAlmostEqual(node.inputs["Mask 1"].default_value, .37)
        self.assertEqual(node.node_tree.name, "Mix Shaders")
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_extend_copied_multi_user_group_affects_only_selected_node(self):
        first = self.add()
        second = self.tree.nodes.new("ShaderNodeGroup")
        second.node_tree = first.node_tree
        old_group = first.node_tree
        old_interface = interface(old_group)
        second.inputs["Mask 1"].default_value = .81
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], second.inputs["Base Shader"])
        self.tree.links.new(second.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        before = links(self.tree)
        mixer.add_shader_slot(first)
        self.assertNotEqual(first.node_tree, old_group)
        self.assertEqual(second.node_tree, old_group)
        self.assertEqual(interface(old_group), old_interface)
        self.assertAlmostEqual(second.inputs["Mask 1"].default_value, .81)
        self.assertEqual(links(self.tree), before)

    def test_staging_failure_keeps_original_group_and_surrounding_graph(self):
        node = self.add()
        old_group = node.node_tree
        old_interface, before = interface(old_group), links(self.tree)
        groups = set(bpy.data.node_groups)
        with mock.patch.object(mixer, "_build_chain", side_effect=RuntimeError("Injected staging failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected staging failure"):
                mixer.add_shader_slot(node)
        self.assertEqual(node.node_tree, old_group)
        self.assertEqual(interface(old_group), old_interface)
        self.assertEqual(links(self.tree), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_post_swap_failure_restores_group_external_values_and_links(self):
        node = self.add()
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Base Shader"])
        self.tree.links.new(node.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        node.inputs["Mask 1"].default_value = .43
        old_group, before = node.node_tree, links(self.tree)
        values, groups = node_inputs(node), set(bpy.data.node_groups)
        with mock.patch.object(mixer, "sync_tree", side_effect=RuntimeError("Injected swap failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected swap failure"):
                mixer.add_shader_slot(node)
        self.assertEqual(node.node_tree, old_group)
        self.assertEqual(links(self.tree), before)
        self.assertEqual(node_inputs(node), values)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_empty_shader_is_disabled_even_with_white_mask(self):
        node = self.add()
        node.inputs["Mask 1"].default_value = 1
        mixer.sync_tree(self.tree)
        gate = mixer._socket(node.inputs, mixer._pairs(node.node_tree)[0][2])
        self.assertEqual(gate.default_value, 0)
        self.assertTrue(gate.hide and gate.hide_value)

    def test_linked_black_shader_is_enabled_and_disconnect_disables_it(self):
        node = self.add()
        black = emission(self.tree, "Black Shader", (0, 0, 0, 1))
        link = self.tree.links.new(black.outputs[0], node.inputs["Shader 1"])
        mixer.sync_tree(self.tree)
        gate = mixer._socket(node.inputs, mixer._pairs(node.node_tree)[0][2])
        self.assertEqual(gate.default_value, 1)
        self.tree.links.remove(link)
        mixer.sync_tree(self.tree)
        self.assertEqual(gate.default_value, 0)

    def test_copied_nodes_use_independent_connection_gates(self):
        first = self.add()
        second = self.tree.nodes.new("ShaderNodeGroup")
        second.node_tree = first.node_tree
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], first.inputs["Shader 1"])
        mixer.sync_tree(self.tree)
        gate_id = mixer._pairs(first.node_tree)[0][2]
        self.assertEqual(mixer._socket(first.inputs, gate_id).default_value, 1)
        self.assertEqual(mixer._socket(second.inputs, gate_id).default_value, 0)

    def test_priority_is_visible_top_to_bottom(self):
        node = self.add()
        mixer.add_shader_slot(node)
        mixer.add_shader_slot(node)
        group = node.node_tree
        output = group.nodes["Output"]
        current = output.inputs["Shader"].links[0].from_node
        for number in (1, 2, 3):
            self.assertEqual(current.name, "Mix {}".format(number))
            self.assertEqual(current.inputs[2].links[0].from_socket.name, "Shader {}".format(number))
            current = current.inputs[1].links[0].from_node
        self.assertEqual(current.name, "Inputs")
        self.assertEqual([item.name for item in node.inputs if not item.hide],
                         ["Base Shader", "Mask 1", "Shader 1", "Mask 2", "Shader 2", "Mask 3", "Shader 3"])

    def test_internal_edits_and_changed_interface_are_not_overwritten(self):
        node = self.add()
        group = node.node_tree
        group.nodes["Mix 1"].mute = True
        with self.assertRaisesRegex(ValueError, "internals were edited"):
            mixer.add_shader_slot(node)
        self.assertTrue(group.nodes["Mix 1"].mute)
        group.nodes["Mix 1"].mute = False
        group.interface.new_socket(name="Custom", in_out="INPUT", socket_type="NodeSocketFloat")
        with self.assertRaisesRegex(ValueError, "interface was changed"):
            mixer.add_shader_slot(node)
        self.assertEqual(node.node_tree, group)

    def test_animated_group_is_not_rebuilt(self):
        node = self.add()
        group = node.node_tree
        group.nodes["Mix 1"].keyframe_insert(data_path="location", frame=1)
        with self.assertRaisesRegex(ValueError, "animated"):
            mixer.add_shader_slot(node)
        self.assertEqual(node.node_tree, group)

    def test_nested_editor_targets_edit_tree_instead_of_material_root(self):
        nested = bpy.data.node_groups.new("Existing User Group", "ShaderNodeTree")
        context = SimpleNamespace(space_data=SimpleNamespace(type="NODE_EDITOR", tree_type="ShaderNodeTree",
                                  edit_tree=nested, cursor_location=(40, 60)))
        before = links(self.tree)
        self.assertTrue(mixer.RR_OT_add_mix_shaders.poll(context))
        # Blender operators are created through bpy.ops, not Python class
        # construction. This context unit exercises the same execute method with
        # only its report interface supplied, so the nested edit tree is explicit.
        operator = SimpleNamespace(report=lambda _kind, message: self.fail(message))
        self.assertEqual(mixer.RR_OT_add_mix_shaders.execute(operator, context), {"FINISHED"})
        self.assertEqual(links(self.tree), before)
        self.assertEqual(len(nested.nodes), 1)
        self.assertEqual(nested.nodes.active.id_data, nested)
        self.assertEqual(tuple(nested.nodes.active.location), (40, 60))
        self.assertTrue(mixer.RR_OT_add_shader_slot.poll(context))
        self.assertEqual(mixer.RR_OT_add_shader_slot.execute(operator, context), {"FINISHED"})
        self.assertEqual(len(mixer._pairs(nested.nodes.active.node_tree)), 2)
        self.assertEqual(len(nested.nodes), 1)

    def test_geometry_editor_is_not_a_target(self):
        context = SimpleNamespace(space_data=SimpleNamespace(type="NODE_EDITOR", tree_type="GeometryNodeTree",
                                  edit_tree=self.tree))
        self.assertFalse(mixer.RR_OT_add_mix_shaders.poll(context))
        self.assertFalse(mixer.RR_OT_add_shader_slot.poll(context))

    def test_graph_updates_only_sync_relevant_updated_owners(self):
        node = self.add()
        unrelated = bpy.data.objects.new("Unrelated Empty", None)
        fake = SimpleNamespace(updates=[SimpleNamespace(id=unrelated)])
        with mock.patch.object(mixer, "sync_mixers") as sync:
            mixer._on_graph_update(None, fake)
            sync.assert_not_called()
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Shader 1"])
        fake.updates = [SimpleNamespace(id=self.target)]
        mixer._on_graph_update(None, fake)
        self.assertEqual(mixer._socket(node.inputs, mixer._pairs(node.node_tree)[0][2]).default_value, 1)

    def test_index_rediscovers_copied_material_and_nested_owners(self):
        self.add()
        copy = self.target.copy()
        nested = bpy.data.node_groups.new("User Nested", "ShaderNodeTree")
        mixer.add_mix_shaders(nested)
        mixer._OWNER_TREES.clear()
        mixer.rebuild_index()
        self.assertEqual(set(mixer._OWNER_TREES),
                         {self.tree.as_pointer(), copy.node_tree.as_pointer(), nested.as_pointer()})

    def test_shared_images_and_other_group_references_remain_untouched(self):
        image = bpy.data.images.new("Original Image", 1, 1)
        texture = self.tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        existing = bpy.data.node_groups.new("Original Nested Shader", "ShaderNodeTree")
        group_node = self.tree.nodes.new("ShaderNodeGroup")
        group_node.node_tree = existing
        original_name, original_tree = image.name, group_node.node_tree
        images_before = set(bpy.data.images)
        node = self.add()
        mixer.add_shader_slot(node)
        self.assertEqual(texture.image, image)
        self.assertEqual(image.name, original_name)
        self.assertEqual(group_node.node_tree, original_tree)
        self.assertEqual(set(bpy.data.images), images_before)

    def test_save_reload_preserves_native_graph_and_connection_gates_without_addon(self):
        node = self.add()
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Shader 1"])
        node.inputs["Mask 1"].default_value = .72
        mixer.add_shader_slot(node)
        expected, target_name = interface(node.node_tree), self.target.name
        expected_links = links(self.tree)
        with tempfile.TemporaryDirectory(prefix="rr-mixer-persistence-") as temp:
            path = str(Path(temp) / "fixture.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            mixer.unregister()
            try:
                bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
                tree = bpy.data.materials[target_name].node_tree
                restored = next(item for item in tree.nodes if mixer.is_mixer(item))
                self.assertEqual(restored.bl_idname, "ShaderNodeGroup")
                self.assertEqual(interface(restored.node_tree), expected)
                self.assertEqual(links(tree), expected_links)
                self.assertEqual(mixer._socket(restored.inputs, mixer._pairs(restored.node_tree)[0][2]).default_value, 1)
                self.assertAlmostEqual(restored.inputs["Mask 1"].default_value, .72)
            finally:
                mixer.register()
                mixer.rebuild_index()

    def test_undo_redo_expansion_keeps_links(self):
        node = self.add()
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Base Shader"])
        self.tree.links.new(node.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        name, node_name, before = self.target.name, node.name, links(self.tree)
        bpy.ops.ed.undo_push(message="Before Mixer Slot")
        mixer.add_shader_slot(node)
        bpy.ops.ed.undo_push(message="Mixer Slot")
        self.assertEqual(len(mixer._pairs(node.node_tree)), 2)
        self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
        restored = bpy.data.materials[name].node_tree.nodes[node_name]
        self.assertEqual(len(mixer._pairs(restored.node_tree)), 1)
        self.assertEqual(links(restored.id_data), before)
        self.assertIn(restored.id_data.as_pointer(), mixer._OWNER_TREES)
        self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
        restored = bpy.data.materials[name].node_tree.nodes[node_name]
        self.assertEqual(len(mixer._pairs(restored.node_tree)), 2)
        self.assertEqual(links(restored.id_data), before)

    def test_rendered_priority_empty_shader_black_shader_and_fractional_masks(self):
        cases = [
            ("empty overlay keeps base", 1, None, 0, None, (0, 0, 1)),
            ("top overlay", 1, (0, 1, 0, 1), 1, (1, 0, 0, 1), (0, 1, 0)),
            ("lower overlay", 0, (0, 1, 0, 1), 1, (1, 0, 0, 1), (1, 0, 0)),
            ("empty top keeps lower", 1, None, 1, (1, 0, 0, 1), (1, 0, 0)),
            ("linked black is real shader", 1, (0, 0, 0, 1), 1, (1, 0, 0, 1), (0, 0, 0)),
            ("half top over lower", .5, (0, 1, 0, 1), 1, (1, 0, 0, 1), (.5, .5, 0)),
            ("negative mask clamps", -2, (0, 1, 0, 1), 0, None, (0, 0, 1)),
            ("large mask clamps", 4, (0, 1, 0, 1), 0, None, (0, 1, 0)),
        ]
        scene = bpy.data.scenes.new("Mixer Numeric Atlas")
        size, tile_pixels = 3, 12
        for index, (name, mask1, shader1, mask2, shader2, expected) in enumerate(cases):
            mat = material("Atlas " + name)
            tree = mat.node_tree
            node = mixer.add_mix_shaders(tree)
            mixer.add_shader_slot(node)
            tree.links.new(tree.nodes["Original Base"].outputs[0], node.inputs["Base Shader"])
            for number, mask, color in ((1, mask1, shader1), (2, mask2, shader2)):
                # Linked values also test clamping beyond the UI's normal range.
                value = tree.nodes.new("ShaderNodeValue")
                value.outputs[0].default_value = mask
                tree.links.new(value.outputs[0], node.inputs["Mask {}".format(number)])
                if color is not None:
                    overlay = emission(tree, "Overlay {}".format(number), color)
                    tree.links.new(overlay.outputs[0], node.inputs["Shader {}".format(number)])
            tree.links.new(node.outputs[0], tree.nodes["Original Output"].inputs["Surface"])
            mixer.sync_tree(tree)
            x, y = index % size, index // size
            mesh = bpy.data.meshes.new("Tile " + name)
            mesh.from_pydata([(x-.46, y-.46, 0), (x+.46, y-.46, 0),
                              (x+.46, y+.46, 0), (x-.46, y+.46, 0)], [], [(0, 1, 2, 3)])
            mesh.materials.append(mat)
            obj = bpy.data.objects.new("Tile " + name, mesh)
            scene.collection.objects.link(obj)
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.cycles.max_bounces = 0
        scene.render.threads_mode = "FIXED"
        scene.render.threads = 2
        scene.render.resolution_x = scene.render.resolution_y = size * tile_pixels
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "OPEN_EXR"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "32"
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        camera_data = bpy.data.cameras.new("Mixer Test Camera")
        camera = bpy.data.objects.new("Mixer Test Camera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = ((size - 1)/2, (size - 1)/2, 10)
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = size
        scene.camera = camera
        with tempfile.TemporaryDirectory(prefix="rr-mixer-render-") as temp:
            scene.render.filepath = str(Path(temp) / "atlas.exr")
            bpy.ops.render.render(write_still=True, scene=scene.name)
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            pixels, width = list(image.pixels), image.size[0]
            for index, (name, _m1, _s1, _m2, _s2, expected) in enumerate(cases):
                x = (index % size)*tile_pixels + tile_pixels//2
                y = (index // size)*tile_pixels + tile_pixels//2
                rgb = pixels[(y * width + x)*4:(y * width + x)*4+3]
                with self.subTest(sample=name):
                    for actual, wanted in zip(rgb, expected):
                        self.assertAlmostEqual(actual, wanted, delta=.002)
            bpy.data.images.remove(image)
        print("RR_SHADER_MIXER_SHADER_SAMPLES_PASS samples={}".format(len(cases)))


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    suite = unittest.defaultTestLoader.loadTestsFromNames(arguments, sys.modules[__name__]) if arguments else \
        unittest.defaultTestLoader.loadTestsFromTestCase(ShaderMixerTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("RR_SHADER_MIXER_PASS tests={}".format(result.testsRun))
    raise SystemExit(0 if result.wasSuccessful() else 1)
