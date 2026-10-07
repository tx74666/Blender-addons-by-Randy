"""Focused private Direct snapshot-hook checks, without bpy or native export.

The real three hooks and existing state/socket guards are compiled from source.
Installed-graph validation is an explicit dependency spy, not a replacement
proof of native skin, Cloth, FBX or Body attachment equivalence.
"""

import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "addons/character_designer/skirt_surface_direct.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
FUNCTIONS = {node.name: node for node in TREE.body if isinstance(node, ast.FunctionDef)}
SHARED_TREE = ast.parse((SOURCE.parent / "skirt_surface.py").read_text(encoding="utf-8"))
SHARED_FUNCTIONS = {node.name: node for node in SHARED_TREE.body if isinstance(node, ast.FunctionDef)}


class NativeID(dict):
    __hash__ = object.__hash__

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


class Modifier:
    def __init__(self, name, kind, events, **values):
        self.__dict__.update(name=name, type=kind, _events=events, fail_pause=False,
                             show_viewport=True, show_render=True, is_active=False,
                             object=None, node_group=None, values={"native": 0.75}, **values)

    def __setattr__(self, name, value):
        if name in {"show_viewport", "show_render"}:
            self._events.append((self.name, name, value))
            if self.fail_pause and name == "show_render" and value is False:
                self.__dict__["fail_pause"] = False
                raise RuntimeError("native pause failed")
        self.__dict__[name] = value


class Modifiers(list):
    def remove(self, value):
        self.events.append(("remove_modifier", value.name))
        super().remove(value)
        value.node_group.users -= 1
        if value.is_active:
            self[-1].is_active = True  # Native removal may select another UI row.


class Groups(dict):
    def remove(self, value):
        self.events.append(("remove_group", value.name))
        del self[value.name]


def fixture():
    env = dict(json=json, math=__import__("math"), VERSION=1,
               BACKEND="DIRECT_MAIN_CLOTH_V1", STATE_KEY="direct-state")
    exec(compile(ast.Module(body=[node for node in TREE.body
                                 if isinstance(node, ast.ClassDef) and node.name == "SkirtDirectError"],
                            type_ignores=[]), str(SOURCE), "exec"), env)
    helpers = {"json": json, "hashlib": hashlib}
    exec(compile(ast.Module(body=[SHARED_FUNCTIONS[name] for name in ("_json", "_digest")],
                            type_ignores=[]), str(SOURCE.parent / "skirt_surface.py"), "exec"), helpers)
    events, reads = [], []
    rig = NS(name="Main Rig")
    source = NativeID(rig=rig, record="stored", **{"direct-state": json.dumps(
        dict(version=1, owner="owned", mode="AUTOMATIC", editing=False, pending=False))})
    source.name, source.library, source.override_library = "Dress", None, None
    source.data = NS(library=None, override_library=None, raw={"coords": [[0., 1., 2.]], "weights": [[0, 1.]]})
    group = NativeID(role="DIRECT_NODE_GROUP", owner="owned", source=source)
    group.name, group.users = "Direct Output", 1
    group.library = group.override_library = None
    group.use_fake_user, group.graph = False, {"node": {"values": (False, 3)}}
    body_group = NativeID(role="BODY_NODE_GROUP")
    body_group.name, body_group.users = "Body Relay", 1
    output = Modifier("Direct absolute output", "NODES", events)
    output.node_group = group
    output.properties = NS(inputs=NS(Socket_2=NS(type="VALUE", attribute_name="", layer_name="", value=True)))
    arm = Modifier("Original ARM", "ARMATURE", events)
    arm.object = rig
    sub = Modifier("Original Subsurf", "SUBSURF", events)
    source.modifiers = Modifiers((arm, output, sub))
    source.modifiers.events = events
    cloth = Modifier("Owned Cloth", "CLOTH", events)
    actual = NS(name="Owned C", modifiers=[cloth])
    manual, rotation = NS(name="Manual", values={"mix": "BEFORE_FULL", "mute": False}), NS(
        name="Physical", values={"mute": True, "influence": 0.})
    surface = {"version": 1, "overlay": output.name, "node_group": group.name,
               "mode_socket": "Socket_2", "roles": {"INPUT_SURFACE": ["Input"], "CLOTH_PROXY": [actual.name],
               "BODY_ATTACHMENT": ["Body relay"], "COLLIDER": ["Pelvis", "Left", "Right"]}}
    record = {"owner": "owned", "source": source.name, "rig": rig.name,
              "physics": {"backend": env["BACKEND"], "surface": surface}}
    groups = Groups({group.name: group, body_group.name: body_group})
    groups.events = events
    bpy = NS(app=NS(background=True), data=NS(filepath="C:/private/cdesigner-unity-test/character.blend",
                                            objects={source.name: source}, node_groups=groups))

    def rna(value, exclude=()):
        data = dict(name=value.name, **copy.deepcopy(value.values))
        if hasattr(value, "type"):
            data.update(type=value.type, object=getattr(value.object, "name", None),
                        show_viewport=value.show_viewport, show_render=value.show_render,
                        is_active=value.is_active)
        return {key: item for key, item in data.items() if key not in exclude}

    def source_contract(obj, rec):
        return {"raw": copy.deepcopy(obj.data.raw), "modifiers": [rna(mod, {"is_active"})
                for mod in obj.modifiers if mod.name != rec["physics"]["surface"]["overlay"]]}

    def validate(obj, native_rig, rec):
        reads.append("validate_installed")
        if obj is not source or native_rig is not rig or rec is not record or source.get("invalid_graph"):
            raise env["SkirtDirectError"]("native installed graph rejected")
        return actual, cloth

    def outside(objects, allowed):
        reads.append("outside_users")
        if objects != (group,) or allowed != {source} or group.get("external"):
            raise env["SkirtDirectError"]("external node user")

    env.update(bpy=bpy, profiles=NS(MODES={"MANUAL", "AUTOMATIC"}),
               skirt=NS(RIG_KEY="rig", read_record=lambda _source: record),
               shared=NS(_json=helpers["_json"], _digest=helpers["_digest"], _rna=rna,
                         _node_content=lambda item: copy.deepcopy(item.graph),
                         _source_contract=source_contract, _outside_users=outside),
               validate=validate, _overlay=lambda *_args: output,
               _object=lambda *_args: actual,
               _deform=lambda *_args: [(NS(name="DEF"), manual, rotation)])
    names = ("_require", "_installation", "_state", "_mode_input",
             "export_capture", "validate_snapshot", "strip_export_snapshot")
    exec(compile(ast.Module(body=[FUNCTIONS[name] for name in names], type_ignores=[]), str(SOURCE), "exec"), env)
    return NS(env=env, source=source, rig=rig, record=record, group=group, body_group=body_group,
              output=output, arm=arm, sub=sub, cloth=cloth, events=events, reads=reads,
              bpy=bpy, groups=groups, manual=manual, rotation=rotation)


