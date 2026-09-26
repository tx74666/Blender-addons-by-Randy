"""Image export regressions; run in an isolated Blender --factory-startup."""

import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addons"))
import random_realm_builder_exporter as rr
from random_realm_builder_exporter import rr_image_io


class ImageExportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rr_image_io_")
        self.addCleanup(self.directory.cleanup)
        self.images = []
        self.addCleanup(self.remove_images)

    def remove_images(self):
        for image in self.images:
            bpy.data.images.remove(image)

    def image(self, *, float_buffer=False, colorspace="sRGB"):
        image = bpy.data.images.new("ReviewImage", width=2, height=2, alpha=True, float_buffer=float_buffer)
        self.images.append(image)
        image.colorspace_settings.name = colorspace
        return image

    def load(self, path, colorspace="sRGB"):
        image = bpy.data.images.load(path, check_existing=False)
        self.images.append(image)
        image.colorspace_settings.name = colorspace
        return image

    def path(self, filename):
        return os.path.join(self.directory.name, filename)

    def export(self, image):
        warnings = []
        relative = rr.copy_image_for_manifest(
            "ReviewAsset", SimpleNamespace(name="Material"), "BaseColor", image,
            self.path("textures"), set(), warnings,
        )
        self.assertFalse(warnings)
        self.assertTrue(relative.endswith(".png"))
        return os.path.join(self.directory.name, relative)

    def assert_pixels(self, image, expected, tolerance=1.0 / 255):
        actual = list(image.pixels[:])
        self.assertEqual(len(actual), len(expected))
        for value, wanted in zip(actual, expected):
            self.assertAlmostEqual(value, wanted, delta=tolerance)

    def state(self, image):
        return (
            image.filepath_raw, image.file_format, image.source,
            image.colorspace_settings.name, image.alpha_mode, image.is_dirty,
            bytes(image.packed_file.data) if image.packed_file else None,
            tuple(image.pixels[:]),
        )

    def test_packed_edits_override_existing_disk_texture_without_changing_source(self):
        image = self.image()
        original = self.path("original.png")
        image.filepath_raw = original
        image.pixels[:] = [1, 0, 0, 1] * 4
        image.save()
        image.pixels[:] = [0, 1, 0, 1] * 4
        image.pack()
        before = self.state(image)
        result = self.export(image)
        self.assert_pixels(self.load(result), [0, 1, 0, 1] * 4)
        self.assert_pixels(self.load(original), [1, 0, 0, 1] * 4)
        self.assertEqual(self.state(image), before)

    def test_dirty_unpacked_edits_override_disk_and_remain_unsaved(self):
        image = self.image()
        image.filepath_raw = self.path("original.png")
        image.pixels[:] = [1, 0, 0, 1] * 4
        image.save()
        image.pixels[:] = [0, 0, 1, 1] * 4
        before = self.state(image)
        self.assertTrue(image.is_dirty)
        self.assert_pixels(self.load(self.export(image)), [0, 0, 1, 1] * 4)
        self.assertEqual(self.state(image), before)

    def test_generated_unsaved_image_exports_its_current_pixels(self):
        image = self.image()
        image.pixels[:] = [0.2, 0.4, 0.6, 0.5] * 4
        before = self.state(image)
        self.assert_pixels(self.load(self.export(image)), [0.2, 0.4, 0.6, 0.5] * 4)
        self.assertEqual(self.state(image), before)

    def test_png_preserves_byte_color_alpha_and_row_order(self):
        corners = [1, 0, 0, 1, 0, 1, 0, 0.75, 0, 0, 1, 0.5, 0.2, 0.4, 0.6, 0.25]
        for colorspace in ("sRGB", "Non-Color"):
            with self.subTest(colorspace=colorspace):
                image = self.image(colorspace=colorspace)
                image.pixels[:] = corners
                before = self.state(image)
                output = self.path(colorspace + ".png")
                rr.write_image_pixels_to_png(image, output)
                self.assert_pixels(self.load(output, colorspace), corners)
                self.assertEqual(self.state(image), before)

    def test_float_image_uses_native_precision_without_modifying_source(self):
        for colorspace in ("sRGB", "Non-Color"):
            with self.subTest(colorspace=colorspace):
                image = self.image(float_buffer=True, colorspace=colorspace)
                image.pixels[:] = [0.12345, 0.34567, 0.67891, 1] * 4
                before = self.state(image)
                expected_path = self.path(colorspace + "-native.png")
                image.save(filepath=expected_path, save_copy=True)
                output = self.path(colorspace + "-rr.png")
                rr.write_image_pixels_to_png(image, output)
                self.assertEqual(self.state(image), before)
                self.assert_pixels(self.load(output, colorspace), list(self.load(expected_path, colorspace).pixels[:]), 1e-6)

    def test_packed_jpeg_is_exported_with_png_extension_and_original_format_preserved(self):
        generated = self.image()
        generated.pixels[:] = [1, 0, 0, 1] * 4
        original = self.path("original.jpg")
        scene = bpy.context.scene
        original_format = scene.render.image_settings.file_format
        try:
            scene.render.image_settings.file_format = "JPEG"
            generated.save_render(original, scene=scene)
        finally:
            scene.render.image_settings.file_format = original_format
        image = self.load(original)
        image.pixels[:] = [0, 1, 0, 1] * 4
        image.pack()
        before = self.state(image)
        output = self.export(image)
        self.assertEqual(Path(output).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assert_pixels(self.load(output), [0, 1, 0, 1] * 4)
        self.assertEqual(self.state(image), before)

    def test_failed_save_preserves_existing_output_and_cleans_temporary(self):
        output = self.path("keep.png")
        Path(output).write_bytes(b"previous output")

        def fail(**kwargs):
            Path(kwargs["filepath"]).write_bytes(b"incomplete")
            raise RuntimeError("encoder failed")

        image = SimpleNamespace(
            name="Failure", filepath_raw="//original.exr", file_format="OPEN_EXR", save=fail,
        )
        with self.assertRaisesRegex(RuntimeError, "encoder failed"):
            rr_image_io.save_png_copy(image, output)
        self.assertEqual(image.filepath_raw, "//original.exr")
        self.assertEqual(image.file_format, "OPEN_EXR")
        self.assertEqual(Path(output).read_bytes(), b"previous output")
        self.assertEqual(os.listdir(self.directory.name), ["keep.png"])

    def test_failed_publish_preserves_output_and_source(self):
        image = self.image()
        image.pixels[:] = [0.2, 0.4, 0.6, 1] * 4
        before = self.state(image)
        output = self.path("keep.png")
        Path(output).write_bytes(b"previous output")
        with mock.patch.object(rr_image_io.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaisesRegex(PermissionError, "locked"):
                rr_image_io.save_png_copy(image, output)
        self.assertEqual(self.state(image), before)
        self.assertEqual(Path(output).read_bytes(), b"previous output")
        self.assertEqual(os.listdir(self.directory.name), ["keep.png"])

    def test_pbr_success_updates_destination_and_failure_preserves_empty_path(self):
        image = self.image()
        image.pixels[:] = [0.2, 0.4, 0.6, 1] * 4
        role = rr.PBR_BAKE_ROLES[0]
        material = SimpleNamespace(name="ReviewMaterial")
        with mock.patch.object(rr_image_io, "save_png_copy", side_effect=RuntimeError("encoder failed")):
            with self.assertRaisesRegex(RuntimeError, "encoder failed"):
                rr.save_pbr_framework_image(material, role, image, self.directory.name)
        self.assertEqual(image.filepath_raw, "")
        output = rr.save_pbr_framework_image(material, role, image, self.directory.name)
        self.assertEqual(image.filepath_raw, output)
        self.assert_pixels(self.load(output), [0.2, 0.4, 0.6, 1] * 4)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ImageExportTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("RR_IMAGE_IO_REGRESSIONS_PASS")
