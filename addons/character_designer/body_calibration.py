"""Character-owned calibration; queries and viewport drawing never write Rest."""
import hashlib
import json
import math

from mathutils import Vector
from . import limb_ik

VERSION = 1
KEY = 'character_designer_body_calibration_v1'
PARTS = ('ARMS', 'LEGS')
# Keep the saved enum's original numeric order and legacy service entry point.
# Fingers use their existing tools, without a second Body Setup confirmation.
# New calibration owns the wrist together with its arm; old records are retained.
STORED_PARTS = ('ARMS', 'LEGS', 'HANDS', 'FINGERS')


def settings(rig):
    return rig.character_designer_body_calibration


def selected_part(rig):
    part = settings(rig).part
    return 'ARMS' if part == 'HANDS' else part


def record(rig):
    raw = rig.get(KEY, '')
    result = json.loads(raw) if raw else {'version': VERSION, 'applied': {}, 'confirmed': {}, 'palms': {}, 'mappings': {}}
    if not isinstance(result,dict) or result.get('version') != VERSION:
        raise ValueError('Unsupported Body Calibration version; keep this character unchanged.')
    if any(not isinstance(result.get(key),dict) for key in ('applied','confirmed','palms','mappings')):
        raise ValueError('Body Calibration record is incomplete; keep the character unchanged.')
    return result


def save(rig, value):
    rig[KEY] = json.dumps(value, sort_keys=True, allow_nan=False)


def chains(context, rig, kind=None):
    result = []
    stored = record(rig)['mappings']
    ui = limb_ik._settings(context)
    inventory = limb_ik._validate_inventory(rig) if rig.mode != 'EDIT' else edit_inventory(rig)
    analysis = None
    for k in ((kind,) if kind else limb_ik.KINDS):
        for side in limb_ik.SIDES:
            key = k + '.' + side
            existing = inventory['rigs'].get((k, side))
            names = existing['chain'] if existing else None
            if not names and ui and ui.armature == rig:
                mapped = tuple(getattr(ui,limb_ik._field_name(k,side,role),'') for role in limb_ik.ROLES)
                names = mapped if any(mapped) else None
            names = names or stored.get(key)
            if not names:
                analysis = analysis or limb_ik.analyze_armature(rig)
                found = analysis['limbs'][k][side]
                if found['status'] in {'AMBIGUOUS', 'LOW_CONFIDENCE'}:
                    raise ValueError(f'{side} {k}: choose the three native bones in Controls > Advanced.')
                if found['status'] != 'READY':
                    continue
                names = [found[role] for role in limb_ik.ROLES]
            _validate_chain(rig,k,side,names)
            result.append(limb_ik.LimbChain(k, side, *names))
    return result


def _validate_chain(rig, kind, side, names):
    """Use the same native-chain contract for every mapping source and live mode."""
    key = kind+'.'+side
    bones = rig.data.edit_bones if rig.mode == 'EDIT' else rig.data.bones
    if not isinstance(names,(tuple,list)) or len(names) != 3 or any(not isinstance(n,str) or n not in bones for n in names):
        raise ValueError(f'{key}: mapping is incomplete or missing a bone.')
    if len(set(names)) != 3: raise ValueError(f'{key}: mapping needs three different bones.')
    selected = [bones[n] for n in names]
    if any(not b.use_deform or b.get(limb_ik.OWNER_KEY) in limb_ik.GENERATED_CONTROL_OWNERS
           or limb_ik._EXCLUDED_TOKENS.intersection(limb_ik._name_parts(b.name)[0]) for b in selected):
        raise ValueError(f'{key}: mapping must contain native Deform bones, not helpers or controls.')
    if any(limb_ik._side_from_name(n) not in ('',None,side) for n in names):
        raise ValueError(f'{key}: mapping contains an opposite-side bone.')
    a,b,c = [rest(rig,n) for n in names]
    if b['parent'] != names[0] or c['parent'] != names[1]:
        raise ValueError(f'{key}: mapping must form one direct upper → lower → end hierarchy.')
    lengths = [(Vector(r['tail'])-Vector(r['head'])).length for r in (a,b,c)]
    if min(lengths) <= limb_ik.EPSILON: raise ValueError(f'{key}: zero-length native segment.')
    tolerance = (lengths[0]+lengths[1])*1e-6
    if any((Vector(p['tail'])-Vector(q['head'])).length > tolerance for p,q in ((a,b),(b,c))):
        raise ValueError(f'{key}: mapped joints are spatially disconnected.')


