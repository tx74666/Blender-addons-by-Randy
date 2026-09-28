"""The faster calibrated search must preserve the previous numerical result."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import bpy
from mathutils import Matrix, Vector

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'addons'),str(ROOT/'tests')]
import character_designer
from character_designer import body_calibration as c, body_setup, limb_ik, limb_ik_fk as fk
from test_body_calibration_blender import base, prepare


def build(reference=False, mirror=False):
    source=json.loads((ROOT/'tests/fixtures/body_calibration_shallow_parented.json').read_text())
    base.reset_scene()
    rig=bpy.data.objects.new('Shallow performance',bpy.data.armatures.new('Shallow performance'))
    bpy.context.scene.collection.objects.link(rig)
    rig.matrix_world=Matrix(source['world'])
    if mirror:rig.matrix_world=Matrix.Diagonal((-1.,1.,1.,1.))@rig.matrix_world
    rig.select_set(True);bpy.context.view_layer.objects.active=rig
    bpy.ops.object.mode_set(mode='EDIT')
    for name,state in source['bones'].items():
        bone=rig.data.edit_bones.new(name)
        bone.head=state['head'];bone.tail=state['tail'];bone.align_roll(Vector(state['z']))
    for name,state in source['bones'].items():
        bone=rig.data.edit_bones[name]
        bone.parent=rig.data.edit_bones.get(state['parent']);bone.use_connect=state['use_connect']
    bpy.ops.object.mode_set(mode='OBJECT');bpy.context.view_layer.update()
    c.settings(rig).include_legs=False
    prepare(rig)
    rest=c.native_rest(rig)
    ns=dict(fk.__dict__)
    exec((ROOT/'tests/fixtures/body_reach_reference_0_62_16.py').read_text(),ns)
    match=ns['_match_ik'] if reference else fk._match_ik
    refine=ns['_refine_calibrated_reach'] if reference else fk._refine_calibrated_reach
    old_update=fk._update
    trials=[];updates=[]
    def update(*args):
        updates.append(1);return old_update(*args)
    def trace(context,armature,inventory,data,desired,**kwargs):
        result=match(context,armature,inventory,data,desired,**kwargs)
        if kwargs.get('reach_offset') is not None:
            key=(tuple(data['chain']),tuple(desired[data['chain'][2]].translation+kwargs['reach_offset']))
            state=tuple(tuple(tuple(row) for row in armature.pose.bones[data[role].name].matrix_basis)
                        for role in ('target','pole'))
            trials.append((key,state,fk._pose_errors(armature,desired)))
        return result
    ns['_match_ik']=trace;ns['_update']=update
    with patch.object(fk,'_match_ik',side_effect=trace),patch.object(fk,'_refine_calibrated_reach',refine),patch.object(fk,'_update',side_effect=update):
        body_setup.generate(bpy.context,rig)
    c.verify_rest(rig,rest)
    pose={p.name:(tuple(tuple(row) for row in p.matrix),tuple(tuple(row) for row in p.matrix_basis),
                    tuple((con.type,con.mute) for con in p.constraints),p.get(fk.PROPERTY)) for p in rig.pose.bones}
    return pose,trials,len(updates)


class GeneratePerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):character_designer.register()

    def test_parented_search_matches_reference_with_fewer_evaluations(self):
        for mirror in (False,):
            with self.subTest(mirror=mirror):
                old,old_trials,old_updates=build(reference=True,mirror=mirror)
                new,new_trials,new_updates=build(mirror=mirror)
                by_key={}
                for key,state,errors in old_trials:
                    if key in by_key:
                        self.assertEqual(by_key[key],(state,errors),'Repeated target was not deterministic')
                    by_key[key]=(state,errors)
                self.assertLess(len(new_trials),len(old_trials))
                self.assertLess(new_updates,old_updates)
                self.assertEqual({key for key,_,_ in new_trials},set(by_key))
                self.assertEqual(len(new_trials),len(by_key))
                for key,state,errors in new_trials:self.assertEqual((state,errors),by_key[key])
                self.assertEqual(new,old,'Search changed generated/native pose, mute or IK state')
                print('REACH_EVALUATIONS',mirror,len(old_trials),len(new_trials),old_updates,new_updates,flush=True)

    def test_existing_mirrored_shallow_guard_is_not_relaxed(self):
        for reference in (True,False):
            with self.subTest(reference=reference):
                with self.assertRaisesRegex(ValueError,'changed the current deformation'):
                    build(reference=reference,mirror=True)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(GeneratePerformanceTests))
    if not result.wasSuccessful():raise SystemExit(1)
