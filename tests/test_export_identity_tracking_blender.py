"""Persistent export ownership regressions; disposable Blender process only.

Run --background --factory-startup --python this_file. Real native duplicate /
save / reload operations use generated fixtures. The real Standard publication
pipeline is exercised with a tiny FBX writer in a temporary Build/Data folder.
No authored project or Unity asset is opened, saved, or overwritten.
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
sys.path.insert(0, str(ROOT / "tests"))
import random_realm_builder_exporter as rr
from random_realm_builder_exporter import rr_export_identity_tracking as tracking
from random_realm_builder_exporter import rr_export_identity_repair as repair
from test_export_identity_repair_blender import mesh_object, native_snapshot, select_only


def owned_value(value):
    """Copy borrowed IDProperty groups/arrays but retain identity of ID refs."""
    if isinstance(value, bpy.types.ID):
        return ("ID", value.name, value.as_pointer())
    if hasattr(value, "items"):
        return tuple(sorted((key, owned_value(item)) for key, item in value.items()))
    if hasattr(value, "to_list"):
        return tuple(owned_value(item) for item in value.to_list())
    if isinstance(value, (tuple, list)):
        return tuple(owned_value(item) for item in value)
    return value


def object_properties(obj):
    return owned_value(dict(obj.items()))


def object_refs(value):
    # Saved ownership uses native weak constraint targets so the registry
    # cannot keep an artist-deleted Object alive as an orphan datablock.
    if isinstance(value, bpy.types.Object):
        if value.get(tracking.CARRIER_MARKER):
            for constraint in value.constraints:
                if constraint.type == "COPY_LOCATION" and constraint.target is not None:
                    yield constraint.target
        else:
            yield value
    elif hasattr(value, "items"):
        for _key, item in value.items():
            yield from object_refs(item)
    elif hasattr(value, "to_list"):
        for item in value.to_list():
            yield from object_refs(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from object_refs(item)


def registered_objects(scene=None):
    scene = scene or bpy.context.scene
    return set(object_refs(scene.get(tracking.REGISTRY_PROP)))


def registry_snapshot():
    scenes = [(scene.name, owned_value(scene.get(tracking.REGISTRY_PROP)))
              for scene in bpy.data.scenes]
    carriers = [(obj.name, tuple((item.name, item.type, owned_value(item.target),
                                 item.mute, item.influence) for item in obj.constraints))
                for obj in bpy.data.objects if obj.get(tracking.CARRIER_MARKER)]
    return scenes, carriers


def native_state(objects):
    bpy.context.view_layer.update()
    return (
        native_snapshot(objects),
        tuple((obj.as_pointer(), obj.select_get(), obj.hide_get(),
               obj.hide_viewport, obj.hide_render, obj.hide_select,
               tuple((slot.link, slot.material.as_pointer() if slot.material else None)
                     for slot in obj.material_slots)) for obj in objects),
        bpy.context.view_layer.objects.active,
    )


def unrelated_properties(obj):
    return owned_value({key: value for key, value in obj.items()
                        if not key.startswith("rr_export_") and key not in repair.CORE_PROPS})


def portable_geometry(obj):
    """Geometry/pose signature meaningful even after loading new Blender IDs."""
    return (
        tuple(tuple(vertex.co) for vertex in obj.data.vertices),
        tuple(tuple(face.vertices) for face in obj.data.polygons),
        tuple(tuple(row) for row in obj.matrix_world),
        obj.parent.name if obj.parent else None,
    )


def tree_bytes(directory):
    directory = Path(directory)
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def reset_runtime_guards():
    rr.reset_object_manager_duplicate_guard()
    rr.reset_object_manager_name_sync_state()
    rr.reset_scene_selection_queue_lookup()


def clear_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    for scene in bpy.data.scenes:
        rr.clear_reference_object(scene)
        scene.rr_builder_export_settings.export_queue.clear()
        if tracking.REGISTRY_PROP in scene:
            del scene[tracking.REGISTRY_PROP]
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.materials):
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)
    reset_runtime_guards()


class ExportIdentityTrackingTests(unittest.TestCase):
    def setUp(self):
        clear_scene()
        self.temporary = tempfile.TemporaryDirectory(prefix="rr_identity_tracking_")
        self.directory = Path(self.temporary.name)
        self.output = self.directory / "Build" / "Data"
        self.output.mkdir(parents=True)
        self.settings = bpy.context.scene.rr_builder_export_settings
        self.settings.output_root = str(self.output)
        self.settings.export_mode = rr.EXPORT_MODE_GENERAL
        self.settings.include_model_with_export = True
        self.settings.include_icon_with_export = False

    def tearDown(self):
        clear_scene()
        self.temporary.cleanup()

    def package(self, asset_id, stable_id, payload=b"previous FBX fixture"):
        directory = self.output / asset_id
        directory.mkdir(parents=True)
        (directory / "model.fbx").write_bytes(payload)
        (directory / "model.fbx.meta").write_text(
            "fileFormatVersion: 2\nguid: 54ab4c4d6bc9463f8de3a3b90ff071cd\n", encoding="utf-8")
        manifest = {
            "id": asset_id, "stableId": stable_id, "previousIds": [],
            "modelFile": "model.fbx", "sourceObject": asset_id,
            "uvExport": {"modelSha256": hashlib.sha256(payload).hexdigest()},
        }
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return directory

    def duplicate(self, owner, name):
        copied = owner.copy()
        copied.data = owner.data.copy()
        bpy.context.scene.collection.objects.link(copied)
        copied.name = name
        return copied

    def legacy_identity(self, obj, stable_id, asset_id):
        obj[rr.EXPORT_STABLE_ID_PROP] = stable_id
        obj[rr.EXPORT_LAST_ID_PROP] = asset_id
        obj[rr.EXPORT_PREVIOUS_IDS_PROP] = "[]"
        obj[rr.EXPORT_ASSET_ID_OVERRIDE_PROP] = asset_id

    def export_tiny(self, obj, payload):
        paths = []

        def writer(_root, path):
            paths.append(Path(path))
            Path(path).write_bytes(payload)
            return []

        with mock.patch.object(rr, "export_fbx", side_effect=writer):
            result = rr.export_builder_asset(
                obj, self.settings, export_model=True, include_icon=False, queue_import=False)
        self.assertEqual(len(paths), 1)
        return result, paths[0]

    def assert_unchanged(self, objects, before):
        native, properties, registry = before
        self.assertEqual(native_state(objects), native)
        self.assertEqual([object_properties(obj) for obj in objects], properties)
        self.assertEqual(registry_snapshot(), registry)

    def full_snapshot(self, objects):
        return (native_state(objects), [object_properties(obj) for obj in objects],
                registry_snapshot())

    def test_ensure_assigns_one_stable_identity_and_saved_owner_reference(self):
        owner = mesh_object("Wall")
        owner["artist"] = {"note": "keep", "weights": [0.2, 0.5, 0.9]}
        select_only(owner)
        before = native_state([owner])
        users_before = owner.users
        unrelated = unrelated_properties(owner)
        stable = rr.ensure_export_identity(owner)[0]
        self.assertTrue(stable)
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], stable)
        self.assertEqual(registered_objects(), {owner})
        self.assertEqual(owner.users, users_before, "Ownership must not retain a deleted Object")
        carriers = [obj for obj in bpy.data.objects if obj.get(tracking.CARRIER_MARKER)]
        self.assertEqual(len(carriers), 1)
        self.assertFalse(carriers[0].users_scene, "The registry must not appear in an artist's scene")
        registry = registry_snapshot()
        self.assertEqual(rr.ensure_export_identity(owner)[0], stable)
        self.assertEqual(registry_snapshot(), registry)
        self.assertEqual(native_state([owner]), before)
        self.assertEqual(unrelated_properties(owner), unrelated)

    def test_native_duplicate_does_not_relink_the_scene_registry_to_the_copy(self):
        owner = mesh_object("Wall")
        stable = rr.ensure_export_identity(owner)[0]
        owner[rr.EXPORT_ASSET_ID_OVERRIDE_PROP] = "Wall"
        owner[rr.EXPORT_PREVIOUS_IDS_PROP] = '["OldWall"]'
        owner["rr_reference_marked"] = True  # Inherited marker, no actual Core pointer.
        owner["rr_reference_stable_id"] = stable
        owner["artist_note"] = "copied geometry remains authored"
        registry = registry_snapshot()
        owner_props = object_properties(owner)
        select_only(owner)
        self.assertEqual(bpy.ops.object.duplicate(), {"FINISHED"})
        copied = bpy.context.view_layer.objects.active
        self.assertIsNot(copied, owner)
        copied.name = "Glass"
        self.assertEqual(registered_objects(), {owner})
        self.assertEqual(registry_snapshot(), registry)
        self.assertEqual(object_properties(owner), owner_props)
        reset_runtime_guards()  # Existing copies after a reload cannot use new-ID detection.
        owner_props = object_properties(owner)  # Baseline the existing name-sync marker too.
        before = native_state([owner, copied])
        unrelated = unrelated_properties(copied)
        rr.prepare_export_identity(copied, self.settings)
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], stable)
        self.assertNotEqual(copied[rr.EXPORT_STABLE_ID_PROP], stable)
        self.assertEqual(rr.export_asset_id(owner), "Wall")
        self.assertEqual(rr.export_asset_id(copied), "Glass")
        self.assertEqual(rr.export_previous_ids(copied), [])
        self.assertIn(copied.get(rr.EXPORT_ASSET_ID_OVERRIDE_PROP), (None, "Glass"))
        self.assertNotIn("rr_reference_marked", copied)
        self.assertNotIn("rr_reference_stable_id", copied)
        self.assertEqual(native_state([owner, copied]), before)
        self.assertEqual(unrelated_properties(copied), unrelated)
        self.assertEqual(object_properties(owner), owner_props)
        self.assertIn(owner, registered_objects())
        self.assertIn(copied, registered_objects())
        self.assertTrue(rr.validate_export_identity(owner))
        self.assertTrue(rr.validate_export_identity(copied))

    def test_save_open_preserves_original_owner_and_repairs_a_preexisting_copy(self):
        owner = mesh_object("Wall")
        stable = rr.ensure_export_identity(owner)[0]
        copied = self.duplicate(owner, "Glass")
        # Simulate opening an older saved scene with both IDs already present.
        # Save-time new-object heuristics must not supply the ownership proof.
        reset_runtime_guards()
        select_only(copied)
        bpy.context.view_layer.update()
        geometries = {obj.name: portable_geometry(obj) for obj in (owner, copied)}
        path = self.directory / "ownership_fixture.blend"
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False), {"FINISHED"})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(path)), {"FINISHED"})
        self.settings = bpy.context.scene.rr_builder_export_settings
        owner = bpy.data.objects["Wall"]
        copied = bpy.data.objects["Glass"]
        self.assertIn(owner, registered_objects())
        self.assertIsNot(owner, copied)
        reset_runtime_guards()
        before = native_state([owner, copied])
        rr.prepare_export_identity(owner, self.settings)
        rr.prepare_export_identity(copied, self.settings)
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], stable)
        self.assertNotEqual(copied[rr.EXPORT_STABLE_ID_PROP], stable)
        self.assertEqual(native_state([owner, copied]), before)
        self.assertEqual({obj.name: portable_geometry(obj) for obj in (owner, copied)}, geometries)
        self.assertIn(owner, registered_objects())
        self.assertIn(copied, registered_objects())
        second = self.directory / "repaired_fixture.blend"
        copied_stable = copied[rr.EXPORT_STABLE_ID_PROP]
        self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(second), check_existing=False), {"FINISHED"})
        self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(second)), {"FINISHED"})
        self.settings = bpy.context.scene.rr_builder_export_settings
        self.assertEqual(bpy.data.objects["Wall"][rr.EXPORT_STABLE_ID_PROP], stable)
        self.assertEqual(bpy.data.objects["Glass"][rr.EXPORT_STABLE_ID_PROP], copied_stable)
        self.assertEqual(registered_objects(), {bpy.data.objects["Wall"], bpy.data.objects["Glass"]})

    def test_deleting_owner_and_recreating_its_name_does_not_reuse_old_identity(self):
        owner = mesh_object("Wall")
        stable = rr.ensure_export_identity(owner)[0]
        geometry = portable_geometry(owner)
        select_only(owner)
        self.assertEqual(bpy.ops.object.delete(), {"FINISHED"})
        self.assertIsNone(bpy.data.objects.get("Wall"))
        recreated = mesh_object("Wall")
        reset_runtime_guards()
        new_stable = rr.ensure_export_identity(recreated)[0]
        self.assertNotEqual(new_stable, stable)
        self.assertEqual(portable_geometry(recreated), geometry)
        self.assertIn(recreated, registered_objects())

    def test_recreated_name_cannot_overwrite_a_different_identity_or_model_guid(self):
        owner = mesh_object("Wall")
        stable = rr.ensure_export_identity(owner)[0]
        package = self.package("Wall", stable)
        files = tree_bytes(self.output)
        select_only(owner)
        self.assertEqual(bpy.ops.object.delete(), {"FINISHED"})
        recreated = mesh_object("Wall")
        new_stable = rr.ensure_export_identity(recreated)[0]
        self.assertNotEqual(new_stable, stable)
        select_only(recreated)
        reset_runtime_guards()
        before = self.full_snapshot([recreated])
        with mock.patch.object(rr, "export_fbx") as writer:
            with self.assertRaisesRegex(RuntimeError, "belongs to a different object"):
                rr.export_builder_asset(recreated, self.settings, include_icon=False)
            writer.assert_not_called()
        self.assert_unchanged([recreated], before)
        self.assertEqual(tree_bytes(self.output), files)
        # Pairing the new Object explicitly is the deliberate replacement path.
        plan = rr.build_unity_asset_rebind_plan(recreated, package, settings=self.settings)
        rr.apply_unity_asset_rebind_plan(plan, self.settings)
        self.assertEqual(recreated[rr.EXPORT_STABLE_ID_PROP], stable)
        rr.prepare_export_identity(recreated, self.settings)
        self.assertEqual(tree_bytes(self.output), files)

    def test_unique_legacy_identity_is_registered_before_a_later_copy(self):
        owner = mesh_object("Wall")
        self.legacy_identity(owner, "rr_asset_unique_legacy", "Wall")
        self.assertFalse(registered_objects())
        rr.prepare_export_identity(owner, self.settings)
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], "rr_asset_unique_legacy")
        self.assertEqual(registered_objects(), {owner})
        copied = self.duplicate(owner, "Glass")
        reset_runtime_guards()
        rr.prepare_export_identity(owner, self.settings)
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], "rr_asset_unique_legacy")
        self.assertNotEqual(copied[rr.EXPORT_STABLE_ID_PROP], "rr_asset_unique_legacy")
        self.assertEqual(rr.export_previous_ids(copied), [])

    def test_untracked_shared_legacy_identity_is_not_guessed_or_partly_changed(self):
        owner = mesh_object("Wall")
        self.legacy_identity(owner, "rr_asset_ambiguous", "Wall")
        copied = self.duplicate(owner, "Glass")
        reset_runtime_guards()
        select_only(copied)
        objects = [owner, copied]
        before = self.full_snapshot(objects)
        with self.assertRaisesRegex(RuntimeError, "(?i)identity conflict"):
            rr.prepare_export_identity(copied, self.settings)
        self.assert_unchanged(objects, before)

    def test_explicit_legacy_wall_owner_detaches_glass_without_file_or_geometry_changes(self):
        owner = mesh_object("Wall")
        self.legacy_identity(owner, "rr_asset_legacy_wall", "Wall")
        copied = self.duplicate(owner, "Glass")
        copied["rr_reference_marked"] = True
        copied["rr_reference_stable_id"] = "rr_asset_legacy_wall"
        self.package("Wall", "rr_asset_legacy_wall")
        files = tree_bytes(self.output)
        reset_runtime_guards()
        select_only(copied)
        before = native_state([owner, copied])
        unrelated = [unrelated_properties(obj) for obj in (owner, copied)]
        rr.keep_export_identity_owner(owner)
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], "rr_asset_legacy_wall")
        self.assertNotEqual(copied[rr.EXPORT_STABLE_ID_PROP], "rr_asset_legacy_wall")
        self.assertEqual(rr.export_asset_id(owner), "Wall")
        self.assertEqual(rr.export_asset_id(copied), "Glass")
        self.assertEqual(rr.export_previous_ids(copied), [])
        self.assertNotIn(rr.EXPORT_ASSET_ID_OVERRIDE_PROP, copied)
        self.assertNotIn("rr_reference_marked", copied)
        self.assertNotIn("rr_reference_stable_id", copied)
        self.assertEqual(native_state([owner, copied]), before)
        self.assertEqual([unrelated_properties(obj) for obj in (owner, copied)], unrelated)
        self.assertEqual(tree_bytes(self.output), files)
        self.assertIn(owner, registered_objects())
        self.assertIn(copied, registered_objects())
        self.assertTrue(rr.validate_export_identity(owner))
        self.assertTrue(rr.validate_export_identity(copied))

    def assert_unsafe_peer_rollback(self, kind):
        owner = mesh_object("Wall")
        stable = rr.ensure_export_identity(owner)[0]
        self.legacy_identity(owner, stable, "Wall")
        editable_copy = self.duplicate(owner, "AlphaCopy")
        peer = self.duplicate(owner, "UnsafePeer")
        if kind == "core":
            rr.mark_reference_object(peer, bpy.context.scene)
            self.assertIs(rr.get_reference_object(bpy.context.scene), peer)
        elif kind == "managed":
            peer[rr.OBJECT_MANAGER_ASSEMBLY_ROOT_PROP] = True
            peer[rr.OBJECT_MANAGER_ASSEMBLY_ID_PROP] = "managed_peer_fixture"
            peer[rr.OBJECT_MANAGER_ASSEMBLY_NAME_PROP] = "UnsafePeer"
        elif kind == "linked":
            library = self.directory / "readonly_peer.blend"
            bpy.data.libraries.write(str(library), {peer})
            name = peer.name
            bpy.data.objects.remove(peer, do_unlink=True)
            with bpy.data.libraries.load(str(library), link=True) as (_available, requested):
                requested.objects = [name]
            peer = requested.objects[0]
            bpy.context.scene.collection.objects.link(peer)
            self.assertFalse(peer.is_editable)
        else:
            raise AssertionError(kind)
        reset_runtime_guards()
        select_only(owner)
        objects = [owner, editable_copy, peer]
        before = self.full_snapshot(objects)
        reference = rr.get_reference_object(bpy.context.scene)
        for action in ("explicit_keep", "automatic_prepare"):
            with self.subTest(action=action):
                with self.assertRaises((ValueError, RuntimeError)):
                    if action == "explicit_keep":
                        rr.keep_export_identity_owner(owner)
                    else:
                        rr.prepare_export_identity(owner, self.settings)
                self.assert_unchanged(objects, before)
                self.assertIs(rr.get_reference_object(bpy.context.scene), reference)

    def test_actual_core_peer_blocks_explicit_detach_and_rolls_back_all_peers(self):
        self.assert_unsafe_peer_rollback("core")

    def test_managed_peer_blocks_explicit_detach_and_rolls_back_all_peers(self):
        self.assert_unsafe_peer_rollback("managed")

    def test_linked_peer_blocks_explicit_detach_and_rolls_back_all_peers(self):
        self.assert_unsafe_peer_rollback("linked")

    def test_keep_owner_operator_repairs_renamed_legacy_source_and_rejects_stale_target(self):
        owner = mesh_object("Renamed_Wall")
        self.legacy_identity(owner, "rr_asset_legacy_operator", "Wall")
        copied = self.duplicate(owner, "Glass")
        self.package("Wall", "rr_asset_legacy_operator")
        files = tree_bytes(self.output)
        before = self.full_snapshot([owner, copied])
        with self.assertRaisesRegex(RuntimeError, "object changed"):
            bpy.ops.rr_builder.keep_export_identity_owner(
                "EXEC_DEFAULT", target_name=owner.name, target_uid="stale")
        self.assert_unchanged([owner, copied], before)
        native = native_state([owner, copied])
        result = bpy.ops.rr_builder.keep_export_identity_owner(
            "EXEC_DEFAULT", target_name=owner.name,
            target_uid=str(rr.object_manager_runtime_object_uid(owner)))
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual(rr.export_asset_id(owner), "Wall")
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], "rr_asset_legacy_operator")
        self.assertNotEqual(copied[rr.EXPORT_STABLE_ID_PROP], owner[rr.EXPORT_STABLE_ID_PROP])
        self.assertEqual(native_state([owner, copied]), native)
        self.assertEqual(tree_bytes(self.output), files)

    def test_explicit_use_object_name_is_not_reversed_by_default_route_tracking(self):
        owner = mesh_object("Renamed_Wall")
        self.legacy_identity(owner, "rr_asset_route_choice", "Wall")
        self.package("Wall", "rr_asset_route_choice")
        rr.prepare_export_identity(owner, self.settings)
        self.assertEqual(rr.export_asset_id(owner), "Wall")
        result = bpy.ops.rr_builder.use_object_export_name("EXEC_DEFAULT", target_name=owner.name)
        self.assertEqual(result, {"FINISHED"})
        rr.prepare_export_identity(owner, self.settings)
        self.assertEqual(rr.export_asset_id(owner), "Renamed_Wall")
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], "rr_asset_route_choice")

    def test_stable_identity_recovers_a_unique_package_without_old_name_metadata(self):
        self.package("Wall", "rr_asset_only_stable")
        owner = mesh_object("Renamed_Without_History")
        owner[rr.EXPORT_STABLE_ID_PROP] = "rr_asset_only_stable"
        rr.prepare_export_identity(owner, self.settings)
        self.assertEqual(rr.export_asset_id(owner), "Wall")
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], "rr_asset_only_stable")

    def test_existing_stable_package_keeps_folder_through_repeated_native_renames(self):
        stable = "rr_asset_existing_wall"
        package = self.package("Wall", stable)
        meta = (package / "model.fbx.meta").read_bytes()
        owner = mesh_object("Artist_Renamed_Wall")
        owner[rr.EXPORT_STABLE_ID_PROP] = stable
        owner[rr.EXPORT_LAST_ID_PROP] = "Wall"
        rr.prepare_export_identity(owner, self.settings)
        self.assertEqual(rr.export_asset_id(owner), "Wall")
        select_only(owner)
        for index, name in enumerate(("Lobby_Wall", "Entry_Wall", "Final_Wall")):
            rr.remember_object_manager_name_sync_state(owner)
            owner.name = name
            rr.sync_object_manager_names()
            rr.prepare_export_identity(owner, self.settings)
            before = native_state([owner])
            payload = ("model revision " + str(index)).encode()
            result, staged_path = self.export_tiny(owner, payload)
            self.assertEqual(result[0], "Wall")
            self.assertNotEqual(staged_path.parent, package)
            self.assertEqual(rr.export_asset_id(owner), "Wall")
            self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], stable)
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["id"], "Wall")
            self.assertEqual(manifest["stableId"], stable)
            self.assertEqual(manifest["sourceObject"], name)
            self.assertEqual((package / "model.fbx").read_bytes(), payload)
            self.assertEqual((package / "model.fbx.meta").read_bytes(), meta)
            self.assertEqual(native_state([owner]), before)
            self.assertFalse((self.output / name).exists())

    def test_new_first_export_uses_current_name_then_keeps_its_folder(self):
        owner = mesh_object("Original_Display")
        rr.ensure_export_identity(owner)
        # Identity allocation alone predates publication; the first Standard
        # package must use the display name current when it is first exported.
        owner.name = "First_Export_Name"
        self.export_tiny(owner, b"first exported model")
        package = self.output / "First_Export_Name"
        self.assertTrue(package.is_dir())
        self.assertFalse((self.output / "Original_Display").exists())
        stable = owner[rr.EXPORT_STABLE_ID_PROP]
        meta = b"guid: first_export_model_guid\n"
        (package / "model.fbx.meta").write_bytes(meta)
        owner.name = "Later_Display_Name"
        self.export_tiny(owner, b"updated model")
        self.assertEqual(rr.export_asset_id(owner), "First_Export_Name")
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], stable)
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sourceObject"], "Later_Display_Name")
        self.assertEqual(manifest["stableId"], stable)
        self.assertEqual((package / "model.fbx.meta").read_bytes(), meta)
        self.assertFalse((self.output / "Later_Display_Name").exists())

    def test_copy_exports_as_new_package_and_cannot_overwrite_original_model_or_guid(self):
        owner = mesh_object("Wall")
        stable = rr.ensure_export_identity(owner)[0]
        package = self.package("Wall", stable)
        rr.prepare_export_identity(owner, self.settings)
        original = tree_bytes(package)
        copied = self.duplicate(owner, "Glass")
        reset_runtime_guards()
        select_only(copied)
        before = native_state([owner, copied])
        self.export_tiny(copied, b"independent glass model")
        self.assertEqual(tree_bytes(package), original)
        self.assertEqual(native_state([owner, copied]), before)
        glass = self.output / "Glass"
        self.assertEqual((glass / "model.fbx").read_bytes(), b"independent glass model")
        manifest = json.loads((glass / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "Glass")
        self.assertNotEqual(manifest["stableId"], stable)
        self.assertEqual(manifest["stableId"], copied[rr.EXPORT_STABLE_ID_PROP])
        self.assertEqual(manifest["sourceObject"], "Glass")
        self.assertEqual(owner[rr.EXPORT_STABLE_ID_PROP], stable)

    def test_failed_staged_export_preserves_original_files_guid_and_owner(self):
        stable = "rr_asset_protected_wall"
        package = self.package("Wall", stable)
        owner = mesh_object("Renamed_Wall")
        owner[rr.EXPORT_STABLE_ID_PROP] = stable
        owner[rr.EXPORT_LAST_ID_PROP] = "Wall"
        rr.prepare_export_identity(owner, self.settings)
        select_only(owner)
        files = tree_bytes(self.output)
        before = self.full_snapshot([owner])
        staged_paths = []

        def writer(_root, path):
            staged_paths.append(Path(path))
            Path(path).write_bytes(b"incomplete staged model")
            raise RuntimeError("fixture FBX failure")

        with mock.patch.object(rr, "export_fbx", side_effect=writer):
            with self.assertRaisesRegex(RuntimeError, "fixture FBX failure"):
                rr.export_builder_asset(owner, self.settings, export_model=True,
                                        include_icon=False, queue_import=False)
        self.assertEqual(len(staged_paths), 1)
        self.assertNotEqual(staged_paths[0].parent, package)
        self.assertEqual(tree_bytes(self.output), files)
        self.assert_unchanged([owner], before)
        self.assertEqual(rr.export_asset_id(owner), "Wall")
        self.assertFalse((self.output / owner.name).exists())

    def test_ambiguous_legacy_export_stops_before_writer_or_package_changes(self):
        stable = "rr_asset_ambiguous_export"
        owner = mesh_object("Wall")
        self.legacy_identity(owner, stable, "Wall")
        copied = self.duplicate(owner, "Glass")
        self.package("Wall", stable)
        reset_runtime_guards()
        select_only(copied)
        before = self.full_snapshot([owner, copied])
        files = tree_bytes(self.output)
        with mock.patch.object(rr, "export_fbx") as writer:
            with self.assertRaisesRegex(RuntimeError, "(?i)identity conflict"):
                rr.export_builder_asset(copied, self.settings, export_model=True,
                                        include_icon=False, queue_import=False)
            writer.assert_not_called()
        self.assert_unchanged([owner, copied], before)
        self.assertEqual(tree_bytes(self.output), files)


def main():
    if hasattr(bpy.types.Scene, "rr_builder_export_settings"):
        raise RuntimeError("Use a disposable --factory-startup Blender process.")
    if Path(rr.__file__).resolve() != (ROOT / "addons" / "random_realm_builder_exporter" / "__init__.py").resolve():
        raise RuntimeError("Import the canonical repository package for these regressions.")
    rr.register()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(ExportIdentityTrackingTests)
        print("EXPORT_IDENTITY_TRACKING_TEST_COUNT=" + str(suite.countTestCases()))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise RuntimeError("Export identity tracking regressions failed.")
        print("RR_EXPORT_IDENTITY_TRACKING_BLENDER_PASS tests=" + str(result.testsRun))
    finally:
        rr.unregister()


if __name__ == "__main__":
    main()