def edit_inventory(rig):
    """Live tagged EditBones for display; incomplete edited controls stay explicit."""
    rigs = {}
    for bone in rig.data.edit_bones:
        if bone.get(limb_ik.OWNER_KEY) != limb_ik.OWNER_VALUE: continue
        role = bone.get(limb_ik.ROLE_KEY)
        if role not in {'HAND_IK','FOOT_IK','POLE'}: continue
        key = bone.get(limb_ik.KIND_KEY),bone.get(limb_ik.SIDE_KEY)
        item = rigs.setdefault(key,{})
        item['pole' if role == 'POLE' else 'target'] = bone
        if bone.get(limb_ik.CHAIN_KEY): item['chain'] = json.loads(bone[limb_ik.CHAIN_KEY])
    return {'rigs':rigs}


def rest(rig, name):
    b = (rig.data.edit_bones if rig.mode == 'EDIT' else rig.data.bones)[name]
    edit = rig.mode == 'EDIT'
    head,tail = (b.head,b.tail) if edit else (b.head_local,b.tail_local)
    z = limb_ik._project_perpendicular((b.matrix if edit else b.matrix_local).to_3x3().col[2],tail-head).normalized()
    return {'head': list(head),
            'tail': list(b.tail if edit else b.tail_local),
            'z': list(z),
            'parent': b.parent.name if b.parent else '', 'use_connect': bool(b.use_connect),
            'use_deform': bool(b.use_deform)}


def direction(rig, chain):
    from .body_calibration_math import stable_bend
    a, b = rest(rig, chain.upper), rest(rig, chain.lower)
    s, e, w = Vector(a['head']), Vector(b['head']), Vector(b['tail'])
    projected = limb_ik._project_perpendicular(e-s, w-s)
    total = (e-s).length + (w-e).length
    stable = stable_bend(s,e,w,limb_ik.EPSILON)
    return s, e, w, projected.normalized() if stable else None


def target(rig, kind):
    s = settings(rig)
    if s.preset == 'DEFAULT': return Vector((0,1 if kind == 'ARM' else -1,0))
    axes = {'X':(1,0,0),'NX':(-1,0,0),'Y':(0,1,0),'NY':(0,-1,0),'Z':(0,0,1),'NZ':(0,0,-1)}
    choice = s.arm_axis if kind == 'ARM' else s.leg_axis
    return Vector(axes[choice] if choice != 'VECTOR' else s.arm_direction if kind == 'ARM' else s.leg_direction)


def _hash(value):
    def clean(v):
        if isinstance(v, float):
            if not math.isfinite(v): raise ValueError('Non-finite calibration input.')
            return float(format(v, '.6g'))
        if isinstance(v, dict): return {k: [round(a,5) for a in x] if k == 'z' else clean(x) for k,x in v.items()}
        if isinstance(v, (list,tuple)): return [clean(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, allow_nan=False).encode()).hexdigest()


def native_rest(rig):
    bones = rig.data.edit_bones if rig.mode == 'EDIT' else rig.data.bones
    return {b.name: rest(rig, b.name) for b in bones
            if b.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS}


def verify_rest(rig, before, allowed=()):
    for name, old in before.items():
        if name in allowed: continue
        new = rest(rig, name)
        scale = max((Vector(old['tail'])-Vector(old['head'])).length, 1e-8)
        if any(new[k] != old[k] for k in ('parent', 'use_connect', 'use_deform')) or any(
                (Vector(new[k])-Vector(old[k])).length > (scale*2e-6 if k != 'z' else 1e-5)
                for k in ('head','tail','z')):
            raise ValueError(f'Operation changed protected native Rest: {name}.')


def _config(rig, part):
    s = settings(rig)
    if part == 'FINGERS': return {'include': s.include_fingers}
    kind = 'LEG' if part == 'LEGS' else 'ARM'
    existing = any(b.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE and b.get(limb_ik.KIND_KEY) == kind for b in rig.data.bones)
    result = {'preset': s.preset, 'enabled': existing or (s.include_legs if part == 'LEGS' else s.include_arms)}
    if part in {'ARMS','LEGS'}:
        result.update(direction=list(target(rig, 'ARM' if part == 'ARMS' else 'LEG')),
                      allow_bend=s.allow_bend if s.preset == 'CUSTOM' else False,
                      minimum_bend=s.minimum_bend if s.preset == 'CUSTOM' else 5.,
                      max_shift=s.max_shift if s.preset == 'CUSTOM' else .1,
                      max_length=s.max_length if s.preset == 'CUSTOM' else .005,
                      pole_distance=s.pole_distance)
    return result


