"""Compare actual metallic ring boundaries with Blender's native Mix Shader.

Run only in a serial, isolated factory process:
blender --background --factory-startup --disable-autoexec --threads 1
--python-exit-code 1 --python tests/test_rr_shader_mixer_edges_blender.py
-- --report-dir <persistent validation directory>

Both renders use the same object, shader nodes, camera, world and sampling seed.
Only the Material Output Surface connection changes. The original saved Ring
Mask asset is appended, never rebuilt or overwritten. No .blend file is saved.
"""

import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
mixer = importlib.import_module("random_realm_builder_exporter.rr_shader_mixer")
RESOLUTION = 96
SAMPLES = 32
ORTHO_SCALE = 1.04
# Linear EXR channels, including every boundary pixel and alpha. Do not relax
# this threshold in response to a regression; first investigate the mismatch.
ABSOLUTE_TOLERANCE = 0.002
REPORT_DIR = None
REPORTS = []


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _value(value):
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def _tree_signature(tree):
    """Watch source parameters, node presentation and all source connections."""
    nodes = []
    for node in tree.nodes:
        properties = []
        for prop in node.bl_rna.properties:
            if (prop.identifier != "rna_type" and prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
                    and not prop.is_readonly):
                properties.append((prop.identifier, _value(getattr(node, prop.identifier))))
        defaults = [
            (direction, socket.identifier, _value(socket.default_value))
            for direction, sockets in (("INPUT", node.inputs), ("OUTPUT", node.outputs))
            for socket in sockets if hasattr(socket, "default_value")
        ]
        nodes.append((node.name, node.bl_idname, tuple(properties), tuple(defaults)))
    links = sorted((link.from_node.name, link.from_socket.identifier,
                    link.to_node.name, link.to_socket.identifier) for link in tree.links)
    return tuple(nodes), tuple(links)


def _principled(tree, name, color, *, metallic, roughness=0.0):
    node = tree.nodes.new("ShaderNodeBsdfPrincipled")
    node.name = name
    node.inputs["Base Color"].default_value = color
    node.inputs["Metallic"].default_value = metallic
    node.inputs["Roughness"].default_value = roughness
    # The diffuse base has no dielectric reflection. The metallic overlays
    # remain real reflective BSDFs, with a uniform non-black world behind them.
    if not metallic:
        node.inputs["IOR"].default_value = 1.0
        node.inputs["Specular IOR Level"].default_value = 0.0
    return node


def _source_material(name, color, *, roughness=0.0):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.clear()
    shader = _principled(material.node_tree, "Source Metallic BSDF", color, metallic=1.0, roughness=roughness)
    shader.inputs["IOR"].default_value = 1.0
    output = material.node_tree.nodes.new("ShaderNodeOutputMaterial")
    output.is_active_output = True
    material.node_tree.links.new(shader.outputs[0], output.inputs["Surface"])
    return material, shader


def _copy_source_shader(tree, source, name):
    node = tree.nodes.new(source.bl_idname)
    node.name = name
    for target, original in zip(node.inputs, source.inputs):
        if hasattr(original, "default_value"):
            target.default_value = _value(original.default_value)
    return node.outputs[0]


class ShaderMixerEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = json.loads((ROOT / "node_library/manifest.json").read_text(encoding="utf-8"))
        bundle = next(item for item in manifest["bundles"] if item["id"] == "ring_mask")
        cls.asset_path = ROOT / bundle["path"]
        cls.asset_sha256 = _sha256(cls.asset_path)
        if cls.asset_sha256 != bundle["sha256"]:
            raise AssertionError("The saved Ring Mask does not match the canonical manifest.")
        with bpy.data.libraries.load(str(cls.asset_path), link=False) as (source, target):
            if "Ring Mask" not in source.node_groups:
                raise AssertionError("The canonical Ring Mask asset is missing.")
            target.node_groups = ["Ring Mask"]
        cls.ring_mask = target.node_groups[0]
        cls.ring_signature = _tree_signature(cls.ring_mask)

    def setUp(self):
        self.scene = bpy.data.scenes.new("Metal Ring A-B Fixture")
        self.scene.render.engine = "CYCLES"
        cycles = self.scene.cycles
        cycles.device = "CPU"
        cycles.samples = SAMPLES
        cycles.seed = 314159
        cycles.use_animated_seed = False
        cycles.use_adaptive_sampling = False
        cycles.use_denoising = False
        cycles.max_bounces = 2
        self.scene.render.threads_mode = "FIXED"
        self.scene.render.threads = 1
        self.scene.render.resolution_x = self.scene.render.resolution_y = RESOLUTION
        self.scene.render.resolution_percentage = 100
        self.scene.render.film_transparent = True
        self.scene.render.image_settings.file_format = "OPEN_EXR"
        self.scene.render.image_settings.color_mode = "RGBA"
        self.scene.render.image_settings.color_depth = "32"
        self.scene.view_settings.view_transform = "Standard"
        self.scene.view_settings.look = "None"
        world = bpy.data.worlds.new("Uniform Reflective World")
        world.use_nodes = True
        world.node_tree.nodes["Background"].inputs["Color"].default_value = (.6, .6, .6, 1)
        world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0
        self.scene.world = world
        camera_data = bpy.data.cameras.new("Fixed Ring Camera")
        camera = bpy.data.objects.new("Fixed Ring Camera", camera_data)
        self.scene.collection.objects.link(camera)
        camera.location = (0, 0, 4)
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = ORTHO_SCALE
        self.scene.camera = camera

        self.material = bpy.data.materials.new("Ring Comparison Target")
        self.material.use_nodes = True
        self.tree = self.material.node_tree
        self.tree.nodes.clear()
        self.output = self.tree.nodes.new("ShaderNodeOutputMaterial")
        self.output.is_active_output = True
        self.base = _principled(self.tree, "Teal Base", (.06, .55, .38, 1), metallic=0.0).outputs[0]
        self.sources = []
        # The thin-ring case retains the actual live Gold parameters. This is
        # intentionally separate from the lightweight near-mirror numeric cases.
        actual_gold = self._testMethodName == "test_two_disjoint_thin_gold_rings_hard_boundaries"
        gold_color = (.64, .38547, .19569, 1) if actual_gold else (.83, .56, .12, 1)
        gold_roughness = .3 if actual_gold else 0.0
        for name, color in (("Gold Source", gold_color),
                            ("Copper Source", (.72, .23, .07, 1))):
            source, shader = _source_material(name, color, roughness=gold_roughness if name == "Gold Source" else 0.0)
            self.sources.append((source, _tree_signature(source.node_tree), shader))
        self.gold = _copy_source_shader(self.tree, self.sources[0][2], "Shared Gold")
        self.copper = _copy_source_shader(self.tree, self.sources[1][2], "Shared Copper")

        mesh = bpy.data.meshes.new("One UV Plane")
        mesh.from_pydata([(-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0)],
                         [], [(0, 1, 2, 3)])
        uv = mesh.uv_layers.new(name="Ring UV")
        for loop in mesh.loops:
            co = mesh.vertices[loop.vertex_index].co
            uv.data[loop.index].uv = (co.x + .5, co.y + .5)
        mesh.materials.append(self.material)
        self.plane = bpy.data.objects.new("Same Surface In Both Renders", mesh)
        self.scene.collection.objects.link(self.plane)
        self.geometry_before = tuple(tuple(vertex.co) for vertex in mesh.vertices)
        self.uv_before = tuple(tuple(item.uv) for item in uv.data)
        self.original_scene = bpy.context.window.scene
        bpy.context.window.scene = self.scene

    def tearDown(self):
        self.assertEqual(_tree_signature(self.ring_mask), self.ring_signature)
        self.assertEqual(_sha256(self.asset_path), self.asset_sha256)
        for source, signature, _shader in self.sources:
            self.assertEqual(_tree_signature(source.node_tree), signature)
        self.assertEqual(tuple(tuple(vertex.co) for vertex in self.plane.data.vertices), self.geometry_before)
        self.assertEqual(tuple(tuple(item.uv) for item in self.plane.data.uv_layers.active.data), self.uv_before)
        bpy.context.window.scene = self.original_scene
        for obj in list(self.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.scenes.remove(self.scene)
        bpy.data.materials.remove(self.material)
        for source, _signature, _shader in self.sources:
            bpy.data.materials.remove(source)

    def _render(self, shader, filepath):
        self.tree.links.new(shader, self.output.inputs["Surface"])
        self.scene.render.filepath = str(filepath)
        bpy.ops.render.render(write_still=True, scene=self.scene.name)
        image = bpy.data.images.load(str(filepath), check_existing=False)
        try:
            self.assertEqual(tuple(image.size), (RESOLUTION, RESOLUTION))
            return list(image.pixels)
        finally:
            bpy.data.images.remove(image)

    def _compare(self, case_name, rings, overlays, *, manual_overlays=None):
        custom = mixer.add_mix_shaders(self.tree)
        self.tree.links.new(self.base, custom.inputs["Base Shader"])
        masks = []
        for number, (inner, width, softness) in enumerate(rings, start=1):
            node = self.tree.nodes.new("ShaderNodeGroup")
            node.node_tree = self.ring_mask
            node.inputs["Inner Radius"].default_value = inner
            node.inputs["Ring Width"].default_value = width
            node.inputs["Edge Softness"].default_value = softness
            masks.append(node.outputs["Mask"])
            self.tree.links.new(node.outputs["Mask"], custom.inputs["Mask {}".format(number)])
            self.tree.links.new(overlays[number - 1], custom.inputs["Shader {}".format(number)])
        # Slot 1 covers slot 2: compose lowest priority first, exactly as the
        # documented native mixer does. Shared-gold cases are also order neutral.
        manual = self.base
        manual_overlays = overlays if manual_overlays is None else manual_overlays
        for index in reversed(range(len(rings))):
            node = self.tree.nodes.new("ShaderNodeMixShader")
            self.tree.links.new(masks[index], node.inputs[0])
            self.tree.links.new(manual, node.inputs[1])
            self.tree.links.new(manual_overlays[index], node.inputs[2])
            manual = node.outputs[0]
        self.assertEqual(custom.inputs["Mask 2"].default_value, 0.0)
        protected = [(node, tuple((socket.identifier, _value(socket.default_value))
                                  for socket in node.inputs if hasattr(socket, "default_value")))
                     for node in self.tree.nodes if node.bl_idname in {"ShaderNodeBsdfPrincipled", "ShaderNodeGroup"}]
        temporary = tempfile.TemporaryDirectory(prefix="rr-metal-ring-edges-") if REPORT_DIR is None else None
        directory = Path(temporary.name) if temporary else REPORT_DIR
        directory.mkdir(parents=True, exist_ok=True)
        try:
            reference = self._render(manual, directory / (case_name + "-manual.exr"))
            actual = self._render(custom.outputs["Shader"], directory / (case_name + "-mixer.exr"))
            self.assertEqual(len(actual), len(reference))
            self.assertTrue(all(math.isfinite(value) for value in actual + reference), "Non-finite shader pixels.")
            errors = [abs(result - expected) for result, expected in zip(actual, reference)]
            boundary_indices = []
            footprint = 2 * ORTHO_SCALE / RESOLUTION
            for y in range(RESOLUTION):
                for x in range(RESOLUTION):
                    wx = ((x + .5) / RESOLUTION - .5) * ORTHO_SCALE
                    wy = ((y + .5) / RESOLUTION - .5) * ORTHO_SCALE
                    radius = 2 * math.hypot(wx, wy)
                    if any(abs(radius - boundary) <= footprint
                           for inner, width, _softness in rings for boundary in (inner, inner + width)):
                        boundary_indices.append((y * RESOLUTION + x) * 4)
            self.assertGreater(len(boundary_indices), 0)
            weights = (.2126, .7152, .0722)
            signed_edge = [sum((actual[index + channel] - reference[index + channel]) * weight
                               for channel, weight in enumerate(weights)) for index in boundary_indices]
            report = {
                "case": case_name,
                "passed": max(errors) <= ABSOLUTE_TOLERANCE,
                "engine": "CYCLES",
                "resolution": RESOLUTION,
                "samples": SAMPLES,
                "seed": 314159,
                "absolute_tolerance": ABSOLUTE_TOLERANCE,
                "channels_compared": len(errors),
                "max_absolute_error": max(errors),
                "mean_absolute_error": sum(errors) / len(errors),
                "edge_pixels_compared": len(boundary_indices),
                "mean_signed_edge_luminance_error": sum(signed_edge) / len(signed_edge),
                "minimum_signed_edge_luminance_error": min(signed_edge),
                "ring_mask_sha256": self.asset_sha256,
                "rings": rings,
                "source_gold_parameters": {socket.name: _value(socket.default_value)
                                           for socket in self.sources[0][2].inputs
                                           if socket.name in {"Base Color", "Metallic", "Roughness", "IOR", "Alpha"}},
                "manual_overlay_instances": [socket.node.name for socket in manual_overlays],
                "mixer_overlay_instances": [socket.node.name for socket in overlays],
                "source_materials_unchanged": all(_tree_signature(source.node_tree) == signature
                                                   for source, signature, _shader in self.sources),
            }
            REPORTS.append(report)
            if REPORT_DIR is not None:
                (directory / (case_name + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print("RR_SHADER_MIXER_EDGES_CASE " + json.dumps(report, sort_keys=True))
            worst = max(range(len(errors)), key=errors.__getitem__)
            self.assertLessEqual(max(errors), ABSOLUTE_TOLERANCE,
                                 "{} pixel {}, channel {}: native={} mixer={}; edge mean={}".format(
                                     case_name, worst // 4, worst % 4, reference[worst], actual[worst],
                                     report["mean_signed_edge_luminance_error"]))
            for node, defaults in protected:
                self.assertEqual(tuple((socket.identifier, _value(socket.default_value)) for socket in node.inputs
                                       if hasattr(socket, "default_value")), defaults)
        finally:
            if temporary:
                temporary.cleanup()

    def test_single_metal_ring_unused_second_slot(self):
        self._compare("single-metal-ring", ((.3, .01, 0.0),), (self.gold,))

    def test_two_disjoint_thin_gold_rings_hard_boundaries(self):
        # The real manual chain has two instances of the same Material Gold
        # group, while both mixer slots reuse the second instance. Keep that
        # graph difference instead of reducing this to one near-mirror BSDF.
        group = bpy.data.node_groups.new("Material Gold Edge Fixture", "ShaderNodeTree")
        group.interface.new_socket(name="Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
        copied_gold = _copy_source_shader(group, self.sources[0][2], "Actual Gold Parameters")
        group_output = group.nodes.new("NodeGroupOutput")
        group_output.is_active_output = True
        group.links.new(copied_gold, group_output.inputs["Shader"])
        instances = []
        for name in ("Manual Gold Instance 1", "Shared Gold Instance 2"):
            instance = self.tree.nodes.new("ShaderNodeGroup")
            instance.name = name
            instance.node_tree = group
            instances.append(instance.outputs["Shader"])
        self.assertAlmostEqual(self.sources[0][2].inputs["Roughness"].default_value, .3, places=6)
        self._compare("disjoint-hard-gold", ((.1, .01, 0.0), (.3, .01, 0.0)),
                      (instances[1], instances[1]), manual_overlays=tuple(instances))

    def test_two_gold_rings_soft_boundaries(self):
        self._compare("soft-gold", ((.1, .05, .012), (.3, .05, .012)), (self.gold, self.gold))

    def test_overlapping_distinct_metal_shaders_follow_slot_priority(self):
        self._compare("overlap-gold-copper", ((.24, .10, .02), (.28, .10, .02)), (self.gold, self.copper))


if __name__ == "__main__":
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError("Run only in an isolated --background --factory-startup scene.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("tests", nargs="*")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    REPORT_DIR = args.report_dir.resolve() if args.report_dir is not None else None
    suite = unittest.defaultTestLoader.loadTestsFromNames(args.tests, sys.modules[__name__]) if args.tests else \
        unittest.defaultTestLoader.loadTestsFromTestCase(ShaderMixerEdgeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {"passed": result.wasSuccessful(), "tests": result.testsRun,
               "engine": "CYCLES", "blender_version": bpy.app.version_string, "cases": REPORTS,
               "live_scene_edited": False, "blend_files_written": False}
    if REPORT_DIR is not None:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "shader-mixer-edges.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("RR_SHADER_MIXER_EDGES_PASS tests={}".format(result.testsRun) if result.wasSuccessful()
          else "RR_SHADER_MIXER_EDGES_FAIL tests={}".format(result.testsRun))
    raise SystemExit(0 if result.wasSuccessful() else 1)
