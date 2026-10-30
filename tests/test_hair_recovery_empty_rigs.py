"""An unrelated empty Armature owns no physics bones during Hair recovery."""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1]/'addons/character_designer/hair_wiggle2_adapter.py'


class Source(dict):
    def as_pointer(self):
        return id(self)


class EmptyRigRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.source = Source(marker='original history marker')
        self.bone = NS(wiggle_head=True, wiggle_tail=True)
        self.rig = NS(type='ARMATURE', pose=NS(bones=[self.bone]), wiggle_enable=True)
        self.empty = NS(type='ARMATURE', pose=None)
        self.peer_bone = NS(wiggle_head=False, wiggle_tail=False)
        self.peer = NS(type='ARMATURE',pose=NS(bones=[self.peer_bone]))
        self.scene = NS(objects=[self.rig,self.empty,self.peer],wiggle_enable=True)
        self.token = dict(needed=True, marker_raw=self.source['marker'], backend=object(),
            rig=self.rig, scene=self.scene, bones={'Owned Hair':self.bone}, names=('Owned Hair',),
            original={'Owned Hair':{'wiggle_head':False,'wiggle_tail':False}},
            groups={'Owned Hair':None}, rig_original=False, scene_original=False)
        def restore(owner,field,value):
            setattr(owner,field,value)
        self.runtime = NS(_writes=lambda backend:nullcontext(), _restore=Mock(side_effect=restore),
            _restore_group=Mock(), _list_remove=Mock())
        self.rig.name = 'Owned Hair Rig'
        self.ns = dict(runtime=self.runtime,prepare_recovery=Mock(return_value=self.token),
            rollback_recovery=Mock(), _RECOVERY_KEY='marker',_HISTORY_PENDING=True,
            _LAST_SOURCE_POINTER=None)
        tree = ast.parse(SOURCE.read_bytes())
        nodes = [node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='recover_saved']
        self.assertEqual(len(nodes),1)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.ns)

    def recover(self):
        return self.ns['recover_saved'](object(),self.source)

    def test_unrelated_zero_bone_armature_does_not_block_owned_recovery(self):
        result = self.recover()
        self.assertTrue(result['stopped_confirmed'])
        self.assertFalse(self.bone.wiggle_head or self.bone.wiggle_tail)
        self.assertFalse(self.rig.wiggle_enable)
        self.assertFalse(self.scene.wiggle_enable)
        self.assertNotIn('marker',self.source)
        self.assertFalse(self.ns['_HISTORY_PENDING'])
        self.assertEqual(self.ns['_LAST_SOURCE_POINTER'],self.source.as_pointer())
        self.ns['rollback_recovery'].assert_not_called()

    def test_other_rig_real_live_physics_still_preserves_scene_enable(self):
        self.peer_bone.wiggle_tail = True
        self.recover()
        self.assertFalse(self.rig.wiggle_enable)
        self.assertTrue(self.scene.wiggle_enable)
        self.assertTrue(self.peer_bone.wiggle_tail)
        self.assertFalse(self.peer_bone.wiggle_head)
        self.assertNotIn('marker',self.source)
        self.assertFalse(any(call.args[0] is self.scene for call in self.runtime._restore.call_args_list))

    def test_native_restore_failure_retains_existing_transaction_rollback(self):
        self.runtime._restore_group.side_effect = RuntimeError('owned native setter refused')
        with self.assertRaisesRegex(RuntimeError,'native setter refused'):
            self.recover()
        self.ns['rollback_recovery'].assert_called_once()
        self.assertIs(self.ns['rollback_recovery'].call_args.args[1],self.token)
        self.assertEqual(self.source['marker'],'original history marker')
        self.assertTrue(self.ns['_HISTORY_PENDING'])


if __name__=='__main__':
    unittest.main()
