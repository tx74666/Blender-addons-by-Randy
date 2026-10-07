"""Pure model-adapter checks using the real Direct three-hook source fixture.

The existing fixture delegates installed native graph validation to a spy.
These tests establish adapter admission/transport/side effects, not a Blender
binding, FBX round-trip, skeletal animation or final-surface acceptance.
"""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "addons/character_designer/dress_plain_native_skin.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    real_hooks = load(ROOT / "tests/test_skirt_direct_export_snapshot.py", "plain_real_hook_fixture")
    f = real_hooks.fixture()
    f.source.type, f.source.data.shape_keys = "MESH", None
    f.rig.type, f.rig.data = "ARMATURE", NS(pose_position="POSE")
    bone = NS(name="DEF", rotation_mode="QUATERNION", location=[0., 0.02, 0.],
              rotation_euler=[0., 0., 0.], rotation_quaternion=[1., 0., 0., 0.],
              rotation_axis_angle=[0., 0., 1., 0.], scale=[1., .999994, 1.])
    f.rig.pose, f.rig.frame = NS(bones=[bone]), {"matrix_basis": [[1., 0., 0., .3]]}
    f.env["shared"]._frame = lambda rig: copy.deepcopy(rig.frame)
    f.source["direct-state"] = json.dumps(dict(version=1, owner="owned", mode="MANUAL", editing=False, pending=False))
    f.output.properties.inputs.Socket_2.value = False
    f.cloth.show_viewport = f.cloth.show_render = False
    f.events.clear()
    service = NS(BACKEND=f.env["BACKEND"], VERSION=1, skirt=f.env["skirt"], shared=f.env["shared"],
                 **{name: f.env[name] for name in ("export_capture", "validate_snapshot", "strip_export_snapshot")})
    module = load(SOURCE, "plain_model_component")
    module._service = lambda: service
    f.module, f.service, f.bone = module, service, bone
    return f