def _palm_geometry(rig, obj, face):
    if obj is None or obj.type != 'MESH' or obj.mode == 'EDIT':
        raise ValueError('Choose a mesh palm face outside mesh Edit Mode.')
    if face < 0 or face >= len(obj.data.polygons): raise ValueError('Palm face no longer exists.')
    polygon = obj.data.polygons[face]
    ids = list(polygon.vertices)
    source = obj.data.shape_keys.reference_key.data if obj.data.shape_keys else obj.data.vertices
    points = [source[i].co.copy() for i in ids]
    normal = Vector()
    for a,b in zip(points,points[1:]+points[:1]): normal += a.cross(b)
    if normal.length < 1e-12: raise ValueError('Degenerate palm reference face.')
    relative = rig.matrix_world.inverted() @ obj.matrix_world
    if abs(relative.to_3x3().determinant()) < 1e-12: raise ValueError('Singular palm reference transform.')
    # Inverse transpose preserves the declared outward normal under scale/reflection.
    normal = (relative.to_3x3().inverted().transposed() @ normal).normalized()
    center = relative @ (sum(points, Vector())/len(points))
    evidence = {'object': obj.name, 'mesh': obj.data.name, 'face': face, 'vertices': ids,
                'coordinates': [list(p) for p in points], 'matrix': [list(r) for r in relative],
                'counts': [len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons)]}
    return normal, center, evidence


def capture_palm(rig, side, obj, face, flip, acknowledged):
    if not acknowledged: raise ValueError('Confirm that the arrow points out of the palm, or reverse it first.')
    normal, center, evidence = _palm_geometry(rig,obj,face)
    value = record(rig)
    value['palms'][side] = {'evidence': evidence, 'fingerprint': _hash(evidence), 'flip': bool(flip)}
    save(rig, value)


def palm(rig, side):
    import bpy
    saved = record(rig)['palms'].get(side)
    if not saved: raise ValueError(f'{side} palm: undefined; choose and confirm a reference face.')
    proof = saved['evidence']
    normal, center, evidence = _palm_geometry(rig,bpy.data.objects.get(proof['object']),proof['face'])
    if _hash(evidence) != saved['fingerprint']:
        raise ValueError(f'{side} palm: reference geometry/transform changed; confirm the face again.')
    if saved.get('mirror'):
        from .finger_symmetry import reflect
        normal,center=reflect(normal),reflect(center)
    return normal * (-1 if saved['flip'] else 1), center, saved


def _finger_evidence(context, rig):
    import bpy
    from . import finger_targets, finger_flex, finger_bank, finger_definition
    candidates = finger_targets.index(rig)
    if not candidates: return {}, []
    meshes = [obj for obj in bpy.data.objects if obj.type == 'MESH'
              and any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers)
              and getattr(obj, 'character_designer_finger_bank', None)
              and obj.character_designer_finger_bank.survey]
    if len(meshes) != 1: raise ValueError('Fingers: choose one existing captured mesh bound to this rig.')
    obj = meshes[0]
    evidence, errors = {}, []
    for key in sorted(candidates):
        try:
            chain = finger_targets.resolve(obj,rig,key,candidates)
            slot = obj.character_designer_finger_bank.slots[key]
            if not slot.guide.confirmed: raise ValueError('Existing Finger Setup is not confirmed.')
            resolved = record(rig).get('finger_references',{}).get(key)
            raw = (resolved['record'] if resolved and resolved['source'] == _hash(slot.guide.record)
                   and resolved['mesh'] == obj.name else json.loads(slot.guide.record))
            normal = raw['basis'].get('normal')
            if not normal: raise ValueError('Saved bend direction is undefined.')
            bend_world = obj.matrix_world.to_3x3().inverted().transposed() @ Vector(normal)*(1 if slot.guide.flip_bend else -1)
            bend = rig.matrix_world.to_3x3().inverted() @ bend_world
            for bone in chain:
                r = rest(rig,bone.name)
                t, _, axis = finger_flex.frame(Vector(r['tail'])-Vector(r['head']),bend)
                actual_x = t.cross(Vector(r['z'])).normalized()
                if actual_x.dot(axis) < math.cos(math.radians(5)):
                    raise ValueError(f'{bone.name}: existing Roll needs review in Fingers > Bone Roll.')
            # Read only the vertices recorded in this reference, never a whole mesh in drawing.
            source = obj.data.shape_keys.reference_key.data if obj.data.shape_keys else obj.data.vertices
            refs = [[i,list(source[i].co)] for i,_ in raw['basis']['coordinates']]
            if any((Vector(co)-Vector(saved)).length > 1e-6 for (_,co),(_,saved) in zip(refs,raw['basis']['coordinates'])):
                raise ValueError('Reference vertices changed; Preview/Recheck the existing reference.')
            topology = {}
            for kind, rows in raw.get('local_evidence',{}).items():
                seq = obj.data.polygons if kind == 'FACES' else obj.data.edges
                topology[kind] = {i:list(seq[int(i)].vertices) for i in rows}
            evidence[key] = {'bones': {b.name: rest(rig,b.name) for b in chain}, 'record': slot.guide.record,
                             'binding': slot.bones, 'flip': slot.guide.flip_bend, 'reference': refs, 'topology': topology,
                             'counts': [len(obj.data.vertices),len(obj.data.edges),len(obj.data.polygons)],
                             'matrix': [list(r) for r in rig.matrix_world.inverted() @ obj.matrix_world]}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            errors.append(f'{key}: {exc}')
    return evidence, errors


