"""Pose-asset shortcut ownership and ordinary-rig delegation.

Real Blender addon keymaps are used when factory-startup provides them. The
background-only fallback tests collection/flag routing with synthetic items;
neither path claims to test GUI double clicks or merged user-keymap priority.
The official asset operator is spied on for delegation, not executed in a fake
Asset Browser context.
"""
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'addons'),str(ROOT/'tests')]
from character_designer import control_pose_assets as poses, body_setup

OFFICIAL='poselib.apply_pose_asset'
ROUTER='character_designer.apply_control_pose'
FIELDS=('idname','type','value','ctrl','alt','shift','oskey','any','active')
BACKENDS=set()


class SyntheticItems(list):
    def new(self,idname,event,value,**kwargs):
        flags={name:False for name in ('ctrl','alt','shift','oskey','any')}
        flags.update({name:value for name,value in kwargs.items() if name in flags})
        item=SimpleNamespace(idname=idname,type=event,value=value,active=True,properties=SimpleNamespace(),**flags)
        self.insert(0 if kwargs.get('head') else len(self),item)
        return item


class SyntheticMaps(dict):
    def new(self,*,name,space_type='EMPTY'):
        result=SimpleNamespace(name=name,space_type=space_type,keymap_items=SyntheticItems())
        self[name]=result
        return result
    def remove(self,km): self.pop(km.name)


def signature(item):
    return tuple(getattr(item,name) for name in FIELDS)


