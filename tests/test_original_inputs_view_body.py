"""Actual Body-reference transaction with fake native boundaries, not Blender QA.

The production view_body function performs the mutations and rollback. Native
activation/admission, pose evaluation and ID objects are deliberately simulated;
passing tests do not establish Blender latency, geometry, or Undo behavior.
"""
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = (Path(__file__).resolve().parents[1] / 'addons' / 'character_designer'
          / 'unity_animation_original_inputs.py')


class ID:
    def __init__(self, name):
        self.name = name

    def __deepcopy__(self, memo):
        return self


class Rig(dict):
    def as_pointer(self):
        return 104

    def __deepcopy__(self, memo):
        return self


class Tracks(list):
    def get(self, name):
        return next((track for track in self if track.name == name), None)


class World:
    def __init__(self, *, tracks=True, role='BASE', tweak=False, enabled=True):
        self.body, self.source, self.inputs = ID('Body Custom'), ID('Body Source'), ID('Custom20')
        self.scene = ID('Owned Scene')
        self.context = NS(scene=self.scene)
        self.rig = Rig(user_value=4)
        self.rig.animation_data = NS(use_tweak_mode=tweak, nla_tracks=Tracks())
        self.pose, self.display = 'original pose', {'selected': 'Right Target'}
        self.events, self.activation_calls, self.active_calls = [], 0, 0
        self.failure, self.rollback_failure = None, None
        self.primary = RuntimeError('injected Body switch failure')
        self.session = dict(rig=self.rig, scene=self.scene, tracks={}, edit_role=role,
            tweak_enabled=tweak, enabled=enabled,
            input_action=self.inputs, preview_reference={'legacy_side': 'CUSTOM'},
            original_inputs={'body_action': self.body, 'body_source': self.source},
            properties_before={'owned_input': {'present': False}})
        if tracks:
            self.make_tracks()
        self.native = NS(_BUSY=set(), _SUSPENDED={}, _detached=deepcopy,
            activate=self.activate, _tracks=self.tracks, _assign_preview_base=self.assign,
            _store=self.store, _restore_live_pose=self.restore_live_pose)
        self.composition = NS(_prior=self.prior, _restore_prior=self.restore_prior,
            _tweak=self.tweak, _slot=lambda action, slot: slot, _plain=lambda value: value,
            _STRIP_FIELDS=('frame_start', 'frame_end', 'scale'))
        self.ns = dict(native=self.native, composition=self.composition,
            bone_display=NS(_snapshot=lambda rig: deepcopy(self.display), _restore=self.restore_display),
            _active=self.active, _reference=lambda pair, side: {'legacy_side': side},
            preview_base_timing=self.timing)
        names = {'_require', 'view_body'}
        nodes = [node for node in ast.parse(SOURCE.read_text(encoding='utf-8')).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        if {node.name for node in nodes} != names:
            raise AssertionError('The tested Body-reference API is missing.')
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)

    def make_tracks(self):
        for role, action in (('BASE', self.body), ('RIGGING', self.inputs)):
            strip = NS(action=action, action_slot=7, frame_start=1., frame_end=59., scale=1.)
            track = NS(name=role, strips=[strip])
            self.rig.animation_data.nla_tracks.append(track)
            self.session['tracks'][role] = {'name': role, 'action': action, 'slot': 7,
                'fields': {'frame_start': 1., 'frame_end': 59., 'scale': 1.}}

    def active(self, context, rig):
        self.active_calls += 1
        if self.failure == 'admission':
            raise self.primary
        return self.session

    def tracks(self, session):
        return {role: self.rig.animation_data.nla_tracks.get(row['name'])
                for role, row in session['tracks'].items()}

    def activate(self, context, session, *, edit_role, enabled):
        self.activation_calls += 1
        self.events.append(('activate', self.activation_calls))
        pointer = self.rig.as_pointer()
        self.native._BUSY.add(pointer)
        try:
            if not session['tracks']:
                self.make_tracks()
                self.rig['owned_input'] = 7
            session.update(edit_role=edit_role, tweak_enabled=enabled, enabled=True)
            self.rig.animation_data.use_tweak_mode = enabled
            self.pose = 'evaluated ' + session['preview_reference']['legacy_side']
            if self.failure == 'activate' + str(self.activation_calls):
                raise self.primary
            return session
        finally:
            # The actual native boundary clears its local busy membership.
            self.native._BUSY.discard(pointer)

    def timing(self, session, reference):
        source = reference['legacy_side'] == 'SOURCE'
        return (self.source if source else self.body), 9 if source else 7, {'end': 31., 'scale': 2.}

    def assign(self, strip, action, slot, timing):
        self.events.append(('assign', self.rig.as_pointer() in self.native._BUSY))
        strip.action, strip.action_slot = action, slot
        strip.frame_end, strip.scale = timing['end'], timing['scale']
        if self.failure == 'assign':
            raise self.primary

    def store(self, rig, session):
        self.events.append(('store', self.rig.as_pointer() in self.native._BUSY))
        if self.rollback_failure == 'store' and session['preview_reference']['legacy_side'] == 'CUSTOM':
            raise RuntimeError('rollback owner store failed')

    def prior(self, context, rig):
        return {'pose': self.pose, 'tweak': rig.animation_data.use_tweak_mode}

    def restore_prior(self, context, rig, prior):
        self.pose = prior['pose']
        rig.animation_data.use_tweak_mode = prior['tweak']
        if self.rollback_failure == 'prior':
            raise RuntimeError('rollback native prior failed')

    def restore_display(self, rig, display):
        self.display = deepcopy(display)
        if self.rollback_failure == 'display':
            raise RuntimeError('rollback display failed')

    def restore_live_pose(self, rig, prior):
        self.pose = prior['pose']

    def tweak(self, context, rig, track, *, enabled):
        self.rig.animation_data.use_tweak_mode = enabled

    def state(self):
        return deepcopy({'session': self.session, 'properties': dict(self.rig),
            'pose': self.pose, 'display': self.display,
            'tweak': self.rig.animation_data.use_tweak_mode,
            'busy': self.native._BUSY, 'suspended': self.native._SUSPENDED,
            'strips': [(track.name, [(strip.action, strip.action_slot, strip.frame_start,
                                    strip.frame_end, strip.scale) for strip in track.strips])
                       for track in self.rig.animation_data.nla_tracks]})

    def switch(self):
        return self.ns['view_body'](self.context, self.rig, 'SOURCE')


