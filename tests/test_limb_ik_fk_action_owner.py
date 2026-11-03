"""Pure ID/row ownership transactions; native geometry/keying tested separately."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS,ModuleType
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'addons/character_designer/limb_ik_fk_action_owner.py'
spec=importlib.util.spec_from_file_location('ikfk_owner_under_test',SOURCE)
A=importlib.util.module_from_spec(spec);spec.loader.exec_module(A)


class ID:
 __hash__=object.__hash__
 __eq__=object.__eq__
 __ne__=object.__ne__
 def __init__(self,name,**fields):
  self.properties={};self.name=name;self.library=None;self.asset_data=None
  self.override_library=None;self.is_editable=True;self.__dict__.update(fields)
 def __bool__(self):return True
 def __getitem__(self,key):return self.properties[key]
 def __setitem__(self,key,value):self.properties[key]=value
 def __contains__(self,key):return key in self.properties
 def __iter__(self):return iter(self.properties)
 def __len__(self):return len(self.properties)
 def get(self,key,default=None):return self.properties.get(key,default)
 def keys(self):return self.properties.keys()
 def values(self):return self.properties.values()
 def items(self):return self.properties.items()
 def pop(self,key,default=None):return self.properties.pop(key,default)
 def update(self,value):self.properties.update(dict(value))
 @property
 def name_full(self):return self.name
 def as_pointer(self):return id(self)

class Action(ID):
 def __init__(self,name,db):
  super().__init__(name);self.db=db;self.use_fake_user=False;self.curves=[('pose','loc',1.,2.)]
  self.slots=[NS(handle=1,target_id_type='OBJECT',identifier='OBRig')]
 @property
 def users(self):return self.db.users(self)
 def copy(self):
  result=self.db.new(self.name+'.copy');result.update(self)
  result.curves=copy.deepcopy(self.curves);result.slots=copy.deepcopy(self.slots)
  result.use_fake_user=self.use_fake_user;return result

class Actions(list):
 def __init__(self):super().__init__();self.rig=None;self.saved=None;self.foreign=[]
 def new(self,name):
  action=Action(name,self);self.append(action);return action
 def remove(self,action):
  if action.users:raise ValueError('Native ID still has users')
  super().remove(action)
 def ids(self,value):
  if isinstance(value,ID):yield value
  elif isinstance(value,dict):
   for item in value.values():yield from self.ids(item)
  elif isinstance(value,(tuple,list)):
   for item in value:yield from self.ids(item)
 def users(self,value):
  count=int(value.use_fake_user)
  count+=int(self.rig.animation_data.action is value)
  count+=sum(item is value for item in self.ids(dict(self.rig)))
  count+=sum(item is value for action in self for item in self.ids(dict(action)))
  count+=sum(row.get(k) is value for row in self.saved.items.records for k in ('custom_action','source_action'))
  count+=sum(item is value for item in self.foreign)
  return count

class ExpiredRow(RuntimeError):pass
class Row:
 def __init__(self,rows,index):
  object.__setattr__(self,'rows',rows);object.__setattr__(self,'index',index);object.__setattr__(self,'epoch',rows.epoch)
 def record(self):
  if self.epoch!=self.rows.epoch:
   self.rows.expired_reads+=1;raise ExpiredRow('CollectionProperty mutation expired old row')
  return self.rows.records[self.index]
 def __getattr__(self,key):return self.record()[key]
 def __setattr__(self,key,value):self.record()[key]=value

class Rows:
 def __init__(self,records):self.records=records;self.epoch=0;self.expired_reads=0
 def __len__(self):return len(self.records)
 def __iter__(self):return (Row(self,i) for i in range(len(self.records)))
 def __getitem__(self,index):return Row(self,index)
 def add(self):
  self.epoch+=1;self.records.append({'item_id':'','custom_action':None});return self[-1]
 def remove(self,index):self.epoch+=1;self.records.pop(index)

class Objects(dict):pass

class NativeCollection(ID):
 def __init__(self,name,bones):super().__init__(name,parent=None,bones=list(bones))
 def unassign(self,bone):self.bones.remove(bone)
 def assign(self,bone):self.bones.append(bone)


class AnimationBinding:
 """Binding another Action defers native key evaluation until an update."""
 def __init__(self,rig,action):
  self.rig=rig;self._action=action;self.pending=False;self.action_slot=action.slots[0]
  self.use_tweak_mode=False;self.use_nla=False;self.nla_tracks=[]
  self.action_blend_type='REPLACE';self.action_influence=1.
 @property
 def action(self):return self._action
 @action.setter
 def action(self,value):
  if value is not self._action:self._action=value;self.pending=True
 def evaluate(self):
  if self.pending:self.rig.pose_marker='keyed input';self.pending=False


class OwnerTests(unittest.TestCase):
 def setUp(self):
  self.db=Actions();self.parent=self.db.new('Original Custom');self.parent.use_fake_user=True
  self.source=self.db.new('Linked Source');self.source.library=ID('Source library')
  self.cp=self.db.new('Original checkpoint');self.cp.use_fake_user=True
  self.data=ID('Armature');self.rig=ID('Rig',data=self.data,type='ARMATURE',pose_marker='original')
  self.collection=NativeCollection('Body',[ID('BoneA'),ID('BoneB'),ID('BoneC')])
  self.data.collections_all=[self.collection]
  self.groups=ModuleType('_owner_pure_native.bone_collections')
  self.groups._FRAME_CACHE={self.rig.as_pointer():('current','FK')}
  self.groups._FRAME_OWNER_REJECTIONS={self.rig.as_pointer():('expired rejection',0.)}
  package=ModuleType('_owner_pure_native');package.__path__=[];package.bone_collections=self.groups
  pkgpatch=patch.dict(sys.modules,{package.__name__:package,self.groups.__name__:self.groups})
  pkgpatch.start();self.addCleanup(pkgpatch.stop)
  namepatch=patch.object(A,'__package__',package.__name__);namepatch.start();self.addCleanup(namepatch.stop)
  self.rig.animation_data=AnimationBinding(self.rig,self.parent)
  self.rig['history_refs']={'historical':self.source}
  self.token='1'*32
  self.parent['token']=self.token;self.parent['checkpoint']=self.cp
  self.cp['record']='original immutable checkpoint bytes';self.cp['refs']={'custom':self.parent,'source':self.source,'rig':self.rig}
  self.rows=Rows([dict(item_id='parent',name='Parent',rig=self.rig,side='CUSTOM',custom_action=self.parent,
      custom_slot=1,source_action=self.source,source_slot=1,current_rig_revision=self.token,
      new_animation=False,composition_json='',work_item_id='old-work',clip_key='old-clip',link_identity='old-link',
      sync_selected=True,bl_rna=NS(properties=[]))])
  self.saved=NS(items=self.rows,retired_items=(),active_index=0,status='unchanged',has_error=False)
  self.db.rig=self.rig;self.db.saved=self.saved
  objects=Objects(Rig=self.rig);objects.active=self.rig
  self.context=NS(object=self.rig,mode='POSE',scene=NS(objects=objects,frame_current=14,frame_subframe=.25),
      view_layer=NS(objects=objects,name='ViewLayer'),screen=NS(is_animation_playing=False),
      window_manager=NS(character_designer_animation=NS(target='before')))
  self.worklist=NS(state=lambda ctx:self.saved,current=lambda ctx:self.rows[self.saved.active_index],
      _item=lambda saved,item_id:next(row for row in self.rows if row.item_id==item_id),
      _slot=lambda action,handle:next(s for s in action.slots if s.handle==handle),
      links=NS(ACTION_KEY='association'),_idle=lambda ctx:None,_rigging_module=lambda rig,action:None,
      _check_baseline=lambda row:None,_association=lambda row:None)
  self.revision=NS(TOKEN='token',CHECKPOINT='checkpoint',RECORD='record',REFS='refs',SCHEMA='revision/1',
      _row=lambda row:dict(row.record()),_json=lambda v:repr(v),_encode=self.encode,_store=self.store,
      _read=self.read,_modules=lambda:None,_restore_current=self.restore,
      _no_action_ID_properties=self.no_id_properties,_validate_paths=self.validate_paths)
  self.revision.validate=lambda ctx,item_id:self.validate_row(self.worklist._item(self.saved,item_id))
  self.target=NS(REFS='history_refs',managed=lambda rig:True,_capture=self.capture,_validate_capture=lambda ctx,c:None)
  self.api=NS(bpy=NS(types=NS(Action=Action),data=NS(actions=self.db)),worklist=self.worklist,
      revision=self.revision,target=self.target,
      pose=NS(SESSION_KEY='pose_session',REFS_KEY='pose_refs',active=lambda rig:False,
          natural_active=lambda rig:False,_pose_owner=lambda rig:True),
      rest=NS(is_rest=lambda rig:False),original=NS(active=lambda rig:False),
      composition=NS(active=lambda rig:False),ua=NS(active_preview=lambda rig:None))
  self.offset=NS(_action_hash=lambda api,action,handle:hashlib.sha256(repr(action.curves).encode()).hexdigest(),
      prepare_variant=self.prepare_variant,_variant_owner=self.variant_owner,new_variant_action=self.new_variant)
  self.patch=patch.object(A,'_modules',return_value=(self.offset,self.api));self.patch.start();self.addCleanup(self.patch.stop)
  self.parent_value=dict(rows=[dict(self.rows.records[0])],source_curve_hashes={'parent':'source-proof'},capture=self.capture(self.context,self.rig,include_owned_ik_poles=True))

 def capture(self,ctx,rig,*,include_owned_ik_poles):
  if include_owned_ik_poles is not True:raise TypeError('Owned Pole capability required')
  return dict(action=rig.animation_data.action,slot=rig.animation_data.action_slot,pose=rig.pose_marker,
      metadata=[dict(owner=rig,values=dict(rig))])
 def restore(self,ctx,rig,capture,api):
  rig.animation_data.action=capture['action'];rig.animation_data.action_slot=capture['slot']
  rig.animation_data.evaluate();rig.pose_marker=capture['pose']
  for entry in capture['metadata']:
   entry['owner'].properties.clear();entry['owner'].update(entry['values'])
 def no_id_properties(self,values):
  if any(isinstance(v,ID) for v in values.values()):raise ValueError('Historical recovery ID property')
 def validate_paths(self,rig,action,handle,api,capture):
  if any(c[0]=='unsupported' for c in action.curves):raise ValueError('Unsupported animation path')
 def validate_row(self,row):
  action=row.custom_action
  if row.current_rig_revision!=action.get('token'):raise ValueError('Token is not this row owner')
  cp=action.get('checkpoint')
  if cp is None or not any(value is action for value in cp['refs'].values()):raise ValueError('Checkpoint does not own exact Action')
  return A._clone(self.parent_value)
 def read(self,ctx,row,api):return self.validate_row(row)
 def encode(self,value,refs,history,rig,*,custom,token):
  refs.update(rig=rig,source=self.source,parent=self.parent,parent_checkpoint=self.cp)
  return value
 def store(self,checkpoint,value,refs):
  checkpoint['record']='new checkpoint '+str(len(value['rows']));checkpoint['refs']=dict(refs)
  checkpoint.payload=value;checkpoint.use_fake_user=True
 def prepare_variant(self,ctx,rig,*,label):
  row=self.worklist.current(ctx)
  if row.side!='CUSTOM' or rig.animation_data.action is not row.custom_action:raise ValueError('Custom owner changed')
  self.validate_row(row)
  return dict(saved=self.saved,item_id=row.item_id,item=row,original=self.parent,slot=1,
      revision_value=A._clone(self.parent_value),phase='prepared',staged_action=None)
 def variant_owner(self,variant,*,final=False):
  if self.worklist.current(self.context).item_id!=variant['item_id']:raise ValueError('Another row became active')
 def new_variant(self,variant):
  copied=self.parent.copy();variant['staged_action']=copied;variant['phase']='staged';return copied
 def unchanged(self):
  return (tuple(self.parent.curves),dict(self.parent),dict(self.cp),tuple(self.source.curves),dict(self.rows.records[0]))

 def merge_fixture(self):
  """Structured capture subset for the input merge; native replay is separate."""
  bones={name:ID(name,rotation_mode=mode,location=[.1,.2,.3],rotation_euler=[.4,.5,.6],
      rotation_quaternion=[1.,.1,.2,.3],rotation_axis_angle=[.7,0.,1.,0.],scale=[1.,1.,1.])
      for name,mode in (('TargetL','XYZ'),('TargetR','XYZ'),('NativeL','QUATERNION'),('NativeR','AXIS_ANGLE'))}
  self.rig.pose=NS(bones=bones)
  left,right=ID('IK_L'),ID('IK_R')
  inventory={'rigs':{'left':dict(target=bones['TargetL'],entries=[(bones['NativeL'],left,{'role':'IK'})]),
      'right':dict(target=bones['TargetR'],entries=[(bones['NativeR'],right,{'role':'IK'})])}}
  match=ModuleType(A.__package__+'.limb_ik_fk');match.PROPERTY='ik_fk'
  match._limb=lambda:NS(_validate_inventory=lambda rig:inventory)
  modulepatch=patch.dict(sys.modules,{match.__name__:match});modulepatch.start();self.addCleanup(modulepatch.stop)
  self.target._owned_ik_pole_constraints=lambda rig:{('NativeL','IK_L'):left,('NativeR','IK_R'):right}
  plan=A.prepare(self.context,self.rig);A.stage(plan)
  secondary=ID('Secondary',data=ID('SecondaryData'))
  def entry(owner,action,delta):
   pose={name:dict(rotation_mode=pb.rotation_mode,**{field:[value+delta for value in getattr(pb,field)]
       for field in ('location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale')})
       for name,pb in bones.items()}
   properties={name:{'artist_float':1.25+delta} for name in bones}
   properties['TargetL']['ik_fk']=1. if not delta else 0.
   properties['TargetR']['ik_fk']=0. if not delta else 1.
   constraints={name:{'Ordinary':dict(influence=.25+delta,mute=bool(delta))} for name in bones}
   constraints['NativeL']['IK_L']=dict(pole_angle=.4+delta,influence=.25+delta)
   constraints['NativeR']['IK_R']=dict(pole_angle=.7+delta,influence=.25+delta)
   return dict(target=owner,data=owner.data,action=action,snapshot=dict(pose=pose,slot=1),
       properties=properties,constraints=constraints)
  pending=dict(targets=[entry(self.rig,self.parent,0.),entry(secondary,self.source,0.)],metadata=['old-metadata'])
  fresh=dict(targets=[entry(self.rig,plan['staged_action'],10.),entry(secondary,self.source,10.)],metadata=['fresh-metadata'])
  plan['pending_capture']=A._clone(pending)
  switches=[(bones['TargetL'],{'NativeL'},[(left,.4)])]
  return plan,bones,left,right,pending,fresh,switches

 def test_stage_keeps_postmatch_pending_capture_separate_from_prepare_rollback(self):
  plan=A.prepare(self.context,self.rig)
  self.rig.pose_marker='matched before Action binding'
  staged=A.stage(plan)
  self.assertEqual(plan['capture']['pose'],'original')
  self.assertEqual(plan['pending_capture']['pose'],'matched before Action binding')
  self.assertIs(plan['pending_capture']['action'],self.parent)
  self.rig.pose_marker='later keying input'
  self.assertIs(A.stage(plan),staged)
  self.assertEqual(plan['pending_capture']['pose'],'matched before Action binding')

 def test_keyed_input_mask_uses_active_rotation_and_authenticated_selected_pole(self):
  plan,bones,left,right,pending,fresh,switches=self.merge_fixture()
  pose,properties,angles=A._keyed_input_paths(plan,[(bones['TargetL'],{'NativeL','NativeR'},[(left,.4)])])
  self.assertEqual(properties,{('TargetL','ik_fk')});self.assertEqual(angles,{('NativeL','IK_L')})
  self.assertEqual({field for name,field,index in pose if name=='NativeL'},{'location','rotation_quaternion','scale'})
  self.assertEqual({field for name,field,index in pose if name=='NativeR'},{'location','rotation_axis_angle','scale'})
  self.assertEqual(len(pose),20)
  self.assertNotIn(('NativeL','rotation_euler',0),pose)
  self.assertNotIn(('NativeR','rotation_quaternion',0),pose)

 def test_keyed_input_mask_refuses_unknown_target_or_other_limbs_pole(self):
  plan,bones,left,right,pending,fresh,switches=self.merge_fixture()
  for bad in ([(ID('foreign'),{'NativeL'},[])],[(bones['TargetL'],{'NativeL'},[(right,.7)])]):
   with self.assertRaisesRegex(ValueError,'owned native limb|authenticated native IK'):A._keyed_input_paths(plan,bad)
  with self.assertRaisesRegex(ValueError,'Identify'):A._keyed_input_paths(plan,[])

 def test_preserve_merge_keeps_unkeyed_inputs_unused_rotation_and_secondary_rig(self):
  plan,bones,left,right,pending,fresh,switches=self.merge_fixture();restored=[]
  with patch.object(self.target,'_capture',side_effect=lambda *a,**kw:A._clone(fresh)), \
       patch.object(self.revision,'_restore_current',side_effect=lambda ctx,rig,value,api:restored.append(value)):
   A.preserve_staged(plan,switches)
  self.assertEqual(len(restored),1);execution=restored[0]
  old=pending['targets'][0];now=fresh['targets'][0];actual=execution['targets'][0]
  for field in ('location','rotation_quaternion','scale'):
   self.assertEqual(actual['snapshot']['pose']['NativeL'][field],now['snapshot']['pose']['NativeL'][field])
  for field in ('rotation_euler','rotation_axis_angle'):
   self.assertEqual(actual['snapshot']['pose']['NativeL'][field],old['snapshot']['pose']['NativeL'][field])
  self.assertEqual(actual['snapshot']['pose']['TargetR'],old['snapshot']['pose']['TargetR'])
  self.assertEqual(actual['properties']['TargetL']['ik_fk'],now['properties']['TargetL']['ik_fk'])
  self.assertEqual(actual['properties']['TargetL']['artist_float'],old['properties']['TargetL']['artist_float'])
  self.assertEqual(actual['properties']['TargetR'],old['properties']['TargetR'])
  self.assertEqual(actual['constraints']['NativeL']['IK_L']['pole_angle'],now['constraints']['NativeL']['IK_L']['pole_angle'])
  self.assertEqual(actual['constraints']['NativeR']['IK_R']['pole_angle'],old['constraints']['NativeR']['IK_R']['pole_angle'])
  self.assertEqual(actual['constraints']['NativeR']['Ordinary'],now['constraints']['NativeR']['Ordinary'])
  self.assertEqual(execution['targets'][1]['snapshot'],pending['targets'][1]['snapshot'])
  self.assertEqual(execution['targets'][1]['properties'],pending['targets'][1]['properties'])
  self.assertEqual(execution['metadata'],fresh['metadata'])
  self.assertEqual(plan['pending_capture'],pending)

 def test_pending_merge_refuses_target_data_or_property_inventory_change_before_replay(self):
  plan,bones,left,right,pending,fresh,switches=self.merge_fixture()
  for changed in ('target-set','data','properties'):
   bad=A._clone(fresh)
   if changed=='target-set':bad['targets'].pop()
   elif changed=='data':bad['targets'][0]['data']=ID('ForeignData')
   else:bad['targets'][0]['properties']['TargetR']['unexpected']=3.
   with self.subTest(changed=changed), \
        patch.object(self.target,'_capture',side_effect=lambda *a,**kw:A._clone(bad)), \
        patch.object(self.revision,'_restore_current') as replay:
    with self.assertRaisesRegex(ValueError,'targets changed|data or secondary Action|numeric input inventory'):
     A.preserve_staged(plan,switches)
    replay.assert_not_called()

 def test_new_owner_is_one_copy_new_row_new_checkpoint_and_source_stays_immutable(self):
  before=self.unchanged();initial=set(self.db)
  plan=A.prepare(self.context,self.rig)
  self.assertEqual(set(self.db),initial)
  copied=A.stage(plan);self.assertIs(A.stage(plan),copied)
  copied.curves.append(('keyed','rotation',14.25,.2))
  self.rig.pose_marker='matched'
  result=A.publish(plan)
  self.assertIs(result['action'],copied);self.assertIs(self.rig.animation_data.action,copied)
  self.assertNotEqual(result['item_id'],'parent');self.assertEqual(len(self.rows),2)
  self.assertEqual(set(self.db)-initial,{copied,result['checkpoint']})
  self.assertIs(copied['checkpoint']['refs']['ik_fk_current_action'],copied)
  self.assertNotEqual(copied['token'],self.token)
  self.assertEqual(self.rows.expired_reads,0)
  self.assertEqual(self.unchanged(),before)

 def test_publish_then_rollback_restores_binding_rows_and_removes_only_its_ids(self):
  before=self.unchanged();initial=set(self.db)
  plan=A.prepare(self.context,self.rig);A.stage(plan);self.rig.pose_marker='changed'
  A.publish(plan);A.rollback(plan);A.rollback(plan)
  self.assertIs(self.rig.animation_data.action,self.parent);self.assertEqual(self.rig.pose_marker,'original')
  self.assertEqual(len(self.rows),1);self.assertEqual(set(self.db),initial)
  self.assertEqual(self.unchanged(),before);self.assertEqual(self.saved.status,'unchanged')

 def test_publication_flushes_copied_keys_but_retains_unkeyed_pose_and_new_association(self):
  before=self.unchanged();plan=A.prepare(self.context,self.rig);copied=A.stage(plan)
  self.assertTrue(self.rig.animation_data.pending)
  self.rig.pose_marker='current unkeyed override'
  append=A._append_row
  def associate_new_owner(*args):
   value=append(*args);self.rig[self.worklist.links.ACTION_KEY]=copied;return value
  with patch.object(A,'_append_row',side_effect=associate_new_owner):result=A.publish(plan)
  self.assertFalse(self.rig.animation_data.pending)
  self.rig.animation_data.evaluate()
  self.assertEqual(self.rig.pose_marker,'current unkeyed override')
  self.assertIs(self.rig[self.worklist.links.ACTION_KEY],copied)
  self.assertIs(result['action'],copied);self.assertEqual(self.unchanged(),before)
  self.validate_row(self.worklist.current(self.context))

 def test_failure_after_published_pose_restore_rolls_back_only_this_publication(self):
  before=self.unchanged();initial=set(self.db)
  plan=A.prepare(self.context,self.rig);copied=A.stage(plan);self.rig.pose_marker='unkeyed matched pose'
  preserve=A._preserve_published_pose
  def fail_after_restore(*args):
   preserve(*args);raise RuntimeError('injected published pose restore failure')
  with patch.object(A,'_preserve_published_pose',side_effect=fail_after_restore):
   with self.assertRaisesRegex(RuntimeError,'published pose restore'):A.publish(plan)
  A.rollback(plan)
  self.assertEqual(self.rig.pose_marker,'original');self.assertIs(self.rig.animation_data.action,self.parent)
  self.assertEqual(set(self.db),initial);self.assertNotIn(copied,self.db)
  self.assertEqual(len(self.rows),1);self.assertEqual(self.unchanged(),before)

 def test_source_and_asset_refuse_before_copy_even_with_worklist_association(self):
  for action in (self.source,self.parent):
   self.rig.animation_data.action=action
   if action is self.parent:action.asset_data=object()
   initial=set(self.db)
   with self.assertRaisesRegex(ValueError,'read-only|read.only'):A.prepare(self.context,self.rig)
   self.assertEqual(set(self.db),initial)

 def test_explicitly_unmanaged_returns_none_but_recovery_tags_cannot_bypass_owner(self):
  self.rows.records.clear();self.target.managed=lambda rig:False
  self.parent.pop('token');self.parent.pop('checkpoint')
  self.assertIsNone(A.prepare(self.context,self.rig))
  self.parent['token']=self.token
  with self.assertRaises((ValueError,IndexError,StopIteration)):A.prepare(self.context,self.rig)

 def test_formal_owned_pole_capture_support_is_required(self):
  def old_capture(ctx,rig):return {}
  self.target._capture=old_capture
  with self.assertRaisesRegex(ValueError,'owned.Pole'):A.prepare(self.context,self.rig)

 def test_parent_or_source_changes_refuse_before_publication(self):
  for changed in (self.parent,self.source):
   plan=A.prepare(self.context,self.rig);copied=A.stage(plan)
   changed.curves.append(('external','mutation',1.,3.))
   with self.assertRaisesRegex(ValueError,'changed'):A.publish(plan)
   self.assertEqual(len(self.rows),1)
   changed.curves.pop();A.rollback(plan)
   self.assertNotIn(copied,self.db)

 def test_foreign_native_user_blocks_cleanup_and_retains_recovery_ids(self):
  plan=A.prepare(self.context,self.rig);copied=A.stage(plan)
  self.db.foreign.append(copied);initial=set(self.db)
  with self.assertRaisesRegex(ValueError,'foreign user'):A.rollback(plan)
  self.assertEqual(set(self.db),initial);self.assertIs(self.rig.animation_data.action,copied)
  self.db.foreign.clear();A.rollback(plan)

 def test_orphan_recovery_copies_it_without_rebinding_parent_or_rewriting_old_checkpoint(self):
  before=self.unchanged();clone=self.parent.copy();clone.curves.append(('keyed','mode',14.,1.))
  self.rig.animation_data.action=clone;self.rig.animation_data.action_slot=clone.slots[0]
  with self.assertRaises(ValueError):A.prepare(self.context,self.rig)
  original=(tuple(clone.curves),dict(clone))
  plan=A.prepare_recovery(self.context,self.rig,parent_item_id='parent')
  result=A.publish_recovery(plan)
  self.assertIsNot(result['action'],clone);self.assertIsNot(result['checkpoint'],self.cp)
  self.assertEqual((tuple(clone.curves),dict(clone)),original);self.assertEqual(self.unchanged(),before)
  self.assertEqual(self.rows.expired_reads,0)

 def test_orphan_with_foreign_user_wrong_checkpoint_or_row_owner_is_not_claimed(self):
  clone=self.parent.copy();self.rig.animation_data.action=clone;self.rig.animation_data.action_slot=clone.slots[0]
  self.db.foreign.append(clone)
  with self.assertRaisesRegex(ValueError,'foreign native user'):A.prepare_recovery(self.context,self.rig)
  self.db.foreign.clear();clone['checkpoint']=self.db.new('Other checkpoint')
  with self.assertRaisesRegex(ValueError,'inherited parent checkpoint'):A.prepare_recovery(self.context,self.rig)
  clone['checkpoint']=self.cp;self.rows.records[0]['custom_action']=clone
  with self.assertRaises(ValueError):A.prepare_recovery(self.context,self.rig)

 def test_prepared_rollback_restores_matching_changes_before_any_action_allocation(self):
  initial=set(self.db);plan=A.prepare(self.context,self.rig)
  self.rig.pose_marker='matching already changed pose'
  order=tuple(self.collection.bones);cache=self.groups._FRAME_CACHE
  self.collection.bones.reverse();cache.pop(self.rig.as_pointer())
  A.rollback(plan);A.rollback(plan)
  self.assertEqual(self.rig.pose_marker,'original');self.assertEqual(set(self.db),initial)
  self.assertEqual(tuple(self.collection.bones),order);self.assertIs(self.groups._FRAME_CACHE,cache)
  self.assertEqual(cache[self.rig.as_pointer()],('current','FK'))

 def test_partial_native_publication_failure_can_roll_back_exactly(self):
  for seam in ('_publish_checkpoint','_append_row'):
   initial=set(self.db);before=self.unchanged()
   plan=A.prepare(self.context,self.rig);A.stage(plan)
   real=getattr(A,seam)
   def fail_after_write(*args):
    real(*args);raise RuntimeError('injected after native publication')
   with patch.object(A,seam,side_effect=fail_after_write):
    with self.assertRaisesRegex(RuntimeError,'injected'):A.publish(plan)
   A.rollback(plan)
   self.assertEqual(set(self.db),initial);self.assertEqual(self.unchanged(),before)
   self.assertEqual(len(self.rows),1);self.assertEqual(self.rows.expired_reads,0)

 def test_allocation_hash_failure_still_retains_a_cleanup_owner(self):
  initial=set(self.db);plan=A.prepare(self.context,self.rig)
  real=self.offset.new_variant_action
  def fail_after_allocation(variant):
   real(variant);raise RuntimeError('injected copy hash failure')
  self.offset.new_variant_action=fail_after_allocation
  with self.assertRaisesRegex(RuntimeError,'copy hash'):A.stage(plan)
  self.assertIsNotNone(plan['staged_action'])
  A.rollback(plan);self.assertEqual(set(self.db),initial)

 def test_stale_frame_or_new_active_action_is_not_overwritten_by_old_rollback(self):
  plan=A.prepare(self.context,self.rig);staged=A.stage(plan)
  self.context.scene.frame_current=15
  with self.assertRaises(ValueError):A.rollback(plan)
  self.assertIn(staged,self.db);self.assertIs(self.rig.animation_data.action,staged)
  self.context.scene.frame_current=14
  foreign=self.db.new('Later user action');self.rig.animation_data.action=foreign
  with self.assertRaises(ValueError):A.rollback(plan)
  self.assertIs(self.rig.animation_data.action,foreign);self.assertIn(staged,self.db)
  self.rig.animation_data.action=staged;A.rollback(plan)

 def test_new_row_identity_setter_failure_removes_only_the_unique_empty_row(self):
  initial=set(self.db);before=self.unchanged()
  plan=A.prepare(self.context,self.rig);A.stage(plan)
  actual=Row.__setattr__;triggered=[]
  def fail_identity(row,key,value):
   if key=='item_id' and row.record().get('item_id')=='' and not triggered:
    triggered.append(True);raise RuntimeError('injected native item_id setter failure')
   return actual(row,key,value)
  with patch.object(Row,'__setattr__',fail_identity):
   with self.assertRaisesRegex(RuntimeError,'item_id setter'):A.publish(plan)
  self.assertEqual(triggered,[True]);self.assertEqual(len(self.rows),2)
  A.rollback(plan)
  self.assertEqual(len(self.rows),1);self.assertEqual(set(self.db),initial)
  self.assertEqual(self.unchanged(),before)

 def test_display_restore_proves_all_member_sets_before_reordering_or_ram_writes(self):
  other=NativeCollection('Other',[ID('BoneD')]);self.data.collections_all.append(other)
  snapshot=A.capture_display(self.rig);order=tuple(self.collection.bones)
  self.collection.bones.reverse();other.bones.append(ID('Foreign added member'))
  self.groups._FRAME_CACHE[self.rig.as_pointer()]=('later transient state',)
  with self.assertRaisesRegex(ValueError,'member set'):A.restore_display(snapshot)
  self.assertEqual(tuple(self.collection.bones),tuple(reversed(order)))
  self.assertEqual(self.groups._FRAME_CACHE[self.rig.as_pointer()],('later transient state',))
  other.bones.pop();A.restore_display(snapshot)
  self.assertEqual(tuple(self.collection.bones),order)
  self.assertEqual(self.groups._FRAME_CACHE[self.rig.as_pointer()],('current','FK'))

 def test_display_recovery_refuses_replaced_cache_dictionary_identity(self):
  snapshot=A.capture_display(self.rig);original=self.groups._FRAME_CACHE
  replacement=dict(original);self.groups._FRAME_CACHE=replacement
  with self.assertRaisesRegex(ValueError,'ownership changed'):A.restore_display(snapshot)
  self.assertIs(self.groups._FRAME_CACHE,replacement)
  self.groups._FRAME_CACHE=original;A.restore_display(snapshot)

 def test_natural_pose_workspace_refuses_normal_keying_and_orphan_recovery(self):
  self.api.pose.natural_active=lambda rig:True
  initial=set(self.db)
  with self.assertRaisesRegex(ValueError,'Natural'):A.prepare(self.context,self.rig)
  self.assertEqual(set(self.db),initial)
  clone=self.parent.copy();self.rig.animation_data.action=clone;self.rig.animation_data.action_slot=clone.slots[0]
  initial=set(self.db);before=self.unchanged()
  with self.assertRaisesRegex(ValueError,'pose/animation workspace'):A.prepare_recovery(self.context,self.rig)
  self.assertEqual(set(self.db),initial);self.assertEqual(self.unchanged(),before)
  self.assertIs(self.rig.animation_data.action,clone)


if __name__=='__main__':unittest.main()
