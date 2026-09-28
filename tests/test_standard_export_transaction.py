"""Small filesystem/fault-injection checks; no Blender process is launched."""

import ast
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


if __name__ == "__main__":
    # Blender retains its own CLI flags in sys.argv when running --python.
    unittest.main(argv=[__file__], verbosity=2)
