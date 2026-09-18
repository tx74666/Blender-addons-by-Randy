"""Saved icon HDRI snapshots and real Blender World/render ownership contracts."""

import importlib.util
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import bpy
from mathutils import Vector


SOURCE = Path(__file__).resolve().parents[1] / "addons/random_realm_builder_exporter/rr_icon_lighting.py"
SPEC = importlib.util.spec_from_file_location("rr_icon_lighting_contract", SOURCE)
lighting = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lighting)


def forest():
    return next(studio for studio in bpy.context.preferences.studio_lights if studio.name == "forest.exr")


def profile(**changes):
    result = {
        "version": 1,
        "mode": "HDRI",
        "studio_name": "forest.exr",
        "hdri_path": forest().path,
        "rotation_z": 1.0698868,
        "intensity": 0.8,
        "world_space": True,
        "sun_threshold": 8.0,
    }
    result.update(changes)
    return result


def area(width, height, angle=0.2, *, mode="MATERIAL"):
    shading = SimpleNamespace(
        type=mode,
        use_scene_world=False,
        use_scene_lights=False,
        use_studiolight_view_rotation=True,
        selected_studio_light=forest(),
        studiolight_intensity=0.8,
        studiolight_rotate_z=angle,
    )
    return SimpleNamespace(type="VIEW_3D", width=width, height=height, spaces=SimpleNamespace(active=SimpleNamespace(shading=shading)))


