"""Native relinking smoke checks; disposable Blender --factory-startup only.

The routing check runs the real Standard publication and manifest code with a
small stand-in FBX writer. It never touches a user's .blend or Unity project.
"""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))
import random_realm_builder_exporter as rr
from random_realm_builder_exporter import rr_export_identity_repair as repair


def mesh_object(name):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def native_snapshot(objects):
    return [(obj.name, obj.data.as_pointer(), tuple(tuple(v.co) for v in obj.data.vertices),
             tuple(tuple(p.vertices) for p in obj.data.polygons),
             tuple(tuple(row) for row in obj.matrix_world), obj.parent)
            for obj in objects]


def identity_snapshot(objects):
    return [{key: obj[key] for key in repair.IDENTITY_PROPS if key in obj} for obj in objects]


def select_only(obj):
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def is_core(obj):
    return any(rr.get_reference_object(scene) == obj for scene in bpy.data.scenes)


def is_group(obj):
    return bool(rr.is_object_manager_assembly_root(obj)
                or rr.object_manager_assembly_root_for_object(obj)
                or rr.object_manager_variant_group_root(obj))


class NativeIdentityRepairTests(unittest.TestCase):
    def setUp(self):
        for scene in bpy.data.scenes:
            rr.clear_reference_object(scene)
            scene.rr_builder_export_settings.export_queue.clear()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        rr.reset_object_manager_duplicate_guard()
        rr.reset_object_manager_name_sync_state()
        rr.reset_scene_selection_queue_lookup()
        self.temporary = tempfile.TemporaryDirectory(prefix="rr_native_relink_")
        self.directory = Path(self.temporary.name)
        self.output = self.directory / "Build" / "Data"
        self.package = self.output / "Floor_A"
        self.package.mkdir(parents=True)
        self.model = self.package / "model.fbx"
        self.model.write_bytes(b"previous model fixture")
        self.manifest = {"id": "Floor_A", "stableId": "rr_asset_original",
                         "previousIds": [], "modelFile": "model.fbx",
                         "sourceObject": "Floor_A", "uvExport": {
                             "modelSha256": hashlib.sha256(self.model.read_bytes()).hexdigest()}}
        self.write_manifest()
        self.settings = bpy.context.scene.rr_builder_export_settings
        self.settings.output_root = str(self.output)
        self.settings.export_mode = rr.EXPORT_MODE_GENERAL

    def tearDown(self):
        for scene in bpy.data.scenes:
            rr.clear_reference_object(scene)
        self.temporary.cleanup()

    def write_manifest(self):
        (self.package / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def plan(self, target, detach=(), **overrides):
        if not overrides:
            return rr.build_unity_asset_rebind_plan(target, self.package,
                                                  settings=self.settings, detach_copies=bool(detach))
        callbacks = dict(asset_id=rr.export_asset_id, previous_ids=rr.export_previous_ids,
                         is_core=is_core, is_group=is_group,
                         is_editable=lambda obj: getattr(obj, "is_editable", True)
                         and getattr(obj, "library", None) is None,
                         unbound_asset_id=rr.object_manager_display_name)
        callbacks.update(overrides)
        return repair.build_rebind_plan(target, self.package, rr.live_export_identity_roots(),
                                        detach=detach, **callbacks)

    def apply(self, plan):
        return rr.apply_unity_asset_rebind_plan(plan, self.settings)

    def test_core_and_native_copy_relink_preserves_scene_data_and_original_owner(self):
        original = mesh_object("Floor_A")
        original[rr.EXPORT_STABLE_ID_PROP] = self.manifest["stableId"]
        rr.ensure_export_identity(original)
        rr.mark_reference_object(original, bpy.context.scene)
        copied = original.copy()
        copied.data = original.data.copy()
        bpy.context.scene.collection.objects.link(copied)
        select_only(original)
        before = native_snapshot([original, copied])
        package_bytes = {path.name: path.read_bytes() for path in self.package.iterdir()}
        with self.assertRaisesRegex(RuntimeError, "identity conflict"):
            rr.validate_export_identity(original)
        self.apply(self.plan(original, [copied]))
        self.assertEqual(before, native_snapshot([original, copied]))
        self.assertEqual(original[rr.EXPORT_STABLE_ID_PROP], self.manifest["stableId"])
        self.assertNotEqual(copied[rr.EXPORT_STABLE_ID_PROP], self.manifest["stableId"])
        self.assertEqual([], rr.export_previous_ids(copied))
        self.assertNotIn("rr_reference_marked", copied)
        self.assertIs(rr.get_reference_object(bpy.context.scene), original)
        self.assertIs(bpy.context.view_layer.objects.active, original)
        self.assertEqual(original["rr_reference_stable_id"], self.manifest["stableId"])
        self.assertTrue(rr.validate_export_identity(original))
        self.assertTrue(rr.validate_export_identity(copied))
        self.assertEqual(package_bytes, {path.name: path.read_bytes() for path in self.package.iterdir()})

    def test_binding_routes_model_manifest_and_native_rename_to_chosen_package(self):
        target = mesh_object("Readable_Artist_Name")
        self.apply(self.plan(target))
        stable = target[rr.EXPORT_STABLE_ID_PROP]
        history = rr.export_previous_ids(target)
        rr.remember_object_manager_name_sync_state(target)
        target.name = "New_Readable_Name"
        rr.sync_object_manager_names()
        self.assertEqual("Floor_A", rr.export_asset_id(target))
        self.assertEqual(history, rr.export_previous_ids(target))
        self.assertEqual("Floor_A", target[rr.EXPORT_LAST_ID_PROP])
        self.assertEqual(stable, target[rr.EXPORT_STABLE_ID_PROP])
        select_only(target)
        geometry = native_snapshot([target])
        payload = b"fresh model fixture"
        model_meta = self.package / "model.fbx.meta"
        model_meta.write_text("guid: original_model_guid\n", encoding="utf-8")
        guid_before = model_meta.read_bytes()

        def tiny_fbx(_root, path):
            Path(path).write_bytes(payload)
            return []

        with mock.patch.object(rr, "export_fbx", side_effect=tiny_fbx):
            result = rr.export_builder_asset(target, self.settings, queue_import=False)
        self.assertEqual("Floor_A", result[0])
        self.assertEqual(payload, self.model.read_bytes())
        manifest = json.loads((self.package / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("Floor_A", manifest["id"])
        self.assertEqual(stable, manifest["stableId"])
        self.assertEqual("New_Readable_Name", manifest["sourceObject"])
        self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest["uvExport"]["modelSha256"])
        self.assertEqual(guid_before, model_meta.read_bytes())
        self.assertFalse((self.output / target.name).exists())
        self.assertEqual(geometry, native_snapshot([target]))

    def test_native_duplicate_clear_removes_binding_without_touching_original(self):
        target = mesh_object("Readable_Artist_Name")
        self.apply(self.plan(target))
        original_identity = identity_snapshot([target])
        rr.reset_object_manager_duplicate_guard()
        select_only(target)
        bpy.ops.object.duplicate()
        duplicate = bpy.context.view_layer.objects.active
        self.assertEqual("Floor_A", duplicate.get(repair.ASSET_PROP))
        geometry = native_snapshot([target, duplicate])
        rr.clear_inherited_rr_identity_from_native_duplicates()
        self.assertEqual(geometry, native_snapshot([target, duplicate]))
        self.assertNotIn(repair.ASSET_PROP, duplicate)
        self.assertNotIn(rr.EXPORT_STABLE_ID_PROP, duplicate)
        self.assertEqual(original_identity, identity_snapshot([target]))

    def test_bound_group_rename_does_not_manufacture_display_name_aliases(self):
        target = mesh_object("Readable_Artist_Name")
        self.apply(self.plan(target))
        before = identity_snapshot([target])
        # A bound ordinary asset can later be organized as an assembly. Its
        # display-name synchronizer must continue to honor the existing route.
        target[rr.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        target[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_fixture"
        target[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = target.name
        rr.remember_object_manager_name_sync_state(target)
        target.name = "New_Assembly_Display"
        rr.sync_object_manager_names()
        self.assertEqual("New_Assembly_Display", rr.object_manager_display_name(target))
        self.assertEqual("Floor_A", rr.export_asset_id(target))
        self.assertEqual(before, identity_snapshot([target]))

    def test_other_output_folder_and_changed_route_are_rejected_before_writes(self):
        target = mesh_object("Readable_Artist_Name")
        before = identity_snapshot([target])
        old_root = self.settings.output_root
        self.settings.output_root = str(self.directory / "AnotherOutput")
        with self.assertRaisesRegex(ValueError, "current Export Folder"):
            self.plan(target)
        self.settings.output_root = old_root
        plan = self.plan(target)
        self.settings.output_root = str(self.directory / "AnotherOutput")
        with self.assertRaisesRegex(ValueError, "folder changed"):
            self.apply(plan)
        self.assertEqual(before, identity_snapshot([target]))
        self.settings.output_root = old_root
        self.settings.export_mode = rr.EXPORT_MODE_BUILDING
        with self.assertRaisesRegex(ValueError, "Standard independent"):
            self.plan(target)
        self.assertEqual(before, identity_snapshot([target]))

    def test_use_object_name_preserves_stable_identity_tracks_alias_and_rolls_back_conflict(self):
        target = mesh_object("Readable_Artist_Name")
        self.apply(self.plan(target))
        stable = target[rr.EXPORT_STABLE_ID_PROP]
        result = bpy.ops.rr_builder.use_object_export_name("EXEC_DEFAULT", target_name=target.name)
        self.assertEqual({"FINISHED"}, result)
        self.assertNotIn(rr.EXPORT_ASSET_ID_OVERRIDE_PROP, target)
        self.assertEqual(target.name, rr.export_asset_id(target))
        self.assertEqual(stable, target[rr.EXPORT_STABLE_ID_PROP])
        self.assertEqual(["Floor_A"], rr.export_previous_ids(target))
        self.apply(self.plan(target))
        peer = mesh_object("Another_Asset")
        peer[rr.EXPORT_ASSET_ID_OVERRIDE_PROP] = target.name
        peer[rr.EXPORT_STABLE_ID_PROP] = "rr_asset_different_owner"
        before = identity_snapshot([target, peer])
        with self.assertRaisesRegex(RuntimeError, "identity conflict"):
            bpy.ops.rr_builder.use_object_export_name("EXEC_DEFAULT", target_name=target.name)
        self.assertEqual(before, identity_snapshot([target, peer]))
        with self.assertRaisesRegex(RuntimeError, "object changed"):
            bpy.ops.rr_builder.use_object_export_name(
                "EXEC_DEFAULT", target_name=target.name, target_uid="stale_object_uid")
        self.assertEqual(before, identity_snapshot([target, peer]))

    def test_stale_preview_and_late_all_scene_conflict_leave_identity_unchanged(self):
        target = mesh_object("Readable_Artist_Name")
        plan = self.plan(target)
        before = identity_snapshot([target])
        self.manifest["sourceObject"] = "Edited_After_Preview"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.apply(plan)
        self.assertEqual(before, identity_snapshot([target]))
        plan = self.plan(target)
        other = mesh_object("Floor_A")
        other[rr.EXPORT_STABLE_ID_PROP] = "rr_asset_someone_else"
        before = identity_snapshot([target, other])
        with self.assertRaisesRegex(RuntimeError, "identity conflict"):
            self.apply(plan)
        self.assertEqual(before, identity_snapshot([target, other]))

    def test_group_and_actual_core_copies_cannot_be_detached_or_stolen(self):
        target = mesh_object("Readable_Artist_Name")
        before = identity_snapshot([target])
        with self.assertRaisesRegex(ValueError, "editable independent"):
            self.plan(target, is_editable=lambda _obj: False)
        target[rr.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
        target[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "assembly_fixture"
        target[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = target.name
        with self.assertRaisesRegex(ValueError, "managed groups"):
            self.plan(target)
        rr.clear_object_manager_props(target)
        original = mesh_object("Floor_A")
        original[rr.EXPORT_STABLE_ID_PROP] = self.manifest["stableId"]
        rr.mark_reference_object(original, bpy.context.scene)
        core_identity = identity_snapshot([original])
        with self.assertRaisesRegex(ValueError, "Core"):
            self.plan(target, [original])
        self.assertEqual(before, identity_snapshot([target]))
        self.assertEqual(core_identity, identity_snapshot([original]))


def main():
    if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
        raise RuntimeError("Use a disposable --factory-startup Blender process.")
    if Path(rr.__file__).resolve() != (ROOT / "addons" / "random_realm_builder_exporter" / "__init__.py").resolve():
        raise RuntimeError("The smoke checks must import the canonical repository package.")
    rr.register()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeIdentityRepairTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise RuntimeError("Native export identity repair checks failed.")
        print(f"RR_EXPORT_IDENTITY_REPAIR_BLENDER_PASS tests={result.testsRun}")
    finally:
        rr.unregister()


if __name__ == "__main__":
    main()