class PlainNativeSkinTests(unittest.TestCase):
    def test_import_is_stdlib_only_and_no_existing_runtime_imports_adapter(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        top_imports = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(top_imports, {"inspect", "json"})
        self.assertFalse(any(isinstance(node, ast.ImportFrom) and node.level for node in tree.body))
        self.assertFalse(any(isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) for node in tree.body))
        self.assertNotIn("register", {node.name for node in tree.body if isinstance(node, ast.FunctionDef)})
        for path in SOURCE.parent.glob("*.py"):
            if path == SOURCE:
                continue
            other = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(other):
                if isinstance(node, ast.Import):
                    self.assertFalse(any(alias.name.endswith("dress_plain_native_skin") for alias in node.names), path.name)
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "dress_plain_native_skin", path.name)
                    self.assertFalse(any(alias.name == "dress_plain_native_skin" for alias in node.names), path.name)

    def test_pending_true_complete_manual_capture_and_strip_preserves_state(self):
        f = fixture()
        raw = json.loads(f.source["direct-state"])
        raw["pending"] = True
        f.source["direct-state"] = json.dumps(raw)
        state_before = f.source["direct-state"]
        receipt = json.loads(json.dumps(f.module.capture(f.source), allow_nan=False))
        self.assertIs(receipt["direct_proof"]["state"]["pending"], True)
        f.module.validate(f.source, receipt)
        with patch.dict(sys.modules, {"bpy": f.bpy}):
            f.module.prepare(f.source, receipt)
            self.assertEqual(f.events, [])
            result = f.module.strip(f.source, receipt)
        self.assertEqual(f.source["direct-state"], state_before)
        self.assertEqual(list(f.source.modifiers), [f.arm, f.sub])
        self.assertTrue(result["manual_original_preserved"])
        self.assertFalse(result["simulation_baked"] or result["animation_supported"] or result["export_authorized"])

    def test_pending_transition_or_unknown_typed_value_rejects_before_strip(self):
        for start in (False, True):
            f = fixture()
            raw = json.loads(f.source["direct-state"])
            raw["pending"] = start
            f.source["direct-state"] = json.dumps(raw)
            receipt = f.module.capture(f.source)
            raw["pending"] = not start
            f.source["direct-state"] = json.dumps(raw)
            with patch.dict(sys.modules, {"bpy": f.bpy}), self.assertRaises(ValueError):
                f.module.strip(f.source, receipt)
            self.assertEqual(f.events, [])
        for value in (0, 1, None, "false", [], {}):
            f = fixture()
            receipt = f.module.capture(f.source)
            receipt["direct_proof"]["state"]["pending"] = value
            with patch.dict(sys.modules, {"bpy": f.bpy}), self.assertRaises(ValueError):
                f.module.strip(f.source, receipt)
            self.assertEqual(f.events, [])
            raw = json.loads(f.source["direct-state"])
            raw["pending"] = value
            f.source["direct-state"] = json.dumps(raw)
            with self.assertRaises(ValueError):
                f.module.capture(f.source)
            self.assertEqual(f.events, [])

    def test_real_hooks_json_roundtrip_model_only_strip_preserves_channels(self):
        f = fixture()
        receipt = f.module.capture(f.source)
        transported = json.loads(json.dumps(receipt, allow_nan=False))
        f.module.validate(f.source, transported)
        with patch.dict(sys.modules, {"bpy": f.bpy}):
            prepared = f.module.prepare(f.source, transported)
            self.assertEqual(f.events, [])
            result = f.module.strip(f.source, prepared)
        self.assertEqual(list(f.source.modifiers), [f.arm, f.sub])
        self.assertEqual(f.bone.location, [0., 0.02, 0.])
        self.assertEqual(f.bone.scale, [1., .999994, 1.])
        self.assertIs(f.groups[f.body_group.name], f.body_group)
        self.assertTrue(result["manual_original_preserved"] and result["manual_pose_retained_at_strip"])
        self.assertTrue(result["body_attachment_omitted"] and result["physics_omitted"])
        self.assertFalse(any(result[key] for key in ("simulation_baked", "final_surface_equivalent",
            "animation_supported", "FBX_verified", "Unity_verified", "export_authorized", "export_verified")))
        self.assertIn("validate_installed", f.reads)

    def test_unknown_or_tampered_receipt_rejects_without_writes(self):
        mutations = (lambda r: r.update(version=True), lambda r: r.update(schema="unknown"),
                     lambda r: r.update(backend="unknown"), lambda r: r.update(scope="SKELETAL_ACTION"),
                     lambda r: r.update(animation_supported=True), lambda r: r.update(export_authorized=True),
                     lambda r: r.update(extra=True), lambda r: r["omission"].update(simulation_baked=0),
                     lambda r: r["direct_signature"].update(export_capture=["source", "allow_direct"]))
        for change in mutations:
            with self.subTest(change=change):
                f = fixture()
                receipt = f.module.capture(f.source)
                change(receipt)
                with patch.dict(sys.modules, {"bpy": f.bpy}), self.assertRaises(ValueError):
                    f.module.strip(f.source, receipt)
                self.assertEqual(f.events, [])

    def test_actual_state_and_manual_physical_mutes_must_prove_preservation(self):
        changes = (lambda f: f.source.__setitem__("direct-state", f.source["direct-state"].replace("MANUAL", "AUTOMATIC")),
                   lambda f: f.source.__setitem__("direct-state", f.source["direct-state"].replace('"editing": false', '"editing": true')),
                   lambda f: f.source.__setitem__("direct-state", f.source["direct-state"].replace('"pending": false', '"pending": 0')),
                   lambda f: f.manual.values.update(mute=True), lambda f: f.rotation.values.update(mute=False))
        for change in changes:
            with self.subTest(change=change):
                f = fixture()
                change(f)
                with self.assertRaises(ValueError):
                    f.module.capture(f.source)
                self.assertEqual(f.events, [])

    def test_channel_placement_or_actual_graph_change_after_capture_rejects(self):
        for change in (lambda f: f.bone.location.__setitem__(1, .04),
                       lambda f: setattr(f.bone, "rotation_mode", "XYZ"),
                       lambda f: f.rig.frame["matrix_basis"][0].__setitem__(3, .4),
                       lambda f: f.source.update(invalid_graph=True),
                       lambda f: f.arm.__dict__.update(show_render=False)):
            f = fixture()
            receipt = f.module.capture(f.source)
            change(f)
            with patch.dict(sys.modules, {"bpy": f.bpy}), self.assertRaises(ValueError):
                f.module.strip(f.source, receipt)
            self.assertEqual(f.events, [])

    def test_author_keys_and_nonfinite_channels_reject(self):
        f = fixture()
        f.source.data.shape_keys = object()
        with self.assertRaises(ValueError):
            f.module.capture(f.source)
        self.assertEqual(f.reads, [])
        f = fixture()
        f.bone.location[0] = float("nan")
        with self.assertRaises(ValueError):
            f.module.capture(f.source)
        self.assertEqual(f.events, [])

    def test_hook_signature_unknown_backend_or_version_reject_before_hook(self):
        for change in (lambda s: setattr(s, "export_capture", lambda source, allow_direct=False: None),
                       lambda s: setattr(s, "BACKEND", "unknown"), lambda s: setattr(s, "VERSION", True)):
            f = fixture()
            change(f.service)
            with self.assertRaises(ValueError):
                f.module.capture(f.source)
            self.assertEqual(f.reads, [])
            self.assertEqual(f.events, [])

    def test_animation_artist_foreground_and_external_group_reject_before_removal(self):
        for path, background in (("C:/private/cdesigner-action-test/animation.blend", True),
                                 ("C:/artist/X.blend", True),
                                 ("C:/private/cdesigner-unity-test/character.blend", False)):
            f = fixture()
            receipt = f.module.capture(f.source)
            f.bpy.data.filepath, f.bpy.app.background = path, background
            with patch.dict(sys.modules, {"bpy": f.bpy}), self.assertRaises(ValueError):
                f.module.strip(f.source, receipt)
            self.assertEqual(f.events, [])
        f = fixture()
        receipt = f.module.capture(f.source)
        f.group.update(external=True)
        with patch.dict(sys.modules, {"bpy": f.bpy}), self.assertRaises(ValueError):
            f.module.strip(f.source, receipt)
        self.assertEqual(f.events, [])

    def test_after_strip_channel_loss_is_failure_not_constant_success(self):
        f = fixture()
        receipt = f.module.capture(f.source)
        original = f.service.strip_export_snapshot

        def corrupt(source, proof):
            result = original(source, proof)
            f.bone.location[1] = 0.
            return result

        f.service.strip_export_snapshot = corrupt
        with patch.dict(sys.modules, {"bpy": f.bpy}), self.assertRaisesRegex(ValueError, "changed native"):
            f.module.strip(f.source, receipt)
        # Failure is in a disposable snapshot, and must not be published.
        self.assertNotIn(f.output, f.source.modifiers)


if __name__ == "__main__":
    unittest.main()
