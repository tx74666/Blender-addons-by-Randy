"""Focused source-only Body relay guards; native geometry/bind remains untested."""

import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


PATH = Path(__file__).resolve().parents[1] / "addons/character_designer/skirt_surface_direct.py"
TREE = ast.parse(PATH.read_text(encoding="utf-8"))
DEFS = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}


def require(value, message):
    if not value:
        raise ValueError(message)


def load(*names, **env):
    env.update(_require=require)
    body = [ast.parse(ast.unparse(DEFS[name])).body[0] for name in names]
    # Supply the real defer ABI without importing bpy or executing a package.
    for function in body:
        function.body = [ast.Pass() if isinstance(n, ast.ImportFrom) else n for n in function.body]
    exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), str(PATH), "exec"), env)
    return env


class BodyRelayTests(unittest.TestCase):
    def fixture(self):
        rig = NS(type="ARMATURE", parent=None)
        arm = NS(type="ARMATURE", object=rig, use_vertex_groups=True, use_bone_envelopes=False,
                 vertex_group="", use_multi_modifier=False, show_viewport=True, show_render=True)
        body = NS(type="MESH", library=None, override_library=None, parent_type="OBJECT", parent=rig,
                  data=NS(users=1, library=None, override_library=None, vertices=[]),
                  modifiers=[arm], vertex_groups=[])
        env = load("_body_preflight", shared=NS(_require=require, _owned_bones=lambda _r: {"DressDEF"}),
                   skirt=NS(is_shared=lambda _r: True))
        return env, body, rig

    def test_body_native_single_user_preallocation_gate(self):
        env, body, rig = self.fixture()
        self.assertIs(env["_body_preflight"](body, rig, object(), {}), rig)
        for change in ("shared", "duplicate_armature", "hidden", "dress_weight", "foreign_parent"):
            env, body, rig = self.fixture()
            if change == "shared":
                body.data.users = 2
            elif change == "duplicate_armature":
                body.modifiers.append(body.modifiers[0])
            elif change == "hidden":
                body.modifiers[0].show_viewport = False
            elif change == "dress_weight":
                body.vertex_groups = [NS(name="DressDEF", index=0)]
                body.data.vertices = [NS(groups=[NS(group=0, weight=1.)])]
            else:
                body.parent = object()
            with self.subTest(change=change), self.assertRaises(ValueError):
                env["_body_preflight"](body, rig, object(), {})

    def test_entire_install_and_failure_are_deferred_without_flush(self):
        events, busy = [], [False]

        @contextmanager
        def defer(context, *, flush_on_exit):
            self.assertIs(context, self)
            self.assertIs(flush_on_exit, False)
            busy[0] = True
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")
                busy[0] = False

        def core(context, source, *, body, capability):
            self.assertTrue(busy[0])
            events.extend(["rest", "bind", "context_restore", "rollback"])
            raise RuntimeError("late bind failure")

        env = load("install", forearm_twist=NS(defer_runtime=defer), _install=core)
        source = NS(data=NS(shape_keys=None))
        with self.assertRaisesRegex(RuntimeError, "late bind failure"):
            env["install"](self, source, body=object())
        self.assertFalse(busy[0])
        self.assertEqual(events, ["enter", "rest", "bind", "context_restore", "rollback", "exit"])

    def test_artist_dress_keys_reject_before_core_or_defer(self):
        env = load("install")
        with self.assertRaisesRegex(ValueError, "Preserve these Keys"):
            env["install"](None, NS(data=NS(shape_keys=object())))

    def test_body_relay_has_no_shared_data_or_second_skin_path(self):
        text = ast.unparse(DEFS["_body"])
        for forbidden in ("tx.copy", "body.copy", "body.data.copy", "animation_data_clear", "shape_key_clear", "'ARMATURE'"):
            self.assertNotIn(forbidden, text)
        self.assertIn("'GeometryNodeObjectInfo'", text)
        self.assertIn("info.transform_space = 'RELATIVE'", text)
        self.assertIn("info.inputs['As Instance'].default_value = False", text)
        self.assertIn("info.inputs['Object'].default_value = body", text)
        # The real allocation statements enrol each ID before further work.
        statements = [ast.unparse(n) for n in DEFS["_body"].body]
        for allocation, receipt in (("mesh = bpy.data.meshes.new", "tx.data.append(mesh)"),
                                    ("clone = bpy.data.objects.new", "tx.objects.append(clone)"),
                                    ("group = bpy.data.node_groups.new", "tx.nodes.append(group)")):
            index = next(i for i, statement in enumerate(statements) if statement.startswith(allocation))
            self.assertEqual(statements[index + 1], receipt)

    def test_removal_receipts_cover_independent_body_mesh_and_both_graphs(self):
        preflight = ast.unparse(DEFS["preflight_remove"])
        commit = ast.unparse(DEFS["commit_remove"])
        self.assertIn("groups = (group, body_group)", preflight)
        self.assertIn("data = tuple((obj.data for obj in objects))", preflight)
        self.assertIn("list(data) + list(groups)", preflight)
        self.assertIn("data = opaque['data']", commit)
        self.assertNotIn("!= 'BODY_ATTACHMENT'", commit)
        self.assertIn("for group in groups:", commit)
        self.assertLess(commit.index("bpy.data.objects.remove"), commit.index("bpy.data.node_groups.remove"))
        self.assertIn("group.users == 0", commit)
        self.assertIn("mesh.users == 0", commit)


if __name__ == "__main__":
    unittest.main()
