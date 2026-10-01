"""Ring/Arc Stack behavioral contracts in an isolated Blender factory session.

Run serially with --background --factory-startup --disable-autoexec --threads 2
--python-exit-code 1 --python tests/test_rr_ring_stack_blender.py.
The numerical test renders one tiny CPU atlas using the actual Ring Mask asset.
No production .blend or asset file is changed.
"""

import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
ADDONS = ROOT / "addons"
sys.path.insert(0, str(ADDONS))
ring = importlib.import_module("random_realm_builder_exporter.rr_ring_stack")
ui = importlib.import_module("random_realm_builder_exporter.rr_ring_stack_ui")
assert Path(ring.__file__).resolve().is_relative_to(ADDONS.resolve()), ring.__file__
MANIFEST = json.loads((ROOT / "node_library/manifest.json").read_text(encoding="utf-8"))
ASSET = ROOT / next(bundle["path"] for bundle in MANIFEST["bundles"] if bundle["id"] == "ring_mask")


def plain(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bpy.types.ID):
        return value.bl_rna.identifier, value.as_pointer()
    try:
        return tuple(plain(item) for item in value)
    except TypeError:
        return str(value)


def rna_values(value):
    result = []
    for prop in value.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.is_readonly or prop.type == "COLLECTION":
            continue
        if prop.type == "POINTER" and prop.identifier not in {"image", "object", "node_tree"}:
            continue
        result.append((prop.identifier, plain(getattr(value, prop.identifier))))
    return tuple(result)


def graph_snapshot(tree):
    """Public node graph state, including socket values and original UI positions."""
    return (
        tuple((node.name, node.bl_idname, rna_values(node),
               node.parent.name if node.parent else None,
               tuple(rna_values(socket) for socket in node.inputs),
               tuple(rna_values(socket) for socket in node.outputs),
               tuple(sorted((key, plain(value)) for key, value in node.items())))
              for node in tree.nodes),
        tuple(sorted((link.from_node.name, link.from_socket.identifier,
                      link.to_node.name, link.to_socket.identifier) for link in tree.links)),
    )


def make_material(name, color=(0, 0, 1, 1)):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.use_fake_user = True
    tree = mat.node_tree
    tree.nodes.clear()
    shader = tree.nodes.new("ShaderNodeEmission")
    shader.name = "Original Base"
    shader.inputs["Color"].default_value = color
    shader.inputs["Strength"].default_value = 1
    shader.location = (-300, 70)
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.name = "Original Output"
    output.is_active_output = True
    output.location = (300, 70)
    tree.links.new(shader.outputs[0], output.inputs["Surface"])
    return mat


def surface_node(mat):
    output = next(node for node in mat.node_tree.nodes
                  if node.bl_idname == "ShaderNodeOutputMaterial" and node.is_active_output)
    return output.inputs["Surface"].links[0].from_node


def layer_state(stack):
    return tuple((layer.uid, layer.name, layer.radius, layer.width, layer.softness,
                  layer.enabled, layer.source_material.name if layer.source_material else None,
                  layer.mode, layer.start_angle, layer.sweep_angle)
                 for layer in stack.layers)


def reachable_trees(tree, seen=None):
    seen = set() if seen is None else seen
    if tree.as_pointer() in seen:
        return
    seen.add(tree.as_pointer())
    yield tree
    for node in tree.nodes:
        if node.bl_idname == "ShaderNodeGroup" and node.node_tree:
            yield from reachable_trees(node.node_tree, seen)


class RingStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ring.register()
        cls.asset_hash = hashlib.sha256(ASSET.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        ring.unregister()
        assert hashlib.sha256(ASSET.read_bytes()).hexdigest() == cls.asset_hash

    def setUp(self):
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mat in list(bpy.data.materials):
            bpy.data.materials.remove(mat, do_unlink=True)
        for group in list(bpy.data.node_groups):
            bpy.data.node_groups.remove(group, do_unlink=True)
        for image in list(bpy.data.images):
            if image.name.startswith("Ring Stack Test"):
                bpy.data.images.remove(image, do_unlink=True)
        with bpy.data.libraries.load(str(ASSET), link=False) as (available, requested):
            self.assertIn("Ring Mask", available.node_groups)
            requested.node_groups = ["Ring Mask"]
        self.mask = bpy.data.node_groups["Ring Mask"]
        self.mask_before = graph_snapshot(self.mask)
        self.target = make_material("Hub Base")
        self.red = make_material("Red Emission", (1, 0, 0, 1))
        self.green = make_material("Green Emission", (0, 1, 0, 1))

    def add(self, material=None, source=None, **values):
        material = material or self.target
        stack = ring.get_stack(material)
        stack.ring_mask = self.mask
        layer = ring.add_ring(material)
        uid = layer.uid
        for key, value in values.items():
            setattr(layer, key, value)
        layer.source_material = source or self.red
        ring.rebuild_stack(material)
        self.assertFalse(stack.error, stack.error)
        return next(item for item in stack.layers if item.uid == uid)

    def assert_close(self, actual, expected, places=5):
        self.assertEqual(len(actual), len(expected))
        for got, wanted in zip(actual, expected):
            self.assertAlmostEqual(got, wanted, places=places)

    def group(self, material=None):
        wrapper = surface_node(material or self.target)
        self.assertEqual(wrapper.bl_idname, "ShaderNodeGroup")
        self.assertEqual(wrapper.inputs["Base Shader"].type, "SHADER")
        self.assertEqual(wrapper.outputs["Shader"].type, "SHADER")
        return wrapper.node_tree

    def mix_uids(self, material=None):
        """Walk the observable output chain from highest priority down to Base."""
        group = self.group(material)
        output = next(node for node in group.nodes if node.bl_idname == "NodeGroupOutput")
        node = output.inputs["Shader"].links[0].from_node
        result = []
        while node.bl_idname == "ShaderNodeMixShader":
            result.append(node.get("rr_ring_uid"))
            self.assertTrue(node.inputs[2].is_linked, "Every enabled layer needs its own shader")
            self.assertTrue(node.inputs[1].is_linked, "Underlying shader must remain connected")
            node = node.inputs[1].links[0].from_node
        self.assertEqual(node.bl_idname, "NodeGroupInput")
        return result

    def test_wraps_only_active_surface_preserves_base_volume_displacement_and_unused_nodes(self):
        tree = self.target.node_tree
        output = tree.nodes["Original Output"]
        volume = tree.nodes.new("ShaderNodeVolumePrincipled")
        displacement = tree.nodes.new("ShaderNodeValue")
        unused = tree.nodes.new("ShaderNodeTexNoise")
        unused.name = "Artist unrelated noise"
        tree.links.new(volume.outputs[0], output.inputs["Volume"])
        tree.links.new(displacement.outputs[0], output.inputs["Displacement"])
        inactive = tree.nodes.new("ShaderNodeOutputMaterial")
        inactive.name = "Unused Cycles Output"
        inactive.is_active_output = False
        tree.links.new(tree.nodes["Original Base"].outputs[0], inactive.inputs["Surface"])
        before = graph_snapshot(tree)
        self.add(name="Outer trim")
        wrapper = surface_node(self.target)
        self.assertEqual(wrapper.inputs["Base Shader"].links[0].from_node.name, "Original Base")
        self.assertEqual(len(tree.nodes), len(before[0]) + 1)
        self.assertEqual(output.inputs["Volume"].links[0].from_node, volume)
        self.assertEqual(output.inputs["Displacement"].links[0].from_node, displacement)
        self.assertEqual(inactive.inputs["Surface"].links[0].from_node.name, "Original Base")
        ring.remove_stack(self.target)
        self.assertEqual(graph_snapshot(tree), before)
        self.assertEqual(graph_snapshot(self.mask), self.mask_before)

    def test_priority_matches_list_order_and_every_ring_uses_shared_original_mask(self):
        self.add(source=self.red, name="Low")
        self.add(source=self.green, name="High")
        self.add(source=self.red, name="Highest")
        stack = ring.get_stack(self.target)
        self.assertEqual(self.mix_uids(), [item.uid for item in stack.layers if item.enabled])
        masks = [node for node in self.group().nodes if node.get("rr_ring_role") == "mask"]
        self.assertEqual(len(masks), 3)
        self.assertTrue(all(node.node_tree == self.mask for node in masks))
        self.assertEqual(graph_snapshot(self.mask), self.mask_before)

    def test_parameter_and_enabled_edits_update_graph_without_touching_sources(self):
        source_before = graph_snapshot(self.red.node_tree)
        layer = self.add(radius=0.38, width=0.14, softness=0.023)
        mask = next(node for node in self.group().nodes if node.get("rr_ring_role") == "mask")
        self.assert_close([mask.inputs[name].default_value for name in
                           ("Inner Radius", "Ring Width", "Edge Softness")], [.38, .14, .023])
        layer.radius = .42
        mask = next(node for node in self.group().nodes if node.get("rr_ring_role") == "mask")
        self.assertAlmostEqual(mask.inputs["Inner Radius"].default_value, .42, places=5)
        layer.enabled = False
        # Disabled rings may remain muted internally; the render test checks their actual result.
        self.assertFalse(ring.get_stack(self.target).error)
        layer.enabled = True
        self.assertIn(layer.uid, self.mix_uids())
        self.assertEqual(graph_snapshot(self.red.node_tree), source_before)

    def test_duplicate_copies_parameters_but_new_uid_and_independent_edits(self):
        first = self.add(name="Golden Arc", radius=.31, width=.07, softness=.02,
                         mode="ARC", start_angle=-42, sweep_angle=135)
        original_uid = first.uid
        stack = ring.get_stack(self.target)
        index = next(i for i, layer in enumerate(stack.layers) if layer.uid == original_uid)
        duplicate = ring.duplicate_ring(self.target, index)
        self.assertNotEqual(original_uid, duplicate.uid)
        first = next(layer for layer in stack.layers if layer.uid == original_uid)
        for field in ("radius", "width", "softness", "mode", "start_angle", "sweep_angle",
                      "source_material", "enabled"):
            self.assertEqual(getattr(first, field), getattr(duplicate, field))
        duplicate.radius = .63
        self.assertAlmostEqual(first.radius, .31, places=5)
        self.assertEqual(len(stack.layers), 2)

    def test_move_changes_priority_and_delete_last_restores_original_surface(self):
        original = graph_snapshot(self.target.node_tree)
        self.add(source=self.red)
        self.add(source=self.green)
        stack = ring.get_stack(self.target)
        order = [layer.uid for layer in stack.layers]
        ring.move_ring(self.target, 0, 1)
        self.assertEqual([layer.uid for layer in stack.layers], list(reversed(order)))
        self.assertEqual(self.mix_uids(), list(reversed(order)))
        ring.delete_ring(self.target, 0)
        self.assertEqual(self.mix_uids(), [order[0]])
        ring.delete_ring(self.target, 0)
        self.assertEqual(len(stack.layers), 0)
        self.assertEqual(graph_snapshot(self.target.node_tree), original)

    def test_source_image_and_nested_group_datablocks_remain_shared(self):
        image = bpy.data.images.new("Ring Stack Test shared image", width=1, height=1)
        image.pixels[:] = (1, .1, .2, 1)
        nested = bpy.data.node_groups.new("Artist Shared Texture", "ShaderNodeTree")
        nested.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        tex = nested.nodes.new("ShaderNodeTexImage")
        tex.image = image
        out = nested.nodes.new("NodeGroupOutput")
        nested.links.new(tex.outputs["Color"], out.inputs["Color"])
        source_tree = self.red.node_tree
        node = source_tree.nodes.new("ShaderNodeGroup")
        node.node_tree = nested
        source_tree.links.new(node.outputs["Color"], source_tree.nodes["Original Base"].inputs["Color"])
        before, nested_before = graph_snapshot(source_tree), graph_snapshot(nested)
        groups_before = set(bpy.data.node_groups)
        images_before = set(bpy.data.images)
        self.add()
        trees = list(reachable_trees(self.group()))
        self.assertIn(nested, trees)
        self.assertTrue(any(node.image == image for tree in trees for node in tree.nodes
                            if node.bl_idname == "ShaderNodeTexImage"))
        self.assertEqual(set(bpy.data.images), images_before)
        self.assertEqual(graph_snapshot(source_tree), before)
        self.assertEqual(graph_snapshot(nested), nested_before)
        self.assertFalse(any(group.name.startswith("Artist Shared Texture.")
                             for group in set(bpy.data.node_groups) - groups_before))

    def test_self_source_validation_preserves_last_working_graph(self):
        layer = self.add()
        group = self.group()
        before = graph_snapshot(group)
        layer.source_material = self.target
        self.assertTrue(ring.get_stack(self.target).error)
        with self.assertRaises(ValueError):
            ring.rebuild_stack(self.target)
        self.assertEqual(surface_node(self.target).node_tree, group)
        self.assertEqual(graph_snapshot(group), before)

    def test_disabled_invalid_source_is_ignored_and_valid_source_recovers_error(self):
        layer = self.add()
        stack = ring.get_stack(self.target)
        layer.source_material = self.target
        self.assertTrue(stack.error)
        layer.enabled = False
        self.assertFalse(stack.error, stack.error)
        self.assertEqual(self.mix_uids(), [])
        layer.enabled = True
        self.assertTrue(stack.error)
        layer.source_material = self.green
        self.assertFalse(stack.error, stack.error)
        self.assertEqual(self.mix_uids(), [layer.uid])
        self.assertEqual(surface_node(self.target).inputs["Base Shader"].links[0].from_node.name,
                         "Original Base")

    def test_repeated_parameter_edits_keep_group_count_bounded_and_preserve_identity(self):
        self.add(source=self.red)
        self.add(source=self.green)
        stack = ring.get_stack(self.target)
        original_group = self.group()
        original_names = set(bpy.data.node_groups.keys())
        source_snapshots = (graph_snapshot(self.red.node_tree), graph_snapshot(self.green.node_tree))
        for index in range(50):
            stack.layers[0].radius = .1 + index * .01
            self.assertFalse(stack.error, stack.error)
            self.assertEqual(self.group(), original_group)
            self.assertEqual(set(bpy.data.node_groups.keys()), original_names)
        self.assertEqual(graph_snapshot(self.red.node_tree), source_snapshots[0])
        self.assertEqual(graph_snapshot(self.green.node_tree), source_snapshots[1])

    def test_indirect_material_dependency_cycle_is_rejected_without_destroying_graph(self):
        self.add(material=self.red, source=self.green)
        self.add(material=self.target, source=self.red)
        layer = self.add(material=self.green, source=make_material("Leaf"))
        before = graph_snapshot(self.group(self.green))
        layer.source_material = self.target
        self.assertTrue(ring.get_stack(self.green).error)
        with self.assertRaises(ValueError):
            ring.rebuild_stack(self.green)
        self.assertEqual(graph_snapshot(self.group(self.green)), before)

    def test_material_copy_edit_is_isolated_from_original_stack_and_layers(self):
        self.add(name="Original ring", radius=.2)
        original_tree = graph_snapshot(self.target.node_tree)
        original_group = self.group()
        original_contents = graph_snapshot(original_group)
        original_layers = layer_state(ring.get_stack(self.target))
        duplicate = self.target.copy()
        duplicate.name = "Independent Material Copy"
        copied = ring.get_stack(duplicate)
        copied.layers[0].radius = .72
        ring.rebuild_stack(duplicate)
        self.assertNotEqual(self.group(duplicate), original_group)
        self.assertEqual(graph_snapshot(self.target.node_tree), original_tree)
        self.assertEqual(graph_snapshot(original_group), original_contents)
        self.assertEqual(layer_state(ring.get_stack(self.target)), original_layers)
        self.assertAlmostEqual(copied.layers[0].radius, .72, places=5)

    def test_removed_source_material_validation_keeps_base_and_other_nodes(self):
        self.add()
        bpy.data.materials.remove(self.red, do_unlink=True)
        tree = self.target.node_tree
        original = tree.nodes["Original Base"]
        # Clearing a source can bypass that layer or report it, but may never lose Base.
        try:
            ring.rebuild_stack(self.target)
        except ValueError:
            pass
        self.assertIn(original.name, tree.nodes)
        self.assertEqual(surface_node(self.target).inputs["Base Shader"].links[0].from_node, original)
        ring.remove_stack(self.target)
        self.assertEqual(surface_node(self.target), original)

    def test_user_rewired_surface_is_not_overwritten_by_later_ring_edits(self):
        layer = self.add()
        tree = self.target.node_tree
        alternative = tree.nodes.new("ShaderNodeBsdfDiffuse")
        tree.links.new(alternative.outputs[0], tree.nodes["Original Output"].inputs["Surface"])
        layer.width = .24
        self.assertEqual(surface_node(self.target), alternative)
        try:
            ring.rebuild_stack(self.target)
        except ValueError:
            pass
        self.assertEqual(surface_node(self.target), alternative)
        with self.assertRaises(ValueError):
            ring.remove_stack(self.target)
        self.assertEqual(surface_node(self.target), alternative)

    def test_failed_duplicate_keeps_layer_data_order_and_original_graph(self):
        self.add(name="Preserved")
        stack = ring.get_stack(self.target)
        layers_before = layer_state(stack)
        graph_before = graph_snapshot(self.group())
        material_before = graph_snapshot(self.target.node_tree)
        groups_before = set(bpy.data.node_groups)
        with mock.patch.object(ring, "_stage_group", side_effect=RuntimeError("Injected staging failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected staging failure"):
                ring.duplicate_ring(self.target, 0)
        self.assertEqual(layer_state(stack), layers_before)
        self.assertEqual(graph_snapshot(self.group()), graph_before)
        self.assertEqual(graph_snapshot(self.target.node_tree), material_before)
        self.assertEqual(set(bpy.data.node_groups), groups_before)

    def test_failed_commit_restores_existing_group_and_does_not_leak_temporary_groups(self):
        self.add()
        group = self.group()
        graph_before = graph_snapshot(group)
        material_before = graph_snapshot(self.target.node_tree)
        groups_before = set(bpy.data.node_groups)
        original_copy = ring.shader._copy_node_graph
        failed = False

        def fail_once(source_nodes, source_links, destination):
            nonlocal failed
            if destination == group and not failed:
                failed = True
                destination.nodes.new("ShaderNodeTexNoise")
                raise RuntimeError("Injected mid-commit failure")
            return original_copy(source_nodes, source_links, destination)

        with mock.patch.object(ring.shader, "_copy_node_graph", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "Injected mid-commit failure"):
                ring.rebuild_stack(self.target)
        self.assertTrue(failed)
        self.assertEqual(surface_node(self.target).node_tree, group)
        self.assertEqual(graph_snapshot(group), graph_before)
        self.assertEqual(graph_snapshot(self.target.node_tree), material_before)
        self.assertEqual(set(bpy.data.node_groups), groups_before)

    def test_current_material_honors_pinned_nested_editor_and_rejects_conflicting_editors(self):
        def editor(material, shader_type="OBJECT"):
            space = SimpleNamespace(tree_type="ShaderNodeTree", shader_type=shader_type,
                                    id=material, edit_tree=self.mask, pin=True)
            return SimpleNamespace(type="NODE_EDITOR", spaces=SimpleNamespace(active=space))

        first, second = editor(self.target), editor(self.red)
        context = SimpleNamespace(area=first, space_data=first.spaces.active,
                                  active_object=SimpleNamespace(active_material=self.green))
        self.assertEqual(ui.current_material(context), self.target)
        context.area = SimpleNamespace(type="VIEW_3D")
        context.window = SimpleNamespace(screen=SimpleNamespace(areas=[first, second]))
        self.assertIsNone(ui.current_material(context))
        context.window.screen.areas = [first, editor(self.target)]
        self.assertEqual(ui.current_material(context), self.target)
        context.window.screen.areas = []
        self.assertEqual(ui.current_material(context), self.green)
        context.window.screen.areas = [editor(None, "WORLD")]
        self.assertIsNone(ui.current_material(context))

    def test_ui_draw_exposes_ordered_layer_controls_and_arc_fields_only_when_needed(self):
        layer = self.add()
        calls = []

        class Layout:
            enabled = True
            active = True

            def row(self, **kwargs):
                return self

            def column(self, **kwargs):
                return self

            def label(self, **kwargs):
                calls.append(("label", kwargs))

            def prop(self, data, name, **kwargs):
                calls.append(("prop", name, kwargs))

            def operator(self, name, **kwargs):
                calls.append(("operator", name, kwargs))
                return SimpleNamespace()

            def template_list(self, *args, **kwargs):
                calls.append(("list", args, kwargs))

        context = SimpleNamespace(area=SimpleNamespace(type="VIEW_3D"), window=None,
                                  active_object=SimpleNamespace(active_material=self.target))
        ui.draw_ring_stack(Layout(), context)
        self.assertTrue(next(call for call in calls if call[0] == "list")[2]["sort_lock"])
        properties = {call[1] for call in calls if call[0] == "prop"}
        self.assertTrue({"mode", "radius", "width", "softness", "source_material"} <= properties)
        self.assertNotIn("start_angle", properties)
        layer.mode = "ARC"
        calls.clear()
        ui.draw_ring_stack(Layout(), context)
        properties = {call[1] for call in calls if call[0] == "prop"}
        self.assertTrue({"start_angle", "sweep_angle"} <= properties)

    def test_operator_add_undo_redo_restores_both_layer_data_and_surface_graph(self):
        """Use Blender's actual undo stack, never a mocked operator result."""
        ui.register()
        try:
            mesh = bpy.data.meshes.new("Ring Stack Undo Mesh")
            obj = bpy.data.objects.new("Ring Stack Undo Object", mesh)
            bpy.context.scene.collection.objects.link(obj)
            mesh.materials.append(self.target)
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            target_name = self.target.name
            graph_before = graph_snapshot(self.target.node_tree)
            self.assertTrue(bpy.context.preferences.edit.use_global_undo)
            # Background scripts have no UI event to delimit an undo step. The
            # pushes create the same before/after boundaries as a button click.
            self.assertEqual(bpy.ops.ed.undo_push(message="Before Ring Stack Add"), {"FINISHED"})
            self.assertEqual(bpy.ops.rr_builder.ring_stack_action(action="ADD"), {"FINISHED"})
            self.target = bpy.data.materials[target_name]
            state_after = layer_state(ring.get_stack(self.target))
            self.assertEqual(len(state_after), 1)
            self.assertEqual(surface_node(self.target).bl_idname, "ShaderNodeGroup")
            self.assertEqual(bpy.ops.ed.undo_push(message="Ring Stack Add"), {"FINISHED"})
            self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
            self.target = bpy.data.materials[target_name]
            self.assertEqual(len(ring.get_stack(self.target).layers), 0)
            self.assertEqual(graph_snapshot(self.target.node_tree), graph_before)
            self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
            self.target = bpy.data.materials[target_name]
            self.assertEqual(layer_state(ring.get_stack(self.target)), state_after)
            self.assertEqual(surface_node(self.target).inputs["Base Shader"].links[0].from_node.name,
                             "Original Base")
        finally:
            ui.unregister()

    def test_save_reopen_and_registration_reload_preserve_layer_data_and_connections(self):
        self.add(name="Saved Arc", radius=.27, width=.11, softness=.03,
                 mode="ARC", start_angle=-25, sweep_angle=155)
        self.add(source=self.green, name="Disabled trim", enabled=False)
        expected = layer_state(ring.get_stack(self.target))
        target_name = self.target.name
        with tempfile.TemporaryDirectory(prefix="rr-ring-stack-persistence-") as temp:
            path = str(Path(temp) / "isolated_fixture.blend")
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            ring.unregister()
            ring.register()
            bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
            self.target = bpy.data.materials[target_name]
            self.assertEqual(layer_state(ring.get_stack(self.target)), expected)
            self.assertEqual(surface_node(self.target).inputs["Base Shader"].links[0].from_node.name,
                             "Original Base")
            ring.rebuild_stack(self.target)
            self.assertFalse(ring.get_stack(self.target).error)
            self.assertEqual(layer_state(ring.get_stack(self.target)), expected)

    def test_rendered_ring_arc_overlap_disable_and_base_fallback(self):
        """One 80x80, 1-sample CPU render; each tile has constant known UVs."""
        cases = [
            ("top covers lower", .45, 30, {}, (0, 1, 0)),
            ("lower only", .25, 30, {}, (1, 0, 0)),
            ("original base", .85, 30, {}, (0, 0, 1)),
            ("disable top", .45, 30, {"enabled": False}, (1, 0, 0)),
            ("arc first quadrant", .45, 30, {"mode": "ARC", "start_angle": 0, "sweep_angle": 90}, (0, 1, 0)),
            ("arc clockwise excluded", .45, -30, {"mode": "ARC", "start_angle": 0, "sweep_angle": 90}, (1, 0, 0)),
            ("arc second quadrant excluded", .45, 135, {"mode": "ARC", "start_angle": 0, "sweep_angle": 90}, (1, 0, 0)),
            ("arc negative start", .45, -30, {"mode": "ARC", "start_angle": -60, "sweep_angle": 90}, (0, 1, 0)),
            ("arc wrapped start inside", .45, 20, {"mode": "ARC", "start_angle": 330, "sweep_angle": 90}, (0, 1, 0)),
            ("arc wrapped start outside", .45, 90, {"mode": "ARC", "start_angle": 330, "sweep_angle": 90}, (1, 0, 0)),
            ("zero sweep invisible", .45, 0, {"mode": "ARC", "start_angle": 0, "sweep_angle": 0}, (1, 0, 0)),
            ("360 sweep complete", .45, 240, {"mode": "ARC", "start_angle": 150, "sweep_angle": 360}, (0, 1, 0)),
            ("360 negative start complete", .45, -150, {"mode": "ARC", "start_angle": -42, "sweep_angle": 360}, (0, 1, 0)),
            ("ring ignores arc parameters", .45, 240, {"mode": "RING", "start_angle": 0, "sweep_angle": 0}, (0, 1, 0)),
            ("outside normalized disk", 1.1, 30, {}, (0, 0, 1)),
            ("radial softness halfway", .325, 30, {"softness": .05}, (.5, .5, 0)),
            ("arc keeps radial softness", .325, 30, {"softness": .05, "mode": "ARC", "start_angle": 0, "sweep_angle": 90}, (.5, .5, 0)),
            ("zero width invisible", .45, 30, {"width": 0}, (1, 0, 0)),
        ]
        size, pixels_per_tile = 5, 16
        scene = bpy.data.scenes.new("Ring Stack Test Numeric Atlas")
        for index, (name, radius, angle, top_values, expected) in enumerate(cases):
            material = make_material("Atlas " + name)
            self.add(material, self.red, radius=.2, width=.4, softness=0)
            top = self.add(material, self.green, radius=.3, width=.3, softness=0)
            stack = ring.get_stack(material)
            top_index = next(i for i, layer in enumerate(stack.layers) if layer.uid == top.uid)
            while top_index:
                ring.move_ring(material, top_index, -1)
                top_index -= 1
            for key, value in top_values.items():
                setattr(top, key, value)
            ring.rebuild_stack(material)
            x, y = index % size, index // size
            mesh = bpy.data.meshes.new("Tile " + name)
            mesh.from_pydata([(x-.46, y-.46, 0), (x+.46, y-.46, 0),
                              (x+.46, y+.46, 0), (x-.46, y+.46, 0)], [], [(0, 1, 2, 3)])
            uv = mesh.uv_layers.new(name="UVMap")
            theta = math.radians(angle)
            coords = (.5 + radius * math.cos(theta) / 2, .5 + radius * math.sin(theta) / 2)
            for loop in uv.data:
                loop.uv = coords
            obj = bpy.data.objects.new("Tile " + name, mesh)
            scene.collection.objects.link(obj)
            mesh.materials.append(material)
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.cycles.max_bounces = 0
        scene.render.threads_mode = "FIXED"
        scene.render.threads = 2
        scene.render.resolution_x = scene.render.resolution_y = size * pixels_per_tile
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "OPEN_EXR"
        scene.render.image_settings.color_mode = "RGBA"
        scene.render.image_settings.color_depth = "32"
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        camera_data = bpy.data.cameras.new("Ring Stack Test Camera")
        camera = bpy.data.objects.new("Ring Stack Test Camera", camera_data)
        scene.collection.objects.link(camera)
        camera.location = ((size-1)/2, (size-1)/2, 10)
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = size
        scene.camera = camera
        with tempfile.TemporaryDirectory(prefix="rr-ring-stack-render-") as temp:
            scene.render.filepath = str(Path(temp) / "atlas.exr")
            bpy.ops.render.render(write_still=True, scene=scene.name)
            image = bpy.data.images.load(scene.render.filepath, check_existing=False)
            width = image.size[0]
            pixels = list(image.pixels)
            self.assertEqual(tuple(image.size), (size * pixels_per_tile,) * 2)
            for index, (name, radius, angle, values, expected) in enumerate(cases):
                x = (index % size) * pixels_per_tile + pixels_per_tile // 2
                y = (index // size) * pixels_per_tile + pixels_per_tile // 2
                rgb = pixels[(y * width + x)*4:(y * width + x)*4+3]
                with self.subTest(sample=name):
                    self.assertTrue(all(math.isfinite(value) for value in rgb), rgb)
                    for actual, wanted in zip(rgb, expected):
                        self.assertAlmostEqual(actual, wanted, delta=.002)
            bpy.data.images.remove(image)
        print("RR_RING_STACK_SHADER_SAMPLES_PASS samples=" + str(len(cases)))


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    suite = unittest.defaultTestLoader.loadTestsFromNames(arguments, sys.modules[__name__]) if arguments else \
        unittest.defaultTestLoader.loadTestsFromTestCase(RingStackTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print(f"RR_RING_STACK_PASS tests={result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
