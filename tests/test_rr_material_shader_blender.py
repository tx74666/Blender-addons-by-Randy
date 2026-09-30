"""Material Surface extraction contracts in an isolated Blender factory scene.

Run serially with --background --factory-startup --disable-autoexec --threads 2
--python-exit-code 1 --python tests/test_rr_material_shader_blender.py.
No production .blend is opened, changed, or saved.
"""

import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy


ADDONS = Path(__file__).resolve().parents[1] / "addons"
sys.path.insert(0, str(ADDONS))
rr = importlib.import_module("random_realm_builder_exporter")
shader = importlib.import_module("random_realm_builder_exporter.rr_material_shader")
assert Path(rr.__file__).resolve().is_relative_to(ADDONS.resolve()), rr.__file__


def plain(value):
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    if isinstance(value, bpy.types.ID):
        return (value.bl_rna.identifier, value.as_pointer())
    try:
        return tuple(plain(item) for item in value)
    except TypeError:
        return str(value)


def properties(value):
    """Record RNA values, omitting read-only UI measurements and collections."""
    result = []
    for prop in value.bl_rna.properties:
        if prop.identifier == "rna_type" or prop.is_readonly or prop.type == "COLLECTION":
            continue
        if prop.type == "POINTER" and prop.identifier not in {"image", "object", "node_tree"}:
            continue
        result.append((prop.identifier, plain(getattr(value, prop.identifier))))
    return tuple(result)


def links_snapshot(tree):
    return tuple(sorted((link.from_node.name, link.from_socket.identifier,
                         link.to_node.name, link.to_socket.identifier)
                        for link in tree.links))


def graph_snapshot(tree):
    nodes = []
    for node in tree.nodes:
        extra = []
        if hasattr(node, "color_ramp"):
            ramp = node.color_ramp
            extra.append(("ramp", properties(ramp),
                          tuple((element.position, tuple(element.color)) for element in ramp.elements)))
        if hasattr(node, "mapping"):
            mapping = node.mapping
            extra.append(("mapping", properties(mapping),
                          tuple(tuple((point.location[:], point.handle_type) for point in curve.points)
                                for curve in mapping.curves)))
        if hasattr(node, "image_user"):
            extra.append(("image_user", properties(node.image_user)))
        nodes.append((node.name, node.bl_idname, properties(node),
                      node.parent.name if node.parent else None,
                      tuple(properties(socket) for socket in node.inputs),
                      tuple(properties(socket) for socket in node.outputs), tuple(extra),
                      tuple(sorted((key, plain(value)) for key, value in node.items()))))
    return tuple(nodes), links_snapshot(tree), tree.nodes.active.name if tree.nodes.active else None


def interface_outputs(tree):
    return tuple((item.name, item.socket_type, item.identifier)
                 for item in tree.interface.items_tree
                 if item.item_type == "SOCKET" and item.in_out == "OUTPUT")


def make_material(name, default_shader=True):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.clear()
    output = new_node(material.node_tree, "ShaderNodeOutputMaterial", "Active Output", (800, 0))
    output.is_active_output = True
    if default_shader:
        surface = new_node(material.node_tree, "ShaderNodeBsdfPrincipled", "Main Shader", (350, 0))
        material.node_tree.links.new(surface.outputs[0], output.inputs["Surface"])
    return material


def new_node(tree, kind, name, location=(0, 0)):
    node = tree.nodes.new(kind)
    node.name = name
    node.label = "Label " + name
    node.location = location
    return node


def connect(tree, source, output, target, socket):
    return tree.links.new(source.outputs[output], target.inputs[socket])


def make_complex_material(name):
    """A real two-node graph outside the lightweight texture + shader rule."""
    material = make_material(name)
    roughness = new_node(material.node_tree, "ShaderNodeMath", "Roughness Math", (0, 0))
    roughness.operation = "MULTIPLY"
    roughness.inputs[0].default_value = 0.35
    roughness.inputs[1].default_value = 0.8
    connect(material.node_tree, roughness, 0, material.node_tree.nodes["Main Shader"], "Roughness")
    return material


def make_nested_group(name="Shared Dirt"):
    group = bpy.data.node_groups.new(name, "ShaderNodeTree")
    group.interface.new_socket(name="Tint", in_out="INPUT", socket_type="NodeSocketColor")
    group.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    input_node = group.nodes.new("NodeGroupInput")
    output_node = group.nodes.new("NodeGroupOutput")
    bsdf = group.nodes.new("ShaderNodeBsdfDiffuse")
    connect(group, input_node, "Tint", bsdf, "Color")
    connect(group, bsdf, 0, output_node, "Shader")
    return group