def plan(context, rig, part):
    from .body_calibration_math import solve
    config = _config(rig,part)
    result = {'part': part, 'changes': {}, 'limbs': [], 'wrists': [], 'errors': [], 'warnings': [], 'skipped': False,
              'needs_acknowledgment': False, 'mappings': {}}
    evidence = {'version': VERSION, 'config': config}
    if part in {'ARMS','LEGS'}:
        # Older previews must be reviewed against the native-precision candidate.
        evidence['candidate_revision'] = 3 if part == 'ARMS' else 2
    if not config.get('enabled', config.get('include', True)):
        result['skipped'] = True
    elif part == 'FINGERS':
        data, errors = _finger_evidence(context,rig)
        evidence['fingers'] = data
        result['errors'] += errors
        result['skipped'] = not data and not errors
    else:
        resolved = chains(context,rig,'LEG' if part == 'LEGS' else 'ARM')
        if part == 'HANDS':
            from . import body_calibration_hands as hands
            try:
                if hands.linked(rig): resolved=list(hands.pair(context,rig).values())
                if hands.pending_reference(context,rig):
                    raise ValueError('New palm reference is pending: use Use for Both Hands before Preview / Apply.')
            except ValueError as exc:
                result['errors'].append(str(exc))
                # Do not draw an old candidate beside a newly selected reference.
                resolved=[]
                evidence['pending_error']=str(exc)
        evidence['bones'], evidence['palms'] = {}, {}
        result['skipped'] = not resolved and not result['errors']
        used = set()
        for chain in resolved:
            if used.intersection(chain.names): result['errors'].append('Overlapping limb mappings.')
            used.update(chain.names)
            result['mappings'][chain.kind+'.'+chain.side] = list(chain.names)
            a,b,c = [rest(rig,n) for n in chain.names]
            for name in chain.names:
                evidence['bones'][name] = rest(rig,name)
                parent = evidence['bones'][name]['parent']
                while parent and parent not in evidence['bones']:
                    evidence['bones'][parent] = rest(rig,parent)
                    parent = evidence['bones'][parent]['parent']
            try:
                if part == 'HANDS':
                    frame=hands.reference(rig,chain)
                    evidence['palms'][chain.side]=frame['proof']
                    changed=dict(c,z=frame['z'])
                    if (Vector(c['z'])-Vector(frame['z'])).length>1e-5:result['changes'][chain.end]=changed
                    result['limbs'].append(dict(frame,side=chain.side,chain=list(chain.names)))
                else:
                    s,e,w = Vector(a['head']),Vector(b['head']),Vector(b['tail'])
                    scale = (e-s).length+(w-e).length
                    if b['parent'] != chain.upper or (Vector(a['tail'])-e).length > scale*1e-6 or (Vector(c['head'])-w).length > scale*1e-6:
                        raise ValueError('Mapped bones must meet at their joints and form an ordered chain.')
                    computation = solve(s,e,w,config['direction'],absolute_tolerance=limb_ik.EPSILON,native_precision=True,
                                        **{k:config[k] for k in ('allow_bend','minimum_bend','max_shift','max_length')})
                    result['errors'] += [chain.side+': '+m for m in computation['errors']]
                    result['needs_acknowledgment'] |= computation['needs_acknowledgment']
                    new_a,new_b = dict(a,tail=list(computation['joint']),z=list(computation['z'])),dict(b,head=list(computation['joint']),z=list(computation['z']))
                    for name,old,new in ((chain.upper,a,new_a),(chain.lower,b,new_b)):
                        if any((Vector(old[k])-Vector(new[k])).length > (scale*1e-6 if k != 'z' else 1e-5) for k in ('head','tail','z')):
                            result['changes'][name] = new
                    if part == 'ARMS':
                        from . import body_calibration_hands as hands
                        # One candidate owns the entire arm. The wrist uses the
                        # proposed forearm frame, not its pre-Apply Roll.
                        frame = hands.forearm_reference(rig,chain,new_b)
                        result['wrists'].append(dict(frame,side=chain.side,chain=list(chain.names)))
                        if (Vector(c['z'])-Vector(frame['z'])).length > 1e-5:
                            result['changes'][chain.end] = dict(c,z=frame['z'])
                    computation.update(side=chain.side,start=list(s),original=list(e),end=list(w),chain=list(chain.names))
                    result['limbs'].append(computation)
            except ValueError as exc:
                result['errors'].append(chain.side+': '+str(exc))
    evidence['mappings'] = result['mappings']
    result['signature'] = _hash(evidence)
    return result


