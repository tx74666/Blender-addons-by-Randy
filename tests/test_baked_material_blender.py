"""Independent baked materials; run serially in factory-startup Blender.

This script creates tiny 2x2 images and shader fixtures. It performs no bake,
loads no user blend, and registers no RR Helper UI or handlers.
"""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import bpy


MODULE = Path(__file__).resolve().parents[1] / "addons" / "random_realm_builder_exporter" / "rr_baked_material.py"
SPEC = importlib.util.spec_from_file_location("rr_baked_material", MODULE)
baked = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(baked)


def material_graph(material):
    return (
        material.name, material.use_nodes,
        tuple((node.name, node.bl_idname, node.label, node.parent.name if node.parent else "",
               tuple(node.location), node.select,
               tuple((socket.name, tuple(socket.default_value) if hasattr(socket.default_value, "__len__")
                      else socket.default_value) for socket in node.inputs if hasattr(socket, "default_value")))
              for node in material.node_tree.nodes),
        tuple((link.from_node.name, link.from_socket.identifier, link.to_node.name, link.to_socket.identifier)
              for link in material.node_tree.links),
        material.node_tree.nodes.active.name if material.node_tree.nodes.active else "",
    )


class BakedMaterialTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="rr_baked_material_")
        self.addCleanup(self.directory.cleanup)
        self.original_materials = set(bpy.data.materials)
        self.original_images = set(bpy.data.images)
        self.original_objects = set(bpy.data.objects)
        self.original_meshes = set(bpy.data.meshes)
        self.addCleanup(self.remove_fixtures)
        self.source = bpy.data.materials.new("Gold")
        self.source.use_nodes = True
        tree = self.source.node_tree
        principled = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfPrincipled")
        principled.inputs["Metallic"].default_value = 0.83
        principled.inputs["Roughness"].default_value = 0.27
        mix = tree.nodes.new("ShaderNodeMixShader")
        mix.inputs[0].default_value = 0.42
        tree.links.new(principled.outputs["BSDF"], mix.inputs[1])
        tree.links.new(principled.outputs["BSDF"], mix.inputs[2])
        output = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial")
        tree.links.new(mix.outputs[0], output.inputs["Surface"])
        self.objects = []
        for index in range(2):
            mesh = bpy.data.meshes.new(f"Source fixture {index}")
            mesh.materials.append(self.source)
            obj = bpy.data.objects.new(f"Source user {index}", mesh)
            bpy.context.scene.collection.objects.link(obj)
            self.objects.append(obj)
        self.images = {}
        for role in ("BaseColor", "Roughness", "Metallic", "Normal"):
            image = bpy.data.images.new(f"Saved {role}", width=2, height=2, alpha=True)
            image.colorspace_settings.name = "sRGB" if role == "BaseColor" else "Non-Color"
            image.pixels[:] = ([0.2, 0.4, 0.6, 1.0] if role == "BaseColor" else [0.5, 0.5, 1.0, 1.0]) * 4
            image.filepath_raw = str(Path(self.directory.name) / f"{role}.png")
            image.file_format = "PNG"
            image.save()
            self.images[role] = image

    def remove_fixtures(self):
        for collection, originals in ((bpy.data.objects, self.original_objects),
                                      (bpy.data.materials, self.original_materials),
                                      (bpy.data.meshes, self.original_meshes),
                                      (bpy.data.images, self.original_images)):
            for item in list(collection):
                if item not in originals:
                    collection.remove(item, do_unlink=True)

    def image_state(self):
        return {role: (image.name, image.colorspace_settings.name, image.filepath_raw,
                       image.file_format, image.is_dirty, tuple(image.pixels[:]),
                       bytes(image.packed_file.data) if image.packed_file else None)
                for role, image in self.images.items()}

    def test_clean_material_preserves_source_shared_images_and_object_slots(self):
        source_state = material_graph(self.source)
        images_state = self.image_state()
        source_users = self.source.users
        material = baked.create_baked_material(self.source, self.images, uv_map_name="BakeUV")
        self.assertEqual("Gold_baked", material.name)
        self.assertTrue(material.use_fake_user)
        self.assertEqual(self.source, material["rr_pbr_baked_source_material"])
        self.assertEqual("Gold", material["rr_pbr_baked_source_identity"])
        self.assertEqual("Gold", material["rr_pbr_baked_source_name"])
        self.assertEqual(list(self.images), json.loads(material["rr_pbr_baked_roles"]))
        self.assertEqual("preview_required", material["rr_pbr_baked_quality"])
        self.assertEqual(source_state, material_graph(self.source))
        self.assertEqual(images_state, self.image_state())
        # The persistent source ID reference keeps the original alive without
        # changing its shader graph, images or any object's material slot.
        self.assertEqual(source_users + 1, self.source.users)
        self.assertTrue(all(obj.material_slots[0].material == self.source for obj in self.objects))
        tree = material.node_tree
        principled = tree.nodes["Principled BSDF"]
        self.assertEqual("ShaderNodeBsdfPrincipled", principled.bl_idname)
        self.assertEqual(1.0, principled.inputs["Alpha"].default_value)
        self.assertFalse(principled.inputs["Alpha"].links)
        normal = tree.nodes["Normal Map"]
        self.assertEqual("TANGENT", normal.space)
        self.assertEqual("BakeUV", normal.uv_map)
        self.assertEqual("BakeUV", tree.nodes["Bake UV Map"].uv_map)
        self.assertEqual(normal, principled.inputs["Normal"].links[0].from_node)
        self.assertEqual(4, sum(node.bl_idname == "ShaderNodeTexImage" for node in tree.nodes))
        self.assertFalse(any(node.bl_idname in {"ShaderNodeGroup", "ShaderNodeMixShader"} for node in tree.nodes))

    def test_readable_collision_numbers_and_unicode_byte_limit(self):
        bpy.data.materials.new("Gold_baked")
        bpy.data.materials.new("Gold_baked_01")
        result = baked.create_baked_material(self.source, {"BaseColor": self.images["BaseColor"]})
        self.assertEqual("Gold_baked_02", result.name)
        self.source.name = "环形金属材料" * 8
        first = baked.create_baked_material(self.source, {"BaseColor": self.images["BaseColor"]})
        second = baked.create_baked_material(self.source, {"BaseColor": self.images["BaseColor"]})
        self.assertLessEqual(len(first.name.encode("utf-8")), 63)
        self.assertLessEqual(len(second.name.encode("utf-8")), 63)
        self.assertTrue(first.name.endswith("_baked"))
        self.assertTrue(second.name.endswith("_baked_01"))
        self.assertNotIn("\ufffd", first.name + second.name)

    def test_validation_creates_nothing_and_preserves_mismatched_colorspace(self):
        materials = set(bpy.data.materials)
        self.assertEqual(self.images, baked.validate_baked_images(self.images))
        self.assertEqual(materials, set(bpy.data.materials))
        normal = self.images["Normal"]
        normal.colorspace_settings.name = "sRGB"
        with self.assertRaisesRegex(ValueError, "Non-Color"):
            baked.create_baked_material(self.source, self.images)
        self.assertEqual("sRGB", normal.colorspace_settings.name)
        self.assertEqual(materials, set(bpy.data.materials))
        with self.assertRaises(ValueError):
            baked.create_baked_material(self.source, {"Normal": normal})

    def test_unsaved_dirty_target_is_rejected_without_creating_material(self):
        image = bpy.data.images.new("Unsaved target", width=2, height=2, alpha=True)
        image.colorspace_settings.name = "sRGB"
        image.pixels[:] = [0.3, 0.3, 0.3, 1.0] * 4
        materials = set(bpy.data.materials)
        with self.assertRaisesRegex(ValueError, "save or pack"):
            baked.create_baked_material(self.source, {"BaseColor": image})
        self.assertEqual(materials, set(bpy.data.materials))

    def test_packed_saved_result_is_valid_without_disk_file(self):
        image = self.images["BaseColor"]
        image.pack()
        Path(image.filepath_raw).unlink()
        images_state = self.image_state()
        material = baked.create_baked_material(self.source, {"BaseColor": image})
        self.assertEqual(image, material.node_tree.nodes["Base Color"].image)
        self.assertEqual(images_state, self.image_state())

    def test_corrupt_or_changed_size_file_is_rejected_before_creation(self):
        path = Path(self.images["BaseColor"].filepath_raw)
        original = path.read_bytes()
        materials = set(bpy.data.materials)
        for payload in (b"not PNG", original[:-4], original[:45] + bytes([original[45] ^ 1]) + original[46:]):
            with self.subTest(payload=payload[:12]):
                path.write_bytes(payload)
                with self.assertRaises(ValueError):
                    baked.create_baked_material(self.source, {"BaseColor": self.images["BaseColor"]})
                self.assertEqual(materials, set(bpy.data.materials))
        path.write_bytes(original)
        self.images["BaseColor"].scale(4, 4)
        with self.assertRaisesRegex(ValueError, "dimensions differ"):
            baked.create_baked_material(self.source, {"BaseColor": self.images["BaseColor"]})
        self.assertEqual(materials, set(bpy.data.materials))

    def test_construction_failure_removes_only_new_material(self):
        original = material_graph(self.source)
        materials = set(bpy.data.materials)
        images_state = self.image_state()
        def fail(material, images, uv_map_name, *_controls):
            material.use_nodes = True
            material.node_tree.nodes.new("ShaderNodeTexImage").image = images["BaseColor"]
            raise RuntimeError("injected node construction failure")
        with mock.patch.object(baked, "_populate_material", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                baked.create_baked_material(self.source, self.images)
        self.assertEqual(materials, set(bpy.data.materials))
        self.assertEqual(original, material_graph(self.source))
        self.assertEqual(images_state, self.image_state())


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BakedMaterialTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print("RR_BAKED_MATERIAL_REGRESSIONS_PASS")
