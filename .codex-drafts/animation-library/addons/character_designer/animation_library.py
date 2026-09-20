"""Persistent collections of source packets and separately cached target Actions."""
import hashlib
import json
import math
from pathlib import Path
import uuid

import bpy
from mathutils import Matrix
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import PropertyGroup

from . import unity_animation as ua, unity_animation_adapter as adapter, animation_retarget as curves

OWNER = 'character_designer_animation_library'


class CDAnimationResult(PropertyGroup):
    target: PointerProperty(type=bpy.types.Object)
    action: PointerProperty(type=bpy.types.Action)
    signature: StringProperty()
    original_hash: StringProperty()
    status: StringProperty()


class CDAnimationClip(PropertyGroup):
    source_id: StringProperty()
    revision: StringProperty()
    source_name: StringProperty()
    packet: PointerProperty(type=bpy.types.Text)
    selected: BoolProperty(default=True)
    status: StringProperty()
    results: CollectionProperty(type=CDAnimationResult)


class CDAnimationCollection(PropertyGroup):
    source_id: StringProperty()
    clips: CollectionProperty(type=CDAnimationClip)
    active: IntProperty(default=0)


class CDAnimationLibrary(PropertyGroup):
    collections: CollectionProperty(type=CDAnimationCollection)
    active: IntProperty(default=0)
    target: PointerProperty(type=bpy.types.Object, poll=lambda self,obj:obj.type=='ARMATURE')
    folder: StringProperty(name='Exchange Folder', subtype='DIR_PATH')
    status: StringProperty()
    session_target: PointerProperty(type=bpy.types.Object)
    session_action: PointerProperty(type=bpy.types.Action)
    previous_action: PointerProperty(type=bpy.types.Action)
    session: StringProperty()
    session_clip: StringProperty()


CLASSES=(CDAnimationResult,CDAnimationClip,CDAnimationCollection,CDAnimationLibrary)


def state(context):return context.scene.character_designer_animation_library


def current(context):
    s=state(context)
    c=s.collections[s.active] if 0<=s.active<len(s.collections) else None
    return c,c.clips[c.active] if c and 0<=c.active<len(c.clips) else None


def target(context):
    from . import character_setup
    return state(context).target or character_setup.preferred_rig(context)


def action_hash(action):
    data=[(f.data_path,f.array_index,f.mute,f.extrapolation,
           [(tuple(p.co),tuple(p.handle_left),tuple(p.handle_right),p.interpolation,p.handle_left_type,p.handle_right_type)
            for p in f.keyframe_points],[(m.type,repr([(p.identifier,repr(getattr(m,p.identifier)))
                for p in m.bl_rna.properties if not p.is_readonly and p.type!='COLLECTION'])) for m in f.modifiers])
          for f in curves._curves(action)]
    return hashlib.sha256(repr(data).encode()).hexdigest()


def identity(data):
    project=data.get('projectId','legacy')
    source=data.get('sourceGuid') or data.get('targetGuid') or data.get('targetAssetPath')
    clip=data.get('clipGuid')
    if source and clip:
        return f"{project}:{source}:{clip}:{data.get('clipLocalId',0)}"
    # Old packets without asset IDs remain usable; their exact origin path is
    # part of identity, never a bare display name shared by unrelated motions.
    return 'legacy:'+hashlib.sha256(str(data['_path']).encode()).hexdigest()


def revision(data):
    payload={k:v for k,v in data.items() if k not in {'_path','exportedUtc'}}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def read(item):
    if not item.packet:raise ua.UnityAnimationError('Embedded source packet is missing; add its source file again.')
    text=item.packet.as_string()
    if len(text)>256*1024*1024:raise ua.UnityAnimationError('Embedded animation packet exceeds 256 MB.')
    data=ua.validate_package(json.loads(text),item.packet.get('origin','Embedded packet'))
    if revision(data)!=item.revision or identity(data)!=item.source_id:
        raise ua.UnityAnimationError('Embedded source data changed; add the new source as a revision instead.')
    return data


def add_collection(context,name='Motion Collection',source_id=''):
    c=state(context).collections.add();c.name=name;c.source_id=source_id or uuid.uuid4().hex
    state(context).active=len(state(context).collections)-1
    return c


