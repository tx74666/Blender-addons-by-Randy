"""Run the actual repair function on two legs, retaining global witnesses."""
import ast
from copy import deepcopy
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import uuid

SOURCE=Path(__file__).resolve().parents[1]/'addons/character_designer/limb_ik_fk.py'


class Vec:
    def __init__(self,value=()):self.values=list(value) if value else [0.,0.,0.]
    def __iter__(self):return iter(self.values)
    def copy(self):return Vec(self.values)
    def __add__(self,other):return Vec(a+b for a,b in zip(self,other))
    def __sub__(self,other):return Vec(a-b for a,b in zip(self,other))
    def __mul__(self,value):return Vec(a*value for a in self)
    __rmul__=__mul__
    @property
    def length(self):return math.sqrt(sum(a*a for a in self))
    def normalize(self):
        length=self.length
        self.values=[a/length for a in self]
    def normalized(self):
        value=self.copy();value.normalize();return value
    def dot(self,other):return sum(a*b for a,b in zip(self,other))


class Matrix(list):
    @classmethod
    def at(cls,x=0.,y=0.,z=0.):return cls([[1.,0.,0.,x],[0.,1.,0.,y],[0.,0.,1.,z],[0.,0.,0.,1.]])
    def copy(self):return Matrix(deepcopy(list(self)))
    @property
    def translation(self):return Vec(self[i][3] for i in range(3))
    @translation.setter
    def translation(self,value):
        for i,x in enumerate(value):self[i][3]=x
    def inverted(self):return Matrix.at()  # Every native Rest in this fixture is identity.
    def __matmul__(self,other):return Matrix([[sum(self[i][k]*other[k][j] for k in range(4)) for j in range(4)] for i in range(4)])


class Named(dict):
    def __iter__(self):return iter(self.values())


class Bone(dict):
    def __init__(self,name,**values):
        super().__init__(values);self.name=name;self.matrix_local=Matrix.at()


def finite(matrix):return all(math.isfinite(x) for row in matrix for x in row)
def difference(a,b):return max(abs(x-y) for ar,br in zip(a,b) for x,y in zip(ar,br))


