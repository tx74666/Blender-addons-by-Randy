"""Target-specific native Action baking for the existing Unity exchange format.

Humanoid roles are evidence, not permission to rewrite a rig. Native FK and
validated Character Designer control graphs are supported; unknown drivers fail.
"""
import hashlib
import json
import math
from dataclasses import dataclass

import bpy
from mathutils import Matrix, Vector

from . import unity_animation as ua, animation_retarget as curves

REVISION = 1
ALIASES = {
    'Hips': ('hips', 'pelvis'), 'Spine': ('spine',), 'Chest': ('chest', 'spine1'),
    'UpperChest': ('upperchest', 'spine2'), 'Neck': ('neck',), 'Head': ('head',),
}
for side, suffix in (('Left', 'l'), ('Right', 'r')):
    for role, names in {
        'Shoulder': ('shoulder',), 'UpperArm': ('upperarm', 'arm'),
        'LowerArm': ('forearm', 'lowerarm'), 'Hand': ('hand',),
        'UpperLeg': ('thigh', 'upleg', 'upperleg'), 'LowerLeg': ('shin', 'leg', 'lowerleg'),
        'Foot': ('foot',), 'Toes': ('toe', 'toebase', 'toes'), 'Eye': ('eye',),
    }.items():
        ALIASES[side+role] = tuple(n+suffix for n in names)+tuple(side.lower()+n for n in names)
    for finger in ('Thumb', 'Index', 'Middle', 'Ring', 'Little'):
        stem = 'pinky' if finger == 'Little' else finger.lower()
        for i, part in enumerate(('Proximal', 'Intermediate', 'Distal'), 1):
            ALIASES[side+finger+part] = (f'{side.lower()}hand{stem}{i}',
                f'{"" if stem == "thumb" else "f"}{stem}{i:02}{suffix}')

REQUIRED = ('Hips', 'Spine', 'Head', *(s+r for s in ('Left', 'Right')
             for r in ('UpperArm', 'LowerArm', 'Hand', 'UpperLeg', 'LowerLeg', 'Foot')))
LANDMARKS = {'Hips':'Spine', 'Spine':'Chest', 'Chest':'UpperChest', 'UpperChest':'Neck', 'Neck':'Head'}
for side in ('Left','Right'):
    LANDMARKS.update({side+a:side+b for a,b in (('Shoulder','UpperArm'),('UpperArm','LowerArm'),
        ('LowerArm','Hand'),('Hand','MiddleProximal'),('UpperLeg','LowerLeg'),('LowerLeg','Foot'),('Foot','Toes'))})
    for finger in ('Thumb','Index','Middle','Ring','Little'):
        LANDMARKS[side+finger+'Proximal']=side+finger+'Intermediate'
        LANDMARKS[side+finger+'Intermediate']=side+finger+'Distal'


def normalized(name):
    return ''.join(c for c in name.rsplit(':', 1)[-1].lower().removeprefix('def-') if c.isalnum())


def roles(bones, *, target=None, context=None):
    result = {}
    for key, bone in bones.items():
        explicit = bone.get('humanRole', '') if isinstance(bone, dict) else ''
        role = explicit or next((r for r, names in ALIASES.items() if normalized(bone['name'] if isinstance(bone, dict) else bone.name) in names), '')
        if not role: continue
        if role in result: raise ua.UnityAnimationError(f'Ambiguous {role} bone mapping.')
        result[role] = key
    if target is not None and context is not None:
        from . import character_setup, limb_ik, torso_controls
        for role in ('Hips', 'Head'):
            found = character_setup.bone_mapping_status(context, role.upper(), target)
            if found.get('status') in {'AUTO','CONFIRMED','OVERRIDE'}: result[role] = found['name']
        inventory = limb_ik._validate_inventory(target)
        for (kind, side), entry in inventory['rigs'].items():
            prefix = 'Left' if side == 'L' else 'Right'
            for role, name in zip(('UpperArm', 'LowerArm', 'Hand') if kind == 'ARM' else
                                  ('UpperLeg', 'LowerLeg', 'Foot'), entry['chain']): result[prefix+role] = name
        torso = torso_controls.get_record(target)
        if torso:
            for role, name in zip(('Spine', 'Chest', 'UpperChest'), torso['sources']): result[role] = name
    return result


