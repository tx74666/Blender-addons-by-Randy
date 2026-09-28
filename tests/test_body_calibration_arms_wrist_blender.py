"""One arm candidate owns elbow and wrist Roll; no separate hand setup UI."""
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'addons'), str(ROOT/'tests')]
import character_designer
from character_designer import (body_calibration as c, body_calibration_ui as ui,
    body_calibration_overlay as overlay, body_calibration_display as display,
    body_setup, limb_ik)
from test_body_calibration_blender import fixture, mesh_state, prepare


class Layout:
    def __init__(self):
        self.props=[]; self.labels=[]; self.buttons=[]; self.enums=[]; self.events=[]; self.prop_texts={}
    def row(self, **kw): return self
    column = box = split = row
    def prop(self, owner, name, **kw):
        self.props.append(name); self.prop_texts[name]=kw.get('text'); self.events.append(('prop',name))
    def prop_search(self, owner, name, *args, **kw): self.prop(owner,name,**kw)
    def prop_enum(self, owner, name, value, **kw): self.enums.append(value); self.events.append(('enum',value))
    def label(self, text='', **kw): self.labels.append(text); self.events.append(('label',text))
    def operator(self, name, **kw):
        self.buttons.append((name, kw.get('text','')))
        self.events.append(('operator',name))
        return SimpleNamespace()


class ArmsWristTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): character_designer.register()

    def test_normal_and_legacy_selection_show_only_arms_legs_fingers(self):
        rig,_ = fixture(); s=c.settings(rig); s.show_details=True
        saved=rig.get(c.KEY)
        for selected in ('ARMS','HANDS'):
            s.part=selected; layout=Layout(); ui.draw(layout,bpy.context)
            self.assertEqual(layout.enums,['ARMS','LEGS','FINGERS'])
            self.assertIn(('character_designer.forearm_twist_start','Forearm Twist Setup'),layout.buttons)
            self.assertFalse(any(name.startswith('palm_') for name in layout.props))
            self.assertFalse(any('A-pose' in text or 'Mesh Reference' in text for _,text in layout.buttons))
            self.assertIn('Wrist axes follow the forearm',layout.labels)
            self.assertEqual(c.selected_part(rig),'ARMS')
            self.assertEqual(s.part,selected)  # Drawing never migrates saved data.
            self.assertNotIn('tab',layout.props)
            self.assertNotIn('preset',layout.props)
            self.assertEqual(layout.prop_texts['show_directions'],'Preview Directions')
        self.assertEqual(saved,rig.get(c.KEY))
        values={item.identifier:item.value for item in s.bl_rna.properties['part'].enum_items}
        self.assertEqual(values,{'ARMS':0,'LEGS':1,'HANDS':2,'FINGERS':3})

    def test_fingers_entry_uses_existing_panel_without_body_audit_or_axes(self):
        from character_designer import body_calibration_hands as hands
        rig,mesh=fixture(); s=c.settings(rig)
        s.part='FINGERS'; s.show_details=True; s.show_directions=True
        saved=c.native_rest(rig),rig.get(c.KEY),mesh_state(mesh)
        eye=bpy.context.scene.character_designer_finger_definition
        with patch.object(c,'_finger_evidence',side_effect=AssertionError('Duplicate finger audit')), \
             patch.object(display,'_finger_token',side_effect=AssertionError('Finger mesh traversal')):
            layout=Layout(); ui.draw(layout,bpy.context)
            self.assertEqual(layout.enums,['ARMS','LEGS','FINGERS'])
            self.assertEqual(layout.props,[])
            self.assertEqual(layout.buttons,[])
            self.assertTrue(hands.fingers_visible(bpy.context))
            for enabled in (False,True):
                eye.overlays_enabled=enabled
                with patch.object(display,'get',side_effect=AssertionError('Body overlay cache entered')):
                    self.assertEqual(overlay.segments(bpy.context),([],[]))
                self.assertEqual(eye.overlays_enabled,enabled)
            s.part='ARMS'
            self.assertFalse(hands.fingers_visible(bpy.context))
            self.assertTrue(overlay.segments(bpy.context)[0])
        self.assertEqual(saved,(c.native_rest(rig),rig.get(c.KEY),mesh_state(mesh)))

    def test_saved_controls_tab_does_not_hide_calibration_or_reset_custom_parameters(self):
        rig,_=fixture(); s=c.settings(rig); s.tab='CONTROLS'; s.part='ARMS'; s.preset='CUSTOM'
        before=rig.get(c.KEY); layout=Layout()
        self.assertTrue(ui.draw(layout,bpy.context))
        self.assertNotIn('tab',layout.props)
        self.assertNotIn('preset',layout.props)
        self.assertNotIn('arm_axis',layout.props)
        self.assertNotIn('minimum_bend',layout.props)
        self.assertIn(('character_designer.body_calibration','Apply Calibration'),layout.buttons)
        self.assertEqual(s.tab,'CONTROLS'); self.assertEqual(s.preset,'CUSTOM')
        self.assertEqual(before,rig.get(c.KEY))

    def test_calibration_parameters_use_only_existing_advanced_and_keep_saved_values(self):
        rig,_=fixture(); s=c.settings(rig); settings=limb_ik._settings(bpy.context)
        s.preset='CUSTOM'; s.arm_axis='VECTOR'; s.leg_axis='VECTOR'
        s.arm_direction=(0,1,.15); s.leg_direction=(0,-1,.1)
        s.minimum_bend=7.; s.max_shift=.15; s.max_length=.008; s.pole_distance=1.2
        fields=('preset','arm_axis','leg_axis','arm_direction','leg_direction',
                'allow_bend','minimum_bend','max_shift','max_length','pole_distance')
        def values():
            return tuple(tuple(getattr(s,name)) if name.endswith('_direction') else getattr(s,name) for name in fields)
        before=values(),rig.get(c.KEY)
        try:
            for part in ('ARMS','HANDS','LEGS'):
                s.part=part; signature=c.plan(bpy.context,rig,c.selected_part(rig))['signature']
                settings.show_body_setup_advanced=False; closed=Layout()
                limb_ik.CHARACTERDESIGNER_PT_limb_ik.draw(SimpleNamespace(layout=closed),bpy.context)
                self.assertEqual(closed.props.count('show_body_setup_advanced'),1)
                self.assertTrue(set(fields).isdisjoint(closed.props))
                settings.show_body_setup_advanced=True; opened=Layout()
                limb_ik.CHARACTERDESIGNER_PT_limb_ik.draw(SimpleNamespace(layout=opened),bpy.context)
                self.assertEqual(opened.props.count('show_body_setup_advanced'),1)
                self.assertEqual(opened.props.count('preset'),1)
                current='leg' if part=='LEGS' else 'arm'; other='arm' if current=='leg' else 'leg'
                self.assertIn(current+'_axis',opened.props)
                self.assertIn(current+'_direction',opened.props)
                self.assertNotIn(other+'_axis',opened.props)
                for name in fields[5:]: self.assertIn(name,opened.props)
                self.assertEqual(before,(values(),rig.get(c.KEY)))
                self.assertEqual(signature,c.plan(bpy.context,rig,c.selected_part(rig))['signature'])
            s.preset='DEFAULT'; default=Layout()
            limb_ik.CHARACTERDESIGNER_PT_limb_ik.draw(SimpleNamespace(layout=default),bpy.context)
            self.assertIn('preset',default.props)
            self.assertNotIn('leg_axis',default.props)
            self.assertEqual(s.preset,'DEFAULT')
        finally:
            settings.show_body_setup_advanced=False

    def test_generate_is_directly_below_fingers_and_does_not_need_tab_change(self):
        from character_designer import body_setup_ui
        rig,_=fixture(); s=c.settings(rig); s.tab='SETUP'; s.part='FINGERS'
        with patch.object(c,'generation_ready',return_value=True), \
             patch.object(c,'finger_setup_status',return_value={'state':'CONFIRMED','message':''}):
            layout=Layout(); self.assertTrue(ui.draw(layout,bpy.context))
        fingers=layout.events.index(('enum','FINGERS'))
        actions=[i for i,event in enumerate(layout.events) if event==('operator','character_designer.body_setup')]
        self.assertEqual(len(actions),1)
        self.assertEqual(actions[0],fingers+2)  # One confirmation label, then Generate.
        self.assertFalse(any('Remove' in text for _,text in layout.buttons))
        self.assertNotIn('tab',layout.props)
        with patch.object(body_setup_ui,'_has_generated',return_value=True):
            generated=Layout(); self.assertFalse(ui.draw(generated,bpy.context))
        self.assertEqual(sum(name=='character_designer.body_setup' for name,_ in generated.buttons),2)
        self.assertFalse(any(name=='character_designer.body_calibration' for name,_ in generated.buttons))
        self.assertEqual(s.tab,'SETUP')

    def test_first_build_mapping_remains_reachable_and_actions_are_not_duplicated(self):
        from character_designer import body_setup_ui
        rig,_=fixture(); settings=limb_ik._settings(bpy.context)
        settings.show_body_setup_advanced=True; settings.armature=rig
        layout=Layout()
        with patch.object(body_setup_ui,'draw_actions') as actions:
            limb_ik.CHARACTERDESIGNER_PT_limb_ik.draw(SimpleNamespace(layout=layout),bpy.context)
        self.assertEqual(actions.call_count,1)
        self.assertIn(('character_designer.limb_ik_analyze','Analyze Rig'),layout.buttons)
        self.assertIn('show_body_setup_advanced',layout.props)
        kind,side=limb_ik.SELECTED_LIMBS.get(settings.selected_limb,('ARM','L'))
        for role in limb_ik.ROLES: self.assertIn(limb_ik._field_name(kind,side,role),layout.props)
        self.assertNotIn('tab',layout.props)
        settings.show_body_setup_advanced=False
        with patch.object(body_setup_ui,'_has_generated',return_value=True), \
             patch.object(body_setup_ui,'draw_actions') as actions:
            controls=Layout()
            limb_ik.CHARACTERDESIGNER_PT_limb_ik.draw(SimpleNamespace(layout=controls),bpy.context)
        self.assertEqual(actions.call_count,1)
        self.assertIn('selected_limb',controls.props)
        self.assertIn('show_directions',controls.props)  # Still able to hide an active preview after Generate.
        self.assertNotIn('preset',controls.props)

    def test_candidate_forearm_not_old_roll_defines_both_wrist_frames(self):
        rig,mesh=fixture(); before=c.native_rest(rig); authored=mesh_state(mesh)
        objects=set(bpy.data.objects); count=len(rig.data.bones)
        p=c.preview(bpy.context,rig,'ARMS')
        self.assertFalse(p['errors'],p)
        self.assertEqual({frame['side'] for frame in p['wrists']},{'L','R'})
        for frame in p['wrists']:
            lower,hand=frame['chain'][1:]
            forearm=p['changes'].get(lower,before[lower])
            y=Vector(before[hand]['tail'])-Vector(before[hand]['head']); y.normalize()
            wanted=limb_ik._project_perpendicular(Vector(forearm['z']),y).normalized()
            self.assertLess((wanted-Vector(frame['z'])).length,1e-6)
            self.assertGreater(Vector(frame['x']).cross(Vector(frame['y'])).dot(Vector(frame['z'])),.99999)
            self.assertEqual(frame['forearm_start'],forearm['head'])
            self.assertEqual(frame['wrist'],before[hand]['head'])
        self.assertEqual(before,c.native_rest(rig))
        self.assertEqual(authored,mesh_state(mesh))
        self.assertEqual((objects,count),(set(bpy.data.objects),len(rig.data.bones)))
        c.apply(bpy.context,rig,'ARMS',acknowledge=True)
        for frame in p['wrists']:
            name=frame['chain'][-1]; actual=c.rest(rig,name)
            self.assertEqual((actual['head'],actual['tail']),(before[name]['head'],before[name]['tail']))
            self.assertLess((Vector(actual['z'])-Vector(frame['z'])).length,1e-5)
        self.assertFalse(c.plan(bpy.context,rig,'ARMS')['changes'])
        c.confirm(bpy.context,rig,'ARMS')
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'CONFIRMED')

    def test_legacy_palm_settings_and_broken_reference_do_not_define_new_candidate(self):
        rig,_=fixture(); s=c.settings(rig)
        first=c.plan(bpy.context,rig,'ARMS')
        for obj in tuple(bpy.data.objects):
            if obj.name.startswith('Palm '): bpy.data.objects.remove(obj,do_unlink=True)
        s.palm_reference='MESH'; s.palm_tilt=.9; s.palm_plane_flip=True; s.palm_face=1000
        saved=c.record(rig)['palms']
        changed=c.plan(bpy.context,rig,'ARMS')
        self.assertEqual(first,changed)
        c.preview(bpy.context,rig,'ARMS'); c.apply(bpy.context,rig,'ARMS',acknowledge=True)
        c.confirm(bpy.context,rig,'ARMS')
        self.assertEqual(saved,c.record(rig)['palms'])

    def test_late_failure_rolls_back_upper_lower_and_wrist_together(self):
        rig,mesh=fixture(); p=c.preview(bpy.context,rig,'ARMS')
        self.assertTrue(any(name.startswith('hand.') for name in p['changes']))
        before=c.native_rest(rig),mesh_state(mesh),rig.get(c.KEY)
        with patch.object(c,'verify_rest',side_effect=ValueError('Injected post-write check')):
            with self.assertRaisesRegex(ValueError,'Injected post-write check'):
                c.apply(bpy.context,rig,'ARMS',acknowledge=True)
        restored=c.native_rest(rig)
        self.assertEqual(set(before[0]),set(restored))
        for name, saved in before[0].items():
            actual=restored[name]
            # Edit-bone Roll round trips through Blender's float32 basis.
            self.assertLess((Vector(saved['z'])-Vector(actual['z'])).length,1e-6)
            self.assertEqual({k:v for k,v in saved.items() if k!='z'},
                             {k:v for k,v in actual.items() if k!='z'})
        self.assertEqual(before[1:],(mesh_state(mesh),rig.get(c.KEY)))

    def test_wrist_edit_invalidates_arm_confirmation_and_blocks_generate(self):
        rig,mesh=fixture(); prepare(rig)
        limb_ik._mode_set(bpy.context,rig,'EDIT'); rig.data.edit_bones['hand.L'].roll+=.12
        limb_ik._mode_set(bpy.context,rig,'OBJECT'); bpy.context.view_layer.update()
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'REVIEW')
        before=c.native_rest(rig),mesh_state(mesh),len(rig.data.bones)
        with self.assertRaisesRegex(ValueError,'Arms:'):
            body_setup.generate(bpy.context,rig)
        self.assertEqual(before,(c.native_rest(rig),mesh_state(mesh),len(rig.data.bones)))

    def test_generate_remove_preserve_combined_confirmed_rest_and_mesh(self):
        rig,mesh=fixture(); prepare(rig)
        r=c.record(rig); r['confirmed']['HANDS']='historical-hand-proof'; c.save(rig,r)
        before=c.native_rest(rig); authored=mesh_state(mesh)
        def evaluated():
            bpy.context.view_layer.update()
            return [v.co.copy() for v in mesh.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices]
        baseline=evaluated()
        with patch.object(limb_ik,'_apply_direct_preroll',side_effect=AssertionError('Generate changed Rest')):
            body_setup.generate(bpy.context,rig)
            c.verify_rest(rig,before)
            self.assertLess(max((a-b).length for a,b in zip(baseline,evaluated())),2e-6)
            body_setup.remove(bpy.context,rig)
        c.verify_rest(rig,before)
        self.assertEqual(authored,mesh_state(mesh))
        self.assertEqual(c.record(rig)['confirmed']['HANDS'],'historical-hand-proof')

    def test_wrist_overlay_uses_combined_candidate_even_for_saved_hands_selection(self):
        rig,_=fixture(); s=c.settings(rig); s.show_directions=True; s.part='HANDS'
        p=c.plan(bpy.context,rig,'ARMS'); before=c.native_rest(rig); raw=rig.get(c.KEY)
        lines,labels=overlay.segments(bpy.context)
        self.assertTrue(lines)
        for frame in p['wrists']:
            hand=before[frame['chain'][-1]]; size=(Vector(hand['tail'])-Vector(hand['head'])).length
            for axis in ('x','y','z'):
                tip=rig.matrix_world@(Vector(frame['wrist'])+Vector(frame[axis])*size*.7)
                self.assertEqual(sum(text==axis.upper() and (point-tip).length<1e-6 for point,text in labels),1)
            expected=rig.matrix_world@(Vector(frame['wrist'])+
                (Vector(frame['y'])*math.cos(.72)+Vector(frame['z'])*math.sin(.72))*size*.5)
            self.assertTrue(any((point-expected).length<1e-6 for a,b,_ in lines for point in (a,b)))
        self.assertEqual(sum(text=='Forearm' for _,text in labels),2)
        self.assertEqual(before,c.native_rest(rig)); self.assertEqual(raw,rig.get(c.KEY))

    def test_operator_saved_hands_selection_applies_and_confirms_arms(self):
        rig,_=fixture(); s=c.settings(rig); s.part='HANDS'
        for action in ('PREVIEW','APPLY','CONFIRM'):
            self.assertEqual(bpy.ops.character_designer.body_calibration(action=action,acknowledge=True),{'FINISHED'})
        self.assertEqual(c.status(bpy.context,rig,'ARMS')['state'],'CONFIRMED')
        self.assertNotIn('HANDS',c.record(rig)['confirmed'])


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ArmsWristTests))
    if not result.wasSuccessful(): raise SystemExit(1)
