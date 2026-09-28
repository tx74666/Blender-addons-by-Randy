"""Disposable GUI fixture for paired wrist graphics, undo and safe UI reload."""
import ast
import importlib
import json
import sys
import traceback
from pathlib import Path
import bpy
from mathutils import Vector
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_body_calibration_blender import fixture,character_designer,c,limb_ik
from character_designer import body_calibration_ui as ui,body_calibration_overlay as overlay,body_calibration_hands as hands,finger_bones,forearm_twist

OUT=Path(sys.argv[sys.argv.index('--')+1]);stage=0;report={'errors':[]};before={};after={};rig_name=''

class WRIST_QA_PT_panel(bpy.types.Panel):
    bl_label='Hands / Wrist QA'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='Item'
    def draw(self,context):ui.draw(self.layout,context)


def refresh_function(module,name,class_name=None):
    tree=ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
    if class_name:tree=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==class_name)
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    node.decorator_list=[]
    scope={};exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),module.__file__,'exec'),module.__dict__,scope)
    if class_name:getattr(module,class_name).__dict__[name].__func__.__code__=scope[name].__code__
    else:setattr(module,name,scope[name])


def step():
    global stage,before,after,rig_name
    try:
        if stage==0:
            character_designer.register();bpy.utils.register_class(WRIST_QA_PT_panel)
            bpy.context.preferences.view.show_splash=False
            rig,_=fixture();rig_name=rig.name
            limb_ik._mode_set(bpy.context,rig,'EDIT')
            for side,sign in (('L',1),('R',-1)):
                bone=rig.data.edit_bones['hand.'+side];bone.tail=bone.head+Vector((sign*.12,0,-.10))
            limb_ik._mode_set(bpy.context,rig,'OBJECT');rig.show_in_front=True
            s=c.settings(rig);s.part='HANDS';s.palm_reference='PLANE';s.palm_tilt=.2;s.show_directions=True
            wm=bpy.context.window_manager.character_designer;wm.ui_page='RIG';wm.rig_section='BODY'
            bpy.context.preferences.edit.use_global_undo=True
            bpy.ops.ed.undo_push(message='Before wrist Apply');before=c.native_rest(rig)
            for area in bpy.context.screen.areas:
                if area.type=='VIEW_3D':
                    area.spaces.active.region_3d.view_distance=2.4
                    area.spaces.active.region_3d.view_location=(0,0,1.35)
                    area.spaces.active.region_3d.view_rotation=Vector((0,-6,1)).to_track_quat('Z','Y')
                    area.spaces.active.region_3d.view_perspective='ORTHO';area.spaces.active.show_region_ui=True
            report['objects_before']=len(bpy.data.objects);report['bones_before']=len(rig.data.bones)
        elif stage in (1,3,5,7):
            bpy.ops.screen.screenshot(filepath=str(OUT/f'hands_gui_{stage}.png'))
        elif stage==2:
            rig=bpy.data.objects[rig_name]
            assert bpy.ops.character_designer.body_calibration(action='PREVIEW')=={'FINISHED'}
            assert bpy.ops.character_designer.body_calibration(action='APPLY')=={'FINISHED'}
            after=c.native_rest(rig);bpy.ops.ed.undo_push(message='After wrist Apply')
            assert bpy.ops.ed.undo()=={'FINISHED'};c.verify_rest(bpy.data.objects[rig_name],before)
            assert bpy.ops.ed.redo()=={'FINISHED'};c.verify_rest(bpy.data.objects[rig_name],after)
            report['undo_redo']=True
        elif stage==4:
            rig=bpy.data.objects[rig_name];s=c.settings(rig)
            saved={p.identifier:tuple(getattr(s,p.identifier)) if getattr(p,'is_array',False) else getattr(s,p.identifier)
                   for p in s.bl_rna.properties if p.identifier!='rna_type' and not p.is_readonly}
            data=c.native_rest(rig);poses={p.name:p.matrix_basis.copy() for p in rig.pose.bones};record=rig.get(c.KEY)
            ui.unregister();importlib.reload(c);importlib.reload(hands);importlib.reload(overlay);importlib.reload(ui);ui.register()
            for key,value in saved.items():setattr(c.settings(rig),key,value)
            refresh_function(finger_bones,'poll','CHARACTERDESIGNER_PT_fingers');refresh_function(forearm_twist,'context_mesh')
            assert data==c.native_rest(rig) and poses=={p.name:p.matrix_basis for p in rig.pose.bones} and record==rig.get(c.KEY)
            assert len(overlay._handles)==2
            report['safe_refresh']=True
            c.settings(rig).part='FINGERS'
            assert finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context)
        elif stage==6:
            rig=bpy.data.objects[rig_name];c.settings(rig).part='HANDS'
            assert not finger_bones.CHARACTERDESIGNER_PT_fingers.poll(bpy.context)
            limb_ik._mode_set(bpy.context,rig,'EDIT')
            report['routing']=True
        else:
            assert len(bpy.data.objects)==report['objects_before'] and len(bpy.data.objects[rig_name].data.edit_bones)==report['bones_before']
            report['graphics_no_added_data']=True
            (OUT/'hands_gui_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            bpy.ops.wm.quit_blender();return None
        stage+=1;return .7
    except Exception:
        report['errors'].append(traceback.format_exc());(OUT/'hands_gui_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        bpy.ops.wm.quit_blender();return None


bpy.app.timers.register(step,first_interval=1.)
