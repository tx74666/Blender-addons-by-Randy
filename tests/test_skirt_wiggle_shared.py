"""Persisted shared Dress recovery is explicit, scoped and reversible."""
import ast
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import test_wiggle2_runtime as runtime_fixture
from test_wiggle2_runtime import FIELDS, Owner, snapshot


ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixture.RuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.source, self.rig, self.scene, self.bone = f.skirt, f.rig, f.scene, f.b
        self.source['rig'] = self.rig
        self.rig['source'] = self.source
        self.rig.data = Owner('Rig Data', 'ARMATURE_DATA')
        self.rig.data['owner'] = 'owned'
        self.scene['scene_id'] = 'scene-token'
        self.source['scene_ref'] = 'scene-token'
        for owner in (self.rig, self.scene, self.bone):
            owner.bl_rna = NS(properties={field: NS() for field in FIELDS})
        self.record = {'version': 1, 'owner': 'owned', 'chains': [{'phys': [self.bone.name]}]}
        self.source['record'] = json.dumps(self.record)
        self.graph = Mock(return_value={})
        self.skirt = NS(RIG_KEY='rig', SOURCE_KEY='source', OWNER_KEY='owner', RECORD_KEY='record',
            read_record=lambda source: self.record, is_shared=lambda record: False)
        self.package = ModuleType('_skirt_shared_fixture')
        self.package.wiggle2_runtime = f.runtime
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {self.package.__name__: self.package}).start()
        self.ns = dict(__package__=self.package.__name__, _SESSIONS={}, STATE_KEY='state',
            STATE_SCENE_KEY='scene_ref', _FLAGS=tuple(list(FIELDS)[:6]),
            _DEFAULTS={name: FIELDS[name] for name in ('wiggle_stiff', 'wiggle_damp', 'wiggle_gravity',
                                                    'wiggle_mass', 'wiggle_stretch', 'wiggle_chain')},
            BACKEND_NAME='Wiggle 2', BACKEND_VERSION='2.2.4', skirt=self.skirt, json=json, math=math,
            bpy=f.bpy, _graph=self.graph, _origin_scene=lambda source, rig: self.scene,
            _backend=Mock(return_value=f.backend), _fail=lambda message: (_ for _ in ()).throw(ValueError(message)))
        tree = ast.parse((ROOT / 'skirt_wiggle.py').read_text(encoding='utf8'))
        names = {'_state', '_write', '_raw', 'preview_state', 'prepare_recovery', 'recover_saved',
                 '_restore_recovery', 'rollback_recovery', 'set_paused'}
        exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                     and node.name in names], type_ignores=[]), str(ROOT / 'skirt_wiggle.py'), 'exec'), self.ns)
        @contextmanager
        def writes(backend):
            with f.runtime._writes(backend):
                yield
        self.ns['_native_writes'] = writes
        self.ns['_restore_raw'] = f.runtime._restore
        lease = f.start('SKIRT')
        self.lease = lease
        saved = dict(rig=self.rig, names=lease['names'], bone_refs=lease['bones'], lease=lease,
                     source=self.source, scene=self.scene, record=self.record, paused=False)
        self.ns['_SESSIONS'][self.source.as_pointer()] = saved
        original = {name: {field: lease['native_original'][name][field]
                    for field in (*self.ns['_FLAGS'], *self.ns['_DEFAULTS'])} for name in lease['names']}
        self.ns['_write'](self.source, self.record, 'AUTOMATIC', original=original,
                          lease={'scene_enable': lease['scene_original'], 'rig_enable': lease['rig_original']})
        self.original_state = self.source['state']
        self.ns['_SESSIONS'].clear()
        f.runtime.forget('SKIRT')
    def state(self):
        return [snapshot(owner) for owner in (self.scene, self.rig, self.bone, self.fixture.a)], self.source['state']
    def test_prepare_is_read_only_and_recover_rollback_restores_exact_owned_state(self):
        before = self.state()
        token = self.ns['prepare_recovery'](self.fixture.context, self.source)
        self.assertEqual(self.state(), before)
        self.assertTrue(token['changed'])
        recovered = self.ns['recover_saved'](self.fixture.context, self.source, token)
        self.assertTrue(recovered['stopped_confirmed'])
        self.assertFalse(self.bone.wiggle_tail)
        self.assertFalse(self.scene.wiggle_enable)
        self.ns['rollback_recovery'](self.fixture.context, recovered)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.scene.frame_current, 23)
    def test_changed_produced_flags_refuse_before_any_write(self):
        self.bone.wiggle_mute = True
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'changed externally'):
            self.ns['prepare_recovery'](self.fixture.context, self.source)
        self.assertEqual(self.state(), before)
    def test_missing_new_marker_refuses_legacy_live_flags(self):
        state = json.loads(self.source['state'])
        state.pop('runtime_v2')
        self.source['state'] = json.dumps(state)
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'ownership marker'):
            self.ns['recover_saved'](self.fixture.context, self.source)
        self.assertEqual(self.state(), before)
    def test_reverse_source_pointer_refuses_copied_owner(self):
        self.rig['source'] = self.fixture.hair
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'ownership no longer agree'):
            self.ns['recover_saved'](self.fixture.context, self.source)
        self.assertEqual(self.state(), before)
    def test_prepared_token_rejects_changed_native_before_action(self):
        token = self.ns['prepare_recovery'](self.fixture.context, self.source)
        state = json.loads(self.source['state'])
        state['reason'] = 'external source edit'
        self.source['state'] = json.dumps(state)
        before = self.state()
        with self.assertRaisesRegex(ValueError, 'changed after'):
            self.ns['recover_saved'](self.fixture.context, self.source, token)
        self.assertEqual(self.state(), before)
    def test_prepared_token_accepts_peer_delta_and_receipt_uses_action_boundary(self):
        token = self.ns['prepare_recovery'](self.fixture.context, self.source)
        peer = self.fixture.a
        peer.wiggle_tail = True
        self.fixture.runtime._list_add(self.scene, self.rig.name, (peer.name,))
        before = self.state()
        receipt = self.ns['recover_saved'](self.fixture.context, self.source, token)
        self.ns['rollback_recovery'](self.fixture.context, receipt)
        self.assertEqual(self.state(), before)
    def test_recovery_preserves_foreign_peer_flags_list_and_globals(self):
        peer = self.fixture.a
        peer.wiggle_tail = True
        self.fixture.runtime._list_add(self.scene, self.rig.name, (peer.name,))
        before = snapshot(peer)
        self.ns['recover_saved'](self.fixture.context, self.source)
        self.assertEqual(snapshot(peer), before)
        self.assertTrue(self.scene.wiggle_enable)
        self.assertTrue(self.rig.wiggle_enable)
        self.assertEqual(self.fixture.runtime._members(self.scene), {(self.rig.name, peer.name)})
    def test_recovery_fault_rolls_back_exact_source_and_native_state(self):
        before = self.state()
        self.bone.fault = 'wiggle_tail'
        with self.assertRaisesRegex(RuntimeError, 'unset fault'):
            self.ns['recover_saved'](self.fixture.context, self.source)
        self.assertEqual(self.state(), before)
    def test_metadata_preview_does_not_hash_and_reports_uncertain_redo(self):
        self.ns['_backend'].side_effect = AssertionError('draw touched backend')
        info = self.ns['preview_state'](self.source)
        self.assertFalse(info['stopped_confirmed'])
        self.assertTrue(info['pending_recovery'])
        self.assertFalse(info['running'])
        self.ns['_backend'].assert_not_called()
    def test_clean_state_needs_no_backend_or_recovery_write(self):
        self.bone.wiggle_tail = False
        self.ns['_backend'].side_effect = AssertionError('clean stop touched backend')
        before = self.state()
        token = self.ns['recover_saved'](self.fixture.context, self.source)
        self.assertTrue(token['stopped_confirmed'])
        self.assertFalse(token['changed'])
        self.assertEqual(self.state(), before)
    def active_session(self):
        self.ns['recover_saved'](self.fixture.context, self.source)
        lease = self.fixture.start('SKIRT')
        saved = dict(rig=self.rig, names=lease['names'], bone_refs=lease['bones'], lease=lease,
                     source=self.source, scene=self.scene, record=self.record, paused=False,
                     backend=self.fixture.backend)
        self.ns['_SESSIONS'][self.source.as_pointer()] = saved
        self.ns['_changed'] = Mock(return_value='')
        return saved
    def test_pause_bad_backend_refuses_before_any_native_or_metadata_write(self):
        saved = self.active_session()
        self.ns['_backend'].side_effect = ValueError('backend hash changed')
        before = self.state()
        with patch.object(self.fixture.runtime, 'set_paused') as setter:
            with self.assertRaisesRegex(ValueError, 'backend hash changed'):
                self.ns['set_paused'](self.fixture.context, self.source, True)
            setter.assert_not_called()
        self.ns['_changed'].assert_not_called()
        self.assertFalse(saved['paused'])
        self.assertEqual(self.state(), before)
    def test_pause_returns_current_backend_proof_without_cached_available_status(self):
        self.active_session()
        self.ns['status'] = Mock(side_effect=AssertionError('used stale availability cache'))
        result = self.ns['set_paused'](self.fixture.context, self.source, True)
        self.assertTrue(result['available'])
        self.assertTrue(result['paused'])
        self.assertFalse(result['running'])
        self.assertEqual(result['version'], '2.2.4')
        self.ns['status'].assert_not_called()


class LifecycleTests(unittest.TestCase):
    def test_pre_stops_only_live_leases_and_post_timer_never_reconnect(self):
        tree = ast.parse((ROOT / 'skirt_wiggle.py').read_text(encoding='utf8'))
        names = {'_lifecycle_pre', '_save_post', '_resume_saved'}
        namespace = dict(_SESSIONS={11: object(), 22: object()}, _SAVE_RESUME=['old'],
                         persistent=lambda function: function,
                         _stop=Mock(), _start=Mock(side_effect=AssertionError('implicit reconnect')),
                         suspend=Mock(side_effect=AssertionError('inactive source sweep')))
        exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                     and node.name in names], type_ignores=[]), 'lifecycle', 'exec'), namespace)
        namespace['_lifecycle_pre']()
        self.assertEqual([call.args[0] for call in namespace['_stop'].call_args_list], [11, 22])
        self.assertTrue(all(call.kwargs['preserve_preference'] for call in namespace['_stop'].call_args_list))
        namespace['_save_post']()
        self.assertEqual(namespace['_SAVE_RESUME'], [])
        self.assertIsNone(namespace['_resume_saved']())
        namespace['_start'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
