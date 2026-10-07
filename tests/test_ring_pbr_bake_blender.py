"""Native Ring-mask PBR contracts in one disposable factory-startup Blender.

Loads the actual saved Ring Mask, Extend Mask, Ring Group and Mix Shaders
assets. Tiny CPU Cycles bakes verify pixels, instance contexts and HDR scaling;
no live user file is read, saved or modified. Run serially after resource handoff.
"""

import json
import math
from pathlib import Path
import sys
import unittest

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
from random_realm_builder_exporter import rr_pbr_shader_bake as bake
from random_realm_builder_exporter import rr_ring_nodes


def material(name):
    result = bpy.data.materials.new(name)
    result.use_nodes = True
    return result


def output_and_shader(source):
    output = next(node for node in source.node_tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial")
    shader = next(node for node in source.node_tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
    return output, shader


def append_group(filename, name):
    with bpy.data.libraries.load(str(ROOT / "node_library" / "assets" / filename), link=False) as (source, target):
        if name not in source.node_groups:
            raise AssertionError("The canonical native asset is missing: " + name)
        target.node_groups = [name]
    return target.node_groups[0]


def quad(source):
    mesh = bpy.data.meshes.new("Ring bake fixture mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.materials.append(source)
    uv = mesh.uv_layers.new(name="BakeUV")
    for item, value in zip(uv.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        item.uv = value
    uv.active_render = True
    obj = bpy.data.objects.new("Ring bake fixture", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()
    return obj


def two_material_quad(source, other):
    """Independent full 0..1 UVs per material on two faces of the same Mesh."""
    mesh = bpy.data.meshes.new("Two material bake fixture mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                      (2, 0, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0)], [],
                     [(0, 1, 2, 3), (4, 5, 6, 7)])
    mesh.materials.append(source)
    mesh.materials.append(other)
    mesh.polygons[1].material_index = 1
    uv = mesh.uv_layers.new(name="BakeUV")
    values = ((0, 0), (1, 0), (1, 1), (0, 1)) * 2
    for item, value in zip(uv.data, values):
        item.uv = value
    uv.active_render = True
    obj = bpy.data.objects.new("Two material bake fixture", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.view_layer.update()
    return obj


def independent_floor_material():
    source = material("Independent opaque floor source")
    _output, shader = output_and_shader(source)
    # Linked RGB keeps this material eligible for the real automatic bake entry.
    color = source.node_tree.nodes.new("ShaderNodeRGB")
    color.outputs[0].default_value = (.16, .23, .31, 1)
    source.node_tree.links.new(color.outputs[0], shader.inputs["Base Color"])
    shader.inputs["Roughness"].default_value = .61
    shader.inputs["Metallic"].default_value = .29
    shader.inputs["Emission Strength"].default_value = 0
    return source


def graph_state(source):
    seen = set()
    records = []

    def value(socket):
        default = socket.default_value
        if isinstance(default, (int, float, str, bool)):
            return default
        try:
            return tuple(default)
        except TypeError:
            return str(default)

    def visit(tree):
        if tree.as_pointer() in seen:
            return
        seen.add(tree.as_pointer())
        records.append((tree.as_pointer(), tree.name,
                        tuple((node.name, node.bl_idname, node.mute,
                               node.node_tree.as_pointer() if node.bl_idname == "ShaderNodeGroup" and node.node_tree else 0,
                               tuple((socket.identifier, value(socket)) for socket in node.inputs
                                     if hasattr(socket, "default_value"))) for node in tree.nodes),
                        tuple((link.from_node.name, link.from_socket.identifier,
                               link.to_node.name, link.to_socket.identifier) for link in tree.links),
                        tree.nodes.active.name if tree.nodes.active else None))
        for node in tree.nodes:
            if node.bl_idname == "ShaderNodeGroup" and node.node_tree:
                visit(node.node_tree)

    visit(source.node_tree)
    return tuple(records)


def datablocks():
    return tuple(frozenset(item.as_pointer() for item in collection)
                 for collection in (bpy.data.materials, bpy.data.node_groups, bpy.data.images))


def group_node(tree, group, name):
    node = tree.nodes.new("ShaderNodeGroup")
    node.node_tree = group
    node.name = name
    return node


def ring_fixture(*, strength=12):
    source = material("Ring floor source")
    tree = source.node_tree
    output, background = output_and_shader(source)
    background.inputs["Base Color"].default_value = (.12, .24, .36, 1)
    background.inputs["Roughness"].default_value = .72
    background.inputs["Metallic"].default_value = .13
    background.inputs["Emission Color"].default_value = (.03, .06, .09, 1)
    background.inputs["Emission Strength"].default_value = 2
    line = tree.nodes.new("ShaderNodeBsdfPrincipled")
    line.name = "Ring line shader"
    line.inputs["Base Color"].default_value = (.8, .4, .1, 1)
    line.inputs["Roughness"].default_value = .22
    line.inputs["Metallic"].default_value = .81
    line.inputs["Emission Color"].default_value = (.8, .4, .1, 1)
    line.inputs["Emission Strength"].default_value = strength
    radial = group_node(tree, append_group("Randy_Ring_Mask.blend", "Ring Mask"), "Actual Ring Mask")
    for name, value in (("Inner Radius", .45), ("Ring Width", .2), ("Edge Softness", .03),
                        ("Start Angle", 0), ("Sweep Angle", 360)):
        radial.inputs[name].default_value = value
    extend = group_node(tree, append_group("Randy_Extend_Mask.blend", "Extend Mask"), "Actual Extend Mask")
    tree.links.new(radial.outputs["Ring Data"], extend.inputs["Source"])
    extend.inputs["Mode"].default_value = "Outer"
    extend.inputs["Width"].default_value = .08
    extend.inputs["Gap"].default_value = .05
    extend.inputs["Softness"].default_value = .01
    ring = group_node(tree, append_group("Randy_Ring_Group.blend", "Ring Group"), "Actual Ring Group")
    tree.links.new(radial.outputs["Mask"], ring.inputs["Mask 1"])
    tree.links.new(extend.outputs["Mask"], ring.inputs["Mask 2"])
    tree.links.new(line.outputs["BSDF"], ring.inputs["Shader"])
    mixer = group_node(tree, append_group("Randy_Mix_Shaders.blend", "Mix Shaders"), "Actual Mix Shaders")
    tree.links.new(background.outputs["BSDF"], mixer.inputs["Base Shader"])
    tree.links.new(ring.outputs["Mask"], mixer.inputs["Mask 1"])
    tree.links.new(ring.outputs["Shader"], mixer.inputs["Shader 1"])
    tree.links.new(mixer.outputs["Shader"], output.inputs["Surface"])
    return source, ring, radial, line


def smoothstep(low, high, value):
    factor = min(1, max(0, (value - low) / (high - low)))
    return factor * factor * (3 - 2 * factor)


def mask_oracle(u, v):
    radius = 2 * math.hypot(u - .5, v - .5)
    if radius > 1:
        return 0
    radial = smoothstep(.45, .48, radius) * (1 - smoothstep(.62, .65, radius))
    outer = smoothstep(.70, .71, radius) * (1 - smoothstep(.77, .78, radius))
    return max(radial, outer)


class RingPbrBakeNativeTests(unittest.TestCase):
    def setUp(self):
        for collection in (bpy.data.objects, bpy.data.materials, bpy.data.meshes,
                           bpy.data.images, bpy.data.node_groups):
            for item in list(collection):
                collection.remove(item, do_unlink=True)
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.device = "CPU"
        scene.cycles.samples = 1
        scene.render.threads_mode = "FIXED"
        scene.render.threads = 1

    def native_pixels(self, source, obj, role, size=64):
        image = bpy.data.images.new("Native " + role, width=size, height=size, alpha=True, is_data=True)
        image.colorspace_settings.name = "Non-Color"
        slot = obj.material_slots[0]
        old_link, old_material = slot.link, slot.material
        before = graph_state(source)
        existing = datablocks()
        try:
            with bake.cloned_materials([source], role) as cloned:
                target = cloned[source]
                node = target.node_tree.nodes.new("ShaderNodeTexImage")
                node.image = image
                for candidate in target.node_tree.nodes:
                    candidate.select = candidate == node
                target.node_tree.nodes.active = node
                slot.link, slot.material = "OBJECT", target
                try:
                    bpy.ops.object.bake(type="NORMAL" if role == "Normal" else "EMIT", margin=0,
                                        use_clear=True, normal_space="TANGENT")
                finally:
                    slot.material, slot.link = old_material, old_link
            result = tuple(image.pixels[:])
            self.assertEqual(before, graph_state(source))
            self.assertEqual(existing, datablocks())
            self.assertEqual(old_material, slot.material)
            self.assertEqual(old_link, slot.link)
            return result
        finally:
            bpy.data.images.remove(image, do_unlink=True)

    def sample(self, pixels, radius, size=64):
        x = min(size - 1, max(0, round((.5 + radius / 2) * size - .5)))
        y = size // 2
        offset = (y * size + x) * 4
        u, v = (x + .5) / size, (y + .5) / size
        return pixels[offset:offset + 3], mask_oracle(u, v)

    def test_saved_ring_extend_group_mixer_bake_real_channels_and_mask_composition(self):
        source, _ring, _radial, _line = ring_fixture()
        obj = quad(source)
        source_before = graph_state(source)
        ids = datablocks()
        plan = bake.analyze_material(source)
        self.assertTrue(plan["has_ring"])
        self.assertEqual([.8, .4, .1, 1], [round(item, 6) for item in plan["ring_color"]])
        self.assertAlmostEqual(12, plan["ring_emission_strength"])
        self.assertAlmostEqual(.18, plan["emission_strength"], places=6)
        self.assertEqual(source_before, graph_state(source))
        self.assertEqual(ids, datablocks())
        maps = {role: self.native_pixels(source, obj, role) for role in
                ("BaseColor", "Roughness", "Metallic", "Emission", "RingMask", "RingBase", "Normal")}
        for radius in (.1, .46, .55, .63, .68, .74, .82):
            for role, pixels in maps.items():
                actual, mask = self.sample(pixels, radius)
                if role == "BaseColor":
                    expected = tuple(base * (1 - mask) + line * mask
                                     for base, line in zip((.12, .24, .36), (.8, .4, .1)))
                elif role == "Roughness":
                    expected = (.72 * (1 - mask) + .22 * mask,) * 3
                elif role == "Metallic":
                    expected = (.13 * (1 - mask) + .81 * mask,) * 3
                elif role == "RingMask":
                    expected = (mask,) * 3
                elif role == "RingBase":
                    expected = tuple(value * (1 - mask) for value in (.12, .24, .36))
                elif role == "Emission":
                    expected = tuple(value * (1 - mask) for value in (1 / 3, 2 / 3, 1))
                else:
                    expected = (.5, .5, 1)
                for value, wanted in zip(actual, expected):
                    self.assertAlmostEqual(wanted, value, delta=.015, msg=role + " at radius " + str(radius))
        self.assertEqual(source_before, graph_state(source))

    def test_zero_ring_strength_keeps_explicit_mask_and_original_ring_color(self):
        source, _ring, _radial, _line = ring_fixture(strength=0)
        plan = bake.analyze_material(source)
        self.assertTrue(plan["has_ring"])
        self.assertEqual(0, plan["ring_emission_strength"])
        self.assertEqual([.8, .4, .1, 1], [round(item, 6) for item in plan["ring_color"]])
        obj = quad(source)
        actual, _expected = self.sample(self.native_pixels(source, obj, "RingMask"), .55)
        self.assertTrue(all(item > .99 for item in actual))

    def test_hdr_emission_is_normalized_without_clipping_and_zero_is_valid(self):
        source = material("HDR source")
        output, _shader = output_and_shader(source)
        emit = source.node_tree.nodes.new("ShaderNodeEmission")
        emit.inputs["Color"].default_value = (.8, .2, .1, 1)
        emit.inputs["Strength"].default_value = 16
        source.node_tree.links.new(emit.outputs[0], output.inputs["Surface"])
        plan = bake.analyze_material(source)
        self.assertAlmostEqual(12.8, plan["emission_strength"], places=5)
        obj = quad(source)
        pixels = self.native_pixels(source, obj, "Emission", size=8)
        for actual, expected in zip(pixels[4 * 27:4 * 27 + 3], (1, .25, .125)):
            self.assertAlmostEqual(expected, actual, delta=.004)
        emit.inputs["Strength"].default_value = 0
        plan = bake.analyze_material(source)
        self.assertEqual(0, plan["emission_strength"])
        self.assertFalse(plan["has_emission"])
        pixels = self.native_pixels(source, obj, "Emission", size=8)
        self.assertTrue(all(abs(value) < 1e-6 for index, value in enumerate(pixels) if index % 4 < 3))

    def test_group_input_constants_and_two_shared_group_instances_keep_their_context(self):
        source = material("Instance source")
        group = bpy.data.node_groups.new("Shared shader instance", "ShaderNodeTree")
        group.interface.new_socket(name="Color", in_out="INPUT", socket_type="NodeSocketColor")
        group.interface.new_socket(name="Strength", in_out="INPUT", socket_type="NodeSocketFloat")
        group.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
        incoming = group.nodes.new("NodeGroupInput")
        outgoing = group.nodes.new("NodeGroupOutput")
        outgoing.is_active_output = True
        emit = group.nodes.new("ShaderNodeEmission")
        group.links.new(incoming.outputs["Color"], emit.inputs["Color"])
        group.links.new(incoming.outputs["Strength"], emit.inputs["Strength"])
        group.links.new(emit.outputs[0], outgoing.inputs["Shader"])
        a = group_node(source.node_tree, group, "First instance")
        b = group_node(source.node_tree, group, "Second instance")
        a.inputs["Color"].default_value = (.8, .1, .1, 1)
        a.inputs["Strength"].default_value = 2
        b.inputs["Color"].default_value = (.1, .1, .6, 1)
        b.inputs["Strength"].default_value = 7
        mix = source.node_tree.nodes.new("ShaderNodeMixShader")
        mix.inputs[0].default_value = .25
        source.node_tree.links.new(a.outputs["Shader"], mix.inputs[1])
        source.node_tree.links.new(b.outputs["Shader"], mix.inputs[2])
        output, _shader = output_and_shader(source)
        source.node_tree.links.new(mix.outputs[0], output.inputs["Surface"])
        plan = bake.analyze_material(source)
        self.assertAlmostEqual(4.2, plan["emission_strength"], places=5)
        before = graph_state(source)
        with bake.cloned_materials([source], "Emission") as cloned:
            nodes = cloned[source].node_tree.nodes
            self.assertNotEqual(nodes[a.name].node_tree, nodes[b.name].node_tree)
            self.assertNotEqual(group, nodes[a.name].node_tree)
        pixels = self.native_pixels(source, quad(source), "Emission", size=8)
        expected = tuple((first * 2 * .75 + second * 7 * .25) / 4.2
                         for first, second in zip((.8, .1, .1), (.1, .1, .6)))
        for actual, wanted in zip(pixels[4 * 27:4 * 27 + 3], expected):
            self.assertAlmostEqual(wanted, actual, delta=.004)
        self.assertEqual(before, graph_state(source))

    def assert_rejected_without_allocation(self, source, message):
        before, graph = datablocks(), graph_state(source)
        with self.assertRaisesRegex(ValueError, message):
            with bake.cloned_materials([source], "BaseColor"):
                self.fail("Unsupported material reached baking.")
        self.assertEqual(before, datablocks())
        self.assertEqual(graph, graph_state(source))

    def test_unsupported_glass_closure_is_rejected_before_cloning(self):
        source = material("Unsupported glass")
        output, _shader = output_and_shader(source)
        glass = source.node_tree.nodes.new("ShaderNodeBsdfGlass")
        source.node_tree.links.new(glass.outputs[0], output.inputs["Surface"])
        self.assert_rejected_without_allocation(source, "Unsupported Surface shader")

    def test_procedural_linked_emission_strength_is_rejected_before_cloning(self):
        source = material("Linked strength")
        _output, shader = output_and_shader(source)
        shader.inputs["Emission Color"].default_value = (1, .5, .1, 1)
        noise = source.node_tree.nodes.new("ShaderNodeTexNoise")
        source.node_tree.links.new(noise.outputs["Fac"], shader.inputs["Emission Strength"])
        self.assert_rejected_without_allocation(source, "Emission Strength must resolve to a constant")

    def test_additional_principled_lobes_fresnel_and_weight_fail_before_allocation(self):
        for names, default in bake._BASIC_PRINCIPLED_DEFAULTS:
            with self.subTest(socket=names[0]):
                source = material("Unsupported " + names[0])
                _output, shader = output_and_shader(source)
                incoming = bake._input(shader, *names)
                if incoming is None:
                    continue  # The historical alias is absent on this Blender version.
                incoming.default_value = .2 if default == 0 else default + .2
                self.assert_rejected_without_allocation(source, "basic opaque PBR bake requires")
                incoming.default_value = default
                value = source.node_tree.nodes.new("ShaderNodeValue")
                value.outputs[0].default_value = default
                source.node_tree.links.new(value.outputs[0], incoming)
                self.assert_rejected_without_allocation(source, "linked " + incoming.name)

    def test_ring_group_shader_without_its_mask_is_rejected_before_cloning(self):
        source, ring, _radial, _line = ring_fixture()
        output, _shader = output_and_shader(source)
        source.node_tree.links.new(ring.outputs["Shader"], output.inputs["Surface"])
        self.assert_rejected_without_allocation(source, "not mixed by its own Mask")

    def test_edited_ring_group_signature_is_rejected_before_cloning(self):
        source, ring, _radial, _line = ring_fixture()
        node = next(item for item in ring.node_tree.nodes if item.bl_idname == "ShaderNodeMath")
        node.operation = "ADD"
        self.assert_rejected_without_allocation(source, "calculation was edited")

    def test_multiple_ring_colors_are_explicitly_rejected(self):
        source, ring, radial, _line = ring_fixture()
        tree = source.node_tree
        output, _shader = output_and_shader(source)
        other = group_node(tree, rr_ring_nodes.new_group(), "Different Ring Group")
        line = tree.nodes.new("ShaderNodeEmission")
        line.inputs["Color"].default_value = (.1, .3, .8, 1)
        line.inputs["Strength"].default_value = 12
        tree.links.new(line.outputs[0], other.inputs["Shader"])
        tree.links.new(radial.outputs["Mask"], other.inputs["Mask 1"])
        previous = output.inputs["Surface"].links[0].from_socket
        mix = tree.nodes.new("ShaderNodeMixShader")
        tree.links.new(previous, mix.inputs[1])
        tree.links.new(other.outputs["Mask"], mix.inputs[0])
        tree.links.new(other.outputs["Shader"], mix.inputs[2])
        tree.links.new(mix.outputs[0], output.inputs["Surface"])
        self.assert_rejected_without_allocation(source, "different colors or emission strengths")

    def test_all_materials_preflight_before_first_copy_and_failure_cleans_only_owned_ids(self):
        source = material("Valid source")
        invalid = material("Invalid source")
        output, _shader = output_and_shader(invalid)
        glass = invalid.node_tree.nodes.new("ShaderNodeBsdfGlass")
        invalid.node_tree.links.new(glass.outputs[0], output.inputs["Surface"])
        before, graph = datablocks(), graph_state(source)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            with bake.cloned_materials([source, invalid], "Metallic"):
                self.fail("Invalid second material reached baking.")
        self.assertEqual(before, datablocks())
        ring, _instance, _radial, _line = ring_fixture()
        before_ring, graph_ring = datablocks(), graph_state(ring)
        with self.assertRaisesRegex(RuntimeError, "caller failed"):
            with bake.cloned_materials([ring], "RingBase"):
                raise RuntimeError("caller failed")
        self.assertEqual(before_ring, datablocks())
        self.assertEqual(graph_ring, graph_state(ring))
        self.assertEqual(graph, graph_state(source))

    def test_real_auto_entry_mixed_slots_ring_only_pass_preserves_non_ring_source(self):
        import tempfile
        from unittest import mock
        import random_realm_builder_exporter as rr
        source, _ring, _radial, _line = ring_fixture()
        other = independent_floor_material()
        obj = two_material_quad(source, other)
        originals = graph_state(source), graph_state(other)
        source_groups = frozenset(group.as_pointer() for group in bpy.data.node_groups)
        original_image_count = len(bpy.data.images)
        original_material_count = len(bpy.data.materials)
        rr.register()
        try:
            settings = bpy.context.scene.rr_builder_export_settings
            settings.pbr_framework_use_material_filter = False
            settings.pbr_framework_use_role_filter = False
            settings.pbr_bake_samples = 1
            settings.pbr_bake_margin = 2
            with tempfile.TemporaryDirectory(prefix="rr_ring_mixed_slots_") as output:
                settings.pbr_bake_output_root = output
                with mock.patch.object(rr, "clamp_pbr_bake_size", return_value=64):
                    result = rr.bake_selected_to_pbr(bpy.context, settings)
                self.assertEqual(2, result["material_count"])
                self.assertEqual(12, result["image_count"])
                self.assertTrue(all(Path(path).is_file() for path in result["files"]))
                ring_result, other_result = (slot.material for slot in obj.material_slots)
                self.assertNotEqual(source, ring_result)
                self.assertNotEqual(other, other_result)
                self.assertIn("RingMask", json.loads(ring_result["rr_pbr_baked_roles"]))
                self.assertNotIn("RingMask", json.loads(other_result["rr_pbr_baked_roles"]))
                self.assertEqual(0, other_result["rr_pbr_baked_emission_strength"])
                self.assertEqual(originals, (graph_state(source), graph_state(other)))
                self.assertEqual(source_groups, frozenset(group.as_pointer() for group in bpy.data.node_groups))
                self.assertEqual(original_image_count + 12, len(bpy.data.images))
                self.assertEqual(original_material_count + 2, len(bpy.data.materials))
                self.assertFalse(any(image.name.startswith("RR Scratch") for image in bpy.data.images))
        finally:
            rr.unregister()

    def test_real_auto_repeat_bake_uses_renamed_source_and_fresh_ring_maps(self):
        import hashlib
        import tempfile
        from unittest import mock
        import random_realm_builder_exporter as rr
        source, _ring, radial, line = ring_fixture()
        identity = source.name
        obj = quad(source)
        shared = bpy.data.objects.new("Untouched shared source user", obj.data)
        bpy.context.scene.collection.objects.link(shared)
        source_groups = frozenset(group.as_pointer() for group in bpy.data.node_groups)
        rr.register()
        try:
            settings = bpy.context.scene.rr_builder_export_settings
            settings.pbr_framework_use_material_filter = False
            settings.pbr_framework_use_role_filter = False
            settings.pbr_bake_samples = 1
            settings.pbr_bake_margin = 2
            with tempfile.TemporaryDirectory(prefix="rr_ring_repeat_") as output:
                settings.pbr_bake_output_root = output
                with mock.patch.object(rr, "clamp_pbr_bake_size", return_value=64):
                    first = rr.bake_selected_to_pbr(bpy.context, settings)
                first_material = obj.material_slots[0].material
                self.assertEqual(7, first["image_count"])
                self.assertEqual(source, first_material["rr_pbr_baked_source_material"])
                self.assertEqual(identity, first_material["rr_pbr_baked_source_identity"])
                first_hashes = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                                for path in first["files"]}
                first_mask = first_material.node_tree.nodes["RingMask"].image
                old_pixels = tuple(first_mask.pixels[:])
                old_line, _expected = self.sample(old_pixels, .55)
                self.assertGreater(old_line[0], .95)

                source.name = "Renamed original Ring floor"
                radial.inputs["Inner Radius"].default_value = .15
                line.inputs["Base Color"].default_value = (.2, .7, .3, 1)
                line.inputs["Emission Color"].default_value = (.2, .7, .3, 1)
                line.inputs["Emission Strength"].default_value = 0
                changed_graph = graph_state(source)
                settings.pbr_framework_use_material_filter = True
                # The UI chooses the assigned baked name, rather than the
                # original source's new name. Both disabled-name forms work.
                rr.set_pbr_framework_selected_material_names(settings, {first_material.name})
                for disabled_name in (first_material.name, source.name):
                    rr.set_pbr_bake_disabled_material_names(settings, {disabled_name})
                    before = datablocks()
                    disabled = rr.bake_selected_to_pbr(bpy.context, settings)
                    self.assertEqual(0, disabled["material_count"])
                    self.assertEqual([first_material.name], disabled["disabled_materials"])
                    self.assertEqual(before, datablocks())
                    self.assertEqual(first_material, obj.material_slots[0].material)
                rr.set_pbr_bake_disabled_material_names(settings, set())
                with mock.patch.object(rr, "clamp_pbr_bake_size", return_value=64):
                    second = rr.bake_selected_to_pbr(bpy.context, settings)
                fresh = obj.material_slots[0].material
                self.assertEqual(1, second["material_count"])
                self.assertEqual(7, second["image_count"])
                self.assertNotEqual(first_material, fresh)
                self.assertEqual(source, fresh["rr_pbr_baked_source_material"])
                self.assertEqual(identity, fresh["rr_pbr_baked_source_identity"])
                self.assertEqual(source.name, fresh["rr_pbr_baked_source_name"])
                self.assertEqual(0, fresh["rr_pbr_baked_ring_emission_strength"])
                for actual, expected in zip(fresh["rr_pbr_baked_ring_color"], (.2, .7, .3, 1)):
                    self.assertAlmostEqual(expected, actual, places=6)
                new_pixels = tuple(fresh.node_tree.nodes["RingMask"].image.pixels[:])
                new_line, _expected = self.sample(new_pixels, .55)
                self.assertLess(new_line[0], .05)
                self.assertNotEqual(old_pixels, new_pixels)
                self.assertTrue(set(first["files"]).isdisjoint(second["files"]))
                for path, original_hash in first_hashes.items():
                    self.assertEqual(original_hash, hashlib.sha256(Path(path).read_bytes()).hexdigest())
                self.assertEqual(first_material, bpy.data.materials.get(first_material.name))
                self.assertEqual(source, obj.data.materials[0])
                self.assertEqual("DATA", shared.material_slots[0].link)
                self.assertEqual(source, shared.material_slots[0].material)
                self.assertEqual(changed_graph, graph_state(source))
                self.assertEqual(source_groups, frozenset(group.as_pointer() for group in bpy.data.node_groups))
                package = Path(output) / "fresh-export"
                package.mkdir()
                entries, warnings = rr.build_material_map_manifest(obj, str(package))
                self.assertFalse(warnings)
                self.assertEqual(1, len(entries))
                entry = entries[0]
                self.assertEqual(identity, entry["sourceMaterial"])
                self.assertEqual(fresh.name, entry["material"])
                self.assertEqual([.2, .7, .3, 1], entry["surface"]["ringColor"])
                self.assertEqual(0, entry["surface"]["ringEmissionStrength"])
                self.assertTrue((package / entry["ringMask"]).is_file())
                new_mask_path = fresh.node_tree.nodes["RingMask"].image.filepath_raw
                self.assertEqual(hashlib.sha256(Path(new_mask_path).read_bytes()).hexdigest(),
                                 hashlib.sha256((package / entry["ringMask"]).read_bytes()).hexdigest())
        finally:
            rr.unregister()

    def test_legacy_baked_source_name_resolves_before_any_bake_allocation(self):
        from unittest import mock
        import random_realm_builder_exporter as rr
        source = independent_floor_material()
        obj = quad(source)
        legacy = material("Legacy result")
        legacy["rr_pbr_baked_roles"] = json.dumps(["BaseColor"])
        legacy["rr_pbr_baked_source_name"] = source.name
        obj.material_slots[0].link = "OBJECT"
        obj.material_slots[0].material = legacy
        before, original = datablocks(), graph_state(source)
        rr.register()
        try:
            settings = bpy.context.scene.rr_builder_export_settings
            settings.pbr_framework_use_material_filter = True
            rr.set_pbr_framework_selected_material_names(settings, {legacy.name})
            with mock.patch.object(rr.rr_pbr_shader_bake, "analyze_material",
                                   side_effect=RuntimeError("original reached preflight")) as analyze, \
                    mock.patch.object(rr, "create_bake_image") as allocate:
                with self.assertRaisesRegex(RuntimeError, "original reached preflight"):
                    rr.bake_selected_to_pbr(bpy.context, settings)
                analyze.assert_called_once_with(source)
                allocate.assert_not_called()
            self.assertEqual(before, datablocks())
            self.assertEqual(original, graph_state(source))
            self.assertEqual(legacy, obj.material_slots[0].material)
            self.assertEqual(source, obj.data.materials[0])
        finally:
            rr.unregister()

    def test_missing_deleted_and_cyclic_baked_sources_fail_before_allocation(self):
        from unittest import mock
        import random_realm_builder_exporter as rr
        assigned = material("Invalid baked source fixture")
        assigned["rr_pbr_baked_roles"] = json.dumps(["BaseColor"])
        obj = quad(assigned)
        rr.register()
        try:
            settings = bpy.context.scene.rr_builder_export_settings
            settings.pbr_framework_use_material_filter = False
            for kind in ("legacy missing", "deleted ID", "self cycle", "two material cycle"):
                with self.subTest(kind=kind):
                    if "rr_pbr_baked_source_material" in assigned:
                        del assigned["rr_pbr_baked_source_material"]
                    assigned["rr_pbr_baked_source_name"] = "Source no longer exists"
                    expected_error = "original bake material is missing"
                    if kind == "deleted ID":
                        deleted = material("Deleted original")
                        assigned["rr_pbr_baked_source_material"] = deleted
                        bpy.data.materials.remove(deleted, do_unlink=True)
                    elif kind == "self cycle":
                        assigned["rr_pbr_baked_source_material"] = assigned
                        expected_error = "references form a cycle"
                    elif kind == "two material cycle":
                        other = material("Other cyclic baked result")
                        other["rr_pbr_baked_roles"] = json.dumps(["BaseColor"])
                        other["rr_pbr_baked_source_material"] = assigned
                        assigned["rr_pbr_baked_source_material"] = other
                        expected_error = "references form a cycle"
                    before, original = datablocks(), graph_state(assigned)
                    with mock.patch.object(rr, "create_bake_image") as allocate, \
                            mock.patch.object(rr, "bake_active_meshes") as native:
                        with self.assertRaisesRegex(RuntimeError, expected_error):
                            rr.bake_selected_to_pbr(bpy.context, settings)
                        allocate.assert_not_called()
                        native.assert_not_called()
                    self.assertEqual(before, datablocks())
                    self.assertEqual(original, graph_state(assigned))
                    self.assertEqual(assigned, obj.material_slots[0].material)
        finally:
            rr.unregister()

    def test_source_material_id_and_identity_persist_in_saved_library(self):
        import tempfile
        source = independent_floor_material()
        original_identity = source.name
        saved = material("Persistent baked result fixture")
        saved["rr_pbr_baked_roles"] = json.dumps(["BaseColor"])
        saved["rr_pbr_baked_source_material"] = source
        saved["rr_pbr_baked_source_name"] = source.name
        saved["rr_pbr_baked_source_identity"] = original_identity
        source.name = "Original renamed before saving library"
        graph = graph_state(source)
        with tempfile.TemporaryDirectory(prefix="rr_bake_source_id_") as directory:
            path = str(Path(directory) / "persistent-source.blend")
            bpy.data.libraries.write(path, {saved}, fake_user=True)
            with bpy.data.libraries.load(path, link=False) as (_available, target):
                target.materials = [saved.name]
            loaded = target.materials[0]
            restored = loaded["rr_pbr_baked_source_material"]
            self.assertIsInstance(restored, bpy.types.Material)
            self.assertEqual(restored, bpy.data.materials.get(restored.name))
            self.assertEqual(original_identity, loaded["rr_pbr_baked_source_identity"])
            self.assertTrue(restored.name.startswith(source.name))
            self.assertTrue(restored.use_nodes)
            self.assertEqual(graph, graph_state(source))

    def test_repeat_bake_failure_restores_the_assigned_baked_slot_and_scene_state(self):
        import tempfile
        from unittest import mock
        import random_realm_builder_exporter as rr
        source = independent_floor_material()
        obj = quad(source)
        assigned = material("Existing baked result before injected failure")
        assigned["rr_pbr_baked_roles"] = json.dumps(["BaseColor"])
        assigned["rr_pbr_baked_source_material"] = source
        assigned["rr_pbr_baked_source_name"] = source.name
        assigned["rr_pbr_baked_source_identity"] = source.name
        obj.material_slots[0].link = "OBJECT"
        obj.material_slots[0].material = assigned
        before_graph = graph_state(source)
        before_materials = frozenset(bpy.data.materials)
        before_groups = frozenset(bpy.data.node_groups)
        before_engine = bpy.context.scene.render.engine
        before_samples = bpy.context.scene.cycles.samples
        before_uv = rr.capture_mesh_uv_state([obj])
        rr.register()
        try:
            settings = bpy.context.scene.rr_builder_export_settings
            settings.pbr_framework_use_material_filter = True
            rr.set_pbr_framework_selected_material_names(settings, {assigned.name})

            def fail(_role, _settings):
                target = obj.material_slots[0].material
                self.assertNotEqual(assigned, target)
                self.assertNotEqual(source, target)
                self.assertEqual("ShaderNodeRGB", next(
                    node for node in target.node_tree.nodes if node.bl_idname == "ShaderNodeRGB").bl_idname)
                self.assertEqual(before_graph, graph_state(source))
                raise RuntimeError("injected repeat bake failure")

            with tempfile.TemporaryDirectory(prefix="rr_repeat_restore_") as directory:
                settings.pbr_bake_output_root = directory
                with mock.patch.object(rr, "clamp_pbr_bake_size", return_value=8), \
                        mock.patch.object(rr, "bake_active_meshes", side_effect=fail):
                    with self.assertRaisesRegex(RuntimeError, "injected repeat bake failure"):
                        rr.bake_selected_to_pbr(bpy.context, settings)
            self.assertEqual("OBJECT", obj.material_slots[0].link)
            self.assertEqual(assigned, obj.material_slots[0].material)
            self.assertEqual(source, obj.data.materials[0])
            self.assertEqual(before_graph, graph_state(source))
            self.assertEqual(before_materials, frozenset(bpy.data.materials))
            self.assertEqual(before_groups, frozenset(bpy.data.node_groups))
            self.assertEqual(before_engine, bpy.context.scene.render.engine)
            self.assertEqual(before_samples, bpy.context.scene.cycles.samples)
            self.assertEqual(before_uv, rr.capture_mesh_uv_state([obj]))
            self.assertEqual([obj], list(bpy.context.selected_objects))
            self.assertEqual(obj, bpy.context.view_layer.objects.active)
            self.assertFalse(any(image.name.startswith("RR Bake Scratch") for image in bpy.data.images))
        finally:
            rr.unregister()


if __name__ == "__main__":
    if not bpy.app.background:
        raise RuntimeError("Run this suite only in a disposable background Blender.")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RingPbrBakeNativeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("RR_RING_PBR_BAKE_PASS " + json.dumps({"tests": result.testsRun, "real_saved_assets": True,
                                                "tiny_cpu_cycles_bakes": True}))