class LegPoleRepairScope(unittest.TestCase):
    def setUp(self):
        self.tags=('pole_direction','configured_direction')
        self.data=NS(bones=Named())
        self.rig=NS(data=self.data,pose=NS(bones=Named()))
        self.rows={};self.generated=[];self.modes={};self.native_names=[];self.angles={}
        for side,x in (('L',-.25),('R',.25)):
            key=('LEG',side);chain=[stem+'.'+side for stem in ('thigh','shin','foot')]
            for index,name in enumerate(chain):
                self.data.bones[name]=Bone(name)
                self.rig.pose.bones[name]=NS(matrix=Matrix.at(x,0.,float(index)))
            pole=Bone('Pole.'+side,kind='LEG',side=side,**{tag:[0.,-1.,0.] for tag in self.tags})
            target=Bone('Target.'+side,kind='LEG',side=side)
            self.data.bones[pole.name]=pole;self.data.bones[target.name]=target
            self.rig.pose.bones[pole.name]=NS(matrix=Matrix.at(x,-1.5,1.))
            self.rig.pose.bones[target.name]=NS(matrix=Matrix.at(x,0.,2.))
            self.generated.extend((pole,target));self.native_names.extend(chain)
            self.rows[key]=dict(pole=pole,target=target,chain=chain)
            self.modes[key]='IK' if side=='L' else 'FK'
            self.angles[key]=-.004 if side=='L' else -3.137
        self.inventory=dict(rigs=self.rows,bones=self.generated,schema='direct')
        self.rest={name:dict(unchanged_native_rest=True) for name in self.native_names}
        self.other_author=dict(weights=[.125,.875],artist='preserve')
        self.switch_keys=[];self.skin_seen=[];self.guard_excluded=[];self.post_write_seen=False
        self.switch_extra=None
        self.limb=NS(_validate_inventory=Mock(return_value=self.inventory),_matrix_is_finite=finite,
            LEGACY_DIRECT_PREROLL_SCHEMA='legacy-direct',DIRECT_PREROLL_SCHEMA='direct',
            KIND_KEY='kind',SIDE_KEY='side',EPSILON=1e-8,POLE_DIRECTION_PARALLEL_RATIO=1e-6,
            POLE_DIRECTION_KEY=self.tags[0],POLE_CONFIGURED_DIRECTION_KEY=self.tags[1],
            _project_perpendicular=lambda value,axis:value-axis.normalized()*value.dot(axis.normalized()))
        self.package=ModuleType('_leg_scope_'+uuid.uuid4().hex);self.package.__path__=[]
        self.body=NS(_native_skin=lambda rig:list(self.native_names),_verify_skin=Mock(side_effect=self.verify_skin))
        self.region=NS(_native_scope=lambda rig,names:set(names),_guard=self.capture_guard,_verify_guard=self.verify_guard)
        self.package.body_setup=self.body;self.package.body_region=self.region
        self.package.body_setup_removal=NS(SKIN_MATRIX_TOLERANCE=2e-4)
        self.package.control_pose_assets=NS(native_rest=lambda rig:deepcopy(self.rest))
        modules=patch.dict(sys.modules,{self.package.__name__:self.package});modules.start();self.addCleanup(modules.stop)
        self.transaction=Mock(side_effect=self.atomic);self.body._atomic=self.transaction
        self.switch=Mock(side_effect=self.switch_limb)
        self.verify=Mock(side_effect=self.verify_pose)
        self.ns=dict(__package__=self.package.__name__,_limb=lambda:self.limb,_error=ValueError,
            _update=Mock(),mode_for_rig=lambda rig,row:self.modes[('LEG',row['pole']['side'])],
            pose_names=lambda row:list(row['chain']),switch_limb=self.switch,_verify=self.verify,
            Vector=Vec,math=math)
        names={'_matrices','_leg_front_policy','_leg_front_direction','_leg_front_layout',
               '_repair_leg_poles_with_transaction','repair_leg_poles'}
        nodes=[n for n in ast.parse(SOURCE.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.ns)

    def state(self):
        return dict(bones={name:deepcopy(dict(bone)) for name,bone in self.data.bones.items()},
            pose={name:pb.matrix.copy() for name,pb in self.rig.pose.bones.items()},
            angles=deepcopy(self.angles),modes=deepcopy(self.modes),rest=deepcopy(self.rest),other=deepcopy(self.other_author))

    def restore(self,state):
        for name,values in state['bones'].items():
            self.data.bones[name].clear();self.data.bones[name].update(deepcopy(values))
        for name,matrix in state['pose'].items():self.rig.pose.bones[name].matrix=matrix.copy()
        self.angles.clear();self.angles.update(state['angles'])
        self.modes.clear();self.modes.update(state['modes'])
        self.rest=deepcopy(state['rest']);self.other_author=deepcopy(state['other'])

    def atomic(self,context,rig,operation):
        self.assertIs(rig,self.rig)
        before=self.state()
        try:return operation()
        except Exception:self.restore(before);raise

    def stale(self,side):
        for tag in self.tags:self.rows[('LEG',side)]['pole'][tag]=[0.,1.,0.]

    def switch_limb(self,context,rig,key,mode,**kwargs):
        self.switch_keys.append(key)
        self.assertEqual(kwargs['keyframe'],False)
        self.assertTrue(kwargs['calibrated_rest'] and kwargs['precise'])
        self.assertFalse(kwargs['sync_display'])
        row=self.rows[key]
        point,_stale=self.ns['_leg_front_layout'](rig,row,{name:pb.matrix for name,pb in rig.pose.bones.items()})
        rig.pose.bones[row['pole'].name].matrix.translation=point
        for name,matrix in kwargs['desired_pose'].items():rig.pose.bones[name].matrix=matrix.copy()
        self.angles[key]+=.033
        if self.switch_extra:self.switch_extra(key)
        return dict(errors=(0.,0.,0.))

    def capture_guard(self,rig,excluded):
        self.guard_excluded.append(set(excluded))
        return {name:dict(properties=deepcopy(dict(bone)),pose=rig.pose.bones[name].matrix.copy(),
                         angle=deepcopy(self.angles.get(('LEG',name.rsplit('.',1)[-1]))))
                for name,bone in self.data.bones.items() if name not in excluded}

    def verify_guard(self,rig,before,excluded):
        if before!=self.capture_guard(rig,excluded):raise ValueError('Protected other-leg input changed')

    def verify_pose(self,rig,wanted):
        self.assertEqual(set(wanted),set(self.native_names))
        if any(difference(rig.pose.bones[name].matrix,matrix)>2e-4 for name,matrix in wanted.items()):
            raise ValueError('Global native pose witness failed')

    def verify_skin(self,rig,skin,*,tolerance):
        self.skin_seen.append(set(skin))
        self.assertEqual(set(skin),set(self.native_names))
        self.assertEqual(tolerance,2e-4)
        if any(difference(rig.pose.bones[name].matrix,matrix)>tolerance for name,matrix in skin.items()):
            raise ValueError('Global skin witness failed')

    def invoke(self,desired=None):
        return self.ns['_repair_leg_poles_with_transaction'](NS(),self.rig,desired,self.transaction)

    def test_one_stale_left_does_not_switch_or_change_healthy_right(self):
        self.stale('L');right=self.angles[('LEG','R')]
        result=self.invoke()
        self.assertEqual(self.switch_keys,[('LEG','L')])
        self.assertEqual(self.angles[('LEG','R')],right)
        self.assertEqual(result['repaired'],['LEG:L'])
        self.assertTrue(all('shin.R' not in excluded and 'Pole.R' not in excluded for excluded in self.guard_excluded))

    def test_one_stale_right_is_repaired_in_its_existing_FK_mode(self):
        self.stale('R');result=self.invoke()
        self.assertEqual(self.switch_keys,[('LEG','R')])
        self.assertEqual(self.switch.call_args.args[3],'FK')
        self.assertEqual(result['repaired'],['LEG:R'])

    def test_both_needed_legs_are_repaired(self):
        self.stale('L');self.stale('R');result=self.invoke()
        self.assertEqual(self.switch_keys,[('LEG','L'),('LEG','R')])
        self.assertEqual(result['repaired'],['LEG:L','LEG:R'])

    def test_healthy_legs_are_a_read_only_noop_without_transaction(self):
        before=self.state();self.assertEqual(self.invoke(),{'repaired':[],'changed':False})
        self.assertEqual(self.state(),before);self.switch.assert_not_called();self.transaction.assert_not_called()

    def test_actual_right_desired_motion_is_not_lost_when_left_metadata_is_stale(self):
        self.stale('L');wanted=self.rig.pose.bones['shin.R'].matrix.copy();wanted[0][3]+=.02
        result=self.invoke({'shin.R':wanted})
        self.assertEqual(result['repaired'],['LEG:L','LEG:R'])
        self.assertLessEqual(difference(self.rig.pose.bones['shin.R'].matrix,wanted),2e-4)

    def test_subtolerance_right_bake_noise_does_not_trigger_matching(self):
        self.stale('L');wanted=self.rig.pose.bones['shin.R'].matrix.copy();wanted[0][3]+=1e-6
        self.invoke({'shin.R':wanted});self.assertEqual(self.switch_keys,[('LEG','L')])

    def test_failure_after_real_tag_writes_restores_complete_transaction_state(self):
        self.stale('L');before=self.state()
        def fail_after_tags(*args,**kwargs):
            self.post_write_seen=self.rows[('LEG','L')]['pole'][self.tags[0]]==[0.,-1.,0.]
            self.assertTrue(self.post_write_seen)
            raise ValueError('Failure after actual tag writes')
        self.body._verify_skin.side_effect=fail_after_tags
        with self.assertRaisesRegex(ValueError,'actual tag writes'):self.invoke()
        self.assertTrue(self.post_write_seen);self.assertEqual(self.state(),before)

    def test_shared_dependency_changing_other_native_pose_is_rejected_and_rolled_back(self):
        self.stale('L');before=self.state()
        def shared(key):
            if key==('LEG','L'):self.rig.pose.bones['shin.R'].matrix[0][3]+=.02
        self.switch_extra=shared
        with self.assertRaisesRegex(ValueError,'Global native pose'):self.invoke()
        self.assertEqual(self.state(),before)

    def test_shared_dependency_changing_other_leg_angle_is_protected_and_rolled_back(self):
        self.stale('L');before=self.state()
        def shared(key):
            if key==('LEG','L'):self.angles[('LEG','R')]+=.2
        self.switch_extra=shared
        with self.assertRaisesRegex(ValueError,'Protected other-leg'):self.invoke()
        self.assertEqual(self.state(),before)

    def test_any_blended_mode_still_refuses_before_any_transaction(self):
        self.stale('L');self.modes[('LEG','R')]='BLEND';before=self.state()
        with self.assertRaisesRegex(ValueError,'Choose IK or FK'):self.invoke()
        self.assertEqual(self.state(),before);self.transaction.assert_not_called()

    def test_invalid_or_foreign_desired_pose_refuses_before_mutation(self):
        self.stale('L')
        for desired in ({'foreign':Matrix.at()},{'shin.L':Matrix.at(float('nan'))}):
            with self.subTest(desired=desired):
                before=self.state()
                with self.assertRaisesRegex(ValueError,'finite matrices'):self.invoke(desired)
                self.assertEqual(self.state(),before);self.transaction.assert_not_called()

    def test_public_legacy_entry_keeps_original_body_atomic_transaction(self):
        self.stale('L');result=self.ns['repair_leg_poles'](NS(),self.rig)
        self.assertEqual(result['repaired'],['LEG:L'])
        self.transaction.assert_called_once();self.assertIs(self.transaction.call_args.args[1],self.rig)


if __name__=='__main__':unittest.main()
