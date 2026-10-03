"""Migration checks using actual saved native groups, without rendering."""
import importlib
from pathlib import Path
import sys
import unittest
from unittest import mock
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
ring = importlib.import_module('random_realm_builder_exporter.rr_ring_nodes')


class RetirementTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        arc_path = ROOT / 'node_library/validation/fixtures/Randy_Arc_Mask_0_2_0.blend'
        if not arc_path.exists():
            arc_path = ROOT / 'node_library/assets/Randy_Arc_Mask.blend'
        with bpy.data.libraries.load(str(arc_path)) as (_a, b):
            b.node_groups = ['Arc Mask']
        self.arc = b.node_groups[0]
        path = ROOT / 'node_library/dependencies/Randy_Ring_Mask.blend'
        if not path.exists():
            path = ROOT / 'node_library/assets/Randy_Ring_Mask.blend'
        with bpy.data.libraries.load(str(path)) as (_a, b):
            b.node_groups = ['Ring Mask']
        self.old = b.node_groups[0]
        self.mat = bpy.data.materials.new('Migration fixture')
        self.mat.use_nodes = True
        self.tree = self.mat.node_tree
        self.n = self.tree.nodes.new('ShaderNodeGroup')
        self.n.node_tree = self.old
        self.n.inputs['Inner Radius'].default_value = .3
        self.n.inputs['Ring Width'].default_value = .01
        self.n.inputs['Edge Softness'].default_value = .004
        self.source = self.tree.nodes.new('ShaderNodeValue')
        self.source.outputs[0].default_value = .27
        self.mix = self.tree.nodes.new('ShaderNodeMixShader')
        self.tree.links.new(self.source.outputs[0], self.n.inputs['Inner Radius'])
        self.tree.links.new(self.n.outputs['Mask'], self.mix.inputs[0])

    def test_existing_node_connections_controls_and_presentation_survive(self):
        self.n.location = (140, -35)
        self.n.label = 'My inner ring'
        self.n.hide = True
        self.n.show_options = False
        pointer = self.n.as_pointer()
        self.assertEqual(ring.replace_legacy_ring_masks(self.tree, self.arc), 1)
        self.assertEqual(self.n.as_pointer(), pointer)
        self.assertEqual(self.n.node_tree, self.arc)
        self.assertEqual(self.n.inputs['Start Angle'].default_value, 0)
        self.assertEqual(self.n.inputs['Sweep Angle'].default_value, 360)
        self.assertAlmostEqual(self.n.inputs['Ring Width'].default_value, .01)
        self.assertAlmostEqual(self.n.inputs['Edge Softness'].default_value, .004)
        self.assertEqual(self.n.inputs['Inner Radius'].links[0].from_node, self.source)
        self.assertEqual(self.mix.inputs[0].links[0].from_node, self.n)
        self.assertEqual(tuple(self.n.location), (140, -35))
        self.assertEqual(self.n.label, 'My inner ring')
        self.assertTrue(self.n.hide)
        self.assertFalse(self.n.show_options)
        self.assertEqual(ring.replace_legacy_ring_masks(self.tree, self.arc), 0)

    def test_custom_calculation_is_preserved(self):
        self.old.nodes.new('ShaderNodeMath')
        with self.assertRaisesRegex(ValueError, 'customized'):
            ring.replace_legacy_ring_masks(self.tree, self.arc)
        self.assertEqual(self.n.node_tree, self.old)

    def test_custom_math_clamp_is_preserved(self):
        internal = next(node for node in self.old.nodes if node.bl_idname == 'ShaderNodeMath')
        internal.use_clamp = not internal.use_clamp
        before = ring._radial_graph_signature(self.old)
        with self.assertRaisesRegex(ValueError, 'customized'):
            ring.replace_legacy_ring_masks(self.tree, self.arc)
        self.assertEqual(self.n.node_tree, self.old)
        self.assertEqual(ring._radial_graph_signature(self.old), before)

    def test_custom_instancer_uv_source_is_preserved(self):
        internal = next(node for node in self.old.nodes if node.bl_idname == 'ShaderNodeTexCoord')
        internal.from_instancer = not internal.from_instancer
        before = ring._radial_graph_signature(self.old)
        with self.assertRaisesRegex(ValueError, 'customized'):
            ring.replace_legacy_ring_masks(self.tree, self.arc)
        self.assertEqual(self.n.node_tree, self.old)
        self.assertEqual(ring._radial_graph_signature(self.old), before)

    def test_repeated_external_socket_names_keep_exact_connected_input(self):
        other = self.tree.nodes.new('ShaderNodeValue')
        other.outputs[0].default_value = .63
        math = self.tree.nodes.new('ShaderNodeMath')
        math.operation = 'SUBTRACT'
        self.assertEqual(math.inputs[0].name, math.inputs[1].name)
        self.tree.links.new(other.outputs[0], math.inputs[0])
        self.tree.links.new(self.n.outputs['Mask'], math.inputs[1])
        first_id, second_id = math.inputs[0].identifier, math.inputs[1].identifier
        self.assertEqual(ring.replace_legacy_ring_masks(self.tree, self.arc), 1)
        self.assertEqual(math.inputs[0].links[0].from_node, other)
        self.assertEqual(math.inputs[1].links[0].from_node, self.n)
        self.assertEqual(math.inputs[0].identifier, first_id)
        self.assertEqual(math.inputs[1].identifier, second_id)
        self.assertEqual(self.n.inputs['Inner Radius'].links[0].from_node, self.source)

    def test_migration_failure_restores_original_and_repeated_external_input_link(self):
        other = self.tree.nodes.new('ShaderNodeValue')
        math = self.tree.nodes.new('ShaderNodeMath')
        math.operation = 'SUBTRACT'
        self.tree.links.new(other.outputs[0], math.inputs[0])
        self.tree.links.new(self.n.outputs['Mask'], math.inputs[1])
        second = self.tree.nodes.new('ShaderNodeGroup')
        second.node_tree = self.old
        second.inputs['Ring Width'].default_value = .07
        second_math = self.tree.nodes.new('ShaderNodeMath')
        self.tree.links.new(other.outputs[0], second_math.inputs[0])
        self.tree.links.new(second.outputs['Mask'], second_math.inputs[1])
        before = (self.n.name, self.n.label, self.n.inputs['Ring Width'].default_value,
                  second.name, second.inputs['Ring Width'].default_value)
        graph_before = ring._radial_graph_signature(self.old)
        # The first plan resolves its three external endpoints successfully.
        # Fail at the second plan's Math input, then permit rollback resolution.
        resolve = ring._socket
        lookups = 0

        def fail_second_plan_socket(sockets, identifier):
            nonlocal lookups
            lookups += 1
            if lookups == 4:
                raise RuntimeError('Injected migration failure')
            return resolve(sockets, identifier)

        with mock.patch.object(ring, '_socket', side_effect=fail_second_plan_socket):
            with self.assertRaisesRegex(RuntimeError, 'Injected migration failure'):
                ring.replace_legacy_ring_masks(self.tree, self.arc)
        self.assertEqual(self.n.node_tree, self.old)
        self.assertEqual(second.node_tree, self.old)
        self.assertEqual((self.n.name, self.n.label, self.n.inputs['Ring Width'].default_value,
                          second.name, second.inputs['Ring Width'].default_value), before)
        self.assertEqual(math.inputs[0].links[0].from_node, other)
        self.assertEqual(math.inputs[1].links[0].from_node, self.n)
        self.assertEqual(second_math.inputs[0].links[0].from_node, other)
        self.assertEqual(second_math.inputs[1].links[0].from_node, second)
        self.assertEqual(self.n.inputs['Inner Radius'].links[0].from_node, self.source)
        self.assertEqual(ring._radial_graph_signature(self.old), graph_before)

    def test_repeated_external_output_names_keep_exact_source_socket(self):
        group = bpy.data.node_groups.new('Repeated external outputs', 'ShaderNodeTree')
        for _ in range(2):
            group.interface.new_socket(name='Value', in_out='OUTPUT', socket_type='NodeSocketFloat')
        value = group.nodes.new('ShaderNodeValue')
        output = group.nodes.new('NodeGroupOutput')
        group.links.new(value.outputs[0], output.inputs[1])
        source = self.tree.nodes.new('ShaderNodeGroup')
        source.node_tree = group
        self.assertEqual(source.outputs[0].name, source.outputs[1].name)
        self.tree.links.new(source.outputs[1], self.n.inputs['Ring Width'])
        identifier = source.outputs[1].identifier
        self.assertEqual(ring.replace_legacy_ring_masks(self.tree, self.arc), 1)
        link = self.n.inputs['Ring Width'].links[0]
        self.assertEqual(link.from_node, source)
        self.assertEqual(link.from_socket.identifier, identifier)
        self.assertEqual(link.from_socket, source.outputs[1])

    def test_animated_material_is_preserved(self):
        self.n.inputs['Inner Radius'].keyframe_insert('default_value', frame=1)
        with self.assertRaisesRegex(ValueError, 'unanimated owner'):
            ring.replace_legacy_ring_masks(self.tree, self.arc)
        self.assertEqual(self.n.node_tree, self.old)

    def test_full_ring_shortcut_can_use_explicit_compatible_legacy_arc(self):
        g = self.tree.nodes.new('ShaderNodeGroup')
        g.node_tree = ring.new_group()
        with mock.patch.object(ring, 'find_mask_group', return_value=self.arc):
            n = ring.add_ring(g)
        self.assertEqual(n.node_tree, self.arc)
        self.assertEqual(n.inputs['Sweep Angle'].default_value, 360)
        self.assertEqual(g.inputs['Mask 1'].links[0].from_node, n)


if __name__ == '__main__':
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError('Use isolated factory background Blender only.')
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RetirementTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
