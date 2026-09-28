"""Separate factory GUI process: real draw handlers and native undo, no user file."""
import json
import sys
import traceback
from pathlib import Path
import bpy
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_body_calibration_blender import fixture,prepare,character_designer,c,limb_ik,body_setup
from character_designer import body_calibration_overlay as overlay
from character_designer import body_calibration_ui

class BODYCALIBRATION_PT_gui_test(bpy.types.Panel):
    bl_label='Body Calibration QA'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='Item'
    def draw(self,context):body_calibration_ui.draw(self.layout,context)

OUT=Path(sys.argv[sys.argv.index('--')+1])
report={'undo':False,'redo':False,'reload':False,'screenshots':[],'errors':[]}
stage=0
rig_name=''
bpy.context.preferences.view.show_splash=False


def step():
    global stage,rig_name
    try:
        if stage==0:
            character_designer.register()
            bpy.utils.register_class(BODYCALIBRATION_PT_gui_test)
            rig,mesh=fixture();rig_name=rig.name
            limb_ik._mode_set(bpy.context,rig,'POSE')
            rig.show_in_front=True
            c.settings(rig).show_directions=True
            c.settings(rig).tab='SETUP'
            bpy.context.window_manager.character_designer.ui_page='RIG'
            bpy.context.window_manager.character_designer.rig_section='BODY'
            bpy.context.preferences.edit.use_global_undo=True
            bpy.ops.ed.undo_push(message='Before calibration')
            report['before']=c.native_rest(rig)
            timeline=next((a for a in bpy.context.screen.areas if a.type=='DOPESHEET_EDITOR'),None)
            if timeline:timeline.type='VIEW_3D'
            report['multiple_viewports']=sum(a.type=='VIEW_3D' for a in bpy.context.screen.areas)>1
            for area in bpy.context.screen.areas:
                if area.type=='VIEW_3D':
                    area.spaces.active.region_3d.view_distance=3.2
                    area.spaces.active.region_3d.view_location=(0,0,1)
                    area.spaces.active.region_3d.view_rotation=Vector((0,-6,1)).to_track_quat('Z','Y')
                    area.spaces.active.region_3d.view_perspective='ORTHO'
                    area.spaces.active.show_region_ui=True
                    area.tag_redraw()
        elif stage in (1,3,5,7,11,13):
            path=OUT/f'GUI_{stage}.png'
            bpy.ops.screen.screenshot(filepath=str(path))
            report['screenshots'].append(str(path))
        elif stage==2:
            rig=bpy.data.objects[rig_name]
            s=c.settings(rig);s.preset='CUSTOM';s.arm_axis='Z';s.max_shift=.5
            c.preview(bpy.context,rig,'ARMS')
        elif stage==4:
            rig=bpy.data.objects[rig_name]
            c.settings(rig).part='ARMS'
            assert bpy.ops.character_designer.body_calibration(action='APPLY',acknowledge=True)=={'FINISHED'}
            report['after']=c.native_rest(rig)
            bpy.ops.ed.undo_push(message='After calibration')
        elif stage==6:
            assert bpy.ops.ed.undo()=={'FINISHED'}
            rig=bpy.data.objects[rig_name]
            c.verify_rest(rig,report['before']);report['undo']=True
            assert bpy.ops.ed.redo()=={'FINISHED'}
            rig=bpy.data.objects[rig_name]
            c.verify_rest(rig,report['after']);report['redo']=True
            prepare(rig);body_setup.generate(bpy.context,rig)
            overlay.register();overlay.register()
            assert len(overlay._handles)==2
            overlay.unregister();assert not overlay._handles
            overlay.register();assert len(overlay._handles)==2
            report['reload']=True
        elif stage==8:
            path=str(OUT/'synthetic_gui_validation.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
        elif stage==9:
            assert len(overlay._handles)==2
            report['file_reload_handlers']=True
        elif stage==10:
            rig=bpy.data.objects[rig_name]
            body_setup.remove(bpy.context,rig)
            limb_ik._mode_set(bpy.context,rig,'POSE')
            c.settings(rig).preset='CUSTOM'
            c.settings(rig).arm_axis='NY'
            c.settings(rig).show_details=False
            rig.pose.bones['hand.L'].rotation_mode='XYZ'
            rig.pose.bones['hand.L'].rotation_euler.x=.2
            rig.update_tag()
            bpy.context.view_layer.update()
            candidate=c.preview(bpy.context,rig,'ARMS')
            assert c.apply_readiness(bpy.context,rig,candidate['changes'])['code']=='POSE'
            report['pose_blocker_visible']=True
        elif stage==12:
            c.settings(bpy.data.objects[rig_name]).show_details=True
        elif stage==14:
            import importlib
            rig=bpy.data.objects[rig_name]
            settings=c.settings(rig)
            saved={p.identifier:tuple(getattr(settings,p.identifier)) if getattr(p,'is_array',False) else getattr(settings,p.identifier)
                   for p in settings.bl_rna.properties if p.identifier!='rna_type' and not p.is_readonly}
            before=c.native_rest(rig)
            poses={p.name:p.matrix_basis.copy() for p in rig.pose.bones}
            body_calibration_ui.unregister()
            importlib.reload(c);importlib.reload(overlay);importlib.reload(body_calibration_ui)
            body_calibration_ui.register()
            for key,value in saved.items():setattr(c.settings(rig),key,value)
            c.verify_rest(rig,before)
            assert poses=={p.name:p.matrix_basis for p in rig.pose.bones}
            assert c.settings(rig).show_details and c.settings(rig).preset=='CUSTOM'
            assert len(overlay._handles)==2
            report['ui_reload_preserved_data']=True
        else:
            report.pop('before',None);report.pop('after',None)
            (OUT/'gui_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            bpy.ops.wm.quit_blender()
            return None
        stage+=1
        return .5
    except Exception:
        report['errors'].append(traceback.format_exc())
        (OUT/'gui_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(step,first_interval=1.,persistent=True)
