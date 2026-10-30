"""Native Off-cycle regression, derived from the pinned Hair2 native fixture.

Trusted isolated background only. --expect-residual selects a read-only profile
of the old Off defect instead of candidate acceptance. No artist file is opened.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

EXPECT_RESIDUAL = '--expect-residual' in sys.argv
if EXPECT_RESIDUAL:
    sys.argv.remove('--expect-residual')
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_hair_wiggle2_blender08 as n


def snapshot(source, dress, rig, data):
    return {'hair': n.native_state(rig, n.hair_names(data)),
            'skirt': n.native_state(rig, n.dress_names(dress)),
            'scene': n.runtime._raw(n.bpy.context.scene, 'wiggle_enable'),
            'rig': n.runtime._raw(rig, 'wiggle_enable'),
            'scene_group': n.runtime._group(n.bpy.context.scene),
            'hair_marker': source.get(n.hair._RECOVERY_KEY),
            'skirt_state': dress.get(n.skirt.STATE_KEY),
            'permission': rig.get(n.control.STATE_KEY)}


class PhysicsOffNativeTests(unittest.TestCase):
    tearDown = n.HairWiggle2NativeTests.tearDown
    test_03_part_pause_resume = n.HairWiggle2NativeTests.test_03_part_pause_resume_keeps_peer_solving
    test_04_master_off_on = n.HairWiggle2NativeTests.test_04_master_off_on_preserves_preference_and_author_inputs
    test_06_configure_failure = n.HairWiggle2NativeTests.test_06_real_configure_failure_rolls_back_only_hair
    test_07_save_reopen = n.HairWiggle2NativeTests.test_07_savecopy_reopen_has_no_automatic_reconnect
    test_08_history_recovery = n.HairWiggle2NativeTests.test_08_undo_redo_owned_residue_master_off_recovery
    test_09_resume_rollback = n.HairWiggle2NativeTests.test_09_scoped_error_stop_identity_and_resume_playback_rollback

    def test_13_off_frames_have_no_owned_solver_or_deep_guard_calls(self):
        source, dress, rig, data, profiles = n.fixture()
        n.start_skirt(dress)
        n.start_hair(source, data, profiles)
        n.advance(2)
        before_author = n.author(source, dress, rig)
        n.control.set_enabled(n.bpy.context, False)
        before = snapshot(source, dress, rig, data)
        codes = {n.BACKEND.reset_bone.__code__: 'native_reset_bone',
                 n.hair._changed.__code__: 'hair_deep_guard',
                 n.skirt._changed.__code__: 'dress_deep_guard'}
        calls = dict.fromkeys(codes.values(), 0)
        def profile(frame, event, arg):
            if event == 'call' and frame.f_code in codes:
                calls[codes[frame.f_code]] += 1
        sys.setprofile(profile)
        try:
            n.advance(3)
            n.hair._mode_timer()
            n.skirt._timer()
        finally:
            sys.setprofile(None)
        row = {'case': 'off_frame_owned_work', 'calls': calls,
               'native_members': sorted(n.runtime._members(n.bpy.context.scene)),
               'scene_enable': n.bpy.context.scene.wiggle_enable,
               'expected_residual': EXPECT_RESIDUAL}
        n.REPORT['off_frame_work'] = row
        if EXPECT_RESIDUAL:
            n.require(calls['native_reset_bone'] > 0 and calls['hair_deep_guard'] > 0
                      and calls['dress_deep_guard'] > 0, 'Old residual was not reproduced.')
        else:
            n.require(not any(calls.values()), 'Off frames still invoked an owned solver or deep guard.')
            n.require(not n.runtime._members(n.bpy.context.scene), 'Paused owners remain in the native solver list.')
            n.require(not n.bpy.context.scene.wiggle_enable and not rig.wiggle_enable,
                      'All-paused scene/rig still enabled native solve.')
            n.require(snapshot(source, dress, rig, data) == before, 'Off frames mutated native state or markers.')
            n.assert_author(before_author, source, dress, rig)
            n.control.set_enabled(n.bpy.context, True)
            n.require(n.facade.status()['active'] and not n.facade.status()['paused'], 'Hair resume failed.')
            n.require(n.skirt.status(n.bpy.context, dress)['running'], 'Dress resume failed.')
            n.advance(2)
            n.assert_author(before_author, source, dress, rig)
        row['passed'] = True
        n.REPORT['cases'].append(row)

    def test_14_pause_detach_and_marker_faults_roll_back_exactly(self):
        source, dress, rig, data, profiles = n.fixture()
        n.start_skirt(dress)
        n.start_hair(source, data, profiles)
        before_author = n.author(source, dress, rig)
        before = snapshot(source, dress, rig, data)
        remove = n.runtime._list_remove
        def fault_after_detach(*args):
            remove(*args)
            raise RuntimeError('injected detach failure')
        with patch.object(n.runtime, '_list_remove', side_effect=fault_after_detach):
            with self.assertRaisesRegex(RuntimeError, 'injected detach failure'):
                n.control.set_enabled(n.bpy.context, False)
        n.require(snapshot(source, dress, rig, data) == before, 'Detach fault lost native/marker state.')
        with patch.object(n.hair, '_write_marker', side_effect=RuntimeError('injected marker failure')):
            with self.assertRaisesRegex(RuntimeError, 'marker failure'):
                n.hair.set_preview_paused(n.bpy.context, source, True)
        n.require(snapshot(source, dress, rig, data) == before, 'Marker fault lost native/marker state.')
        n.assert_author(before_author, source, dress, rig)
        n.REPORT['cases'].append({'case': 'detach_and_marker_faults', 'passed': True})

    def test_15_paused_native_undo_redo_recovers_detached_marker(self):
        source, dress, rig, data, profiles = n.fixture()
        n.require(n.bpy.ops.ed.undo_push.poll(), 'Native Undo setup unavailable.')
        n.bpy.context.preferences.edit.use_global_undo = True
        n.bpy.ops.ed.undo_push(message='Off-cycle author baseline')
        n.start_skirt(dress)
        n.start_hair(source, data, profiles)
        n.control.set_enabled(n.bpy.context, False)
        names = source.name, dress.name, rig.name
        n.bpy.ops.ed.undo_push(message='Off-cycle paused detached state')
        n.require(n.bpy.ops.ed.undo.poll(), 'Native Undo unavailable.')
        n.bpy.ops.ed.undo()
        n.require(n.bpy.ops.ed.redo.poll(), 'Native Redo unavailable.')
        n.bpy.ops.ed.redo()
        source, dress, rig = (n.bpy.data.objects[name] for name in names)
        n.require(not n.runtime._LEASES, 'History silently reconnected ownership.')
        n.require(not n.runtime._members(n.bpy.context.scene), 'History restored detached list entries.')
        n.require(json.loads(source[n.hair._RECOVERY_KEY])['paused'], 'Hair paused recovery marker missing.')
        n.hair.prepare_recovery(n.bpy.context, source)
        n.skirt.prepare_recovery(n.bpy.context, dress)
        n.control.set_enabled(n.bpy.context, False)
        n.require(n.hair.preview_state(source)['stopped_confirmed'], 'Paused Hair history recovery failed.')
        n.require(n.skirt.preview_state(dress)['stopped_confirmed'], 'Paused Dress history recovery failed.')
        n.require(not n.runtime._LEASES and not n.runtime._members(n.bpy.context.scene), 'Recovery reconnected solve.')
        n.REPORT['cases'].append({'case': 'paused_native_history_recovery', 'passed': True})

    def test_16_paused_savecopy_reopen_stays_stopped(self):
        source, dress, rig, data, profiles = n.fixture()
        n.start_skirt(dress)
        n.start_hair(source, data, profiles)
        n.control.set_enabled(n.bpy.context, False)
        frame = n.bpy.context.scene.frame_current
        names = source.name, dress.name, rig.name
        with tempfile.TemporaryDirectory(prefix='off_cycle_', dir=n.args.report_json.parent) as folder:
            path = Path(folder) / 'paused.blend'
            n.require('FINISHED' in n.bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True), 'Paused SaveCopy failed.')
            n.require(not n.runtime._LEASES, 'Paused save retained a transient lease.')
            n.require('FINISHED' in n.bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=True), 'Paused reopen failed.')
            source, dress, rig = (n.bpy.data.objects[name] for name in names)
            n.require(n.bpy.context.scene.frame_current == frame, 'Paused save moved the frame.')
            n.require(not n.runtime._LEASES and not n.runtime._members(n.bpy.context.scene), 'Paused reopen auto resumed.')
            n.require(n.hair.preview_state(source)['stopped_confirmed'], 'Reopened Hair not stopped.')
            n.require(n.skirt.preview_state(dress)['stopped_confirmed'], 'Reopened Dress not stopped.')
        n.REPORT['cases'].append({'case': 'paused_savecopy_reopen_stopped', 'passed': True})

    def test_17_paused_external_author_edit_refuses_resume_and_stop_retains_edit(self):
        source, dress, rig, data, profiles = n.fixture()
        n.start_skirt(dress)
        n.start_hair(source, data, profiles)
        n.control.set_enabled(n.bpy.context, False)
        # Whitespace changes an exact owned source record without changing its
        # valid parsed geometry/weights. Resume may not borrow the old proof.
        edited = source[n.registry.REGISTRY_KEY] + ' '
        source[n.registry.REGISTRY_KEY] = edited
        before = snapshot(source, dress, rig, data)
        with self.assertRaisesRegex(ValueError, 'binding or registry changed'):
            n.hair.set_preview_paused(n.bpy.context, source, False)
        n.require(source[n.registry.REGISTRY_KEY] == edited, 'Resume overwrote the author record edit.')
        n.require(snapshot(source, dress, rig, data) == before, 'Rejected resume changed physics state.')
        n.facade.stop_preview(n.bpy.context, source=source)
        n.require(source[n.registry.REGISTRY_KEY] == edited, 'Stop overwrote the author record edit.')
        n.require(not n.facade.status()['active'], 'Safe Stop retained a Hair writer.')
        n.REPORT['cases'].append({'case': 'paused_author_record_edit_refusal_and_safe_stop', 'passed': True})


if EXPECT_RESIDUAL:
    class ResidualOnly(unittest.TestCase):
        tearDown = PhysicsOffNativeTests.tearDown
        test_residual = PhysicsOffNativeTests.test_13_off_frames_have_no_owned_solver_or_deep_guard_calls
    n.HairWiggle2NativeTests = ResidualOnly
else:
    n.HairWiggle2NativeTests = PhysicsOffNativeTests
n.REPORT['schema'] = 'character-designer.physics-off-cycle-native/1'
n.REPORT['off_cycle_script'] = n.pin(__file__)
n.REPORT['derived_fixture'] = n.pin(n.__file__)
n.REPORT['artist_operations'] = False
n.REPORT['fps_measurement'] = False
n.main()