def status(context, rig, part):
    try:
        p = plan(context,rig,part)
        r = record(rig)
        if p['errors']: state,message = 'ERROR',' '.join(p['errors'])
        elif p['skipped']: state,message = 'SKIPPED','Excluded or no applicable anatomy.'
        elif r['confirmed'].get(part) == p['signature']: state,message = 'CONFIRMED','Checked and confirmed for these inputs.'
        elif not p['changes'] and r['applied'].get(part) == p['signature']: state,message = 'READY','Calibration applied. Confirm to continue.'
        elif part in r['confirmed']: state,message = 'REVIEW','Related inputs changed; preview, apply if needed, then confirm again.'
        else: state,message = 'UNSET','Preview and Apply Calibration before confirming.'
        return {'state':state,'message':message,'plan':p}
    except (ValueError, RuntimeError, KeyError, TypeError, IndexError) as exc:
        return {'state':'ERROR','message':str(exc)}


def preview(context, rig, part):
    if part == 'FINGERS':
        references = _validate_finger_references(rig)
        value = record(rig)
        value['finger_references'] = references
        save(rig,value)
    p = plan(context,rig,part)
    value = record(rig)
    value.setdefault('previews',{})[part] = p['signature']
    save(rig,value)
    return p


class CalibrationBlocked(ValueError):
    def __init__(self, issue):
        self.issue = issue
        super().__init__(issue['detail'])


def _affected_bones(rig, changes):
    affected = set(changes)
    for name in changes:
        bone = rig.data.bones[name]
        affected.update(b.name for b in bone.children_recursive)
        affected.update(b.name for b in bone.parent_recursive)
    return affected


def apply_readiness(context, rig, changes):
    """Cheap current-rig checks for the selected panel only; never flush Edit data."""
    def issue(code, message, detail, bones=()):
        return dict(code=code,message=message,detail=detail,bones=list(bones))
    if rig.library or rig.override_library or rig.data.library or rig.data.users != 1 or not rig.is_editable:
        return issue('DATA','Armature is shared or linked','Apply requires a local, editable, single-user armature.')
    if context.object != rig or context.mode not in {'OBJECT','POSE','EDIT_ARMATURE'}:
        return issue('CONTEXT','Select this armature','Make this character active in Object/Pose/Edit Mode.')
    if rig.mode == 'EDIT':
        if len(context.objects_in_mode) != 1:
            return issue('CONTEXT','Multiple armatures are in Edit Mode','Finish multi-object editing before Apply.')
        # Pose matrices cannot describe uncommitted EditBones. Apply flushes and rechecks.
        return None
    if not changes: return None
    from . import body_setup
    if body_setup.has_generated(rig):
        return issue('CONTROLS','Remove controls before recalibrating',
                     'Remove Generated Controls explicitly (keeps calibration), then recalibrate and rebuild.')
    affected = _affected_bones(rig,changes)
    constrained = sorted(n for n in affected if rig.pose.bones[n].constraints)
    if constrained:
        return issue('CONSTRAINT','Constraints protect this chain',
                     f'{constrained[0]}: existing constraint prevents safe Rest calibration.',constrained)
    if rig.constraints:
        return issue('CONSTRAINT','Armature has constraints','Armature object constraints prevent safe calibration.')
    if limb_ik._direct_chain_has_animation(rig,affected):
        return issue('ANIMATION','Animation protects this chain',
                     'Affected bones/ancestors have Action, NLA or driver inputs; keep their authored Rest axes.')
    non_rest = sorted(n for n in affected if not at_rest(rig,n))
    if non_rest:
        local_pose = sorted(n for n in non_rest if not limb_ik._matrix_basis_is_identity(rig.pose.bones[n],2e-5))
        if local_pose:
            names = local_pose[0] + (f' (+{len(local_pose)-1})' if len(local_pose)>1 else '')
            return issue('POSE','Pose blocks Apply',
                         f'{names}: posed bones depend on this arm/leg. Restore their pose transforms to Rest before Apply.',local_pose)
        return issue('EVALUATED_POSE','Evaluated pose differs from Rest',
                     f'{non_rest[0]}: inspect the parent pose or evaluated transforms before Apply.',non_rest)
    return None


