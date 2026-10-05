"""Blender integration contracts; creates isolated scenes via context overrides.

This file never saves/opens the user's Main file or assigns window.scene.
Persistence tests write/read only an owned Scene's dependency closure. Run
explicitly in Blender; ordinary Python skips the Blender-only fixture.
"""

import argparse
import copy
from contextlib import contextmanager, ExitStack
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

try:
    import bpy
    from mathutils import Matrix
except ImportError:
    bpy = None
    Matrix = None

_test_directory = str(Path(__file__).resolve().parent)
_fixture_spec = importlib.util.spec_from_file_location(
    "_rr_unity_layout_validation_" + uuid.uuid4().hex,
    Path(_test_directory) / "test_unity_device_layout_validation.py")
_fixture = importlib.util.module_from_spec(_fixture_spec)
_fixture_spec.loader.exec_module(_fixture)
IDENTITY, PNG, package, layout = _fixture.IDENTITY, _fixture.PNG, _fixture.package, _fixture.layout
RUNTIME_MODULE_PATH = _fixture.RUNTIME_MODULE_PATH
RUNTIME_ADDON_DIRECTORY = _fixture.RUNTIME_ADDON_DIRECTORY


OUTPUT_OWNED_DIR = None
SNAPSHOTS = []


def candidate_helper():
    """Load sibling production __init__.py, or the explicit staging candidate."""
    if RUNTIME_ADDON_DIRECTORY is not None:
        dependencies = RUNTIME_ADDON_DIRECTORY
        source = dependencies / "__init__.py"
        package_paths = [str(dependencies)]
    else:
        source = Path(_test_directory) / "rr_helper_candidate.py"
        dependencies = next((parent / "Tools/AssetPipeline/Blender/addons/random_realm_builder_exporter"
                             for parent in Path(__file__).resolve().parents
                             if (parent / "Tools/AssetPipeline/Blender/addons/random_realm_builder_exporter").is_dir()), None)
        package_paths = None
    if not source.is_file() or dependencies is None or not dependencies.is_dir():
        raise RuntimeError("The selected RR Helper source and its dependency directory must exist.")
    name = "_rr_unity_layout_helper_contract_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, source, submodule_search_locations=package_paths)
    module = importlib.util.module_from_spec(spec)
    added = [str(path) for path in (Path(_test_directory), dependencies) if str(path) not in sys.path]
    sys.path[:0] = added
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    finally:
        for path in added:
            sys.path.remove(path)
    return module


