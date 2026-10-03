"""Native Mix Shaders contracts in an isolated factory Blender process.

Run serially: blender --background --factory-startup --disable-autoexec
--threads 2 --python-exit-code 1 --python tests/test_rr_shader_mixer_blender.py.
Tiny CPU renders check native math, including edits with RR Helper disabled.
"""

import importlib
import json
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


def legacy_group():
    """Build a v1 fixture without changing what new user nodes create."""
    group = bpy.data.node_groups.new("Legacy Mix Shaders", "ShaderNodeTree")
    group[mixer._KIND] = 1
    base = group.interface.new_socket(name="Base Shader", in_out="INPUT", socket_type="NodeSocketShader")
    output = group.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    group[mixer._BASE], group[mixer._OUTPUT] = base.identifier, output.identifier
    group[mixer._PAIRS] = json.dumps([mixer._new_pair(group, 1)])
    mixer._build_chain(group)
    return group


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

    def add_legacy(self, tree=None):
        tree = tree or self.tree
        node = tree.nodes.new("ShaderNodeGroup")
        node.node_tree = legacy_group()
        mixer.sync_tree(tree)
        mixer._remember_owner(tree)
        return node

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
                         ["Base Shader", "Mask 1", "Shader 1", "Mask 2", "Shader 2"])
        self.assertEqual(node.node_tree.get(mixer._KIND), 2)
        self.assertFalse(any(item.name.startswith("_Connected") for item in node.inputs))
        self.assertEqual([(item.name, item.type) for item in node.outputs], [("Shader", "SHADER")])
        self.assertIsNone(node.node_tree.asset_data)

    def test_separate_additions_have_independent_groups(self):
        first, second = self.add(), self.add()
        self.assertNotEqual(first.node_tree, second.node_tree)
        mixer.add_shader_slot(first)
        self.assertEqual(len(mixer._pairs(first.node_tree)), 3)
        self.assertEqual(len(mixer._pairs(second.node_tree)), 2)

    def test_expanding_library_asset_keeps_template_and_clears_copy_asset_status(self):
        node = self.add()
        original = node.node_tree
        original.asset_mark()
        original.use_fake_user = True
        original.asset_data.description = "Reusable library template"
        interface_before = interface(original)
        mixer.add_shader_slot(node)
        self.assertNotEqual(node.node_tree, original)
        self.assertIsNone(node.node_tree.asset_data)
        self.assertFalse(node.node_tree.use_fake_user)
        self.assertIsNotNone(original.asset_data)
        self.assertTrue(original.use_fake_user)
        self.assertEqual(interface(original), interface_before)
        self.assertEqual(original.asset_data.description, "Reusable library template")

    def test_expand_linked_asset_forks_local_copy_without_changing_library_or_other_node(self):
        template = mixer._new_group()
        template.name = "Linked Mix Shaders Template"
        template.asset_mark()
        template.use_fake_user = True
        with tempfile.TemporaryDirectory(prefix="rr-mixer-linked-") as temp:
            path = Path(temp) / "mixer-library.blend"
            bpy.data.libraries.write(str(path), {template}, fake_user=True)
            bpy.data.node_groups.remove(template)
            with bpy.data.libraries.load(str(path), link=True) as (available, requested):
                self.assertIn("Linked Mix Shaders Template", available.node_groups)
                requested.node_groups = ["Linked Mix Shaders Template"]
            linked = requested.node_groups[0]
            self.assertIsNotNone(linked.library)
            self.assertFalse(linked.is_editable)
            first = self.tree.nodes.new("ShaderNodeGroup")
            second = self.tree.nodes.new("ShaderNodeGroup")
            first.node_tree = second.node_tree = linked
            first.inputs["Mask 1"].default_value = .37
            second.inputs["Mask 1"].default_value = .81
            self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], first.inputs["Base Shader"])
            self.tree.links.new(first.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
            before_links = links(self.tree)
            before_interface = interface(linked)
            before_signature = mixer._graph_signature(linked)
            before_file = path.read_bytes()
            self.assertEqual(mixer.add_shader_slot(first), 3)
            self.assertNotEqual(first.node_tree, linked)
            self.assertIsNone(first.node_tree.library)
            self.assertTrue(first.node_tree.is_editable)
            self.assertIsNone(first.node_tree.asset_data)
            self.assertFalse(first.node_tree.use_fake_user)
            self.assertEqual(second.node_tree, linked)
            self.assertEqual(interface(linked), before_interface)
            self.assertEqual(mixer._graph_signature(linked), before_signature)
            self.assertEqual(links(self.tree), before_links)
            self.assertAlmostEqual(first.inputs["Mask 1"].default_value, .37)
            self.assertAlmostEqual(second.inputs["Mask 1"].default_value, .81)
            self.assertEqual(path.read_bytes(), before_file)

    def test_expand_single_linked_instance_keeps_unreferenced_library_template(self):
        template = mixer._new_group()
        template.name = "Single Linked Template"
        template.asset_mark()
        with tempfile.TemporaryDirectory(prefix="rr-mixer-single-linked-") as temp:
            path = Path(temp) / "mixer-library.blend"
            bpy.data.libraries.write(str(path), {template}, fake_user=True)
            bpy.data.node_groups.remove(template)
            with bpy.data.libraries.load(str(path), link=True) as (_available, requested):
                requested.node_groups = ["Single Linked Template"]
            linked = requested.node_groups[0]
            node = self.tree.nodes.new("ShaderNodeGroup")
            node.node_tree = linked
            self.assertIsNotNone(linked.library)
            before = mixer._graph_signature(linked)
            library_bytes = path.read_bytes()
            self.assertEqual(mixer.add_shader_slot(node), 3)
            self.assertIsNotNone(bpy.data.node_groups.get("Single Linked Template"))
            self.assertEqual(mixer._graph_signature(linked), before)
            self.assertIsNotNone(linked.library)
            self.assertIsNone(node.node_tree.library)
            self.assertEqual(path.read_bytes(), library_bytes)

    def test_expand_single_local_asset_instance_keeps_template_without_a_fake_user(self):
        node = self.add()
        template = node.node_tree
        template.asset_mark()
        template.use_fake_user = False
        name = template.name
        before = mixer._graph_signature(template)
        self.assertEqual(mixer.add_shader_slot(node), 3)
        self.assertNotEqual(node.node_tree, template)
        self.assertEqual(template.users, 0)
        self.assertEqual(bpy.data.node_groups.get(name), template)
        self.assertIsNotNone(template.asset_data)
        self.assertFalse(template.use_fake_user)
        self.assertEqual(mixer._graph_signature(template), before)
        self.assertIsNone(node.node_tree.asset_data)
        self.assertFalse(node.node_tree.use_fake_user)

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
        self.assertEqual(mixer.add_shader_slot(node), 3)
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

    def test_missing_target_rejects_removal_without_creating_or_changing_groups(self):
        node = self.add()
        before = (set(bpy.data.node_groups.keys()), interface(node.node_tree), links(self.tree))
        self.assertFalse(mixer.is_mixer(None))
        with self.assertRaisesRegex(ValueError, "Select a Mix Shaders"):
            mixer.remove_shader_slot(None)
        self.assertEqual((set(bpy.data.node_groups.keys()), interface(node.node_tree), links(self.tree)), before)

    def test_remove_three_to_two_to_one_preserves_retained_sockets_values_and_links(self):
        node = self.add()
        mixer.add_shader_slot(node)
        pointer = node.as_pointer()
        base = self.tree.nodes["Original Base"]
        red = emission(self.tree, "Retained Red", (1, 0, 0, 1))
        green = emission(self.tree, "Retained Green", (0, 1, 0, 1))
        factor = self.tree.nodes.new("ShaderNodeValue")
        factor.name = "Retained Factor"
        factor.outputs[0].default_value = .63
        consumer = self.tree.nodes.new("ShaderNodeAddShader")
        consumer.name = "Retained Output Consumer"
        self.tree.links.new(base.outputs[0], node.inputs["Base Shader"])
        self.tree.links.new(red.outputs[0], node.inputs["Shader 1"])
        self.tree.links.new(green.outputs[0], node.inputs["Shader 2"])
        self.tree.links.new(factor.outputs[0], node.inputs["Mask 2"])
        self.tree.links.new(node.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        self.tree.links.new(node.outputs[0], consumer.inputs[0])
        node.inputs["Mask 1"].default_value = .37
        node.inputs["Mask 2"].default_value = .63
        identifiers = {socket.name: socket.identifier for socket in node.inputs}
        output_id = node.outputs[0].identifier
        before_links = links(self.tree)
        before_nodes = {item.as_pointer() for item in self.tree.nodes}
        self.assertEqual(mixer.remove_shader_slot(node), 2)
        self.assertEqual(node.as_pointer(), pointer)
        self.assertEqual(links(self.tree), before_links)
        self.assertEqual({item.as_pointer() for item in self.tree.nodes}, before_nodes)
        self.assertEqual({socket.name: socket.identifier for socket in node.inputs},
                         {name: identifier for name, identifier in identifiers.items()
                          if name not in {"Mask 3", "Shader 3"}})
        self.assertEqual(node.outputs[0].identifier, output_id)
        self.assertAlmostEqual(node.inputs["Mask 1"].default_value, .37)
        self.assertAlmostEqual(node.inputs["Mask 2"].default_value, .63)
        expected_links = tuple(item for item in before_links if not
                               (item[2] == node.name and item[3] in
                                {identifiers["Mask 2"], identifiers["Shader 2"]}))
        self.assertEqual(mixer.remove_shader_slot(node), 1)
        self.assertEqual(node.as_pointer(), pointer)
        self.assertEqual(links(self.tree), expected_links)
        self.assertEqual({item.as_pointer() for item in self.tree.nodes}, before_nodes)
        self.assertEqual({socket.name: socket.identifier for socket in node.inputs},
                         {name: identifiers[name] for name in ("Base Shader", "Mask 1", "Shader 1")})
        self.assertEqual(node.outputs[0].identifier, output_id)
        self.assertAlmostEqual(node.inputs["Mask 1"].default_value, .37)
        self.assertEqual(node.node_tree.name, "Mix Shaders")
        self.assertEqual(len(bpy.data.node_groups), 1)
        mixer._validate_group(node.node_tree)

    def test_remove_rejects_last_remaining_pair_without_changing_graph(self):
        node = self.add()
        self.assertEqual(mixer.remove_shader_slot(node), 1)
        node.inputs["Mask 1"].default_value = .54
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Base Shader"])
        self.tree.links.new(node.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        group = node.node_tree
        before = (interface(group), mixer._graph_signature(group), links(self.tree), node_inputs(node))
        groups = set(bpy.data.node_groups)
        with self.assertRaises(ValueError):
            mixer.remove_shader_slot(node)
        self.assertEqual(node.node_tree, group)
        self.assertEqual((interface(group), mixer._graph_signature(group), links(self.tree), node_inputs(node)), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_remove_shared_asset_pair_only_changes_requested_instance(self):
        first = self.add()
        mixer.add_shader_slot(first)
        template = first.node_tree
        template.asset_mark()
        template.asset_data.description = "Keep reusable three-pair template"
        template.use_fake_user = True
        second = self.tree.nodes.new("ShaderNodeGroup")
        second.node_tree = template
        second.inputs["Mask 3"].default_value = .81
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], second.inputs["Shader 3"])
        self.tree.links.new(second.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        before = (interface(template), mixer._graph_signature(template), links(self.tree), node_inputs(second))
        self.assertEqual(mixer.remove_shader_slot(first), 2)
        self.assertNotEqual(first.node_tree, template)
        self.assertEqual(second.node_tree, template)
        self.assertEqual((interface(template), mixer._graph_signature(template), links(self.tree), node_inputs(second)), before)
        self.assertIsNotNone(template.asset_data)
        self.assertEqual(template.asset_data.description, "Keep reusable three-pair template")
        self.assertTrue(template.use_fake_user)
        self.assertIsNone(first.node_tree.asset_data)
        self.assertFalse(first.node_tree.use_fake_user)

    def test_remove_linked_asset_pair_forks_local_group_and_keeps_library_bytes(self):
        template = mixer._new_group()
        template.name = "Linked Removable Mixer Template"
        template.asset_mark()
        with tempfile.TemporaryDirectory(prefix="rr-mixer-remove-linked-") as temp:
            path = Path(temp) / "mixer-library.blend"
            bpy.data.libraries.write(str(path), {template}, fake_user=True)
            bpy.data.node_groups.remove(template)
            with bpy.data.libraries.load(str(path), link=True) as (_available, requested):
                requested.node_groups = ["Linked Removable Mixer Template"]
            linked = requested.node_groups[0]
            first = self.tree.nodes.new("ShaderNodeGroup")
            second = self.tree.nodes.new("ShaderNodeGroup")
            first.node_tree = second.node_tree = linked
            first.inputs["Mask 1"].default_value = .37
            second.inputs["Mask 2"].default_value = .81
            self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], first.inputs["Base Shader"])
            self.tree.links.new(first.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
            before = (interface(linked), mixer._graph_signature(linked), links(self.tree), path.read_bytes())
            self.assertEqual(mixer.remove_shader_slot(first), 1)
            self.assertNotEqual(first.node_tree, linked)
            self.assertIsNone(first.node_tree.library)
            self.assertTrue(first.node_tree.is_editable)
            self.assertIsNone(first.node_tree.asset_data)
            self.assertFalse(first.node_tree.use_fake_user)
            self.assertEqual(second.node_tree, linked)
            self.assertIsNotNone(linked.library)
            self.assertEqual((interface(linked), mixer._graph_signature(linked), links(self.tree), path.read_bytes()), before)
            self.assertAlmostEqual(first.inputs["Mask 1"].default_value, .37)
            self.assertAlmostEqual(second.inputs["Mask 2"].default_value, .81)

    def test_remove_connected_pair_discards_its_cables_but_keeps_sources_and_other_consumers(self):
        node = self.add()
        red = emission(self.tree, "Removed Slot Shader Source", (1, 0, 0, 1))
        factor = self.tree.nodes.new("ShaderNodeValue")
        factor.name = "Removed Slot Mask Source"
        factor.outputs[0].default_value = .74
        other_mix = self.tree.nodes.new("ShaderNodeMixShader")
        other_mix.name = "Independent Shader Consumer"
        self.tree.links.new(red.outputs[0], node.inputs["Shader 2"])
        self.tree.links.new(factor.outputs[0], node.inputs["Mask 2"])
        self.tree.links.new(red.outputs[0], other_mix.inputs[2])
        self.tree.links.new(factor.outputs[0], other_mix.inputs[0])
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Base Shader"])
        self.tree.links.new(node.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        removed_ids = {node.inputs["Mask 2"].identifier, node.inputs["Shader 2"].identifier}
        expected = tuple(item for item in links(self.tree)
                         if not (item[2] == node.name and item[3] in removed_ids))
        before_nodes = {item.as_pointer() for item in self.tree.nodes}
        self.assertEqual(mixer.remove_shader_slot(node), 1)
        self.assertEqual(links(self.tree), expected)
        self.assertEqual({item.as_pointer() for item in self.tree.nodes}, before_nodes)
        self.assertAlmostEqual(factor.outputs[0].default_value, .74)
        self.assertEqual(other_mix.inputs[2].links[0].from_node, red)
        self.assertEqual(other_mix.inputs[0].links[0].from_node, factor)

    def test_remove_rejects_manually_edited_group_without_creating_a_candidate(self):
        node = self.add()
        group = node.node_tree
        group.nodes["Mix 1"].mute = True
        before = (interface(group), mixer._graph_signature(group), links(self.tree), node_inputs(node))
        groups = set(bpy.data.node_groups)
        with self.assertRaisesRegex(ValueError, "internals were edited"):
            mixer.remove_shader_slot(node)
        self.assertEqual(node.node_tree, group)
        self.assertEqual((interface(group), mixer._graph_signature(group), links(self.tree), node_inputs(node)), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_remove_staging_failure_keeps_original_group_and_has_no_leaked_candidate(self):
        node = self.add()
        group = node.node_tree
        before = (interface(group), mixer._graph_signature(group), links(self.tree), node_inputs(node))
        groups = set(bpy.data.node_groups)
        with mock.patch.object(mixer, "_build_chain", side_effect=RuntimeError("Injected remove staging failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected remove staging failure"):
                mixer.remove_shader_slot(node)
        self.assertEqual(node.node_tree, group)
        self.assertEqual((interface(group), mixer._graph_signature(group), links(self.tree), node_inputs(node)), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_remove_post_swap_failure_restores_removed_cables_values_and_group_without_leaks(self):
        node = self.add()
        red = emission(self.tree, "Rollback Shader Source", (1, 0, 0, 1))
        factor = self.tree.nodes.new("ShaderNodeValue")
        self.tree.links.new(red.outputs[0], node.inputs["Shader 2"])
        self.tree.links.new(factor.outputs[0], node.inputs["Mask 2"])
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Base Shader"])
        self.tree.links.new(node.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        node.inputs["Mask 1"].default_value = .43
        node.inputs["Mask 2"].default_value = .76
        group = node.node_tree
        before = (interface(group), mixer._graph_signature(group), links(self.tree), node_inputs(node))
        groups = set(bpy.data.node_groups)
        restore = mixer._restore_external_state
        calls = 0

        def fail_after_swap_restore(candidate_node, state):
            nonlocal calls
            calls += 1
            restore(candidate_node, state)
            if calls == 1:
                self.assertNotEqual(candidate_node.node_tree, group)
                raise RuntimeError("Injected remove commit failure")

        with mock.patch.object(mixer, "_restore_external_state", side_effect=fail_after_swap_restore):
            with self.assertRaisesRegex(RuntimeError, "Injected remove commit failure"):
                mixer.remove_shader_slot(node)
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(node.node_tree, group)
        self.assertEqual((interface(group), mixer._graph_signature(group), links(self.tree), node_inputs(node)), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_legacy_empty_shader_is_disabled_even_with_white_mask(self):
        node = self.add_legacy()
        node.inputs["Mask 1"].default_value = 1
        mixer.sync_tree(self.tree)
        gate = mixer._socket(node.inputs, mixer._pairs(node.node_tree)[0][2])
        self.assertEqual(gate.default_value, 0)
        self.assertTrue(gate.hide and gate.hide_value)

    def test_legacy_linked_black_shader_is_enabled_and_disconnect_disables_it(self):
        node = self.add_legacy()
        black = emission(self.tree, "Black Shader", (0, 0, 0, 1))
        link = self.tree.links.new(black.outputs[0], node.inputs["Shader 1"])
        mixer.sync_tree(self.tree)
        gate = mixer._socket(node.inputs, mixer._pairs(node.node_tree)[0][2])
        self.assertEqual(gate.default_value, 1)
        self.tree.links.remove(link)
        mixer.sync_tree(self.tree)
        self.assertEqual(gate.default_value, 0)

    def test_legacy_copied_nodes_use_independent_connection_gates(self):
        first = self.add_legacy()
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

    def test_expand_twenty_four_slots_without_a_fixed_count_limit(self):
        node = self.add()
        before = interface(node.node_tree)
        for expected_count in range(3, 25):
            self.assertEqual(mixer.add_shader_slot(node), expected_count)
        self.assertEqual(len(mixer._pairs(node.node_tree)), 24)
        self.assertEqual(len(node.inputs), 49)
        self.assertTrue(set(before) <= set(interface(node.node_tree)))
        self.assertEqual(len(bpy.data.node_groups), 1)

    def native_mix(self, *, linked_factor=False):
        node = self.tree.nodes.new("ShaderNodeMixShader")
        node.name = "Existing Floor Mix"
        node.label = "Original floor layer"
        node.location = (350, -140)
        node.inputs[0].default_value = .37
        overlay = emission(self.tree, "Existing Red Overlay", (1, 0, 0, 1))
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs[1])
        self.tree.links.new(overlay.outputs[0], node.inputs[2])
        if linked_factor:
            factor = self.tree.nodes.new("ShaderNodeValue")
            factor.name = "Existing Factor"
            factor.outputs[0].default_value = .64
            self.tree.links.new(factor.outputs[0], node.inputs[0])
        self.tree.links.new(node.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        consumer = self.tree.nodes.new("ShaderNodeAddShader")
        consumer.name = "Second Output Consumer"
        self.tree.links.new(node.outputs[0], consumer.inputs[0])
        self.tree.nodes.active = node
        return node

    def test_native_mix_conversion_preserves_default_factor_layout_and_both_shaders(self):
        old = self.native_mix()
        old_pointer = old.as_pointer()
        surviving = {item.as_pointer() for item in self.tree.nodes if item != old}
        node = mixer.expand_native_mix_shader(old)
        self.assertEqual(node.bl_idname, "ShaderNodeGroup")
        self.assertNotEqual(node.as_pointer(), old_pointer)
        self.assertEqual(node.name, "Existing Floor Mix")
        self.assertEqual(node.label, "Original floor layer")
        self.assertEqual(tuple(node.location), (350, -140))
        self.assertAlmostEqual(node.inputs["Mask 1"].default_value, .37)
        self.assertEqual(node.inputs["Mask 2"].default_value, 0)
        self.assertEqual(len(mixer._pairs(node.node_tree)), 2)
        self.assertEqual(node.inputs["Base Shader"].links[0].from_node.name, "Original Base")
        self.assertEqual(node.inputs["Shader 1"].links[0].from_node.name, "Existing Red Overlay")
        self.assertEqual({item.as_pointer() for item in self.tree.nodes if item != node}, surviving)
        self.assertEqual(self.tree.nodes.active, node)
        self.assertTrue(node.select)

    def test_native_mix_conversion_preserves_linked_factor_and_every_output_consumer(self):
        old = self.native_mix(linked_factor=True)
        node = mixer.expand_native_mix_shader(old)
        self.assertEqual(node.inputs["Mask 1"].links[0].from_node.name, "Existing Factor")
        self.assertAlmostEqual(node.inputs["Mask 1"].default_value, .37)
        self.assertEqual({item.to_node.name for item in node.outputs[0].links},
                         {"Original Output", "Second Output Consumer"})
        self.assertEqual(self.tree.nodes["Original Output"].inputs["Surface"].links[0].from_node, node)
        self.assertEqual(self.tree.nodes["Second Output Consumer"].inputs[0].links[0].from_node, node)

    def test_native_mix_conversion_partial_link_failure_restores_original_graph(self):
        old = self.native_mix(linked_factor=True)
        before, values = links(self.tree), node_inputs(old)
        original_nodes = {item.as_pointer() for item in self.tree.nodes}
        original_groups = set(bpy.data.node_groups)
        connect = mixer._connect_native_mix_links

        def fail_after_output_redirect(owner, mapped):
            connect(owner, [mapped[-1]])
            raise RuntimeError("Injected native conversion link failure")

        with mock.patch.object(mixer, "_connect_native_mix_links", side_effect=fail_after_output_redirect):
            with self.assertRaisesRegex(RuntimeError, "Injected native conversion"):
                mixer.expand_native_mix_shader(old)
        self.assertEqual(links(self.tree), before)
        self.assertEqual(node_inputs(old), values)
        self.assertEqual({item.as_pointer() for item in self.tree.nodes}, original_nodes)
        self.assertEqual(set(bpy.data.node_groups), original_groups)
        self.assertEqual(old.bl_idname, "ShaderNodeMixShader")

    def test_native_mix_operator_exposes_an_extra_pair_without_a_fresh_asset(self):
        old = self.native_mix()
        context = SimpleNamespace(space_data=SimpleNamespace(type="NODE_EDITOR", tree_type="ShaderNodeTree",
                                  edit_tree=self.tree))
        operator = SimpleNamespace(node_name="", report=lambda _kind, message: self.fail(message))
        self.assertTrue(mixer.RR_OT_add_shader_slot.poll(context))
        self.assertEqual(mixer.RR_OT_add_shader_slot.execute(operator, context), {"FINISHED"})
        self.assertEqual(self.tree.nodes.active.bl_idname, "ShaderNodeGroup")
        self.assertEqual(len(mixer._pairs(self.tree.nodes.active.node_tree)), 2)
        self.assertEqual(self.tree.nodes.active.inputs["Mask 2"].default_value, 0)

    def test_native_mix_conversion_blocks_animation_paths_without_mutating_graph(self):
        node = self.native_mix()
        node.inputs[0].keyframe_insert(data_path="default_value", frame=1)
        before = links(self.tree)
        with self.assertRaisesRegex(ValueError, "animated shader tree"):
            mixer.expand_native_mix_shader(node)
        self.assertEqual(links(self.tree), before)
        self.assertEqual(node.bl_idname, "ShaderNodeMixShader")

    def test_native_mix_before_after_conversion_renders_identically_without_helper(self):
        scene = bpy.data.scenes.new("Native Mix Conversion Comparison")
        for index, convert in enumerate((False, True)):
            mat = material("Converted comparison" if convert else "Ordinary comparison")
            tree = mat.node_tree
            native = tree.nodes.new("ShaderNodeMixShader")
            native.inputs[0].default_value = .37
            tree.links.new(tree.nodes["Original Base"].outputs[0], native.inputs[1])
            tree.links.new(emission(tree, "Original Overlay", (1, 0, 0, 1)).outputs[0], native.inputs[2])
            tree.links.new(native.outputs[0], tree.nodes["Original Output"].inputs["Surface"])
            if convert:
                replacement = mixer.expand_native_mix_shader(native)
                self.assertEqual(replacement.inputs["Mask 2"].default_value, 0)
            mesh = bpy.data.meshes.new("Conversion tile")
            mesh.from_pydata([(index-.46, -.46, 0), (index+.46, -.46, 0),
                              (index+.46, .46, 0), (index-.46, .46, 0)], [], [(0, 1, 2, 3)])
            mesh.materials.append(mat)
            obj = bpy.data.objects.new("Conversion tile", mesh)
            scene.collection.objects.link(obj)
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.cycles.max_bounces = 0
        scene.render.threads_mode = "FIXED"
        scene.render.threads = 2
        scene.render.resolution_x, scene.render.resolution_y = 32, 16
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "OPEN_EXR"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "32"
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        camera_data = bpy.data.cameras.new("Conversion Camera")
        camera = bpy.data.objects.new("Conversion Camera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = (.5, 0, 10)
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = 2
        scene.camera = camera
        with tempfile.TemporaryDirectory(prefix="rr-native-conversion-render-") as temp:
            scene.render.filepath = str(Path(temp) / "comparison.exr")
            mixer.unregister()
            try:
                bpy.ops.render.render(write_still=True, scene=scene.name)
            finally:
                mixer.register()
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            pixels = list(image.pixels)
            samples = [pixels[(8 * 32 + x) * 4:(8 * 32 + x) * 4 + 3] for x in (8, 24)]
            for rgb in samples:
                for actual, wanted in zip(rgb, (.37, 0, .63)):
                    self.assertAlmostEqual(actual, wanted, delta=.002)
            for native, converted in zip(*samples):
                self.assertAlmostEqual(native, converted, delta=.002)
            bpy.data.images.remove(image)
        print("RR_NATIVE_MIX_CONVERSION_RENDER_PASS samples=2")

    def test_visual_edits_do_not_prevent_expansion(self):
        node = self.add()
        group = node.node_tree
        internal = group.nodes["Mix 1"]
        internal.location = (1200, -800)
        internal.label = "My arranged layer"
        internal.name = "My renamed layer"
        internal.width = 350
        internal.hide = True
        internal.use_custom_color = True
        internal.color = (.4, .2, .6)
        self.assertEqual(mixer.add_shader_slot(node), 3)

    def test_internal_constant_changes_are_not_silently_overwritten(self):
        node = self.add()
        group = node.node_tree
        group.nodes["Clamp Mask 1"].inputs[1].default_value = .5
        with self.assertRaisesRegex(ValueError, "internals were edited"):
            mixer.add_shader_slot(node)
        self.assertEqual(group.nodes["Clamp Mask 1"].inputs[1].default_value, .5)
        self.assertEqual(node.node_tree, group)

    def test_legacy_expansion_preserves_gate_schema_and_existing_links(self):
        node = self.add_legacy()
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Shader 1"])
        before = links(self.tree)
        self.assertEqual(mixer.add_shader_slot(node), 2)
        self.assertEqual(node.node_tree.get(mixer._KIND), 1)
        self.assertEqual(links(self.tree), before)
        self.assertEqual(mixer._socket(node.inputs, mixer._pairs(node.node_tree)[0][2]).default_value, 1)

    def test_saved_v1_signature_allows_arranging_legacy_graph(self):
        node = self.add_legacy()
        group = node.node_tree
        group[mixer._SIGNATURE] = json.dumps((
            sorted((item.name, item.bl_idname,
                    item.operation if item.bl_idname == "ShaderNodeMath" else "",
                    bool(item.use_clamp) if item.bl_idname == "ShaderNodeMath" else False,
                    bool(item.mute),
                    bool(item.is_active_output) if item.bl_idname == "NodeGroupOutput" else False,
                    tuple(item.location), item.label, item.width, bool(item.hide),
                    bool(item.use_custom_color), tuple(item.color)) for item in group.nodes),
            sorted((item.from_node.name, item.from_socket.identifier,
                    item.to_node.name, item.to_socket.identifier) for item in group.links)),
            separators=(",", ":"))
        del group[mixer._SIGNATURE_SCHEMA]
        group.nodes["Mix 1"].location = (400, 250)
        group.nodes["Mix 1"].label = "Legacy Layout"
        self.assertEqual(mixer.add_shader_slot(node), 2)

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
        operator = SimpleNamespace(node_name="", report=lambda _kind, message: self.fail(message))
        self.assertEqual(mixer.RR_OT_add_mix_shaders.execute(operator, context), {"FINISHED"})
        self.assertEqual(links(self.tree), before)
        self.assertEqual(len(nested.nodes), 1)
        self.assertEqual(nested.nodes.active.id_data, nested)
        self.assertEqual(tuple(nested.nodes.active.location), (40, 60))
        self.assertTrue(mixer.RR_OT_add_shader_slot.poll(context))
        self.assertEqual(mixer.RR_OT_add_shader_slot.execute(operator, context), {"FINISHED"})
        self.assertEqual(len(mixer._pairs(nested.nodes.active.node_tree)), 3)
        self.assertEqual(len(nested.nodes), 1)

    def test_geometry_editor_is_not_a_target(self):
        context = SimpleNamespace(space_data=SimpleNamespace(type="NODE_EDITOR", tree_type="GeometryNodeTree",
                                  edit_tree=self.tree))
        self.assertFalse(mixer.RR_OT_add_mix_shaders.poll(context))
        self.assertFalse(mixer.RR_OT_add_shader_slot.poll(context))

    def test_registered_operator_expands_in_real_shader_editor_context(self):
        window = bpy.context.window
        self.assertIsNotNone(window)
        area = next(item for item in window.screen.areas if item.type == "VIEW_3D")
        original_type = area.type
        obj = bpy.data.objects.new("Operator Target", bpy.data.meshes.new("Operator Mesh"))
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(self.target)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        try:
            area.type = "NODE_EDITOR"
            area.ui_type = "ShaderNodeTree"
            area.spaces.active.shader_type = "OBJECT"
            area.spaces.active.pin = False
            region = next(item for item in area.regions if item.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=area, region=region):
                self.assertEqual(mixer._editor_tree(bpy.context), self.tree)
                self.assertEqual(bpy.ops.rr_builder.add_mix_shaders(use_transform=False), {"FINISHED"})
                node = self.tree.nodes.active
                self.assertEqual(bpy.ops.rr_builder.add_shader_slot(), {"FINISHED"})
                self.assertEqual(len(mixer._pairs(node.node_tree)), 3)
        finally:
            area.type = original_type

    def test_registered_add_remove_operators_target_named_node_independently_of_active_node(self):
        window = bpy.context.window
        self.assertIsNotNone(window)
        area = next(item for item in window.screen.areas if item.type == "VIEW_3D")
        original_type = area.type
        obj = bpy.data.objects.new("Named Operator Target", bpy.data.meshes.new("Named Operator Mesh"))
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(self.target)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        first, second = self.add(), self.add()
        first.name = "Button Target Mixer"
        second.name = "Unrelated Active Mixer"
        second_group = second.node_tree
        second_interface = interface(second_group)
        second.inputs["Mask 2"].default_value = .82
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], first.inputs["Base Shader"])
        self.tree.links.new(first.outputs[0], self.tree.nodes["Original Output"].inputs["Surface"])
        before_links = links(self.tree)
        try:
            area.type = "NODE_EDITOR"
            area.ui_type = "ShaderNodeTree"
            area.spaces.active.shader_type = "OBJECT"
            area.spaces.active.pin = False
            region = next(item for item in area.regions if item.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=area, region=region):
                self.tree.nodes.active = second
                self.assertEqual(bpy.ops.rr_builder.add_shader_slot(node_name=first.name), {"FINISHED"})
                self.assertEqual(len(mixer._pairs(first.node_tree)), 3)
                self.tree.nodes.active = second
                self.assertEqual(bpy.ops.rr_builder.remove_shader_slot(node_name=first.name), {"FINISHED"})
                self.assertEqual(len(mixer._pairs(first.node_tree)), 2)
                self.tree.nodes.active = second
                self.assertEqual(bpy.ops.rr_builder.remove_shader_slot(node_name=first.name), {"FINISHED"})
                self.assertEqual(len(mixer._pairs(first.node_tree)), 1)
                self.assertEqual(second.node_tree, second_group)
                self.assertEqual(interface(second_group), second_interface)
                self.assertEqual(len(mixer._pairs(second.node_tree)), 2)
                self.assertAlmostEqual(second.inputs["Mask 2"].default_value, .82)
                self.assertEqual(links(self.tree), before_links)
        finally:
            area.type = original_type

    def test_graph_updates_only_sync_relevant_updated_owners(self):
        node = self.add_legacy()
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
        self.add_legacy()
        copy = self.target.copy()
        nested = bpy.data.node_groups.new("User Nested", "ShaderNodeTree")
        self.add_legacy(nested)
        mixer._OWNER_TREES.clear()
        mixer.rebuild_index()
        self.assertEqual(set(mixer._OWNER_TREES),
                         {self.tree.as_pointer(), copy.node_tree.as_pointer(), nested.as_pointer()})

    def test_native_v2_groups_are_never_indexed_or_synchronized(self):
        node = self.add()
        self.tree.links.new(self.tree.nodes["Original Base"].outputs[0], node.inputs["Shader 1"])
        node.inputs["Mask 1"].default_value = 1
        self.assertEqual(mixer.sync_tree(self.tree), 0)
        mixer.rebuild_index()
        self.assertNotIn(self.tree.as_pointer(), mixer._OWNER_TREES)
        fake = SimpleNamespace(updates=[SimpleNamespace(id=self.target)])
        with mock.patch.object(mixer, "sync_mixers") as sync:
            mixer._on_graph_update(None, fake)
            sync.assert_not_called()

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

    def test_save_reload_preserves_native_v2_graph_without_addon(self):
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
                self.assertEqual(restored.node_tree.get(mixer._KIND), 2)
                self.assertFalse(any(item.name.startswith("_Connected") for item in restored.inputs))
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
        self.assertEqual(len(mixer._pairs(node.node_tree)), 3)
        self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
        restored = bpy.data.materials[name].node_tree.nodes[node_name]
        self.assertEqual(len(mixer._pairs(restored.node_tree)), 2)
        self.assertEqual(links(restored.id_data), before)
        self.assertNotIn(restored.id_data.as_pointer(), mixer._OWNER_TREES)
        self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
        restored = bpy.data.materials[name].node_tree.nodes[node_name]
        self.assertEqual(len(mixer._pairs(restored.node_tree)), 3)
        self.assertEqual(links(restored.id_data), before)

    def test_rendered_priority_empty_shader_black_shader_and_fractional_masks(self):
        cases = [
            ("zero mask unused slot keeps base", 0, None, 0, None, (0, 0, 1)),
            ("white mask unlinked shader is native black", 1, None, 0, None, (0, 0, 0)),
            ("top overlay", 1, (0, 1, 0, 1), 1, (1, 0, 0, 1), (0, 1, 0)),
            ("lower overlay", 0, (0, 1, 0, 1), 1, (1, 0, 0, 1), (1, 0, 0)),
            ("unlinked top with white mask covers lower", 1, None, 1, (1, 0, 0, 1), (0, 0, 0)),
            ("linked black is real shader", 1, (0, 0, 0, 1), 1, (1, 0, 0, 1), (0, 0, 0)),
            ("half top over lower", .5, (0, 1, 0, 1), 1, (1, 0, 0, 1), (.5, .5, 0)),
            ("negative mask clamps", -2, (0, 1, 0, 1), 0, None, (0, 0, 1)),
            ("large mask clamps", 4, (0, 1, 0, 1), 0, None, (0, 1, 0)),
        ]
        scene = bpy.data.scenes.new("Mixer Numeric Atlas")
        size, tile_pixels = 3, 12
        pending_link_edits = []
        for index, (name, mask1, shader1, mask2, shader2, expected) in enumerate(cases):
            mat = material("Atlas " + name)
            tree = mat.node_tree
            node = mixer.add_mix_shaders(tree)
            tree.links.new(tree.nodes["Original Base"].outputs[0], node.inputs["Base Shader"])
            for number, mask, color in ((1, mask1, shader1), (2, mask2, shader2)):
                # Linked values also test clamping beyond the UI's normal range.
                value = tree.nodes.new("ShaderNodeValue")
                value.outputs[0].default_value = mask
                tree.links.new(value.outputs[0], node.inputs["Mask {}".format(number)])
                if color is not None:
                    overlay = emission(tree, "Overlay {}".format(number), color)
                    # These connections are deliberately made after unregister,
                    # proving no handler is required for further shader edits.
                    pending_link_edits.append((tree, overlay.outputs[0], node.inputs["Shader {}".format(number)], None))
                elif mask == 1:
                    temporary = emission(tree, "Temporary Linked Shader", (1, 0, 1, 1))
                    link = tree.links.new(temporary.outputs[0], node.inputs["Shader {}".format(number)])
                    pending_link_edits.append((tree, None, None, link))
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
            mixer.unregister()
            try:
                for tree, from_socket, to_socket, old_link in pending_link_edits:
                    if old_link is not None:
                        tree.links.remove(old_link)
                    else:
                        tree.links.new(from_socket, to_socket)
                self.assertFalse(any(callback in getattr(bpy.app.handlers, name)
                                     for name, callback in mixer._HANDLERS))
                bpy.ops.render.render(write_still=True, scene=scene.name)
            finally:
                mixer.register()
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
