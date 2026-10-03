"""Native Mix Shaders / Ring Group overlays in a Blender factory process.

Run serially with --background --factory-startup --disable-autoexec --threads 1
--python-exit-code 1 --python tests/test_rr_shader_mixer_ui_blender.py.
Draw handler and keymap ownership use Blender's real APIs. GPU draw calls are
mocked: visual placement and native event priority still require GUI validation.
"""

import importlib
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy
from mathutils import Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
mixer = importlib.import_module("random_realm_builder_exporter.rr_shader_mixer")
ring = importlib.import_module("random_realm_builder_exporter.rr_ring_nodes")
ui = importlib.import_module("random_realm_builder_exporter.rr_shader_mixer_ui")


def native_state(node):
    group = node.node_tree
    return (
        node.as_pointer(), node.bl_idname, group.as_pointer(), group.bl_idname,
        mixer._graph_signature(group),
        tuple((item.identifier, item.name, item.in_out, item.socket_type)
              for item in group.interface.items_tree if item.item_type == "SOCKET"),
        tuple((socket.identifier, socket.default_value)
              for socket in node.inputs if hasattr(socket, "default_value")),
        tuple((link.from_node.name, link.from_socket.identifier,
               link.to_node.name, link.to_socket.identifier) for link in node.id_data.links),
    )


def center(rectangle):
    left, bottom, right, top = rectangle
    return (left + right) * .5, (bottom + top) * .5


def keymap_item_ids(config):
    return tuple((keymap.name, item.id) for keymap in config.keymaps
                 for item in keymap.keymap_items
                 if item.idname == ui.RR_OT_click_shader_mixer_buttons.bl_idname)


