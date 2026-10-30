"""Original display blocks setup changes, not owned physics permission Off."""
import json
import unittest

import test_animation_physics as fixture


physics = fixture.physics
ORIGINAL_KEY = 'character_designer_body_original_mode_v1'
ORIGINAL_RAW = '{"owner":"original-session","display":"NATIVE","saved":[1,2,3]}'


class OriginalToggleTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PhysicsTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.rig[ORIGINAL_KEY] = ORIGINAL_RAW
        self.f.modules['skirt_rig']._require_controls_for_setup = self.refuse_setup

    @staticmethod
    def refuse_setup(source):
        raise ValueError('Switch to Controls in Bone Display before changing the skirt setup.')

    def stopped_dress(self, mode='MANUAL'):
        f = self.f
        f.record['physics'] = None
        f.source[physics.SKIRT_RECORD] = json.dumps(f.record)
        f.wiggle.mode = lambda source: mode
        f.source[f.wiggle.STATE_KEY] = json.dumps({'mode': mode, 'reason': 'Original keeps this mode.'})

    def assert_original_exact(self):
        self.assertEqual(self.f.rig[ORIGINAL_KEY], ORIGINAL_RAW)
        self.assertFalse(any(call[0] in {'strict-leave', 'restore-view'} for call in self.f.calls))

    def active_hair(self):
        f = self.f
        source = f.add_hair()
        state = {'active': True, 'source': source.name,
                 'source_pointer': source.as_pointer(), 'paused': False}
        f.hair.status = lambda context: dict(state)

        def pause(context, source, paused):
            f.calls.append(('hair', source.name, paused))
            state['paused'] = paused

        f.hair.set_preview_paused = pause
        return state

    def test_original_stopped_modes_master_off_repeat_on_preserves_saved_inputs(self):
        f = self.f
        for mode in ('MANUAL', 'AUTOMATIC'):
            with self.subTest(mode=mode):
                self.stopped_dress(mode)
                f.calls.clear()
                record_raw = f.source[physics.SKIRT_RECORD]
                mode_raw = f.source[f.wiggle.STATE_KEY]
                physics.set_enabled(f.context, False)
                self.assertFalse(physics._read(f.rig)['enabled'])
                physics.set_enabled(f.context, False)
                physics.set_enabled(f.context, True)
                self.assertTrue(physics._read(f.rig)['enabled'])
                self.assertEqual(f.source[physics.SKIRT_RECORD], record_raw)
                self.assertEqual(f.source[f.wiggle.STATE_KEY], mode_raw)
                self.assertEqual(f.calls, [])
                self.assert_original_exact()

    def test_original_stopped_dress_part_off_repeat_on_does_not_start_solver(self):
        f = self.f
        self.stopped_dress()
        mode_raw = f.source[f.wiggle.STATE_KEY]
        physics.set_component(f.context, 'SKIRT', False)
        self.assertFalse(physics._read(f.rig)['skirt_enabled'])
        physics.set_component(f.context, 'SKIRT', False)
        physics.set_component(f.context, 'SKIRT', True)
        self.assertTrue(physics._read(f.rig)['skirt_enabled'])
        self.assertEqual(f.source[f.wiggle.STATE_KEY], mode_raw)
        self.assertEqual(f.calls, [])
        self.assert_original_exact()

    def test_original_inactive_dress_does_not_block_active_hair_master_pause(self):
        f = self.f
        self.stopped_dress()
        state = self.active_hair()
        physics.set_enabled(f.context, False)
        self.assertTrue(state['paused'])
        physics.set_enabled(f.context, True)
        self.assertFalse(state['paused'])
        self.assertEqual(f.calls, [('hair', 'Hair', True), ('hair', 'Hair', False)])
        self.assert_original_exact()

    def test_original_owned_cloth_still_validates_and_roundtrips_native_flags(self):
        f = self.f
        record_raw = f.source[physics.SKIRT_RECORD]
        physics.set_enabled(f.context, False)
        self.assertEqual(physics._flags(f.cloth), [False, False])
        self.assertIn(('validate', 'Dress', True), f.calls)
        physics.set_enabled(f.context, True)
        self.assertEqual(physics._flags(f.cloth), [True, True])
        self.assertEqual(f.source[physics.SKIRT_RECORD], record_raw)
        self.assert_original_exact()

    def test_original_changed_native_graph_still_refuses_before_permission_or_writes(self):
        f = self.f

        def refuse_graph(source):
            raise ValueError('owned Cloth graph changed')

        f.modules['skirt_physics'].validate_physics = refuse_graph
        with self.assertRaisesRegex(ValueError, 'owned Cloth graph changed'):
            physics.set_enabled(f.context, False)
        self.assertNotIn(physics.STATE_KEY, f.rig)
        self.assertEqual(physics._flags(f.cloth), [True, True])
        self.assertEqual(f.calls, [])
        self.assert_original_exact()

    def test_original_changed_active_skirt_session_guard_is_not_bypassed(self):
        f = self.f
        self.stopped_dress('AUTOMATIC')
        f.wiggle._SESSIONS[f.source.as_pointer()] = {'source': f.source, 'paused': False}
        f.wiggle._changed = lambda saved: 'Original editing paused automatic Dress motion.'
        with self.assertRaisesRegex(ValueError, 'Dress simulation changed'):
            physics.set_enabled(f.context, False)
        self.assertNotIn(physics.STATE_KEY, f.rig)
        self.assertFalse(f.wiggle._SESSIONS[f.source.as_pointer()]['paused'])
        self.assertEqual(f.calls, [])
        self.assert_original_exact()

    def test_original_unowned_native_flags_still_refuse_before_permission(self):
        f = self.f
        self.stopped_dress('AUTOMATIC')
        f.rig.pose.bones.append(fixture.SimpleNamespace(name='PHYS', wiggle_head=False, wiggle_tail=True))
        with self.assertRaisesRegex(ValueError, 'unowned live Wiggle'):
            physics.set_enabled(f.context, False)
        self.assertNotIn(physics.STATE_KEY, f.rig)
        self.assertTrue(f.rig.pose.bones[0].wiggle_tail)
        self.assertEqual(f.calls, [])
        self.assert_original_exact()

    def test_original_later_hair_failure_restores_cloth_and_exact_prior_permission(self):
        f = self.f
        state = self.active_hair()
        physics._write(f.rig, physics._fresh())
        before = f.rig[physics.STATE_KEY]
        record_raw = f.source[physics.SKIRT_RECORD]
        pause = f.hair.set_preview_paused

        def fail_after_pause(context, source, paused):
            pause(context, source, paused)
            if paused:
                raise RuntimeError('injected Hair pause failure')

        f.hair.set_preview_paused = fail_after_pause
        with self.assertRaisesRegex(RuntimeError, 'injected Hair pause failure'):
            physics.set_enabled(f.context, False)
        self.assertEqual(f.rig[physics.STATE_KEY], before)
        self.assertEqual(physics._flags(f.cloth), [True, True])
        self.assertEqual(f.source[physics.SKIRT_RECORD], record_raw)
        self.assertFalse(state['paused'])
        self.assertEqual([call for call in f.calls if call[0] == 'hair'],
                         [('hair', 'Hair', True), ('hair', 'Hair', False)])
        self.assert_original_exact()


if __name__ == '__main__':
    unittest.main()