def rig_signature(target):
    driven = {f.data_path for f in target.animation_data.drivers} if target.animation_data else set()
    def constraint(c):
        return [(p.identifier, getattr(c, p.identifier).name_full if isinstance(getattr(c, p.identifier), bpy.types.ID)
                 else repr(getattr(c, p.identifier))) for p in c.bl_rna.properties
                if not p.is_readonly and p.type not in {'COLLECTION'} and p.identifier != 'rna_type'
                and c.path_from_id(p.identifier) not in driven]
    ad = target.animation_data
    payload = (REVISION, tuple(tuple(r) for r in target.matrix_world),
        [(b.name, b.parent.name if b.parent else '', tuple(tuple(r) for r in b.matrix_local),
          tuple(b.tail_local), b.use_connect, b.use_deform, b.inherit_scale, b.use_inherit_rotation,
          b.use_local_location, target.pose.bones[b.name].rotation_mode,
          [constraint(c) for c in target.pose.bones[b.name].constraints]) for b in target.data.bones],
        [(f.data_path, f.array_index, f.mute, f.driver.expression,
          [(v.name, v.type, [(t.id.name_full if t.id else '', t.data_path, t.bone_target) for t in v.targets])
           for v in f.driver.variables]) for f in ad.drivers] if ad else [],
        [(k, str(v)) for k, v in target.data.items() if k.startswith('character_designer_')])
    return hashlib.sha256(repr(payload).encode()).hexdigest()


def controls(target):
    """Validate existing ownership before using its ordinary FK switches/controls."""
    from . import limb_ik, limb_ik_fk, torso_controls, spine_ik_fk, root_control, foot_controls
    inventory = limb_ik._validate_inventory(target)
    redirects, switches, accepted, drivers = {}, {}, set(), set()
    for pb, c, record in inventory['records']:
        accepted.add((pb.name, c.name))
    if inventory['rigs']:
        drivers |= limb_ik_fk.owned_driver_paths(target)
        for entry in inventory['rigs'].values():
            control = target.pose.bones[entry['target'].name]
            if control.bone.get(limb_ik_fk.VERSION_KEY) != limb_ik_fk.VERSION:
                raise ua.UnityAnimationError('This limb has no validated FK switch; update Body Setup first.')
            switches[control.name] = (limb_ik_fk.PROPERTY, 0.)
    for module in (root_control, torso_controls, spine_ik_fk):
        record = module.get_record(target)
        if not record: continue
        module.validate(target, inventory=inventory)
        accepted.update((e['owner'], e['name']) for e in record['constraints'])
        if module is torso_controls: redirects.update(record['controls'])
        if module is root_control:
            drivers |= module.owned_driver_paths(target)
            switches[record['master']] = (module.SCALE_PROPERTY, 1.)
        if module is spine_ik_fk:
            redirects.update(record['fk_controls']); switches[record['chest']] = (module.PROPERTY, 0.)
            drivers.update(target.pose.bones[e['owner']].constraints[e['name']].path_from_id('influence')
                           for e in record['constraints'] if e.get('driven'))
    # Foot records already receive complete validation from the limb inventory.
    for record in foot_controls.records(target).values():
        accepted.update((e['owner'], e['name']) for e in record['constraints'])
        redirects[record['toe']] = record['toe_control']
    drivers |= foot_controls.owned_driver_paths(target)
    for pb in target.pose.bones:
        for c in pb.constraints:
            if (pb.name, c.name) not in accepted:
                raise ua.UnityAnimationError(f'{pb.name}: unsupported constraint {c.name}; existing rig kept intact.')
    for f in target.animation_data.drivers if target.animation_data else ():
        if f.data_path not in drivers:
            raise ua.UnityAnimationError(f'Unrecognized rig driver: {f.data_path}; no driver was removed.')
    return redirects, switches


@dataclass
class Adaptation:
    source: dict
    target: dict
    conversion: Matrix
    scale: float
    rotations: dict
    warnings: list
    exact: object = None
    local_origins: object = None