class ShaderMixerUiTests(unittest.TestCase):
    def setUp(self):
        ui.unregister()
        for material in tuple(bpy.data.materials):
            bpy.data.materials.remove(material)
        for group in tuple(bpy.data.node_groups):
            bpy.data.node_groups.remove(group)
        self.material = bpy.data.materials.new("Native Mixer UI Fixture")
        self.material.use_nodes = True
        self.tree = self.material.node_tree
        self.node = mixer.add_mix_shaders(self.tree, location=(120, -35))
        self.node.select = True
        self.tree.nodes.active = self.node
        self.context = SimpleNamespace(
            area=SimpleNamespace(type="NODE_EDITOR", tag_redraw=mock.Mock()),
            region=SimpleNamespace(type="WINDOW", width=640, height=480),
            space_data=SimpleNamespace(type="NODE_EDITOR", tree_type="ShaderNodeTree", edit_tree=self.tree),
            preferences=bpy.context.preferences,
        )
        self.rectangles = ui.button_layout_rects((100, 200), (320, 180), region_size=(640, 480))
        self.config = bpy.context.window_manager.keyconfigs.addon
        self.private_config = None
        if self.config is None:
            # Some background startup paths lack the global add-on keyconfig.
            # A real isolated keyconfig still verifies Blender keymap APIs and
            # ownership without modifying its default/user keyconfig.
            self.private_config = bpy.context.window_manager.keyconfigs.new("RR Mixer UI Factory Tests")
            self.config = self.private_config
        self.registration_context = SimpleNamespace(
            window_manager=SimpleNamespace(keyconfigs=SimpleNamespace(addon=self.config)))
        self.foreign_items = []

    def tearDown(self):
        ui.unregister()
        for keymap, item in self.foreign_items:
            try:
                keymap.keymap_items.remove(item)
            except (ReferenceError, RuntimeError):
                pass
        if self.private_config is not None:
            bpy.context.window_manager.keyconfigs.remove(self.private_config)
        for material in tuple(bpy.data.materials):
            bpy.data.materials.remove(material)
        for group in tuple(bpy.data.node_groups):
            bpy.data.node_groups.remove(group)

    def register_ui(self):
        with mock.patch.object(ui.bpy, "context", self.registration_context):
            ui.register()

    def invoke(self, point):
        event = SimpleNamespace(mouse_region_x=point[0], mouse_region_y=point[1])
        return ui.RR_OT_click_shader_mixer_buttons.invoke(
            SimpleNamespace(report=mock.Mock()), self.context, event)

    def fake_gpu(self):
        shader = mock.Mock()
        gpu = SimpleNamespace(
            state=SimpleNamespace(blend_get=mock.Mock(return_value="NONE"), blend_set=mock.Mock()),
            shader=SimpleNamespace(from_builtin=mock.Mock(return_value=shader), unbind=mock.Mock()),
        )
        return gpu


    def ring_node(self, mask_count=2):
        node = self.tree.nodes.new("ShaderNodeGroup")
        node.node_tree = ring.new_group(mask_count)
        node.name = "Ring Group controls"
        node.select = True
        self.tree.nodes.active = node
        return node


    def test_ring_group_header_dispatches_mask_slots_with_options_hidden(self):
        node = self.ring_node()
        node.show_options = False
        before = native_state(node)
        self.assertEqual(ui._active_node(self.context), node)
        self.assertTrue(ui.RR_OT_click_shader_mixer_buttons.poll(self.context))
        operations = SimpleNamespace(rr_builder=SimpleNamespace(
            add_mask_slot=mock.Mock(return_value={"FINISHED"}),
            remove_mask_slot=mock.Mock(return_value={"FINISHED"}),
            add_shader_slot=mock.Mock(), remove_shader_slot=mock.Mock()))
        gpu = self.fake_gpu()
        with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
            with mock.patch.object(ui.bpy, "context", self.context):
                with mock.patch.object(ui, "gpu", gpu):
                    with mock.patch.object(ui, "_draw_button") as draw:
                        ui.draw_overlay()
            with mock.patch.object(ui.bpy, "ops", operations):
                self.assertEqual(self.invoke(center(self.rectangles["ADD"])), {"FINISHED"})
                self.assertEqual(self.invoke(center(self.rectangles["REMOVE"])), {"FINISHED"})
        self.assertEqual([call.args[2:] for call in draw.call_args_list],
                         [("ADD", True), ("REMOVE", True)])
        operations.rr_builder.add_mask_slot.assert_called_once_with("EXEC_DEFAULT", node_name=node.name)
        operations.rr_builder.remove_mask_slot.assert_called_once_with("EXEC_DEFAULT", node_name=node.name)
        operations.rr_builder.add_shader_slot.assert_not_called()
        operations.rr_builder.remove_shader_slot.assert_not_called()
        self.assertEqual(native_state(node), before)


    def test_ring_group_minimum_one_disables_minus_and_keeps_plus(self):
        node = self.ring_node(1)
        before = native_state(node)
        operations = SimpleNamespace(rr_builder=SimpleNamespace(
            add_mask_slot=mock.Mock(return_value={"FINISHED"}), remove_mask_slot=mock.Mock()))
        gpu = self.fake_gpu()
        with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
            with mock.patch.object(ui.bpy, "ops", operations):
                self.assertEqual(self.invoke(center(self.rectangles["REMOVE"])), {"CANCELLED"})
                self.assertEqual(self.invoke(center(self.rectangles["ADD"])), {"FINISHED"})
            with mock.patch.object(ui.bpy, "context", self.context):
                with mock.patch.object(ui, "gpu", gpu):
                    with mock.patch.object(ui, "_draw_button") as draw:
                        ui.draw_overlay()
        self.assertEqual([call.args[2:] for call in draw.call_args_list],
                         [("ADD", True), ("REMOVE", False)])
        operations.rr_builder.remove_mask_slot.assert_not_called()
        self.assertEqual(native_state(node), before)


    def test_ring_group_unselected_collapsed_and_sidebar_controls_are_hidden(self):
        node = self.ring_node()
        before = native_state(node)
        for attribute, value in (("select", False), ("hide", True)):
            old = getattr(node, attribute)
            setattr(node, attribute, value)
            self.assertIsNone(ui._active_node(self.context))
            self.assertEqual(ui.node_button_rects(self.context), {})
            setattr(node, attribute, old)
        self.context.region.type = "UI"
        self.assertIsNone(ui._active_node(self.context))
        self.assertEqual(native_state(node), before)


    def test_ring_group_rejected_nested_edit_is_reported_without_mutation(self):
        node = self.ring_node()
        before = native_state(node)
        operations = SimpleNamespace(rr_builder=SimpleNamespace(
            remove_mask_slot=mock.Mock(side_effect=RuntimeError("Animated inputs cannot be removed"))))
        operator = SimpleNamespace(report=mock.Mock())
        point = center(self.rectangles["REMOVE"])
        event = SimpleNamespace(mouse_region_x=point[0], mouse_region_y=point[1])
        with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
            with mock.patch.object(ui.bpy, "ops", operations):
                self.assertEqual(ui.RR_OT_click_shader_mixer_buttons.invoke(
                    operator, self.context, event), {"CANCELLED"})
        operator.report.assert_called_once_with({"ERROR"}, "Animated inputs cannot be removed")
        self.assertEqual(native_state(node), before)

    def test_real_register_is_idempotent_and_unregister_removes_only_owned_resources(self):
        keymap = self.config.keymaps.new(name="Node Editor", space_type="NODE_EDITOR", region_type="WINDOW")
        foreign = keymap.keymap_items.new("wm.search_menu", "F6", "PRESS")
        self.foreign_items.append((keymap, foreign))
        foreign_id = foreign.id
        before = native_state(self.node)
        self.register_ui()
        first_handle = ui._DRAW_HANDLE
        first_items = keymap_item_ids(self.config)
        self.assertIsNotNone(first_handle)
        self.assertEqual(len(first_items), 1)
        self.assertEqual(len(ui._KEYMAP_ITEMS), 1)
        self.assertTrue(ui.RR_OT_click_shader_mixer_buttons.is_registered)
        self.register_ui()
        self.assertIs(ui._DRAW_HANDLE, first_handle)
        self.assertEqual(keymap_item_ids(self.config), first_items)
        own_keymap, own_item = ui._KEYMAP_ITEMS[0]
        self.assertEqual(own_keymap.space_type, "NODE_EDITOR")
        self.assertEqual(own_item.type, "LEFTMOUSE")
        self.assertEqual(own_item.value, "PRESS")
        ui.unregister()
        ui.unregister()
        self.assertIsNone(ui._DRAW_HANDLE)
        self.assertEqual(ui._KEYMAP_ITEMS, [])
        self.assertEqual(keymap_item_ids(self.config), ())
        self.assertFalse(getattr(ui.RR_OT_click_shader_mixer_buttons, "is_registered", False))
        self.assertIn(foreign_id, [item.id for item in keymap.keymap_items])
        self.assertEqual(native_state(self.node), before)

    def test_partial_register_failure_removes_real_draw_handle_and_operator_class(self):
        bad_config = SimpleNamespace(keymaps=SimpleNamespace(
            new=mock.Mock(side_effect=RuntimeError("Injected keymap creation failure"))))
        failed_context = SimpleNamespace(window_manager=SimpleNamespace(
            keyconfigs=SimpleNamespace(addon=bad_config)))
        original_remove = bpy.types.SpaceNodeEditor.draw_handler_remove
        with mock.patch.object(bpy.types.SpaceNodeEditor, "draw_handler_remove", wraps=original_remove) as remove:
            with mock.patch.object(ui.bpy, "context", failed_context):
                with self.assertRaisesRegex(RuntimeError, "Injected keymap creation failure"):
                    ui.register()
            self.assertEqual(remove.call_count, 1)
        self.assertIsNone(ui._DRAW_HANDLE)
        self.assertEqual(ui._KEYMAP_ITEMS, [])
        self.assertFalse(getattr(ui.RR_OT_click_shader_mixer_buttons, "is_registered", False))
        self.register_ui()
        self.assertIsNotNone(ui._DRAW_HANDLE)
        self.assertEqual(len(ui._KEYMAP_ITEMS), 1)

    def test_pure_layout_has_precise_nonoverlapping_hits_and_follows_dpi_zoom(self):
        self.assertEqual(set(self.rectangles), {"ADD", "REMOVE"})
        for action, rect in self.rectangles.items():
            with self.subTest(action=action):
                self.assertEqual(ui.button_at_point(self.rectangles, center(rect)), action)
                self.assertIsNone(ui.button_at_point(self.rectangles, (rect[2], rect[3])))
                self.assertGreater(rect[0], 100 + 80)  # Leave the disclosure/title side alone.
        gap = (self.rectangles["ADD"][2] + self.rectangles["REMOVE"][0]) * .5
        self.assertIsNone(ui.button_at_point(self.rectangles, (gap, center(self.rectangles["ADD"])[1])))
        for point in ((0, 0), (150, 190), (321, 190), (310, 201)):
            self.assertIsNone(ui.button_at_point(self.rectangles, point))
        scaled = ui.button_layout_rects((150, 300), (480, 270), ui_scale=1.5, region_size=(960, 720))
        for action, rect in self.rectangles.items():
            for actual, expected in zip(scaled[action], rect):
                self.assertAlmostEqual(actual, expected * 1.5)
        zoomed = ui.button_layout_rects((50, 100), (182, 88), region_size=(640, 480))
        self.assertEqual(set(zoomed), {"ADD", "REMOVE"})
        self.assertLess(zoomed["ADD"][2] - zoomed["ADD"][0],
                        self.rectangles["ADD"][2] - self.rectangles["ADD"][0])

    def test_tiny_offscreen_invalid_layouts_are_hidden(self):
        for top_left, bottom_right, options in (
            ((0, 10), (50, 2), {}),
            ((0, 20), (220, 0), {"region_size": (200, 480)}),
            ((-300, 20), (-80, 0), {"region_size": (640, 480)}),
            ((0, float("nan")), (220, 0), {}),
            ((0, 20), (220, 0), {"ui_scale": 0}),
            ((0, 20), (220, 0), {"region_size": (640, math.inf)}),
        ):
            with self.subTest(top_left=top_left, options=options):
                self.assertEqual(ui.button_layout_rects(top_left, bottom_right, **options), {})

    def test_projection_uses_absolute_frame_location_and_dpi_scaled_dimensions(self):
        view = SimpleNamespace(view_to_region=mock.Mock(
            side_effect=lambda x, y, clip=False: (x * .75 + 40, y * .75 + 350)))
        context = SimpleNamespace(
            preferences=SimpleNamespace(system=SimpleNamespace(dpi=108)),
            region=SimpleNamespace(view2d=view, width=1000, height=800))
        node = SimpleNamespace(location_absolute=Vector((100, -40)), dimensions=Vector((330, 250)))
        with mock.patch.object(ui, "_active_node", return_value=node):
            actual = ui.node_button_rects(context, node)
        self.assertEqual(view.view_to_region.call_args_list,
                         [mock.call(150, -60, clip=False), mock.call(480, -90, clip=False)])
        expected = ui.button_layout_rects((152.5, 305), (400, 282.5), ui_scale=1.5,
                                         region_size=(1000, 800))
        self.assertEqual(actual, expected)
        self.assertEqual(set(actual), {"ADD", "REMOVE"})

    def test_nonmixer_hidden_unselected_and_readonly_context_never_show_controls(self):
        self.assertTrue(ui.RR_OT_click_shader_mixer_buttons.poll(self.context))
        before = native_state(self.node)
        ordinary = self.tree.nodes.new("ShaderNodeMixShader")
        self.tree.nodes.active = ordinary
        ordinary.select = True
        self.assertFalse(ui.RR_OT_click_shader_mixer_buttons.poll(self.context))
        self.assertEqual(ui.node_button_rects(self.context), {})
        self.tree.nodes.active = self.node
        for attribute in ("hide", "select"):
            old = getattr(self.node, attribute)
            setattr(self.node, attribute, attribute == "hide")
            self.assertFalse(ui.RR_OT_click_shader_mixer_buttons.poll(self.context))
            self.assertEqual(ui.node_button_rects(self.context), {})
            setattr(self.node, attribute, old)
        self.context.space_data.edit_tree = SimpleNamespace(is_editable=False, library=None)
        self.assertFalse(ui.RR_OT_click_shader_mixer_buttons.poll(self.context))
        self.assertEqual(ui.node_button_rects(self.context), {})
        self.context.space_data.edit_tree = self.tree
        self.context.region.type = "UI"
        self.assertFalse(ui.RR_OT_click_shader_mixer_buttons.poll(self.context))
        self.assertEqual(native_state(self.node), before)

    def test_hidden_node_options_keep_selected_header_controls_and_clicks(self):
        self.node.show_options = False
        before = native_state(self.node)
        self.assertEqual(ui._active_node(self.context), self.node)
        self.assertTrue(ui.RR_OT_click_shader_mixer_buttons.poll(self.context))
        gpu = self.fake_gpu()
        operations = SimpleNamespace(rr_builder=SimpleNamespace(
            add_shader_slot=mock.Mock(return_value={"FINISHED"}),
            remove_shader_slot=mock.Mock(return_value={"FINISHED"})))
        # Background Blender has no laid-out node dimensions or GPU viewport;
        # use known header rectangles while retaining the real selected node
        # and its Show Options state for drawing and click eligibility.
        with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
            with mock.patch.object(ui.bpy, "context", self.context):
                with mock.patch.object(ui, "gpu", gpu):
                    with mock.patch.object(ui, "_draw_button") as draw:
                        ui.draw_overlay()
            with mock.patch.object(ui.bpy, "ops", operations):
                self.assertEqual(self.invoke(center(self.rectangles["ADD"])), {"FINISHED"})
                self.assertEqual(self.invoke(center(self.rectangles["REMOVE"])), {"FINISHED"})
        self.assertEqual([call.args[2:] for call in draw.call_args_list],
                         [("ADD", True), ("REMOVE", True)])
        operations.rr_builder.add_shader_slot.assert_called_once_with(
            "EXEC_DEFAULT", node_name=self.node.name)
        operations.rr_builder.remove_shader_slot.assert_called_once_with(
            "EXEC_DEFAULT", node_name=self.node.name)
        self.assertFalse(self.node.show_options)
        self.assertEqual(native_state(self.node), before)

    def test_outside_and_gap_clicks_pass_through_without_native_graph_changes(self):
        operations = SimpleNamespace(rr_builder=SimpleNamespace(add_shader_slot=mock.Mock(),
                                                                remove_shader_slot=mock.Mock()))
        before = native_state(self.node)
        gap = (self.rectangles["ADD"][2] + self.rectangles["REMOVE"][0]) * .5
        with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
            with mock.patch.object(ui.bpy, "ops", operations):
                for point in ((0, 0), (150, 190), (gap, 190), (320, 190)):
                    self.assertEqual(self.invoke(point), {"PASS_THROUGH"})
        operations.rr_builder.add_shader_slot.assert_not_called()
        operations.rr_builder.remove_shader_slot.assert_not_called()
        self.context.area.tag_redraw.assert_not_called()
        self.assertEqual(native_state(self.node), before)

    def test_exact_button_clicks_dispatch_explicit_node_name(self):
        operations = SimpleNamespace(rr_builder=SimpleNamespace(
            add_shader_slot=mock.Mock(return_value={"FINISHED"}),
            remove_shader_slot=mock.Mock(return_value={"FINISHED"})))
        before = native_state(self.node)
        with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
            with mock.patch.object(ui.bpy, "ops", operations):
                self.assertEqual(self.invoke(center(self.rectangles["ADD"])), {"FINISHED"})
                self.assertEqual(self.invoke(center(self.rectangles["REMOVE"])), {"FINISHED"})
        operations.rr_builder.add_shader_slot.assert_called_once_with("EXEC_DEFAULT", node_name=self.node.name)
        operations.rr_builder.remove_shader_slot.assert_called_once_with("EXEC_DEFAULT", node_name=self.node.name)
        self.assertEqual(self.context.area.tag_redraw.call_count, 2)
        self.assertEqual(native_state(self.node), before)

    def test_rejected_nested_edit_reports_cleanly_and_keeps_native_state(self):
        before = native_state(self.node)
        for action, operation in (("ADD", "add_shader_slot"), ("REMOVE", "remove_shader_slot")):
            with self.subTest(action=action):
                rejected = mock.Mock(side_effect=RuntimeError("Customized group cannot be rebuilt"))
                operations = SimpleNamespace(rr_builder=SimpleNamespace(**{operation: rejected}))
                operator = SimpleNamespace(report=mock.Mock())
                point = center(self.rectangles[action])
                event = SimpleNamespace(mouse_region_x=point[0], mouse_region_y=point[1])
                with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
                    with mock.patch.object(ui.bpy, "ops", operations):
                        self.assertEqual(ui.RR_OT_click_shader_mixer_buttons.invoke(
                            operator, self.context, event), {"CANCELLED"})
                operator.report.assert_called_once_with({"ERROR"}, "Customized group cannot be rebuilt")
                self.assertEqual(native_state(self.node), before)

    def test_minimum_one_disables_minus_but_preserves_native_group_and_gpu_blend(self):
        self.assertEqual(mixer.remove_shader_slot(self.node), 1)
        before = native_state(self.node)
        operations = SimpleNamespace(rr_builder=SimpleNamespace(remove_shader_slot=mock.Mock()))
        with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
            with mock.patch.object(ui.bpy, "ops", operations):
                self.assertEqual(self.invoke(center(self.rectangles["REMOVE"])), {"CANCELLED"})
        operations.rr_builder.remove_shader_slot.assert_not_called()
        gpu = self.fake_gpu()
        with mock.patch.object(ui.bpy, "context", self.context):
            with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
                with mock.patch.object(ui, "gpu", gpu):
                    with mock.patch.object(ui, "_draw_button") as draw:
                        ui.draw_overlay()
        self.assertEqual([call.args[2:] for call in draw.call_args_list],
                         [("ADD", True), ("REMOVE", False)])
        self.assertEqual(gpu.state.blend_set.call_args_list, [mock.call("ALPHA"), mock.call("NONE")])
        gpu.shader.unbind.assert_called_once_with()
        self.assertEqual(native_state(self.node), before)

    def test_draw_failure_restores_gpu_blend_and_does_not_mutate_shader_graph(self):
        before = native_state(self.node)
        gpu = self.fake_gpu()
        with mock.patch.object(ui.bpy, "context", self.context):
            with mock.patch.object(ui, "node_button_rects", return_value=self.rectangles):
                with mock.patch.object(ui, "gpu", gpu):
                    with mock.patch.object(ui, "_draw_button", side_effect=RuntimeError("Injected GPU draw failure")):
                        ui.draw_overlay()
        self.assertEqual(gpu.state.blend_set.call_args_list, [mock.call("ALPHA"), mock.call("NONE")])
        gpu.shader.unbind.assert_called_once_with()
        self.assertEqual(native_state(self.node), before)


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ShaderMixerUiTests))
    if result.wasSuccessful():
        print(f"RR_SHADER_MIXER_UI_PASS tests={result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