@unittest.skipIf(bpy is None, "This fixture requires Blender's bpy runtime")
class UnityDeviceLayoutBlenderTests(unittest.TestCase):
    TEST_PROP = "rr_unity_layout_unittest_owner"

    def setUp(self):
        self.token = str(uuid.uuid4())
        self.reference_ids = {"reference-" + self.token}
        self.original_scene = bpy.context.scene
        self.scene = None
        if OUTPUT_OWNED_DIR is None:
            self.directory = tempfile.TemporaryDirectory(prefix="rr-layout-blender-contract-")
            self.addCleanup(self.directory.cleanup)
            self.base = Path(self.directory.name)
        else:
            self.base = OUTPUT_OWNED_DIR / self.token
            self.base.mkdir()
        self.addCleanup(self.cleanup_scene)
        (self.base / "screen.png").write_bytes(PNG)
        self.scene = bpy.data.scenes.new("RR Layout Test " + self.token)
        self.scene[self.TEST_PROP] = self.token
        self.view_layer = self.scene.view_layers[0]
        self.anchor = self.artist_object("Core")
        self.payload = package()
        self.payload["reference"]["id"] = next(iter(self.reference_ids))
        self.payload["reference"]["name"] = "Identical visible Core name"
        self.payload["devices"][0]["id"] = "device-" + self.token

    def cleanup_scene(self):
        # Cleanup only IDs minted by this fixture, including failed-import staging
        # resources. Never use a scene switch or a global orphan purge.
        try:
            for obj in list(bpy.data.objects):
                if self.ours(obj):
                    bpy.data.objects.remove(obj, do_unlink=True)
            for collection in list(bpy.data.collections):
                if self.ours(collection):
                    bpy.data.collections.remove(collection, do_unlink=True)
            for values in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
                for value in list(values):
                    if self.ours(value) and value.users == 0:
                        values.remove(value)
        finally:
            for scene in list(bpy.data.scenes):
                if scene.get(self.TEST_PROP) == self.token:
                    bpy.data.scenes.remove(scene)
            self.assertIs(bpy.context.scene, self.original_scene,
                          "The import must never switch the user's active scene.")

    def ours(self, value):
        return value.get(self.TEST_PROP) == self.token or (
            value.get(layout.OWNER_PROP) == layout.OWNER and
            value.get(layout.REFERENCE_ID_PROP) in self.reference_ids)

    @contextmanager
    def context(self):
        with bpy.context.temp_override(scene=self.scene, view_layer=self.view_layer):
            yield

    def artist_object(self, name, data=None, collection=None):
        obj = bpy.data.objects.new(name + " " + self.token, data)
        obj[self.TEST_PROP] = self.token
        (collection or self.scene.collection).objects.link(obj)
        return obj

    def do_import(self, payload=None):
        with self.context():
            result = layout.import_package(self.payload if payload is None else payload, self.anchor, self.base)
            self.view_layer.update()
            collection = result["collection"]
            SNAPSHOTS.append({
                "test": self.id(), "reference_id": result["reference_id"],
                "snapshot_id": result["snapshot_id"], "device_count": result["device_count"],
                "mesh_count": result["mesh_count"], "collection": collection.name,
                "root_count": len(self.managed(collection, "ROOT")),
                "part_count": len(self.managed(collection, "PART")),
                "screen_count": len(self.managed(collection, "SCREEN")),
                "object_count": len(collection.all_objects), "warnings": result["warnings"],
                "artifact_directory": str(self.base),
            })
        return result

    def managed(self, collection, role=None, device_id=None):
        return [obj for obj in collection.all_objects
                if obj.get(layout.OWNER_PROP) == layout.OWNER and
                (role is None or obj.get(layout.ROLE_PROP) == role) and
                (device_id is None or obj.get(layout.DEVICE_ID_PROP) == device_id)]

    def one(self, collection, role, device_id=None):
        objects = self.managed(collection, role, device_id)
        self.assertEqual(len(objects), 1, (role, device_id))
        return objects[0]

    def assert_matrix(self, actual, expected):
        for row in range(4):
            for column in range(4):
                self.assertAlmostEqual(actual[row][column], expected[row][column], places=5,
                                       msg=f"matrix[{row}][{column}]")

    def resources(self):
        return {name: {value.as_pointer() for value in values if self.ours(value)}
                for name, values in (("objects", bpy.data.objects), ("collections", bpy.data.collections),
                                     ("meshes", bpy.data.meshes), ("materials", bpy.data.materials),
                                     ("images", bpy.data.images))}

    def screen(self):
        self.payload["devices"][0]["screenPreview"] = {
            "path": "screen.png", "relativeMatrix": IDENTITY.copy(), "width": 2.0, "height": 1.0,
        }

    def test_shared_mesh_geometry_uv_material_overrides_and_reference_flags(self):
        second = copy.deepcopy(self.payload["devices"][0])
        second["id"] = "second-" + self.token
        second["name"] = "Another device"
        self.payload["devices"].append(second)
        material = copy.deepcopy(self.payload["materials"][0])
        material["id"] = "material-002"
        material["color"] = [0.8, 0.2, 0.1, 1.0]
        material["emission"] = [0.1, 0.2, 0.3]
        self.payload["materials"].append(material)
        second["parts"][0]["materialIds"] = ["material-002", "material-002"]
        self.screen()

        result = self.do_import()
        collection = result["collection"]
        self.assertEqual(result["reference_id"], self.payload["reference"]["id"])
        self.assertEqual(result["device_count"], 2)
        self.assertEqual(result["mesh_count"], 1)
        self.assertEqual(result["snapshot_id"], self.payload["snapshotId"])
        parts = self.managed(collection, "PART")
        self.assertEqual(len(parts), 2)
        self.assertIs(parts[0].data, parts[1].data, "Deduplicated mesh must have a single Blender datablock.")
        for part in parts:
            self.assertTrue(all(slot.link == "OBJECT" for slot in part.material_slots))
        first = self.one(collection, "PART", self.payload["devices"][0]["id"])
        other = self.one(collection, "PART", second["id"])
        self.assertIsNot(first.material_slots[0].material, other.material_slots[0].material)
        for actual, expected in zip(first.material_slots[0].material.diffuse_color, [0.3, 0.4, 0.5, 1.0]):
            self.assertAlmostEqual(actual, expected, places=5)
        for actual, expected in zip(other.material_slots[0].material.diffuse_color, [0.8, 0.2, 0.1, 1.0]):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertEqual(len(first.material_slots), 2, "Extra renderer material slots must remain usable.")
        self.assertIs(other.material_slots[0].material, other.material_slots[1].material)
        for actual, expected in zip(first.material_slots[1].material.diffuse_color, [0.65, 0.65, 0.65, 1.0]):
            self.assertAlmostEqual(actual, expected, places=5)
        self.assertEqual(tuple(first.data.polygons[0].vertices), (0, 2, 1))
        expected_uv = [(0.0, 0.0), (0.0, 1.0), (1.0, 0.0)]
        for actual, expected in zip(first.data.uv_layers.active.data, expected_uv):
            self.assertEqual(tuple(actual.uv), expected)
        self.assertTrue(layout.is_unity_layout_reference(collection))
        for obj in collection.all_objects:
            self.assertTrue(layout.is_unity_layout_reference(obj))
            self.assertTrue(obj.hide_select)
            if obj.data is not None:
                self.assertTrue(layout.is_unity_layout_reference(obj.data))
            for slot in obj.material_slots:
                self.assertTrue(layout.is_unity_layout_reference(slot.material))
        preview = self.one(collection, "SCREEN")
        self.assertEqual(len(preview.data.vertices), 4)
        self.assertEqual(len(preview.data.polygons), 2)
        self.assertEqual(len(preview.data.uv_layers.active.data), 6)
        textures = [node.image for node in preview.material_slots[0].material.node_tree.nodes if node.type == "TEX_IMAGE"]
        self.assertEqual(len(textures), 1)
        self.assertTrue(layout.is_unity_layout_reference(textures[0]))
        self.assertIsNotNone(textures[0].packed_file)

    def test_core_anchor_unit_scale_and_parent_inverse_keep_shear_and_local_geometry(self):
        self.scene.unit_settings.system = "METRIC"
        self.scene.unit_settings.scale_length = 0.5
        parent = self.artist_object("Nonuniform Core parent")
        parent.scale = (2.0, 1.0, 3.0)
        self.anchor.parent = parent
        self.anchor.rotation_euler = (0.0, 0.0, 0.4)
        self.anchor.location = (10.0, 20.0, 30.0)
        self.payload["devices"][0]["relativeMatrix"] = [
            1.0, 0.3, 0.0, 1.0, 0.0, 1.0, 0.0, 2.0,
            0.0, 0.0, 1.0, 3.0, 0.0, 0.0, 0.0, 1.0,
        ]
        self.payload["devices"][0]["parts"][0]["relativeMatrix"][11] = 0.25
        result = self.do_import()
        root = self.one(result["collection"], "ROOT")
        part = self.one(result["collection"], "PART")
        relative = Matrix(((1.0, 0.0, -0.3, -2.0), (0.0, 1.0, 0.0, -6.0),
                           (0.0, 0.0, 1.0, 4.0), (0.0, 0.0, 0.0, 1.0)))
        part_relative = Matrix.Translation((0.0, -0.5, 0.0))
        self.assertIs(root.parent, self.anchor)
        self.assert_matrix(root.matrix_parent_inverse, relative)
        self.assert_matrix(root.matrix_basis, Matrix.Identity(4))
        self.assert_matrix(root.matrix_world, self.anchor.matrix_world @ relative)
        self.assert_matrix(part.matrix_world, self.anchor.matrix_world @ relative @ part_relative)
        self.assertEqual(tuple(part.data.vertices[1].co), (-2.0, 0.0, 0.0),
                         "Device and part transforms must not be baked again into local geometry.")

    def test_renamed_collection_updates_by_reference_id_and_keeps_device_root_identity(self):
        first = self.do_import()
        collection = first["collection"]
        root = self.one(collection, "ROOT")
        root_pointer = root.as_pointer()
        collection.name = "Artist renamed collection " + self.token
        artist = self.artist_object("Unrelated artist object", collection=collection)
        part = self.one(collection, "PART")
        root_child = self.artist_object("Artist root child")
        root_child.parent = root
        root_child.location = (1.0, 2.0, 3.0)
        part_child = self.artist_object("Artist part child")
        part_child.parent = part
        part_child.location = (0.4, 0.5, 0.6)
        with self.context():
            self.view_layer.update()
            root_child_world = root_child.matrix_world.copy()
            part_child_world = part_child.matrix_world.copy()
            root_child_basis = root_child.matrix_basis.copy()
            part_child_basis = part_child.matrix_basis.copy()
            root.hide_select = False
            root.select_set(True)
            self.view_layer.objects.active = root
        updated = copy.deepcopy(self.payload)
        updated["reference"]["name"] = "Changed display name"
        updated["devices"][0]["relativeMatrix"][3] = 2.0
        second = self.do_import(updated)
        self.assertIs(second["collection"], collection)
        self.assertEqual(self.one(collection, "ROOT").as_pointer(), root_pointer)
        self.assertIs(collection.objects.get(artist.name), artist)
        self.assertAlmostEqual(root.matrix_parent_inverse[0][3], -2.0)
        self.assertIs(root_child.parent, root)
        self.assertIs(part_child.parent, self.one(collection, "PART"))
        self.assert_matrix(root_child.matrix_world, root_child_world)
        self.assert_matrix(part_child.matrix_world, part_child_world)
        self.assert_matrix(root_child.matrix_basis, root_child_basis)
        self.assert_matrix(part_child.matrix_basis, part_child_basis)
        with self.context():
            self.assertTrue(root.select_get())
            self.assertIs(self.view_layer.objects.active, root)
        # Same display name with a distinct persistent identity is a separate layout.
        other = copy.deepcopy(updated)
        other["reference"]["id"] = "other-reference-" + self.token
        self.reference_ids.add(other["reference"]["id"])
        third = self.do_import(other)
        self.assertIsNot(third["collection"], collection)
        self.assertEqual(len(self.managed(collection, "ROOT")), 1)

    def test_removed_device_keeps_artist_children_world_selection_and_shared_mesh(self):
        first = self.do_import()
        collection = first["collection"]
        root = self.one(collection, "ROOT")
        part = self.one(collection, "PART")
        child = self.artist_object("Artist child")
        child.parent = part
        child.location = (0.4, 0.5, 0.6)
        artist = self.artist_object("Artist shared mesh", data=part.data, collection=collection)
        old_mesh = part.data
        with self.context():
            self.view_layer.update()
            child_world = child.matrix_world.copy()
            artist.select_set(True)
            self.view_layer.objects.active = artist
        replacement = copy.deepcopy(self.payload)
        replacement["devices"][0]["id"] = "replacement-device-" + self.token
        result = self.do_import(replacement)
        self.assertIs(result["collection"], collection)
        self.assertIs(artist.data, old_mesh, "Shared data used by artist objects must survive cleanup.")
        self.assertIs(collection.objects.get(artist.name), artist)
        self.assertIs(child.parent, self.anchor)
        self.assert_matrix(child.matrix_world, child_world)
        self.assertEqual(len(self.managed(collection, "ROOT")), 1)
        self.assertEqual(self.one(collection, "ROOT").get(layout.DEVICE_ID_PROP), replacement["devices"][0]["id"])
        with self.context():
            self.assertTrue(artist.select_get())
            self.assertIs(self.view_layer.objects.active, artist)

    def test_invalid_matrix_or_missing_png_preserves_previous_layout_and_resources(self):
        self.screen()
        first = self.do_import()
        collection = first["collection"]
        root = self.one(collection, "ROOT")
        world = root.matrix_world.copy()
        before = self.resources()
        old_snapshot = collection.get("rr_unity_layout_snapshot_id")
        invalid = copy.deepcopy(self.payload)
        invalid["devices"][0]["relativeMatrix"][0] = 0.0
        with self.assertRaises(layout.LayoutValidationError):
            self.do_import(invalid)
        (self.base / "screen.png").unlink()
        with self.assertRaises(layout.LayoutValidationError):
            self.do_import()
        self.assertEqual(self.resources(), before)
        self.assertIs(self.one(collection, "ROOT"), root)
        self.assert_matrix(root.matrix_world, world)
        self.assertEqual(collection.get("rr_unity_layout_snapshot_id"), old_snapshot)

    def test_commit_failure_rolls_back_reused_root_and_removes_staging_resources(self):
        first = self.do_import()
        collection = first["collection"]
        root = self.one(collection, "ROOT")
        old_parent = root.parent
        old_inverse = root.matrix_parent_inverse.copy()
        old_world = root.matrix_world.copy()
        before = self.resources()
        updated = copy.deepcopy(self.payload)
        updated["devices"][0]["relativeMatrix"][3] = 9.0
        original = layout._parent_relative
        calls = 0

        def fail_on_reused_root(obj, parent, matrix):
            nonlocal calls
            calls += 1
            original(obj, parent, matrix)
            if obj == root:
                raise RuntimeError("Injected failure after reused ROOT transform changed")

        with mock.patch.object(layout, "_parent_relative", side_effect=fail_on_reused_root):
            with self.assertRaisesRegex(RuntimeError, "Injected failure"):
                self.do_import(updated)
        with self.context():
            self.view_layer.update()
        self.assertGreaterEqual(calls, 3, "Injection must occur after staging the ROOT and PART.")
        self.assertEqual(self.resources(), before, "Failed import leaked owned staging data.")
        self.assertIs(root.parent, old_parent)
        self.assert_matrix(root.matrix_parent_inverse, old_inverse)
        self.assert_matrix(root.matrix_world, old_world)
        self.assertIs(self.one(collection, "ROOT"), root)

    def test_staging_failure_after_image_load_preserves_previous_layout(self):
        self.screen()
        first = self.do_import()
        collection = first["collection"]
        root = self.one(collection, "ROOT")
        world = root.matrix_world.copy()
        before = self.resources()
        updated = copy.deepcopy(self.payload)
        # Load/pack the PNG before the injected third transform conversion fails.
        updated["materials"][0]["texturePath"] = "screen.png"
        updated["devices"][0]["parts"].append(copy.deepcopy(updated["devices"][0]["parts"][0]))
        original = layout.unity_to_blender_matrix
        calls = 0
        image_loaded = False

        def fail_second_part(matrix, meters_per_unit=1.0):
            nonlocal calls, image_loaded
            calls += 1
            if calls == 3:
                image_loaded = len(self.resources()["images"]) > len(before["images"])
                raise RuntimeError("Injected staging failure after PNG loading")
            return original(matrix, meters_per_unit)

        with mock.patch.object(layout, "unity_to_blender_matrix", side_effect=fail_second_part):
            with self.assertRaisesRegex(RuntimeError, "Injected staging failure"):
                self.do_import(updated)
        self.assertTrue(image_loaded, "The failure must happen after allocating a packed image.")
        self.assertEqual(self.resources(), before)
        self.assertIs(self.one(collection, "ROOT"), root)
        self.assert_matrix(root.matrix_world, world)

    def test_candidate_selected_collection_queue_and_direct_export_guards(self):
        helper = candidate_helper()
        self.addCleanup(sys.modules.pop, helper.__name__, None)
        imported = self.do_import()
        collection = imported["collection"]
        reference = self.one(collection, "PART")
        properties_before = dict(reference.items())
        for identity_change in (lambda: helper.ensure_export_identity(reference),
                                lambda: helper.prepare_export_identity(reference)):
            with self.assertRaisesRegex(RuntimeError, "preview-only"):
                identity_change()
        self.assertTrue(helper.export_identity_repair_is_group(reference))
        self.assertEqual(dict(reference.items()), properties_before)
        mesh = bpy.data.meshes.new("Artist mesh " + self.token)
        mesh[self.TEST_PROP] = self.token
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        artist = self.artist_object("Artist export", mesh, collection)
        artist.parent = self.anchor

        class Queue(list):
            def add(queue):
                item = SimpleNamespace(object_name="", icon_preview_root_name="")
                queue.append(item)
                return item

        settings = SimpleNamespace(export_mode=helper.EXPORT_MODE_GENERAL, export_queue=Queue(),
                                   output_root=str(self.base), queue_active_index=0)
        operator = SimpleNamespace(include_model=True, include_icon=False, report=mock.Mock())
        context = SimpleNamespace(scene=SimpleNamespace(rr_builder_export_settings=settings),
                                  selected_objects=[reference, artist], object=artist, edit_object=None,
                                  collection=collection, view_layer=self.view_layer)
        submitted = []

        def capture(objects, _settings, _context, source, _model, _icon):
            submitted.append((source, list(objects)))
            return {"FINISHED"}

        with self.context(), mock.patch.object(helper, "export_objects", side_effect=capture):
            self.view_layer.objects.active = artist
            self.assertEqual(helper.RR_OT_export_selected.execute(operator, context), {"FINISHED"})
            self.assertEqual(helper.RR_OT_export_collection.execute(operator, context), {"FINISHED"})
        self.assertEqual(submitted, [("selected", [artist]), ("collection", [artist])])
        with self.context(), ExitStack() as patches:
            for name in ("autosave_icon_preview_lights", "initialize_queue_item_framing",
                         "set_queue_active_index_without_preview_sync"):
                patches.enter_context(mock.patch.object(helper, name))
            patches.enter_context(mock.patch.object(helper, "resolve_icon_framing_root", return_value=artist))
            self.assertEqual(helper.RR_OT_queue_selected.execute(operator, context), {"FINISHED"})
            self.assertEqual([item.object_name for item in settings.export_queue], [artist.name])
            stale = settings.export_queue.add()
            stale.object_name = reference.name
            self.assertEqual(helper.queue_roots(settings), [artist])
            self.assertEqual(helper.expand_related_export_roots([reference, artist]), [artist])
            self.assertEqual(helper.queue_roots_for_export_roots([reference, artist]), [artist])
            self.assertEqual(helper.get_asset_meshes(self.anchor), [artist])
            for direct in (lambda: helper.export_fbx(reference, str(self.base / "blocked.fbx")),
                           lambda: helper.export_builder_asset(reference, settings)):
                with self.assertRaisesRegex(RuntimeError, "preview-only"):
                    direct()

        # Exercise the actual FBX function, substituting only its final exporter
        # and unrelated map/Surface Text preparation. No FBX or production write.
        exported_selection = []

        def fbx_sink(**kwargs):
            exported_selection.extend(obj for obj in self.view_layer.objects
                                      if obj.select_get(view_layer=self.view_layer))
            self.assertTrue(kwargs["use_selection"])
            self.assertEqual(Path(kwargs["filepath"]), self.base / "artist.fbx")
            return {"FINISHED"}

        def select_export_root(root):
            for obj in self.view_layer.objects:
                obj.select_set(False)
            root.select_set(True)
            for obj in helper.get_export_asset_meshes(root):
                obj.select_set(True)
            self.view_layer.objects.active = root

        class BpyProxy:
            ops = SimpleNamespace(export_scene=SimpleNamespace(fbx=fbx_sink))

            def __getattr__(self, name):
                return getattr(bpy, name)

        bpy_proxy = BpyProxy()
        with self.context(), ExitStack() as patches:
            patches.enter_context(mock.patch.object(helper, "bpy", bpy_proxy))
            patches.enter_context(mock.patch.object(helper, "set_active_export_root", side_effect=select_export_root))
            patches.enter_context(mock.patch.object(helper, "cleanup_stale_surface_text_sampling_aliases"))
            patches.enter_context(mock.patch.object(helper, "prepare_unity_export_maps", return_value=([], [])))
            for name in ("create_surface_text_export_meshes", "create_surface_text_sampling_export_aliases",
                         "create_surface_text_frame_export_aliases"):
                patches.enter_context(mock.patch.object(helper, name, return_value=[]))
            helper.export_fbx(self.anchor, str(self.base / "artist.fbx"))
        self.assertEqual(set(exported_selection), {self.anchor, artist})
        self.assertFalse((self.base / "artist.fbx").exists())
        self.assertFalse((self.base / "blocked.fbx").exists())

    def test_owned_blend_save_reopen_update_deduplicates_and_keeps_packed_png(self):
        self.screen()
        first = self.do_import()
        reference_id = self.payload["reference"]["id"]
        device_id = self.payload["devices"][0]["id"]
        blend = self.base / "owned-layout.blend"
        # Save only this fixture Scene's dependency closure; never save or open
        # the user's Main file or switch their active window Scene.
        bpy.data.libraries.write(str(blend), {self.scene}, path_remap="ABSOLUTE", fake_user=False)
        self.assertTrue(blend.is_file())
        original_name = self.scene.name
        old_scene = self.scene
        self.scene = None
        bpy.data.scenes.remove(old_scene)
        for obj in list(bpy.data.objects):
            if self.ours(obj):
                bpy.data.objects.remove(obj, do_unlink=True)
        for value in list(bpy.data.collections):
            if self.ours(value):
                bpy.data.collections.remove(value, do_unlink=True)
        for values in (bpy.data.meshes, bpy.data.materials, bpy.data.images):
            for value in list(values):
                if self.ours(value) and value.users == 0:
                    values.remove(value)
        (self.base / "screen.png").unlink()
        with bpy.data.libraries.load(str(blend), link=False) as (available, loaded):
            self.assertIn(original_name, available.scenes)
            loaded.scenes = [original_name]
        self.scene = loaded.scenes[0]
        self.view_layer = self.scene.view_layers[0]
        self.anchor = next(obj for obj in self.scene.objects if obj.get(self.TEST_PROP) == self.token)
        collection = next(value for value in self.scene.collection.children
                          if value.get(layout.REFERENCE_ID_PROP) == reference_id)
        root = self.one(collection, "ROOT", device_id)
        root_pointer = root.as_pointer()
        preview = self.one(collection, "SCREEN")
        image = next(node.image for node in preview.material_slots[0].material.node_tree.nodes
                     if node.type == "TEX_IMAGE")
        self.assertIsNotNone(image.packed_file, "Packed PNG must survive without its source file.")
        self.assertEqual(tuple(image.size), (1, 1))
        self.assertFalse((self.base / "screen.png").exists())
        (self.base / "screen.png").write_bytes(PNG)
        updated = self.do_import()
        self.assertIs(updated["collection"], collection)
        self.assertEqual(self.one(collection, "ROOT", device_id).as_pointer(), root_pointer)
        self.assertEqual(len(self.managed(collection, "ROOT")), 1)
        self.assertEqual(len(self.managed(collection, "PART")), 1)
        self.assertEqual(len(self.managed(collection, "SCREEN")), 1)
        self.assertEqual(len([value for value in self.scene.collection.children
                              if value.get(layout.REFERENCE_ID_PROP) == reference_id]), 1)
        self.assertIs(bpy.context.scene, self.original_scene)


