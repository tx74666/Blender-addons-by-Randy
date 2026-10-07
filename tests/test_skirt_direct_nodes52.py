"""Typed 5.2 socket controls; actual native ABI was probed separately by Root."""

import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


PATH = Path(__file__).resolve().parents[1] / "addons/character_designer/skirt_surface_direct.py"
TREE = ast.parse(PATH.read_text(encoding="utf-8"))
DEFS = {n.name: n for n in TREE.body if isinstance(n, ast.FunctionDef)}


def require(value, message):
    if not value:
        raise ValueError(message)


def load(*names, **extras):
    env = dict(_require=require, SkirtDirectError=ValueError)
    env.update(extras)
    exec(compile(ast.Module(body=[DEFS[n] for n in names], type_ignores=[]), str(PATH), "exec"), env)
    return env


class TypedSocketTests(unittest.TestCase):
    def fixture(self):
        socket = NS(type="VALUE", attribute_name="", layer_name="", value=False)
        modifier = NS(properties=NS(inputs=NS(Socket_2=socket)), node_group=object(),
                      show_viewport=True, show_render=True)
        return socket, modifier

    def test_typed_boolean_roundtrip_and_missing_interface_rejection(self):
        env = load("_mode_input")
        socket, modifier = self.fixture()
        self.assertIs(env["_mode_input"](modifier, "Socket_2"), socket)
        for value in (False, True, False):
            env["_mode_input"](modifier, "Socket_2").value = value
            self.assertIs(env["_mode_input"](modifier, "Socket_2").value, value)
        for other in (NS(), NS(properties={"Socket_2": True})):
            with self.assertRaises(ValueError):
                env["_mode_input"](other, "Socket_2")
        with self.assertRaises(ValueError):
            env["_mode_input"](modifier, "Missing")

    def test_attribute_layer_nonbool_and_identifier_are_strict(self):
        env = load("_mode_input")
        for key, value in (("type", "ATTRIBUTE"), ("attribute_name", "artist"), ("layer_name", "layer"),
                           ("value", 1), ("value", None)):
            socket, modifier = self.fixture()
            setattr(socket, key, value)
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                env["_mode_input"](modifier, "Socket_2")
        for identifier in ("", 2, None):
            with self.assertRaises(ValueError):
                env["_mode_input"](self.fixture()[1], identifier)

    def test_sync_uses_actual_typed_owner(self):
        socket, modifier = self.fixture()
        cloth = NS(show_viewport=False, show_render=False)
        env = load("_mode_input", "_use_cloth", "_sync",
                   _installation=lambda _r: {"mode_socket": "Socket_2"},
                   _overlay=lambda *_a: modifier, _object=lambda *_a: NS(modifiers=[cloth]))
        for mode, editing, pending, expected in (("AUTOMATIC", False, False, True),
                                                  ("AUTOMATIC", True, True, False),
                                                  ("MANUAL", False, False, False)):
            env["_sync"](None, {}, dict(mode=mode, editing=editing, pending=pending))
            self.assertIs(socket.value, expected)
            self.assertIs(cloth.show_viewport, expected)
            self.assertIs(cloth.show_render, expected)

    def test_capture_restore_preserves_typed_socket_and_endpoint_flags(self):
        socket, modifier = self.fixture()
        source, record = {"state": "original"}, {"owner": "owner"}
        source["rig"] = object()
        cloth = NS(show_viewport=True, show_render=False)
        env = load("_mode_input", "capture_mode", "restore_mode", STATE_KEY="state", skirt=NS(RIG_KEY="rig"),
                   validate=lambda *_a: None, _state=lambda *_a: {},
                   _installation=lambda _r: {"mode_socket": "Socket_2"},
                   _overlay=lambda *_a: modifier, _object=lambda *_a: NS(modifiers=[cloth]))
        captured = env["capture_mode"](source, record)
        socket.value, source["state"] = True, "edited"
        cloth.show_viewport = cloth.show_render = False
        env["restore_mode"](source, record, captured)
        self.assertIs(socket.value, False)
        self.assertEqual(source["state"], "original")
        self.assertEqual((cloth.show_viewport, cloth.show_render), (True, False))
        captured["socket"] = 1
        with self.assertRaises(ValueError):
            env["restore_mode"](source, record, captured)


if __name__ == "__main__":
    unittest.main()
