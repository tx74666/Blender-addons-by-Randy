"""Paired A-pose wrist planes and routing of existing finger tools."""
import math
import sys
import unittest
from pathlib import Path
import bpy
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'addons'),str(ROOT/'tests')]
import character_designer
from character_designer import body_calibration as c,body_calibration_hands as h,body_calibration_overlay as overlay,limb_ik
from character_designer.finger_symmetry import reflect
from test_body_calibration_blender import fixture,mesh_state,prepare


class HandsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):character_designer.register()

    def test_apose_plane_is_paired_right_handed_and_roll_only(self):
        rig,mesh=fixture()
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        for side,sign in (('L',1),('R',-1)):
            b=rig.data.edit_bones['hand.'+side]
            b.tail=b.head+Vector((sign*.12,0,-.10))
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        s=c.settings(rig);s.part='HANDS';s.palm_reference='PLANE';s.palm_tilt=.31;s.show_directions=True
        before=c.native_rest(rig),mesh_state(mesh),len(bpy.data.objects),len(rig.data.bones)
        p=c.preview(bpy.context,rig,'HANDS')
        self.assertFalse(p['errors'],p)
        frames={f['side']:f for f in p['limbs']}
        for axis in ('y','z','palm'):
            self.assertLess((reflect(Vector(frames['L'][axis]))-Vector(frames['R'][axis])).length,1e-6)
        self.assertLess((reflect(Vector(frames['L']['x']))+Vector(frames['R']['x'])).length,1e-6)
        for frame in frames.values():
            x,y,z=[Vector(frame[k]) for k in ('x','y','z')]
            self.assertGreater(x.cross(y).dot(z),.99999)
            self.assertGreater(frame['axis_difference_degrees'],20)
        for mode in ('OBJECT','POSE','EDIT'):
            limb_ik._mode_set(bpy.context,rig,mode)
            lines,labels=overlay.segments(bpy.context)
            self.assertTrue(lines)
        self.assertTrue({'X','Y','Z','Forearm'}.issubset({s for _,s in labels}))
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        c.verify_rest(rig,before[0]);c.apply(bpy.context,rig,'HANDS');c.confirm(bpy.context,rig,'HANDS')
        after=c.native_rest(rig)
        for name,old in before[0].items():
            for key in ('head','tail','parent','use_connect','use_deform'):self.assertEqual(old[key],after[name][key])
            if not name.startswith('hand.'):self.assertEqual(old,after[name])
        self.assertEqual((mesh_state(mesh),len(bpy.data.objects),len(rig.data.bones)),before[1:])
        self.assertFalse(c.plan(bpy.context,rig,'HANDS')['changes'])
        s.palm_tilt+=.05
        self.assertEqual(c.status(bpy.context,rig,'HANDS')['state'],'REVIEW')
        with self.assertRaisesRegex(ValueError,'Preview'):c.apply(bpy.context,rig,'HANDS')

    def test_reference_from_either_hand_drives_both_and_goes_stale(self):
        rig,_=fixture()
        for side in ('L','R'):
            obj=bpy.data.objects['Palm '+side]
            for v in obj.data.vertices:v.co.z+=v.co.y*.4
            old=rig.get(c.KEY);before=c.native_rest(rig)
            refs,normal,center,source=h.picked_reference(bpy.context,rig,obj,0,False)
            self.assertEqual(source,side);self.assertEqual(old,rig.get(c.KEY))
            h.capture_pair(bpy.context,rig,obj,0,False,True)
            left,right=c.palm(rig,'L')[0],c.palm(rig,'R')[0]
            self.assertLess((reflect(left)-right).length,1e-6)
            self.assertFalse(c.plan(bpy.context,rig,'HANDS')['errors'])
            c.verify_rest(rig,before)
            point=obj.data.vertices[0].co.copy();obj.data.vertices[0].co.z+=.001
            for which in ('L','R'):
                with self.assertRaisesRegex(ValueError,'changed'):c.palm(rig,which)
            obj.data.vertices[0].co=point

    def test_missing_pair_refuses_before_saving_and_old_reference_survives(self):
        rig,_=fixture();obj=bpy.data.objects['Palm L'];before=rig.get(c.KEY)
        self.assertEqual(h.mode(rig),'MESH')
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        rig.data.edit_bones.remove(rig.data.edit_bones['hand.R'])
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        with self.assertRaises(ValueError):h.capture_pair(bpy.context,rig,obj,0,False,True)
        self.assertEqual(before,rig.get(c.KEY))
        c.settings(rig).palm_reference='PLANE'
        self.assertTrue(c.plan(bpy.context,rig,'HANDS')['errors'])

    def test_pending_mesh_reference_cannot_apply_saved_candidate(self):
        rig,_=fixture();s=c.settings(rig);s.part='HANDS';s.show_directions=True
        left,right=[bpy.data.objects['Palm '+side] for side in ('L','R')]
        h.capture_pair(bpy.context,rig,left,0,False,True)
        c.preview(bpy.context,rig,'HANDS');c.apply(bpy.context,rig,'HANDS');c.confirm(bpy.context,rig,'HANDS')
        s.palm_object=right;s.palm_flip=True
        before=c.native_rest(rig)
        p=c.preview(bpy.context,rig,'HANDS')
        self.assertTrue(p['errors']);self.assertFalse(p['limbs']);self.assertFalse(p['skipped'])
        for action in (c.apply,c.confirm):
            with self.assertRaisesRegex(ValueError,'Use for Both Hands'):action(bpy.context,rig,'HANDS')
        self.assertFalse(any(text=='Palm plane' for _,text in overlay.segments(bpy.context)[1]))
        c.verify_rest(rig,before)
        h.capture_pair(bpy.context,rig,right,0,True,True)
        self.assertFalse(h.pending_reference(bpy.context,rig))
        self.assertFalse(c.plan(bpy.context,rig,'HANDS')['errors'])
        s.palm_face=100
        self.assertTrue(c.plan(bpy.context,rig,'HANDS')['errors'])

    def test_legacy_asymmetric_hands_keep_independent_references(self):
        rig,_=fixture()
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        rig.data.edit_bones['hand.R'].tail.z+=.08
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        before=c.record(rig)['palms']
        self.assertFalse(h.linked(rig))
        self.assertFalse(c.preview(bpy.context,rig,'HANDS')['errors'])
        c.apply(bpy.context,rig,'HANDS');c.confirm(bpy.context,rig,'HANDS')
        self.assertEqual(c.status(bpy.context,rig,'HANDS')['state'],'CONFIRMED')
        self.assertEqual(before,c.record(rig)['palms'])
        c.settings(rig).palm_reference='PLANE'
        self.assertTrue(c.plan(bpy.context,rig,'HANDS')['errors'])

    def test_switching_legacy_mesh_mode_does_not_claim_linked_proof(self):
        rig,_=fixture();s=c.settings(rig);s.palm_object=None
        s.part='HANDS';saved=c.record(rig)['palms']
        self.assertFalse(h.linked(rig))
        s.palm_reference='MESH'
        self.assertFalse(h.linked(rig))
        self.assertTrue(c.preview(bpy.context,rig,'HANDS')['errors'])
        for action in (c.apply,c.confirm):
            with self.assertRaisesRegex(ValueError,'Use for Both Hands'):action(bpy.context,rig,'HANDS')
        self.assertEqual(bpy.ops.character_designer.body_calibration(action='SAVED_REF'),{'FINISHED'})
        self.assertEqual(s.palm_reference,'AUTO');self.assertEqual(saved,c.record(rig)['palms'])
        self.assertFalse(c.plan(bpy.context,rig,'HANDS')['errors'])
        h.capture_pair(bpy.context,rig,bpy.data.objects['Palm R'],0,False,True)
        self.assertTrue(h.linked(rig));self.assertFalse(h.pending_reference(bpy.context,rig))
        self.assertFalse(c.plan(bpy.context,rig,'HANDS')['errors'])

    def test_existing_finger_panel_follows_active_rig_and_mesh_edit(self):
        from character_designer import finger_bones
        rig,mesh=fixture();s=c.settings(rig)
        wm=bpy.context.window_manager.character_designer;wm.ui_page='RIG';wm.rig_section='BODY'
        for tab in ('SETUP','CONTROLS'):
            for part in (*c.PARTS,'FINGERS'):
                s.tab=tab;s.part=part
                self.assertEqual(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context),part=='FINGERS')
        s.tab='SETUP';s.part='FINGERS'
        rig.select_set(False);mesh.select_set(True);bpy.context.view_layer.objects.active=mesh
        bpy.ops.object.mode_set(mode='EDIT')
        self.assertTrue(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context))
        s.part='HANDS';self.assertFalse(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context))
        bpy.ops.object.mode_set(mode='OBJECT')
        other=bpy.data.objects.new('Other rig',bpy.data.armatures.new('Other rig'))
        bpy.context.collection.objects.link(other);mesh.modifiers[0].object=other
        self.assertFalse(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context))
        c.settings(other).part='FINGERS'
        self.assertTrue(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context))
        mesh.modifiers.new('Second rig','ARMATURE').object=rig
        self.assertFalse(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context))
        mesh.modifiers.clear()
        self.assertTrue(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context))
        wm.rig_section='HAIR';self.assertFalse(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context))
        wm.ui_page='WEIGHT';self.assertFalse(finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context))

    def test_twist_entry_uses_configured_body_and_retains_active_session(self):
        import test_forearm_twist_blender as f
        from character_designer import forearm_twist as twist,character_setup
        data=f.make_fixture('DIRECT_PREROLL',build_ik=False)
        rig,mesh=data['armature'],data['mesh']
        clothes=bpy.data.objects.new('Clothes',bpy.data.meshes.new('Clothes'))
        bpy.context.collection.objects.link(clothes);clothes.modifiers.new('Armature','ARMATURE').object=rig
        setup=character_setup.settings(bpy.context);setup.body=mesh
        limb_ik._mode_set(bpy.context,rig,'POSE')
        before=f.structure_snapshot(rig,mesh),f.key_snapshot(mesh),f.pose_snapshot(rig)
        self.assertEqual(twist.context_mesh(bpy.context),mesh)
        bpy.context.window_manager.character_designer_forearm_twist.symmetry=False
        twist.start_paired_test(bpy.context)
        # Selecting the rig while testing a different explicit mesh must not cancel the session.
        setup.body=clothes
        limb_ik._mode_set(bpy.context,rig,'POSE')
        self.assertEqual(twist.context_mesh(bpy.context),mesh)
        twist.finish_test(bpy.context,False)
        self.assertEqual(bpy.context.object,rig);self.assertEqual(bpy.context.mode,'POSE')
        self.assertEqual(before[0],f.structure_snapshot(rig,mesh));self.assertEqual(before[1],f.key_snapshot(mesh))
        f.assert_pose_snapshot(rig,before[2],'Twist entry cancel')
        setup.body=None
        with self.assertRaisesRegex(twist.ForearmTwistError,'Select the body'):twist.context_mesh(bpy.context)
        other=bpy.data.objects.new('Other rig',bpy.data.armatures.new('Other rig'))
        bpy.context.collection.objects.link(other);clothes.modifiers[0].object=other;setup.body=clothes
        self.assertEqual(twist.context_mesh(bpy.context),mesh)
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        with self.assertRaisesRegex(twist.ForearmTwistError,'Finish editing'):twist.start_paired_test(bpy.context)
        limb_ik._mode_set(bpy.context,rig,'OBJECT')

    def test_forearm_profile_survives_hand_roll_calibration(self):
        import test_forearm_twist_blender as f
        from character_designer import forearm_twist as twist
        data=f.make_fixture('DIRECT_PREROLL',build_ik=False)
        rig,mesh=data['armature'],data['mesh']
        twist.start_test(bpy.context,mesh,side='L');twist.set_ratio(bpy.context,3,.37);twist.finish_test(bpy.context,True)
        records=twist._records(mesh);profile=[(r['vertices'],r['ratio']) for r in records['L']['rings']]
        keys=f.key_snapshot(mesh);weights=tuple(tuple((g.group,g.weight) for g in v.groups) for v in mesh.data.vertices)
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        s=c.settings(rig);s.palm_reference='PLANE';s.palm_tilt=.2
        c.preview(bpy.context,rig,'HANDS');c.apply(bpy.context,rig,'HANDS')
        refreshed=twist._prepare_runtime_records(mesh)
        self.assertEqual(profile,[(r['vertices'],r['ratio']) for r in refreshed['L']['rings']])
        self.assertEqual(keys,f.key_snapshot(mesh))
        self.assertEqual(weights,tuple(tuple((g.group,g.weight) for g in v.groups) for v in mesh.data.vertices))
        self.assertFalse(limb_ik._validate_inventory(rig)['rigs'])
        twist.remove_calibration(bpy.context,mesh,'L')

    def test_legacy_plane_service_remains_available_without_old_overlay_ui(self):
        rig,_=fixture();s=c.settings(rig);s.palm_reference='PLANE';s.part='HANDS';s.show_directions=True
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        for side,sign in (('L',1),('R',-1)):
            bone=rig.data.edit_bones['hand.'+side]
            bone.tail=bone.head+Vector((sign*.12,0,-.10));bone.roll=.83*sign
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        before=c.native_rest(rig)
        frames=c.preview(bpy.context,rig,'HANDS')['limbs']
        for frame in frames:
            self.assertLess(abs(abs(Vector(frame['x']).y)-1),1e-6)
            self.assertAlmostEqual(Vector(frame['y']).y,0,places=6)
            self.assertAlmostEqual(Vector(frame['z']).y,0,places=6)
        lines,labels=overlay.segments(bpy.context)
        self.assertEqual(sum(text=='Forearm' for _,text in labels),2)
        self.assertFalse(any('plane' in text.lower() or 'current' in text.lower() for _,text in labels))
        # Saved HANDS selection now previews the combined Arms candidate.
        for frame in c.plan(bpy.context,rig,'ARMS')['wrists']:
            pivot=rig.matrix_world@Vector(frame['wrist'])
            size=(Vector(before[frame['chain'][-1]]['tail'])-Vector(frame['wrist'])).length
            expected=pivot+rig.matrix_world.to_3x3()@(Vector(frame['y'])*math.cos(.72)+Vector(frame['z'])*math.sin(.72))*size*.5
            self.assertTrue(any((point-expected).length<1e-6 for a,b,_ in lines for point in (a,b)))
            for axis in ('x','y','z'):
                tip=rig.matrix_world@(Vector(frame['wrist'])+Vector(frame[axis])*size*.7)
                self.assertTrue(any(text==axis.upper() and (point-tip).length<1e-6 for point,text in labels))
        c.verify_rest(rig,before)
        c.apply(bpy.context,rig,'HANDS')
        for frame in frames:
            name=frame['chain'][-1];bone=rig.data.bones[name]
            self.assertGreater(abs(bone.matrix_local.to_3x3().col[0].y),.99999)
            self.assertEqual(before[name]['head'],c.rest(rig,name)['head'])
            self.assertEqual(before[name]['tail'],c.rest(rig,name)['tail'])
        s.part='ARMS';s.show_details=False
        self.assertFalse(any('wrist' in text.lower() for _,text in overlay.segments(bpy.context)[1]))
        s.show_details=True
        self.assertFalse(any(text=='Current wrist (Hands)' for _,text in overlay.segments(bpy.context)[1]))


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(HandsTests))
    if not result.wasSuccessful():raise SystemExit(1)
