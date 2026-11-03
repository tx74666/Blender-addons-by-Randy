"""Narrow capability/legacy-history contract; actual RNA is checked natively."""
import copy
import importlib.util
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT=Path(__file__).resolve().parents[1]/'addons/character_designer'


class ID(dict):
    __hash__=object.__hash__
    def __init__(self,**values):super().__init__();self.__dict__.update(values)


class OwnedPoleTests(unittest.TestCase):
    def setUp(self):
        name='_owned_poles_'+uuid.uuid4().hex
        package=ModuleType(name);package.__path__=[str(ROOT)]
        self.limb=ModuleType(name+'.limb_ik')
        self.limb.VERSION_KEY='bone_version';self.limb.OWNER_VALUE='owned';self.limb.RIG_VERSION=1
        self.rig=ID(data=ID(),pose=NS(bones={}))
        # Real ownership puts the version on generated Bones, not Armature
        # data. An incorrect data-version shortcut must not hide this rig.
        self.rig.data['armature_id']='actual-data-owner'
        prop=NS(type='FLOAT',is_array=False,is_readonly=False,is_animatable=True,hard_min=-math.pi,hard_max=math.pi)
        self.con=NS(name='owned relation',type='IK',id_data=self.rig,target=self.rig,pole_target=self.rig,
            pole_angle=.3,bl_rna=NS(properties={'pole_angle':prop}),as_pointer=lambda:123,
            path_from_id=lambda field:'pose.bones["lower"].constraints["owned relation"].'+field)
        self.pb=NS(name='lower',id_data=self.rig,constraints={self.con.name:self.con})
        self.rig.pose.bones['lower']=self.pb
        self.record=dict(role='IK',owner='owned',version=1,rig_id='real-owned-token')
        self.limb._validate_inventory=Mock(return_value={'rigs':{('ARM','L'):{'entries':[(self.pb,self.con,self.record)]}}})
        bpy=NS(types=NS(ID=ID))
        self.patcher=patch.dict(sys.modules,{'bpy':bpy,name:package,self.limb.__name__:self.limb})
        self.patcher.start();self.addCleanup(self.patcher.stop)
        def load(short):
            spec=importlib.util.spec_from_file_location(name+'.'+short,ROOT/(short+'.py'))
            module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module);return module
        self.target=load('animation_worklist_target');self.revision=load('animation_worklist_revision')
        self.entry=dict(target=self.rig,data=self.rig.data,constraints={'lower':{'owned relation':dict(type='IK',mute=False)}},
            snapshot={'pose':{'lower':dict(location=[0.,0.,0.],rotation_euler=[0.,0.,0.],rotation_quaternion=[1.,0.,0.,0.],
                rotation_axis_angle=[0.,0.,1.,0.],scale=[1.,1.,1.])}},properties={'lower':{}},
            action=ID(label='old Action'),rest={'historical':'unchanged'},metadata={'old':'immutable'})
        self.pb.path_from_id=lambda:'pose.bones["lower"]'
        self.rig.path_resolve=lambda path:self.con.pole_angle
        self.path=self.con.path_from_id('pole_angle')

    def captured(self,value=.3):
        entry=dict(self.entry,constraints={'lower':{'owned relation':dict(type='IK',mute=False,pole_angle=value)}})
        return entry

    def test_legacy_capture_grants_no_new_path_and_does_not_scan_inventory(self):
        self.assertEqual(self.target._validate_owned_ik_poles(self.entry),{})
        self.limb._validate_inventory.assert_not_called()
        api=NS(target=self.target,fingerprint=NS(_selected_curves=lambda action,handle:[NS(data_path=self.path,array_index=0)]))
        with self.assertRaisesRegex(ValueError,'cannot fully roll back'):
            self.revision._validate_paths(self.rig,object(),1,api,{'targets':[self.entry]})

    def test_only_captured_owned_native_scalar_path_is_supported(self):
        self.assertNotIn(self.limb.VERSION_KEY,self.rig.data)
        self.assertEqual(self.target._validate_owned_ik_poles(self.captured()),{self.path:1})
        api=NS(target=self.target,fingerprint=NS(_selected_curves=lambda action,handle:[NS(data_path=self.path,array_index=0)]))
        self.revision._validate_paths(self.rig,object(),1,api,{'targets':[self.captured()]})
        api.fingerprint._selected_curves=lambda action,handle:[NS(data_path=self.path,array_index=1)]
        with self.assertRaisesRegex(ValueError,'array component'):
            self.revision._validate_paths(self.rig,object(),1,api,{'targets':[self.captured()]})

    def test_foreign_same_named_relation_or_native_target_is_rejected(self):
        self.con.target=ID()
        with self.assertRaisesRegex(ValueError,'binding changed'):
            self.target._validate_owned_ik_poles(self.captured())
        self.con.target=self.rig;self.record['owner']='foreign'
        with self.assertRaisesRegex(ValueError,'binding changed'):
            self.target._validate_owned_ik_poles(self.captured())

    def test_nonfinite_bool_out_of_native_range_or_wrong_constraint_refuses(self):
        for value in (float('nan'),float('inf'),True,math.pi+.01):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'finite native scalar'):
                self.target._validate_owned_ik_poles(self.captured(value))
        entry=self.captured();entry['constraints']['lower']['unknown']=entry['constraints']['lower'].pop('owned relation')
        with self.assertRaisesRegex(ValueError,'not an actual owned'):
            self.target._validate_owned_ik_poles(entry)

    def test_readonly_or_non_scalar_rna_cannot_gain_permission(self):
        prop=self.con.bl_rna.properties['pole_angle']
        for field in ('is_readonly','is_array'):
            setattr(prop,field,True)
            with self.assertRaisesRegex(ValueError,'binding changed'):
                self.target._validate_owned_ik_poles(self.captured())
            setattr(prop,field,False)

    def test_plain_or_foreign_only_rig_gets_no_owned_capability(self):
        self.limb._validate_inventory.return_value={'rigs':{}}
        self.assertEqual(self.target._owned_ik_pole_constraints(self.rig),{})
        with self.assertRaisesRegex(ValueError,'not an actual owned'):
            self.target._validate_owned_ik_poles(self.captured())

    def test_child_only_adds_missing_field_and_keeps_native_ids_and_parent(self):
        parent={'targets':[self.entry],'metadata':['historical'],'context':{'frame':7}}
        fresh={'targets':[self.captured(.7)],'metadata':['new'],'context':{'frame':14}}
        before=repr(parent)
        child=self.target._merge_owned_ik_poles(parent,fresh,self.rig)
        self.assertEqual(repr(parent),before)
        self.assertEqual(child['metadata'],parent['metadata']);self.assertEqual(child['context'],parent['context'])
        self.assertIs(child['targets'][0]['action'],self.entry['action'])
        self.assertIs(child['targets'][0]['data'],self.rig.data)
        self.assertEqual(child['targets'][0]['snapshot'],self.entry['snapshot'])
        self.assertEqual(child['targets'][0]['constraints']['lower']['owned relation']['pole_angle'],.7)

    def test_child_keeps_existing_historical_pole_value_not_fresh_pending_value(self):
        parent={'targets':[self.captured(.2)],'metadata':[],'context':{}}
        child=self.target._merge_owned_ik_poles(parent,{'targets':[self.captured(.8)]},self.rig)
        self.assertEqual(child['targets'][0]['constraints']['lower']['owned relation']['pole_angle'],.2)
        self.assertEqual(parent['targets'][0]['constraints']['lower']['owned relation']['pole_angle'],.2)

    def test_merge_rejects_changed_native_data_or_relation_without_mutation(self):
        parent={'targets':[self.entry]};fresh={'targets':[dict(self.captured(),data=ID())]}
        before=repr(parent)
        with self.assertRaisesRegex(ValueError,'exact fresh'):
            self.target._merge_owned_ik_poles(parent,fresh,self.rig)
        self.assertEqual(repr(parent),before)


if __name__=='__main__':unittest.main()
