"""Small stdlib checks of the real Direct service, without importing bpy.

These cover rejection/state/rollback boundaries. Native skin, bind, Cloth and
node interfaces must still pass the separate Blender cold-install check.
"""

import ast
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "addons/character_designer/skirt_surface_direct.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
FUNCTIONS = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}


class DirectError(ValueError):
    pass


def require(value, message):
    if not value:
        raise DirectError(message)


def loaded(names, **extras):
    env = dict(json=json, math=math, _require=require, SkirtDirectError=DirectError,
               STATE_KEY="direct-state", VERSION=1, BACKEND="DIRECT_MAIN_CLOTH_V1",
               profiles=NS(MODES=("MANUAL", "AUTOMATIC"), PROFILE_KEY="profile"),
               shared=NS(_json=lambda value: json.dumps(value, sort_keys=True)))
    env.update(extras)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[FUNCTIONS[n] for n in names], type_ignores=[])), str(SOURCE), "exec"), env)
    return env


class Source(dict):
    pass


class NativeID(dict):
    """Native datablock equality uses identity, unlike ordinary test dicts."""

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


class DirectServiceTests(unittest.TestCase):
    def tag_fixture(self):
        roles = next(n.value for n in TREE.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "OBJECT_ROLES" for t in n.targets))
        actual_roles = eval(compile(ast.Expression(roles), str(SOURCE), "eval"), {})
        node_role = next(ast.literal_eval(n.value) for n in TREE.body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "NODE_ROLE" for t in n.targets))
        body_node_role = next(ast.literal_eval(n.value) for n in TREE.body if isinstance(n, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == "BODY_NODE_ROLE" for t in n.targets))
        source = Source(artist="unchanged")
        source.data = NativeID(artist_mesh="unchanged")
        source.data.users = 1
        env = loaded(("_tag",), OBJECT_ROLES=actual_roles, NODE_ROLE=node_role, BODY_NODE_ROLE=body_node_role,
                     ROLE_KEY="role", skirt=NS(OWNER_KEY="owner", SOURCE_KEY="source"))
        return env, source, {"owner": "native-owner"}

    def test_direct_role_tag_owns_only_independent_data(self):
        env, source, record = self.tag_fixture()
        self.assertEqual(env["OBJECT_ROLES"], {"INPUT_SURFACE", "CLOTH_PROXY", "BODY_ATTACHMENT", "COLLIDER"})
        for role in sorted(env["OBJECT_ROLES"]):
            obj = NativeID(copied_artist_tag="discard")
            obj.data = NativeID(copied_artist_mesh_tag="discard")
            obj.data.users = 1
            env["_tag"](obj, source, record, role)
            for target in (obj, obj.data):
                self.assertEqual(set(target), {"owner", "source", "role"})
                self.assertEqual(target["owner"], record["owner"])
                self.assertIs(target["source"], source)
                self.assertEqual(target["role"], role)
        # Body attachment must never retag the artist's shared Mesh. Node
        # ownership likewise does not require, or modify, an Object data ID.
        for role in ("BODY_ATTACHMENT", env["NODE_ROLE"], env["BODY_NODE_ROLE"]):
            obj = NativeID(copied_artist_tag="discard")
            obj.data = source.data
            env["_tag"](obj, source, record, role, data=False)
            self.assertIs(obj["source"], source)
            self.assertEqual(obj["role"], role)
        self.assertEqual(dict(source), {"artist": "unchanged"})
        self.assertEqual(dict(source.data), {"artist_mesh": "unchanged"})

    def test_unknown_role_rejects_before_any_tag_mutation(self):
        env, source, record = self.tag_fixture()
        obj = NativeID(existing_object="preserve")
        obj.data = NativeID(existing_mesh="preserve")
        obj.data.users = 1
        with self.assertRaisesRegex(DirectError, "Unknown Direct Dress role"):
            env["_tag"](obj, source, record, "NEUTRAL_RIG")
        self.assertEqual(dict(obj), {"existing_object": "preserve"})
        self.assertEqual(dict(obj.data), {"existing_mesh": "preserve"})
        self.assertEqual(dict(source.data), {"artist_mesh": "unchanged"})

    def test_artist_or_shared_mesh_rejects_before_object_mutation(self):
        env, source, record = self.tag_fixture()
        for kind, users in (("artist", 1), ("foreign_shared", 2), ("foreign_unused", 0)):
            obj = NativeID(existing_object="preserve")
            obj.data = source.data if kind == "artist" else NativeID(existing_mesh="preserve")
            obj.data.users = users
            original_mesh = dict(obj.data)
            with self.assertRaisesRegex(DirectError, "preserve artist data"):
                env["_tag"](obj, source, record, "COLLIDER")
            self.assertEqual(dict(obj), {"existing_object": "preserve"})
            self.assertEqual(dict(obj.data), original_mesh)
            self.assertEqual(dict(source), {"artist": "unchanged"})
            self.assertEqual(dict(source.data), {"artist_mesh": "unchanged"})

    def test_direct_tag_callers_do_not_use_delta_role_service(self):
        counts = {}
        for name, function in FUNCTIONS.items():
            for call in (n for n in ast.walk(function) if isinstance(n, ast.Call)):
                if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                    self.assertNotEqual((call.func.value.id, call.func.attr), ("shared", "_tag"))
                if isinstance(call.func, ast.Name) and call.func.id == "_tag":
                    counts[name] = counts.get(name, 0) + 1
        self.assertEqual(counts, {"_mesh_copy": 1, "_add_colliders": 2, "_body": 2, "_install": 1})

    def test_cyclic_native_ring_and_exact_weights(self):
        env = loaded(("_ring_ids", "_pins"))
        record = {"fit": {"rings": [[4, 5, 6, 7], [8, 9, 10, 11], [0, 1, 2, 3]]}}
        ring = env["_ring_ids"](record, 12)
        self.assertEqual(ring, [4, 5, 6, 7])
        values = [0.125] * 12
        for index in ring:
            values[index] = 1.
        self.assertEqual(env["_pins"](values, 12, ring)[4], 1.)
        for rings in ([[4, 5, 6, 4], [8, 9, 10, 11], [0, 1, 2, 3]],
                      [[4, 5, 6, 7], [8, 9, 10, 12], [0, 1, 2, 3]],
                      [[4, 5, 6, 7], [8, 9, 10], [0, 1, 2, 3]]):
            with self.assertRaises(DirectError):
                env["_ring_ids"]({"fit": {"rings": rings}}, 12)
        for bad in (values[:-1], values[:4] + [.999] + values[5:],
                    values[:2] + [math.nan] + values[3:],
                    values[:2] + [True] + values[3:],
                    values[:2] + [1.1] + values[3:]):
            with self.assertRaises(DirectError):
                env["_pins"](bad, 12, ring)

    def test_keys_rejected_before_allocation(self):
        source = Source()
        source.data = NS(shape_keys=object())
        calls = []
        skirt = NS(_require_controls_for_setup=lambda _source: calls.append("read"),
                   read_record=lambda _source: {"owner": "test"})
        shared = NS(_Transaction=lambda *_args: calls.append("allocation"))
        env = loaded(("_install",), skirt=skirt, shared=shared)
        with self.assertRaisesRegex(DirectError, "Preserve these Keys"):
            env["_install"](None, source)
        self.assertEqual(calls, ["read"])

    def test_nonempty_old_ram_cache_rejected(self):
        env = loaded(("_cache_install_safe",), shared=NS(_cache_upgrade_safe=lambda *_args: None))
        cloth = NS(point_cache=NS(info="0 frames in memory (0 B)"))
        env["_cache_install_safe"](cloth, {"physics": {}})
        cloth.point_cache.info = "1 frames in memory (28 KiB)"
        with self.assertRaisesRegex(DirectError, "payload cannot be rolled back"):
            env["_cache_install_safe"](cloth, {"physics": {}})

    def state_fixture(self):
        source = Source(rig=object())
        source["direct-state"] = json.dumps(dict(version=1, owner="test", mode="AUTOMATIC", editing=False, pending=False))
        surface = {"version": 1, "home_scene": "Home", "mode_socket": "Socket_2"}
        record = {"owner": "test", "physics": {"backend": "DIRECT_MAIN_CLOTH_V1", "surface": surface, "baked_range": None}}
        output = NS(properties=NS(inputs=NS(Socket_2=NS(type="VALUE", attribute_name="", layer_name="", value=True))),
                    node_group=object(), show_viewport=True, show_render=True)
        cache = NS(frame_start=1, is_baked=False, is_baking=False, use_external=False, use_disk_cache=False)
        cloth = NS(type="CLOTH", show_viewport=True, show_render=True, point_cache=cache)
        actual = NS(modifiers=[cloth])
        scene = NS(name="Home", frame_current=1, frame_subframe=0.)
        names = ("_installation", "_state", "_write_state", "_use_cloth", "_mode_input", "_sync", "capture_mode", "restore_mode", "set_mode", "require_bake_ready", "set_editing", "reset_completed")
        env = loaded(names, skirt=NS(RIG_KEY="rig"), validate=lambda *_args: (actual, cloth),
                     _overlay=lambda *_args: output, _object=lambda *_args: actual)
        return env, source, record, NS(scene=scene), output, cloth

    def test_baked_manual_preview_keeps_cache_and_refuses_stale_automatic(self):
        env, source, record, context, output, cloth = self.state_fixture()
        # The native seal is authoritative even if saved baked_range is absent.
        cloth.point_cache.is_baked = True
        cache_before = dict(vars(cloth.point_cache))
        captured = env["capture_mode"](source, record)
        env["set_mode"](source, record, "MANUAL")
        self.assertTrue(json.loads(source["direct-state"])["pending"])
        self.assertFalse(output.properties.inputs.Socket_2.value)
        self.assertEqual(vars(cloth.point_cache), cache_before)
        before_refusal = source["direct-state"]
        with self.assertRaisesRegex(DirectError, "Reset the simulation explicitly"):
            env["set_mode"](source, record, "AUTOMATIC")
        self.assertEqual(source["direct-state"], before_refusal)
        self.assertEqual(vars(cloth.point_cache), cache_before)
        # The public tuning transaction uses this real endpoint rollback.
        env["restore_mode"](source, record, captured)
        self.assertEqual(source["direct-state"], captured["raw_state"])
        self.assertTrue(output.properties.inputs.Socket_2.value)
        self.assertEqual(vars(cloth.point_cache), cache_before)

    def test_unsealed_manual_requires_explicit_reset_before_automatic(self):
        env, source, record, context, output, cloth = self.state_fixture()
        cache_before = dict(vars(cloth.point_cache))
        env["set_mode"](source, record, "MANUAL")
        self.assertTrue(json.loads(source["direct-state"])["pending"])
        self.assertFalse(output.properties.inputs.Socket_2.value)
        self.assertFalse(cloth.show_viewport)
        self.assertFalse(cloth.show_render)
        before_refusal = source["direct-state"]
        with self.assertRaisesRegex(DirectError, "Reset the simulation explicitly"):
            env["set_mode"](source, record, "AUTOMATIC")
        self.assertEqual(source["direct-state"], before_refusal)
        self.assertEqual(vars(cloth.point_cache), cache_before)
        # This tests only the successful public Reset handoff, not native RAM
        # payload preservation, seek or Cloth re-evaluation.
        env["reset_completed"](context, source, record)
        state = json.loads(source["direct-state"])
        self.assertEqual(state["mode"], "MANUAL")
        self.assertFalse(state["pending"])
        self.assertFalse(output.properties.inputs.Socket_2.value)
        self.assertFalse(cloth.show_viewport)
        self.assertFalse(cloth.show_render)
        with self.assertRaises(DirectError):
            env["require_bake_ready"](source, record)
        env["set_mode"](source, record, "AUTOMATIC")
        env["require_bake_ready"](source, record)
        self.assertTrue(output.properties.inputs.Socket_2.value)
        self.assertTrue(cloth.show_viewport)
        self.assertTrue(cloth.show_render)

    def test_manual_unknown_native_bake_state_refuses_before_writing(self):
        for value in (1, None):
            env, source, record, context, output, cloth = self.state_fixture()
            cloth.point_cache.is_baked = value
            before = source["direct-state"]
            with self.subTest(value=value), self.assertRaises(DirectError):
                env["set_mode"](source, record, "MANUAL")
            self.assertEqual(source["direct-state"], before)
            self.assertTrue(output.properties.inputs.Socket_2.value)

    def test_bake_readiness_only_accepts_enabled_automatic_output(self):
        env, source, record, context, output, cloth = self.state_fixture()
        env["require_bake_ready"](source, record)
        for field, value in (("mode", "MANUAL"), ("editing", True), ("pending", True)):
            env, source, record, context, output, cloth = self.state_fixture()
            state = json.loads(source["direct-state"])
            state[field] = value
            source["direct-state"] = json.dumps(state)
            before = source["direct-state"]
            with self.subTest(field=field), self.assertRaises(DirectError):
                env["require_bake_ready"](source, record)
            self.assertEqual(source["direct-state"], before)
        for field, value in (("type", "NODES"), ("show_viewport", False), ("show_render", False)):
            env, source, record, context, output, cloth = self.state_fixture()
            setattr(cloth, field, value)
            with self.subTest(field=field), self.assertRaises(DirectError):
                env["require_bake_ready"](source, record)

    def test_editing_and_explicit_reset_keep_identity(self):
        env, source, record, context, output, cloth = self.state_fixture()
        surface_before = json.dumps(record["physics"]["surface"], sort_keys=True)
        env["set_editing"](source, record, True)
        self.assertFalse(output.properties.inputs.Socket_2.value)
        self.assertFalse(cloth.show_viewport)
        env["set_editing"](source, record, False)
        with self.assertRaisesRegex(DirectError, "Reset the simulation explicitly"):
            env["set_mode"](source, record, "AUTOMATIC")
        # Public Reset commits this saved metadata only after its native free
        # and this handoff; actual is_baked=False remains the hard condition.
        record["physics"]["baked_range"] = [1, 20]
        result = env["reset_completed"](context, source, record)
        self.assertTrue(output.properties.inputs.Socket_2.value)
        self.assertTrue(cloth.show_viewport)
        self.assertFalse(result["clearance_accepted"])
        self.assertFalse(result["same_frame_recalculation_accepted"])
        self.assertEqual(json.dumps(record["physics"]["surface"], sort_keys=True), surface_before)

    def test_reset_rejections_and_failed_sync_restore(self):
        for owner, field, value in (("scene", "name", "Other"), ("scene", "frame_current", 2),
                                    ("scene", "frame_subframe", .5), ("cache", "is_baked", True),
                                    ("cache", "is_baking", True), ("cache", "use_external", True),
                                    ("cache", "use_disk_cache", True)):
            env, source, record, context, output, cloth = self.state_fixture()
            env["set_editing"](source, record, True)
            env["set_editing"](source, record, False)
            raw = source["direct-state"]
            setattr(context.scene if owner == "scene" else cloth.point_cache, field, value)
            with self.assertRaises(DirectError):
                env["reset_completed"](context, source, record)
            self.assertEqual(source["direct-state"], raw)
        env, source, record, context, output, cloth = self.state_fixture()
        env["set_editing"](source, record, True)
        with self.assertRaises(DirectError):
            env["reset_completed"](context, source, record)
        env["set_editing"](source, record, False)
        captured = env["capture_mode"](source, record)
        count = [0]
        def validate(*_args):
            count[0] += 1
            if count[0] == 3:
                raise DirectError("injected post-sync failure")
            return NS(modifiers=[cloth]), cloth
        env["validate"] = validate
        with self.assertRaisesRegex(DirectError, "injected"):
            env["reset_completed"](context, source, record)
        self.assertEqual(source["direct-state"], captured["raw_state"])
        self.assertFalse(output.properties.inputs.Socket_2.value)
        self.assertFalse(cloth.show_viewport)

    def test_real_install_recovery_attempts_all_restorations(self):
        handler = next(n for n in ast.walk(FUNCTIONS["_install"]) if isinstance(n, ast.ExceptHandler) and n.name == "error")
        wrapper = ast.parse("def recovery():\n    try:\n        raise RuntimeError('original failure')\n    except Exception as error:\n        pass\n").body[0]
        wrapper.body[0].handlers[0].body = handler.body
        calls = []
        def fail_owned():
            calls.append("owned")
            raise RuntimeError("owned failure")
        def fail_ui(*_args):
            calls.append("UI")
            raise RuntimeError("UI failure")
        source = Source(record="changed", profile="changed", **{"direct-state": "changed"})
        modifier = NS(is_active=False)
        source.modifiers = [modifier]
        holder = {"physics_influence": 0.}
        env = loaded(("_restore_optional", "_restore_active"), source=source, rig=NS(pose=NS(bones={})),
                     saved_rotations=[("missing", "rotation", False)], holder=holder, old_influence=1.,
                     old_ui={}, _restore_ui=fail_ui, original_record="original", original_profile=None,
                     original_state="original-state", original_modifiers=(modifier,), original_active=[True],
                     skirt=NS(RECORD_KEY="record"), tx=NS(rollback=fail_owned,
                     restore_context=lambda: calls.append("context"), enable_last=lambda: calls.append("flags")))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])), str(SOURCE), "exec"), env)
        with self.assertRaisesRegex(DirectError, "owned IDs.*rotation.*property UI"):
            env["recovery"]()
        self.assertEqual(source["record"], "original")
        self.assertNotIn("profile", source)
        self.assertEqual(source["direct-state"], "original-state")
        self.assertEqual(holder["physics_influence"], 1.)
        self.assertTrue(modifier.is_active)
        self.assertEqual(calls, ["owned", "UI", "context", "flags"])


if __name__ == "__main__":
    unittest.main()
