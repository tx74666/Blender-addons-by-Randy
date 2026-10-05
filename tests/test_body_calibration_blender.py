"""Disposable-process integration tests for preview -> Apply -> Confirm -> Direct."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Matrix, Vector
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'addons'),str(ROOT/'tests')]
import character_designer
from character_designer import body_calibration as c, body_setup, limb_ik, body_calibration_overlay as overlay
import test_limb_ik_blender as base


def fixture():
    base.reset_scene()
    rig=base.make_humanoid(roll_offset=.31)
    data=bpy.data.meshes.new('Calibration skin')
    data.from_pydata([(.4,0,1.5),(.6,.12,1.46),(.9,0,1.42),(0,0,1.)],[],[])
    mesh=bpy.data.objects.new('Calibration skin',data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.modifiers.new('Armature','ARMATURE').object=rig
    for name,index in [('upper_arm.L',0),('forearm.L',1),('hand.L',2),('Hips',3)]:
        mesh.vertex_groups.new(name=name).add([index],1.,'REPLACE')
    mesh.shape_key_add(name='Basis')
    mesh.shape_key_add(name='Smile').data[0].co.z+=.01
    palms(rig)
    return rig,mesh


def palms(rig):
    for side in 'LR':
        palm=bpy.data.meshes.new('Palm '+side)
        center=rig.data.bones['hand.'+side].head_local
        palm.from_pydata([center+Vector(v) for v in ((-.02,-.02,0),(0,.02,0),(.02,-.02,0))],[],[(0,1,2)])
        obj=bpy.data.objects.new('Palm '+side,palm)
        bpy.context.scene.collection.objects.link(obj)
        obj.matrix_world=rig.matrix_world
        bpy.context.view_layer.update()
        c.capture_palm(rig,side,obj,0,False,True)


def prepare(rig):
    for part in c.PARTS:
        p=c.preview(bpy.context,rig,part)
        if p['skipped']:continue
        c.apply(bpy.context,rig,part,acknowledge=True)
        c.confirm(bpy.context,rig,part)


def mesh_state(mesh):
    return (tuple(tuple(v.co) for v in mesh.data.vertices),
            tuple(tuple((g.group,g.weight) for g in v.groups) for v in mesh.data.vertices),
            tuple((k.name,tuple(tuple(p.co) for p in k.data)) for k in mesh.data.shape_keys.key_blocks))


class CalibrationBlender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):character_designer.register()

    def test_parented_shallow_wrist_roll_generate_refines_controls_only(self):
        from character_designer import limb_ik_fk
        source=json.loads((ROOT/'tests/fixtures/body_calibration_shallow_parented.json').read_text())
        base.reset_scene()
        data=bpy.data.armatures.new('Shallow parented')
        rig=bpy.data.objects.new('Shallow parented',data);bpy.context.collection.objects.link(rig)
        rig.matrix_world=Matrix(source['world']);rig.select_set(True);bpy.context.view_layer.objects.active=rig
        bpy.ops.object.mode_set(mode='EDIT')
        for name,state in source['bones'].items():
            bone=data.edit_bones.new(name);bone.head=state['head'];bone.tail=state['tail'];bone.align_roll(Vector(state['z']))
        for name,state in source['bones'].items():
            bone=data.edit_bones[name];bone.parent=data.edit_bones.get(state['parent']);bone.use_connect=state['use_connect']
        bpy.ops.object.mode_set(mode='OBJECT');bpy.context.view_layer.update()
        s=c.settings(rig);s.palm_reference='PLANE';s.include_fingers=False;s.include_legs=False
        prepare(rig)
        before=c.native_rest(rig);basis={p.name:p.matrix_basis.copy() for p in rig.pose.bones}
        pose={p.name:p.matrix.copy() for p in rig.pose.bones}
        actual=limb_ik_fk._refine_calibrated_reach
        original_match=limb_ik_fk._match_ik
        calls=[];offsets=[]
        def trace(*args,**kwargs):calls.append(args[3]['chain']);return actual(*args,**kwargs)
        def trace_match(*args,**kwargs):
            if kwargs.get('reach_offset') is not None:offsets.append(kwargs['reach_offset'].length)
            return original_match(*args,**kwargs)
        with patch.object(limb_ik_fk,'_refine_calibrated_reach',side_effect=trace),patch.object(limb_ik_fk,'_match_ik',side_effect=trace_match):
            body_setup.generate(bpy.context,rig)
        self.assertTrue(calls)
        self.assertTrue(any(offset>0 for offset in offsets))
        self.assertLess(max(offsets),3e-6)
        self.assertEqual(basis,{n:rig.pose.bones[n].matrix_basis for n in basis})
        c.verify_rest(rig,before)
        self.assertLess(limb_ik_fk._pose_errors(rig,pose)[0],2e-5)
        body_setup.generate(bpy.context,rig);body_setup.remove(bpy.context,rig);c.verify_rest(rig,before)
        self.assertEqual(set(rig.data.bones.keys()),set(before))
        self.assertEqual(basis,{n:rig.pose.bones[n].matrix_basis for n in basis})
        body_setup.generate(bpy.context,rig);body_setup.remove(bpy.context,rig)
        c.verify_rest(rig,before)
        for name,matrix in basis.items():rig.pose.bones[name].matrix_basis=matrix
        rig.update_tag();bpy.context.view_layer.update()
        checkpoint=c.native_rest(rig),rig.get(c.KEY),set(bpy.data.objects.keys())
        def fail_reach(*args,**kwargs):
            offset=kwargs.get('reach_offset')
            if offset is not None and offset.length>0:raise ValueError('injected reach failure')
            return original_match(*args,**kwargs)
        with patch.object(limb_ik_fk,'_match_ik',side_effect=fail_reach):
            with self.assertRaisesRegex(ValueError,'injected reach failure'):body_setup.generate(bpy.context,rig)
        self.assertEqual(checkpoint,(c.native_rest(rig),rig.get(c.KEY),set(bpy.data.objects.keys())))
        self.assertEqual(basis,{n:rig.pose.bones[n].matrix_basis for n in basis})
        self.assertFalse(limb_ik._validate_inventory(rig)['rigs'])
        self.assertFalse(rig.animation_data and rig.animation_data.drivers)

    def test_legacy_switching_never_uses_calibration_reach_search(self):
        import test_limb_ik_fk_blender as legacy
        from character_designer import limb_ik_fk
        with patch.object(limb_ik_fk,'_refine_calibrated_reach',side_effect=AssertionError('Legacy entered calibration refinement')):
            for method in ('ROLL_DECOUPLED','DIRECT_PREROLL'):
                rig,key,_=legacy.build(method,'LEFT_ARM')
                limb_ik_fk.switch_limb(bpy.context,rig,key,'FK')
                limb_ik_fk.switch_limb(bpy.context,rig,key,'IK')

    def test_shallow_native_precision_apply_generate_and_visuals(self):
        from character_designer import limb_fk_visuals,limb_ik_fk
        rig,mesh=fixture()
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        for side,sign in (('L',1),('R',-1)):
            s,e,w=[Vector((x*sign,y,z)) for x,y,z in (
                (.11077477782964706,.06768570840358734,1.6531343460083008),
                (.3262162506580353,.06925076246261597,1.4328820705413818),
                (.5040974020957947,.06748819351196289,1.2489454746246338))]
            upper,lower,hand=[rig.data.edit_bones[n+'.'+side] for n in ('upper_arm','forearm','hand')]
            end=hand.tail-hand.head
            for b in (upper,lower,hand):b.use_connect=False
            upper.parent.tail=s;upper.head=s;upper.tail=e;lower.head=e;lower.tail=w;hand.head=w;hand.tail=w+end
            for b in (upper,lower,hand):b.use_connect=True
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        count=len(rig.data.bones);authored=mesh_state(mesh)
        p=c.preview(bpy.context,rig,'ARMS')
        self.assertTrue(p['changes'])
        c.apply(bpy.context,rig,'ARMS')
        self.assertEqual(len(rig.data.bones),count)
        self.assertFalse(c.plan(bpy.context,rig,'ARMS')['changes'])
        applied=c.native_rest(rig)
        for name,expected in p['changes'].items():c.verify_rest(rig,{name:expected})
        c.apply(bpy.context,rig,'ARMS')
        self.assertEqual(applied,c.native_rest(rig))
        prepare(rig)
        rest=c.native_rest(rig)
        def evaluated():
            bpy.context.view_layer.update()
            return [v.co.copy() for v in mesh.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices]
        initial=evaluated();maximum=0.
        with patch.object(limb_ik,'_apply_direct_preroll',side_effect=AssertionError('Generate attempted Rest calibration')), \
             patch.object(limb_ik_fk,'_pole_position',side_effect=AssertionError('Generate repositioned the planned Pole')):
            for _ in range(2):
                bases={p.name:p.matrix_basis.copy() for p in rig.pose.bones}
                body_setup.generate(bpy.context,rig)
                body_setup.generate(bpy.context,rig)
                self.assertEqual(bases,{n:rig.pose.bones[n].matrix_basis for n in bases})
                bones=set(rig.data.bones.keys())
                for action in (limb_fk_visuals.build,limb_fk_visuals.fit_ik_sizes,limb_fk_visuals.restore_ik_sizes,limb_fk_visuals.remove):
                    action(bpy.context,rig)
                    self.assertEqual(set(rig.data.bones.keys()),bones)
                    c.verify_rest(rig,rest)
                maximum=max(maximum,max((a-b).length for a,b in zip(initial,evaluated())))
                body_setup.remove(bpy.context,rig)
                c.verify_rest(rig,rest)
                self.assertEqual(len(rig.data.bones),count)
                maximum=max(maximum,max((a-b).length for a,b in zip(initial,evaluated())))
        self.assertLess(maximum,2e-6)
        self.assertEqual(authored,mesh_state(mesh))
        print('SHALLOW_GENERATE_VISUAL_REMOVE_MAX_VERTEX_DELTA',maximum)

    def test_calibrated_matching_failure_removes_new_drivers(self):
        from character_designer import limb_ik_fk
        rig,mesh=fixture();prepare(rig)
        before=c.native_rest(rig),mesh_state(mesh),set(bpy.data.objects.keys()),rig.get(c.KEY)
        identities=[obj.as_pointer() for obj in (rig,rig.data,mesh,mesh.data,mesh.data.shape_keys)]
        poses={p.name:p.matrix_basis.copy() for p in rig.pose.bones}
        original=limb_ik_fk.switch_limb
        def fail_after_matching(*args,**kwargs):
            original(*args,**kwargs)
            self.assertTrue(rig.animation_data and rig.animation_data.drivers)
            raise ValueError('Injected failure after calibrated control matching')
        with patch.object(limb_ik_fk,'switch_limb',side_effect=fail_after_matching):
            with self.assertRaisesRegex(ValueError,'Injected failure'):
                body_setup.generate(bpy.context,rig)
        c.verify_rest(rig,before[0])
        self.assertEqual((mesh_state(mesh),set(bpy.data.objects.keys()),rig.get(c.KEY)),before[1:])
        self.assertEqual(set(before[0]),set(rig.data.bones.keys()))
        self.assertEqual(identities,[obj.as_pointer() for obj in (rig,rig.data,mesh,mesh.data,mesh.data.shape_keys)])
        self.assertEqual(poses,{p.name:p.matrix_basis for p in rig.pose.bones})
        self.assertFalse(rig.animation_data and rig.animation_data.drivers)
        self.assertFalse(body_setup.has_generated(rig))

    def test_read_only_preview_and_overlay(self):
        rig,mesh=fixture()
        before=c.native_rest(rig),mesh_state(mesh),len(bpy.data.objects),len(rig.data.bones)
        s=c.settings(rig);s.show_directions=True
        for mode in ('OBJECT','POSE','EDIT'):
            limb_ik._mode_set(bpy.context,rig,mode)
            for preset in ('DEFAULT','CUSTOM'):
                s.preset=preset
                c.preview(bpy.context,rig,'ARMS')
                lines,labels=overlay.segments(bpy.context)
                self.assertTrue(lines and labels)
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        c.verify_rest(rig,before[0])
        self.assertEqual((mesh_state(mesh),len(bpy.data.objects),len(rig.data.bones)),before[1:])

    def test_preview_button_opens_visuals_without_rest_write(self):
        rig,mesh=fixture();s=c.settings(rig);s.part='ARMS';s.show_directions=False
        before=c.native_rest(rig),mesh_state(mesh)
        self.assertEqual(bpy.ops.character_designer.body_calibration(action='PREVIEW'),{'FINISHED'})
        self.assertTrue(s.show_directions)
        self.assertTrue(overlay.segments(bpy.context)[0])
        c.verify_rest(rig,before[0]);self.assertEqual(mesh_state(mesh),before[1])
        self.assertEqual(s.last_error,'')

    def test_readiness_locates_local_pose_without_clearing_it(self):
        rig,mesh=fixture()
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        parent=rig.data.edit_bones['hand.L']
        for name in ('f_index.01.L','f_index.02.L'):
            bone=rig.data.edit_bones.new(name);bone.head=parent.tail;bone.tail=bone.head+Vector((.06,0,0));bone.parent=parent;parent=bone
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        rig.pose.bones['f_index.01.L'].location.x=.0041
        bpy.context.view_layer.update()
        p=c.preview(bpy.context,rig,'ARMS');before=c.native_rest(rig)
        poses={b.name:b.matrix_basis.copy() for b in rig.pose.bones}
        issue=c.apply_readiness(bpy.context,rig,p['changes'])
        self.assertEqual(issue['code'],'POSE');self.assertEqual(issue['bones'],['f_index.01.L'])
        with self.assertRaises(c.CalibrationBlocked):c.apply(bpy.context,rig,'ARMS')
        rig.data.bones['f_index.01.L'].hide_select=True
        with self.assertRaisesRegex(ValueError,'show/unlock'):c.select_pose_blockers(bpy.context,rig,'ARMS')
        self.assertEqual(rig.mode,'OBJECT')
        rig.data.bones['f_index.01.L'].hide_select=False
        self.assertEqual(c.select_pose_blockers(bpy.context,rig,'ARMS'),['f_index.01.L'])
        self.assertEqual(rig.mode,'POSE')
        self.assertEqual([b.name for b in rig.pose.bones if b.select],['f_index.01.L'])
        self.assertEqual(poses,{b.name:b.matrix_basis for b in rig.pose.bones});c.verify_rest(rig,before)
        constraint=rig.pose.bones['f_index.01.L'].constraints.new('LIMIT_ROTATION')
        self.assertEqual(c.apply_readiness(bpy.context,rig,p['changes'])['code'],'CONSTRAINT')
        rig.pose.bones['f_index.01.L'].constraints.remove(constraint)
        rig.pose.bones['f_index.01.L'].keyframe_insert('location',frame=1)
        self.assertEqual(c.apply_readiness(bpy.context,rig,p['changes'])['code'],'ANIMATION')
        self.assertIsNone(c.apply_readiness(bpy.context,rig,{}))

    def test_compact_panel_details_and_uncommitted_edit_preflight(self):
        from character_designer import body_calibration_ui as ui
        from types import SimpleNamespace
        rig,_=fixture();s=c.settings(rig)
        class Layout:
            def __init__(self,labels):self.labels=labels
            def row(self,**kw):return self
            split=row
            def column(self,**kw):return self
            def box(self):return self
            def label(self,**kw):self.labels.append(kw.get('text',''))
            def prop(self,*a,**kw):pass
            def prop_enum(self,*a,**kw):pass
            def operator(self,*a,**kw):return SimpleNamespace()
        compact=[];ui.draw(Layout(compact),bpy.context)
        self.assertFalse(any('direction' in x or 'Undo' in x or 'Length change' in x for x in compact))
        s.show_details=True;details=[];ui.draw(Layout(details),bpy.context)
        self.assertTrue(any('direction' in x for x in details))
        before=c.plan(bpy.context,rig,'ARMS')['signature']
        s.show_details=False
        self.assertEqual(before,c.plan(bpy.context,rig,'ARMS')['signature'])
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        rig.data.edit_bones['hand.L'].name='hand_new.L'
        self.assertIsNone(c.apply_readiness(bpy.context,rig,{'hand_new.L':{}}))
        with self.assertRaisesRegex(ValueError,'Leave Edit'):c.select_pose_blockers(bpy.context,rig,'ARMS')

    def test_apply_confirm_generate_update_remove_rebuild(self):
        rig,mesh=fixture(); before=mesh_state(mesh)
        prepare(rig)
        rest=c.native_rest(rig)
        with patch.object(limb_ik,'_apply_direct_preroll',side_effect=AssertionError('Rest writer called')):
            for _ in range(2):
                body_setup.generate(bpy.context,rig)
                c.verify_rest(rig,rest)
                inv=limb_ik._validate_inventory(rig)
                self.assertEqual(inv['schema'],limb_ik.DIRECT_PREROLL_SCHEMA)
                self.assertFalse(any(b.name.startswith(('MCH_upper_arm','MCH_forearm','ORI_upper_arm','ORI_forearm')) for b in rig.data.bones))
                print('GENERATED_BONES',[(b.name,b.get(limb_ik.ROLE_KEY),b.use_deform) for b in rig.data.bones if b.get(limb_ik.OWNER_KEY)])
                body_setup.generate(bpy.context,rig)
                body_setup.remove(bpy.context,rig)
                c.verify_rest(rig,rest)
        self.assertEqual(mesh_state(mesh),before)

    def test_generate_requires_confirmation(self):
        rig,_=fixture()
        before=c.native_rest(rig)
        with self.assertRaisesRegex(ValueError,'Arms|ARMS'):body_setup.generate(bpy.context,rig)
        c.verify_rest(rig,before)

    def test_calibrated_remove_still_bakes_actual_ik_motion(self):
        from character_designer import body_setup_removal,limb_ik_fk
        rig,mesh=fixture();prepare(rig);rest=c.native_rest(rig)
        authored=mesh_state(mesh)
        basis={p.name:p.matrix_basis.copy() for p in rig.pose.bones}
        body_setup.generate(bpy.context,rig)
        target=rig.pose.bones[limb_ik._validate_inventory(rig)['rigs'][('ARM','L')]['target'].name]
        target.location+=Vector((.03,-.04,.025));limb_ik_fk._update(bpy.context,rig)
        surfaces=body_setup_removal._bound_surfaces(bpy.context,rig)
        body_setup.remove(bpy.context,rig)
        self.assertLess(body_setup_removal._check_surfaces(bpy.context,rig,surfaces),1e-4)
        self.assertNotEqual(basis['forearm.L'],rig.pose.bones['forearm.L'].matrix_basis)
        c.verify_rest(rig,rest);self.assertEqual(authored,mesh_state(mesh))

    def test_whole_body_components_keep_existing_contracts(self):
        import test_body_setup_plan_blender as planning
        rig,mesh=planning.fixture()
        rig.pose.bones['Hips'].matrix_basis=Matrix.Identity(4)
        bpy.context.view_layer.update()
        palms(rig);prepare(rig)
        before=c.native_rest(rig)
        result=body_setup.generate(bpy.context,rig)
        self.assertEqual(set(result['created']),set(planning.planner.COMPONENT_KEYS)-{'SPINE'})
        c.verify_rest(rig,before)
        body_setup.generate(bpy.context,rig)
        body_setup.remove(bpy.context,rig)
        c.verify_rest(rig,before)

    def test_transformed_and_mirrored_objects(self):
        from mathutils import Euler
        for scale in ((.7,.7,.7),(-1,1,1),(1,2,.5)):
            rig,_=fixture()
            rig.matrix_world=Matrix.Translation((2,3,4)) @ Euler((.2,-.3,.5)).to_matrix().to_4x4() @ Matrix.Diagonal((*scale,1))
            for side in 'LR':bpy.data.objects['Palm '+side].matrix_world=rig.matrix_world
            bpy.context.view_layer.update()
            for side in 'LR':c.capture_palm(rig,side,bpy.data.objects['Palm '+side],0,False,True)
            transform=rig.matrix_world.copy()
            prepare(rig);rest=c.native_rest(rig)
            try:body_setup.generate(bpy.context,rig)
            except ValueError as exc:
                self.assertTrue(any(word in str(exc).lower() for word in ('scale','transform','calibrat','deformation','preserv')),str(exc))
                print('EXPLICIT_SCALE_REFUSAL',scale,str(exc))
            c.verify_rest(rig,rest)
            self.assertEqual(rig.matrix_world,transform)

    def test_advanced_builder_cannot_replane_after_apply(self):
        rig,_=fixture();prepare(rig)
        self.assertTrue(c.calibrated(rig))
        before=c.native_rest(rig)
        chain=c.chains(bpy.context,rig,'ARM')[0]
        p=limb_ik._build_plan(rig,chain,.75,Vector((0,0,-1)))
        with patch.object(limb_ik,'_apply_direct_preroll',side_effect=AssertionError('Old Rest writer')):
            with self.assertRaisesRegex(ValueError,'confirmed bend plane'):
                limb_ik._build_plans(bpy.context,rig,[p],schema=limb_ik.DIRECT_PREROLL_SCHEMA)
        c.verify_rest(rig,before)

    def test_existing_controls_cannot_skip_dependencies(self):
        rig,_=fixture();prepare(rig);body_setup.generate(bpy.context,rig)
        s=c.settings(rig);s.include_arms=False;s.preset='CUSTOM';s.arm_axis='Z'
        self.assertNotEqual(c.status(bpy.context,rig,'ARMS')['state'],'SKIPPED')
        with self.assertRaises(ValueError):body_setup.generate(bpy.context,rig)

    def test_excluded_arm_mapping_does_not_block_legs(self):
        rig,_=fixture()
        s=c.settings(rig);s.include_arms=False
        ui=limb_ik._settings(bpy.context);ui.armature=rig
        for role,name in zip(limb_ik.ROLES,('upper_arm.L','Missing Forearm','hand.L')):
            setattr(ui,limb_ik._field_name('ARM','L',role),name)
        prepare(rig)
        result=body_setup.generate(bpy.context,rig)
        self.assertIn('LIMBS',result['created'])
        self.assertEqual(set(limb_ik._validate_inventory(rig)['rigs']),{('LEG','L'),('LEG','R')})

    def test_edit_overlay_keeps_actual_controls(self):
        rig,_=fixture();prepare(rig);body_setup.generate(bpy.context,rig)
        c.settings(rig).show_directions=True
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        lines,labels=overlay.segments(bpy.context)
        words=[s for p,s in labels]
        self.assertIn('Pole [Edit Rest]',words)
        self.assertIn('Wrist',words)
        self.assertFalse(any('Not generated' in s for s in words))

    def test_mapping_roles_are_signed(self):
        rig,_=fixture();prepare(rig)
        previous=c.plan(bpy.context,rig,'ARMS')['signature']
        chains=c.chains(bpy.context,rig,'ARM')
        swapped=[limb_ik.LimbChain('ARM','R' if x.side=='L' else 'L',*x.names) for x in chains]
        with patch.object(c,'chains',return_value=swapped):
            self.assertNotEqual(c.plan(bpy.context,rig,'ARMS')['signature'],previous)

    def test_reapply_after_old_confirmation_is_ready_until_reconfirmed(self):
        rig,mesh=fixture(); prepare(rig)
        old=c.record(rig)['confirmed']['ARMS']
        before=c.native_rest(rig),mesh_state(mesh)
        # A new pole distance changes the proof, without moving the native rig.
        c.settings(rig).pole_distance += .1
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'REVIEW')
        c.preview(bpy.context,rig,'ARMS')
        c.apply(bpy.context,rig,'ARMS')
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'READY')
        self.assertEqual(c.record(rig)['confirmed']['ARMS'],old)
        with self.assertRaisesRegex(ValueError,'Arms:'):
            c.require_confirmed(bpy.context,rig)
        c.confirm(bpy.context,rig,'ARMS')
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'CONFIRMED')
        self.assertNotEqual(c.record(rig)['confirmed']['ARMS'],old)
        # Pole distance is shared by both parts; Legs must still be reviewed.
        self.assertEqual(c.status(bpy.context,rig,'LEGS')['state'],'REVIEW')
        c.preview(bpy.context,rig,'LEGS');c.apply(bpy.context,rig,'LEGS')
        self.assertEqual(c.status(bpy.context,rig,'LEGS')['state'],'READY')
        c.confirm(bpy.context,rig,'LEGS')
        c.require_confirmed(bpy.context,rig)
        self.assertEqual(before,(c.native_rest(rig),mesh_state(mesh)))
        c.settings(rig).pole_distance += .1
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'REVIEW')

    def test_stale_preview_and_failed_apply_roll_back(self):
        rig,_=fixture();c.preview(bpy.context,rig,'ARMS')
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        rig.data.edit_bones['upper_arm.L'].roll+=.1
        limb_ik._mode_set(bpy.context,rig,'POSE')
        before=c.native_rest(rig)
        with self.assertRaisesRegex(ValueError,'stale'):c.apply(bpy.context,rig,'ARMS')
        c.preview(bpy.context,rig,'ARMS')
        original=body_setup.body_setup_transaction.assert_original_ids
        calls=[]
        def fail_once(checkpoint):
            calls.append(True)
            if len(calls)==1:raise ValueError('injected')
            return original(checkpoint)
        with patch.object(body_setup.body_setup_transaction,'assert_original_ids',side_effect=fail_once):
            with self.assertRaisesRegex(ValueError,'injected'):c.apply(bpy.context,rig,'ARMS')
        c.verify_rest(rig,before)

    def test_edit_mode_and_repeat_no_drift(self):
        rig,_=fixture();limb_ik._mode_set(bpy.context,rig,'EDIT')
        rig.data.edit_bones.active=rig.data.edit_bones['hand.L']
        for bone in rig.data.edit_bones:bone.select=bone.select_head=bone.select_tail=False
        selection=limb_ik._capture_context(bpy.context,rig)
        c.preview(bpy.context,rig,'ARMS');c.apply(bpy.context,rig,'ARMS')
        self.assertEqual(rig.mode,'EDIT')
        restored=limb_ik._capture_context(bpy.context,rig)
        self.assertEqual(selection['active_bone'],restored['active_bone'])
        self.assertEqual(selection['bone_selection'],restored['bone_selection'])
        before=c.native_rest(rig)
        c.preview(bpy.context,rig,'ARMS');c.apply(bpy.context,rig,'ARMS')
        c.verify_rest(rig,before)

    def test_saved_mapping_validates_native_hierarchy_and_live_edit(self):
        for defect in ('parent','deform','duplicate','generated','geometry'):
            with self.subTest(defect=defect):
                rig,_=fixture();prepare(rig)
                limb_ik._settings(bpy.context).armature=None
                limb_ik._mode_set(bpy.context,rig,'EDIT')
                bone=rig.data.edit_bones['hand.L']
                if defect=='parent':bone.parent=None
                elif defect=='deform':bone.use_deform=False
                elif defect=='generated':bone[limb_ik.OWNER_KEY]=limb_ik.OWNER_VALUE
                elif defect=='geometry':
                    bone.use_connect=False
                    bone.head.x+=.01
                else:
                    data=c.record(rig);data['mappings']['ARM.L'][2]='forearm.L';c.save(rig,data)
                self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'ERROR')
                with self.assertRaises(ValueError):c.preview(bpy.context,rig,'ARMS')
                limb_ik._mode_set(bpy.context,rig,'POSE')
                self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'ERROR')
                with self.assertRaises(Exception):body_setup.generate(bpy.context,rig)

    def test_confirmation_dependencies_and_pose_independence(self):
        rig,_=fixture();prepare(rig)
        rig.pose.bones['hand.L'].rotation_euler.y=.2
        c.settings(rig).show_directions=True
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'CONFIRMED')
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        rig.data.edit_bones['forearm.L'].roll+=.2
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'REVIEW')
        self.assertNotIn('HANDS',c.record(rig)['confirmed'])
        self.assertEqual(c.status(bpy.context,rig,'LEGS')['state'],'CONFIRMED')

    def test_animation_scope_guard(self):
        rig,_=fixture()
        rig.pose.bones['shin.R'].keyframe_insert('rotation_euler',frame=1)
        c.preview(bpy.context,rig,'ARMS');c.apply(bpy.context,rig,'ARMS')
        c.preview(bpy.context,rig,'LEGS')
        with self.assertRaisesRegex(ValueError,'Action|driver'):c.apply(bpy.context,rig,'LEGS')

    def test_palm_reference_staleness_and_mirrored_scale(self):
        rig,_=fixture();obj=bpy.data.objects['Palm L']
        obj.scale=(-2,3,.5)
        bpy.context.view_layer.update()
        with self.assertRaisesRegex(ValueError,'changed'):c.palm(rig,'L')
        c.capture_palm(rig,'L',obj,0,False,True)
        normal,_,_=c.palm(rig,'L')
        self.assertGreater(normal.dot(Vector((0,0,-1))),.999)
        obj.data.vertices[0].co.x+=.001
        with self.assertRaisesRegex(ValueError,'changed'):c.palm(rig,'L')

    def test_save_reopen_state(self):
        rig,_=fixture();prepare(rig);name=rig.name
        with tempfile.TemporaryDirectory(prefix='body-calibration-') as folder:
            path=str(Path(folder)/'copy.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            rig=bpy.data.objects[name]
            for part in ('ARMS','LEGS'):
                self.assertEqual(c.status(bpy.context,rig,part)['state'],'CONFIRMED')

    def test_continuous_motion_and_rest_guard(self):
        rig,mesh=fixture();prepare(rig);body_setup.generate(bpy.context,rig)
        before=c.native_rest(rig)
        inv=limb_ik._validate_inventory(rig)
        target=rig.pose.bones[inv['rigs'][('ARM','L')]['target'].name]
        pole=rig.pose.bones[inv['rigs'][('ARM','L')]['pole'].name]
        previous=None
        max_step=0
        for i in range(81):
            t=i/80*math.tau
            target.location=(.06*math.sin(t),-.08*(1-math.cos(t)),.08*math.sin(t))
            target.rotation_mode='XYZ';target.rotation_euler=(.12*math.sin(t),.25*math.sin(t),0)
            pole.location=(0,.03*math.sin(t),.03*math.sin(t))
            rig.update_tag();bpy.context.view_layer.update()
            rotation=rig.pose.bones['forearm.L'].matrix.to_quaternion()
            if previous is not None:
                step=previous.rotation_difference(rotation).angle
                max_step=max(max_step,min(step,math.tau-step))
            previous=rotation
        self.assertLess(max_step,.3)
        c.verify_rest(rig,before)
        print('CONTINUOUS_MOTION_MAX_STEP_RAD',max_step)

    def test_legacy_stable_and_direct_do_not_migrate(self):
        for method in ('ROLL_DECOUPLED','DIRECT_PREROLL'):
            rig,_=fixture()
            base.analyze(rig)
            limb_ik._settings(bpy.context).build_method=method
            if method=='DIRECT_PREROLL':
                self.assertEqual(bpy.ops.character_designer.limb_ik_direct_preroll_check(),{'FINISHED'})
            self.assertEqual(bpy.ops.character_designer.limb_ik_build_all(),{'FINISHED'})
            before=c.native_rest(rig)
            body_setup.generate(bpy.context,rig)
            self.assertFalse(c.calibrated(rig))
            c.verify_rest(rig,before)
            body_setup.remove(bpy.context,rig)
            c.verify_rest(rig,before)

    def test_unconfigured_fingers_do_not_block_body_generate_update_remove(self):
        rig,mesh=fixture()
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        parent=rig.data.edit_bones['hand.L']
        for i in range(3):
            bone=rig.data.edit_bones.new(f'f_index.{i+1:02}.L')
            bone.head=parent.tail; bone.tail=bone.head+Vector((.03,0,0))
            bone.parent=parent; bone.use_connect=True; parent=bone
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        self.assertEqual(c.status(bpy.context,rig,'FINGERS')['state'],'ERROR')
        value=c.record(rig)
        value['confirmed']['FINGERS']='historical-finger-proof'
        value['finger_references']={'legacy':{'source':'preserve-me'}}
        c.save(rig,value)
        prepare(rig)
        rest=c.native_rest(rig); authored=mesh_state(mesh)
        with patch.object(c,'_finger_evidence',side_effect=AssertionError('Hidden finger gate')):
            c.require_confirmed(bpy.context,rig)
            body_setup.generate(bpy.context,rig)
            body_setup.generate(bpy.context,rig)
            body_setup.remove(bpy.context,rig)
        c.verify_rest(rig,rest)
        self.assertEqual(authored,mesh_state(mesh))
        self.assertEqual(c.record(rig)['confirmed']['FINGERS'],'historical-finger-proof')
        self.assertEqual(c.record(rig)['finger_references'],value['finger_references'])

    def test_fingers_reuses_existing_both_side_records(self):
        from finger_tools_fixtures import bound_fixture, targets, bank
        obj,rig,_=bound_fixture()
        for side in 'LR':
            bank.select(bpy.context,'INDEX',side)
            result=targets.calibrate(bpy.context)
            self.assertFalse(result['failed'])
        limb_ik._mode_set(bpy.context,rig,'POSE')
        saved=tuple((slot.name,slot.bones,slot.guide.record) for slot in obj.character_designer_finger_bank.slots)
        before=mesh_state(obj)
        p=c.preview(bpy.context,rig,'FINGERS')
        self.assertFalse(p['errors'],p['errors'])
        c.apply(bpy.context,rig,'FINGERS');c.confirm(bpy.context,rig,'FINGERS')
        self.assertEqual(c.status(bpy.context,rig,'FINGERS')['state'],'CONFIRMED')
        self.assertEqual(saved,tuple((slot.name,slot.bones,slot.guide.record) for slot in obj.character_designer_finger_bank.slots))
        self.assertEqual(before,mesh_state(obj))
        # Reuse the existing capture after a genuine vertex index permutation.
        import bmesh
        limb_ik._mode_set(bpy.context,rig,'OBJECT')
        rig.select_set(False);obj.select_set(True);bpy.context.view_layer.objects.active=obj
        bpy.ops.object.mode_set(mode='EDIT')
        bm=bmesh.from_edit_mesh(obj.data)
        bm.verts.sort(key=lambda vertex:-vertex.index)
        bm.verts.index_update();bm.verts.ensure_lookup_table()
        bmesh.update_edit_mesh(obj.data,loop_triangles=True,destructive=True)
        bpy.ops.object.mode_set(mode='OBJECT')
        limb_ik._mode_set(bpy.context,rig,'POSE')
        c.preview(bpy.context,rig,'FINGERS')
        c.apply(bpy.context,rig,'FINGERS');c.confirm(bpy.context,rig,'FINGERS')
        self.assertEqual(c.status(bpy.context,rig,'FINGERS')['state'],'CONFIRMED')
        self.assertEqual(saved,tuple((slot.name,slot.bones,slot.guide.record) for slot in obj.character_designer_finger_bank.slots))
        limb_ik._mode_set(bpy.context,rig,'EDIT')
        rig.data.edit_bones['f_index.01.L'].roll+=.3
        self.assertEqual(c.status(bpy.context,rig,'FINGERS')['state'],'ERROR')


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(CalibrationBlender)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():raise SystemExit(1)