class MaterialShaderTests(unittest.TestCase):
    def setUp(self):
        for material in list(bpy.data.materials):
            bpy.data.materials.remove(material, do_unlink=True)
        for group in list(bpy.data.node_groups):
            bpy.data.node_groups.remove(group, do_unlink=True)
        for image in list(bpy.data.images):
            if image.name.startswith("RR Import Test"):
                bpy.data.images.remove(image, do_unlink=True)

    def import_group(self, source, target=None, **kwargs):
        return shader.import_material_shader(source, target, **kwargs)

    def assert_vector_close(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for value, reference in zip(actual, expected):
            self.assertAlmostEqual(value, reference, places=6)

    def test_single_bsdf_imports_native_values_without_group_or_target_mutation(self):
        source, target = make_material("Plain Metal"), make_material("Hub_Base")
        original = source.node_tree.nodes["Main Shader"]
        original.inputs["Base Color"].default_value = (0.1, 0.3, 0.7, 1)
        original.inputs["Metallic"].default_value = 0.73
        original.inputs["Roughness"].default_value = 0.21
        original.inputs["IOR"].default_value = 1.67
        original["artist_note"] = "Keep these shader values"
        new_node(source.node_tree, "ShaderNodeTexNoise", "Unrelated Noise")
        tree = target.node_tree
        tree.nodes.active = tree.nodes["Main Shader"]
        tree.nodes["Main Shader"].select = True
        tree.nodes["Active Output"].select = False
        before, source_before = graph_snapshot(tree), graph_snapshot(source.node_tree)
        self.assertTrue(shader.is_simple_material_surface(source))
        group, inserted, status = self.import_group(source, target, location=(1300, 220))
        self.assertIsNone(group)
        self.assertEqual(status, "DIRECT")
        self.assertEqual(inserted.bl_idname, "ShaderNodeBsdfPrincipled")
        self.assertEqual(inserted.label, original.label)
        self.assertEqual(inserted["artist_note"], original["artist_note"])
        self.assert_vector_close(inserted.location, (1300, 220))
        self.assertEqual(tuple(properties(s) for s in inserted.inputs),
                         tuple(properties(s) for s in original.inputs))
        self.assertFalse(any(socket.is_linked for socket in inserted.outputs))
        self.assertEqual(links_snapshot(tree), before[1])
        self.assertEqual(len(tree.nodes), len(before[0]) + 1)
        self.assertEqual(len(bpy.data.node_groups), 0)
        tree.nodes.remove(inserted)
        self.assertEqual(graph_snapshot(tree), before)
        self.assertEqual(graph_snapshot(source.node_tree), source_before)

    def test_image_and_wave_plus_bsdf_copy_link_settings_and_shared_image(self):
        for kind in ("ShaderNodeTexImage", "ShaderNodeTexWave"):
            with self.subTest(kind=kind):
                source, target = make_material(kind), make_material("Hub_Base")
                tree = source.node_tree
                texture = new_node(tree, kind, "Pattern", (-200, 40))
                bsdf = tree.nodes["Main Shader"]
                bsdf.location = (250, -70)
                image = None
                if kind == "ShaderNodeTexImage":
                    image = bpy.data.images.new("RR Import Test shared", 4, 4)
                    texture.image = image
                    texture.interpolation = "Closest"
                    texture.extension = "EXTEND"
                    texture.projection = "BOX"
                    texture.projection_blend = 0.24
                else:
                    texture.wave_type = "RINGS"
                    texture.rings_direction = "X"
                    texture.wave_profile = "SAW"
                    texture.inputs["Scale"].default_value = 7.5
                    texture.inputs["Distortion"].default_value = 3.7
                    texture.texture_mapping.translation = (1.2, -0.2, 0.4)
                connect(tree, texture, "Color", bsdf, "Base Color")
                source_before, target_before = graph_snapshot(tree), graph_snapshot(target.node_tree)
                old_nodes = set(target.node_tree.nodes)
                image_ids = {item.as_pointer() for item in bpy.data.images}
                self.assertTrue(shader.is_simple_material_surface(source))
                group, inserted, status = self.import_group(source, target)
                copies = [node for node in target.node_tree.nodes if node not in old_nodes]
                self.assertEqual((group, status, len(copies)), (None, "DIRECT", 2))
                copied_texture = next(node for node in copies if node.bl_idname == kind)
                self.assertEqual(inserted.bl_idname, bsdf.bl_idname)
                self.assertEqual(inserted.inputs["Base Color"].links[0].from_node, copied_texture)
                self.assert_vector_close(inserted.location - copied_texture.location,
                                         bsdf.location - texture.location)
                self.assertFalse(any(socket.is_linked for socket in inserted.outputs))
                if image is not None:
                    self.assertEqual(copied_texture.image, image)
                    for name in ("interpolation", "extension", "projection", "projection_blend"):
                        self.assertEqual(getattr(copied_texture, name), getattr(texture, name))
                else:
                    for name in ("wave_type", "rings_direction", "wave_profile"):
                        self.assertEqual(getattr(copied_texture, name), getattr(texture, name))
                    self.assertEqual(properties(copied_texture.texture_mapping), properties(texture.texture_mapping))
                self.assertEqual(tuple(properties(socket) for socket in copied_texture.inputs),
                                 tuple(properties(socket) for socket in texture.inputs))
                self.assertEqual({item.as_pointer() for item in bpy.data.images}, image_ids)
                self.assertEqual(len(bpy.data.node_groups), 0)
                for node in copies:
                    target.node_tree.nodes.remove(node)
                self.assertEqual(graph_snapshot(target.node_tree), target_before)
                self.assertEqual(graph_snapshot(tree), source_before)

    def test_single_texture_surface_copies_natively_when_blender_accepts_link(self):
        source, target = make_material("Texture Only", default_shader=False), make_material("Hub_Base")
        texture = new_node(source.node_tree, "ShaderNodeTexWave", "Only Wave")
        connect(source.node_tree, texture, "Color", source.node_tree.nodes["Active Output"], "Surface")
        error = shader.validate_material_surface(source)
        if error:
            self.assertFalse(shader.is_simple_material_surface(source))
            self.skipTest("This Blender version rejects Texture Color -> Material Surface: " + error)
        before = graph_snapshot(source.node_tree)
        group, inserted, status = self.import_group(source, target)
        self.assertEqual((group, status), (None, "DIRECT"))
        self.assertEqual(inserted.bl_idname, "ShaderNodeTexWave")
        self.assertFalse(any(socket.is_linked for socket in inserted.outputs))
        self.assertEqual(len(bpy.data.node_groups), 0)
        self.assertEqual(graph_snapshot(source.node_tree), before)

    def test_frames_and_reroutes_preserve_simple_graph_layout(self):
        source, target = make_material("Framed Wave"), make_material("Hub_Base")
        tree = source.node_tree
        outer = new_node(tree, "NodeFrame", "Outer", (-300, 250))
        inner = new_node(tree, "NodeFrame", "Inner", (60, -50))
        inner.parent = outer
        texture = new_node(tree, "ShaderNodeTexWave", "Pattern", (-100, 0))
        texture.parent = inner
        bsdf = tree.nodes["Main Shader"]
        bsdf.parent = inner
        bsdf.location = (200, -40)
        first = new_node(tree, "NodeReroute", "Input Reroute", (80, 0))
        first.parent = inner
        last = new_node(tree, "NodeReroute", "Surface Reroute", (500, -40))
        last.parent = outer
        connect(tree, texture, "Color", first, 0)
        connect(tree, first, 0, bsdf, "Base Color")
        connect(tree, bsdf, 0, last, 0)
        connect(tree, last, 0, tree.nodes["Active Output"], "Surface")
        unrelated = new_node(tree, "ShaderNodeTexNoise", "Unrelated In Frame")
        unrelated.parent = inner
        old_nodes, before = set(target.node_tree.nodes), graph_snapshot(tree)
        self.assertTrue(shader.is_simple_material_surface(source))
        group, inserted, status = self.import_group(source, target)
        self.assertEqual((group, status), (None, "DIRECT"))
        self.assertEqual(inserted.bl_idname, "NodeReroute")
        copies = {node.label: node for node in target.node_tree.nodes if node not in old_nodes}
        dependencies, _ = shader.surface_dependency_nodes(source)
        self.assertEqual(set(copies), {node.label for node in dependencies})
        for original in dependencies:
            copied = copies[original.label]
            if original.parent is not None:
                self.assertEqual(copied.parent, copies[original.parent.label])
                self.assert_vector_close(copied.location, original.location)
        self.assertEqual(graph_snapshot(tree), before)
        self.assertEqual(len(bpy.data.node_groups), 0)

    def test_complex_and_nested_graphs_still_generate_shared_shader_groups(self):
        for kind in ("MATH", "TWO_TEXTURES", "NESTED"):
            with self.subTest(kind=kind):
                source, target = make_material(kind), make_material("Hub_Base")
                tree = source.node_tree
                bsdf = tree.nodes["Main Shader"]
                nested = None
                if kind == "MATH":
                    value = new_node(tree, "ShaderNodeMath", "Roughness")
                    connect(tree, value, 0, bsdf, "Roughness")
                elif kind == "TWO_TEXTURES":
                    color = new_node(tree, "ShaderNodeTexWave", "Color Pattern")
                    rough = new_node(tree, "ShaderNodeTexNoise", "Rough Pattern")
                    connect(tree, color, "Color", bsdf, "Base Color")
                    connect(tree, rough, "Fac", bsdf, "Roughness")
                else:
                    nested = make_nested_group()
                    value = new_node(tree, "ShaderNodeGroup", "Shared Nested")
                    value.node_tree = nested
                    connect(tree, value, "Shader", tree.nodes["Active Output"], "Surface")
                before = graph_snapshot(tree)
                self.assertFalse(shader.is_simple_material_surface(source))
                group, inserted, status = self.import_group(source, target)
                self.assertEqual(status, "CREATED")
                self.assertEqual(inserted.node_tree, group)
                self.assertEqual(group.color_tag, "SHADER")
                self.assertFalse(inserted.outputs["Shader"].is_linked)
                if nested is not None:
                    self.assertEqual(group.nodes["Shared Nested"].node_tree, nested)
                self.assertEqual(graph_snapshot(tree), before)

    def test_simple_graph_bypasses_old_group_without_changing_group_or_users(self):
        source, target, existing_user = [make_material(name) for name in ("Old Gold", "Hub_Base", "User")]
        group, _, _ = self.import_group(source)
        group.color_tag = "COLOR"
        instance = existing_user.node_tree.nodes.new("ShaderNodeGroup")
        instance.node_tree = group
        connect(existing_user.node_tree, instance, "Shader", existing_user.node_tree.nodes["Active Output"], "Surface")
        identity, group_before = group.as_pointer(), graph_snapshot(group)
        interface, user_before = interface_outputs(group), graph_snapshot(existing_user.node_tree)
        source.node_tree.nodes["Main Shader"].inputs["Metallic"].default_value = 0.87
        source_before = graph_snapshot(source.node_tree)
        result, inserted, status = self.import_group(source, target)
        self.assertEqual((result, status), (None, "DIRECT"))
        self.assertAlmostEqual(inserted.inputs["Metallic"].default_value, 0.87, places=6)
        self.assertEqual(group.as_pointer(), identity)
        self.assertEqual(graph_snapshot(group), group_before)
        self.assertEqual(interface_outputs(group), interface)
        self.assertEqual(group.color_tag, "COLOR")
        self.assertEqual(graph_snapshot(existing_user.node_tree), user_before)
        self.assertEqual(instance.node_tree, group)
        self.assertEqual(graph_snapshot(source.node_tree), source_before)
        self.assertEqual(list(bpy.data.node_groups), [group])

    def test_each_direct_import_reads_current_source_values(self):
        source, target = make_material("Changing Gold"), make_material("Hub_Base")
        original = source.node_tree.nodes["Main Shader"]
        original.inputs["Metallic"].default_value = 0.12
        _, first, _ = self.import_group(source, target)
        original.inputs["Metallic"].default_value = 0.91
        _, second, _ = self.import_group(source, target)
        self.assertAlmostEqual(first.inputs["Metallic"].default_value, 0.12, places=6)
        self.assertAlmostEqual(second.inputs["Metallic"].default_value, 0.91, places=6)
        self.assertNotEqual(first, second)
        self.assertEqual(len({node.name for node in target.node_tree.nodes}), len(target.node_tree.nodes))
        self.assertEqual(target.node_tree.nodes["Main Shader"].inputs["Metallic"].default_value, 0)
        self.assertEqual(len(bpy.data.node_groups), 0)

    def test_valid_simple_source_ignores_invalid_old_group_without_repairing_it(self):
        source, target = make_material("Outdated Group"), make_material("Hub_Base")
        group = bpy.data.node_groups.new("Material Outdated Group", "ShaderNodeTree")
        group.color_tag = "COLOR"
        group.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
        value, output = group.nodes.new("ShaderNodeRGB"), group.nodes.new("NodeGroupOutput")
        connect(group, value, 0, output, "Color")
        before, interface = graph_snapshot(group), interface_outputs(group)
        self.assertTrue(shader.validate_reusable_shader_group(group))
        result, inserted, status = self.import_group(source, target)
        self.assertEqual((result, status), (None, "DIRECT"))
        self.assertEqual(inserted.bl_idname, "ShaderNodeBsdfPrincipled")
        self.assertEqual(graph_snapshot(group), before)
        self.assertEqual(interface_outputs(group), interface)
        self.assertEqual(group.color_tag, "COLOR")
        self.assertEqual(list(bpy.data.node_groups), [group])

    def test_simple_explicit_refresh_and_standalone_generation_keep_group_contract(self):
        source, target = make_material("Standalone Gold"), make_material("Hub_Base")
        group, inserted, status = self.import_group(source)
        self.assertEqual(status, "CREATED")
        self.assertIsNone(inserted)
        source.node_tree.nodes["Main Shader"].inputs["Metallic"].default_value = 0.77
        refreshed, inserted, status = self.import_group(source, target, refresh=True)
        self.assertEqual((refreshed, status), (group, "REFRESHED"))
        self.assertEqual(inserted.node_tree, group)
        self.assertAlmostEqual(group.nodes["Main Shader"].inputs["Metallic"].default_value, 0.77, places=6)
        self.assertEqual(list(bpy.data.node_groups), [group])

    def test_simple_invalid_inputs_and_self_import_leave_data_unchanged(self):
        source = make_material("Self")
        before = graph_snapshot(source.node_tree)
        self.assertFalse(shader.is_simple_material_surface(None))
        invalid = make_material("No Surface", default_shader=False)
        self.assertFalse(shader.is_simple_material_surface(invalid))
        for target in (source, SimpleNamespace()):
            with self.subTest(target=type(target).__name__):
                with self.assertRaises(ValueError):
                    self.import_group(source, target)
                self.assertEqual(graph_snapshot(source.node_tree), before)
                self.assertEqual(len(bpy.data.node_groups), 0)

    def test_direct_copy_failures_restore_existing_target_graph_and_selection(self):
        source, target = make_material("Interrupted Wave"), make_material("Hub_Base")
        texture = new_node(source.node_tree, "ShaderNodeTexWave", "Pattern")
        connect(source.node_tree, texture, "Color", source.node_tree.nodes["Main Shader"], "Base Color")
        tree = target.node_tree
        tree.nodes.active = tree.nodes["Main Shader"]
        tree.nodes["Main Shader"].select = True
        tree.nodes["Active Output"].select = False
        before, source_before = graph_snapshot(tree), graph_snapshot(source.node_tree)
        identities = {node.as_pointer() for node in tree.nodes}
        for helper in ("_copy_rna_settings", "_matching_socket", "_copy_socket_values", "_insertion_location"):
            with self.subTest(helper=helper):
                calls = []

                def fail_once(*args, **kwargs):
                    calls.append(True)
                    # At socket-copy time, the texture -> BSDF link must already
                    # exist. This exercises rollback after a partial graph write.
                    if helper == "_copy_socket_values":
                        self.assertGreater(len(tree.links), len(before[1]))
                    raise RuntimeError("Injected direct-copy failure")

                with mock.patch.object(shader, helper, side_effect=fail_once):
                    with self.assertRaisesRegex(RuntimeError, "Injected direct-copy failure"):
                        self.import_group(source, target)
                self.assertTrue(calls)
                self.assertEqual(graph_snapshot(tree), before)
                self.assertEqual({node.as_pointer() for node in tree.nodes}, identities)
                self.assertEqual(graph_snapshot(source.node_tree), source_before)
                self.assertEqual(len(bpy.data.node_groups), 0)

    def test_active_output_surface_only_excludes_unused_volume_and_displacement(self):
        source = make_material("Blue Glass")
        tree = source.node_tree
        first_output = tree.nodes["Active Output"]
        first_output.name = "Inactive First Output"
        second = new_node(tree, "ShaderNodeOutputMaterial", "Actual Active Output")
        active_shader = new_node(tree, "ShaderNodeBsdfGlass", "Required Glass")
        connect(tree, active_shader, 0, second, "Surface")
        second.is_active_output = True
        volume = new_node(tree, "ShaderNodeVolumePrincipled", "Ignored Volume")
        displacement = new_node(tree, "ShaderNodeDisplacement", "Ignored Displacement")
        connect(tree, volume, 0, second, "Volume")
        connect(tree, displacement, 0, second, "Displacement")
        new_node(tree, "ShaderNodeTexNoise", "Unused Test Noise")
        before = graph_snapshot(tree)
        group, inserted, status = self.import_group(source)
        self.assertEqual(status, "CREATED")
        self.assertIsNone(inserted)
        self.assertEqual(group.name, "Material Blue Glass")
        self.assertEqual(group.bl_idname, "ShaderNodeTree")
        self.assertEqual(group.color_tag, "SHADER")
        self.assertEqual({node.name for node in group.nodes if node.bl_idname != "NodeGroupOutput"},
                         {"Required Glass"})
        self.assertFalse(any(node.bl_idname == "ShaderNodeOutputMaterial" for node in group.nodes))
        outputs = interface_outputs(group)
        self.assertEqual([(name, kind) for name, kind, _ in outputs], [("Shader", "NodeSocketShader")])
        output = next(node for node in group.nodes if node.bl_idname == "NodeGroupOutput")
        self.assertEqual(output.inputs["Shader"].links[0].from_node.name, "Required Glass")
        self.assertEqual(before, graph_snapshot(tree))

    def test_explicit_active_output_wins_across_render_targets_without_changing_flags(self):
        source = make_material("Render Target Outputs", default_shader=False)
        tree = source.node_tree
        tree.nodes.clear()
        outputs = []
        for target in ("ALL", "EEVEE", "CYCLES"):
            output = new_node(tree, "ShaderNodeOutputMaterial", target + " Output")
            output.target = target
            output.is_active_output = True
            surface = new_node(tree, "ShaderNodeEmission", target + " Surface")
            connect(tree, surface, 0, output, "Surface")
            outputs.append(output)
        # Blender 5.2's setter clears the other active flags, even when outputs
        # have different render targets. Respect the user's explicit active
        # output instead of asking get_output_node() to normalize those flags.
        # Substitute only the render-engine context; all nodes, flags, copying,
        # and group identities remain real Blender datablocks. No render runs.
        for active_output in outputs:
            active_output.is_active_output = True
            self.assertEqual([output for output in outputs if output.is_active_output], [active_output])
            before = graph_snapshot(tree)
            for engine in ("CYCLES", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"):
                with self.subTest(active=active_output.target, engine=engine):
                    proxy = SimpleNamespace(context=SimpleNamespace(scene=SimpleNamespace(render=SimpleNamespace(engine=engine))),
                                            data=bpy.data, types=bpy.types)
                    with mock.patch.object(shader, "bpy", proxy):
                        group, _, _ = self.import_group(source, refresh=True)
                    self.assertEqual({node.name for node in group.nodes if node.bl_idname != "NodeGroupOutput"},
                                     {active_output.target + " Surface"})
                    self.assertEqual(before, graph_snapshot(tree))
        self.assertEqual(len(bpy.data.node_groups), 1)

    def comprehensive_material(self):
        source = make_material("Floor Metal")
        tree = source.node_tree
        bsdf, output = tree.nodes["Main Shader"], tree.nodes["Active Output"]
        nodes = {"Main Shader": bsdf}
        specifications = (
            ("ShaderNodeTexCoord", "Coordinates"), ("ShaderNodeMapping", "Mapping"),
            ("ShaderNodeVectorMath", "Vector Math"), ("ShaderNodeTexNoise", "Noise"),
            ("ShaderNodeValToRGB", "Ramp"), ("ShaderNodeTexImage", "Image"),
            ("ShaderNodeUVMap", "UV"), ("ShaderNodeNormalMap", "Normal Map"),
            ("ShaderNodeBump", "Bump"), ("ShaderNodeNewGeometry", "Geometry"),
            ("ShaderNodeAttribute", "Attribute"), ("ShaderNodeObjectInfo", "Object Info"),
            ("ShaderNodeMath", "Math"), ("ShaderNodeMixShader", "Mix"),
            ("ShaderNodeGroup", "Shared Group"), ("ShaderNodeAddShader", "Add"),
            ("ShaderNodeEmission", "Emission"),
        )
        for index, (kind, name) in enumerate(specifications):
            nodes[name] = new_node(tree, kind, name, (-900 + index * 30, 400 - index * 70))
        nodes["Shared Group"].node_tree = make_nested_group()
        nodes["Shared Group"].inputs["Tint"].default_value = (0.2, 0.3, 0.8, 1)
        nodes["Vector Math"].operation = "MULTIPLY"
        nodes["Vector Math"].inputs[1].default_value = (3, 5, 7)
        nodes["Mapping"].vector_type = "TEXTURE"
        nodes["Mapping"].inputs["Location"].default_value = (1.3, -2.4, 0.7)
        nodes["Mapping"].inputs["Rotation"].default_value = (0.3, 0.5, 0.7)
        nodes["Mapping"].inputs["Scale"].default_value = (2, 4, 6)
        nodes["Noise"].noise_dimensions = "4D"
        nodes["Noise"].inputs["W"].default_value = 2.7
        nodes["Noise"].inputs["Scale"].default_value = 8.5
        nodes["Math"].operation = "MULTIPLY_ADD"
        nodes["Math"].use_clamp = True
        nodes["Math"].inputs[2].default_value = 0.27
        nodes["Attribute"].attribute_name = "painted_roughness"
        nodes["UV"].uv_map = "ImportedUV"
        nodes["Normal Map"].space = "OBJECT"
        nodes["Normal Map"].uv_map = "ImportedUV"
        nodes["Normal Map"].inputs["Strength"].default_value = 0.65
        nodes["Bump"].invert = True
        nodes["Bump"].inputs["Distance"].default_value = 0.19
        nodes["Bump"].inputs["Strength"].default_value = 0.37
        nodes["Mix"].inputs[0].default_value = 0.29
        bsdf.inputs["Metallic"].default_value = 0.82
        bsdf.inputs["IOR"].default_value = 1.73
        nodes["Emission"].inputs["Color"].default_value = (0.7, 0.2, 0.1, 1)
        nodes["Emission"].inputs["Strength"].default_value = 3.2
        image = bpy.data.images.new("RR Import Test Shared", width=8, height=8)
        image_node = nodes["Image"]
        image_node.image = image
        image_node.interpolation = "Closest"
        image_node.projection = "BOX"
        image_node.projection_blend = 0.31
        image_node.extension = "CLIP"
        image_node.image_user.frame_start = 3
        image_node.image_user.frame_offset = 7
        image_node.image_user.use_cyclic = True
        ramp = nodes["Ramp"].color_ramp
        ramp.color_mode = "HSV"
        ramp.hue_interpolation = "CW"
        ramp.interpolation = "CONSTANT"
        ramp.elements[0].position = 0.07
        ramp.elements[0].color = (0.1, 0.3, 0.7, 0.9)
        ramp.elements[1].position = 0.93
        ramp.elements[1].color = (0.8, 0.2, 0.3, 1)
        ramp.elements.new(0.28).color = (0.3, 0.6, 0.1, 0.5)
        ramp.elements.new(0.65).color = (0.9, 0.5, 0.2, 1)
        for a, socket_a, b, socket_b in (
            ("Coordinates", "Generated", "Vector Math", 0),
            ("Vector Math", "Vector", "Mapping", "Vector"),
            ("Mapping", "Vector", "Noise", "Vector"),
            ("Noise", "Fac", "Ramp", "Fac"),
            ("Ramp", "Color", "Main Shader", "Base Color"),
            ("UV", "UV", "Image", "Vector"),
            ("Image", "Color", "Normal Map", "Color"),
            ("Normal Map", "Normal", "Bump", "Normal"),
            ("Geometry", "Position", "Bump", "Height"),
            ("Bump", "Normal", "Main Shader", "Normal"),
            ("Attribute", "Fac", "Math", 0),
            ("Object Info", "Random", "Math", 1),
            ("Math", 0, "Main Shader", "Roughness"),
            ("Main Shader", 0, "Mix", 1),
            ("Shared Group", "Shader", "Mix", 2),
            ("Mix", 0, "Add", 0),
            ("Emission", 0, "Add", 1),
        ):
            connect(tree, nodes[a], socket_a, nodes[b], socket_b)
        connect(tree, nodes["Add"], 0, output, "Surface")
        nodes["Noise"]["artist_note"] = "Do not lose this value"
        return source, nodes

    def test_complete_dependencies_properties_defaults_links_and_shared_ids(self):
        source, originals = self.comprehensive_material()
        before = graph_snapshot(source.node_tree)
        images_before = {image.as_pointer() for image in bpy.data.images}
        groups_before = {group.as_pointer() for group in bpy.data.node_groups}
        group, _, _ = self.import_group(source)
        copied = {node.name: node for node in group.nodes if node.bl_idname != "NodeGroupOutput"}
        self.assertEqual(set(copied), set(originals))
        self.assertEqual(images_before, {image.as_pointer() for image in bpy.data.images})
        self.assertEqual(groups_before | {group.as_pointer()},
                         {item.as_pointer() for item in bpy.data.node_groups})
        self.assertEqual(copied["Image"].image, originals["Image"].image)
        self.assertEqual(copied["Shared Group"].node_tree, originals["Shared Group"].node_tree)
        for name, original in originals.items():
            clone = copied[name]
            self.assertEqual(clone.bl_idname, original.bl_idname, name)
            self.assertEqual(clone.label, original.label, name)
            self.assert_vector_close(clone.location, original.location)
            for socket, cloned_socket in zip(original.inputs, clone.inputs):
                self.assertEqual(socket.identifier, cloned_socket.identifier, name)
                if hasattr(socket, "default_value"):
                    self.assertEqual(plain(socket.default_value), plain(cloned_socket.default_value),
                                     (name, socket.name))
        for name, fields in {
            "Vector Math": ("operation",), "Math": ("operation", "use_clamp"),
            "Mapping": ("vector_type",), "Noise": ("noise_dimensions",),
            "Attribute": ("attribute_name", "attribute_type"), "UV": ("uv_map",),
            "Normal Map": ("space", "uv_map"), "Bump": ("invert",),
            "Image": ("interpolation", "projection", "projection_blend", "extension"),
        }.items():
            for field in fields:
                self.assertEqual(getattr(copied[name], field), getattr(originals[name], field), (name, field))
        self.assertEqual(properties(copied["Image"].image_user), properties(originals["Image"].image_user))
        self.assertEqual(properties(copied["Ramp"].color_ramp), properties(originals["Ramp"].color_ramp))
        self.assertEqual([(element.position, tuple(element.color)) for element in copied["Ramp"].color_ramp.elements],
                         [(element.position, tuple(element.color)) for element in originals["Ramp"].color_ramp.elements])
        self.assertEqual(copied["Noise"]["artist_note"], "Do not lose this value")
        expected_links = [link for link in links_snapshot(source.node_tree) if link[2] != "Active Output"]
        self.assertEqual(expected_links, [link for link in links_snapshot(group)
                                          if group.nodes[link[2]].bl_idname != "NodeGroupOutput"])
        self.assertEqual(before, graph_snapshot(source.node_tree))

    def test_import_inserts_unconnected_node_preserving_target_graph(self):
        source = make_complex_material("Blue Glass")
        target = make_material("Hub_Base")
        tree = target.node_tree
        before = graph_snapshot(tree)
        source_before = graph_snapshot(source.node_tree)
        originals = {node.as_pointer() for node in tree.nodes}
        group, inserted, status = self.import_group(source, target, location=(1200, 250))
        self.assertEqual(status, "CREATED")
        self.assertEqual(inserted.node_tree, group)
        self.assertEqual(inserted.bl_idname, "ShaderNodeGroup")
        self.assertFalse(inserted.use_custom_color)
        self.assert_vector_close(inserted.location, (1200, 250))
        self.assertEqual(links_snapshot(tree), before[1])
        self.assertEqual(len(tree.nodes), len(originals) + 1)
        self.assertFalse(any(socket.is_linked for socket in inserted.inputs))
        self.assertFalse(any(socket.is_linked for socket in inserted.outputs))
        tree.nodes.remove(inserted)
        # Insertion may select the new node, but may not mutate old nodes/links.
        after = graph_snapshot(tree)
        strip_selection = lambda nodes: tuple((n[0], n[1], tuple(p for p in n[2] if p[0] != "select"), *n[3:])
                                             for n in nodes)
        self.assertEqual(strip_selection(before[0]), strip_selection(after[0]))
        self.assertEqual(source_before, graph_snapshot(source.node_tree))

    def test_existing_group_reused_without_uncontrolled_duplicates(self):
        source = make_complex_material("Clear Glass")
        target = make_material("Hub_Base")
        first, _, _ = self.import_group(source, target)
        first.color_tag = "NONE"
        snapshot = graph_snapshot(first)
        for _ in range(3):
            group, inserted, status = self.import_group(source, target)
            self.assertEqual(status, "REUSED")
            self.assertEqual(group, first)
            self.assertEqual(inserted.node_tree, first)
            self.assertEqual(group.color_tag, "SHADER")
            self.assertFalse(inserted.use_custom_color)
        self.assertEqual(shader.generated_shader_group(source), first)
        self.assertEqual([group.name for group in bpy.data.node_groups], ["Material Clear Glass"])
        self.assertEqual(snapshot, graph_snapshot(first))

    def test_use_existing_survives_source_surface_disconnection_but_refresh_refuses(self):
        source, target = make_material("Reusable Glass"), make_material("Hub_Base")
        group, _, _ = self.import_group(source)
        group_before = graph_snapshot(group)
        source.node_tree.links.clear()
        self.assertTrue(shader.validate_material_surface(source))
        reused, inserted, status = self.import_group(source, target)
        self.assertEqual(status, "REUSED")
        self.assertEqual(reused, group)
        self.assertEqual(inserted.node_tree, group)
        self.assertEqual(group_before, graph_snapshot(group))
        with self.assertRaises((RuntimeError, ValueError)):
            shader.refresh_material_shader_group(source, group)
        self.assertEqual(group_before, graph_snapshot(group))
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_same_name_empty_or_color_group_cannot_be_inserted_as_surface_shader(self):
        source, target = make_complex_material("Wrong Existing Group"), make_material("Hub_Base")
        target_before = graph_snapshot(target.node_tree)
        for kind in ("EMPTY", "COLOR"):
            with self.subTest(kind=kind):
                group = bpy.data.node_groups.new("Material Wrong Existing Group", "ShaderNodeTree")
                group.color_tag = "COLOR"
                if kind == "COLOR":
                    group.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
                    rgb = group.nodes.new("ShaderNodeRGB")
                    output = group.nodes.new("NodeGroupOutput")
                    connect(group, rgb, 0, output, "Color")
                group_before, interface_before = graph_snapshot(group), interface_outputs(group)
                self.assertTrue(shader.validate_reusable_shader_group(group))
                with self.assertRaises((RuntimeError, ValueError)):
                    self.import_group(source, target)
                self.assertEqual(target_before, graph_snapshot(target.node_tree))
                self.assertEqual(group_before, graph_snapshot(group))
                self.assertEqual(group.color_tag, "COLOR")
                self.assertEqual(len(bpy.data.node_groups), 1)
                with self.assertRaises((RuntimeError, ValueError)):
                    shader.refresh_material_shader_group(source, group)
                self.assertEqual(group_before, graph_snapshot(group))
                self.assertEqual(interface_before, interface_outputs(group))
                self.assertEqual(group.color_tag, "COLOR")
                bpy.data.node_groups.remove(group)

    def test_refresh_repairs_unlinked_shader_output_with_same_datablock_and_interface(self):
        source = make_material("Repair Existing Shader")
        group, _, _ = self.import_group(source)
        identity, interface = group.as_pointer(), interface_outputs(group)
        group.links.clear()
        self.assertTrue(shader.validate_reusable_shader_group(group))
        repaired = shader.refresh_material_shader_group(source, group)
        self.assertEqual(repaired.as_pointer(), identity)
        self.assertEqual(interface_outputs(repaired), interface)
        self.assertEqual(shader.validate_reusable_shader_group(repaired), "")
        output = next(node for node in repaired.nodes if node.bl_idname == "NodeGroupOutput")
        self.assertTrue(output.inputs["Shader"].is_linked)
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_self_import_generates_or_refreshes_group_without_inserting_into_source(self):
        source = make_complex_material("Blue Glass")
        before = graph_snapshot(source.node_tree)
        group, inserted, status = self.import_group(source, source)
        self.assertEqual(status, "CREATED")
        self.assertIsNone(inserted)
        self.assertEqual(before, graph_snapshot(source.node_tree))
        refreshed, inserted, status = self.import_group(source, source, refresh=True)
        self.assertEqual(status, "REFRESHED")
        self.assertEqual(group, refreshed)
        self.assertIsNone(inserted)
        self.assertEqual(before, graph_snapshot(source.node_tree))

    def test_refresh_preserves_datablock_interface_and_existing_external_links(self):
        source = make_complex_material("Floor Metal")
        target = make_material("Hub_Base")
        group, node, _ = self.import_group(source, target)
        connect(target.node_tree, node, "Shader", target.node_tree.nodes["Active Output"], "Surface")
        other = make_material("Other Target")
        _, other_node, _ = self.import_group(source, other)
        connect(other.node_tree, other_node, "Shader", other.node_tree.nodes["Active Output"], "Surface")
        identity = group.as_pointer()
        group.color_tag = "NONE"
        interface = interface_outputs(group)
        target_links, other_links = links_snapshot(target.node_tree), links_snapshot(other.node_tree)
        source.node_tree.nodes["Main Shader"].inputs["Metallic"].default_value = 0.91
        extra = new_node(source.node_tree, "ShaderNodeMath", "New Roughness")
        extra.operation = "MULTIPLY"
        extra.inputs[0].default_value = 0.4
        extra.inputs[1].default_value = 0.7
        connect(source.node_tree, extra, 0, source.node_tree.nodes["Main Shader"], "Roughness")
        before = graph_snapshot(source.node_tree)
        refreshed = shader.refresh_material_shader_group(source, group)
        self.assertEqual(refreshed.as_pointer(), identity)
        self.assertEqual(refreshed.color_tag, "SHADER")
        self.assertEqual(interface_outputs(refreshed), interface)
        self.assertEqual(node.node_tree, refreshed)
        self.assertEqual(other_node.node_tree, refreshed)
        self.assertEqual(target_links, links_snapshot(target.node_tree))
        self.assertEqual(other_links, links_snapshot(other.node_tree))
        self.assertIn("New Roughness", refreshed.nodes)
        self.assertAlmostEqual(refreshed.nodes["Main Shader"].inputs["Metallic"].default_value, 0.91, places=6)
        self.assertEqual(before, graph_snapshot(source.node_tree))
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_invalid_source_validation_and_import_leave_no_generated_groups(self):
        self.assertTrue(shader.validate_material_surface(None))
        source = bpy.data.materials.new("No Nodes")
        source.use_nodes = False
        if not source.use_nodes:
            self.assertTrue(shader.validate_material_surface(source))
        else:
            # Blender 5.2 always enables material nodes; keep the older-file
            # guard covered without assuming this deprecated flag can be unset.
            self.assertTrue(shader.validate_material_surface(SimpleNamespace(use_nodes=False, node_tree=None)))
        source.use_nodes = True
        source.node_tree.nodes.clear()
        self.assertTrue(shader.validate_material_surface(source))
        output = new_node(source.node_tree, "ShaderNodeOutputMaterial", "Output")
        output.is_active_output = True
        self.assertIn("Surface", shader.validate_material_surface(source))
        before = graph_snapshot(source.node_tree)
        with self.assertRaises((RuntimeError, ValueError)):
            self.import_group(source)
        self.assertEqual(before, graph_snapshot(source.node_tree))
        self.assertEqual(len(bpy.data.node_groups), 0)

    def test_invalid_refresh_preserves_existing_group_contents_and_references(self):
        source = make_complex_material("Floor Metal")
        target = make_material("Hub_Base")
        group, node, _ = self.import_group(source, target)
        group.color_tag = "NONE"
        before = graph_snapshot(group)
        interface = interface_outputs(group)
        source.node_tree.links.clear()
        with self.assertRaises((RuntimeError, ValueError)):
            shader.refresh_material_shader_group(source, group)
        self.assertEqual(before, graph_snapshot(group))
        self.assertEqual(interface, interface_outputs(group))
        self.assertEqual(node.node_tree, group)
        self.assertEqual(group.color_tag, "NONE")
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_recursive_dependency_on_generated_group_is_rejected_before_refresh(self):
        source = make_material("Recursive Source")
        group, _, _ = self.import_group(source)
        direct = new_node(source.node_tree, "ShaderNodeGroup", "Recursion")
        direct.node_tree = group
        connect(source.node_tree, direct, "Shader", source.node_tree.nodes["Active Output"], "Surface")
        before = graph_snapshot(group)
        source_before = graph_snapshot(source.node_tree)
        with self.assertRaises((RuntimeError, ValueError)):
            shader.refresh_material_shader_group(source, group)
        self.assertEqual(before, graph_snapshot(group))
        self.assertEqual(source_before, graph_snapshot(source.node_tree))
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_indirect_recursive_group_dependency_is_also_rejected(self):
        source = make_material("Nested Recursive Source")
        group, _, _ = self.import_group(source)
        wrapper = bpy.data.node_groups.new("Nested Wrapper", "ShaderNodeTree")
        wrapper.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
        inner = wrapper.nodes.new("ShaderNodeGroup")
        inner.node_tree = group
        wrapper_output = wrapper.nodes.new("NodeGroupOutput")
        connect(wrapper, inner, "Shader", wrapper_output, "Shader")
        source_wrapper = new_node(source.node_tree, "ShaderNodeGroup", "Wrapper")
        source_wrapper.node_tree = wrapper
        connect(source.node_tree, source_wrapper, "Shader", source.node_tree.nodes["Active Output"], "Surface")
        before = graph_snapshot(group)
        with self.assertRaises((RuntimeError, ValueError)):
            shader.refresh_material_shader_group(source, group)
        self.assertEqual(before, graph_snapshot(group))
        self.assertEqual(len(bpy.data.node_groups), 2)

    def test_existing_non_shader_name_collision_is_rejected_without_suffix(self):
        source = make_material("Name Collision")
        collision = bpy.data.node_groups.new("Material Name Collision", "GeometryNodeTree")
        with self.assertRaises((RuntimeError, ValueError)):
            self.import_group(source)
        self.assertEqual(list(bpy.data.node_groups), [collision])

    def test_failed_copy_does_not_leave_temporary_groups(self):
        source = make_material("Failed Copy")
        before = graph_snapshot(source.node_tree)
        with mock.patch.object(shader, "_copy_surface_graph", side_effect=RuntimeError("Injected graph-copy failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected graph-copy failure"):
                self.import_group(source)
        self.assertEqual(before, graph_snapshot(source.node_tree))
        self.assertEqual(len(bpy.data.node_groups), 0)

    def test_failure_during_refresh_commit_restores_previous_graph_and_socket_ids(self):
        source = make_complex_material("Interrupted Refresh")
        target = make_material("Hub_Base")
        group, external_node, _ = self.import_group(source, target)
        group.color_tag = "COLOR"
        connect(target.node_tree, external_node, "Shader", target.node_tree.nodes["Active Output"], "Surface")
        group.nodes.active = group.nodes["Main Shader"]
        before = graph_snapshot(group)
        interface, target_links = interface_outputs(group), links_snapshot(target.node_tree)
        source.node_tree.nodes["Main Shader"].inputs["Metallic"].default_value = 0.93
        original_copy = shader._copy_node_graph
        failed = False

        def fail_commit_once(nodes, links, destination):
            nonlocal failed
            if destination == group and not failed:
                failed = True
                destination.nodes.new("ShaderNodeValue")
                raise RuntimeError("Injected partial refresh failure")
            return original_copy(nodes, links, destination)

        with mock.patch.object(shader, "_copy_node_graph", side_effect=fail_commit_once):
            with self.assertRaisesRegex(RuntimeError, "Injected partial refresh failure"):
                shader.refresh_material_shader_group(source, group)
        self.assertTrue(failed)
        self.assertEqual(before, graph_snapshot(group))
        self.assertEqual(interface, interface_outputs(group))
        self.assertEqual(target_links, links_snapshot(target.node_tree))
        self.assertEqual(external_node.node_tree, group)
        self.assertEqual(group.color_tag, "COLOR")
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_failed_reuse_preserves_existing_group_tag_and_target_graph(self):
        source, target = make_complex_material("Reusable Shader"), make_material("Hub_Base")
        group, _, _ = self.import_group(source)
        group.color_tag = "NONE"
        group_before = graph_snapshot(group)
        target_before = graph_snapshot(target.node_tree)
        with self.assertRaises((RuntimeError, ValueError)):
            self.import_group(source, SimpleNamespace())
        self.assertEqual(group.color_tag, "NONE")
        self.assertEqual(group_before, graph_snapshot(group))
        with mock.patch.object(shader, "_insertion_location", side_effect=RuntimeError("Injected placement failure")):
            with self.assertRaisesRegex(RuntimeError, "Injected placement failure"):
                self.import_group(source, target)
        self.assertEqual(group.color_tag, "NONE")
        self.assertEqual(group_before, graph_snapshot(group))
        self.assertEqual(target_before, graph_snapshot(target.node_tree))
        self.assertEqual(len(bpy.data.node_groups), 1)

    def test_nested_frames_preserve_layout_without_copying_unrelated_contents(self):
        source = make_material("Framed Surface")
        tree = source.node_tree
        outer = new_node(tree, "NodeFrame", "Outer Frame", (-500, 200))
        inner = new_node(tree, "NodeFrame", "Inner Frame", (80, -90))
        inner.parent = outer
        bsdf = tree.nodes["Main Shader"]
        bsdf.parent = inner
        bsdf.location = (40, -50)
        unused = new_node(tree, "ShaderNodeTexNoise", "Unused In Same Frame")
        unused.parent = inner
        before = graph_snapshot(tree)
        group, _, _ = self.import_group(source)
        self.assertIn("Outer Frame", group.nodes)
        self.assertIn("Inner Frame", group.nodes)
        self.assertNotIn("Unused In Same Frame", group.nodes)
        self.assertEqual(group.nodes["Main Shader"].parent, group.nodes["Inner Frame"])
        self.assertEqual(group.nodes["Inner Frame"].parent, group.nodes["Outer Frame"])
        for name in ("Main Shader", "Inner Frame", "Outer Frame"):
            self.assert_vector_close(group.nodes[name].location, tree.nodes[name].location)
        self.assertEqual(before, graph_snapshot(tree))

    def test_rgb_curves_and_color_mix_dynamic_sockets_preserved(self):
        source = make_material("Curves And Mix")
        tree = source.node_tree
        curve = new_node(tree, "ShaderNodeRGBCurve", "Curves")
        curve.mapping.initialize()
        curve.mapping.clip_min_y = -0.25
        curve.mapping.clip_max_y = 1.3
        curve.mapping.extend = "EXTRAPOLATED"
        curve.mapping.curves[3].points.new(0.35, 0.62).handle_type = "VECTOR"
        curve.mapping.update()
        curve.inputs["Fac"].default_value = 0.72
        mix = new_node(tree, "ShaderNodeMix", "Color Mix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.clamp_factor = False
        mix.clamp_result = True
        mix.inputs[0].default_value = 0.38
        mix.inputs[6].default_value = (0.2, 0.6, 0.8, 1)
        mix.inputs[7].default_value = (0.9, 0.3, 0.1, 1)
        connect(tree, curve, "Color", mix, 6)
        connect(tree, mix, 2, tree.nodes["Main Shader"], "Base Color")
        group, _, _ = self.import_group(source)
        copied_curve, copied_mix = group.nodes["Curves"], group.nodes["Color Mix"]
        self.assertEqual(properties(curve.mapping), properties(copied_curve.mapping))
        self.assertEqual(tuple(tuple((tuple(point.location), point.handle_type) for point in channel.points)
                               for channel in curve.mapping.curves),
                         tuple(tuple((tuple(point.location), point.handle_type) for point in channel.points)
                               for channel in copied_curve.mapping.curves))
        for name in ("data_type", "blend_type", "clamp_factor", "clamp_result"):
            self.assertEqual(getattr(mix, name), getattr(copied_mix, name))
        for original, copied in zip(mix.inputs, copied_mix.inputs):
            self.assertEqual(original.identifier, copied.identifier)
            self.assertEqual(plain(original.default_value), plain(copied.default_value))
        copied_link = copied_mix.inputs[6].links[0]
        self.assertEqual(copied_link.from_node, copied_curve)
        self.assertEqual(group.nodes["Main Shader"].inputs["Base Color"].links[0].from_socket,
                         copied_mix.outputs[2])

    def editor_space(self, material, edit_tree=None, **values):
        return SimpleNamespace(type="NODE_EDITOR", tree_type="ShaderNodeTree", shader_type="OBJECT",
                               id=material, edit_tree=material.node_tree if edit_tree is None else edit_tree,
                               **values)

    def editor_area(self, space):
        return SimpleNamespace(type="NODE_EDITOR", spaces=SimpleNamespace(active=space))

    def test_target_uses_current_shader_editor_including_pinned_material(self):
        pinned = make_material("Pinned Material")
        unrelated_active = make_material("Active Object Material")
        space = self.editor_space(pinned, pin=True)
        context = SimpleNamespace(area=self.editor_area(space), space_data=space,
                                  active_object=SimpleNamespace(active_material=unrelated_active), window=None)
        self.assertEqual(shader.get_current_shader_material(context), pinned)

    def test_target_from_viewport_requires_one_unambiguous_material_editor(self):
        first, second = make_material("First Editor"), make_material("Second Editor")
        context = SimpleNamespace(area=SimpleNamespace(type="VIEW_3D"),
                                  window=SimpleNamespace(screen=SimpleNamespace(areas=[])))
        self.assertIsNone(shader.get_current_shader_material(context))
        context.window.screen.areas = [self.editor_area(self.editor_space(first))]
        self.assertEqual(shader.get_current_shader_material(context), first)
        context.window.screen.areas.append(self.editor_area(self.editor_space(first)))
        self.assertEqual(shader.get_current_shader_material(context), first)
        context.window.screen.areas.append(self.editor_area(self.editor_space(second)))
        self.assertIsNone(shader.get_current_shader_material(context))

    def test_target_refuses_nested_group_editor_and_world_editor(self):
        material = make_material("Editor Root")
        nested = make_nested_group()
        space = self.editor_space(material, edit_tree=nested)
        context = SimpleNamespace(area=self.editor_area(space), space_data=space, window=None)
        self.assertIsNone(shader.get_current_shader_material(context))
        context.area = SimpleNamespace(type="VIEW_3D")
        context.window = SimpleNamespace(screen=SimpleNamespace(areas=[self.editor_area(space)]))
        self.assertIsNone(shader.get_current_shader_material(context))
        space.edit_tree = material.node_tree
        space.shader_type = "WORLD"
        self.assertIsNone(shader.get_current_shader_material(context))

    def ui_context(self, source, target=None):
        settings = SimpleNamespace(material_shader_search="", material_shader_source_index=list(bpy.data.materials).index(source))
        context = SimpleNamespace(scene=SimpleNamespace(rr_builder_export_settings=settings),
                                  area=SimpleNamespace(type="VIEW_3D"), space_data=None,
                                  window=None, screen=None)
        if target is not None:
            space = self.editor_space(target)
            context.area, context.space_data = self.editor_area(space), space
        return context

    def test_search_filters_existing_list_and_selection_does_not_import(self):
        metal, blue, clear, emission = [make_material(name) for name in
                                        ("Floor Metal", "Blue Glass", "Clear Glass", "Red Emission")]
        context = self.ui_context(blue)
        settings = context.scene.rr_builder_export_settings
        self.assertEqual(rr.matching_material_shader_sources(settings), [])
        settings.material_shader_search = "  gLaSs  "
        self.assertEqual([material.name for _, material in rr.matching_material_shader_sources(settings)],
                         ["Blue Glass", "Clear Glass"])
        selector = SimpleNamespace(source_name=clear.name, source_library="")
        self.assertEqual(rr.RR_OT_select_material_shader_source.execute(selector, context), {"FINISHED"})
        self.assertEqual(rr.selected_material_shader_source(context), clear)
        self.assertEqual(len(bpy.data.node_groups), 0)
        selector.source_name = blue.name
        self.assertEqual(rr.RR_OT_select_material_shader_source.execute(selector, context), {"FINISHED"})
        self.assertEqual(rr.selected_material_shader_source(context), blue)
        settings.material_shader_source_index = list(bpy.data.materials).index(metal)
        self.assertIsNone(rr.selected_material_shader_source(context))
        settings.material_shader_search = ""
        self.assertEqual(rr.matching_material_shader_sources(settings), [])
        self.assertEqual(rr.selected_material_shader_source(context), metal)
        settings.material_shader_source_index = -1
        self.assertIsNone(rr.selected_material_shader_source(context))
        settings.material_shader_source_index = len(bpy.data.materials)
        self.assertIsNone(rr.selected_material_shader_source(context))
        self.assertEqual(len(bpy.data.node_groups), 0)

    def test_material_list_marks_existing_group_and_invalid_material_unavailable(self):
        source, invalid = make_material("Blue Glass"), make_material("Empty Surface", default_shader=False)
        self.import_group(source)

        def draw(item):
            calls = []
            def operator(operator_id, **kwargs):
                self.assertEqual(operator_id, "rr_builder.select_material_shader_source")
                calls.append(kwargs)
                return SimpleNamespace()
            row = SimpleNamespace(enabled=True, operator=operator)
            layout = SimpleNamespace(row=lambda **kwargs: row)
            settings = SimpleNamespace(material_shader_source_index=0)
            rr.draw_material_shader_source(layout, settings, 0, item)
            return row, calls

        row, labels = draw(source)
        self.assertTrue(row.enabled)
        self.assertEqual(labels, [{"text": source.name, "icon": "CHECKMARK", "depress": True}])
        row, labels = draw(invalid)
        self.assertFalse(row.enabled)
        self.assertEqual(labels[0]["text"], invalid.name)

    def make_operator(self, action="IMPORT", source=None, target=None):
        reports = []
        operator = SimpleNamespace(action=action, source_name=source.name if source else "",
                                   source_library="", target_name=target.name if target else "",
                                   report=lambda *args: reports.append(args))
        operator._source = lambda: rr.RR_OT_import_material_shader._source(operator)
        operator.execute = lambda context: rr.RR_OT_import_material_shader.execute(operator, context)
        return operator, reports

    def test_import_click_directly_creates_then_reuses_without_confirmation(self):
        source, target = make_complex_material("Blue Glass"), make_material("Hub_Base")
        context = self.ui_context(source, target)
        dialogs = []
        context.window_manager = SimpleNamespace(invoke_props_dialog=lambda operator, **kwargs:
                                                dialogs.append((operator, kwargs)) or {"RUNNING_MODAL"})
        source_before, target_links = graph_snapshot(source.node_tree), links_snapshot(target.node_tree)
        before_count = len(target.node_tree.nodes)
        group = None
        for action in ("IMPORT", "IMPORT", "EXISTING"):
            with self.subTest(action=action, existing=group is not None):
                operator, reports = self.make_operator(action)
                self.assertEqual(rr.RR_OT_import_material_shader.invoke(operator, context, None), {"FINISHED"})
                self.assertEqual(operator.source_name, source.name)
                self.assertEqual(operator.source_library, "")
                self.assertEqual(operator.target_name, target.name)
                current_group = shader.generated_shader_group(source)
                if group is not None:
                    self.assertEqual(current_group, group)
                group = current_group
                self.assertEqual(group.color_tag, "SHADER")
                before_count += 1
                self.assertEqual(len(target.node_tree.nodes), before_count)
                self.assertEqual(len(bpy.data.node_groups), 1)
                self.assertEqual(source_before, graph_snapshot(source.node_tree))
                self.assertEqual(target_links, links_snapshot(target.node_tree))
                self.assertIn("Inserted", reports[-1][1])
        self.assertEqual(dialogs, [])

    def test_native_import_and_legacy_existing_action_run_without_confirmation(self):
        source, target = make_material("Plain Glass"), make_material("Hub_Base")
        context = self.ui_context(source, target)
        dialogs = []
        context.window_manager = SimpleNamespace(invoke_props_dialog=lambda operator, **kwargs:
                                                dialogs.append((operator, kwargs)) or {"RUNNING_MODAL"})
        source_before, target_before = graph_snapshot(source.node_tree), graph_snapshot(target.node_tree)
        old_nodes = set(target.node_tree.nodes)
        for action in ("IMPORT", "IMPORT", "EXISTING"):
            with self.subTest(action=action):
                operator, reports = self.make_operator(action)
                self.assertEqual(rr.RR_OT_import_material_shader.invoke(operator, context, None), {"FINISHED"})
                self.assertEqual(operator.source_name, source.name)
                self.assertEqual(operator.target_name, target.name)
                self.assertIn("Inserted", reports[-1][1])
                self.assertEqual(len(bpy.data.node_groups), 0)
        copies = [node for node in target.node_tree.nodes if node not in old_nodes]
        self.assertEqual(len(copies), 3)
        self.assertTrue(all(node.bl_idname == "ShaderNodeBsdfPrincipled" for node in copies))
        self.assertTrue(all(not node.outputs[0].is_linked for node in copies))
        self.assertEqual(dialogs, [])
        self.assertEqual(graph_snapshot(source.node_tree), source_before)
        for node in copies:
            target.node_tree.nodes.remove(node)
        self.assertEqual(graph_snapshot(target.node_tree), target_before)

    def test_native_operator_self_import_is_rejected_without_source_changes(self):
        source = make_material("Self Plain Glass")
        before = graph_snapshot(source.node_tree)
        context = self.ui_context(source, source)
        context.window_manager = SimpleNamespace(invoke_props_dialog=mock.Mock())
        operator, reports = self.make_operator()
        self.assertEqual(rr.RR_OT_import_material_shader.invoke(operator, context, None), {"CANCELLED"})
        self.assertTrue(reports)
        context.window_manager.invoke_props_dialog.assert_not_called()
        self.assertEqual(graph_snapshot(source.node_tree), before)
        self.assertEqual(len(bpy.data.node_groups), 0)

    def test_changed_target_is_rejected_without_creating_a_group(self):
        source, target, changed = [make_material(name) for name in ("Blue Glass", "Hub_Base", "Different Target")]
        context = self.ui_context(source, target)
        operator, reports = self.make_operator("IMPORT", source, target)
        changed_space = self.editor_space(changed)
        context.area, context.space_data = self.editor_area(changed_space), changed_space
        result = rr.RR_OT_import_material_shader.execute(operator, context)
        self.assertEqual(result, {"CANCELLED"})
        self.assertIn("changed", reports[-1][1])
        self.assertEqual(len(bpy.data.node_groups), 0)

    def test_refresh_retains_confirmation_before_updating_shared_group(self):
        source, target = make_complex_material("Blue Glass"), make_material("Hub_Base")
        group, _, _ = self.import_group(source, target)
        group_before = graph_snapshot(group)
        source.node_tree.nodes["Main Shader"].inputs["Metallic"].default_value = 0.59
        context = self.ui_context(source, target)
        dialogs = []
        context.window_manager = SimpleNamespace(invoke_props_dialog=lambda operator, **kwargs:
                                                dialogs.append((operator, kwargs)) or {"RUNNING_MODAL"})
        operator, _ = self.make_operator("REFRESH")
        self.assertEqual(rr.RR_OT_import_material_shader.invoke(operator, context, None), {"RUNNING_MODAL"})
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(group_before, graph_snapshot(group))
        self.assertEqual(operator.source_name, source.name)
        self.assertEqual(rr.RR_OT_import_material_shader.execute(operator, context), {"FINISHED"})
        self.assertAlmostEqual(group.nodes["Main Shader"].inputs["Metallic"].default_value, 0.59, places=6)

    def test_panel_keeps_one_import_label_and_automatically_reuses_existing_group(self):
        source, target = make_complex_material("Blue Glass"), make_material("Hub_Base")
        context = self.ui_context(source, target)
        settings = context.scene.rr_builder_export_settings
        calls = []

        class Layout:
            enabled = True

            def box(self, **kwargs):
                return self

            def row(self, **kwargs):
                return Layout()

            def column(self, **kwargs):
                return Layout()

            def label(self, **kwargs):
                pass

            def prop(self, *args, **kwargs):
                pass

            def operator(self, operator_id, **kwargs):
                op = SimpleNamespace()
                calls.append((operator_id, kwargs.get("text"), op, self.enabled))
                return op

        panel = SimpleNamespace(draw_fold_panel=lambda box, *args, **kwargs: box)
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.import_group(source)
                calls.clear()
                rr.RR_PT_builder_exporter.draw_material_shader_page(panel, Layout(), context, settings)
                actions = [(label, op.action, enabled) for operator_id, label, op, enabled in calls
                           if operator_id == "rr_builder.import_material_shader"]
                expected = [("Import Shader", "IMPORT", True)]
                if existing:
                    expected.append(("Refresh", "REFRESH", True))
                self.assertEqual(actions, expected)

    def test_import_without_shader_editor_target_is_rejected_before_execution(self):
        source = make_material("Blue Glass")
        context = self.ui_context(source)
        operator, reports = self.make_operator()
        with mock.patch.object(shader, "import_material_shader") as importer:
            result = rr.RR_OT_import_material_shader.invoke(operator, context, None)
            self.assertEqual(result, {"CANCELLED"})
            importer.assert_not_called()
        self.assertIn("Shader Editor", reports[-1][1])
        self.assertEqual(len(bpy.data.node_groups), 0)

    def test_panel_valid_simple_import_stays_enabled_with_invalid_existing_group(self):
        source, target = make_material("Plain Glass"), make_material("Hub_Base")
        existing = bpy.data.node_groups.new("Material Plain Glass", "ShaderNodeTree")
        context = self.ui_context(source, target)
        settings = context.scene.rr_builder_export_settings
        calls, labels = [], []

        class Layout:
            enabled = True

            def box(self, **kwargs):
                return self

            def row(self, **kwargs):
                return Layout()

            def column(self, **kwargs):
                return Layout()

            def label(self, **kwargs):
                labels.append(kwargs.get("text", ""))

            def prop(self, *args, **kwargs):
                pass

            def operator(self, operator_id, **kwargs):
                op = SimpleNamespace()
                calls.append((operator_id, kwargs.get("text"), op, self.enabled))
                return op

        panel = SimpleNamespace(draw_fold_panel=lambda box, *args, **kwargs: box)
        rr.RR_PT_builder_exporter.draw_material_shader_page(panel, Layout(), context, settings)
        imports = [(label, enabled) for operator_id, label, op, enabled in calls
                   if operator_id == "rr_builder.import_material_shader" and op.action == "IMPORT"]
        self.assertEqual(imports, [("Import Shader", True)])
        self.assertNotIn(shader.validate_reusable_shader_group(existing), labels)
        self.assertEqual(len(existing.nodes), 0)

    def test_refresh_operator_updates_existing_group_without_extra_insertion(self):
        source, target = make_complex_material("Blue Glass"), make_material("Hub_Base")
        group, node, _ = self.import_group(source, target)
        before_count = len(target.node_tree.nodes)
        source.node_tree.nodes["Main Shader"].inputs["Metallic"].default_value = 0.67
        operator, reports = self.make_operator("REFRESH", source)
        context = self.ui_context(source)
        self.assertEqual(rr.RR_OT_import_material_shader.execute(operator, context), {"FINISHED"})
        self.assertEqual(len(target.node_tree.nodes), before_count)
        self.assertEqual(node.node_tree, group)
        self.assertAlmostEqual(group.nodes["Main Shader"].inputs["Metallic"].default_value, 0.67, places=6)
        self.assertIn("Refreshed", reports[-1][1])


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    suite = unittest.defaultTestLoader.loadTestsFromNames(arguments, sys.modules[__name__]) if arguments else \
        unittest.defaultTestLoader.loadTestsFromTestCase(MaterialShaderTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print(f"RR_MATERIAL_SHADER_PASS tests={result.testsRun}")
    raise SystemExit(0 if result.wasSuccessful() else 1)