def _axis_frame(points, left, right, hips, head, *, left_handed=False):
    z = points[head]-points[hips]; x = points[left]-points[right]
    if min(z.length, x.length) < 1e-6: raise ua.UnityAnimationError('Degenerate body landmarks.')
    z.normalize(); x -= z*x.dot(z)
    if x.length < 1e-6: raise ua.UnityAnimationError('Ambiguous body facing/side landmarks.')
    x.normalize(); y = x.cross(z) if left_handed else z.cross(x)
    return Matrix((x, y, z)).transposed()


def plan(context, target, data):
    curves._check_armature(target, 'target') if not (target.animation_data and curves._has_live_nla(target.animation_data)) else curves._uniform_world(target)
    if (target.mode == 'EDIT' or target.library or target.data.library or target.parent or target.constraints
            or target.data.pose_position != 'POSE' or target.data.animation_data):
        raise ua.UnityAnimationError('Use a local character in Pose Position outside Edit Mode, without object/data animation constraints.')
    if target.animation_data and target.animation_data.use_tweak_mode: raise ua.UnityAnimationError('Leave NLA Tweak Mode first.')
    controls(target)
    try:
        exact = ua._mapping(target, data, context.scene.unit_settings.scale_length)
        return Adaptation({}, {}, Matrix.Identity(3), 1, {}, [], exact)
    except ua.UnityAnimationError:
        pass
    source = roles(dict(enumerate(data['bones'])))
    dest = roles({b.name: b for b in target.data.bones if b.use_deform}, target=target, context=context)
    for r in REQUIRED:
        if r not in source or r not in dest:
            raise ua.UnityAnimationError(f'Missing unambiguous {r} on {"source" if r not in source else "target"}; primary motion cannot be discarded.')
    common = set(source) & set(dest)
    source, dest = ({r: m[r] for r in sorted(common)} for m in (source, dest))
    sr = {r: ua._matrix(data['bones'][i]['rest'], r) for r, i in source.items()}
    tr = {r: target.data.bones[n].matrix_local.copy() for r, n in dest.items()}
    sp, tp = ({r: m.translation for r, m in mats.items()} for mats in (sr, tr))
    c = _axis_frame(tp, 'LeftUpperLeg', 'RightUpperLeg', 'Hips', 'Head') @ _axis_frame(
        sp, 'LeftUpperLeg', 'RightUpperLeg', 'Hips', 'Head', left_handed=True).inverted()
    ratios = []
    for s in ('Left', 'Right'):
        a, b = [sum((p[s+x]-p[s+y]).length for x,y in (('UpperLeg','LowerLeg'),('LowerLeg','Foot'))) for p in (sp,tp)]
        if min(a,b) < 1e-6: raise ua.UnityAnimationError('Zero leg length prevents root-motion conversion.')
        ratios.append(b/a)
    if max(ratios)/min(ratios) > 1.2: raise ua.UnityAnimationError('Left/right leg proportions disagree; inspect character mapping.')
    scale = sum(ratios)/2
    # Require the major chains to be real descendants, not just matching names.
    chains = [('Hips','Spine','Head')]+[(s+'UpperArm',s+'LowerArm',s+'Hand') for s in ('Left','Right')]+[(s+'UpperLeg',s+'LowerLeg',s+'Foot') for s in ('Left','Right')]
    for chain in chains:
        for a,b in zip(chain,chain[1:]):
            i = data['bones'][source[b]]['parent']; ancestors = set()
            while i >= 0: ancestors.add(i); i=data['bones'][i]['parent']
            if source[a] not in ancestors or target.data.bones[dest[a]] not in target.data.bones[dest[b]].parent_recursive:
                raise ua.UnityAnimationError(f'{a} → {b}: incompatible bone hierarchy.')
    rotations = {}
    for r in source:
        children = [s for s in source if data['bones'][source[s]]['parent']==source[r]
                    and target.data.bones[dest[s]].parent == target.data.bones[dest[r]]]
        preferred = LANDMARKS.get(r)
        if preferred not in children: preferred = children[0] if len(children)==1 else None
        if preferred and (sp[preferred]-sp[r]).length > 1e-6 and (tp[preferred]-tp[r]).length > 1e-6:
            swing = (tp[preferred]-tp[r]).normalized().rotation_difference((c@(sp[preferred]-sp[r])).normalized())
            rotations[r] = swing @ tr[r].to_quaternion()
        else: rotations[r] = tr[r].to_quaternion()
    warnings=[]
    mapped=set(source.values())
    for i,b in enumerate(data['bones']):
        if i in mapped: continue
        # Ancestor motion is already present in the sampled global child poses.
        descendant=any(_ancestor(data, i, j) for j in mapped)
        moving=_local_motion(data,i)
        if moving and b.get('weighted', b['restSource']=='bindpose') and not descendant:
            raise ua.UnityAnimationError(f"Unmapped weighted channel '{b['name']}' moves independently; cannot safely ignore it.")
        warnings.append(f"{b['name']}: {'inherited motion included in mapped descendants' if descendant else 'static or unweighted auxiliary channel omitted'}")
    local_origins={}
    for r,i in source.items():
        parent=data['bones'][i]['parent']
        first=None
        for frame in data['frames']:
            local=ua._matrix(frame['poses'][i]['matrix'],r)
            if parent>=0:local=ua._matrix(frame['poses'][parent]['matrix'],'parent').inverted()@local
            if first is None:first=local
            if (local.to_scale()-first.to_scale()).length>5e-4:
                raise ua.UnityAnimationError(f'{r}: animated bone scale needs an explicit stretch mapping; channel was not discarded.')
        local_origins[r]=first.translation.copy()
    warnings.append('Target segment lengths and bind offsets retained; dynamic local translations and root motion transferred.')
    return Adaptation(source,dest,c,scale,rotations,warnings,local_origins=local_origins)


