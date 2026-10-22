"""Retired shortcut compatibility on a tiny disposable native fixture."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import bpy
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'addons'), str(ROOT/'tests')]
import character_designer
from character_designer import control_weight_paint as legacy, bone_display, selected_bone_weights as paint


def fixture():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for scene in bpy.data.scenes:
        scene.pop(legacy.SESSION, None)
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    data = bpy.data.armatures.new('Recovery Rig Data')
    rig = bpy.data.objects.new('Recovery Rig', data)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    bone = data.edit_bones.new('Leg')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bone = data.edit_bones.new('Foot')
    bone.head, bone.tail = (0, 0, 1), (0, 1, 1)
    bone.parent = data.edit_bones['Leg']
    bpy.ops.object.mode_set(mode='OBJECT')
    mesh = bpy.data.meshes.new('Recovery Mesh Data')
    mesh.from_pydata([(0,0,0), (0,0,1), (0,1,1)], [(0,1),(1,2)], [])
    obj = bpy.data.objects.new('Recovery Mesh', mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new('Existing Skin', 'ARMATURE').object = rig
    obj.vertex_groups.new(name='Leg').add([0,1,2], .5, 'REPLACE')
    obj.vertex_groups.new(name='Foot').add([2], .5, 'REPLACE')
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Artist Shape')
    key.data[0].co.x, key.value = .125, .25
    paint._enter_pose_mode(bpy.context, rig, ('Leg',), 'Leg')
    return rig, obj


def author_state(rig, mesh):
    return (tuple(tuple(v.co) for v in mesh.data.vertices),
            tuple(tuple(e.vertices) for e in mesh.data.edges),
            tuple((key.name,key.value,tuple(tuple(p.co) for p in key.data)) for key in mesh.data.shape_keys.key_blocks),
            tuple((b.name,tuple(tuple(row) for row in b.matrix_local)) for b in rig.data.bones),
            tuple((b.name,tuple(tuple(row) for row in b.matrix_basis)) for b in rig.pose.bones),
            tuple((g.name,g.lock_weight) for g in mesh.vertex_groups),
            tuple(tuple((g.group,g.weight) for g in v.groups) for v in mesh.data.vertices))


def simulate_old_workspace(rig, mesh):
    # Only a test fixture creates an old record; production has no creator.
    bpy.context.scene[legacy.SESSION] = legacy._state(bpy.context, rig, mesh)
    rig.show_in_front = True
    rig.data.show_bone_custom_shapes = False
    rig.data.display_type = 'OCTAHEDRAL'
    paint._enter_weight_paint(bpy.context, mesh, rig, ('Leg',))


class WeightWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): character_designer.register()

    def setUp(self):
        legacy._remove_callbacks()
        self.rig, self.mesh = fixture()

    def tearDown(self):
        legacy._remove_callbacks()
        for scene in bpy.data.scenes: scene.pop(legacy.SESSION, None)

    def test_retired_entry_points_and_rna_are_absent(self):
        for name in ('enter','draw','target','mesh_for','mapped_bone','CLASSES'):
            self.assertFalse(hasattr(legacy,name),name)
        self.assertIsNone(bpy.types.Operator.bl_rna_get_subclass_py('CHARACTERDESIGNER_OT_control_weights'))
        self.assertIsNone(bpy.types.Panel.bl_rna_get_subclass_py('CHARACTERDESIGNER_PT_control_weights'))

    def test_native_paint_creates_no_session_and_recovery_is_noop(self):
        paint._enter_weight_paint(bpy.context,self.mesh,self.rig,('Leg',))
        self.mesh.vertex_groups['Leg'].add([2],.375,'REPLACE')
        before=author_state(self.rig,self.mesh)
        self.assertIsNone(legacy.active(bpy.context))
        self.assertIsNone(legacy._recover_once())
        self.assertEqual(bpy.context.mode,'PAINT_WEIGHT')
        self.assertEqual(bpy.context.object,self.mesh)
        self.assertEqual(author_state(self.rig,self.mesh),before)

    def test_legacy_recovery_restores_workspace_and_keeps_latest_paint(self):
        display=bone_display._snapshot(self.rig)
        simulate_old_workspace(self.rig,self.mesh)
        self.mesh.vertex_groups['Leg'].add([2],.375,'REPLACE')
        before=author_state(self.rig,self.mesh)
        legacy._recover_once()
        self.assertIsNone(legacy.active(bpy.context))
        self.assertEqual(bpy.context.mode,'POSE')
        self.assertEqual(bpy.context.object,self.rig)
        self.assertEqual(bone_display._snapshot(self.rig),display)
        self.assertEqual(author_state(self.rig,self.mesh),before)

    def test_late_failure_keeps_record_and_rolls_back_display(self):
        simulate_old_workspace(self.rig,self.mesh)
        self.mesh.vertex_groups['Leg'].add([2],.375,'REPLACE')
        before=author_state(self.rig,self.mesh)
        display=bone_display._snapshot(self.rig)
        saved=legacy.active(bpy.context)['state']
        original,calls=legacy._restore,[]
        def fail(context,state):
            calls.append(True)
            original(context,state)
            if len(calls)==1: raise ValueError('Injected late recovery failure')
        with patch.object(legacy,'_restore',side_effect=fail):
            with self.assertRaisesRegex(ValueError,'Injected late'): legacy.leave(bpy.context)
        self.assertEqual(legacy.active(bpy.context)['state'],saved)
        self.assertEqual(bpy.context.mode,'PAINT_WEIGHT')
        self.assertEqual(bone_display._snapshot(self.rig),display)
        self.assertEqual(author_state(self.rig,self.mesh),before)

    def test_saved_layer_recovery_preserves_other_layer_selection(self):
        simulate_old_workspace(self.rig,self.mesh)
        bpy.ops.object.mode_set(mode='OBJECT')
        scene,window=bpy.context.scene,bpy.context.window
        other=scene.view_layers.new('Unrelated Layer')
        window.view_layer=other
        marker=bpy.data.objects.new('Unrelated Selection',None)
        scene.collection.objects.link(marker)
        for obj in other.objects: obj.select_set(False,view_layer=other)
        marker.select_set(True,view_layer=other)
        other.objects.active=marker
        before=author_state(self.rig,self.mesh)
        legacy._recover_once()
        self.assertIsNone(scene.get(legacy.SESSION))
        self.assertEqual(window.scene,scene)
        self.assertEqual(window.view_layer,other)
        self.assertEqual(other.objects.active,marker)
        self.assertEqual([o for o in other.objects if o.select_get(view_layer=other)],[marker])
        self.assertEqual(author_state(self.rig,self.mesh),before)

    def test_bad_saved_layer_retains_record_without_permanent_retry(self):
        simulate_old_workspace(self.rig,self.mesh)
        saved=legacy.active(bpy.context)
        value=json.loads(saved['state']); value['view_layer']='Missing Layer'
        saved['state']=json.dumps(value)
        before=author_state(self.rig,self.mesh)
        legacy._recover_once()
        self.assertTrue(legacy.active(bpy.context))
        self.assertIsNone(legacy._TIMER)
        self.assertEqual(author_state(self.rig,self.mesh),before)

    def test_registration_is_idempotent_and_only_removes_owned_callbacks(self):
        foreign=lambda _value:None
        bpy.app.handlers.load_post.append(foreign)
        try:
            legacy.register();legacy.register()
            self.assertEqual([fn for fn in bpy.app.handlers.load_post if fn.__module__==legacy.__name__],[legacy._load_post])
            self.assertTrue(bpy.app.timers.is_registered(legacy._TIMER))
            legacy.unregister()
            self.assertIn(foreign,bpy.app.handlers.load_post)
            self.assertFalse(any(fn.__module__==legacy.__name__ for fn in bpy.app.handlers.load_post))
            self.assertFalse(bpy.app.timers.is_registered(legacy._recover_once))
        finally: bpy.app.handlers.load_post.remove(foreign)

    def test_saved_legacy_record_reopens_and_recovers_without_losing_paint(self):
        simulate_old_workspace(self.rig,self.mesh)
        self.mesh.vertex_groups['Leg'].add([2],.375,'REPLACE')
        expected=author_state(self.rig,self.mesh)
        with tempfile.TemporaryDirectory(prefix='retired-weight-workspace-') as directory:
            path=str(Path(directory)/'private.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path,copy=True,check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path,use_scripts=True)
            self.rig=bpy.data.objects['Recovery Rig'];self.mesh=bpy.data.objects['Recovery Mesh']
            self.assertTrue(legacy.active(bpy.context))
            legacy._recover_once()
            self.assertIsNone(legacy.active(bpy.context))
            self.assertEqual(author_state(self.rig,self.mesh),expected)


if __name__=='__main__':
    result=unittest.main(argv=[__file__],exit=False).result
    if not result.wasSuccessful(): raise RuntimeError('Retired weight workspace tests failed')
