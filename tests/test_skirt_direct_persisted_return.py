"""Real JSON persistence and the provider's commit tail, without native bpy."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


ROOT = Path(__file__).resolve().parents[1] / "addons/character_designer"
DIRECT = ast.parse((ROOT / "skirt_surface_direct.py").read_bytes())
RIG = ast.parse((ROOT / "skirt_rig.py").read_bytes())
SHARED = ast.parse((ROOT / "skirt_surface.py").read_bytes())


def definition(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def require(value, message):
    if not value:
        raise ValueError(message)


class PersistedReturnTests(unittest.TestCase):
    def scope(self, read=None):
        env = {"json": json, "RECORD_KEY": "record"}
        exec(compile(ast.Module(body=[definition(RIG, "write_record"), definition(SHARED, "_json")], type_ignores=[]), "<real JSON writers>", "exec"), env)
        candidate = {"surface": {"backup": {"rotations": [("DEF", "Old Physics", False)]}}, "weight": .2743}
        source = {}
        env["write_record"](source, candidate)
        env.update(source=source, candidate=candidate, _require=require,
                   shared=NS(_json=env["_json"]), tx=NS(flags=["old endpoint rollback"]),
                   skirt=NS(read_record=read or (lambda obj: json.loads(obj["record"]))))
        body = next(n for n in definition(DIRECT, "_install").body if isinstance(n, ast.Try)).body
        start = next(i for i, n in enumerate(body) if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "persisted" for t in n.targets))
        function = ast.FunctionDef(name="commit", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
                                  body=body[start:], decorator_list=[])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), "<actual provider commit tail>", "exec"), env)
        return env

    def test_first_and_noop_return_the_same_saved_record(self):
        env = self.scope()
        saved = json.loads(env["source"]["record"])
        self.assertNotEqual(env["candidate"], saved)  # Actual tuple/list cause.
        self.assertEqual(env["shared"]._json(env["candidate"]), env["shared"]._json(saved))
        self.assertEqual(env["commit"](), saved)
        self.assertEqual(env["tx"].flags, [])

    def test_read_failure_retains_endpoint_rollback_flags(self):
        def fail(_source):
            raise RuntimeError("ownership read failed")
        env = self.scope(read=fail)
        with self.assertRaisesRegex(RuntimeError, "ownership read failed"):
            env["commit"]()
        self.assertEqual(env["tx"].flags, ["old endpoint rollback"])

    def test_actual_metadata_mutation_rejects_before_commit(self):
        env = self.scope(read=lambda _obj: {"surface": {"backup": {"rotations": [["DEF", "Old Physics", True]]}}, "weight": .2743})
        with self.assertRaisesRegex(ValueError, "saved Direct installation differs"):
            env["commit"]()
        self.assertEqual(env["tx"].flags, ["old endpoint rollback"])

    def test_float_change_is_not_normalized_away(self):
        env = self.scope(read=lambda obj: dict(json.loads(obj["record"]), weight=.27430000000000004))
        with self.assertRaises(ValueError):
            env["commit"]()
        self.assertEqual(env["tx"].flags, ["old endpoint rollback"])


if __name__ == "__main__":
    unittest.main()
