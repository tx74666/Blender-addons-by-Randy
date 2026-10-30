"""Skirt Automatic authenticates stopped same-character Hair history peers."""
import json
from unittest.mock import patch
import unittest

import test_animation_physics as fixture

physics = fixture.physics


class SkirtPeerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PhysicsTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.record['physics'] = None
        self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
        self.prepared, self.recovered = [], []
        self.bad, self.pending = set(), set()
        self.mode = 'MANUAL'
        self.f.wiggle.mode = lambda source: self.mode
        self.f.wiggle._state = lambda source: None
        self.f.wiggle.set_mode = self.start_skirt
        self.f.hair.preview_state = self.preview
        self.f.hair.prepare_recovery = self.prepare
        self.f.hair.recover_saved = self.recover

    def hair(self, name='Hair', *, rig=None, pending=True):
        source = fixture.Object(name, 'MESH')
        source[physics.HAIR_RECORD] = '{}'
        source[physics.HAIR_RIG] = self.f.rig if rig is None else rig
        source['native_hair_flags'] = pending
        if pending:
            source['peer_marker'] = 'exact '+name+' marker'
            self.pending.add(source.as_pointer())
        self.f.context.scene.objects.append(source)
        return source

    def preview(self, source):
        active = bool(self.f.hair_state.get('active')
            and self.f.hair_state.get('source_pointer') == source.as_pointer())
        pending = source.as_pointer() in self.pending
        return dict(active=active, running=active, paused=False, pending=pending,
            pending_recovery=pending, stopped_confirmed=not pending and not active, message='')

    def prepare(self, context, source):
        self.prepared.append(source.name)
        if source.as_pointer() in self.bad:
            raise ValueError('Hair recovery ownership changed')
        return dict(source=source, needed=True, marker_raw=source['peer_marker'])

    def recover(self, context, source, token=None):
        if source.get('peer_marker') != token['marker_raw']:
            raise ValueError('The saved Hair preview changed before recovery')
        self.recovered.append(source.name)
        self.pending.remove(source.as_pointer())
        source.pop('peer_marker')
        source['native_hair_flags'] = False
        return dict(source=source, needed=True, changed=True, stopped_confirmed=True)

    def start_skirt(self, context, source, mode):
        self.f.calls.append(('skirt-mode', source.name, mode))
        self.mode = mode

    def on(self):
        return physics.set_component(self.f.context, 'SKIRT', True)

    def test_explicit_skirt_on_recovers_own_history_without_enabling_hair(self):
        peer = self.hair()
        state = physics._fresh()
        state['hair_enabled'] = False
        physics._write(self.f.rig, state)
        raw_permission = self.f.rig[physics.STATE_KEY]
        self.on()
        self.assertEqual(self.prepared, ['Hair'])
        self.assertEqual(self.recovered, ['Hair'])
        self.assertFalse(peer['native_hair_flags'])
        self.assertNotIn('peer_marker', peer)
        self.assertEqual(self.f.rig[physics.STATE_KEY], raw_permission)
        self.assertFalse(self.f.hair_state['active'])
        self.assertEqual(self.f.calls, [('skirt-mode', 'Dress', 'AUTOMATIC')])

    def test_valid_live_hair_peer_keeps_its_existing_session_and_is_not_recovered(self):
        peer = self.hair(pending=False)
        self.f.hair_state.update(active=True, source=peer.name,
            source_pointer=peer.as_pointer(), paused=False)
        before = dict(peer), dict(self.f.hair_state)
        self.on()
        self.assertEqual((dict(peer), dict(self.f.hair_state)), before)
        self.assertEqual((self.prepared, self.recovered), ([], []))
        self.assertEqual(self.mode, 'AUTOMATIC')

    def test_other_character_history_is_not_recovered_and_foreign_guard_remains(self):
        foreign = fixture.Object('Foreign Rig')
        self.f.context.scene.objects.append(foreign)
        peer = self.hair('Foreign Hair', rig=foreign)
        before = dict(peer)
        def refuse(context, source, mode):
            raise ValueError('Another Wiggle simulation is configured')
        self.f.wiggle.set_mode = refuse
        self.on()
        self.assertEqual(dict(peer), before)
        self.assertEqual((self.prepared, self.recovered), ([], []))
        self.assertIn('Another Wiggle', physics.status(self.f.context)['dresses'][0]['last_error'])

    def test_unmarked_same_character_native_writer_is_not_reclaimed(self):
        peer = self.hair(pending=False)
        peer['native_hair_flags'] = True
        before = dict(peer)
        self.f.wiggle.set_mode = lambda *args: (_ for _ in ()).throw(ValueError('Unowned native writer'))
        self.on()
        self.assertEqual(dict(peer), before)
        self.assertEqual((self.prepared, self.recovered), ([], []))
        self.assertIn('Unowned native', physics.status(self.f.context)['dresses'][0]['last_error'])

    def test_all_pending_candidates_are_authenticated_before_first_native_recovery(self):
        first, second = self.hair('Hair A'), self.hair('Hair B')
        self.bad.add(second.as_pointer())
        before = dict(first), dict(second)
        self.on()
        self.assertEqual(self.prepared, ['Hair A', 'Hair B'])
        self.assertEqual(self.recovered, [])
        self.assertEqual((dict(first), dict(second)), before)
        self.assertEqual(self.mode, 'MANUAL')

    def test_changed_marker_at_recovery_boundary_refuses_without_native_reclaim(self):
        peer = self.hair()
        real_prepare = self.prepare
        def prepare(context, source):
            token = real_prepare(context, source)
            source['peer_marker'] = 'new external marker'
            return token
        self.f.hair.prepare_recovery = prepare
        self.on()
        self.assertEqual(peer['peer_marker'], 'new external marker')
        self.assertTrue(peer['native_hair_flags'])
        self.assertFalse(self.recovered)
        self.assertIn('changed before recovery', physics.status(self.f.context)['dresses'][0]['last_error'])

    def test_later_skirt_start_failure_keeps_completed_peer_recovery_stopped(self):
        peer = self.hair()
        state = physics._fresh()
        state['hair_enabled'] = False
        physics._write(self.f.rig, state)
        permission = self.f.rig[physics.STATE_KEY]
        self.f.wiggle.set_mode = lambda *args: (_ for _ in ()).throw(RuntimeError('Late Skirt configure failed'))
        self.on()
        self.assertFalse(peer['native_hair_flags'])
        self.assertNotIn('peer_marker', peer)
        self.assertEqual(self.f.rig[physics.STATE_KEY], permission)
        choice = physics._skirt_choice(self.f.source, self.f.record)
        self.assertEqual((choice['mode'], choice['pending']), ('AUTOMATIC', True))
        self.assertIn('Late Skirt', physics.status(self.f.context)['dresses'][0]['last_error'])

    def test_existing_skirt_validation_occurs_after_peer_recovery(self):
        peer = self.hair()
        saved = dict(source=self.f.source, scene=self.f.context.scene)
        self.f.wiggle._SESSIONS[self.f.source.as_pointer()] = saved
        def changed(value):
            self.assertIs(value, saved)
            self.assertFalse(peer['native_hair_flags'])
            return ''
        self.f.wiggle._changed = changed
        self.on()
        self.assertEqual(self.recovered, ['Hair'])

    def test_manual_choice_does_not_recover_or_change_hair(self):
        peer = self.hair()
        before = dict(peer)
        physics.set_skirt_mode(self.f.context, self.f.source.name, 'MANUAL', expected_owner='stable-owner')
        self.assertEqual(dict(peer), before)
        self.assertEqual((self.prepared, self.recovered), ([], []))

    def test_permission_off_deferred_choice_does_not_recover_peer(self):
        peer = self.hair()
        state = physics._fresh()
        state['enabled'] = False
        physics._write(self.f.rig, state)
        before = dict(peer)
        physics.set_skirt_mode(self.f.context, self.f.source.name, 'AUTOMATIC', expected_owner='stable-owner')
        self.assertEqual(dict(peer), before)
        self.assertFalse(self.prepared)

    def test_cloth_auto_does_not_attempt_wiggle_peer_recovery(self):
        peer = self.hair()
        self.f.record['physics'] = {'backend':'LEGACY_CAGE','proxy':self.f.proxy.name}
        self.f.source[physics.SKIRT_RECORD] = json.dumps(self.f.record)
        before = dict(peer)
        self.on()
        self.assertEqual(dict(peer), before)
        self.assertFalse(self.prepared)

    def test_wrong_token_source_refuses_before_recovering_any_candidate(self):
        peer = self.hair()
        self.f.hair.prepare_recovery = lambda context, source: dict(source=self.f.source, needed=True)
        self.on()
        self.assertTrue(peer['native_hair_flags'])
        self.assertFalse(self.recovered)
        self.assertIn('saved Hair owner changed', physics.status(self.f.context)['dresses'][0]['last_error'])


if __name__ == '__main__':
    unittest.main()