class PoseKeymapTests(unittest.TestCase):
    def setUp(self):
        poses.unregister()
        self.stack=ExitStack(); self.items=[]; self.maps=[]; self.rig=None
        addon=bpy.context.window_manager.keyconfigs.addon
        self.backend='RNA' if addon else 'SYNTHETIC_NO_ADDON_KEYCONFIG'
        BACKENDS.add(self.backend)
        self.config=addon or SimpleNamespace(keymaps=SyntheticMaps())
        self.official=SimpleNamespace(addon_keymaps=[])
        self.stack.enter_context(patch.dict(sys.modules,{'pose_library.keymaps':self.official}))
        self.api=SimpleNamespace(context=SimpleNamespace(window_manager=SimpleNamespace(
            keyconfigs=SimpleNamespace(addon=self.config))),types=bpy.types,utils=bpy.utils,
            app=bpy.app,ops=bpy.ops)
        self.stack.enter_context(patch.object(poses,'bpy',self.api))

    def tearDown(self):
        try:
            poses.unregister()
            for km,item in reversed(self.items):
                try: km.keymap_items.remove(item)
                except (ReferenceError,RuntimeError,ValueError): pass
            for km in reversed(self.maps):
                if not km.keymap_items: self.config.keymaps.remove(km)
            if self.rig is not None:
                if bpy.context.object and bpy.context.object.mode!='OBJECT':
                    bpy.ops.object.mode_set(mode='OBJECT')
                data=self.rig.data
                bpy.data.objects.remove(self.rig,do_unlink=True)
                bpy.data.armatures.remove(data)
        finally:
            self.stack.close()

    def official_item(self,*,map_name='Asset Browser Main',event='LEFTMOUSE',value='DOUBLE_CLICK',
                      idname=OFFICIAL,active=True,**modifiers):
        km=self.config.keymaps.get(map_name)
        if km is None:
            km=self.config.keymaps.new(name=map_name,space_type='FILE_BROWSER' if map_name=='Asset Browser Main' else 'EMPTY')
            self.maps.append(km)
        item=km.keymap_items.new(idname,event,value,**modifiers)
        item.active=active
        self.items.append((km,item)); self.official.addon_keymaps.append((km,item))
        return item

    def router_items(self):
        km=self.config.keymaps.get('Asset Browser Main')
        return [item for item in km.keymap_items if item.idname==ROUTER] if km else []

    def test_register_disables_only_matching_active_official_double_click(self):
        matched=self.official_item()
        untouched=[self.official_item(active=False),self.official_item(ctrl=True),
                   self.official_item(alt=True),self.official_item(shift=True),
                   self.official_item(oskey=True),self.official_item(any=True),
                   self.official_item(value='CLICK'),self.official_item(event='RIGHTMOUSE'),
                   self.official_item(map_name='Control Pose Test Other'),
                   self.official_item(idname='wm.search_menu')]
        before=signature(matched); others=[signature(item) for item in untouched]
        poses.register()
        self.assertFalse(matched.active)
        self.assertEqual(signature(matched)[:-1],before[:-1])
        self.assertEqual([signature(item) for item in untouched],others)
        self.assertEqual(len(poses._REPLACED),1)
        routed=self.router_items()
        self.assertEqual(len(routed),2)
        self.assertEqual({(item.shift,item.properties.flipped) for item in routed}, {(False,False),(True,True)})
        self.assertEqual((routed[0].type,routed[0].value),('LEFTMOUSE','DOUBLE_CLICK'))
        poses.unregister()
        self.assertEqual(signature(matched),before)
        self.assertEqual([signature(item) for item in untouched],others)
        self.assertFalse(self.router_items())

    def test_register_and_unregister_are_idempotent(self):
        original=self.official_item(); before=signature(original)
        for _ in range(3): poses.register()
        self.assertTrue(poses.CHARACTERDESIGNER_OT_apply_control_pose.is_registered)
        self.assertEqual(len(poses._KEYMAPS),2)
        self.assertEqual(len(self.router_items()),2)
        self.assertEqual(len(poses._REPLACED),1)
        for _ in range(3): poses.unregister()
        self.assertFalse(poses.CHARACTERDESIGNER_OT_apply_control_pose.is_registered)
        self.assertFalse(poses._KEYMAPS); self.assertFalse(poses._REPLACED)
        self.assertFalse(self.router_items())
        self.assertEqual(signature(original),before)
        poses.register(); poses.unregister()
        self.assertEqual(signature(original),before)

    def test_late_official_registration_is_routed_once_and_restored(self):
        poses.register()
        original=self.official_item(); inactive=self.official_item(active=False)
        poses._route_double_click(); poses._route_double_click()
        self.assertFalse(original.active); self.assertFalse(inactive.active)
        self.assertEqual(len(poses._REPLACED),1)
        self.assertEqual(len(self.router_items()),2)
        poses.unregister()
        self.assertTrue(original.active); self.assertFalse(inactive.active)

    def test_missing_official_addon_and_removed_item_are_safe(self):
        self.stack.enter_context(patch.dict(sys.modules,{'pose_library.keymaps':None}))
        poses.register(); poses._route_double_click()
        self.assertFalse(poses._REPLACED)
        self.assertEqual(len(self.router_items()),2)
        class RemovedItem:
            @property
            def idname(self): raise ReferenceError('Test item was removed')
        # Exercise Blender's invalid-RNA exception path without relying on a
        # backend-specific fake pointer after collection removal.
        poses._REPLACED.append(RemovedItem())
        poses.unregister(); poses.unregister()
        self.assertFalse(poses._REPLACED); self.assertFalse(self.router_items())

    def test_user_reenabled_official_item_is_not_toggled_back_off(self):
        original=self.official_item(); poses.register()
        self.assertFalse(original.active)
        original.active=True
        poses.unregister()
        self.assertTrue(original.active)
        self.assertFalse(self.router_items())

    def test_plain_armature_delegates_to_official_operator_without_pose_matching(self):
        if bpy.context.object and bpy.context.object.mode!='OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in bpy.context.selected_objects: obj.select_set(False)
        data=bpy.data.armatures.new('Plain pose keymap rig')
        self.rig=bpy.data.objects.new('Plain pose keymap rig',data)
        bpy.context.scene.collection.objects.link(self.rig)
        self.rig.select_set(True); bpy.context.view_layer.objects.active=self.rig
        bpy.ops.object.mode_set(mode='EDIT')
        bone=data.edit_bones.new('artist_arm'); bone.head=(0,0,0); bone.tail=(0,1,0)
        bpy.ops.object.mode_set(mode='POSE')
        self.assertFalse(body_setup.has_generated(self.rig))
        context=SimpleNamespace(object=self.rig,asset=SimpleNamespace(id_type='ACTION'))
        self.assertTrue(poses.CHARACTERDESIGNER_OT_apply_control_pose.poll(context))
        before={pb.name:pb.matrix_basis.copy() for pb in self.rig.pose.bones}
        report=Mock(); operator=SimpleNamespace(report=report,flipped=False)
        official=Mock()
        self.api.ops=SimpleNamespace(poselib=SimpleNamespace(apply_pose_asset=official))
        with patch.object(poses,'_asset_channels',side_effect=AssertionError('Plain rig parsed as generated controls')), \
             patch.object(poses,'apply_channels',side_effect=AssertionError('Plain rig entered matching')):
            for outcome in ({'FINISHED'},{'CANCELLED'}):
                official.return_value=outcome
                self.assertEqual(poses.CHARACTERDESIGNER_OT_apply_control_pose.execute(operator,context),outcome)
        self.assertEqual(official.call_count,2)
        for call in official.call_args_list: self.assertEqual((call.args,call.kwargs),((),{'flipped':False}))
        report.assert_not_called()
        self.assertEqual(before,{pb.name:pb.matrix_basis for pb in self.rig.pose.bones})


if __name__=='__main__':
    result=unittest.main(argv=[__file__],exit=False).result
    print('CONTROL_POSE_KEYMAP_BACKENDS',','.join(sorted(BACKENDS)),flush=True)
    print('KEYMAP_BOUNDARY: no GUI double-click or merged user-keymap priority test; official operator delegation uses a spy.',flush=True)
    if not result.wasSuccessful(): raise RuntimeError('Control pose keymap tests failed')
