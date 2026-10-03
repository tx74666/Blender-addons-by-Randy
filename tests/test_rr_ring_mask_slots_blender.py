"""Ring Group slot removal contracts; run serially in factory Blender.

No rendering, asset imports or live files are used. These checks exercise native
socket identifiers, owner animation protection, staged rollback and Blender Undo.
"""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
ring = importlib.import_module("random_realm_builder_exporter.rr_ring_nodes")


def links(tree):
    return tuple(sorted((link.from_node.name, link.from_socket.identifier,
                         link.to_node.name, link.to_socket.identifier) for link in tree.links))


def state(node):
    group = node.node_tree
    return (
        tuple((item.identifier, item.name, item.in_out, item.socket_type)
              for item in group.interface.items_tree if item.item_type == "SOCKET"),
        ring._graph_signature(group),
        tuple((socket.identifier, socket.default_value)
              for socket in node.inputs if hasattr(socket, "default_value")),
        links(node.id_data),
    )


class RingMaskSlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ring.register()

    @classmethod
    def tearDownClass(cls):
        ring.unregister()

    def setUp(self):
        for material in tuple(bpy.data.materials):
            bpy.data.materials.remove(material)
        for group in tuple(bpy.data.node_groups):
            bpy.data.node_groups.remove(group)
        self.material = bpy.data.materials.new("Ring slot fixture")
        self.material.use_nodes = True
        self.material.use_fake_user = True
        self.tree = self.material.node_tree
        self.tree.nodes.clear()
        self.node = self.tree.nodes.new("ShaderNodeGroup")
        self.node.node_tree = ring.new_group()
        self.node.name = "Managed Ring Group"
        self.shader = self.tree.nodes.new("ShaderNodeEmission")
        self.shader.inputs["Color"].default_value = (.15, .4, .7, 1)
        self.output = self.tree.nodes.new("ShaderNodeOutputMaterial")
        self.tree.links.new(self.shader.outputs[0], self.node.inputs["Shader"])
        self.tree.links.new(self.node.outputs["Shader"], self.output.inputs["Surface"])
        self.tree.nodes.active = self.node
        self.node.select = True
        self.context = SimpleNamespace(space_data=SimpleNamespace(
            type="NODE_EDITOR", tree_type="ShaderNodeTree", edit_tree=self.tree))

    def add_value(self, number, value):
        source = self.tree.nodes.new("ShaderNodeValue")
        source.name = "Mask source {}".format(number)
        source.outputs[0].default_value = value
        self.tree.links.new(source.outputs[0], self.node.inputs["Mask {}".format(number)])
        return source

    def test_five_masks_shrink_and_expand_without_changing_retained_links_or_identifiers(self):
        first_ids = tuple(ring._mask_ids(self.node.node_tree))
        self.node.inputs["Mask 1"].default_value = .23
        self.add_value(2, .41)
        for expected in (3, 4, 5):
            self.assertEqual(ring.add_mask_slot(self.node), expected)
        for number in (3, 4, 5):
            self.add_value(number, number / 10)
        before = links(self.tree)
        removed_id = ring._mask_ids(self.node.node_tree)[-1]
        expected_links = tuple(link for link in before
                               if not (link[2] == self.node.name and link[3] == removed_id))
        nodes_before = tuple(node.as_pointer() for node in self.tree.nodes)
        retained_ids = tuple(ring._mask_ids(self.node.node_tree)[:-1])
        self.assertEqual(ring.remove_mask_slot(self.node), 4)
        self.assertEqual(tuple(ring._mask_ids(self.node.node_tree)), retained_ids)
        self.assertEqual(links(self.tree), expected_links)
        self.assertEqual(tuple(node.as_pointer() for node in self.tree.nodes), nodes_before)
        self.assertAlmostEqual(self.tree.nodes["Mask source 5"].outputs[0].default_value, .5)
        self.assertAlmostEqual(self.node.inputs["Mask 1"].default_value, .23)
        self.assertEqual(tuple(ring._mask_ids(self.node.node_tree)[:2]), first_ids)
        self.assertEqual(ring.add_mask_slot(self.node), 5)
        self.assertEqual(links(self.tree), expected_links)
        self.assertFalse(self.node.inputs["Mask 5"].is_linked)
        self.assertEqual(self.node.inputs["Mask 5"].default_value, 0)
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_last_mask_is_retained_and_second_remove_is_rejected_without_side_effects(self):
        self.assertEqual(ring.remove_mask_slot(self.node), 1)
        before = state(self.node)
        original = self.node.node_tree
        groups = set(bpy.data.node_groups)
        with self.assertRaisesRegex(ValueError, "at least one Mask"):
            ring.remove_mask_slot(self.node)
        self.assertEqual(self.node.node_tree, original)
        self.assertEqual(state(self.node), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_removal_forks_only_selected_shared_instance(self):
        original = self.node.node_tree
        second = self.tree.nodes.new("ShaderNodeGroup")
        second.node_tree = original
        second.inputs["Mask 2"].default_value = .82
        before = state(second)
        self.assertEqual(ring.remove_mask_slot(self.node), 1)
        self.assertNotEqual(self.node.node_tree, original)
        self.assertEqual(second.node_tree, original)
        self.assertEqual(state(second), before)
        self.assertEqual(len(ring._mask_ids(second.node_tree)), 2)

    def test_removal_retains_source_asset_template_even_when_no_users_remain(self):
        original = self.node.node_tree
        original.asset_mark()
        original.use_fake_user = False
        original_graph = ring._graph_signature(original)
        self.assertEqual(ring.remove_mask_slot(self.node), 1)
        self.assertIn(original, list(bpy.data.node_groups))
        self.assertEqual(original.users, 0)
        self.assertIsNotNone(original.asset_data)
        self.assertEqual(ring._graph_signature(original), original_graph)
        self.assertIsNone(self.node.node_tree.asset_data)
        self.assertFalse(self.node.node_tree.use_fake_user)

    def test_owner_action_prevents_removal_without_damaging_animation(self):
        socket = self.node.inputs["Mask 2"]
        socket.default_value = .64
        socket.keyframe_insert(data_path="default_value", frame=1)
        action = self.tree.animation_data.action
        before = state(self.node)
        groups = set(bpy.data.node_groups)
        with self.assertRaisesRegex(ValueError, "animated shader tree"):
            ring.remove_mask_slot(self.node)
        self.assertEqual(self.tree.animation_data.action, action)
        self.assertEqual(state(self.node), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_owner_driver_prevents_removal_without_damaging_animation(self):
        driver = self.node.inputs["Mask 2"].driver_add("default_value")
        driver.driver.expression = "0.7"
        path = driver.data_path
        before = state(self.node)
        with self.assertRaisesRegex(ValueError, "animated shader tree"):
            ring.remove_mask_slot(self.node)
        self.assertEqual(self.tree.animation_data.drivers[0].data_path, path)
        self.assertEqual(self.tree.animation_data.drivers[0].driver.expression, "0.7")
        self.assertEqual(state(self.node), before)

    def test_customized_internal_graph_is_not_rebuilt(self):
        self.node.node_tree.nodes["Union 1"].operation = "ADD"
        before = state(self.node)
        groups = set(bpy.data.node_groups)
        with self.assertRaisesRegex(ValueError, "calculation was edited"):
            ring.remove_mask_slot(self.node)
        self.assertEqual(state(self.node), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_staging_failure_has_no_leaked_candidate_or_graph_mutation(self):
        original = self.node.node_tree
        before = state(self.node)
        groups = set(bpy.data.node_groups)
        with mock.patch.object(ring, "_build_union", side_effect=RuntimeError("Injected staging failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected staging failure"):
                ring.remove_mask_slot(self.node)
        self.assertEqual(self.node.node_tree, original)
        self.assertEqual(state(self.node), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_post_swap_failure_restores_removed_link_default_and_group(self):
        self.node.inputs["Mask 1"].default_value = .36
        self.node.inputs["Mask 2"].default_value = .81
        self.add_value(2, .72)
        original = self.node.node_tree
        before = state(self.node)
        groups = set(bpy.data.node_groups)
        restore = ring._restore_external_state
        calls = 0

        def fail_after_first_restore(candidate_node, candidate_state):
            nonlocal calls
            calls += 1
            restore(candidate_node, candidate_state)
            if calls == 1:
                self.assertNotEqual(candidate_node.node_tree, original)
                raise RuntimeError("Injected swap failure")

        with mock.patch.object(ring, "_restore_external_state", side_effect=fail_after_first_restore):
            with self.assertRaisesRegex(RuntimeError, "Injected swap failure"):
                ring.remove_mask_slot(self.node)
        self.assertEqual(calls, 2)
        self.assertEqual(self.node.node_tree, original)
        self.assertEqual(state(self.node), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_explicit_named_operator_changes_named_group_with_a_different_active_node(self):
        other = self.tree.nodes.new("ShaderNodeGroup")
        other.node_tree = ring.new_group()
        self.tree.nodes.active = other
        before = state(other)
        reporter = SimpleNamespace(node_name=self.node.name,
                                   report=lambda _level, message: self.fail(message))
        self.assertEqual(ring.RR_OT_add_mask_slot.execute(reporter, self.context), {"FINISHED"})
        self.assertEqual(len(ring._mask_ids(self.node.node_tree)), 3)
        self.assertEqual(ring.RR_OT_remove_mask_slot.execute(reporter, self.context), {"FINISHED"})
        self.assertEqual(len(ring._mask_ids(self.node.node_tree)), 2)
        self.assertEqual(state(other), before)
        self.assertEqual(self.tree.nodes.active, other)

    def test_missing_named_operator_target_reports_without_changing_active_group(self):
        before = state(self.node)
        messages = []
        reporter = SimpleNamespace(node_name="Deleted group",
                                   report=lambda level, message: messages.append((level, message)))
        self.assertEqual(ring.RR_OT_add_mask_slot.execute(reporter, self.context), {"CANCELLED"})
        self.assertEqual(ring.RR_OT_remove_mask_slot.execute(reporter, self.context), {"CANCELLED"})
        self.assertEqual(len(messages), 2)
        self.assertTrue(all(level == {"ERROR"} and "no longer available" in message
                            for level, message in messages))
        self.assertEqual(state(self.node), before)

    def test_context_menu_only_offers_remove_above_one_mask(self):
        layout = SimpleNamespace(separator=mock.Mock(), operator=mock.Mock())
        ring.draw_context_menu(SimpleNamespace(layout=layout), self.context)
        self.assertIn(mock.call(ring.RR_OT_remove_mask_slot.bl_idname, icon="REMOVE"),
                      layout.operator.call_args_list)
        ring.remove_mask_slot(self.node)
        layout.operator.reset_mock()
        ring.draw_context_menu(SimpleNamespace(layout=layout), self.context)
        self.assertNotIn(mock.call(ring.RR_OT_remove_mask_slot.bl_idname, icon="REMOVE"),
                         layout.operator.call_args_list)

    def test_undo_redo_removal_restores_slot_parameters_links_and_upstream_mask(self):
        self.node.inputs["Mask 1"].default_value = .27
        self.node.inputs["Mask 2"].default_value = .58
        self.add_value(2, .76)
        material_name, node_name = self.material.name, self.node.name
        before = state(self.node)
        bpy.ops.ed.undo_push(message="Before Ring Mask removal")
        ring.remove_mask_slot(self.node)
        bpy.ops.ed.undo_push(message="Ring Mask removal")
        self.assertEqual(len(ring._mask_ids(self.node.node_tree)), 1)
        self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
        restored = bpy.data.materials[material_name].node_tree.nodes[node_name]
        self.assertEqual(state(restored), before)
        self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
        restored = bpy.data.materials[material_name].node_tree.nodes[node_name]
        self.assertEqual(len(ring._mask_ids(restored.node_tree)), 1)
        self.assertAlmostEqual(restored.inputs["Mask 1"].default_value, .27)
        self.assertIn("Mask source 2", restored.id_data.nodes)
        self.assertAlmostEqual(restored.id_data.nodes["Mask source 2"].outputs[0].default_value, .76)


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(RingMaskSlotTests))
    if result.wasSuccessful():
        print("RR_RING_MASK_SLOTS_PASS tests={}".format(result.testsRun))
    raise SystemExit(0 if result.wasSuccessful() else 1)