class DirectSnapshotTests(unittest.TestCase):
    def test_capture_detached_finite_json_and_truthful_omission(self):
        f = fixture()
        proof = f.env["export_capture"](f.source)
        transported = json.loads(json.dumps(proof, allow_nan=False))
        f.env["validate_snapshot"](f.source, transported)
        self.assertEqual(f.events, [])
        self.assertGreaterEqual(f.reads.count("validate_installed"), 2)
        self.assertTrue(proof["private_snapshot_api_only"])
        self.assertFalse(proof["export_verified"])
        self.assertEqual(proof["omission"], dict(simulation_baked=False, physics_omitted=True,
            manual_original_preserved=True, body_attachment_omitted=True,
            surface="PLAIN_NATIVE_SKIN_V1", final_surface_equivalent=False))
        proof["roles"]["COLLIDER"].append("tampered")
        self.assertEqual(len(f.record["physics"]["surface"]["roles"]["COLLIDER"]), 3)

    def test_proof_type_numeric_and_state_changes_reject_before_writes(self):
        for mutation in (lambda p: p.update(version=True), lambda p: p.update(extra=True),
                         lambda p: p["omission"].update(simulation_baked=0),
                         lambda p: p.update(mode_value=1), lambda p: p.update(mode_value=float("nan")),
                         lambda p: p["state"].update(pending=True),
                         lambda p: p.update(backend="ACTUAL_SURFACE_DELTA_V1")):
            f = fixture()
            proof = f.env["export_capture"](f.source)
            mutation(proof)
            with self.assertRaises(f.env["SkirtDirectError"]):
                f.env["strip_export_snapshot"](f.source, proof)
            self.assertEqual(f.events, [])

    def test_actual_graph_state_arm_and_original_correction_tamper_reject(self):
        for change in (lambda f: f.source.update(invalid_graph=True),
                       lambda f: f.group.graph.update(unexpected="node"),
                       lambda f: f.source.__setitem__("direct-state", f.source["direct-state"].replace("false", "true")),
                       lambda f: f.arm.__dict__.update(show_render=False),
                       lambda f: f.sub.values.update(levels=3),
                       lambda f: f.manual.values.update(mix="REPLACE"),
                       lambda f: f.rotation.values.update(mute=False)):
            f = fixture()
            proof = f.env["export_capture"](f.source)
            change(f)
            with self.assertRaises(f.env["SkirtDirectError"]):
                f.env["strip_export_snapshot"](f.source, proof)
            self.assertEqual(f.events, [])

    def test_only_local_background_exporter_paths_can_strip(self):
        for path, background, linked in (("C:/artist/X.blend", True, False),
                ("C:/private/cdesigner-unity-test/animation.blend", True, False),
                ("C:/private/cdesigner-action-test/character.blend", True, False),
                ("C:/private/cdesigner-unity-test/character.blend", False, False),
                ("C:/private/cdesigner-unity-test/character.blend", True, True)):
            f = fixture()
            proof = f.env["export_capture"](f.source)
            f.bpy.data.filepath, f.bpy.app.background = path, background
            f.source.data.library = object() if linked else None
            with self.assertRaises(f.env["SkirtDirectError"]):
                f.env["strip_export_snapshot"](f.source, proof)
            self.assertEqual(f.events, [])

    def test_node_external_fake_or_library_users_reject_before_pause(self):
        for change in (lambda f: setattr(f.group, "users", 2),
                       lambda f: f.group.update(external=True),
                       lambda f: setattr(f.group, "use_fake_user", True),
                       lambda f: setattr(f.group, "library", object())):
            f = fixture()
            proof = f.env["export_capture"](f.source)
            change(f)
            with self.assertRaises(f.env["SkirtDirectError"]):
                f.env["strip_export_snapshot"](f.source, proof)
            self.assertEqual(f.events, [])
            self.assertIn(f.output, f.source.modifiers)

    def test_strip_only_absolute_group_pauses_cloth_and_keeps_native_skin(self):
        for kind, prefix in (("character", "cdesigner-unity-"), ("animation", "cdesigner-action-")):
            f = fixture()
            f.bpy.data.filepath = "C:/private/" + prefix + "test/" + kind + ".blend"
            f.output.is_active = True
            raw_source, raw_record, mode = copy.deepcopy(f.source.data.raw), copy.deepcopy(f.record), f.source["direct-state"]
            proof = f.env["export_capture"](f.source)
            row = f.env["strip_export_snapshot"](f.source, proof)
            self.assertEqual(list(f.source.modifiers), [f.arm, f.sub])
            self.assertTrue(f.arm.show_viewport and f.arm.show_render)
            self.assertFalse(f.cloth.show_viewport or f.cloth.show_render)
            self.assertNotIn(f.group.name, f.groups)
            self.assertIs(f.groups[f.body_group.name], f.body_group)
            self.assertEqual(f.body_group.users, 1)
            self.assertEqual(f.source.data.raw, raw_source)
            self.assertEqual(f.record, raw_record)
            self.assertEqual(f.source["direct-state"], mode)
            self.assertEqual(f.rotation.values, {"mute": True, "influence": 0.})
            self.assertTrue(row["body_attachment_omitted"])
            self.assertFalse(row["simulation_baked"] or row["export_verified"] or row["final_surface_equivalent"])
            self.assertEqual(f.reads[-1], "outside_users")

    def test_pause_failure_restores_flags_before_any_removal(self):
        f = fixture()
        proof = f.env["export_capture"](f.source)
        f.cloth.fail_pause = True
        with self.assertRaisesRegex(RuntimeError, "native pause failed"):
            f.env["strip_export_snapshot"](f.source, proof)
        self.assertTrue(f.cloth.show_viewport and f.cloth.show_render)
        self.assertIn(f.output, f.source.modifiers)
        self.assertIn(f.group.name, f.groups)
        self.assertFalse(any(event[0].startswith("remove_") for event in f.events))

    def test_no_uninstall_body_transfer_cache_or_public_release_calls(self):
        strip = FUNCTIONS["strip_export_snapshot"]
        calls = {node.func.id for node in ast.walk(strip)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertTrue(calls.isdisjoint({"commit_remove", "set_mode", "_sync", "_body", "_world_follow"}))
        for filename, function in (("dress_export_snapshot.py", "_record"),
                                   ("unity_export.py", "_dress_record"),
                                   ("unity_export_worker.py", "_dress_backend_source")):
            tree = ast.parse((SOURCE.parent / filename).read_text(encoding="utf-8"))
            node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
            env = dict(json=json, ExportError=ValueError, RECORD_KEY="record",
                       _DRESS_RECORD_KEY="record")
            exec(compile(ast.Module(body=[node], type_ignores=[]), filename, "exec"), env)
            f = fixture()
            f.source.type = "MESH"
            f.source["record"] = json.dumps(f.record)
            with self.assertRaisesRegex(ValueError, "export validation is pending"):
                env[function](f.source)


if __name__ == "__main__":
    unittest.main()
