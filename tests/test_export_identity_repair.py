"""Small package and metadata repair checks; no Blender process is launched."""

import hashlib
import importlib.util
import json
import copy
from pathlib import Path
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / "addons" / "random_realm_builder_exporter" / "rr_export_identity_repair.py"
SPEC = importlib.util.spec_from_file_location("rr_export_identity_repair", MODULE)
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)


class Object(dict):
    def __init__(self, name, **metadata):
        super().__init__(metadata)
        self.name = name
        self.core = False
        self.group = False
        self.editable = True
        self.data = object()


def asset_id(root):
    return root.get(repair.ASSET_PROP) or root.name


def previous_ids(root):
    return json.loads(root.get(repair.PREVIOUS_PROP, "[]"))


class IdentityRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.package = self.directory / "Floor_A"
        self.package.mkdir()
        self.model = self.package / "model.fbx"
        self.model.write_bytes(b"verified model data")
        self.manifest = {
            "id": "Floor_A", "stableId": "rr_asset_original", "previousIds": ["Old_Floor"],
            "displayName": "Floor A", "sourceObject": "Floor_A", "modelFile": "model.fbx",
            "uvExport": {"modelSha256": hashlib.sha256(self.model.read_bytes()).hexdigest()},
        }
        self.write_manifest()

    def tearDown(self):
        self.temporary.cleanup()

    def write_manifest(self):
        (self.package / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def tree_bytes(self):
        return {str(path.relative_to(self.directory)): path.read_bytes()
                for path in self.directory.rglob("*") if path.is_file()}

    def build_plan(self, target, roots, detach=(), package=None):
        return repair.build_rebind_plan(
            target, package or repair.read_export_package(self.package), roots,
            asset_id=asset_id, previous_ids=previous_ids,
            is_core=lambda root: root.core, is_group=lambda root: root.group,
            is_editable=lambda root: root.editable,
            unbound_asset_id=lambda root: root.name, detach=detach,
        )

    def pair(self):
        target = Object("Floor_A", rr_export_stable_id="rr_asset_original",
                        rr_export_last_id="Floor_A", rr_export_previous_ids='["Older_Floor"]')
        duplicate = Object("Floor_A_001", **dict(target))
        duplicate["rr_reference_marked"] = True
        duplicate["rr_reference_stable_id"] = "rr_asset_original"
        return target, duplicate

    def test_folder_manifest_model_and_meta_entries_share_verified_identity(self):
        expected = repair.read_export_package(self.package)
        original = self.tree_bytes()
        for selection in (self.package / "manifest.json", self.model,
                          Path(str(self.package) + ".meta"), Path(str(self.model) + ".meta"),
                          self.package / "manifest.json.meta"):
            with self.subTest(selection=selection):
                self.assertEqual(expected, repair.read_export_package(selection))
        self.assertEqual("Floor A", expected["label"])
        self.assertEqual(original, self.tree_bytes())

    def test_invalid_contract_and_missing_models_are_rejected_without_writes(self):
        for patch in ({"id": "Other"}, {"stableId": ""}, {"previousIds": ["../Other"]},
                      {"modelFile": "../outside.fbx"}, {"modelFile": "C:\\outside.fbx"},
                      {"modelFile": "missing.fbx"}, {"uvExport": {"modelSha256": "0" * 64}}):
            with self.subTest(patch=patch):
                original_manifest = dict(self.manifest)
                self.manifest.update(patch)
                self.write_manifest()
                original = self.tree_bytes()
                with self.assertRaises(ValueError):
                    repair.read_export_package(self.package)
                self.assertEqual(original, self.tree_bytes())
                self.manifest = original_manifest
                self.write_manifest()

    def test_publication_marker_blocks_read(self):
        marker = self.directory / ".rr-Floor_A.publishing"
        marker.write_text("recover pending")
        original = self.tree_bytes()
        with self.assertRaisesRegex(ValueError, "published"):
            repair.read_export_package(self.package)
        self.assertEqual(original, self.tree_bytes())

    def test_managed_group_and_variant_packages_require_the_group_workflow(self):
        for field, value in (("group", {"type": "assembly"}), ("variantGroupId", "group_owner")):
            with self.subTest(field=field):
                self.manifest[field] = value
                self.write_manifest()
                original = self.tree_bytes()
                with self.assertRaisesRegex(ValueError, "group workflow"):
                    repair.read_export_package(self.package)
                self.assertEqual(original, self.tree_bytes())
                del self.manifest[field]

    def test_legacy_package_fingerprint_detects_model_edit_without_hash_contract(self):
        del self.manifest["uvExport"]
        self.write_manifest()
        before = repair.read_export_package(self.model)
        self.model.write_bytes(b"changed model")
        self.assertNotEqual(before["fingerprint"], repair.read_export_package(self.model)["fingerprint"])

    def test_selected_fbx_must_be_the_manifest_model(self):
        other = self.package / "other.fbx"
        other.write_bytes(b"other")
        with self.assertRaisesRegex(ValueError, "does not match"):
            repair.read_export_package(other)

    def test_malformed_manifest_and_empty_model_rejected(self):
        (self.package / "manifest.json").write_text("not json")
        with self.assertRaises(ValueError):
            repair.read_export_package(self.package)
        self.write_manifest()
        self.model.write_bytes(b"")
        with self.assertRaises(ValueError):
            repair.read_export_package(self.package)

    def test_explicit_copy_detachment_preserves_original_aliases_and_all_geometry(self):
        target, duplicate = self.pair()
        original_file_bytes = self.tree_bytes()
        snapshots = [dict(root) for root in (target, duplicate)]
        geometry = [(root.name, root.data) for root in (target, duplicate)]
        plan = self.build_plan(target, [target, duplicate], [duplicate])
        self.assertEqual(snapshots, [dict(root) for root in (target, duplicate)])
        validated = []
        repair.apply_rebind_plan(plan, validate=lambda root: validated.append(root))
        self.assertEqual([target, duplicate], validated)
        self.assertEqual("rr_asset_original", target[repair.STABLE_PROP])
        self.assertEqual(["Old_Floor", "Older_Floor"], previous_ids(target))
        self.assertNotEqual(target[repair.STABLE_PROP], duplicate[repair.STABLE_PROP])
        self.assertEqual("Floor_A_001", duplicate[repair.LAST_PROP])
        self.assertEqual([], previous_ids(duplicate))
        for key in repair.CORE_PROPS + (repair.ASSET_PROP,):
            self.assertNotIn(key, duplicate)
        self.assertEqual(geometry, [(root.name, root.data) for root in (target, duplicate)])
        self.assertEqual(original_file_bytes, self.tree_bytes())

    def test_unapproved_live_owner_and_unrelated_alias_holder_remain_protected(self):
        target, duplicate = self.pair()
        with self.assertRaisesRegex(ValueError, "still conflicts"):
            self.build_plan(target, [target, duplicate])
        unrelated = Object("Different_A", rr_export_stable_id="different_owner",
                           rr_export_previous_ids='["Floor_A"]')
        snapshot = dict(unrelated)
        with self.assertRaisesRegex(ValueError, "still conflicts"):
            self.build_plan(target, [target, unrelated])
        with self.assertRaisesRegex(ValueError, "different asset owner"):
            self.build_plan(target, [target, unrelated], [unrelated])
        self.assertEqual(snapshot, dict(unrelated))

    def test_identity_matching_and_conflict_detection_are_case_insensitive(self):
        target, duplicate = self.pair()
        target[repair.STABLE_PROP] = target[repair.STABLE_PROP].upper()
        duplicate[repair.STABLE_PROP] = duplicate[repair.STABLE_PROP].upper()
        with self.assertRaisesRegex(ValueError, "still conflicts"):
            self.build_plan(target, [target, duplicate])
        plan = self.build_plan(target, [target, duplicate], [duplicate])
        repair.apply_rebind_plan(plan, validate=lambda root: True)
        self.assertEqual("rr_asset_original", target[repair.STABLE_PROP])
        self.assertIn("Older_Floor", previous_ids(target))

    def test_actual_core_group_and_readonly_duplicates_cannot_be_detached(self):
        for flag in ("core", "group", "editable"):
            with self.subTest(flag=flag):
                target, duplicate = self.pair()
                setattr(duplicate, flag, flag != "editable")
                snapshots = [dict(target), dict(duplicate)]
                with self.assertRaises(ValueError):
                    self.build_plan(target, [target, duplicate], [duplicate])
                self.assertEqual(snapshots, [dict(target), dict(duplicate)])
        target, _duplicate = self.pair()
        target.group = True
        with self.assertRaisesRegex(ValueError, "editable independent"):
            self.build_plan(target, [target])

    def test_selected_core_binding_synchronizes_marker_without_inheriting_other_asset_history(self):
        target = Object("Readable_Blender_Name", rr_export_stable_id="old_unrelated",
                        rr_export_last_id="Unrelated_A", rr_export_previous_ids='["Unrelated_Old"]',
                        rr_reference_marked=True, rr_reference_stable_id="old_unrelated")
        target.core = True
        geometry = target.data
        plan = self.build_plan(target, [target])
        repair.apply_rebind_plan(plan, validate=lambda root: True)
        self.assertEqual("Readable_Blender_Name", target.name)
        self.assertIs(geometry, target.data)
        self.assertEqual("Floor_A", asset_id(target))
        self.assertEqual(["Old_Floor"], previous_ids(target))
        self.assertTrue(target["rr_reference_marked"])
        self.assertEqual("rr_asset_original", target["rr_reference_stable_id"])

    def test_selected_noncore_copy_loses_stale_markers_after_explicit_pairing(self):
        target = Object("Readable_Blender_Name", rr_export_stable_id="old_unrelated",
                        rr_reference_marked=True, rr_reference_stable_id="old_unrelated")
        self.assertFalse(target.core)
        plan = self.build_plan(target, [target])
        self.assertEqual(repair.CORE_PROPS, plan["changes"][0]["remove"])
        repair.apply_rebind_plan(plan, validate=lambda root: True)
        self.assertEqual("rr_asset_original", target[repair.STABLE_PROP])
        for key in repair.CORE_PROPS:
            self.assertNotIn(key, target)

    def test_selected_noncore_marker_cleanup_rolls_back_when_validation_fails(self):
        target = Object("Readable_Blender_Name", rr_export_stable_id="old_unrelated",
                        rr_reference_marked=True, rr_reference_stable_id="old_unrelated")
        snapshot = dict(target)
        plan = self.build_plan(target, [target])
        with self.assertRaisesRegex(ValueError, "validator rejected"):
            repair.apply_rebind_plan(plan, validate=lambda root: False)
        self.assertEqual(snapshot, dict(target))

    def test_rejecting_existing_validator_rolls_back_every_touched_object(self):
        target, duplicate = self.pair()
        plan = self.build_plan(target, [target, duplicate], [duplicate])
        snapshots = [copy.deepcopy(dict(root)) for root in (target, duplicate)]
        calls = []
        def reject_second(root):
            calls.append(root)
            if root is duplicate:
                raise RuntimeError("New live alias holder found")
        with self.assertRaisesRegex(RuntimeError, "New live alias"):
            repair.apply_rebind_plan(plan, validate=reject_second)
        self.assertEqual([target, duplicate], calls)
        self.assertEqual(snapshots, [dict(root) for root in (target, duplicate)])

    def test_changed_root_or_actual_core_pointer_requires_new_preview(self):
        for change in (lambda root: setattr(root, "name", "Changed_A"),
                       lambda root: root.__setitem__(repair.PREVIOUS_PROP, '["Changed_Old"]'),
                       lambda root: setattr(root, "core", True)):
            with self.subTest(change=change):
                target, duplicate = self.pair()
                plan = self.build_plan(target, [target, duplicate], [duplicate])
                change(duplicate)
                snapshots = [dict(target), dict(duplicate)]
                with self.assertRaisesRegex(ValueError, "changed since"):
                    repair.apply_rebind_plan(plan, validate=lambda root: True)
                self.assertEqual(snapshots, [dict(target), dict(duplicate)])

    def test_changed_package_or_publishing_marker_requires_new_preview(self):
        target, duplicate = self.pair()
        plan = self.build_plan(target, [target, duplicate], [duplicate])
        snapshots = [dict(target), dict(duplicate)]
        self.manifest["displayName"] = "Changed label"
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "package changed"):
            repair.apply_rebind_plan(plan, validate=lambda root: True)
        self.assertEqual(snapshots, [dict(target), dict(duplicate)])
        plan = self.build_plan(target, [target, duplicate], [duplicate])
        (self.directory / ".rr-Floor_A.publishing").write_text("pending")
        with self.assertRaises(ValueError):
            repair.apply_rebind_plan(plan, validate=lambda root: True)
        self.assertEqual(snapshots, [dict(target), dict(duplicate)])


if __name__ == "__main__":
    unittest.main()