class BodyViewTests(unittest.TestCase):
    def test_ready_base_switch_evaluates_only_new_reference_once(self):
        w = World()
        result = w.switch()
        self.assertIs(result, w.session)
        self.assertEqual(w.activation_calls, 1)
        self.assertEqual(w.session['preview_reference']['legacy_side'], 'SOURCE')
        self.assertIs(w.tracks(w.session)['BASE'].strips[0].action, w.source)
        self.assertEqual((w.session['edit_role'], w.session['tweak_enabled'], w.session['enabled']),
                         ('BASE', False, True))
        self.assertEqual(w.pose, 'evaluated SOURCE')
        self.assertIn(('assign', True), w.events)
        self.assertFalse(w.native._BUSY)

    def test_setup_or_edit_transition_keeps_both_activations(self):
        cases = ({'tracks': False}, {'role': 'RIGGING'}, {'role': 'RIGGING', 'tweak': True})
        for options in cases:
            with self.subTest(options=options):
                w = World(**options)
                w.switch()
                self.assertEqual(w.activation_calls, 2)
                self.assertEqual(w.session['preview_reference']['legacy_side'], 'SOURCE')
                self.assertIn(('assign', True), w.events)
                self.assertFalse(w.rig.animation_data.use_tweak_mode)
                self.assertFalse(w.native._BUSY)

    def test_partial_reference_or_activation_failure_restores_state_and_reraises(self):
        cases = (({}, 'assign'), ({'enabled': False}, 'activate1'),
                 ({'role': 'RIGGING', 'tweak': True}, 'activate1'),
                 ({'role': 'RIGGING', 'tweak': True}, 'activate2'),
                 ({'tracks': False}, 'activate2'))
        for options, fault in cases:
            with self.subTest(options=options, fault=fault):
                w = World(**options)
                before = w.state()
                w.failure = fault
                with self.assertRaises(RuntimeError) as caught:
                    w.switch()
                self.assertIs(caught.exception, w.primary)
                self.assertEqual(w.state(), before)

    def test_rollback_failure_is_attached_without_swallowing_primary(self):
        for stage in ('prior', 'display', 'store'):
            with self.subTest(stage=stage):
                w = World()
                before = w.state()
                w.failure, w.rollback_failure = 'assign', stage
                with self.assertRaises(RuntimeError) as caught:
                    w.switch()
                self.assertIs(caught.exception, w.primary)
                notes = '\n'.join(getattr(caught.exception, '__notes__', ()))
                self.assertIn('Read-only Body view recovery:', notes)
                self.assertIn('rollback', notes)
                self.assertIn(stage if stage != 'prior' else 'native prior', notes)
                self.assertEqual(w.state(), before)

    def test_admission_failure_has_no_mutation(self):
        w = World()
        before = w.state()
        w.failure = 'admission'
        with self.assertRaises(RuntimeError) as caught:
            w.switch()
        self.assertIs(caught.exception, w.primary)
        self.assertEqual(w.state(), before)
        self.assertEqual(w.events, [])

    def test_busy_or_suspended_outer_transaction_is_rejected_unchanged(self):
        for kind in ('busy', 'suspended'):
            with self.subTest(kind=kind):
                w = World()
                if kind == 'busy':
                    w.native._BUSY.add(w.rig.as_pointer())
                else:
                    w.native._SUSPENDED[w.rig.as_pointer()] = 2
                before = w.state()
                with self.assertRaisesRegex(ValueError, 'outside an active preview transaction'):
                    w.switch()
                self.assertEqual(w.state(), before)
                self.assertEqual(w.active_calls, 0)
                self.assertEqual(w.events, [])

    def test_foreign_scene_is_rejected_before_reference_or_activation(self):
        w = World()
        w.context.scene = ID('Foreign Scene')
        before = w.state()
        with self.assertRaisesRegex(ValueError, 'saved owning scene'):
            w.switch()
        self.assertEqual(w.active_calls, 1)
        self.assertEqual(w.events, [])
        self.assertEqual(w.state(), before)


if __name__ == '__main__':
    unittest.main()