def add_files(context,paths,collection=None):
    """Validate the complete requested input first; preserve prior versions."""
    packets=[];manifest_name=None;manifest_id=None
    for path in paths:
        path=Path(bpy.path.abspath(str(path))).resolve()
        if path.name.endswith('.cdcollection.json'):
            manifest=json.loads(path.read_text(encoding='utf-8-sig'))
            if manifest.get('schema')!='cdesigner.animation-collection/1':raise ua.UnityAnimationError('Unsupported collection manifest.')
            manifest_name=manifest.get('name','Unity Collection');manifest_id=manifest.get('id')
            for entry in manifest['clips']:
                child=(path.parent/entry['file']).resolve()
                if not child.is_relative_to(path.parent):raise ua.UnityAnimationError('Collection file escapes its exchange folder.')
                data=ua.load_package(child)
                if entry.get('sha256') and hashlib.sha256(child.read_bytes()).hexdigest()!=entry['sha256']:
                    raise ua.UnityAnimationError('Collection packet checksum mismatch: '+child.name)
                packets.append(data)
        else:packets.append(ua.load_package(path))
    if not packets:raise ua.UnityAnimationError('No animation packets were selected.')
    s=state(context)
    if collection is None and manifest_id:
        collection=next((c for c in s.collections if c.source_id==manifest_id),None)
    if collection is None:collection=add_collection(context,manifest_name or 'Unity Motions',manifest_id or '')
    added=[]
    for data in packets:
        uid,rev=identity(data),revision(data)
        existing=next((c for c in collection.clips if c.source_id==uid and c.revision==rev),None)
        if existing:continue
        packet=next((t for t in bpy.data.texts if t.get(OWNER)==1 and t.get('source_id')==uid and t.get('revision')==rev),None)
        if packet is None:
            packet=bpy.data.texts.new('Unity Source · '+str(data.get('clipName','Animation')))
            packet[OWNER]=1;packet['source_id']=uid;packet['revision']=rev;packet['origin']=data['_path']
            packet.write(json.dumps({k:v for k,v in data.items() if k!='_path'},separators=(',',':')))
            packet.use_fake_user=True
        item=collection.clips.add();item.name=str(data.get('clipName','Animation'))
        item.source_id=uid;item.revision=rev;item.packet=packet
        item.source_name=str(data.get('sourceName') or data.get('targetName') or 'Unity')
        if any(c!=item and c.source_id==uid for c in collection.clips):item.name+=' · update '+rev[:6]
        item.status='Source ready · import for target';added.append(item)
    s.status=f'Added {len(added)} source actions; existing versions retained. Import Selected prepares target Actions without playing.'
    return added


def result_key(context,rig,item):
    return hashlib.sha256(repr((item.revision,adapter.rig_signature(rig),
        context.scene.render.fps,context.scene.render.fps_base,context.scene.unit_settings.scale_length)).encode()).hexdigest()


def ready_result(context,rig,item):
    signature=result_key(context,rig,item)
    return next((r for r in reversed(item.results) if r.target==rig and r.signature==signature and r.action),None)


def import_selected(context,collection=None,items=None):
    rig=target(context)
    if rig is None:raise ua.UnityAnimationError('Set Main Rig in Character Setup or choose a target character.')
    collection=collection or current(context)[0]
    if collection is None:raise ua.UnityAnimationError('Add a motion collection first.')
    items=list(items) if items is not None else [i for i in collection.clips if i.selected]
    if not items:raise ua.UnityAnimationError('Select source actions to import.')
    successes=[];failures=[]
    for item in items:
        try:
            found=ready_result(context,rig,item)
            if found:
                item.status='Ready · edited Action preserved' if action_hash(found.action)!=found.original_hash else 'Ready · already imported'
                successes.append(found.action);continue
            data=read(item);action=adapter.bake(context,rig,data)
            result=item.results.add();result.target=rig;result.action=action
            result.signature=result_key(context,rig,item);result.original_hash=action_hash(action)
            result.status=action['unity_compatibility'];action[OWNER]=1
            action['unity_source_id']=item.source_id;action['unity_source_revision']=item.revision
            action['unity_target']=rig;item.status='Ready · '+rig.name
            successes.append(action)
        except Exception as exc:
            item.status=str(exc);failures.append(item.name+': '+str(exc))
    state(context).status=f'{len(successes)} ready; {len(failures)} incompatible. Current animation unchanged.'
    return successes,failures


def _snapshot(context,rig):
    saved=ua._snapshot(context,rig)
    saved['properties']={pb.name:{k:v for k,v in pb.items() if isinstance(v,(int,float,bool))} for pb in rig.pose.bones}
    return saved


