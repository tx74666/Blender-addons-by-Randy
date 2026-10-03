"""Native Ring Group contracts; run serially in factory background Blender.

This file constructs no real Ring or Arc implementation. Tiny compatible mask
fixtures test the optional edit helpers. Native Maximum and shader pass-through
are sampled in one small CPU atlas with RR Helper's operators unregistered.
"""

import importlib
import hashlib
from pathlib import Path
import sys
import tempfile
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


def interface(group):
    return tuple((item.identifier, item.name, item.in_out, item.socket_type)
                 for item in group.interface.items_tree if item.item_type == "SOCKET")


def mask_fixture(kind="RING", *, current=True):
    name, _filename, source = ring._ASSETS[kind]
    if not current:
        name, source = "Arc Mask", "tools/randy_node_assets/build_arc_mask.py"
    group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    group["randy_asset_source"] = source
    group["randy_asset_version"] = "0.2.1" if current else "0.2.0"
    for item in ring._RADIAL_INPUTS + ("Start Angle", "Sweep Angle"):
        socket = group.interface.new_socket(name=item, in_out="INPUT", socket_type="NodeSocketFloat")
        socket.default_value = {"Inner Radius": .6, "Ring Width": .08,
                                "Sweep Angle": 360 if current else 180}.get(item, 0)
    group.interface.new_socket(name="Mask", in_out="OUTPUT", socket_type="NodeSocketFloat")
    if current:
        group.interface.new_socket(name="Ring Data", in_out="OUTPUT", socket_type="NodeSocketBundle")
    value = group.nodes.new("ShaderNodeValue")
    value.outputs[0].default_value = .5
    output = group.nodes.new("NodeGroupOutput")
    output.is_active_output = True
    group.links.new(value.outputs[0], output.inputs["Mask"])
    return group


def emission(tree, name="One shared shader", color=(.2, .4, .8, 1)):
    node = tree.nodes.new("ShaderNodeEmission")
    node.name = name
    node.inputs["Color"].default_value = color
    node.inputs["Strength"].default_value = 1
    return node


class RingNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ring.register()

    @classmethod
    def tearDownClass(cls):
        ring.unregister()

    def setUp(self):
        for item in list(bpy.data.materials):
            bpy.data.materials.remove(item)
        for item in list(bpy.data.node_groups):
            bpy.data.node_groups.remove(item)
        self.material = bpy.data.materials.new("Ring Target")
        self.material.use_nodes = True
        self.material.use_fake_user = True
        self.tree = self.material.node_tree
        self.tree.nodes.clear()
        self.shader = emission(self.tree)
        self.output = self.tree.nodes.new("ShaderNodeOutputMaterial")
        self.tree.links.new(self.shader.outputs[0], self.output.inputs["Surface"])

    def node(self, group=None):
        node = self.tree.nodes.new("ShaderNodeGroup")
        node.node_tree = ring.new_group() if group is None else group
        node.name = "Ring Group"
        return node

    def test_native_interface_one_shader_two_masks_two_outputs(self):
        before = links(self.tree)
        node = self.node()
        self.assertEqual(links(self.tree), before)
        self.assertTrue(ring.is_ring_group(node))
        self.assertEqual(node.node_tree.color_tag, "SHADER")
        self.assertEqual([(item.name, item.type) for item in node.inputs],
                         [("Shader", "SHADER"), ("Mask 1", "VALUE"), ("Mask 2", "VALUE")])
        self.assertEqual([(item.name, item.type) for item in node.outputs],
                         [("Mask", "VALUE"), ("Shader", "SHADER")])
        self.assertEqual([item.default_value for item in node.inputs if item.type == "VALUE"], [0, 0])

    def test_union_uses_maximum_clamp_and_single_shader_passthrough(self):
        node = self.node()
        group = node.node_tree
        union_nodes = [item for item in group.nodes if item.bl_idname == "ShaderNodeMath"]
        self.assertEqual(len(union_nodes), 2)
        self.assertTrue(all(item.operation == "MAXIMUM" and item.use_clamp for item in union_nodes))
        shader = group.nodes["Output"].inputs["Shader"].links[0]
        self.assertEqual(shader.from_node, group.nodes["Inputs"])
        self.assertEqual(shader.from_socket.name, "Shader")
        self.assertFalse(any(item.bl_idname.startswith("ShaderNodeBsdf") for item in group.nodes))

    def test_at_least_26_masks_preserve_one_shader_and_surrounding_links(self):
        node = self.node()
        self.tree.links.new(self.shader.outputs[0], node.inputs["Shader"])
        self.tree.links.new(node.outputs["Shader"], self.output.inputs["Surface"])
        before, old_interface = links(self.tree), interface(node.node_tree)
        node.inputs["Mask 1"].default_value = .23
        for expected in range(3, 27):
            self.assertEqual(ring.add_mask_slot(node), expected)
        self.assertEqual(links(self.tree), before)
        self.assertTrue(set(old_interface) <= set(interface(node.node_tree)))
        self.assertAlmostEqual(node.inputs["Mask 1"].default_value, .23)
        self.assertEqual(sum(item.type == "SHADER" for item in node.inputs), 1)
        self.assertEqual(sum(item.bl_idname == "ShaderNodeEmission" for item in self.tree.nodes), 1)
        self.assertEqual(len(ring._mask_ids(node.node_tree)), 26)
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_expansion_forks_only_selected_shared_instance(self):
        first = self.node()
        original = first.node_tree
        second = self.node(original)
        second.inputs["Mask 1"].default_value = .81
        before = interface(original)
        ring.add_mask_slot(first)
        self.assertNotEqual(first.node_tree, original)
        self.assertEqual(second.node_tree, original)
        self.assertEqual(interface(original), before)
        self.assertAlmostEqual(second.inputs["Mask 1"].default_value, .81)

    def test_expansion_clears_new_asset_fake_user_preserves_template(self):
        node = self.node()
        original = node.node_tree
        original.asset_mark()
        original.use_fake_user = True
        ring.add_mask_slot(node)
        self.assertIsNone(node.node_tree.asset_data)
        self.assertFalse(node.node_tree.use_fake_user)
        self.assertIsNotNone(original.asset_data)
        self.assertTrue(original.use_fake_user)

    def test_unused_local_asset_template_is_retained_even_without_fake_user(self):
        node = self.node()
        original = node.node_tree
        original.asset_mark()
        original.use_fake_user = False
        before = interface(original)
        ring.add_mask_slot(node)
        self.assertIn(original, list(bpy.data.node_groups))
        self.assertEqual(original.users, 0)
        self.assertEqual(interface(original), before)
        self.assertIsNotNone(original.asset_data)
        self.assertIsNone(node.node_tree.asset_data)

    def test_cosmetic_internal_changes_do_not_block_expansion(self):
        node = self.node()
        internal = node.node_tree.nodes["Union 1"]
        internal.location = (9, 15)
        internal.label = "My layout"
        internal.width = 270
        internal.hide = True
        internal.use_custom_color = True
        internal.color = (.2, .3, .4)
        self.assertEqual(ring.add_mask_slot(node), 3)

    def test_semantic_internal_changes_are_not_overwritten(self):
        node = self.node()
        original = node.node_tree
        original.nodes["Union 1"].operation = "ADD"
        with self.assertRaisesRegex(ValueError, "calculation was edited"):
            ring.add_mask_slot(node)
        self.assertEqual(node.node_tree, original)
        self.assertEqual(original.nodes["Union 1"].operation, "ADD")

    def test_changed_interface_not_overwritten(self):
        node = self.node()
        node.node_tree.interface.new_socket(name="User Input", in_out="INPUT", socket_type="NodeSocketFloat")
        with self.assertRaisesRegex(ValueError, "interface was changed"):
            ring.add_mask_slot(node)
        self.assertIn("User Input", [item.name for item in node.inputs])

    def test_animated_internals_are_not_rebuilt(self):
        node = self.node()
        node.node_tree.nodes["Union 1"].keyframe_insert(data_path="location", frame=1)
        with self.assertRaisesRegex(ValueError, "animated"):
            ring.add_mask_slot(node)

    def test_staging_failure_keeps_original_all_links_and_group_inventory(self):
        node = self.node()
        original, before, groups = node.node_tree, links(self.tree), set(bpy.data.node_groups)
        with mock.patch.object(ring, "_build_union", side_effect=RuntimeError("Injected staging failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected staging failure"):
                ring.add_mask_slot(node)
        self.assertEqual(node.node_tree, original)
        self.assertEqual(links(self.tree), before)
        self.assertEqual(set(bpy.data.node_groups), groups)

    def test_post_swap_failure_rolls_back_shader_values_links_and_inventory(self):
        node = self.node()
        self.tree.links.new(self.shader.outputs[0], node.inputs["Shader"])
        self.tree.links.new(node.outputs["Shader"], self.output.inputs["Surface"])
        node.inputs["Mask 1"].default_value = .43
        original, before, groups = node.node_tree, links(self.tree), set(bpy.data.node_groups)
        restore, calls = ring._restore_external_state, []

        def fail_once(node, state):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("Injected swap failure")
            restore(node, state)

        with mock.patch.object(ring, "_restore_external_state", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "Injected swap failure"):
                ring.add_mask_slot(node)
        self.assertEqual(node.node_tree, original)
        self.assertEqual(links(self.tree), before)
        self.assertEqual(set(bpy.data.node_groups), groups)
        self.assertAlmostEqual(node.inputs["Mask 1"].default_value, .43)

    def test_add_ring_reuses_shared_asset_and_free_slots_then_expands(self):
        asset = mask_fixture()
        before_interface, before_graph = interface(asset), links(asset)
        node = self.node()
        self.tree.links.new(self.shader.outputs[0], node.inputs["Shader"])
        for index in range(1, 25):
            added = ring.add_ring(node)
            self.assertEqual(added.node_tree, asset)
            self.assertEqual(added.inputs["Sweep Angle"].default_value, 360)
            self.assertEqual(added.inputs["Start Angle"].default_value, 0)
            self.assertEqual(node.inputs["Mask {}".format(index)].links[0].from_node, added)
        self.assertEqual(len(ring._mask_ids(node.node_tree)), 24)
        self.assertEqual(interface(asset), before_interface)
        self.assertEqual(links(asset), before_graph)
        self.assertEqual(sum(item.bl_idname == "ShaderNodeEmission" for item in self.tree.nodes), 1)
        self.assertEqual(node.inputs["Shader"].links[0].from_node, self.shader)

    def test_add_arc_uses_current_shared_ring_asset_at_explicit_180(self):
        asset = mask_fixture("ARC")
        self.assertEqual(asset.interface.items_tree["Sweep Angle"].default_value, 360)
        node = self.node()
        added = ring.add_arc(node)
        self.assertEqual(added.node_tree, asset)
        self.assertEqual(added.inputs["Start Angle"].default_value, 0)
        self.assertEqual(added.inputs["Sweep Angle"].default_value, 180)
        self.assertEqual(asset.interface.items_tree["Sweep Angle"].default_value, 360)
        self.assertEqual(node.inputs["Mask 1"].links[0].from_node, added)

    def test_current_ring_data_asset_preferred_over_cached_arc_and_old_named_ring(self):
        legacy = mask_fixture(current=False)
        legacy.name = "Ring Mask"
        legacy["randy_asset_source"] = "tools/randy_node_assets/build_ring_mask.py"
        for item in tuple(legacy.interface.items_tree):
            if item.item_type == "SOCKET" and item.name in {"Start Angle", "Sweep Angle"}:
                legacy.interface.remove(item)
        old_arc = mask_fixture(current=False)
        current = mask_fixture()
        self.assertNotEqual(current.name, "Ring Mask")
        before = interface(legacy), links(legacy), interface(old_arc), links(old_arc)
        self.assertEqual(ring.find_mask_group("RING"), current)
        self.assertEqual(ring.find_mask_group("ARC"), current)
        self.assertEqual((interface(legacy), links(legacy), interface(old_arc), links(old_arc)), before)

    def test_cached_arc_does_not_replace_missing_current_ring_data_asset(self):
        old_arc = mask_fixture(current=False)
        self.assertTrue(ring._compatible_mask(old_arc, "ARC"))
        self.assertFalse(ring._current_mask(old_arc, "ARC"))
        before = interface(old_arc), links(old_arc)
        with mock.patch.object(ring, "_asset_paths", return_value=iter(())):
            with self.assertRaisesRegex(ValueError, "Ring Mask is not available"):
                ring.find_mask_group("RING")
        self.assertEqual((interface(old_arc), links(old_arc)), before)

    def test_new_ring_requires_current_boundary_output(self):
        incomplete = mask_fixture()
        incomplete.interface.remove(incomplete.interface.items_tree["Ring Data"])
        self.assertTrue(ring._compatible_mask(incomplete, "RING"))
        self.assertFalse(ring._current_mask(incomplete, "RING"))
        current = mask_fixture()
        self.assertEqual(ring.find_mask_group("RING"), current)

    def test_two_added_rings_get_distinct_radius_and_keep_group_active(self):
        mask_fixture()
        node = self.node()
        first = ring.add_ring(node)
        first_values = tuple(first.inputs[name].default_value for name in ring._RADIAL_INPUTS)
        second = ring.add_ring(node)
        self.assertAlmostEqual(first.inputs["Inner Radius"].default_value, .6)
        self.assertAlmostEqual(second.inputs["Inner Radius"].default_value, .7)
        self.assertEqual(tuple(first.inputs[name].default_value for name in ring._RADIAL_INPUTS), first_values)
        self.assertEqual(self.tree.nodes.active, node)
        self.assertTrue(node.select and second.select)

    def test_radius_spacing_finds_inward_gap_when_outward_does_not_fit(self):
        mask_fixture()
        node = self.node()
        first = ring.add_ring(node)
        first.inputs["Inner Radius"].default_value = .91
        second = ring.add_ring(node)
        self.assertAlmostEqual(second.inputs["Inner Radius"].default_value, 0)
        self.assertAlmostEqual(first.inputs["Inner Radius"].default_value, .91)
        self.assertIsNone(second.get(ring._NOTICE))

    def test_full_radius_canvas_adds_with_clear_warning_and_preserves_existing(self):
        mask_fixture()
        node = self.node()
        first = ring.add_ring(node)
        first.inputs["Inner Radius"].default_value = 0
        first.inputs["Ring Width"].default_value = 1
        self.tree.nodes.active = node
        context = SimpleNamespace(space_data=SimpleNamespace(type="NODE_EDITOR", tree_type="ShaderNodeTree", edit_tree=self.tree))
        reports = []
        reporter = SimpleNamespace(report=lambda level, message: reports.append((level, message)))
        self.assertEqual(ring.RR_OT_add_ring_mask.execute(reporter, context), {"FINISHED"})
        added = node.inputs["Mask 2"].links[0].from_node
        self.assertIn("No free radius interval", added.get(ring._NOTICE, ""))
        self.assertEqual(reports[0][0], {"WARNING"})
        self.assertEqual(first.inputs["Inner Radius"].default_value, 0)
        self.assertEqual(first.inputs["Ring Width"].default_value, 1)

    def test_arc_added_after_ring_is_spaced_without_changing_arc_angles(self):
        mask_fixture()
        mask_fixture("ARC")
        node = self.node()
        ring.add_ring(node)
        arc = ring.add_arc(node)
        self.assertAlmostEqual(arc.inputs["Inner Radius"].default_value, .7)
        self.assertEqual(arc.inputs["Start Angle"].default_value, 0)
        self.assertEqual(arc.inputs["Sweep Angle"].default_value, 180)

    def test_non_scalar_existing_controls_fall_back_to_manual_notice(self):
        mask_fixture()
        node = self.node()
        unusual = bpy.data.node_groups.new("Vector radial controls", "ShaderNodeTree")
        unusual.interface.new_socket(name="Inner Radius", in_out="INPUT", socket_type="NodeSocketVector")
        unusual.interface.new_socket(name="Ring Width", in_out="INPUT", socket_type="NodeSocketFloat")
        unusual.interface.new_socket(name="Mask", in_out="OUTPUT", socket_type="NodeSocketFloat")
        existing = self.tree.nodes.new("ShaderNodeGroup")
        existing.node_tree = unusual
        existing.inputs["Inner Radius"].default_value = (.2, .3, .4)
        existing.inputs["Ring Width"].default_value = .1
        before = tuple(existing.inputs["Inner Radius"].default_value)
        self.tree.links.new(existing.outputs["Mask"], node.inputs["Mask 1"])
        added = ring.add_ring(node)
        self.assertIn("not finite scalar", added.get(ring._NOTICE, ""))
        self.assertEqual(tuple(existing.inputs["Inner Radius"].default_value), before)
        self.assertAlmostEqual(added.inputs["Inner Radius"].default_value, .6)

    def test_scalar_guard_rejects_non_finite_linked_and_non_value_inputs(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            socket = SimpleNamespace(type="VALUE", is_linked=False, default_value=value)
            self.assertIsNone(ring._constant_scalar(socket))
        self.assertIsNone(ring._constant_scalar(SimpleNamespace(type="VECTOR", is_linked=False,
                                                               default_value=(.1, .2, .3))))
        self.assertIsNone(ring._constant_scalar(SimpleNamespace(type="VALUE", is_linked=True,
                                                               default_value=.6)))

    def test_non_finite_existing_radius_adds_with_notice_and_preserves_parameters(self):
        mask_fixture()
        node = self.node()
        existing = ring.add_ring(node)
        before = tuple(existing.inputs[name].default_value for name in ring._RADIAL_INPUTS)
        original = ring._constant_scalar
        bad_pointer = existing.inputs["Inner Radius"].as_pointer()

        def non_finite(socket):
            # RNA may clamp an assigned NaN differently between builds; inject
            # the same rejection after the finite-value guard at this boundary.
            return None if socket is not None and socket.as_pointer() == bad_pointer else original(socket)

        with mock.patch.object(ring, "_constant_scalar", side_effect=non_finite):
            added = ring.add_ring(node)
        self.assertIn("not finite scalar", added.get(ring._NOTICE, ""))
        self.assertEqual(tuple(existing.inputs[name].default_value for name in ring._RADIAL_INPUTS), before)

    def test_mask_asset_appends_once_from_known_file_and_stays_shared(self):
        asset = mask_fixture()
        asset.asset_mark()
        with tempfile.TemporaryDirectory(prefix="rr-ring-mask-asset-") as temp:
            path = Path(temp) / "Randy_Ring_Mask.blend"
            bpy.data.libraries.write(str(path), {asset}, fake_user=True)
            bpy.data.node_groups.remove(asset)
            node = self.node()
            with mock.patch.object(ring, "_asset_paths", side_effect=lambda _kind: iter((path,))):
                first = ring.add_ring(node)
                second = ring.add_ring(node)
            self.assertEqual(first.node_tree, second.node_tree)
            self.assertEqual(first.node_tree.name, "Ring Mask")
            self.assertEqual(len([group for group in bpy.data.node_groups if ring._compatible_mask(group, "RING")]), 1)

    def test_linked_ring_group_asset_is_copied_locally_without_source_edits(self):
        original = ring.new_group()
        original.asset_mark()
        with tempfile.TemporaryDirectory(prefix="rr-ring-group-linked-") as temp:
            path = str(Path(temp) / "Ring Group.blend")
            bpy.data.libraries.write(path, {original}, fake_user=True)
            source_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            bpy.data.node_groups.remove(original)
            with bpy.data.libraries.load(path, link=True) as (available, requested):
                requested.node_groups = ["Ring Group"]
            linked = requested.node_groups[0]
            node = self.node(linked)
            before, graph_before = interface(linked), ring._graph_signature(linked)
            source_pointer = linked.as_pointer()
            source_library = linked.library
            self.assertIsNotNone(linked.library)
            ring.add_mask_slot(node)
            self.assertIsNone(node.node_tree.library)
            self.assertIn(linked, list(bpy.data.node_groups))
            self.assertEqual(linked.as_pointer(), source_pointer)
            self.assertEqual(linked.library, source_library)
            self.assertEqual(interface(linked), before)
            self.assertEqual(ring._graph_signature(linked), graph_before)
            self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), source_hash)
            self.assertEqual(len(ring._mask_ids(node.node_tree)), 3)

    def test_animated_zero_mask_not_reused_by_add_ring(self):
        mask_fixture()
        node = self.node()
        node.inputs["Mask 1"].keyframe_insert(data_path="default_value", frame=1)
        added = ring.add_ring(node)
        self.assertFalse(node.inputs["Mask 1"].is_linked)
        self.assertFalse(node.inputs["Mask 2"].is_linked)
        self.assertEqual(node.inputs["Mask 3"].links[0].from_node, added)
        self.assertIsNotNone(self.tree.animation_data.action)

    def test_nonzero_unlinked_mask_is_preserved_when_adding_ring(self):
        mask_fixture()
        node = self.node()
        node.inputs["Mask 1"].default_value = .4
        added = ring.add_ring(node)
        self.assertFalse(node.inputs["Mask 1"].is_linked)
        self.assertAlmostEqual(node.inputs["Mask 1"].default_value, .4)
        self.assertEqual(node.inputs["Mask 2"].links[0].from_node, added)

    def test_add_ring_failure_after_expansion_restores_original(self):
        mask_fixture()
        node = self.node()
        for number in (1, 2):
            node.inputs["Mask {}".format(number)].default_value = .5
        original, before = node.node_tree, links(self.tree)
        groups, nodes = set(bpy.data.node_groups), set(self.tree.nodes)
        with mock.patch.object(ring, "_link_mask_node", side_effect=RuntimeError("Injected link failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected link failure"):
                ring.add_ring(node)
        self.assertEqual(node.node_tree, original)
        self.assertEqual(links(self.tree), before)
        self.assertEqual(set(self.tree.nodes), nodes)
        self.assertEqual(set(bpy.data.node_groups), groups)
        self.assertEqual(node.inputs["Mask 1"].default_value, .5)

    def test_missing_asset_returns_clear_error_without_changing_graph(self):
        node = self.node()
        groups, nodes, before = set(bpy.data.node_groups), set(self.tree.nodes), links(self.tree)
        with mock.patch.object(ring, "_asset_paths", return_value=iter(())):
            with self.assertRaisesRegex(ValueError, "Ring Mask is not available"):
                ring.add_ring(node)
        self.assertEqual(set(bpy.data.node_groups), groups)
        self.assertEqual(set(self.tree.nodes), nodes)
        self.assertEqual(links(self.tree), before)

    def test_recursive_mask_reference_is_blocked(self):
        owner = bpy.data.node_groups.new("Owner", "ShaderNodeTree")
        node = owner.nodes.new("ShaderNodeGroup")
        node.node_tree = ring.new_group()
        asset = mask_fixture()
        cycle = asset.nodes.new("ShaderNodeGroup")
        cycle.node_tree = owner
        before = links(owner)
        with self.assertRaisesRegex(ValueError, "recursive"):
            ring.add_ring(node)
        self.assertEqual(links(owner), before)

    def test_nested_edit_tree_operator_targets_only_active_ring_group(self):
        owner = bpy.data.node_groups.new("Nested user graph", "ShaderNodeTree")
        node = owner.nodes.new("ShaderNodeGroup")
        node.node_tree = ring.new_group()
        owner.nodes.active = node
        context = SimpleNamespace(space_data=SimpleNamespace(type="NODE_EDITOR", tree_type="ShaderNodeTree", edit_tree=owner))
        reporter = SimpleNamespace(report=lambda _level, message: self.fail(message))
        before = links(self.tree)
        self.assertTrue(ring.RR_OT_add_mask_slot.poll(context))
        self.assertEqual(ring.RR_OT_add_mask_slot.execute(reporter, context), {"FINISHED"})
        self.assertEqual(len(ring._mask_ids(node.node_tree)), 3)
        self.assertEqual(links(self.tree), before)

    def test_real_bpy_operator_context_in_shader_editor_when_available(self):
        node = self.node()
        self.tree.nodes.active = node
        area = next((area for area in bpy.context.screen.areas if area.type == "PROPERTIES"), None)
        if area is None:
            self.skipTest("Factory background screen has no reusable area")
        old_type = area.type
        mesh = bpy.data.meshes.new("Operator target")
        mesh.materials.append(self.material)
        obj = bpy.data.objects.new("Operator target", mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        try:
            area.type = "NODE_EDITOR"
            area.ui_type = "ShaderNodeTree"
            area.spaces.active.shader_type = "OBJECT"
            area.spaces.active.pin = False
            bpy.context.view_layer.update()
            with bpy.context.temp_override(area=area):
                if area.spaces.active.edit_tree != self.tree:
                    self.skipTest("Blender background area does not expose its edit_tree")
                self.assertEqual(bpy.ops.rr_builder.add_mask_slot(), {"FINISHED"})
            self.assertEqual(len(ring._mask_ids(node.node_tree)), 3)
        finally:
            area.type = old_type
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)

    def test_geometry_editor_not_a_target(self):
        context = SimpleNamespace(space_data=SimpleNamespace(type="NODE_EDITOR", tree_type="GeometryNodeTree", edit_tree=self.tree))
        self.assertFalse(ring.RR_OT_add_mask_slot.poll(context))
        self.assertFalse(ring.RR_OT_add_ring_mask.poll(context))
        self.assertFalse(ring.RR_OT_add_arc_mask.poll(context))

    def test_register_unregister_adds_no_app_handlers(self):
        names = ("depsgraph_update_post", "load_post", "undo_post", "redo_post", "save_pre", "render_pre")
        ring.unregister()
        before = {name: tuple(getattr(bpy.app.handlers, name)) for name in names}
        ring.register()
        self.assertEqual(before, {name: tuple(getattr(bpy.app.handlers, name)) for name in names})

    def test_saved_native_group_reopens_and_changes_without_registered_helpers(self):
        node = self.node()
        self.tree.links.new(self.shader.outputs[0], node.inputs["Shader"])
        self.tree.links.new(node.outputs["Shader"], self.output.inputs["Surface"])
        node.inputs["Mask 1"].default_value = .7
        ring.add_mask_slot(node)
        before, material_name, node_name = links(self.tree), self.material.name, node.name
        with tempfile.TemporaryDirectory(prefix="rr-ring-group-persistence-") as temp:
            path = str(Path(temp) / "fixture.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            ring.unregister()
            try:
                bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
                tree = bpy.data.materials[material_name].node_tree
                restored = tree.nodes[node_name]
                self.assertEqual(links(tree), before)
                self.assertEqual(len(ring._mask_ids(restored.node_tree)), 3)
                restored.inputs["Mask 2"].default_value = .9
                self.assertAlmostEqual(restored.inputs["Mask 2"].default_value, .9)
            finally:
                ring.register()

    def test_render_native_max_union_and_single_shader_without_addon(self):
        cases = [(0, 0, 0), (.2, .7, .7), (.8, .4, .8), (-2, -.5, 0), (2, .5, 1), (.5, .5, .5), (0, 0, .35)]
        scene = bpy.data.scenes.new("Ring native atlas")
        size, tile_pixels = 3, 12
        for index, (a, b, wanted) in enumerate(cases):
            material = bpy.data.materials.new("Union sample {}".format(index))
            material.use_nodes = True
            tree = material.node_tree
            tree.nodes.clear()
            group = tree.nodes.new("ShaderNodeGroup")
            group.node_tree = ring.new_group()
            for number, value in ((1, a), (2, b)):
                scalar = tree.nodes.new("ShaderNodeValue")
                scalar.outputs[0].default_value = value
                tree.links.new(scalar.outputs[0], group.inputs["Mask {}".format(number)])
            shader = emission(tree)
            output = tree.nodes.new("ShaderNodeOutputMaterial")
            if index == len(cases)-1:
                shader.inputs["Color"].default_value = (wanted, wanted, wanted, 1)
                tree.links.new(shader.outputs[0], group.inputs["Shader"])
                tree.links.new(group.outputs["Shader"], output.inputs["Surface"])
            else:
                # Keep the diagnostic Mask shader outside Ring Group; feeding
                # its Mask output back into its Shader input creates a cycle
                # in Blender's outer node graph even though branches differ.
                tree.links.new(group.outputs["Mask"], shader.inputs["Color"])
                tree.links.new(shader.outputs[0], output.inputs["Surface"])
            x, y = index % size, index // size
            mesh = bpy.data.meshes.new("Union tile")
            mesh.from_pydata([(x-.46, y-.46, 0), (x+.46, y-.46, 0),
                              (x+.46, y+.46, 0), (x-.46, y+.46, 0)], [], [(0, 1, 2, 3)])
            mesh.materials.append(material)
            obj = bpy.data.objects.new("Union tile", mesh)
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
        camera_data = bpy.data.cameras.new("Union camera")
        camera = bpy.data.objects.new("Union camera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = ((size-1)/2, (size-1)/2, 10)
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = size
        scene.camera = camera
        ring.unregister()
        try:
            with tempfile.TemporaryDirectory(prefix="rr-ring-group-render-") as temp:
                scene.render.filepath = str(Path(temp) / "atlas.exr")
                bpy.ops.render.render(write_still=True, scene=scene.name)
                image = bpy.data.images.load(scene.render.filepath, check_existing=False)
                pixels, width = list(image.pixels), image.size[0]
                for index, (_a, _b, expected) in enumerate(cases):
                    x = (index % size)*tile_pixels + tile_pixels//2
                    y = (index // size)*tile_pixels + tile_pixels//2
                    rgb = pixels[(y*width+x)*4:(y*width+x)*4+3]
                    for actual in rgb:
                        self.assertAlmostEqual(actual, expected, delta=.002)
                bpy.data.images.remove(image)
        finally:
            ring.register()
        print("RR_RING_NODES_SHADER_SAMPLES_PASS samples={}".format(len(cases)))


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    suite = unittest.defaultTestLoader.loadTestsFromNames(arguments, sys.modules[__name__]) if arguments else \
        unittest.defaultTestLoader.loadTestsFromTestCase(RingNodeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print("RR_RING_NODES_PASS tests={}".format(result.testsRun))
    raise SystemExit(0 if result.wasSuccessful() else 1)