if __name__ == "__main__":
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-owned-dir", type=Path,
                        help="Create a unique run directory here for JSON and owned temporary artifacts.")
    parser.add_argument("--factory-owned-scene", action="store_true",
                        help="Record that the caller launched a separate factory-startup Blender process.")
    options = parser.parse_args(arguments)
    if options.output_owned_dir is not None:
        options.output_owned_dir.mkdir(parents=True, exist_ok=True)
        OUTPUT_OWNED_DIR = options.output_owned_dir.resolve() / ("layout-contract-" + uuid.uuid4().hex)
        OUTPUT_OWNED_DIR.mkdir()
    started = time.time()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(UnityDeviceLayoutBlenderTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "schema": "random-realm.unity-device-layout-tests", "version": 1,
        "blender_version": list(bpy.app.version) if bpy is not None else None,
        "runtime_module": str(RUNTIME_MODULE_PATH),
        "helper_source": str((RUNTIME_ADDON_DIRECTORY / "__init__.py") if RUNTIME_ADDON_DIRECTORY is not None
                             else Path(_test_directory) / "rr_helper_candidate.py"),
        "background": bool(bpy.app.background) if bpy is not None else False,
        "factory_owned_scene": options.factory_owned_scene,
        "tests_run": result.testsRun, "failure_count": len(result.failures),
        "error_count": len(result.errors), "skip_count": len(result.skipped),
        "passed_count": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "runtime_verified": bpy is not None and result.wasSuccessful() and not result.skipped,
        "success": result.wasSuccessful(), "duration_seconds": time.time() - started,
        "failures": [{"test": test.id(), "traceback": trace} for test, trace in result.failures],
        "errors": [{"test": test.id(), "traceback": trace} for test, trace in result.errors],
        "skips": [{"test": test.id(), "reason": reason} for test, reason in result.skipped],
        "snapshots": SNAPSHOTS,
        "persistence_method": "owned Scene libraries.write/load; user Main file and active Scene preserved",
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if OUTPUT_OWNED_DIR is not None:
        report_path = OUTPUT_OWNED_DIR / "report.json"
        report_path.write_text(serialized, encoding="utf-8")
        print("RR_LAYOUT_TEST_REPORT=" + str(report_path))
    else:
        print(serialized)
    raise SystemExit(0 if result.wasSuccessful() else 1)