def _restore(context,rig,saved,previous):
    for n,values in saved.get('properties',{}).items():
        pb=rig.pose.bones.get(n)
        if pb:
            for k,v in values.items():pb[k]=v
    rig.update_tag(refresh={'OBJECT'})
    ua._restore_snapshot(context,rig,saved,previous)


def restore(context):
    s=state(context);rig=s.session_target
    if not s.session:return
    if not rig:raise ua.UnityAnimationError('The preview target is missing; recovery record retained.')
    if not rig.animation_data or rig.animation_data.action!=s.session_action:
        raise ua.UnityAnimationError('Current Action was changed outside the library; restore its preview Action before ending this session.')
    saved=json.loads(s.session)
    if saved.get('rig_signature')!=adapter.rig_signature(rig):
        raise ua.UnityAnimationError('Rig structure or control graph changed during preview; recovery record retained. Restore that change before ending the preview.')
    if saved['had_action'] and not s.previous_action:raise ua.UnityAnimationError('Previous Action was deleted; recovery record retained.')
    if any(n not in rig.pose.bones for n in saved['pose']):raise ua.UnityAnimationError('Rig bones changed during preview; recovery record retained.')
    if s.previous_action and s.previous_action.is_action_layered and not any(x.handle==saved['slot'] for x in s.previous_action.slots):
        raise ua.UnityAnimationError('Previous Action slot was removed; recovery record retained.')
    before=_snapshot(context,rig);active=s.session_action
    try:_restore(context,rig,saved,s.previous_action)
    except Exception:
        _restore(context,rig,before,active);raise
    s.session='';s.session_target=None;s.session_action=None;s.previous_action=None;s.session_clip=''
    s.status='Original Action, pose, timeline and binding controls restored. Library Actions retained.'


def use(context,item=None):
    s=state(context);item=item or current(context)[1];rig=target(context)
    if not item or not rig:raise ua.UnityAnimationError('Choose a source action and a target character.')
    if ua.active_preview(rig):raise ua.UnityAnimationError('Restore the older Unity test preview before using the action library.')
    result=ready_result(context,rig,item)
    if not result:
        # A source remains reusable across targets; never apply another target's cache.
        data=read(item);action=adapter.bake(context,rig,data)
        result=item.results.add();result.target=rig;result.action=action;result.signature=result_key(context,rig,item)
        result.original_hash=action_hash(action);result.status=action['unity_compatibility'];action[OWNER]=1
        action['unity_source_id']=item.source_id;action['unity_source_revision']=item.revision;action['unity_target']=rig
    action=result.action
    if s.session and s.session_target!=rig:restore(context)
    if s.session and (not rig.animation_data or rig.animation_data.action!=s.session_action):
        raise ua.UnityAnimationError('Current Action changed outside the library; existing state was left untouched.')
    before=_snapshot(context,rig);previous=rig.animation_data.action if rig.animation_data else None
    new_session=not s.session
    try:
        ua._set_playing(context,False)
        ad=rig.animation_data_create();ad.action=None;ad.use_nla=False
        for pb in rig.pose.bones:pb.matrix_basis=Matrix.Identity(4)
        ad.action=action;ad.action_slot=action.slots[0];ad.action_blend_type='REPLACE';ad.action_influence=1.;ad.action_extrapolation='HOLD'
        scene=context.scene;scene.frame_start=scene.frame_preview_start=1
        end=max(1,math.ceil(1+action['unity_duration']*scene.render.fps/scene.render.fps_base))
        scene.frame_end=scene.frame_preview_end=end;scene.use_preview_range=True
        ua._set_frame(scene,1);context.view_layer.update()
        if new_session:
            saved=dict(before,rig_signature=adapter.rig_signature(rig))
            s.session=json.dumps(saved);s.previous_action=previous;s.session_target=rig
        s.session_action=action;s.session_clip=item.source_id+':'+item.revision
        ua._set_playing(context,before['playing'])
        s.status='Editing '+action.name+' · Restore ends the session without deleting edits.'
    except Exception:
        _restore(context,rig,before,previous);raise
    return action


def remove_clip(context,collection=None,index=None):
    c=collection or current(context)[0]
    if c is None:return
    index=c.active if index is None else index
    if not 0<=index<len(c.clips):return
    item=c.clips[index];s=state(context)
    if s.session_clip==item.source_id+':'+item.revision:restore(context)
    c.clips.remove(index);c.active=max(0,min(c.active,len(c.clips)-1))
    s.status='Removed from collection only; Unity assets, source packets and existing Actions retained.'