def _ancestor(data, ancestor, child):
    i=data['bones'][child]['parent']
    while i>=0:
        if i==ancestor:return True
        i=data['bones'][i]['parent']
    return False


def _local_motion(data, index):
    parent=data['bones'][index]['parent']
    first=None
    for frame in data['frames']:
        m=ua._matrix(frame['poses'][index]['matrix'],'pose')
        if parent>=0:m=ua._matrix(frame['poses'][parent]['matrix'],'parent').inverted()@m
        if first is None:first=m
        elif max(abs(x-y) for a,b in zip(first,m) for x,y in zip(a,b))>2e-4:return True
    return False


def desired_pose(target, data, adaptation, index):
    if adaptation.exact:
        inv=target.matrix_world.inverted()
        return {n:inv@m for n,m in ua.expected_world_matrices(target,data,index,mapping=adaptation.exact).items()}
    c=adaptation.conversion.to_4x4(); ci=c.inverted()
    frame=data['frames'][index]; raw={r:ua._matrix(frame['poses'][i]['matrix'],r) for r,i in adaptation.source.items()}
    result={}; role_for={n:r for r,n in adaptation.target.items()}
    ordered=sorted(target.data.bones,key=lambda b:len(b.parent_recursive))
    for b in ordered:
        r=role_for.get(b.name)
        inherited=(result[b.parent.name]@b.parent.matrix_local.inverted()@b.matrix_local if b.parent else b.matrix_local.copy())
        if r:
            rest=ua._matrix(data['bones'][adaptation.source[r]]['rest'],r)
            delta=c@raw[r]@rest.inverted()@ci
            rotation=delta.to_quaternion()@adaptation.rotations[r]
            location=inherited.translation
            if r=='Hips':location=b.head_local+adaptation.conversion@(raw[r].translation-rest.translation)*adaptation.scale
            else:
                parent=data['bones'][adaptation.source[r]]['parent']
                if parent>=0:
                    parent_pose=ua._matrix(frame['poses'][parent]['matrix'],'parent')
                    local=(parent_pose.inverted()@raw[r]).translation
                    location+=adaptation.conversion@(parent_pose.to_3x3()@(local-adaptation.local_origins[r]))*adaptation.scale
            inherited=Matrix.LocRotScale(location,rotation,Vector((1,1,1)))
        result[b.name]=inherited
    return {n:result[n] for n in role_for}


