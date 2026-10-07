"""Captured native5.2 graph contract regression; no Blender geometry is mocked."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import types
import unittest

from test_skirt_surface_contract import load, SOURCE

FIXTURE=Path(__file__).resolve().parent/"fixtures/dress_node_contract52.json"
FIXTURE_SHA="401d120ea9bb5b1f9e3ec3c08a583ff72ee3727e684a049e7eca55cf9ce132bf"


class NodeLayoutContract(unittest.TestCase):
    def setUp(self):
        raw=FIXTURE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),FIXTURE_SHA)
        self.fixture=json.loads(raw)
        self.s=load();self.saved=self.fixture["saved"];self.runtime=self.fixture["runtime"]

    def test_actual_saved_and_native_52_layout_difference_is_compatible_read_only(self):
        self.assertNotEqual(self.saved,self.runtime)
        before=copy.deepcopy((self.saved,self.runtime))
        self.assertTrue(self.s["_node_contract_equal"](self.saved,self.runtime))
        self.assertTrue(self.s["_node_contract_equal"](self.runtime,self.saved))
        self.assertEqual((self.saved,self.runtime),before)
        self.assertEqual(self.fixture["provenance"]["difference_count"],15)

    def test_only_six_class_layout_fields_are_ignored_in_both_contracts(self):
        expected={"bl_width_default","bl_width_min","bl_width_max","bl_height_default","bl_height_min","bl_height_max"}
        self.assertEqual(set(self.s["_NODE_LAYOUT_BOUNDS"]),expected)
        saved,current=copy.deepcopy(self.saved),copy.deepcopy(self.runtime)
        for node in saved["nodes"].values():
            for index,key in enumerate(expected):node["rna"][key]=100.+index
        for node in current["nodes"].values():
            for key in expected:node["rna"].pop(key,None)
        self.assertTrue(self.s["_node_contract_equal"](saved,current))
        self.assertTrue(self.s["_node_contract_equal"](current,saved))
        node=next(n for n in ast.parse(SOURCE.read_text(encoding="utf-8")).body
                  if isinstance(n,ast.FunctionDef) and n.name=="_node_content")
        self.assertIn("_NODE_LAYOUT_BOUNDS",ast.unparse(node))

    def test_semantic_operation_target_default_socket_link_type_interface_unknown_stay_rejected(self):
        changes={
            "operation":lambda v:v["nodes"]["Difference"]["rna"].__setitem__("operation","ADD"),
            "target":lambda v:v["nodes"]["Cloth"]["inputs"]["0"]["default_value"].__setitem__("name","Artist other mesh"),
            "socket_default":lambda v:v["nodes"]["Set Position"]["inputs"]["1"].__setitem__("default_value",False),
            "socket_type":lambda v:v["nodes"]["Cloth"]["inputs"]["0"].__setitem__("type","FLOAT"),
            "link":lambda v:v["links"][0].__setitem__(0,"Artist node"),
            "node_type":lambda v:v["nodes"]["Cloth"].__setitem__("type","GeometryNodeInputPosition"),
            "interface":lambda v:v["interface"][0].__setitem__("direction","OUT"),
            "unknown_node_property":lambda v:v["nodes"]["Cloth"]["rna"].__setitem__("bl_new_semantic_field",True),
            "unknown_top_level":lambda v:v.__setitem__("custom_geometry_mode","CHANGED"),
            "layout_name_outside_node_rna":lambda v:v["nodes"]["Cloth"]["inputs"]["0"].__setitem__("bl_height_max",30.),
        }
        for name,change in changes.items():
            with self.subTest(change=name):
                value=copy.deepcopy(self.runtime);change(value)
                self.assertFalse(self.s["_node_contract_equal"](self.saved,value))
                self.assertFalse(self.s["_node_contract_equal"](value,self.saved))

    def test_overlay_still_checks_captured_contract_and_keeps_saved_record(self):
        class Group(dict):
            users=1
        source=types.SimpleNamespace(modifiers={})
        group=Group({self.s["ROLE_KEY"]:self.s["NODE_ROLE"],"owner":"captured-owner","source":source})
        modifier=types.SimpleNamespace(type="NODES",node_group=group)
        source.modifiers["Dress overlay"]=modifier
        self.s["bpy"].data=types.SimpleNamespace(node_groups={"Dress group":group})
        runtime=copy.deepcopy(self.runtime)
        self.s["_node_content"]=lambda _group:runtime
        record={"owner":"captured-owner","physics":{"backend":self.s["BACKEND"],"surface":{
            "owner":"captured-owner","version":1,"roles":{role:[role] for role in self.s["OBJECT_ROLES"]},
            "overlay":"Dress overlay","node_group":"Dress group","node_contract":copy.deepcopy(self.saved)}}}
        before=copy.deepcopy(record)
        self.assertIs(self.s["_overlay"](source,record),modifier)
        self.assertEqual(record,before)
        runtime["nodes"]["Difference"]["rna"]["operation"]="ADD"
        with self.assertRaisesRegex(ValueError,"Preserve the edited Dress node graph"):
            self.s["_overlay"](source,record)
        self.assertEqual(record,before)
        group.users=2
        with self.assertRaisesRegex(ValueError,"unique owned"):
            self.s["_overlay"](source,record)


if __name__=="__main__":unittest.main()