class ProfileContracts(unittest.TestCase):
    def setUp(self):
        self.scene = bpy.data.scenes.new("RR_IconLightingContract")
        self.original_world = bpy.data.worlds.new("RR_OriginalWorld")
        self.original_world.use_nodes = True
        self.original_world.sun_threshold = 10.0
        self.scene.world = self.original_world
        self.root = bpy.data.objects.new("RR_ProfileOwner", None)
        self.scene.collection.objects.link(self.root)

    def tearDown(self):
        bpy.data.objects.remove(self.root, do_unlink=True)
        bpy.data.scenes.remove(self.scene)
        bpy.data.worlds.remove(self.original_world)

    def context(self, active=None, areas=()):
        return SimpleNamespace(area=active, scene=self.scene, screen=SimpleNamespace(areas=areas), window_manager=SimpleNamespace(windows=[]))

    def test_legacy_scene_and_asset_override(self):
        self.assertEqual(lighting.read_profile(self.root, self.scene)["mode"], "LEGACY")
        lighting.write_profile(self.scene, profile())
        self.assertEqual(lighting.read_profile(self.root, self.scene)["mode"], "HDRI")
        lighting.write_profile(self.root, {"version": 1, "mode": "LEGACY"})
        self.assertEqual(lighting.read_profile(self.root, self.scene)["mode"], "LEGACY")

    def test_headless_saved_snapshot_is_detached_from_input_and_view(self):
        material_view = area(100, 100, 0.7)
        captured = lighting.capture_profile(self.context(material_view))
        lighting.write_profile(self.root, captured)
        material_view.spaces.active.shading.studiolight_rotate_z = -1.0
        captured["intensity"] = 999.0
        result = lighting.read_profile(self.root, self.scene)
        self.assertEqual(result["rotation_z"], 0.7)
        self.assertEqual(result["intensity"], 0.8)
        result["rotation_z"] = -2.0
        self.assertEqual(lighting.read_profile(self.root, self.scene)["rotation_z"], 0.7)

    def test_active_material_precedes_largest_and_otherwise_largest_is_used(self):
        small = area(10, 10, 0.1)
        large = area(100, 100, 0.9)
        self.assertEqual(lighting.capture_profile(self.context(small, [large]))["rotation_z"], 0.1)
        solid = area(200, 200, mode="SOLID")
        self.assertEqual(lighting.capture_profile(self.context(solid, [small, large, solid]))["rotation_z"], 0.9)

    def test_capture_uses_viewport_world_threshold_and_intensity(self):
        captured = lighting.capture_profile(self.context(area(100, 100)))
        self.assertEqual(captured["sun_threshold"], 8.0)
        self.assertEqual(captured["hdri_path"], str(Path(forest().path)))

    def test_missing_or_unsupported_view_rejected_without_scene_write(self):
        for option in ("none", "use_scene_world", "use_scene_lights", "view_space"):
            with self.subTest(option=option):
                view = area(10, 10)
                if option == "view_space":
                    view.spaces.active.shading.use_studiolight_view_rotation = False
                elif option != "none":
                    setattr(view.spaces.active.shading, option, True)
                with self.assertRaises(lighting.ProfileError):
                    lighting.capture_profile(self.context(None if option == "none" else view))
                self.assertNotIn(lighting.PROFILE_KEY, self.scene)

    def test_invalid_snapshots_never_replace_existing_profile(self):
        lighting.write_profile(self.root, profile())
        before = self.root[lighting.PROFILE_KEY]
        invalid = [None, [], {}, profile(version=True), profile(version=2), profile(mode="OTHER"),
                   profile(intensity=math.nan), profile(intensity=math.inf), profile(intensity=-1),
                   profile(intensity=True), profile(rotation_z=4), profile(sun_threshold=-1),
                   profile(world_space=False), profile(hdri_path="relative.exr"), profile(studio_name="")]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(lighting.ProfileError):
                    lighting.write_profile(self.root, value)
                self.assertEqual(self.root[lighting.PROFILE_KEY], before)

    def test_corrupt_explicit_asset_does_not_fall_through_to_scene_default(self):
        lighting.write_profile(self.scene, profile())
        for invalid in ("broken JSON", "[]", '{"version":1,"mode":"HDRI"}'):
            self.root[lighting.PROFILE_KEY] = invalid
            with self.assertRaises(lighting.ProfileError):
                lighting.read_profile(self.root, self.scene)

    def test_missing_hdri_rejected_before_world_or_image_allocation(self):
        with tempfile.TemporaryDirectory(prefix="rr_icon_lighting_") as temp:
            invalid = profile(hdri_path=str(Path(temp) / "missing.exr"))
            before = (set(bpy.data.worlds), set(bpy.data.images), dict(self.scene.items()))
            with self.assertRaises(lighting.ProfileError):
                with lighting.temporary_world(self.scene, invalid):
                    self.fail("Missing HDRI must not enter the render context")
            self.assertEqual(before, (set(bpy.data.worlds), set(bpy.data.images), dict(self.scene.items())))
            self.assertEqual(self.scene.world, self.original_world)

    def test_profile_can_be_inspected_without_existing_image(self):
        value = profile(hdri_path=str(Path(tempfile.gettempdir()) / "rr_missing_profile_only.exr"))
        self.root[lighting.PROFILE_KEY] = json.dumps(value)
        self.assertEqual(lighting.read_profile(self.root, check_file=False)["mode"], "HDRI")

    def test_temporary_world_is_linear_positive_rotation_and_has_no_volume(self):
        self.original_world.node_tree.nodes.new("ShaderNodeVolumePrincipled")
        before = (set(bpy.data.worlds), set(bpy.data.images))
        with lighting.temporary_world(self.scene, profile()) as world:
            self.assertEqual(self.scene.world, world)
            self.assertNotEqual(world, self.original_world)
            self.assertAlmostEqual(world.sun_threshold, 8.0)
            nodes = world.node_tree.nodes
            rotate = next(node for node in nodes if node.type == "VECTOR_ROTATE")
            env = next(node for node in nodes if node.type == "TEX_ENVIRONMENT")
            bg = next(node for node in nodes if node.type == "BACKGROUND")
            output = next(node for node in nodes if node.type == "OUTPUT_WORLD")
            self.assertAlmostEqual(rotate.inputs["Angle"].default_value, 1.0698868, places=6)
            self.assertEqual(rotate.rotation_type, "Z_AXIS")
            self.assertAlmostEqual(bg.inputs["Strength"].default_value, 0.8)
            self.assertTrue(env.image.is_float)
            self.assertEqual(env.image.alpha_mode, "NONE")
            self.assertIn("Linear", env.image.colorspace_settings.name)
            self.assertFalse(output.inputs["Volume"].is_linked)
        self.assertEqual(self.scene.world, self.original_world)
        self.assertEqual(before, (set(bpy.data.worlds), set(bpy.data.images)))

    def test_body_exception_restores_world_and_preserves_existing_user_image(self):
        user_image = bpy.data.images.load(forest().path, check_existing=False)
        user_image.alpha_mode = "STRAIGHT"
        before = (set(bpy.data.worlds), set(bpy.data.images))
        try:
            with self.assertRaisesRegex(RuntimeError, "simulated render failure"):
                with lighting.temporary_world(self.scene, profile()):
                    raise RuntimeError("simulated render failure")
            self.assertEqual(self.scene.world, self.original_world)
            self.assertEqual(before, (set(bpy.data.worlds), set(bpy.data.images)))
            self.assertEqual(user_image.alpha_mode, "STRAIGHT")
        finally:
            bpy.data.images.remove(user_image)

    def test_corrupt_image_load_failure_leaves_source_world_intact(self):
        with tempfile.TemporaryDirectory(prefix="rr_icon_lighting_") as temp:
            corrupt = Path(temp) / "bad.exr"
            corrupt.write_text("not an image")
            before = (set(bpy.data.worlds), set(bpy.data.images))
            with self.assertRaises(RuntimeError):
                with lighting.temporary_world(self.scene, profile(hdri_path=str(corrupt))):
                    self.fail("Invalid image must not enter the render context")
            self.assertEqual(self.scene.world, self.original_world)
            self.assertEqual(before, (set(bpy.data.worlds), set(bpy.data.images)))

    def test_none_original_world_and_nested_context_restore_correctly(self):
        self.scene.world = None
        with lighting.temporary_world(self.scene, profile()) as outer:
            with lighting.temporary_world(self.scene, profile(rotation_z=0.0)):
                self.assertNotEqual(self.scene.world, outer)
            self.assertEqual(self.scene.world, outer)
        self.assertIsNone(self.scene.world)

    def test_legacy_context_does_not_touch_world_or_images(self):
        before = (set(bpy.data.worlds), set(bpy.data.images))
        with lighting.temporary_world(self.scene, {"version": 1, "mode": "LEGACY"}) as world:
            self.assertIsNone(world)
            self.assertEqual(self.scene.world, self.original_world)
        self.assertEqual(before, (set(bpy.data.worlds), set(bpy.data.images)))

    def test_actual_eevee_render_produces_transparent_lit_icon_and_restores_world(self):
        data = bpy.data.meshes.new("RR_RenderProbeMesh")
        data.from_pydata([(-1, 0, -1), (1, 0, -1), (1, 0, 1), (-1, 0, 1)], [], [(0, 1, 2, 3)])
        mesh = bpy.data.objects.new("RR_RenderProbe", data)
        self.scene.collection.objects.link(mesh)
        material = bpy.data.materials.new("RR_RenderProbeMaterial")
        material.use_nodes = True
        material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = (0.5, 0.2, 0.05, 1)
        data.materials.append(material)
        camera_data = bpy.data.cameras.new("RR_RenderProbeCamera")
        camera = bpy.data.objects.new("RR_RenderProbeCamera", camera_data)
        self.scene.collection.objects.link(camera)
        camera.location = (0, -5, 0)
        camera.rotation_euler = (Vector((0, 0, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
        camera_data.type = "ORTHO"
        camera_data.ortho_scale = 4
        self.scene.camera = camera
        self.scene.render.engine = "BLENDER_EEVEE"
        self.scene.eevee.taa_render_samples = 8
        self.scene.render.resolution_x = self.scene.render.resolution_y = 64
        self.scene.render.resolution_percentage = 100
        self.scene.render.film_transparent = True
        self.scene.render.image_settings.file_format = "PNG"
        self.scene.render.image_settings.color_mode = "RGBA"
        self.scene.render.image_settings.color_depth = "8"
        try:
            with tempfile.TemporaryDirectory(prefix="rr_icon_lighting_render_") as temp:
                output = Path(temp) / "icon.png"
                self.scene.render.filepath = str(output)
                with lighting.temporary_world(self.scene, profile()):
                    bpy.ops.render.render(write_still=True, scene=self.scene.name)
                self.assertEqual(self.scene.world, self.original_world)
                self.assertTrue(output.is_file())
                result = bpy.data.images.load(str(output), check_existing=False)
                try:
                    pixels = list(result.pixels)
                    alpha = pixels[3::4]
                    self.assertEqual(tuple(result.size), (64, 64))
                    self.assertLess(min(alpha), 0.01)
                    self.assertGreater(max(alpha), 0.99)
                    self.assertGreater(max(pixels[(32 * 64 + 32) * 4:(32 * 64 + 32) * 4 + 3]), 0.05)
                finally:
                    bpy.data.images.remove(result)
        finally:
            bpy.data.objects.remove(camera, do_unlink=True)
            bpy.data.cameras.remove(camera_data)
            bpy.data.objects.remove(mesh, do_unlink=True)
            bpy.data.meshes.remove(data)
            bpy.data.materials.remove(material)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProfileContracts)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