def bake(context, target, data, *, start_frame=1):
    """Produce an unattached, editable Action; a disposable rig verifies evaluation."""
    adaptation=plan(context,target,data)
    redirects,switches=controls(target)
    frames=[start_frame+f['time']*context.scene.render.fps/context.scene.render.fps_base for f in data['frames']]
    clone=target.copy(); clone.data=target.data.copy(); context.scene.collection.objects.link(clone)
    # Object.copy preserves full drivers (including modifiers and variable flags).
    # Only the disposable object's Action/NLA are detached, never the user's rig.
    if clone.animation_data:
        clone.animation_data.action=None; clone.animation_data.use_nla=False
        for f in clone.animation_data.drivers:
            for v in f.driver.variables:
                for t in v.targets:
                    if t.id==target:t.id=clone
    for pb in clone.pose.bones:
        pb.matrix_basis=Matrix.Identity(4)
        for con in pb.constraints:
            if getattr(con,'target',None)==target:con.target=clone
            if getattr(con,'space_object',None)==target:con.space_object=clone
    action=None
    try:
        channels={}; prevq={}; preve={}; worst=0.
        for n,(prop,value) in switches.items():clone.pose.bones[n][prop]=value
        clone.update_tag(refresh={'OBJECT'})
        context.view_layer.update()
        for index in range(len(frames)):
            desired=desired_pose(target,data,adaptation,index)
            for name in sorted(desired,key=lambda n:len(target.data.bones[n].parent_recursive)):
                pb=clone.pose.bones[redirects.get(name,name)]
                if redirects or switches:
                    pb.matrix=desired[name];context.view_layer.update()
                else:
                    b=pb.bone
                    parent={'parent_matrix':desired.get(b.parent.name,b.parent.matrix_local),
                            'parent_matrix_local':b.parent.matrix_local} if b.parent else {}
                    pb.matrix_basis=b.convert_local_to_pose(desired[name],b.matrix_local,invert=True,**parent)
            context.view_layer.update()
            for name,m in desired.items():
                err=max(abs(x-y) for a,b in zip(clone.pose.bones[name].matrix,m) for x,y in zip(a,b))
                worst=max(worst,err)
                if err>2e-4:raise ua.UnityAnimationError(f'{name}: native rig cannot reproduce sample {index} (error {err:.5g}); rig was not changed.')
            for pb in clone.pose.bones:
                loc,rot,scale=pb.matrix_basis.decompose()
                if pb.name in prevq and rot.dot(prevq[pb.name])<0:rot.negate()
                prevq[pb.name]=rot.copy(); mode=target.pose.bones[pb.name].rotation_mode
                if mode=='QUATERNION':path,values='rotation_quaternion',rot
                elif mode=='AXIS_ANGLE':
                    axis,angle=rot.to_axis_angle();path,values='rotation_axis_angle',(angle,*axis)
                else:
                    e=rot.to_euler(mode,preve[pb.name]) if pb.name in preve else rot.to_euler(mode)
                    preve[pb.name]=e.copy();path,values='rotation_euler',e
                for prop,vals in (('location',loc),(path,values),('scale',scale)):
                    for i,v in enumerate(vals):channels.setdefault((pb.path_from_id()+'.'+prop,i),[]).append(v)
        action=bpy.data.actions.new('Unity · '+str(data.get('clipName') or 'Animation'));action.use_fake_user=True
        slot,bag=curves._new_channelbag(action,target)
        for (path,i),values in channels.items():curves._write_curve(bag,path,i,frames,values)
        for n,(prop,value) in switches.items():curves._write_curve(bag,target.pose.bones[n].path_from_id()+f'["{prop}"]',0,frames,[value]*len(frames))
        action['unity_clip_name']=str(data.get('clipName','Animation'))
        action['unity_start_frame']=float(start_frame);action['unity_duration']=data['duration']
        action['unity_sample_rate']=data['sampleRate'];action['unity_adapter_revision']=REVISION
        action['unity_compatibility']=json.dumps({'mode':'exact' if adaptation.exact else 'humanoid',
             'root_scale':adaptation.scale,'notes':adaptation.warnings,'maximum_native_error':worst})
        return action
    except Exception:
        if action:bpy.data.actions.remove(action)
        raise
    finally:
        mesh=clone.data;bpy.data.objects.remove(clone,do_unlink=True)
        if mesh.users==0:bpy.data.armatures.remove(mesh)
