"""Small filesystem/fault-injection checks; no Blender process is launched."""

import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch


ADDON = Path(__file__).resolve().parents[1] / "addons" / "random_realm_builder_exporter"
SPEC = importlib.util.spec_from_file_location(
    "rr_standard_export_transaction", ADDON / "rr_standard_export_transaction.py"
)
transaction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transaction)


def tree_bytes(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


class StandardExportTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.cache = self.root / "transactions"
        self.package = self.output / "Wall_A"
        self.package.mkdir(parents=True)
        self.write_package(self.package, b"old model")
        (self.package / "model.fbx.meta").write_text("guid: model-guid\n")
        (self.package / "textures.meta").write_text("guid: texture-folder-guid\n")
        (self.package / "textures" / "base.png.meta").write_text("guid: texture-guid\n")
        (self.output / "Wall_A.meta").write_text("guid: package-guid\n")
        self.original = tree_bytes(self.output)
        self.marker = self.output / ".rr-Wall_A.publishing"

    def tearDown(self):
        self.temporary.cleanup()

    def write_package(self, package, model):
        package.mkdir(parents=True, exist_ok=True)
        (package / "model.fbx").write_bytes(model)
        (package / "textures").mkdir(exist_ok=True)
        (package / "textures" / "base.png").write_bytes(b"image:" + model)
        manifest = {
            "id": "Wall_A", "modelFile": "model.fbx", "iconFile": "",
            "materialMaps": [{"material": "Wall", "baseColor": "textures/base.png"}],
            "uvExport": {"modelSha256": hashlib.sha256(model).hexdigest()},
        }
        (package / "manifest.json").write_text(json.dumps(manifest))

    def run_export(self, callback):
        return transaction.export_package(str(self.output), "Wall_A", str(self.cache), callback)

    def new_export(self, staging_root):
        self.assertTrue(self.marker.is_file())
        package = Path(staging_root) / "Wall_A"
        shutil.rmtree(package / "textures")
        self.write_package(package, b"new model")
        return ("Wall_A", "Prop", "exported")

    def assert_old_package_preserved(self):
        self.assertEqual(self.original, tree_bytes(self.output))
        self.assertFalse(self.marker.exists())
        self.assertEqual([], list(self.cache.iterdir()))

    def test_export_failure_after_texture_deletion_preserves_complete_old_package(self):
        original_utime = os.utime
        notified = []
        def observe_utime(path, *args, **kwargs):
            if Path(path) == self.package / "manifest.json":
                self.assertFalse(self.marker.exists())
                notified.append(True)
            return original_utime(path, *args, **kwargs)
        def fail(staging_root):
            self.new_export(staging_root)
            raise RuntimeError("texture encoding failed")

        with patch.object(transaction.os, "utime", side_effect=observe_utime):
            with self.assertRaisesRegex(RuntimeError, "texture encoding"):
                self.run_export(fail)
        self.assertEqual([True], notified)
        self.assert_old_package_preserved()

    def test_incomplete_staged_manifest_preserves_old_package(self):
        def incomplete(staging_root):
            self.new_export(staging_root)
            (Path(staging_root) / "Wall_A" / "textures" / "base.png").unlink()

        with self.assertRaisesRegex(RuntimeError, "resource is missing"):
            self.run_export(incomplete)
        self.assert_old_package_preserved()

    def test_stale_model_hash_preserves_old_package(self):
        def corrupt(staging_root):
            self.new_export(staging_root)
            (Path(staging_root) / "Wall_A" / "model.fbx").write_bytes(b"partial FBX")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            self.run_export(corrupt)
        self.assert_old_package_preserved()

    def test_compatibility_texture_paths_are_validated(self):
        for key in ("bakedBaseColor", "baseMap", "occlusion", "ao", "metallicSmoothness"):
            with self.subTest(key=key):
                def missing(staging_root):
                    self.new_export(staging_root)
                    path = Path(staging_root) / "Wall_A" / "manifest.json"
                    manifest = json.loads(path.read_text())
                    manifest["materialMaps"][0][key] = "textures/missing.png"
                    path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(RuntimeError, "resource is missing"):
                    self.run_export(missing)
                self.assert_old_package_preserved()

    def test_icon_only_reuses_existing_verified_model(self):
        def icon_only(staging_root):
            package = Path(staging_root) / "Wall_A"
            (package / "icon.png").write_bytes(b"new icon")
            path = package / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest.update(iconFile="icon.png", exportedResources=["icon"])
            path.write_text(json.dumps(manifest))
        self.run_export(icon_only)
        self.assertEqual(b"old model", (self.package / "model.fbx").read_bytes())
        self.assertEqual(b"new icon", (self.package / "icon.png").read_bytes())

    def test_publication_failure_restores_previous_directory(self):
        original_replace = os.replace

        def fail_new_package(source, destination):
            self.assertTrue(self.marker.exists())
            if Path(source).parent.name == "staged":
                raise PermissionError("Unity held a file")
            return original_replace(source, destination)

        with patch.object(transaction.os, "replace", side_effect=fail_new_package):
            with self.assertRaisesRegex(PermissionError, "Unity held"):
                self.run_export(self.new_export)
        self.assert_old_package_preserved()

    def test_failed_rollback_retains_marker_and_recovery_copy(self):
        original_replace = os.replace

        def fail_new_and_recovery(source, destination):
            if Path(source).parent.name == "staged" or Path(source).name == "previous":
                raise PermissionError("blocked directory")
            return original_replace(source, destination)

        with patch.object(transaction.os, "replace", side_effect=fail_new_and_recovery):
            with self.assertRaisesRegex(RuntimeError, "recovery files retained"):
                self.run_export(self.new_export)
        details = json.loads(self.marker.read_text())
        backup = Path(details["transactionRoot"]) / "previous"
        self.assertEqual(b"old model", (backup / "model.fbx").read_bytes())
        self.assertTrue((backup / "textures" / "base.png").exists())

    def test_success_keeps_surviving_meta_and_signals_after_unlock(self):
        original_utime = os.utime
        signaled = []

        def observe_utime(path, *args, **kwargs):
            if Path(path) == self.package / "manifest.json":
                self.assertFalse(self.marker.exists())
                signaled.append(True)
            return original_utime(path, *args, **kwargs)

        with patch.object(transaction.os, "utime", side_effect=observe_utime):
            result = self.run_export(self.new_export)
        self.assertEqual(("Wall_A", "Prop", "exported"), result)
        self.assertEqual(b"new model", (self.package / "model.fbx").read_bytes())
        for relative, content in self.original.items():
            if relative.endswith(".meta"):
                self.assertEqual(content, (self.output / relative).read_bytes(), relative)
        self.assertEqual([True], signaled)
        self.assertEqual([], list(self.cache.iterdir()))

    def test_surface_snapshot_source_rebased_to_published_package(self):
        def export_snapshot(staging_root):
            self.new_export(staging_root)
            package = Path(staging_root) / "Wall_A"
            snapshot = package / "surface_text_source.rrblend"
            snapshot.write_bytes(b"blend snapshot")
            path = package / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["sourceBlend"] = str(snapshot)
            path.write_text(json.dumps(manifest))

        self.run_export(export_snapshot)
        manifest = json.loads((self.package / "manifest.json").read_text())
        self.assertEqual(str(self.package / "surface_text_source.rrblend"), manifest["sourceBlend"])
        self.assertTrue(Path(manifest["sourceBlend"]).is_file())

    def test_other_writer_marker_is_never_removed(self):
        self.marker.write_text("another writer")
        with self.assertRaises(FileExistsError):
            self.run_export(self.new_export)
        self.assertEqual("another writer", self.marker.read_text())
        self.marker.unlink()
        self.assert_old_package_preserved()

    def test_invalid_resource_cannot_escape_package(self):
        def invalid(staging_root):
            self.new_export(staging_root)
            path = Path(staging_root) / "Wall_A" / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["modelFile"] = "../../outside.fbx"
            path.write_text(json.dumps(manifest))

        with self.assertRaisesRegex(RuntimeError, "invalid"):
            self.run_export(invalid)
        self.assert_old_package_preserved()

    def test_new_package_export_failure_leaves_no_partial_target(self):
        shutil.rmtree(self.package)
        def fail(staging_root):
            self.write_package(Path(staging_root) / "Wall_A", b"partial")
            raise RuntimeError("failed")
        with self.assertRaisesRegex(RuntimeError, "failed"):
            self.run_export(fail)
        self.assertFalse(self.package.exists())
        self.assertFalse(self.marker.exists())


class ExporterBoundaryTests(unittest.TestCase):
    def extract(self, name, namespace):
        tree = ast.parse((ADDON / "__init__.py").read_text(encoding="utf-8-sig"))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(ADDON / "__init__.py"), "exec"), namespace)
        return namespace[name]

    def test_only_ordinary_standard_uses_transaction_and_retire_after_commit(self):
        events = []
        settings = SimpleNamespace(output_root="output", standard=True)
        namespace = {
            "export_mode_is_standard": lambda value: value.standard,
            "object_manager_variant_group_root": lambda obj: obj.variant,
            "validate_standard_output_route": lambda value: None,
            "validate_reference_layout_settings": lambda value: None,
            "mesh_objects_have_export_geometry": lambda value: True,
            "get_export_asset_meshes": lambda value: [],
            "validate_export_identity": lambda value: None,
            "export_asset_id": lambda obj: "Wall_A",
            "variant_export_transaction_parent": lambda value: "transactions",
            "ExportSettingsOutputRootProxy": lambda value, root: SimpleNamespace(output_root=root),
            "retire_standard_flat_model_alias": lambda *args: events.append("retire"),
            "os": os,
            "read_existing_manifest": lambda path: {"modelFile": "model.fbx"},
        }
        def contents(*args, **kwargs):
            events.append((args[1].output_root, kwargs.get("retire_legacy_alias", True)))
            return "result"
        def publish(output, asset_id, parent, callback):
            result = callback("staged")
            events.append("committed")
            return result
        namespace["_export_builder_asset_contents"] = contents
        namespace["rr_standard_export_transaction"] = SimpleNamespace(export_package=publish)
        export = self.extract("export_builder_asset", namespace)
        self.assertEqual("result", export(SimpleNamespace(variant=None), settings))
        self.assertEqual([("staged", False), "committed", "retire"], events)
        for standard, variant in ((False, None), (True, object())):
            events.clear()
            settings.standard = standard
            export(SimpleNamespace(variant=variant), settings)
            self.assertEqual([("output", True)], events)

    def test_library_and_custom_staging_routes_remain_allowed(self):
        namespace = {
            "os": os,
            "bpy": SimpleNamespace(path=SimpleNamespace(abspath=os.path.abspath)),
            "UNITY_TEMP_OUTPUT_ROOT": os.path.abspath("Assets/~Temp/BlenderBridge"),
            "export_mode_is_standard": lambda settings: True,
        }
        self.extract("is_managed_builder_bridge_output_root", namespace)
        validate = self.extract("validate_standard_output_route", namespace)
        for path in ("Library/RandomRealmBuilder/ExportTransactions/package", "custom/export"):
            self.assertTrue(validate(SimpleNamespace(output_root=os.path.abspath(path))))
        with self.assertRaisesRegex(RuntimeError, "managed BlenderBridge"):
            validate(SimpleNamespace(output_root=namespace["UNITY_TEMP_OUTPUT_ROOT"]))

    def test_identity_failure_happens_before_transaction_creation(self):
        transaction_started = []
        def reject_identity(obj):
            raise RuntimeError("duplicate export identity")
        namespace = {
            "validate_standard_output_route": lambda value: None,
            "validate_reference_layout_settings": lambda value: None,
            "mesh_objects_have_export_geometry": lambda value: True,
            "get_export_asset_meshes": lambda value: [],
            "validate_export_identity": reject_identity,
            "rr_standard_export_transaction": SimpleNamespace(
                export_package=lambda *args: transaction_started.append(True)
            ),
        }
        export = self.extract("export_builder_asset", namespace)
        with self.assertRaisesRegex(RuntimeError, "duplicate export identity"):
            export(object(), SimpleNamespace(output_root="unused"))
        self.assertEqual([], transaction_started)

    def test_surface_snapshot_timeout_cleans_temporary_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = root / "write_surface_text_snapshot.py"
            helper.write_text("# not executed")
            binary = root / "blender.exe"
            binary.write_bytes(b"not executed")
            snapshot = root / "source.rrblend"
            def write_library(path, *args, **kwargs):
                Path(path).write_bytes(b"temporary library")
            def timeout(command, **kwargs):
                self.assertEqual(120, kwargs["timeout"])
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            namespace = {
                "os": os, "tempfile": tempfile, "__file__": str(root / "__init__.py"),
                "bpy": SimpleNamespace(
                    app=SimpleNamespace(binary_path=str(binary)),
                    data=SimpleNamespace(libraries=SimpleNamespace(write=write_library)),
                ),
                "_surface_text_snapshot_objects": lambda obj: {"object"},
                "subprocess": SimpleNamespace(run=timeout),
            }
            write_snapshot = self.extract("write_surface_text_source_snapshot", namespace)
            with self.assertRaises(subprocess.TimeoutExpired):
                write_snapshot(object(), str(snapshot))
            self.assertEqual([], list(root.glob(".surface_text_source_*")))


