"""Physics suspension ownership, durable Cloth endpoints, and rollback tests."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_physics.py'
spec = importlib.util.spec_from_file_location('_animation_physics_fixture', SOURCE)
physics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(physics)


class Objects(list):
    def get(self, name):
        return next((item for item in self if item.name == name), None)


class Object(dict):
    def __init__(self, name, kind='ARMATURE'):
        super().__init__()
        self.name, self.type = name, kind
        self.library = self.override_library = self.parent = None
        self.is_editable = True
        self.pose = SimpleNamespace(bones=Objects())

    def as_pointer(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return self is not other


class PhysicsTests(unittest.TestCase):
    def setUp(self):
        physics._LAST_ERRORS.clear()
        self.rig = Object('Rig')
        self.rig.data = Object('RigData')
        self.rig.data.bones = {'Waist': object()}
        self.source = Object('Dress', 'MESH')
        self.record = {'owner': 'stable-owner', 'physics': {'backend': 'LEGACY_CAGE', 'proxy': 'Owned Proxy'},
                       'controls': {'waist': 'Waist'}, 'chains': []}
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.source[physics.SKIRT_RIG] = self.rig
        self.rig['character_designer_skirt_owner'] = 'stable-owner'
        self.rig['character_designer_skirt_source'] = self.source
        self.rig.data['character_designer_skirt_owner'] = 'stable-owner'
        self.cloth = SimpleNamespace(name='Owned Cloth', type='CLOTH', show_viewport=True,
                                     show_render=True, point_cache=SimpleNamespace(is_baking=False))
        self.proxy = Object('Owned Proxy', 'MESH')
        self.proxy['character_designer_skirt_owner'] = 'stable-owner'
        self.proxy['character_designer_skirt_source'] = self.source
        self.proxy.parent, self.proxy.parent_type, self.proxy.parent_bone = self.rig, 'OBJECT', ''
        armature = SimpleNamespace(name='Waist attachment', type='ARMATURE', object=self.rig, use_vertex_groups=True,
            use_bone_envelopes=False, vertex_group='', use_multi_modifier=False,
            show_viewport=True, show_render=True)
        self.proxy.modifiers = Objects([armature, self.cloth])
        self.context = SimpleNamespace(scene=SimpleNamespace(objects=Objects([self.rig, self.source, self.proxy]),
            character_designer_setup=SimpleNamespace(rig=self.rig)),
            view_layer=SimpleNamespace(update=lambda: None))
        self.context.mode = 'POSE'
        self.calls = []
        self.wiggle = SimpleNamespace(STATE_KEY='character_designer_skirt_wiggle_v1',
            _SESSIONS={}, mode=lambda source: 'AUTOMATIC',
            _state=lambda source: None,
            available=lambda: {'available': True}, _changed=lambda saved: '',
            set_mode=lambda context, source, mode: self.calls.append(('start', source.name, mode)),
            _stop=lambda key, **kw: self.calls.append(('stop', key, kw['preserve_preference'])))
        def pause_wiggle(context, source, paused):
            self.calls.append(('wiggle-pause', source.name, paused))
            self.wiggle._SESSIONS[source.as_pointer()]['paused'] = paused
        self.wiggle.set_paused = pause_wiggle
        self.wiggle.preview_state = self.wiggle_preview
        self.wiggle.prepare_recovery = self.prepare_wiggle_recovery
        self.hair_state = {'active': False}
        self.hair = SimpleNamespace(status=lambda context: dict(self.hair_state),
            available=lambda: {'available': True},
            resume_preview=lambda context, source: self.calls.append(('hair-resume', source.name)))
        def pause_hair(context, source, paused):
            self.calls.append(('hair', source.name, paused))
            if self.hair_state.get('source_pointer') == source.as_pointer():
                self.hair_state['paused'] = paused
        self.hair.set_preview_paused = pause_hair
        self.hair.stop_preview = lambda context, **kwargs: self.calls.append(('hair-stop', kwargs['source'].name))
        self.hair.preview_state = self.hair_preview
        self.hair.prepare_recovery = self.prepare_hair_recovery
        self.modules = {
            'body_original_mode': SimpleNamespace(active=lambda rig:
                'character_designer_body_original_mode_v1' in rig),
            'bone_display': SimpleNamespace(view_mode=lambda rig: None),
            'skirt_original_mode': SimpleNamespace(wiggle_original_context=lambda source: None),
            'skirt_rig': SimpleNamespace(_require_controls_for_setup=lambda source: None,
                physics_control=lambda source: ({'physics_influence': 1.0}, self.rig, 'influence')),
            'skirt_physics': SimpleNamespace(validate_physics=self.validate, _owned_mesh=self.owned_mesh),
            'skirt_motion_profiles': SimpleNamespace(read=lambda source, record: {'mode': 'AUTOMATIC'}),
            'skirt_motion_tuning': SimpleNamespace(apply=lambda context, sources, *, mode:
                self.calls.append(('cloth-mode', sources[0].name, mode))),
            'physics_dependencies': SimpleNamespace(status=lambda component, **kwargs:
                {'state': 'UNCHECKED', 'available': False, 'message': '', 'details': []}),
            'skirt_wiggle': self.wiggle, 'hair_wiggle_adapter': self.hair}
        self.patcher = patch.object(physics, '_module', side_effect=lambda name: self.modules[name])
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def validate(self, source):
        self.calls.append(('validate', source.name, self.cloth.show_viewport))
        return self.record, self.rig, self.proxy, self.cloth

    def owned_mesh(self, proxy, source, record):
        if (proxy.get('character_designer_skirt_owner') != record['owner']
                or proxy.get('character_designer_skirt_source') is not source):
            raise ValueError('foreign mesh ownership')

    def wiggle_preview(self, source):
        saved = self.wiggle._SESSIONS.get(source.as_pointer())
        active = saved is not None and saved.get('source') == source
        paused = bool(active and saved.get('paused', False))
        live = any(getattr(bone, 'wiggle_head', False) or getattr(bone, 'wiggle_tail', False)
                   for bone in self.rig.pose.bones)
        return dict(active=active, paused=paused, running=active and not paused,
                    stopped_confirmed=paused if active else not live,
                    pending_recovery=not active and live, message='')

    def prepare_wiggle_recovery(self, context, source):
        if not self.wiggle_preview(source)['stopped_confirmed']:
            raise ValueError('A Dress has an unowned live Wiggle configuration; choose its mode first.')
        return {'changed': False, 'stopped_confirmed': True}

    def hair_preview(self, source):
        current = self.hair.status(self.context)
        active = bool(current.get('active') and current.get('source_pointer') == source.as_pointer())
        paused = bool(active and current.get('paused', False))
        names = [name for item in json.loads(source.get(physics.HAIR_REGISTRY, '{"strands":[]}'))['strands']
                 for name in item['bones']]
        live = any(getattr(bone, 'wiggle_head', False) or getattr(bone, 'wiggle_tail', False)
                   for bone in self.rig.pose.bones if bone.name in names)
        return dict(active=active, paused=paused, running=active and not paused,
                    stopped_confirmed=paused if active else not live,
                    pending_recovery=not active and live, message='')

    def prepare_hair_recovery(self, context, source):
        if not self.hair_preview(source)['stopped_confirmed']:
            raise ValueError('Hair has an unowned Wiggle configuration; stop that preview first.')
        return {'changed': False, 'stopped_confirmed': True}

    def test_default_permission_does_not_create_saved_data(self):
        self.assertTrue(physics.is_enabled(self.source, component='SKIRT'))
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_off_disables_actual_cloth_and_preserves_mode_record(self):
        original = self.source[physics.SKIRT_RECORD]
        physics.set_enabled(self.context, False)
        self.assertEqual(physics._flags(self.cloth), [False, False])
        self.assertEqual(self.source[physics.SKIRT_RECORD], original)
        state = physics._read(self.rig)
        self.assertFalse(state['enabled'])
        self.assertEqual(state['suspended'][0]['flags'], [True, True])
        self.assertFalse(physics.is_enabled(self.source, component='SKIRT'))

    def test_cloth_roundtrip_uses_saved_json_not_ram_token(self):
        physics.set_enabled(self.context, False)
        encoded = self.rig[physics.STATE_KEY]
        self.rig[physics.STATE_KEY] = str(encoded)
        physics.set_enabled(self.context, True)
        self.assertEqual(physics._flags(self.cloth), [True, True])
        self.assertEqual(physics._read(self.rig)['suspended'], [])

    def test_asymmetric_cloth_flags_restore_exactly(self):
        self.cloth.show_render = False
        physics.set_enabled(self.context, False)
        physics.set_enabled(self.context, True)
        self.assertEqual(physics._flags(self.cloth), [True, False])

    def test_external_modifier_edit_refuses_without_overwrite(self):
        physics.set_enabled(self.context, False)
        before = self.rig[physics.STATE_KEY]
        self.cloth.show_viewport = True
        with self.assertRaisesRegex(ValueError, 'external edit'):
            physics.set_enabled(self.context, True)
        self.assertEqual(self.rig[physics.STATE_KEY], before)
        self.assertTrue(self.cloth.show_viewport)

    def test_changed_source_ownership_refuses_resume(self):
        physics.set_enabled(self.context, False)
        self.record['owner'] = 'replacement-owner'
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        with self.assertRaisesRegex(ValueError, 'setup changed'):
            physics.set_enabled(self.context, True)
        self.assertEqual(physics._flags(self.cloth), [False, False])

    def test_other_character_dress_untouched(self):
        other = Object('Other Rig')
        self.source[physics.SKIRT_RIG] = other
        physics.set_enabled(self.context, False)
        self.assertEqual(physics._flags(self.cloth), [True, True])
        self.assertEqual(physics._read(self.rig)['suspended'], [])

    def test_component_preferences_retained_under_master_off(self):
        physics.set_enabled(self.context, False)
        physics.set_component(self.context, 'SKIRT', False)
        physics.set_enabled(self.context, True)
        self.assertEqual(physics._flags(self.cloth), [False, False])
        physics.set_component(self.context, 'SKIRT', True)
        self.assertEqual(physics._flags(self.cloth), [True, True])
        self.assertIn(('cloth-mode', self.source.name, 'AUTOMATIC'), self.calls)

    def test_automatic_choice_is_queued_while_master_off(self):
        physics.set_enabled(self.context, False)
        physics.set_skirt_mode(self.context, self.source.name, 'AUTOMATIC', expected_owner='stable-owner')
        self.assertTrue(physics._skirt_choice(self.source, self.record)['pending'])
        self.assertEqual(physics._flags(self.cloth), [False, False])

    def test_mid_transaction_failure_restores_gate_and_native_flags(self):
        self.context.view_layer.update = lambda: (_ for _ in ()).throw(RuntimeError('injected update'))
        with self.assertRaisesRegex(RuntimeError, 'injected update'):
            physics.set_enabled(self.context, False)
        self.assertNotIn(physics.STATE_KEY, self.rig)
        self.assertEqual(physics._flags(self.cloth), [True, True])

    def test_restore_validation_failure_repauses_native_flags_and_gate(self):
        physics.set_enabled(self.context, False)
        old = self.rig[physics.STATE_KEY]
        self.modules['skirt_physics'].validate_physics = lambda source: (_ for _ in ()).throw(ValueError('changed graph'))
        with self.assertRaisesRegex(ValueError, 'changed graph'):
            physics.set_enabled(self.context, True)
        self.assertEqual(self.rig[physics.STATE_KEY], old)
        self.assertEqual(physics._flags(self.cloth), [False, False])

    def test_wiggle_pause_retains_automatic_preference(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._SESSIONS[self.source.as_pointer()] = {'source': self.source}
        physics.set_enabled(self.context, False)
        self.assertIn(('wiggle-pause', self.source.name, True), self.calls)
        self.assertEqual(physics._read(self.rig)['suspended'][0]['kind'], 'WIGGLE')
        physics.set_enabled(self.context, True)
        self.assertIn(('wiggle-pause', self.source.name, False), self.calls)
        self.assertFalse(any(call[0] in {'start', 'stop'} for call in self.calls))

    def test_foreign_wiggle_configuration_refuses_before_gate(self):
        self.record['physics'] = None
        self.record['chains'] = [{'phys': ['PHYS']}]
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.rig.pose.bones.append(SimpleNamespace(name='PHYS', wiggle_head=False, wiggle_tail=True))
        with self.assertRaisesRegex(ValueError, 'unowned live Wiggle'):
            physics.set_enabled(self.context, False)
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_wiggle_permission_roundtrip_does_not_replay_finished_session(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._SESSIONS[self.source.as_pointer()] = {'source': self.source}
        physics.set_enabled(self.context, False)
        self.wiggle._SESSIONS.clear()  # Save/Undo finished the owned preview.
        physics.set_enabled(self.context, True)
        self.assertFalse(any(call[0] in {'start', 'stop'} for call in self.calls))
        self.assertEqual([call for call in self.calls if call[0] == 'wiggle-pause'],
                         [('wiggle-pause', 'Dress', True)])
        self.assertEqual(physics._read(self.rig)['suspended'], [])

    def test_paused_wiggle_rollback_restores_actual_previous_pause(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._SESSIONS[self.source.as_pointer()] = {'source': self.source, 'paused': True}
        self.context.view_layer.update = lambda: (_ for _ in ()).throw(RuntimeError('injected update'))
        with self.assertRaisesRegex(RuntimeError, 'injected update'):
            physics.set_enabled(self.context, False)
        self.assertTrue(self.wiggle._SESSIONS[self.source.as_pointer()]['paused'])
        self.assertEqual([call for call in self.calls if call[0] == 'wiggle-pause'],
                         [('wiggle-pause', 'Dress', True), ('wiggle-pause', 'Dress', True)])

    def test_wiggle_transaction_rollback_restores_exact_saved_motion_metadata(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._SESSIONS[self.source.as_pointer()] = {'source': self.source, 'paused': False}
        before = '{"reason":"Keep this pre-existing message.","mode":"AUTOMATIC"}'
        self.source[self.wiggle.STATE_KEY] = before
        original_pause = self.wiggle.set_paused
        def pause(context, source, paused):
            original_pause(context, source, paused)
            source[self.wiggle.STATE_KEY] = json.dumps({'reason': 'paused' if paused else '', 'mode': 'AUTOMATIC'})
        self.wiggle.set_paused = pause
        self.context.view_layer.update = lambda: (_ for _ in ()).throw(RuntimeError('injected update'))
        with self.assertRaisesRegex(RuntimeError, 'injected update'):
            physics.set_enabled(self.context, False)
        self.assertEqual(self.source[self.wiggle.STATE_KEY], before)
        self.assertFalse(self.wiggle._SESSIONS[self.source.as_pointer()]['paused'])

    def pending_pair(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.add_hair()
        native = {'SKIRT': True, 'HAIR': True}
        for component, adapter in (('SKIRT', self.wiggle), ('HAIR', self.hair)):
            def preview(source, component=component):
                return dict(active=False, paused=False, running=False,
                    stopped_confirmed=not native[component], pending_recovery=native[component], message='')
            def prepare(context, source, component=component):
                self.calls.append(('prepare-recovery', component))
                return {'changed': native[component], 'stopped_confirmed': not native[component], 'component': component}
            def recover(context, source, token=None, component=component):
                self.assertFalse(physics._read(self.rig)['enabled'])
                self.calls.append(('recover', component))
                receipt = {'before': dict(native), 'component': component, 'stopped_confirmed': True}
                native[component] = False
                return receipt  # Deliberately a different object from prepared token.
            def rollback(context, receipt):
                self.calls.append(('rollback-recovery', receipt['component']))
                native.update(receipt['before'])
            adapter.preview_state, adapter.prepare_recovery = preview, prepare
            adapter.recover_saved, adapter.rollback_recovery = recover, rollback
        return native

    def test_master_off_recovers_proved_pending_components_after_all_preflight(self):
        native = self.pending_pair()
        result = physics.set_enabled(self.context, False)
        self.assertEqual(native, {'SKIRT': False, 'HAIR': False})
        self.assertEqual(self.calls[:4], [('prepare-recovery', 'SKIRT'), ('prepare-recovery', 'HAIR'),
                                        ('recover', 'SKIRT'), ('recover', 'HAIR')])
        self.assertTrue(result['dresses'][0]['stopped_confirmed'])
        self.assertTrue(result['hair']['stopped_confirmed'])

    def test_pending_recovery_rolls_back_actual_action_receipts_in_reverse_order(self):
        native = self.pending_pair()
        self.context.view_layer.update = lambda: (_ for _ in ()).throw(RuntimeError('injected update'))
        with self.assertRaisesRegex(RuntimeError, 'injected update'):
            physics.set_enabled(self.context, False)
        self.assertEqual(native, {'SKIRT': True, 'HAIR': True})
        self.assertEqual(self.calls[-2:], [('rollback-recovery', 'HAIR'), ('rollback-recovery', 'SKIRT')])
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_hair_recovery_needed_before_changed_is_valid_preflight(self):
        native = self.pending_pair()
        self.hair.prepare_recovery = lambda *args: {'needed': True, 'changed': False, 'stopped_confirmed': False}
        physics.set_enabled(self.context, False)
        self.assertFalse(native['HAIR'])

    def test_explicit_already_off_recovers_pending_without_revalidating_paused_cloth(self):
        native = self.pending_pair()
        physics._write(self.rig, dict(physics._fresh(), enabled=False))
        physics.set_enabled(self.context, False)
        self.assertEqual(native, {'SKIRT': False, 'HAIR': False})
        self.assertFalse(any(call[0] == 'validate' for call in self.calls))

    def test_unconfirmed_stop_keeps_backend_error_instead_of_generic_paused_success(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle.preview_state = lambda source: dict(active=False, paused=False, running=False,
            stopped_confirmed=False, pending_recovery=True, message='Saved physics needs review.')
        self.wiggle.prepare_recovery = lambda *args: (_ for _ in ()).throw(
            ValueError('Backend registration changed; cannot confirm stopped.'))
        with self.assertRaisesRegex(ValueError, 'Backend registration changed'):
            physics.set_enabled(self.context, False)
        result = physics.status(self.context)
        self.assertFalse(result['dresses'][0]['stopped_confirmed'])
        self.assertFalse(result['dresses'][0]['paused'])
        self.assertEqual(result['message'], 'Backend registration changed; cannot confirm stopped.')
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_native_pause_backend_error_is_not_hidden_by_paused_message(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._SESSIONS[self.source.as_pointer()] = {'source': self.source, 'paused': False}
        pause = self.wiggle.set_paused
        def backend_error(context, source, paused):
            pause(context, source, paused)
            return {'available': not paused, 'backend_error': 'Verified backend registration changed.' if paused else '',
                    'message': 'Dress physics is paused.'}
        self.wiggle.set_paused = backend_error
        with self.assertRaisesRegex(ValueError, 'Verified backend registration changed'):
            physics.set_enabled(self.context, False)
        self.assertFalse(self.wiggle._SESSIONS[self.source.as_pointer()]['paused'])
        self.assertNotIn(physics.STATE_KEY, self.rig)
        self.assertEqual(physics.status(self.context)['message'], 'Verified backend registration changed.')

    def test_unconfirmed_recovery_receipt_is_rolled_back_and_not_success(self):
        native = self.pending_pair()
        original_recover = self.wiggle.recover_saved
        def unconfirmed(*args, **kwargs):
            receipt = original_recover(*args, **kwargs)
            receipt.update(stopped_confirmed=False, reason='Native stop proof is incomplete.')
            return receipt
        self.wiggle.recover_saved = unconfirmed
        with self.assertRaisesRegex(ValueError, 'Native stop proof is incomplete'):
            physics.set_enabled(self.context, False)
        self.assertEqual(native, {'SKIRT': True, 'HAIR': True})
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_malformed_saved_permission_refuses(self):
        self.rig[physics.STATE_KEY] = json.dumps(dict(physics._fresh(), enabled=1))
        with self.assertRaisesRegex(ValueError, 'need review'):
            physics.require_enabled(self.source)

    def test_direct_sync_runs_after_gate_publication_and_on_rollback(self):
        self.record['physics']['backend'] = 'DIRECT_MAIN_CLOTH_V1'
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        observations = []
        def sync(source, record, state):
            enabled = physics.is_enabled(source, component='SKIRT')
            observations.append(enabled)
            self.cloth.show_viewport = self.cloth.show_render = enabled
        self.modules['skirt_surface_direct'] = SimpleNamespace(_sync=sync, _state=lambda source, record: {})
        physics.set_enabled(self.context, False)
        physics.set_enabled(self.context, True)
        self.assertEqual(observations, [False, True])
        self.assertEqual(physics._flags(self.cloth), [True, True])

    def test_hair_pause_does_not_call_stop_or_seek(self):
        hair_source = Object('Hair', 'MESH')
        hair_source[physics.HAIR_RECORD] = '{}'
        hair_source[physics.HAIR_RIG] = self.rig
        self.context.scene.objects.append(hair_source)
        self.hair.status = lambda context: {'active': True, 'source': hair_source.name, 'source_pointer': hair_source.as_pointer()}
        physics.set_enabled(self.context, False)
        self.assertIn(('hair', 'Hair', True), self.calls)
        physics.set_enabled(self.context, True)
        self.assertIn(('hair', 'Hair', False), self.calls)

    def test_deleted_hair_session_not_replayed_by_master_on(self):
        self.add_hair()
        self.hair.status = lambda context: {'active': False}
        physics.set_enabled(self.context, False)
        physics.set_enabled(self.context, True)
        self.assertFalse(any(call[0] in {'hair', 'hair-start', 'hair-resume'} for call in self.calls))

    def add_hair(self):
        source = Object('Hair', 'MESH')
        source[physics.HAIR_RECORD] = '{}'
        source[physics.HAIR_RIG] = self.rig
        self.context.scene.objects.append(source)
        registry = {'strands': [{'strand_id': 'one', 'bones': []}]}
        self.modules['hair_strand_registry'] = SimpleNamespace(read=lambda source, validate: registry)
        self.modules['hair_motion_profiles'] = SimpleNamespace(effective_all=lambda source, registry: {'one': {}})
        def start_hair(context, source, data, profiles, ids):
            self.calls.append(('hair-start', source.name, ids))
            self.hair_state.update(active=True, source=source.name,
                                   source_pointer=source.as_pointer(), paused=False)
        self.hair.start_preview = start_hair
        return source

    def test_saved_true_inactive_hair_single_enable_starts_owned_preview(self):
        source = self.add_hair()
        physics._write(self.rig, physics._fresh())
        self.assertFalse(physics.status(self.context)['hair']['active'])
        result = physics.set_component(self.context, 'HAIR', True)
        self.assertEqual(self.calls, [('hair-start', 'Hair', ['one'])])
        self.assertTrue(result['hair_enabled'])
        self.assertTrue(result['hair']['active'])
        self.assertTrue(result['hair']['running'])
        self.assertEqual(self.hair_state['source_pointer'], source.as_pointer())
        self.assertTrue(result['skirt_enabled'])
        self.assertEqual(physics._flags(self.cloth), [True, True])

    def test_saved_true_paused_hair_repeat_enable_resumes_without_start_or_timeline(self):
        source = self.add_hair()
        physics._write(self.rig, physics._fresh())
        original = self.rig[physics.STATE_KEY]
        self.hair_state.update(active=True, source=source.name,
                               source_pointer=source.as_pointer(), paused=True)
        self.context.scene.frame_current, self.context.scene.frame_subframe = 37, .25
        self.context.screen = SimpleNamespace(is_animation_playing=False)
        for _ in range(2):
            result = physics.set_component(self.context, 'HAIR', True)
            self.assertTrue(result['hair']['active'])
            self.assertFalse(result['hair']['paused'])
            self.assertTrue(result['hair']['running'])
            self.assertEqual(self.rig[physics.STATE_KEY], original)
            self.assertEqual((self.context.scene.frame_current, self.context.scene.frame_subframe), (37, .25))
            self.assertFalse(self.context.screen.is_animation_playing)
        self.assertEqual(self.calls, [('hair', 'Hair', False), ('hair', 'Hair', False)])

    def test_saved_true_paused_hair_resume_failure_rolls_back_native_pause_and_raw(self):
        source = self.add_hair()
        original = json.dumps(physics._fresh(), indent=2)
        self.rig[physics.STATE_KEY] = original
        self.hair_state.update(active=True, source=source.name,
                               source_pointer=source.as_pointer(), paused=True)
        self.context.view_layer.update = lambda: (_ for _ in ()).throw(RuntimeError('injected update'))
        with self.assertRaisesRegex(RuntimeError, 'injected update'):
            physics.set_component(self.context, 'HAIR', True)
        self.assertEqual(self.rig[physics.STATE_KEY], original)
        self.assertTrue(self.hair_state['paused'])
        self.assertEqual(self.calls, [('hair', 'Hair', False), ('hair', 'Hair', True)])

    def test_saved_true_foreign_active_hair_enable_refuses_with_exact_raw(self):
        source = self.add_hair()
        original = json.dumps(physics._fresh(), indent=2)
        self.rig[physics.STATE_KEY] = original
        foreign = Object(source.name, 'MESH')
        self.hair_state.update(active=True, source=foreign.name,
                               source_pointer=foreign.as_pointer(), paused=False)
        foreign_before = dict(self.hair_state)
        with self.assertRaisesRegex(ValueError, 'Another Hair preview'):
            physics.set_component(self.context, 'HAIR', True)
        self.assertEqual(self.rig[physics.STATE_KEY], original)
        self.assertEqual(self.hair_state, foreign_before)
        self.assertFalse(self.calls)
        self.assertIn('Another Hair preview', physics.status(self.context)['hair']['last_error'])

    def test_saved_true_hair_start_failure_restores_exact_raw(self):
        self.add_hair()
        original = json.dumps(physics._fresh(), indent=2)
        self.rig[physics.STATE_KEY] = original
        self.hair.start_preview = lambda *args: (_ for _ in ()).throw(ValueError('backend refused'))
        with self.assertRaisesRegex(ValueError, 'backend refused'):
            physics.set_component(self.context, 'HAIR', True)
        self.assertEqual(self.rig[physics.STATE_KEY], original)
        self.assertFalse(physics.status(self.context)['hair']['active'])
        self.assertIn('backend refused', physics.status(self.context)['hair']['last_error'])

    def test_inactive_hair_enable_failure_preserves_absent_permission(self):
        self.add_hair()
        self.hair.start_preview = lambda *args: (_ for _ in ()).throw(ValueError('backend refused'))
        with self.assertRaisesRegex(ValueError, 'backend refused'):
            physics.set_component(self.context, 'HAIR', True)
        self.assertNotIn(physics.STATE_KEY, self.rig)
        self.assertFalse(physics.status(self.context)['hair']['active'])
        self.assertIn('backend refused', physics.status(self.context)['hair']['last_error'])

    def test_explicit_hair_start_needs_no_permission_off_on_cycle(self):
        self.add_hair()
        physics.start_hair_preview(self.context)
        self.assertIn(('hair-start', 'Hair', ['one']), self.calls)
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_explicit_hair_resume_uses_visible_preview_action_not_master_pause_api(self):
        source = self.add_hair()
        self.hair.status = lambda context: {'active': True, 'source': source.name, 'source_pointer': source.as_pointer(), 'paused': False}
        physics.start_hair_preview(self.context)
        self.assertIn(('hair-resume', 'Hair'), self.calls)
        self.assertFalse(any(call[0] in {'hair', 'hair-start'} for call in self.calls))

    def test_independent_part_pause_does_not_touch_peer_solver(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._SESSIONS[self.source.as_pointer()] = {'source': self.source, 'paused': False}
        source = self.add_hair()
        self.hair.status = lambda context: {'active': True, 'source': source.name, 'source_pointer': source.as_pointer(), 'paused': False}
        physics.set_component(self.context, 'HAIR', False)
        self.assertIn(('hair', 'Hair', True), self.calls)
        self.assertFalse(any(call[0] == 'wiggle-pause' for call in self.calls))
        self.calls.clear()
        physics.set_component(self.context, 'SKIRT', False)
        self.assertEqual(self.calls, [('wiggle-pause', 'Dress', True)])
        self.assertFalse(any(call[0] in {'start', 'stop', 'hair', 'hair-start', 'hair-resume'} for call in self.calls))

    def test_master_pauses_and_resumes_both_without_starting_or_stopping(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._SESSIONS[self.source.as_pointer()] = {'source': self.source, 'paused': False}
        source = self.add_hair()
        hair_state = {'active': True, 'source': source.name, 'source_pointer': source.as_pointer(), 'paused': False}
        self.hair.status = lambda context: dict(hair_state)
        def pause_hair(context, source, paused):
            self.calls.append(('hair', source.name, paused))
            hair_state['paused'] = paused
        self.hair.set_preview_paused = pause_hair
        physics.set_enabled(self.context, False)
        self.assertTrue(hair_state['paused'])
        self.assertTrue(self.wiggle._SESSIONS[self.source.as_pointer()]['paused'])
        physics.set_enabled(self.context, True)
        self.assertFalse(hair_state['paused'])
        self.assertFalse(self.wiggle._SESSIONS[self.source.as_pointer()]['paused'])
        self.assertFalse(any(call[0] in {'start', 'stop', 'hair-start', 'hair-resume'} for call in self.calls))

    def test_unowned_hair_flat_wiggle_flags_refuse_pause_before_saved_permission(self):
        source = self.add_hair()
        source[physics.HAIR_REGISTRY] = json.dumps({'strands': [{'bones': ['Hair PHYS']}]})
        self.rig.pose.bones.append(SimpleNamespace(name='Hair PHYS', wiggle_head=False, wiggle_tail=True))
        with self.assertRaisesRegex(ValueError, 'unowned Wiggle configuration'):
            physics.set_component(self.context, 'HAIR', False)
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_explicit_hair_start_does_not_stop_other_source(self):
        self.add_hair()
        self.hair.status = lambda context: {'active': True, 'source': 'Other Hair'}
        with self.assertRaisesRegex(ValueError, 'Another Hair preview'):
            physics.start_hair_preview(self.context)
        self.assertNotIn(physics.STATE_KEY, self.rig)
        self.assertFalse(any(call[0] in {'hair', 'hair-start'} for call in self.calls))

    def test_same_name_replacement_is_not_the_live_hair_source(self):
        source = self.add_hair()
        foreign = Object(source.name, 'MESH')
        self.hair.status = lambda context: {'active': True, 'source': source.name,
                                           'source_pointer': foreign.as_pointer(), 'paused': False}
        self.assertFalse(physics.status(self.context)['hair']['active'])
        with self.assertRaisesRegex(ValueError, 'Another Hair preview'):
            physics.start_hair_preview(self.context)
        self.assertFalse(any(call[0] in {'hair', 'hair-start', 'hair-resume'} for call in self.calls))

    def test_explicit_stop_targets_own_hair_without_skirt_or_timeline_actions(self):
        source = self.add_hair()
        current = {'active': True, 'source': source.name,
                   'source_pointer': source.as_pointer(), 'cleanup_pending': True}
        self.hair.status = lambda context: current
        def stop(context, **kwargs):
            self.calls.append(('hair-stop', kwargs['source'].name))
            current['active'] = False
        self.hair.stop_preview = stop
        physics.stop_hair_preview(self.context)
        self.assertEqual(self.calls, [('hair-stop', 'Hair')])
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_stop_does_not_claim_success_when_adapter_retains_active_cleanup(self):
        source = self.add_hair()
        self.hair.status = lambda context: {'active': True, 'source': source.name,
                                           'source_pointer': source.as_pointer(), 'cleanup_pending': True}
        with self.assertRaisesRegex(ValueError, 'cleanup did not finish'):
            physics.stop_hair_preview(self.context)
        self.assertIn('cleanup did not finish', physics.status(self.context)['hair']['last_error'])

    def test_explicit_stop_refuses_foreign_same_named_hair(self):
        source = self.add_hair()
        foreign = Object(source.name, 'MESH')
        self.hair.status = lambda context: {'active': True, 'source': source.name,
                                           'source_pointer': foreign.as_pointer()}
        with self.assertRaisesRegex(ValueError, 'Another Hair preview'):
            physics.stop_hair_preview(self.context)
        self.assertFalse(self.calls)

    def test_pending_cleanup_is_not_running_and_on_retries_its_owned_stop(self):
        source = self.add_hair()
        self.hair.status = lambda context: {'active': True, 'source': source.name,
            'source_pointer': source.as_pointer(), 'cleanup_pending': True,
            'reason': 'Owned Hair cleanup failed.'}
        result = physics.status(self.context)['hair']
        self.assertTrue(result['cleanup_pending'])
        self.assertFalse(result['running'])
        self.assertIn('cleanup failed', result['message'])
        with self.assertRaisesRegex(ValueError, 'cleanup did not finish'):
            physics.start_hair_preview(self.context)
        self.assertEqual(self.calls, [('hair-stop', 'Hair')])

    def test_refused_stop_keeps_visible_cleanup_failure(self):
        source = self.add_hair()
        self.hair.status = lambda context: {'active': True, 'source': source.name,
            'source_pointer': source.as_pointer(), 'cleanup_pending': True}
        self.hair.stop_preview = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('Owned cleanup still failed.'))
        with self.assertRaisesRegex(ValueError, 'cleanup still failed'):
            physics.stop_hair_preview(self.context)
        result = physics.status(self.context)['hair']
        self.assertFalse(result['running'])
        self.assertEqual(result['last_error'], 'Owned cleanup still failed.')

    def test_failed_hair_start_preserves_absent_permission(self):
        self.add_hair()
        self.hair.start_preview = lambda *args: (_ for _ in ()).throw(ValueError('backend refused'))
        with self.assertRaisesRegex(ValueError, 'backend refused'):
            physics.start_hair_preview(self.context)
        self.assertNotIn(physics.STATE_KEY, self.rig)

    def test_failed_hair_enable_restores_exact_saved_permission(self):
        self.add_hair()
        physics.set_component(self.context, 'HAIR', False)
        old = self.rig[physics.STATE_KEY]
        self.hair.start_preview = lambda *args: (_ for _ in ()).throw(ValueError('backend refused'))
        with self.assertRaisesRegex(ValueError, 'backend refused'):
            physics.set_component(self.context, 'HAIR', True)
        self.assertEqual(self.rig[physics.STATE_KEY], old)

    def test_status_invalid_permission_is_not_validated_default_true(self):
        self.rig[physics.STATE_KEY] = 'broken JSON'
        result = physics.status(self.context)
        self.assertFalse(result['valid'])
        self.assertIn('review', result['message'])

    def test_status_does_not_call_backend_hashing_availability(self):
        self.wiggle.available = self.hair.available = lambda: (_ for _ in ()).throw(AssertionError('draw hashed source'))
        result = physics.status(self.context)
        self.assertTrue(result['valid'])

    def test_same_name_foreign_cloth_refuses_before_any_setter(self):
        physics.set_enabled(self.context, False)
        before = self.rig[physics.STATE_KEY]
        replacement = Object(self.proxy.name, 'MESH')
        replacement.modifiers = self.proxy.modifiers
        self.context.scene.objects[self.context.scene.objects.index(self.proxy)] = replacement
        with patch.object(physics, '_set_flags', wraps=physics._set_flags) as setter:
            with self.assertRaisesRegex(ValueError, 'foreign mesh ownership'):
                physics.set_enabled(self.context, True)
            setter.assert_not_called()
        self.assertEqual(self.rig[physics.STATE_KEY], before)
        self.assertEqual(physics._flags(self.cloth), [False, False])

    def test_changed_cloth_binding_refuses_before_any_setter(self):
        physics.set_enabled(self.context, False)
        before = self.rig[physics.STATE_KEY]
        self.proxy.modifiers[0].object = Object('Other armature')
        with patch.object(physics, '_set_flags', wraps=physics._set_flags) as setter:
            with self.assertRaisesRegex(ValueError, 'native bone binding'):
                physics.set_enabled(self.context, True)
            setter.assert_not_called()
        self.assertEqual(self.rig[physics.STATE_KEY], before)

    def test_copied_legacy_source_cannot_claim_original_rig(self):
        physics.set_enabled(self.context, False)
        before = self.rig[physics.STATE_KEY]
        replacement = Object(self.source.name, 'MESH')
        replacement.update(self.source)
        self.context.scene.objects[self.context.scene.objects.index(self.source)] = replacement
        with patch.object(physics, '_set_flags', wraps=physics._set_flags) as setter:
            with self.assertRaisesRegex(ValueError, 'another character'):
                physics.set_enabled(self.context, True)
            setter.assert_not_called()
        self.assertEqual(self.rig[physics.STATE_KEY], before)
        self.assertFalse(any(call[0] == 'start' for call in self.calls))

    def test_hair_already_paused_rolls_back_to_paused(self):
        source = self.add_hair()
        self.hair.status = lambda context: {'active': True, 'source': source.name, 'source_pointer': source.as_pointer(), 'paused': True}
        self.context.view_layer.update = lambda: (_ for _ in ()).throw(RuntimeError('injected update'))
        with self.assertRaisesRegex(RuntimeError, 'injected update'):
            physics.set_enabled(self.context, False)
        self.assertEqual([call for call in self.calls if call[0] == 'hair'],
                         [('hair', 'Hair', True), ('hair', 'Hair', True)])

    def test_wiggle_status_distinguishes_saved_auto_from_running(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        result = physics.status(self.context)['dresses'][0]
        self.assertEqual(result['mode'], 'AUTOMATIC')
        self.assertFalse(result['running'])
        self.assertIn('Choose Automatic to start', result['message'])
        self.wiggle._SESSIONS[self.source.as_pointer()] = {'source': self.source}
        self.assertTrue(physics.status(self.context)['dresses'][0]['running'])
        self.wiggle._SESSIONS[self.source.as_pointer()]['paused'] = True
        paused = physics.status(self.context)['dresses'][0]
        self.assertTrue(paused['active'])
        self.assertTrue(paused['paused'])
        self.assertFalse(paused['running'])

    def test_wiggle_status_keeps_saved_pause_and_backend_reason(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._state = lambda source: {'reason': 'Paused after Undo.'}
        self.modules['physics_dependencies'].status = lambda component, **kwargs: {
            'state': 'MISSING', 'available': False, 'message': 'Backend is missing.', 'details': []}
        result = physics.status(self.context)['dresses'][0]
        self.assertIn('Paused after Undo.', result['message'])
        self.assertIn('Backend is missing.', result['dependency']['message'])

    def test_actual_start_refusal_is_retained_only_for_target_rig_source(self):
        with self.assertRaisesRegex(ValueError, 'selected Dress setup changed'):
            physics.set_skirt_mode(self.context, self.source.name, 'AUTOMATIC', expected_owner='wrong-owner')
        self.assertIn('setup changed', physics.status(self.context)['dresses'][0]['last_error'])
        other = Object('Other Dress', 'MESH')
        self.assertEqual(physics._last_error(self.rig, 'SKIRT', other), '')
        other_rig = Object('Other Rig')
        self.assertEqual(physics._last_error(other_rig, 'SKIRT', self.source), '')

    def test_fresh_dependency_ready_overrides_stale_legacy_false_cache(self):
        self.record['physics'] = None
        self.source[physics.SKIRT_RECORD] = json.dumps(self.record)
        self.wiggle._BACKEND_CACHE = {'available': False, 'reason': 'old disabled state'}
        self.modules['physics_dependencies'].status = lambda component, **kwargs: {
            'state': 'READY', 'available': True, 'message': 'verified enabled', 'details': []}
        row = physics.status(self.context)['dresses'][0]
        self.assertTrue(row['available'])
        self.assertNotIn('old disabled state', row['message'])

    def test_original_context_block_is_separate_from_ready_backend(self):
        self.modules['skirt_rig']._require_controls_for_setup = lambda source: (_ for _ in ()).throw(
            ValueError('Switch to Controls in Bone Display before changing the skirt setup.'))
        self.modules['physics_dependencies'].status = lambda component, **kwargs: {
            'state': 'READY', 'available': True, 'message': 'verified enabled', 'details': []}
        row = physics.status(self.context)['dresses'][0]
        self.assertTrue(row['requires_controls'])
        self.assertTrue(row['mode_blocked'])
        self.assertTrue(row['can_change_mode'])
        self.assertFalse(row['available'])
        self.assertEqual(physics.status(self.context)['dependencies']['SKIRT']['state'], 'READY')
        self.assertIn('Original mode in Bone Display', row['block_reason'])

    def test_return_controls_uses_existing_strict_original_return_only(self):
        self.modules['body_original_mode'] = SimpleNamespace(active=lambda rig: True,
            leave=lambda context, rig: self.calls.append(('strict-leave', rig.name)))
        self.modules['bone_display'] = SimpleNamespace(view_mode=lambda rig: 'NATIVE',
            restore_view=lambda rig: self.calls.append(('restore-view', rig.name)))
        physics.return_to_controls(self.context)
        self.assertIn(('strict-leave', 'Rig'), self.calls)
        self.assertNotIn(('restore-view', 'Rig'), self.calls)

    def test_return_controls_failure_preserves_original_guard_and_reason(self):
        self.modules['body_original_mode'] = SimpleNamespace(active=lambda rig: True,
            leave=lambda context, rig: (_ for _ in ()).throw(ValueError('Original Rest proof refused')))
        self.modules['bone_display'] = SimpleNamespace()
        with self.assertRaisesRegex(ValueError, 'Original Rest proof refused'):
            physics.return_to_controls(self.context)
        self.assertIn('Original Rest proof refused', physics._last_error(self.rig, 'SKIRT'))


if __name__ == '__main__':
    unittest.main()
