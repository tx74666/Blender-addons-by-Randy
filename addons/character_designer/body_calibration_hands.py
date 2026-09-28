"""Paired wrist references; queries never edit bones or infer mesh topology."""
import math
from mathutils import Vector, Quaternion
from . import body_calibration as calibration, limb_ik
from .finger_symmetry import reflect, validate_pair


def mode(rig):
    choice = calibration.settings(rig).palm_reference
    if choice == 'AUTO':
        return 'MESH' if calibration.record(rig)['palms'] else 'PLANE'
    return choice


def pair(context, rig):
    chains = {c.side:c for c in calibration.chains(context,rig,'ARM')}
    if set(chains) != {'L','R'}:
        raise ValueError('Linked hands need both existing hand chains; no counterpart will be created.')
    bones = rig.data.edit_bones if rig.mode == 'EDIT' else rig.data.bones
    validate_pair(bones,bones[chains['L'].end],bones[chains['R'].end])
    return chains


def linked(rig):
    """Old independently saved references remain usable until explicitly replaced."""
    if mode(rig)=='PLANE':return True
    refs=calibration.record(rig)['palms']
    left,right=refs.get('L',{}),refs.get('R',{})
    source=left.get('source_side')
    return (source in {'L','R'} and right.get('source_side')==source
            and left.get('mirror')==(source!='L') and right.get('mirror')==(source!='R')
            and all(key in left and key in right and left[key]==right[key] for key in ('evidence','fingerprint','flip')))


def pending_reference(context,rig):
    settings=calibration.settings(rig)
    if settings.palm_reference!='MESH':return False
    if settings.palm_object is None:
        if not linked(rig):raise ValueError('Choose one palm mesh and Use for Both Hands to link the hands.')
        return False
    references,_,_,_=picked_reference(context,rig,settings.palm_object,settings.palm_face,settings.palm_flip)
    return calibration._hash(references)!=calibration._hash(calibration.record(rig)['palms'])


def reference(rig, chain):
    state = calibration.rest(rig,chain.end)
    head,tail = Vector(state['head']),Vector(state['tail'])
    y = (tail-head).normalized()
    if mode(rig) == 'PLANE':
        # Tilt the horizontal reference into the hand's existing A-pose span.
        # This retains the long axis; Roll alone cannot change that axis.
        normal = limb_ik._project_perpendicular(Vector((0,0,-1)),y)
        if normal.length < 1e-4:
            raise ValueError('Hand points vertically; choose a mesh palm reference for its plane.')
        settings = calibration.settings(rig)
        angle = settings.palm_tilt * (1 if chain.side == 'L' else -1)
        normal = Quaternion(y,angle) @ normal.normalized()
        if settings.palm_plane_flip: normal.negate()
        center = (head+tail)*.5
        proof = {'reference':'APOSE_PLANE','tilt':settings.palm_tilt,'flip':settings.palm_plane_flip}
    else:
        normal,center,proof = calibration.palm(rig,chain.side)
    return frame_from_reference(rig,chain,normal,center,proof)


def forearm_reference(rig, chain, forearm):
    """Continue the candidate forearm's axes at the authored wrist position.

    Hand head/tail define its longitudinal Y axis and are never moved. Only
    Roll is calibrated: project forearm Z onto the hand's perpendicular plane.
    Historical palm references are deliberately neither read nor overwritten.
    """
    frame = frame_from_reference(rig,chain,-Vector(forearm['z']),
                                 Vector(forearm['tail']),{'reference':'FOREARM'})
    twist = (Vector(forearm['tail'])-Vector(forearm['head'])).normalized()
    frame.update(forearm_axis=list(twist),forearm_start=forearm['head'],
                 axis_difference_degrees=math.degrees(Vector(frame['y']).angle(twist)))
    return frame


def frame_from_reference(rig, chain, normal, center, proof):
    """Shared read-only frame for saved calibration and an unaccepted mesh pick."""
    state = calibration.rest(rig,chain.end)
    head,tail = Vector(state['head']),Vector(state['tail'])
    y = (tail-head).normalized()
    projected = limb_ik._project_perpendicular(normal,y)
    if projected.length < 1e-4:
        raise ValueError('Palm reference is parallel to the hand axis; choose another plane.')
    z = -projected.normalized()
    x = y.cross(z).normalized()
    forearm = calibration.rest(rig,chain.lower)
    twist = (Vector(forearm['tail'])-Vector(forearm['head'])).normalized()
    return {'palm':list(-z),'center':list(center),'origin':list((head+tail)*.5),
            'x':list(x),'y':list(y),'z':list(z),'proof':proof,
            'forearm_axis':list(twist),'wrist':list(head),'forearm_start':forearm['head'],
            'axis_difference_degrees':math.degrees(y.angle(twist))}


def picked_reference(context,rig,obj,face,flip):
    """Validate the one face being previewed, without saving either side."""
    chains = pair(context,rig)
    normal,center,evidence = calibration._palm_geometry(rig,obj,face)
    distances = []
    for side,chain in chains.items():
        state=calibration.rest(rig,chain.end)
        head,tail=Vector(state['head']),Vector(state['tail'])
        distances.append(((center-(head+tail)*.5).length,side,(tail-head).length))
    distances.sort()
    near,far=distances
    if near[0] > near[2]*2 or far[0]-near[0] < max(near[2]*.1,1e-6):
        raise ValueError('Choose a palm face near one hand; its side could not be identified reliably.')
    source=near[1]
    references={}
    for side in ('L','R'):
        references[side]={'evidence':evidence,'fingerprint':calibration._hash(evidence),
                          'flip':bool(flip),'mirror':side!=source,'source_side':source}
        candidate=reflect(normal) if side!=source else normal
        state=calibration.rest(rig,chains[side].end)
        if limb_ik._project_perpendicular(candidate,Vector(state['tail'])-Vector(state['head'])).length<1e-4:
            raise ValueError('This reference cannot define both hand planes.')
    return references,normal*(-1 if flip else 1),center,source


def capture_pair(context,rig,obj,face,flip,acknowledged):
    """One explicitly picked palm drives both references about Armature Local X."""
    if not acknowledged: raise ValueError('Check the wrist axes before using this reference for both hands.')
    references,_,_,_=picked_reference(context,rig,obj,face,flip)
    value=calibration.record(rig)
    value['palms']=references
    calibration.save(rig,value)
    calibration.settings(rig).palm_reference='MESH'


def context_rig(context):
    """Follow the active character, including its bound mesh in Edit Mode."""
    obj=context.edit_object if context.mode=='EDIT_MESH' else context.object
    if obj is None: return None
    if obj.type=='ARMATURE': return obj
    if obj.type!='MESH': return None
    rigs={m.object for m in obj.modifiers if m.type=='ARMATURE' and m.object is not None}
    if len(rigs)==1: return next(iter(rigs))
    if rigs: return None
    from . import character_setup
    setup=character_setup.settings(context)
    return setup.rig if setup and setup.body==obj else None


def fingers_visible(context):
    rig=context_rig(context)
    # An unbound mesh still needs initial Capture/Basic Setup access.
    if rig is None:
        obj=context.edit_object if context.mode=='EDIT_MESH' else context.object
        return not (obj and obj.type=='MESH' and any(m.type=='ARMATURE' and m.object for m in obj.modifiers))
    settings=calibration.settings(rig)
    return settings.part=='FINGERS'