def select_pose_blockers(context, rig, part):
    """Locate local pose inputs without clearing transforms or revealing hidden bones."""
    if rig.mode == 'EDIT': raise ValueError('Leave Edit Mode before selecting posed bones.')
    issue = apply_readiness(context,rig,plan(context,rig,part)['changes'])
    if not issue or issue['code'] != 'POSE': raise ValueError('No local pose blocker to select; Preview again.')
    bones = [rig.data.bones[n] for n in issue['bones']]
    hidden = [b.name for b in bones if b.hide or b.hide_select or
              (b.collections and not any(col.is_visible_effectively for col in b.collections))]
    if hidden: raise ValueError(f'{hidden[0]}: show/unlock this bone first; its pose was not changed.')
    snapshot = limb_ik._capture_context(context,rig)
    try:
        if rig.mode != 'POSE':
            if not rig.select_get() or context.view_layer.objects.active != rig:
                raise ValueError('Select this armature before locating posed bones.')
            import bpy
            if bpy.ops.object.mode_set(mode='POSE') != {'FINISHED'}: raise ValueError('Could not enter Pose Mode.')
        rig.data.bones.active = bones[0]
        names = set(issue['bones'])
        for pb in rig.pose.bones: pb.select = pb.name in names
    except Exception:
        limb_ik._restore_context(context,rig,snapshot)
        raise
    return issue['bones']


def _guard_apply(context, rig, changes):
    issue = apply_readiness(context,rig,changes)
    if issue: raise CalibrationBlocked(issue)
    if not changes: return
    affected = _affected_bones(rig,changes)
    import bpy
    for obj in bpy.data.objects:
        if obj.parent == rig and obj.parent_type == 'BONE' and obj.parent_bone in affected:
            raise ValueError(f'{obj.name}: bone-parented attachment depends on the affected Rest frame.')
        constraints = list(obj.constraints)
        if obj.type == 'ARMATURE':
            constraints += [con for pb in obj.pose.bones for con in pb.constraints]
        for con in constraints:
            if limb_ik._constraint_references_controls(con,rig,affected):
                raise ValueError(f'{obj.name}: external constraint {con.name} depends on the affected chain.')
        for mod in obj.modifiers:
            for object_field,bone_field in (('object','subtarget'),('object_from','bone_from'),('object_to','bone_to')):
                if getattr(mod,object_field,None) == rig and getattr(mod,bone_field,'') in affected:
                    raise ValueError(f'{obj.name}: modifier {mod.name} depends on an affected bone frame.')
        for owner in (obj,obj.data,getattr(obj.data,'shape_keys',None)):
            if owner is None: continue
            ad = getattr(owner,'animation_data',None)
            for curve in getattr(ad,'drivers',()):
                if owner == rig.data and limb_ik._path_mentions_bone(curve.data_path,affected):
                    raise ValueError(f'{obj.name}: an armature data driver writes an affected bone.')
                for variable in curve.driver.variables:
                    for t in variable.targets:
                        if t.id in (rig,rig.data) and (getattr(t,'bone_target','') in affected or
                                limb_ik._path_mentions_bone(getattr(t,'data_path',''),affected)):
                            raise ValueError(f'{obj.name}: an external driver reads the affected chain.')
    for action in limb_ik._actions_for_id(rig.data):
        if any(limb_ik._path_mentions_bone(curve.data_path,affected) for curve in limb_ik._fcurves_for_action(action)):
            raise ValueError(f'{action.name}: animation writes affected Armature data.')
    # Connected side branches would be moved implicitly by Blender EditBone writes.
    for name,new in changes.items():
        bone = rig.data.bones[name]
        if (bone.tail_local-Vector(new['tail'])).length > bone.length*1e-6:
            for child in bone.children:
                if child.use_connect and child.name not in changes:
                    raise ValueError(f'{child.name}: connected side branch would move; adjust that connection explicitly.')


