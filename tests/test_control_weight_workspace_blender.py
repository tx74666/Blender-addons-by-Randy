"""Recovery and context boundaries for the reversible Edit Weights workspace."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'addons'), str(ROOT/'tests')]
import character_designer
from character_designer import (control_weight_paint as weights, control_pose_assets as poses,
    bone_display, bone_display_sync, selected_bone_weights as paint, limb_ik)
import test_body_calibration_blender as setup
import test_control_pose_weights_blender as workflows

matrices = workflows.matrices


def context_state(context, mesh, rig):
    state = paint._capture_context_state(context,mesh,rig)
    state['active_object'] = state['active_object'].name if state['active_object'] else None
    state['selected_objects'] = tuple(sorted(obj.name for obj in state['selected_objects']))
    state['pose_selection'] = tuple(sorted(state['pose_selection']))
    return state


def object_flags(obj, layer):
    return obj.hide_get(view_layer=layer),obj.hide_viewport,obj.hide_select


class Layout:
    def __init__(self): self.buttons=[]
    def operator(self,name,**kwargs):
        self.buttons.append((name,kwargs.get('text','')))
        return SimpleNamespace()


class WeightWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): character_designer.register()

    def setUp(self):
        weights.register()
        if weights.active(bpy.context): weights.leave(bpy.context)
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        self.rig,self.mesh = setup.fixture()
        setup.prepare(self.rig)

    _weight_setup = workflows.Workflows._weight_setup
    assertPose = workflows.Workflows.assertPose

    def tearDown(self):
        weights.register()
        if weights.active(bpy.context):
            saved=weights.active(bpy.context)
            layer=bpy.context.scene.view_layers.get(json.loads(saved['state'])['view_layer'])
            if layer and bpy.context.window: bpy.context.window.view_layer=layer
            weights.leave(bpy.context)

    def assertRecoveryEntry(self):
        self.assertTrue(weights.active(bpy.context))
        layout=Layout(); weights.draw(layout,bpy.context)
        self.assertEqual(layout.buttons,[('character_designer.control_weights','Back to Controls')])
        self.assertTrue(weights.CHARACTERDESIGNER_PT_control_weights.poll(bpy.context))

    def test_scene_id_references_survive_save_reopen_and_return(self):
        self._weight_setup()
        rig_name,mesh_name=self.rig.name,self.mesh.name
        before=context_state(bpy.context,self.mesh,self.rig)
        display=bone_display._snapshot(self.rig)
        rest=poses.native_rest(self.rig)
        pose=matrices(self.rig)
        weights.enter(bpy.context)
        self.mesh.vertex_groups['hand.L'].add([2],.41,'REPLACE')
        authored=setup.mesh_state(self.mesh)
        with tempfile.TemporaryDirectory(prefix='control-weights-session-') as directory:
            path=str(Path(directory)/'workspace.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path,copy=True)
            bpy.ops.wm.open_mainfile(filepath=path)
            # Never dereference IDs captured before file loading.
            self.rig=bpy.data.objects[rig_name]; self.mesh=bpy.data.objects[mesh_name]
            saved=weights.active(bpy.context)
            self.assertEqual(saved['rig'],self.rig)
            self.assertEqual(saved['mesh'],self.mesh)
            self.assertEqual(saved['active'],self.rig)
            self.assertTrue(all(isinstance(obj,bpy.types.Object) for obj in saved['selected'].values()))
            self.assertRecoveryEntry()
            weights.leave(bpy.context)
            self.assertFalse(weights.active(bpy.context))
            self.assertEqual(before,context_state(bpy.context,self.mesh,self.rig))
            self.assertEqual(display,bone_display._snapshot(self.rig))
            self.assertEqual(rest,poses.native_rest(self.rig))
            self.assertEqual(authored,setup.mesh_state(self.mesh))
            self.assertPose(pose)

    def test_unregister_after_view_layer_switch_restores_original_layer_only(self):
        self._weight_setup()
        window=bpy.context.window
        self.assertIsNotNone(window)
        scene=bpy.context.scene; original=bpy.context.view_layer
        display=bone_display._snapshot(self.rig)
        before=context_state(bpy.context,self.mesh,self.rig)
        flags=object_flags(self.rig,original),object_flags(self.mesh,original)
        other=scene.view_layers.new('Weight workspace alternate')
        marker=bpy.data.objects.new('Other layer selection',None)
        scene.collection.objects.link(marker)
        weights.enter(bpy.context)
        window.view_layer=other
        for obj in other.objects: obj.select_set(False,view_layer=other)
        marker.select_set(True,view_layer=other); other.objects.active=marker
        self.mesh.hide_set(True,view_layer=other)
        other_before=(other.objects.active,tuple(obj for obj in other.objects if obj.select_get(view_layer=other)),
                      self.mesh.hide_get(view_layer=other))
        try:
            weights.unregister()
            self.assertEqual(window.view_layer,other)
            self.assertFalse(scene.get(weights.SESSION))
            # Display snapshots now include the owning ViewLayer's object eye.
            # Compare the saved layer while keeping the artist's window on the
            # alternate layer; the following checks protect that layer too.
            with bpy.context.temp_override(view_layer=original):
                self.assertEqual(display,bone_display._snapshot(self.rig))
            self.assertEqual(flags,(object_flags(self.rig,original),object_flags(self.mesh,original)))
            self.assertEqual(other_before,(other.objects.active,
                tuple(obj for obj in other.objects if obj.select_get(view_layer=other)),
                self.mesh.hide_get(view_layer=other)))
            # context.mode follows the window's active object; a view-layer-only
            # override still reports the other layer's marker in Object Mode.
            window.view_layer=original
            self.assertEqual(before,context_state(bpy.context,self.mesh,self.rig))
        finally:
            window.view_layer=original
            weights.register()
            if scene.get(weights.SESSION): weights.leave(bpy.context)
            scene.view_layers.remove(other)

    def test_enter_and_rollback_failure_keep_recoverable_original_workspace(self):
        self._weight_setup()
        before=context_state(bpy.context,self.mesh,self.rig)
        display=bone_display._snapshot(self.rig); authored=setup.mesh_state(self.mesh)
        pose=matrices(self.rig); busy=bone_display_sync._BUSY
        with patch.object(weights.paint,'_enter_pose_mode',side_effect=ValueError('Injected enter failure')), \
             patch.object(weights,'_restore',side_effect=RuntimeError('Injected rollback failure')):
            with self.assertRaises((ValueError,RuntimeError)):
                weights.enter(bpy.context)
        self.assertRecoveryEntry()
        self.assertEqual(json.loads(weights.active(bpy.context)['state'])['mode'],before['mode'])
        self.assertEqual(bone_display_sync._BUSY,busy)
        weights.leave(bpy.context)
        self.assertFalse(weights.active(bpy.context))
        self.assertEqual(before,context_state(bpy.context,self.mesh,self.rig))
        self.assertEqual(display,bone_display._snapshot(self.rig))
        self.assertEqual(authored,setup.mesh_state(self.mesh)); self.assertPose(pose)

    def test_return_late_failure_preserves_session_and_new_weights_for_retry(self):
        self._weight_setup()
        before=context_state(bpy.context,self.mesh,self.rig)
        display=bone_display._snapshot(self.rig); pose=matrices(self.rig)
        weights.enter(bpy.context)
        saved_json=weights.active(bpy.context)['state']
        self.mesh.vertex_groups['hand.L'].add([2],.29,'REPLACE')
        authored=setup.mesh_state(self.mesh)
        painting=bone_display._snapshot(self.rig)
        restore=weights._restore; calls=[]
        def fail_after_restore(context,saved):
            calls.append(saved)
            restore(context,saved)
            if len(calls)==1: raise ValueError('Injected after restoring controls')
        with patch.object(weights,'_restore',side_effect=fail_after_restore):
            with self.assertRaisesRegex(ValueError,'Injected after restoring controls'):
                weights.leave(bpy.context)
        self.assertEqual(len(calls),2)
        self.assertRecoveryEntry()
        self.assertEqual(weights.active(bpy.context)['state'],saved_json)
        self.assertEqual(bpy.context.mode,'PAINT_WEIGHT')
        self.assertEqual(painting,bone_display._snapshot(self.rig))
        self.assertEqual(authored,setup.mesh_state(self.mesh)); self.assertPose(pose)
        weights.leave(bpy.context)
        self.assertFalse(weights.active(bpy.context))
        self.assertEqual(before,context_state(bpy.context,self.mesh,self.rig))
        self.assertEqual(display,bone_display._snapshot(self.rig))
        self.assertEqual(authored,setup.mesh_state(self.mesh)); self.assertPose(pose)

    def test_nondefault_selection_visibility_and_vertex_group_are_restored(self):
        target=self._weight_setup()
        layer=bpy.context.view_layer
        marker=bpy.data.objects.new('Artist selection marker',None)
        bpy.context.scene.collection.objects.link(marker); marker.select_set(True)
        self.rig.pose.bones['upper_arm.R'].select=True
        self.rig.data.bones.active=target.bone
        self.rig.data.display_type='STICK'; self.rig.data.show_bone_custom_shapes=True
        self.rig.show_in_front=False; self.rig.hide_select=True
        parent=self.rig.data.collections.new('Artist visibility parent')
        child=self.rig.data.collections.new('Artist visibility child',parent=parent)
        child.assign(self.rig.data.bones['Hips']); child.is_visible=False; parent.is_visible=False
        self.rig.data.collections.active=child
        bone_display._set_hidden(self.rig,self.rig.data.bones['hand.R'],True)
        self.rig.data.bones['hand.R'].hide_select=True
        self.mesh.vertex_groups.active_index=self.mesh.vertex_groups['Hips'].index
        self.mesh.hide_set(True); self.mesh.hide_viewport=True; self.mesh.hide_select=True
        bpy.context.view_layer.update()
        before=context_state(bpy.context,self.mesh,self.rig)
        display=bone_display._snapshot(self.rig)
        flags=object_flags(self.rig,layer),object_flags(self.mesh,layer)
        rest=poses.native_rest(self.rig); pose=matrices(self.rig); authored=setup.mesh_state(self.mesh)
        active_collection=self.rig.data.collections.active.name
        weights.enter(bpy.context)
        self.assertEqual(bpy.context.mode,'PAINT_WEIGHT')
        self.assertEqual(self.mesh.vertex_groups.active.name,'hand.L')
        self.assertEqual(authored,setup.mesh_state(self.mesh))
        self.mesh.vertex_groups['hand.L'].add([2],.63,'REPLACE')
        authored=setup.mesh_state(self.mesh)
        weights.leave(bpy.context)
        self.assertEqual(before,context_state(bpy.context,self.mesh,self.rig))
        self.assertEqual(display,bone_display._snapshot(self.rig))
        self.assertEqual(flags,(object_flags(self.rig,layer),object_flags(self.mesh,layer)))
        self.assertEqual(self.rig.data.collections.active.name,active_collection)
        self.assertFalse(self.rig.show_in_front)
        self.assertEqual(rest,poses.native_rest(self.rig)); self.assertPose(pose)
        self.assertEqual(authored,setup.mesh_state(self.mesh))

    def test_multiple_bound_meshes_require_an_explicit_choice(self):
        self._weight_setup()
        other=self.mesh.copy(); other.data=self.mesh.data.copy(); other.name='Second bound body'
        bpy.context.scene.collection.objects.link(other)
        bpy.context.scene.character_designer_setup.body=None
        before=context_state(bpy.context,self.mesh,self.rig)
        display=bone_display._snapshot(self.rig); authored=setup.mesh_state(self.mesh)
        with self.assertRaisesRegex(ValueError,'Select.*mesh|set Body'):
            weights.enter(bpy.context)
        self.assertFalse(weights.active(bpy.context))
        self.assertEqual(before,context_state(bpy.context,self.mesh,self.rig))
        self.assertEqual(display,bone_display._snapshot(self.rig))
        self.assertEqual(authored,setup.mesh_state(self.mesh))
        bpy.context.scene.character_designer_setup.body=self.mesh
        _rig,selected=weights.enter(bpy.context)
        self.assertEqual(selected,self.mesh)
        self.assertEqual(bpy.context.object,self.mesh)
        weights.leave(bpy.context)


if __name__=='__main__':
    result=unittest.main(argv=[__file__],exit=False).result
    if not result.wasSuccessful(): raise RuntimeError('Control weight workspace tests failed')
