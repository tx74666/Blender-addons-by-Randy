"""The unified checklist reuses complete Finger captures and gates only first build."""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'addons'), str(ROOT/'tests')]
import character_designer
from character_designer import (body_calibration as c, body_calibration_ui as ui,
    body_setup_ui as actions, finger_definition, finger_targets)
from finger_tools_fixtures import bound_fixture, fingerprint, rig_state
from test_body_calibration_arms_wrist_blender import Layout


class UnifiedWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): character_designer.register()

    def setUp(self):
        self.mesh, self.rig, _ = bound_fixture()
        bpy.ops.object.mode_set(mode='OBJECT')
        self.mesh.select_set(False)
        self.rig.select_set(True)
        bpy.context.view_layer.objects.active = self.rig
        self.bank = self.mesh.character_designer_finger_bank

    def state(self): return c.finger_setup_status(bpy.context, self.rig)['state']

    def test_all_captures_are_confirmed_without_geometry_audit_or_data_changes(self):
        before = fingerprint(self.mesh), rig_state(self.rig), self.bank.survey
        records = [(s.name, s.guide.record, s.guide.confirmed, s.guide.pending) for s in self.bank.slots]
        with patch.object(c, '_finger_evidence', side_effect=AssertionError('Duplicate audit')), \
             patch.object(finger_definition, '_validate_mesh', side_effect=AssertionError('Geometry audit')):
            self.assertEqual(self.state(), 'CONFIRMED')
            s = c.settings(self.rig); s.part = 'FINGERS'; s.tab = 'CONTROLS'
            layout = Layout(); ui.draw(layout, bpy.context)
            self.assertNotIn('tab', layout.props)
            self.assertIn('Confirmed', layout.labels)
        self.assertEqual(before, (fingerprint(self.mesh), rig_state(self.rig), self.bank.survey))
        self.assertEqual(records, [(s.name, s.guide.record, s.guide.confirmed, s.guide.pending) for s in self.bank.slots])

    def test_one_complete_capture_does_not_confirm_the_other_nine(self):
        for slot in self.bank.slots:
            if slot.name != 'INDEX.L': slot.guide.record = ''
        self.assertEqual(self.state(), 'UNSET')

    def test_missing_slot_is_not_hidden_by_remaining_captures(self):
        self.bank.slots.remove(self.bank.slots.find('THUMB.R'))
        self.assertEqual(self.state(), 'UNSET')

    def test_unconfirmed_pending_and_wrong_source_need_review(self):
        guide = self.bank.slots['INDEX.L'].guide
        guide.confirmed = False
        self.assertEqual(self.state(), 'REVIEW')
        guide.confirmed = True; guide.pending = 'pending'
        self.assertEqual(self.state(), 'REVIEW')
        guide.pending = ''; guide.source = None
        self.assertEqual(self.state(), 'REVIEW')

    def test_invalid_saved_record_does_not_break_the_panel(self):
        guide = self.bank.slots['INDEX.L'].guide
        original = json.loads(guide.record)
        for record in ('invalid JSON', 'null', '[]', json.dumps({**original, 'internal': False}),
                       json.dumps({**original, 'bank_key': 'INDEX.R'})):
            with self.subTest(record=record[:40]):
                guide.record = record
                self.assertEqual(self.state(), 'REVIEW')
                layout = Layout(); ui.draw(layout, bpy.context)
                self.assertIn('Review', layout.labels)

    def test_current_rig_and_source_identity_cannot_be_borrowed(self):
        other = bpy.data.objects.new('OtherRig', self.rig.data.copy())
        bpy.context.collection.objects.link(other)
        self.assertEqual(c.finger_setup_status(bpy.context, other)['state'], 'UNSET')
        self.mesh.modifiers.new('Other binding', 'ARMATURE').object = other
        self.assertEqual(self.state(), 'REVIEW')

    def test_explicit_unbound_body_is_supported_but_not_another_rig(self):
        self.mesh.modifiers.clear()
        self.assertEqual(self.state(), 'CONFIRMED')
        bpy.context.scene.character_designer_setup.body = None
        self.assertEqual(self.state(), 'UNSET')

    def test_ambiguous_body_requires_explicit_selection(self):
        duplicate = self.mesh.copy(); duplicate.data = self.mesh.data.copy()
        bpy.context.collection.objects.link(duplicate)
        bpy.context.scene.character_designer_setup.body = None
        self.assertEqual(self.state(), 'REVIEW')
        bpy.context.scene.character_designer_setup.body = self.mesh
        self.assertEqual(self.state(), 'CONFIRMED')

    def test_empty_preallocated_slots_are_not_missing_anatomy(self):
        # Original Capture allocates ten slots even on a one-finger character.
        for slot in self.bank.slots:
            if slot.name != 'INDEX.L': slot.guide.record = ''; slot.guide.pending = ''
        survey = json.loads(self.bank.survey)
        survey['candidates'] = {'INDEX.L': survey['candidates']['INDEX.L']}
        self.bank.survey = json.dumps(survey)
        with patch.object(finger_targets, 'index', return_value={'INDEX.L': {}}):
            self.assertEqual(self.state(), 'CONFIRMED')

    def test_explicit_body_is_not_blocked_by_another_mesh_binding(self):
        other = bpy.data.objects.new('OtherRig', bpy.data.armatures.new('OtherRig'))
        bpy.context.collection.objects.link(other)
        duplicate = self.mesh.copy(); duplicate.data = self.mesh.data.copy()
        bpy.context.collection.objects.link(duplicate)
        duplicate.modifiers.new('Other binding', 'ARMATURE').object = other
        self.assertEqual(self.state(), 'CONFIRMED')
        bpy.context.scene.character_designer_setup.body = duplicate
        self.assertEqual(self.state(), 'REVIEW')

    def test_only_genuinely_absent_fingers_are_skipped(self):
        self.bank.survey = ''; self.bank.slots.clear()
        self.assertEqual(self.state(), 'UNSET')  # Native bones still exist.
        with patch.object(finger_targets, 'index', return_value={}):
            self.assertEqual(self.state(), 'SKIPPED')

    def test_generate_gate_uses_all_states_and_rechecks_when_clicked(self):
        ready = {p: {'state': 'CONFIRMED'} for p in c.PARTS}
        self.assertTrue(c.generation_ready(bpy.context, self.rig, items=ready))
        self.assertFalse(c.generation_ready(bpy.context, self.rig,
            items={**ready, 'ARMS': {'state': 'READY'}}))
        with patch.object(c, 'status', side_effect=lambda context, rig, part: ready[part]):
            layout = Layout(); actions.draw_actions(layout, bpy.context)
            self.assertEqual(layout.buttons, [('character_designer.body_setup', 'Generate')])
            self.bank.slots['THUMB.R'].guide.confirmed = False
            layout = Layout(); actions.draw_actions(layout, bpy.context)
            self.assertEqual(layout.buttons, [])
            reports = []
            operator = SimpleNamespace(action='GENERATE', report=lambda level, message: reports.append(message))
            with patch.object(actions._service(), 'generate', side_effect=AssertionError('Stale gate bypass')):
                result = actions.CHARACTERDESIGNER_OT_body_setup.execute(operator, bpy.context)
            self.assertEqual(result, {'CANCELLED'})
            self.assertIn('Confirm Arms', reports[0])

    def test_legacy_update_and_remove_ignore_first_build_gate(self):
        self.bank.slots['INDEX.L'].guide.record = 'invalid JSON'
        with patch.object(actions, '_has_generated', return_value=True), \
             patch.object(c, 'generation_ready', side_effect=AssertionError('Legacy first-build gate')):
            layout = Layout(); actions.draw_actions(layout, bpy.context)
            self.assertEqual([text for _, text in layout.buttons], ['Update', 'Edit Weights', 'Remove Generated Controls'])
            for action, function in (('GENERATE', 'generate'), ('REMOVE', 'remove')):
                with patch.object(actions._service(), function, return_value={}) as call:
                    operator = SimpleNamespace(action=action, report=lambda *args: None)
                    self.assertEqual(actions.CHARACTERDESIGNER_OT_body_setup.execute(operator, bpy.context), {'FINISHED'})
                    call.assert_called_once_with(bpy.context, self.rig)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(UnifiedWorkflowTests))
    if not result.wasSuccessful(): raise SystemExit(1)