class VariantPublicationMetadataTests(unittest.TestCase):
    """Exercise real directory publication; lease/membership services are isolated."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        self.staging = self.root / "staged"
        self.members = [SimpleNamespace(name="Door_A"), SimpleNamespace(name="Door_B")]
        self.queued = []
        for member in self.members:
            package = self.output / member.name
            package.mkdir(parents=True)
            (package / "model.fbx").write_bytes(b"old model")
            (package / "surface_text_source.rrblend").write_bytes(b"old snapshot")
            (package / "textures").mkdir()
            (package / "textures" / "base.png").write_bytes(b"old texture")
            (package / "textures" / "removed.png").write_bytes(b"removed texture")
            for relative in ("model.fbx", "surface_text_source.rrblend", "textures", "textures/base.png", "textures/removed.png"):
                (package / (relative + ".meta")).write_text(f"guid: {member.name}/{relative}\n")
            (self.output / (member.name + ".meta")).write_text(f"guid: {member.name}\n")
            manifest = {
                "id": member.name, "stableId": "stable-" + member.name,
                "sourceBlend": str(package / "surface_text_source.rrblend"),
                "modelFile": "model.fbx", "surfaceText": [{"text": "Old Text"}],
            }
            (package / "manifest.json").write_text(json.dumps(manifest))
            staged = self.staging / member.name
            shutil.copytree(package, staged)
            shutil.rmtree(staged / "textures")
            (staged / "textures").mkdir()
            (staged / "textures" / "base.png").write_bytes(b"fresh texture")
            # Simulate regenerated resource trees without their Unity sidecars.
            for sidecar in staged.rglob("*.meta"):
                sidecar.unlink()
        self.original = tree_bytes(self.output)

        def verify(root, members, expected_revision=None):
            paths = [str(Path(root) / member.name / "manifest.json") for member in members]
            self.assertTrue(all(Path(path).is_file() for path in paths))
            return paths, "revision-1", "DoorVariants"

        namespace = {
            "os": os, "shutil": shutil, "tempfile": tempfile,
            "verify_variant_membership_package": verify,
            "export_asset_id": lambda member: member.name,
            "create_variant_publish_claim": lambda *args: ("claim-path", "writer-lease"),
            "remove_variant_publish_claim": lambda *args: None,
            "remove_export_path": shutil.rmtree,
            "rr_standard_export_transaction": transaction,
            "queue_unity_builder_import": lambda paths: self.queued.extend(paths),
        }
        tree = ast.parse((ADDON / "__init__.py").read_text(encoding="utf-8-sig"))
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "publish_staged_variant_group")
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(ADDON / "__init__.py"), "exec"), namespace)
        self.publish = namespace["publish_staged_variant_group"]

    def tearDown(self):
        self.temporary.cleanup()

    def make_fresh_snapshot(self, member):
        package = self.staging / member.name
        (package / "model.fbx").write_bytes(b"fresh model")
        snapshot = package / "surface_text_source.rrblend"
        snapshot.write_bytes(b"fresh snapshot")
        path = package / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest.update(sourceBlend=str(snapshot), surfaceText=[{"text": "Fresh Text"}])
        path.write_text(json.dumps(manifest))

    def test_shared_metadata_helper_preserves_existing_source_binding_and_surviving_guids(self):
        member = self.members[0]
        staged = self.staging / member.name
        previous = self.output / member.name
        original_manifest = (staged / "manifest.json").read_bytes()
        transaction.prepare_published_metadata(str(staged), str(previous))
        self.assertEqual(original_manifest, (staged / "manifest.json").read_bytes())
        self.assertEqual(b"old snapshot", (staged / "surface_text_source.rrblend").read_bytes())
        for relative in ("model.fbx.meta", "surface_text_source.rrblend.meta", "textures.meta", "textures/base.png.meta"):
            self.assertEqual((previous / relative).read_bytes(), (staged / relative).read_bytes(), relative)
        self.assertFalse((staged / "textures" / "removed.png.meta").exists())

    def test_variant_publication_rebases_fresh_snapshot_preserves_cached_binding_and_guids(self):
        self.make_fresh_snapshot(self.members[0])
        manifests, revision = self.publish(str(self.staging), str(self.output), self.members)
        self.assertEqual("revision-1", revision)
        self.assertEqual(manifests, self.queued)
        self.assertFalse(self.staging.exists())
        for index, member in enumerate(self.members):
            with self.subTest(member=member.name):
                package = self.output / member.name
                manifest = json.loads((package / "manifest.json").read_text())
                self.assertEqual(str(package / "surface_text_source.rrblend"), manifest["sourceBlend"])
                self.assertEqual("stable-" + member.name, manifest["stableId"])
                self.assertEqual(b"fresh snapshot" if index == 0 else b"old snapshot", Path(manifest["sourceBlend"]).read_bytes())
                self.assertEqual(b"fresh model" if index == 0 else b"old model", (package / "model.fbx").read_bytes())
                for relative, contents in self.original.items():
                    if relative.endswith(".meta") and "removed.png" not in relative:
                        self.assertEqual(contents, (self.output / relative).read_bytes(), relative)
                self.assertFalse((package / "textures" / "removed.png.meta").exists())

    def test_missing_second_variant_snapshot_rolls_back_all_published_members(self):
        for member in self.members:
            self.make_fresh_snapshot(member)
        (self.staging / self.members[1].name / "surface_text_source.rrblend").unlink()
        with self.assertRaisesRegex(RuntimeError, "snapshot is missing"):
            self.publish(str(self.staging), str(self.output), self.members)
        self.assertEqual(self.original, tree_bytes(self.output))
        self.assertEqual([], self.queued)
        self.assertFalse(self.staging.exists())


class ModelIconContractTests(unittest.TestCase):
    """Exercise real export/manifest branches against temporary package files."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.package = self.directory / "Door"
        self.package.mkdir()
        self.model = self.package / "model.fbx"
        self.snapshot = self.package / "surface_text_source.rrblend"
        self.manifest_path = self.package / "manifest.json"
        self.model.write_bytes(b"verified cached model")
        self.snapshot.write_bytes(b"snapshot for cached model")
        (self.package / "icon.png").write_bytes(b"old icon")
        (self.package / "model.fbx.meta").write_text("guid: existing-model-guid\n")
        self.old_surface = [{
            "text": "Tasks", "editableVersion": 1,
            "samplingSurfaceExportObjectName": "OldSample",
            "frameObjectNames": ["O", "X", "Y", "Z"],
        }, {"text": "Intro", "editableVersion": 1, "exportObjectName": "OldIntro"}]
        self.new_surface = [{
            "text": "Changed", "editableVersion": 0,
            "exportObjectName": "NewText",
        }]
        self.current_layout = {"role": "member", "relativeAuthoringMatrix": ["new relative placement"]}
        self.old = {
            "id": "Door", "stableId": "stable-door", "previousIds": ["Door_Old"],
            "sourceObject": "Old FBX Node", "sourceBlend": str(self.snapshot),
            "surfaceText": self.old_surface, "modelFile": "model.fbx", "iconFile": "icon.png",
            "exportedResources": ["icon"],
            "bounds": {"center": [1, 2, 3], "size": [4, 5, 6]},
            "uvExport": {"modelSha256": hashlib.sha256(self.model.read_bytes()).hexdigest()},
            "materialMaps": [{"material": "Lit"}], "warnings": [],
            "referenceLayout": {"role": "member", "relativeAuthoringMatrix": ["old placement"]},
        }
        self.write_old()
        self.surface_calls = []
        self.model_exports = []
        self.import_requests = []
        self.root = SimpleNamespace(name="Renamed Authoring Node")
        self.settings = SimpleNamespace(
            output_root=str(self.directory), export_mode="GENERAL", profile_name="Default",
            include_model_with_export=True, include_icon_with_export=False, skip_existing_exports=True,
        )

        def current_surface(root):
            self.surface_calls.append(root.name)
            return self.new_surface

        def export_fbx(root, path):
            self.model_exports.append(root.name)
            Path(path).write_bytes(b"fresh model")
            return []

        def write_snapshot(root, path):
            Path(path).write_bytes(b"fresh snapshot")
            return path

        self.namespace = {
            "os": os, "json": json, "uuid": uuid, "datetime": datetime, "timezone": timezone,
            "bpy": SimpleNamespace(data=SimpleNamespace(filepath="current-authoring.blend")),
            "validate_export_identity": lambda root: None,
            "ensure_export_identity": lambda root, asset_id: ("stable-door", ["Door_Old"]),
            "build_surface_text_manifest": current_surface,
            "mesh_world_bounds": lambda root: (
                SimpleNamespace(x=8, y=9, z=10), SimpleNamespace(x=11, y=12, z=13)),
            "build_reference_layout_for_export": lambda *args, **kwargs: self.current_layout,
            "export_mode_uses_reference_layout": lambda settings: True,
            "export_asset_id": lambda root: "Door", "infer_export_asset_type": lambda *args: "Prop",
            "infer_asset_category": lambda *args: "Props", "build_group_manifest": lambda *args, **kwargs: None,
            "shared_builder_icon_root": lambda root: root,
            "build_material_surface_contracts": lambda root: {"Lit": {}},
            # Existing complete packages must still rebake whenever Model is requested.
            "output_has_requested_resources": lambda *args, **kwargs: True,
            "current_uv_export_contract": lambda path: {
                "modelSha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()},
            "export_fbx": export_fbx, "write_surface_text_source_snapshot": write_snapshot,
            "build_material_map_manifest": lambda *args: ([{"material": "FreshLit"}], []),
            "object_manager_variant_group_root": lambda root: None,
            "render_or_copy_shared_icon": lambda root, settings, path, **kwargs: Path(path).write_bytes(b"fresh icon"),
            "queue_unity_builder_import": lambda paths: self.import_requests.extend(paths),
            "EXPORT_MODE_BUILDING": "BUILDING", "EXPORT_MODE_GENERAL": "GENERAL",
        }
        tree = ast.parse((ADDON / "__init__.py").read_text(encoding="utf-8-sig"))
        names = {
            "write_manifest", "read_existing_manifest", "_export_builder_asset_contents",
            "render_icon_objects", "existing_uv_export_contract", "export_mode_is_standard",
        }
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        self.assertEqual(names, {node.name for node in functions})
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(ADDON / "__init__.py"), "exec"), self.namespace)

    def tearDown(self):
        self.temporary.cleanup()

    def write_old(self):
        self.manifest_path.write_text(json.dumps(self.old), encoding="utf-8")

    def run_contents(self, model=None, icon=None):
        result = self.namespace["_export_builder_asset_contents"](
            self.root, self.settings, export_model=model, include_icon=icon,
            retire_legacy_alias=False)
        return result, json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def assert_identity_preserved(self, manifest):
        for field in ("id", "stableId", "previousIds"):
            self.assertEqual(self.old[field], manifest[field], field)
        self.assertEqual("guid: existing-model-guid\n", (self.package / "model.fbx.meta").read_text())

    def assert_cached_contract(self, manifest):
        for field in ("sourceObject", "sourceBlend", "surfaceText", "bounds", "uvExport", "materialMaps"):
            if field in self.old:
                self.assertEqual(self.old[field], manifest[field], field)
            else:
                self.assertNotIn(field, manifest)
        self.assert_identity_preserved(manifest)
        self.assertEqual(self.current_layout, manifest["referenceLayout"])
        self.assertEqual(b"verified cached model", self.model.read_bytes())
        self.assertEqual(b"snapshot for cached model", self.snapshot.read_bytes())
        self.assertEqual([], self.surface_calls)
        self.assertEqual([], self.model_exports)

    def assert_fresh_model(self, manifest):
        self.assert_identity_preserved(manifest)
        self.assertEqual(self.new_surface, manifest["surfaceText"])
        self.assertEqual(self.root.name, manifest["sourceObject"])
        self.assertEqual(str(self.snapshot), manifest["sourceBlend"])
        self.assertEqual(b"fresh model", self.model.read_bytes())
        self.assertEqual(b"fresh snapshot", self.snapshot.read_bytes())
        self.assertEqual([{"material": "FreshLit"}], manifest["materialMaps"])
        self.assertEqual({"center": [8, 9, 10], "size": [11, 12, 13]}, manifest["bounds"])
        self.assertEqual(hashlib.sha256(b"fresh model").hexdigest(), manifest["uvExport"]["modelSha256"])
        self.assertEqual(self.current_layout, manifest["referenceLayout"])

    def test_model_request_reexports_despite_legacy_skip_in_both_modes(self):
        for mode in ("GENERAL", "BUILDING"):
            with self.subTest(mode=mode):
                self.settings.export_mode = mode
                self.write_old()
                self.model_exports.clear()
                result, manifest = self.run_contents()
                self.assertEqual("exported", result[2])
                self.assertEqual(["model"], manifest["exportedResources"])
                self.assertEqual([self.root.name], self.model_exports)
                self.assert_fresh_model(manifest)
                self.assertEqual(b"old icon", (self.package / "icon.png").read_bytes())
        self.assertEqual([str(self.manifest_path)], self.import_requests)

    def test_model_request_reexports_when_legacy_skip_property_is_absent(self):
        del self.settings.skip_existing_exports
        result, manifest = self.run_contents()
        self.assertEqual("exported", result[2])
        self.assertEqual([self.root.name], self.model_exports)
        self.assert_fresh_model(manifest)

    def test_model_and_icon_refresh_both_resources_despite_legacy_skip(self):
        self.settings.include_icon_with_export = True
        _, manifest = self.run_contents()
        self.assertEqual(["model", "icon"], manifest["exportedResources"])
        self.assertEqual([self.root.name], self.model_exports)
        self.assertEqual(b"fresh icon", (self.package / "icon.png").read_bytes())
        self.assert_fresh_model(manifest)

    def test_icon_only_preserves_model_contract_in_both_modes(self):
        self.settings.include_model_with_export = False
        self.settings.include_icon_with_export = True
        for mode in ("GENERAL", "BUILDING"):
            with self.subTest(mode=mode):
                self.settings.export_mode = mode
                _, manifest = self.run_contents()
                self.assertEqual(["icon"], manifest["exportedResources"])
                self.assertEqual(b"fresh icon", (self.package / "icon.png").read_bytes())
                self.assert_cached_contract(manifest)
        self.assertEqual([str(self.manifest_path)], self.import_requests)

    def test_icon_only_does_not_invent_missing_model_source_fields(self):
        for field in ("sourceBlend", "sourceObject", "surfaceText"):
            self.old.pop(field)
        self.write_old()
        _, manifest = self.run_contents(model=False, icon=True)
        self.assert_cached_contract(manifest)

    def test_icon_only_preserves_explicit_empty_surface_text(self):
        self.old["surfaceText"] = []
        self.write_old()
        _, manifest = self.run_contents(model=False, icon=True)
        self.assert_cached_contract(manifest)

    def test_second_model_export_rebakes_and_removes_deleted_text_contract(self):
        self.run_contents()
        self.new_surface = []
        _, manifest = self.run_contents()
        self.assertEqual([self.root.name, self.root.name], self.model_exports)
        self.assertNotIn("surfaceText", manifest)
        self.assertEqual("current-authoring.blend", manifest["sourceBlend"])
        self.assert_identity_preserved(manifest)

    def test_no_resource_request_leaves_existing_package_untouched(self):
        original = tree_bytes(self.package)
        with self.assertRaisesRegex(RuntimeError, "Enable Model, Icon"):
            self.run_contents(model=False, icon=False)
        self.assertEqual(original, tree_bytes(self.package))
        self.assertEqual([], self.model_exports)

    def run_separate_icon_renderer(self):
        noop = lambda *args, **kwargs: None
        self.namespace.update({
            "sync_object_manager_names": noop, "validate_standard_output_route": noop,
            "validate_reference_layout_settings": noop, "show_builder_popup": noop,
            "expand_related_export_roots": lambda roots: roots,
            "prepare_variant_export_transactions": lambda *args: ([], {}, []),
            "finalize_variant_export_transactions": lambda *args: (set(), []),
            "queue_item_for_root": lambda *args: None, "load_image_for_preview": noop,
        })
        self.namespace["bpy"].ops = SimpleNamespace(object=SimpleNamespace(select_all=noop))
        context = SimpleNamespace(view_layer=SimpleNamespace(objects=SimpleNamespace(active=None)), selected_objects=[])
        result = self.namespace["render_icon_objects"]([self.root], self.settings, context, "selected")
        self.assertEqual({"FINISHED"}, result)
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def test_separate_icon_renderer_preserves_model_contract(self):
        manifest = self.run_separate_icon_renderer()
        self.assertEqual(["icon"], manifest["exportedResources"])
        self.assertEqual(b"fresh icon", (self.package / "icon.png").read_bytes())
        self.assert_cached_contract(manifest)

    def test_separate_icon_renderer_does_not_invent_missing_model_source_fields(self):
        for field in ("sourceBlend", "sourceObject", "surfaceText"):
            self.old.pop(field)
        self.write_old()
        self.assert_cached_contract(self.run_separate_icon_renderer())


if __name__ == "__main__":
    # Blender retains its own CLI flags in sys.argv when running --python.
    unittest.main(argv=[__file__], verbosity=2)
