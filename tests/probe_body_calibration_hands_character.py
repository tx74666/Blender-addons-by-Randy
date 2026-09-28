"""Hands integration on an explicitly named disposable character copy only."""
import hashlib
import json
import sys
import traceback
from array import array
from pathlib import Path
import bpy
from mathutils import Vector

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'addons'))
import character_designer
from character_designer import body_calibration as c,body_calibration_overlay as overlay,body_setup,body_setup_removal


def authored(mesh):
    digest=hashlib.sha256()
    for points in [mesh.data.vertices]+([k.data for k in mesh.data.shape_keys.key_blocks] if mesh.data.shape_keys else []):
        values=array('f',[0])*len(points)*3;points.foreach_get('co',values);digest.update(values.tobytes())
    for v in mesh.data.vertices:digest.update(repr([(g.group,g.weight) for g in v.groups]).encode())
    digest.update(repr([g.name for g in mesh.vertex_groups]).encode())
    return digest.hexdigest()


def main(output):
    path=Path(bpy.data.filepath)
    assert 'validation_copy' in path.stem
    source_sha=hashlib.sha256(path.read_bytes()).hexdigest()
    character_designer.register()
    rig=bpy.data.objects['CoshaRig']
    if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
    for obj in bpy.context.selected_objects:obj.select_set(False)
    rig.hide_set(False);rig.select_set(True);bpy.context.view_layer.objects.active=rig
    bpy.context.view_layer.update()
    meshes=[o for o in bpy.data.objects if o.type=='MESH' and any(m.type=='ARMATURE' and m.object==rig for m in o.modifiers)]
    fingerprints={o.name:authored(o) for o in meshes}
    report={'source':str(path),'sha256':source_sha,'blender':bpy.app.version_string,'operations':[]}
    before=c.native_rest(rig);count=len(rig.data.bones);objects=set(bpy.data.objects.keys())
    report['fingers_before']=c.status(bpy.context,rig,'FINGERS')
    try:c._validate_finger_references(rig);report['finger_reference_before']='valid'
    except ValueError as exc:report['finger_reference_before']=str(exc)
    s=c.settings(rig);s.part='HANDS';s.palm_reference='PLANE';s.show_directions=True
    p=c.preview(bpy.context,rig,'HANDS')
    report['hands_candidate']=p
    lines,labels=overlay.segments(bpy.context)
    report['overlay']={'lines':len(lines),'labels':[text for _,text in labels]}
    assert before==c.native_rest(rig) and objects==set(bpy.data.objects.keys())
    for part in ('ARMS','LEGS','HANDS'):
        try:
            surfaces=body_setup_removal._bound_surfaces(bpy.context,rig)
            p=c.preview(bpy.context,rig,part)
            c.apply(bpy.context,rig,part);c.confirm(bpy.context,rig,part)
            delta=body_setup_removal._check_surfaces(bpy.context,rig,surfaces)
            report['operations'].append({'action':'Apply '+part,'ok':True,'max_mesh_delta':delta,'changes':list(p['changes'])})
        except Exception as exc:
            report['operations'].append({'action':'Apply '+part,'ok':False,'error':str(exc)})
    after=c.native_rest(rig)
    assert count==len(rig.data.bones) and objects==set(bpy.data.objects.keys())
    report['endpoints_unchanged']=all(all(v[k]==after[name][k] for k in ('head','tail','parent','use_connect','use_deform')) for name,v in before.items())
    report['rest_changed']=[n for n in before if before[n]!=after[n]]
    c.verify_rest(rig,before,allowed=('hand.L','hand.R'))
    report['other_rest_preserved_within_float_tolerance']=True
    report['other_max_axis_delta']=max((Vector(before[n]['z'])-Vector(after[n]['z'])).length for n in before if n not in {'hand.L','hand.R'})
    report['parts']={part:c.status(bpy.context,rig,part) for part in c.PARTS}
    try:
        p=c.preview(bpy.context,rig,'FINGERS')
        if not p['skipped']:c.apply(bpy.context,rig,'FINGERS');c.confirm(bpy.context,rig,'FINGERS')
        surfaces=body_setup_removal._bound_surfaces(bpy.context,rig)
        body_setup.generate(bpy.context,rig)
        c.verify_rest(rig,after)
        report['operations'].append({'action':'Generate','ok':True,'max_mesh_delta':body_setup_removal._check_surfaces(bpy.context,rig,surfaces)})
        body_setup.remove(bpy.context,rig);c.verify_rest(rig,after)
    except Exception as exc:
        report['operations'].append({'action':'Generate','ok':False,'error':str(exc)})
        # Separate limb-only trial; never represent this as full checklist acceptance.
        s.include_fingers=False
        try:
            surfaces=body_setup_removal._bound_surfaces(bpy.context,rig)
            native_basis={p.name:p.matrix_basis.copy() for p in rig.pose.bones}
            body_setup.generate(bpy.context,rig);c.verify_rest(rig,after)
            first_delta=body_setup_removal._check_surfaces(bpy.context,rig,surfaces)
            basis_delta=max(abs(a-b) for n,m in native_basis.items() for row,actual in zip(m,rig.pose.bones[n].matrix_basis) for a,b in zip(row,actual))
            assert basis_delta<2e-6
            body_setup.generate(bpy.context,rig);c.verify_rest(rig,after)
            updated_delta=body_setup_removal._check_surfaces(bpy.context,rig,surfaces)
            body_setup.remove(bpy.context,rig);c.verify_rest(rig,after)
            removed_delta=body_setup_removal._check_surfaces(bpy.context,rig,surfaces)
            assert len(rig.data.bones)==count
            report['operations'].append({'action':'Generate / Update / Remove, Fingers excluded in copy only','ok':True,
                                         'max_mesh_deltas':[first_delta,updated_delta,removed_delta],'native_basis_delta':basis_delta})
            body_setup.generate(bpy.context,rig);c.verify_rest(rig,after)
            from character_designer import limb_ik,limb_ik_fk
            inventory=limb_ik._validate_inventory(rig)
            target=rig.pose.bones[inventory['rigs'][('ARM','L')]['target'].name]
            saved=target.matrix_basis.copy();joint_before=rig.pose.bones['forearm.L'].head.copy()
            target.location.x+=.03;rig.update_tag();bpy.context.view_layer.update()
            movement=(rig.pose.bones['forearm.L'].head-joint_before).length
            assert movement>1e-5 and all(limb_ik._matrix_is_finite(p.matrix) for p in rig.pose.bones)
            target.matrix_basis=saved;rig.update_tag();bpy.context.view_layer.update()
            c.verify_rest(rig,after)
            report['operations'].append({'action':'Rebuild / move left hand target / restore, Fingers excluded','ok':True,
                                         'elbow_movement':movement,'restored_mesh_delta':body_setup_removal._check_surfaces(bpy.context,rig,surfaces)})
            body_setup.remove(bpy.context,rig);c.verify_rest(rig,after)
        except Exception as trial:
            report['operations'].append({'action':'Generate, Fingers excluded in copy only','ok':False,'error':str(trial)})
    report['authored_mesh_unchanged']=all(authored(o)==fingerprints[o.name] for o in meshes)
    report['copy_file_unchanged']=hashlib.sha256(path.read_bytes()).hexdigest()==source_sha
    assert report['authored_mesh_unchanged'] and report['copy_file_unchanged']
    Path(output).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('operations','endpoints_unchanged','rest_changed','authored_mesh_unchanged','copy_file_unchanged')}))


if __name__=='__main__':main(sys.argv[sys.argv.index('--')+1])
