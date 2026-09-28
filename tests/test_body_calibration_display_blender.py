"""Display-cache invalidation and compact UI, in a disposable Blender process."""
import sys
import json
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'addons'), str(ROOT/'tests')]
import character_designer
from character_designer import body_calibration as c, body_calibration_display as display
from character_designer import body_calibration_overlay as overlay, body_calibration_ui as ui, limb_ik
from test_body_calibration_blender import fixture


class Layout:
    def __init__(self): self.labels = []; self.props = []
    def label(self, **kw): self.labels.append(kw.get('text', ''))
    def prop(self, data, name, **kw): self.props.append(name)
    def __getattr__(self, name): return lambda *args, **kwargs: self


class DisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): character_designer.register()

    def setUp(self): display.clear()

    def test_repeated_draw_reuses_plan_and_setting_change_invalidates(self):
        rig, _ = fixture(); s = c.settings(rig); s.part = 'ARMS'; s.show_directions = True
        before = c.native_rest(rig), rig.get(c.KEY), set(bpy.data.objects.keys())
        with patch.object(c, 'status', wraps=c.status) as compute:
            first = overlay.segments(bpy.context)
            second = overlay.segments(bpy.context)
            self.assertIs(first, second)
            self.assertEqual(compute.call_count, 1)
            s.pole_distance += .1
            third = overlay.segments(bpy.context)
            self.assertIsNot(first, third)
            self.assertEqual(compute.call_count, 2)
        self.assertEqual(before, (c.native_rest(rig), rig.get(c.KEY), set(bpy.data.objects.keys())))

    def test_direct_palm_reference_edit_invalidates_without_depsgraph_update(self):
        rig, _ = fixture()
        first = display.status(bpy.context, rig, 'HANDS')
        obj = bpy.data.objects['Palm L']
        counts = len(obj.data.vertices), len(obj.data.polygons)
        obj.data.vertices[0].co.z += .01
        changed = display.status(bpy.context, rig, 'HANDS')
        self.assertIsNot(first, changed)
        self.assertEqual(changed['state'], 'ERROR')
        self.assertEqual(counts, (len(obj.data.vertices), len(obj.data.polygons)))

    def test_direct_basis_edit_invalidates_without_depsgraph_update(self):
        rig, _ = fixture(); obj = bpy.data.objects['Palm L']
        obj.shape_key_add(name='Basis')
        first = display.status(bpy.context, rig, 'HANDS')
        obj.data.shape_keys.reference_key.data[0].co.x += .01
        changed = display.status(bpy.context, rig, 'HANDS')
        self.assertIsNot(first, changed)
        self.assertEqual(changed['state'], 'ERROR')

    def test_live_edit_bone_roll_invalidates_without_mode_commit(self):
        rig, _ = fixture(); s = c.settings(rig); s.palm_reference = 'PLANE'
        limb_ik._mode_set(bpy.context, rig, 'EDIT')
        first = display.status(bpy.context, rig, 'HANDS')
        rig.data.edit_bones['hand.L'].roll += .1
        changed = display.status(bpy.context, rig, 'HANDS')
        self.assertIsNot(first, changed)
        self.assertNotEqual(first['plan']['signature'], changed['plan']['signature'])
        limb_ik._mode_set(bpy.context, rig, 'OBJECT')

    def test_same_count_palm_topology_edit_invalidates_without_graph_update(self):
        rig, _ = fixture(); obj = bpy.data.objects['Palm L']
        first = display.status(bpy.context, rig, 'HANDS')
        a,b = obj.data.loops[:2]
        av,bv = a.vertex_index,b.vertex_index
        a.vertex_index,b.vertex_index = bv,av
        changed = display.status(bpy.context, rig, 'HANDS')
        self.assertIsNot(first, changed)
        self.assertEqual(changed['state'], 'ERROR')

    def test_committed_bone_contract_change_invalidates_without_graph_update(self):
        rig, _ = fixture(); first = display.status(bpy.context, rig, 'HANDS')
        rig.data.bones['hand.L'].use_deform = False
        changed = display.status(bpy.context, rig, 'HANDS')
        self.assertIsNot(first, changed)
        self.assertEqual(changed, c.status(bpy.context, rig, 'HANDS'))
        self.assertNotEqual(first['plan']['signature'], changed['plan']['signature'])

    def test_invalid_legacy_palm_does_not_replace_combined_wrist_candidate(self):
        rig,_=fixture(); s=c.settings(rig); s.part='HANDS'; s.show_directions=True
        first=overlay.segments(bpy.context)
        bpy.data.objects['Palm L'].data.vertices[0].co.z += .01
        plan=c.plan(bpy.context,rig,'HANDS')
        self.assertEqual(len(plan['limbs']),1)
        lines,labels=overlay.segments(bpy.context)
        self.assertEqual(first,(lines,labels))
        self.assertEqual(sum(text=='Forearm' for _,text in labels),2)
        self.assertFalse(c.plan(bpy.context,rig,'ARMS')['errors'])

    def test_hidden_mesh_pick_does_not_change_normal_arms_overlay(self):
        rig,_=fixture(); s=c.settings(rig); s.part='HANDS'; s.show_directions=True
        s.palm_reference='MESH'; s.palm_object=bpy.data.objects['Palm L']
        before=c.native_rest(rig),rig.get(c.KEY)
        self.assertTrue(c.plan(bpy.context,rig,'HANDS')['errors'])
        first=overlay.segments(bpy.context)
        s.palm_flip=True
        changed=overlay.segments(bpy.context)
        self.assertEqual(first,changed)
        self.assertEqual(sum(text=='Forearm' for _,text in changed[1]),2)
        for action in (c.apply,c.confirm):
            with self.assertRaisesRegex(ValueError,'Use for Both Hands'):action(bpy.context,rig,'HANDS')
        self.assertEqual(before,(c.native_rest(rig),rig.get(c.KEY)))

    def test_finger_reference_vertex_and_topology_changes_invalidate(self):
        from finger_tools_fixtures import bound_fixture, targets, bank
        obj,rig,_ = bound_fixture()
        for side in 'LR':
            bank.select(bpy.context,'INDEX',side)
            self.assertFalse(targets.calibrate(bpy.context)['failed'])
        limb_ik._mode_set(bpy.context,rig,'POSE')
        c.preview(bpy.context,rig,'FINGERS')
        c.apply(bpy.context,rig,'FINGERS'); c.confirm(bpy.context,rig,'FINGERS')
        first = display.status(bpy.context,rig,'FINGERS')
        self.assertEqual(first['state'],'CONFIRMED')
        raw=json.loads(obj.character_designer_finger_bank.slots['INDEX.L'].guide.record)
        source=obj.data.shape_keys.reference_key.data if obj.data.shape_keys else obj.data.vertices
        vertex=source[raw['basis']['coordinates'][0][0]]; original=vertex.co.copy()
        vertex.co.z += .01
        changed=display.status(bpy.context,rig,'FINGERS')
        self.assertIsNot(first,changed); self.assertNotEqual(changed['state'],'CONFIRMED')
        vertex.co=original
        restored=display.status(bpy.context,rig,'FINGERS')
        self.assertEqual(restored['state'],'CONFIRMED')
        faces=raw.get('local_evidence',{}).get('FACES',{})
        self.assertTrue(faces)
        face=obj.data.polygons[int(next(iter(faces)))]
        a,b=[obj.data.loops[i] for i in face.loop_indices[:2]]
        av,bv=a.vertex_index,b.vertex_index; a.vertex_index,b.vertex_index=bv,av
        changed=display.status(bpy.context,rig,'FINGERS')
        self.assertIsNot(restored,changed); self.assertNotEqual(changed['state'],'CONFIRMED')

    def test_pose_overlay_moves_even_without_cache_handlers(self):
        rig,_=fixture(); s=c.settings(rig); s.part='ARMS'; s.show_directions=True
        limb_ik._mode_set(bpy.context,rig,'POSE'); bpy.context.view_layer.update()
        display.unregister()
        try:
            first=overlay.segments(bpy.context)
            rig.pose.bones['forearm.L'].rotation_mode='XYZ'
            rig.pose.bones['forearm.L'].rotation_euler.x += .2
            bpy.context.view_layer.update()
            changed=overlay.segments(bpy.context)
            self.assertIsNot(first,changed)
            self.assertNotEqual(first[0],changed[0])
        finally: display.register()

    def test_confirm_cannot_use_stale_display_success(self):
        rig, _ = fixture(); s = c.settings(rig); s.palm_reference = 'PLANE'
        c.preview(bpy.context, rig, 'HANDS'); c.apply(bpy.context, rig, 'HANDS'); c.confirm(bpy.context, rig, 'HANDS')
        old = display.status(bpy.context, rig, 'HANDS')
        self.assertEqual(old['state'], 'CONFIRMED')
        s.palm_tilt += .1
        with patch.object(display, 'status', return_value=old):
            with self.assertRaises(ValueError): c.confirm(bpy.context, rig, 'HANDS')
        self.assertEqual(display.status(bpy.context, rig, 'HANDS')['state'], 'REVIEW')

    def test_operator_redo_does_not_show_false_displacement_warning(self):
        rig,_=fixture(); c.settings(rig).part='ARMS'
        c.preview(bpy.context,rig,'ARMS');c.apply(bpy.context,rig,'ARMS',acknowledge=True)
        self.assertFalse(c.plan(bpy.context,rig,'ARMS')['needs_acknowledgment'])
        for action in ('PREVIEW','APPLY','CONFIRM'):
            layout=Layout()
            ui.CHARACTERDESIGNER_OT_body_calibration.draw(SimpleNamespace(action=action,layout=layout),bpy.context)
            self.assertEqual(layout.labels,[])
            self.assertEqual(layout.props,[])

    def test_only_pending_oversized_apply_draws_acknowledgment(self):
        rig,_=fixture(); c.settings(rig).part='ARMS'
        p=c.plan(bpy.context,rig,'ARMS'); p['needs_acknowledgment']=True; p['errors']=[]
        with patch.object(c,'plan',return_value=p):
            for action in ('PREVIEW','CONFIRM','APPLY'):
                layout=Layout()
                ui.CHARACTERDESIGNER_OT_body_calibration.draw(SimpleNamespace(action=action,layout=layout),bpy.context)
                self.assertEqual('acknowledge' in layout.props,action=='APPLY')
                self.assertEqual('This adjustment needs your approval.' in layout.labels,action=='APPLY')

    def test_details_keep_only_key_axes_angle_and_actionable_error(self):
        rig, _ = fixture(); s = c.settings(rig); s.part='HANDS'; s.palm_reference='PLANE'; s.show_details=True
        panel = Layout(); ui.draw(panel, bpy.context)
        self.assertIn('show_details', panel.props)
        self.assertEqual(sum(text=='Wrist axes follow the forearm' or text.startswith('Hand / forearm:') or
                             text=='Axes: Armature local / Rest' for text in panel.labels),3)
        self.assertFalse(any('Solid:' in text or 'Forearm Twist uses' in text for text in panel.labels))
        s.palm_reference='MESH'; s.palm_object=None
        panel = Layout(); ui.draw(panel, bpy.context)
        self.assertIn('show_details', panel.props)
        self.assertFalse(any('palm' in text.lower() for text in panel.labels), panel.labels)

    def test_handler_lifecycle_is_idempotent_and_clears_cached_results(self):
        rig, _ = fixture(); first = display.status(bpy.context, rig, 'HANDS')
        display.register(); display.register()
        for name in ('depsgraph_update_post', 'undo_post', 'redo_post', 'load_post'):
            matches=[fn for fn in getattr(bpy.app.handlers,name) if fn.__module__==display.__name__ and fn.__name__=='_invalidate']
            self.assertEqual(len(matches),1)
        self.assertIsNot(first, display.status(bpy.context, rig, 'HANDS'))
        display._invalidate(None)
        after = display.status(bpy.context, rig, 'HANDS')
        display.unregister()
        self.assertIsNot(after, display.status(bpy.context, rig, 'HANDS'))
        display.register()


if __name__ == '__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DisplayTests))
    if not result.wasSuccessful(): raise SystemExit(1)