def apply(context, rig, part, *, acknowledge=False):
    from . import body_setup
    if part == 'FINGERS': _validate_finger_references(rig)
    if rig.mode == 'EDIT' and len(context.objects_in_mode) != 1:
        raise ValueError('Finish multi-object editing before Apply.')
    p = plan(context,rig,part)
    r = record(rig)
    if p['errors']: raise ValueError(' '.join(p['errors']))
    if r.get('previews',{}).get(part) != p['signature']:
        raise ValueError('Preview is stale or missing. Preview the current candidate before Apply.')
    if p['needs_acknowledgment'] and not acknowledge:
        raise ValueError('Candidate exceeds suggested micro-adjustment range; explicitly acknowledge its visible displacement.')
    original_context = limb_ik._capture_context(context,rig)
    try:
        if rig.mode == 'EDIT': limb_ik._mode_set(context,rig,'OBJECT')
        # Evaluate the same plan again after flushing edit data; never use stale data bones.
        current = plan(context,rig,part)
        if current['signature'] != p['signature']: raise ValueError('Edit baseline changed; preview again outside Edit Mode.')
        _guard_apply(context,rig,p['changes'])
        before = native_rest(rig)
        def commit():
            if p['changes']:
                mirror = rig.data.use_mirror_x
                try:
                    rig.data.use_mirror_x = False
                    limb_ik._mode_set(context,rig,'EDIT')
                    for name in p['changes']: rig.data.edit_bones[name].use_connect = False
                    for name,new in p['changes'].items():
                        b = rig.data.edit_bones[name]
                        b.head,b.tail = new['head'],new['tail']
                        b.align_roll(Vector(new['z']))
                    for name,new in p['changes'].items(): rig.data.edit_bones[name].use_connect = new['use_connect']
                    limb_ik._mode_set(context,rig,'OBJECT')
                finally:
                    rig.data.use_mirror_x = mirror
                context.view_layer.update()
                verify_rest(rig,before,allowed=p['changes'])
                for name,new in p['changes'].items(): verify_rest(rig,{name:new})
            value = record(rig)
            value['mappings'].update(p['mappings'])
            if not body_setup.has_generated(rig):
                # Ownership begins with Apply, before any Advanced builder is reachable.
                value['direct'] = True
            save(rig,value)
            after = plan(context,rig,part)
            if after['errors'] or after['changes']:
                detail = ' '.join(after['errors']) or 'Remaining changes: '+', '.join(after['changes'])+'.'
                raise ValueError('Calibration was rolled back after recheck. '+detail)
            value['applied'][part] = after['signature']
            value.setdefault('history',{})[part] = {'before':before, 'after':after['signature']}
            value.setdefault('previews',{})[part] = after['signature']
            save(rig,value)
            return after
        return body_setup._atomic(context,rig,commit)
    finally:
        limb_ik._restore_context(context,rig,original_context)


def confirm(context, rig, part):
    if part == 'FINGERS': _validate_finger_references(rig)
    p = plan(context,rig,part)
    r = record(rig)
    if p['errors'] or p['changes'] or (not p['skipped'] and r['applied'].get(part) != p['signature']):
        raise ValueError('Checks are not applied/current; preview and apply this part first. ' + ' '.join(p['errors']))
    r['confirmed'][part] = p['signature']
    save(rig,r)


def require_confirmed(context, rig):
    for part in PARTS:
        item = status(context,rig,part)
        if item['state'] not in {'CONFIRMED','SKIPPED'}:
            raise ValueError(part.title()+': '+item['message'])


def finger_setup_status(context, rig):
    """Reuse saved Finger confirmations, without auditing mesh geometry/Roll."""
    from . import character_setup, finger_targets, finger_bank_ui, finger_detect
    valid = {digit+'.'+side for digit in finger_detect.DIGITS for side in limb_ik.SIDES}
    required = set(finger_targets.index(rig)) & valid
    setup = character_setup.settings(context)
    explicit = setup.body if setup and setup.rig == rig else None
    owners = []
    for obj in context.scene.objects:
        if obj.type != 'MESH': continue
        bound = {m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object}
        bank = getattr(obj, 'character_designer_finger_bank', None)
        if bank is None or not (bank.survey or bank.slots): continue
        if rig in bound or (obj == explicit and not bound): owners.append(obj)
    if explicit in owners: owners = [explicit]
    if any({m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object} - {rig}
           for obj in owners):
        return {'state':'REVIEW','message':'Finger mesh has multiple armatures.'}
    if len(owners) > 1:
        return {'state':'REVIEW','message':'Choose Body in Character Setup.'}
    if not owners:
        return {'state':'UNSET' if required else 'SKIPPED',
                'message':'Capture the fingers in Fingers.' if required else 'No recognized finger setup.'}
    obj = owners[0]; bank = obj.character_designer_finger_bank
    # Capture preallocates ten slots, including absent fingers/opposite hands.
    # Only actual saved work adds anatomy beyond the native/survey keys.
    required.update(slot.name for slot in bank.slots if slot.name in valid
                    and (slot.guide.record or slot.guide.pending))
    try:
        # Share the original Finger panel's bounded JSON cache. No BMesh or
        # vertex coordinates are read by this checklist query.
        survey = finger_bank_ui._parsed(bank.survey) if bank.survey else {}
        required.update(key for key in survey.get('candidates', {}) if key in valid)
        if not required:
            return {'state':'SKIPPED','message':'No recognized finger setup.'}
        for key in sorted(required):
            slot = bank.slots.get(key)
            if not slot or not slot.guide.record:
                return {'state':'UNSET','message':'Finish the existing Finger captures.'}
            guide = slot.guide
            if not guide.confirmed or guide.pending or guide.source != obj:
                return {'state':'REVIEW','message':'Finish the existing Finger confirmation.'}
            saved = finger_bank_ui._parsed(guide.record)
            if not saved.get('internal') or saved.get('bank_key') != key:
                return {'state':'REVIEW','message':'Review the existing Finger capture.'}
    except (ValueError, TypeError, AttributeError):
        return {'state':'REVIEW','message':'Review the existing Finger capture.'}
    return {'state':'CONFIRMED','message':'Existing Finger captures are confirmed.'}


