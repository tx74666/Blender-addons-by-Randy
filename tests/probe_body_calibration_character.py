"""Run only against a disposable .blend copy. Never saves or replaces the input."""
import hashlib
import json
import sys
import traceback
from pathlib import Path
import bpy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'addons'))
import character_designer
from character_designer import body_calibration as c,body_setup,body_setup_removal,limb_ik


def main(output):
    source=Path(bpy.data.filepath)
    if 'validation_copy' not in source.name: raise ValueError('Use the explicitly named disposable validation_copy.blend.')
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    character_designer.register()
    rig=bpy.data.objects.get('CoshaRig')
    if rig is None: raise ValueError('Known character rig not found; no guessed mapping will be applied.')
    if bpy.context.object and bpy.context.object.mode!='OBJECT':bpy.ops.object.mode_set(mode='OBJECT')
    for obj in bpy.context.selected_objects:obj.select_set(False)
    rig.hide_set(False);rig.select_set(True);bpy.context.view_layer.objects.active=rig
    bpy.ops.object.mode_set(mode='POSE')
    bpy.context.view_layer.update()
    meshes=[o for o in bpy.data.objects if o.type=='MESH' and any(m.type=='ARMATURE' and m.object==rig for m in o.modifiers)]
    def content(o):
        return repr(([tuple(v.co) for v in o.data.vertices],
                     [[(g.group,g.weight) for g in v.groups] for v in o.data.vertices],
                     [(k.name,[tuple(p.co) for p in k.data]) for k in o.data.shape_keys.key_blocks] if o.data.shape_keys else None))
    fingerprints={o.name:hashlib.sha256(content(o).encode()).hexdigest() for o in meshes}
    before=c.native_rest(rig)
    report={'source':str(source),'source_sha256':sha,'runtime':c.__file__,'blender':bpy.app.version_string,
            'initial_inventory_schema':limb_ik._validate_inventory(rig)['schema'],'parts':{},'operations':[]}
    for part in c.PARTS:
        report['parts'][part]=c.status(bpy.context,rig,part)
    for operation in ('UPDATE','REMOVE'):
        try:
            result=body_setup.generate(bpy.context,rig) if operation=='UPDATE' else body_setup.remove(bpy.context,rig)
            c.verify_rest(rig,before)
            report['operations'].append({'operation':operation,'ok':True})
        except Exception as exc:
            report['operations'].append({'operation':operation,'ok':False,'error':str(exc),'trace':traceback.format_exc()})
    for part in ('ARMS','LEGS'):
        try:
            surfaces=body_setup_removal._bound_surfaces(bpy.context,rig)
            bone_count=len(rig.data.bones)
            candidate=c.preview(bpy.context,rig,part)
            report['operations'].append({'operation':'PREVIEW '+part,'candidate':candidate})
            c.apply(bpy.context,rig,part,acknowledge=True)
            c.confirm(bpy.context,rig,part)
            maximum=body_setup_removal._check_surfaces(bpy.context,rig,surfaces)
            assert len(rig.data.bones)==bone_count
            report['operations'].append({'operation':'APPLY '+part,'ok':True,
                                         'max_vertex_displacement':maximum,'bone_count':bone_count})
        except Exception as exc:
            report['operations'].append({'operation':'APPLY '+part,'ok':False,'error':str(exc)})
    report['protected_mesh_data_unchanged']=all(hashlib.sha256(content(o).encode()).hexdigest()==fingerprints[o.name] for o in meshes)
    report['copy_file_unchanged']=hashlib.sha256(source.read_bytes()).hexdigest()==sha
    report['unverified']=['Actual palm face requires user confirmation; full new-rig Generate and motion on this character are not validated.',
                           'GUI multi-viewport drawing and visual deformation quality are not covered by this background probe.']
    Path(output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print('CHARACTER_PROBE',json.dumps({'operations':[(r['operation'],r.get('ok'),r.get('error')) for r in report['operations']],
                                        'mesh_preserved':report['protected_mesh_data_unchanged'],'copy_file_unchanged':report['copy_file_unchanged']},ensure_ascii=False),flush=True)


if __name__=='__main__':main(sys.argv[sys.argv.index('--')+1])