def generation_ready(context, rig, items=None, fingers=None):
    """First-use UI checklist; legacy generated controls remain accessible."""
    items = items if items is not None else {part:status(context,rig,part) for part in PARTS}
    fingers = fingers if fingers is not None else finger_setup_status(context,rig)
    return all(item['state'] in {'CONFIRMED','SKIPPED'} for item in (*items.values(),fingers))


def _validate_finger_references(rig):
    """Explicit actions own mesh validation; drawing never creates a BMesh."""
    import bpy
    from . import finger_targets, finger_definition
    if not settings(rig).include_fingers: return {}
    wanted = finger_targets.index(rig)
    if not wanted: return {}
    result = {}
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or not any(m.type == 'ARMATURE' and m.object == rig for m in obj.modifiers): continue
        bank = getattr(obj,'character_designer_finger_bank',None)
        if bank is None or not bank.survey: continue
        with finger_definition.FrameReader() as reader:
            for slot in bank.slots:
                if slot.name in wanted and slot.guide.record:
                    resolved = finger_definition._validate_mesh(obj,json.loads(slot.guide.record),True,reader=reader)
                    result[slot.name] = {'mesh':obj.name, 'source':_hash(slot.guide.record), 'record':resolved}
    return result


def calibrated(rig):
    return bool(record(rig).get('direct'))


def at_rest(rig, name):
    """Matrix comparison tolerates native float round trips without clearing pose."""
    pb = rig.pose.bones[name]
    skin = pb.matrix @ rig.data.bones[name].matrix_local.inverted()
    return (not pb.constraints and limb_ik._matrix_basis_is_identity(pb,2e-5)
            and limb_ik._matrix_is_identity(skin,2e-5))


def uses_workflow(rig, inventory=None):
    inventory = inventory or limb_ik._validate_inventory(rig)
    return calibrated(rig) or not inventory['rigs']


def register_direct_rest(context, rig, plans, transaction):
    """Reuse schema 5 with provenance; original == applied == confirmed Rest.

    Old schema-5 characters without this provenance still use their old builder.
    The pre-calibration snapshot belongs exclusively to Apply/Undo, never Remove.
    """
    # _build_plans checks confirmations before starting the ownership transaction.
    # Its temporary schema/feature tags are intentionally incomplete at this point.
    registry = limb_ik._load_direct_rest_registry(rig)
    transaction['direct_rest_before'] = rig.data.get(limb_ik.DIRECT_REST_KEY)
    transaction['direct_rest_touched'] = True
    prepared = []
    for p in plans:
        s,e,w,bend = direction(rig,p.chain)
        if bend is None: raise ValueError('Confirmed chain no longer has a stable bend.')
        requested = limb_ik._project_perpendicular(p.pole_direction,w-s).normalized()
        if requested.dot(bend) < 1-1e-6:
            raise ValueError('Generate/Rebuild cannot change the confirmed bend plane. Remove controls, then use Setup > Apply.')
        states = {}
        for name in p.chain.names[:2]:
            state = rest(rig,name)
            state.pop('use_deform')
            state['roll'] = limb_ik._roll_for_axes(Vector(state['tail'])-Vector(state['head']),Vector(state['z']))
            states[name] = state
        registry['limbs'][p.rig_id] = {'kind':p.chain.kind,'side':p.chain.side,'chain':list(p.chain.names),
                                      'original':states,'applied':states}
        prepared.append(limb_ik._direct_arm_target_frame(rig,p))
    limb_ik._write_direct_rest_registry(rig,registry)
    return prepared
