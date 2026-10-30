"""Two bounded Physics-permission Undo/Redo checks in a private background.

Use the trusted launcher, --addons-root, --report-json and --wiggle-file.
Unavailable native history contexts are SKIP, never claimed as acceptance.
No artist scene, GUI, automatic post-history reconnect or product patch is used.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import unittest

import bpy


ROOT = Path(__file__).resolve().parents[1]
OBSERVATIONS = []


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--addons-root', type=Path, required=True)
    parser.add_argument('--report-json', type=Path, required=True)
    parser.add_argument('--wiggle-file', type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])


def staged_modules():
    root = (OPTIONS.addons_root / 'character_designer').resolve()
    names = []
    for name, module in tuple(sys.modules.items()):
        if name == 'character_designer' or name.startswith('character_designer.'):
            path = getattr(module, '__file__', None)
            if path:
                if not Path(path).resolve().is_relative_to(root):
                    raise AssertionError('Non-staged product module: ' + name)
                names.append(name)
    return sorted(names)


def select_main(rig):
    cd.register()
    bpy.context.scene.character_designer_setup.rig = rig
    bpy.context.scene.character_designer_animation_worklist.workspace_path = ''
    bpy.context.preferences.edit.use_global_undo = True
    bpy.context.preferences.filepaths.temporary_directory = str(SCRATCH)
    staged_modules()


def frame():
    return bpy.context.scene.frame_current, bpy.context.scene.frame_subframe


def native_configuration(source):
    record = skirt.read_record(source)
    rig = source[skirt.RIG_KEY]
    names = [name for chain in record['chains'] for name in chain['phys']]
    flags = {name: {'head': rig.pose.bones[name].wiggle_head,
                    'tail': rig.pose.bones[name].wiggle_tail}
             for name in names}
    row = bpy.context.scene.wiggle.list.get(rig.name)
    listed = sorted(item.name for item in row.list if item.name in names) if row else []
    return {'flags': flags, 'listed_owned_bones': listed,
            'native_configuration_present': bool(listed) or any(
                values['head'] or values['tail'] for values in flags.values()),
            'owned_session': source.as_pointer() in motion._SESSIONS,
            'history_recovery_pending': motion._HISTORY_RECOVERY_PENDING,
            'permission_enabled': control.is_enabled(source, component='SKIRT')}


class PhysicsHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        motion._BACKEND_CACHE = None
        cls.backend, cls.owns_backend = dress.real_backend()

    @classmethod
    def tearDownClass(cls):
        motion.unregister()
        if cls.owns_backend:
            cls.backend.unregister()
            if (cls.backend.wiggle_pre in bpy.app.handlers.frame_change_pre
                    or cls.backend.wiggle_post in bpy.app.handlers.frame_change_post):
                raise AssertionError('Owned Wiggle callbacks remain registered')

    def tearDown(self):
        motion.unregister()

    def require_push(self):
        if not bpy.ops.ed.undo_push.poll():
            self.skipTest('Background context does not support native ed.undo_push')

    def undo(self):
        if not bpy.ops.ed.undo.poll():
            self.skipTest('Native ed.undo unavailable after two real checkpoints')
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})

    def redo(self):
        if not bpy.ops.ed.redo.poll():
            self.skipTest('Native ed.redo unavailable after Undo in this background context')
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})

    def assert_gate(self, source, enabled):
        self.assertEqual(control.is_enabled(source, component='SKIRT'), enabled)
        if enabled:
            control.require_enabled(source, bpy.context, component='SKIRT')
        else:
            with self.assertRaisesRegex(ValueError, 'Enable.*Physics'):
                control.require_enabled(source, bpy.context, component='SKIRT')

    def test_legacy_cloth_off_on_undo_redo_restores_native_flags_and_permission(self):
        motion.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 2
        bpy.context.scene.frame_set(1)
        maker = tuning_fixture.DressMotionTuningTests()
        with motion._native_writes(self.backend):
            sources, rig = maker.make()
        source = sources[0]
        select_main(rig)
        self.require_push()
        tuning.initialize(source)
        source_name, rig_name = source.name, rig.name
        record = skirt.read_record(source)
        waist = rig.pose.bones[record['controls']['waist']]
        waist.keyframe_insert(data_path='location', frame=1)
        waist.keyframe_insert(data_path='location', frame=2)
        before, before_frame = dress.protected(source), frame()
        record, _rig, _proxy, cloth = physics.validate_physics(source)
        self.assertEqual(physics.backend(record), physics.LEGACY_BACKEND)
        before_flags = cloth.show_viewport, cloth.show_render
        before_mode = control._mode(source, record)
        control.set_enabled(bpy.context, False)
        self.assertEqual(bpy.ops.ed.undo_push(message='Physics master Off / Cloth'), {'FINISHED'})
        control.set_enabled(bpy.context, True)
        self.assertEqual(bpy.ops.ed.undo_push(message='Physics master On / Cloth'), {'FINISHED'})
        for operation, enabled in ((self.undo, False), (self.redo, True)):
            operation()
            # Native history replaces IDs.  Reacquire by this fixture's saved
            # names only after the synchronous operator has returned.
            source, rig = bpy.data.objects[source_name], bpy.data.objects[rig_name]
            self.assertEqual(bpy.context.scene.character_designer_setup.rig, rig)
            self.assert_gate(source, enabled)
            if enabled:
                record, _rig, _proxy, cloth = physics.validate_physics(source)
            else:
                # The ordinary graph proof deliberately requires the unpaused
                # Cloth flags. Prove that refusal, then authenticate this exact
                # paused writer through the controller's resume preflight.
                entries = [item for item in control._read(rig)['suspended']
                           if item['source'] == source_name]
                self.assertEqual(len(entries), 1)
                entry = entries[0]
                self.assertEqual(entry['kind'], 'CLOTH')
                owned_source, record = control._source_for_entry(bpy.context, rig, entry)
                self.assertEqual(owned_source, source)
                proxy = bpy.context.scene.objects.get(entry['proxy'])
                self.assertIsNotNone(proxy)
                cloth = proxy.modifiers.get(entry['modifier'])
                self.assertIsNotNone(cloth)
                self.assertEqual(cloth.type, 'CLOTH')
                self.assertEqual(control._flags(cloth), [False, False])
                control._paused_cloth_ownership(source, record, proxy, cloth)
                with self.assertRaisesRegex(physics.SkirtPhysicsError, 'physics bone binding'):
                    physics.validate_physics(source)
            flags = cloth.show_viewport, cloth.show_render
            self.assertEqual(flags, before_flags if enabled else (False, False))
            self.assertEqual(control._mode(source, record), before_mode)
            self.assertEqual(dress.protected(source), before)
            self.assertEqual(frame(), before_frame)
            OBSERVATIONS.append({'case': 'LEGACY_CLOTH', 'history': operation.__name__,
                                 'permission_enabled': enabled, 'cloth_flags': list(flags),
                                 'mode': control._mode(source, record), 'author_exact': True,
                                 'paused_ownership_verified': not enabled,
                                 'unpaused_validator_rejected_paused': not enabled})

    def test_wiggle_off_history_preserves_auto_and_defers_reconnect_without_false_stop_claim(self):
        sources, rig = dress.fixture()
        source = sources[0]
        select_main(rig)
        self.require_push()
        source_name, rig_name = source.name, rig.name
        record = skirt.read_record(source)
        waist = rig.pose.bones[record['controls']['waist']]
        waist.keyframe_insert(data_path='location', frame=8)
        waist.keyframe_insert(data_path='location', frame=16)
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        control.set_enabled(bpy.context, False)
        before, phys, before_frame = dress.protected(source), dress.phys_channels(source), frame()
        self.assertEqual(bpy.ops.ed.undo_push(message='Physics master Off / Wiggle'), {'FINISHED'})
        control.set_enabled(bpy.context, True)
        self.assertTrue(motion.status(bpy.context, source)['running'])
        self.assertEqual(bpy.ops.ed.undo_push(message='Physics master On / Wiggle'), {'FINISHED'})
        for operation, enabled in ((self.undo, False), (self.redo, True)):
            operation()
            source, rig = bpy.data.objects[source_name], bpy.data.objects[rig_name]
            self.assertEqual(bpy.context.scene.character_designer_setup.rig, rig)
            self.assert_gate(source, enabled)
            self.assertEqual(motion.mode(source), 'AUTOMATIC')
            self.assertFalse(motion._SESSIONS)
            self.assertTrue(motion._HISTORY_RECOVERY_PENDING)
            observed = native_configuration(source)
            if not enabled:
                self.assertFalse(observed['native_configuration_present'])
            # On-history may restore native solver flags without a live Python
            # lease. Record that actual state instead of labelling it stopped.
            motion._resume_saved()
            self.assertEqual(native_configuration(source), observed)
            self.assertFalse(motion._SESSIONS)
            self.assertTrue(motion._HISTORY_RECOVERY_PENDING)
            self.assertEqual(dress.phys_channels(source), phys)
            self.assertEqual(dress.protected(source), before)
            self.assertEqual(frame(), before_frame)
            OBSERVATIONS.append(dict(observed, case='WIGGLE', history=operation.__name__,
                                     mode=motion.mode(source), author_exact=True,
                                     stopped_confirmed=not observed['native_configuration_present']))
        # Only this explicit authenticated operation reconnects/retires saved
        # native flags.  It is outside the Undo/Redo callbacks under test.
        motion.set_mode(bpy.context, source, 'MANUAL')
        self.assertFalse(native_configuration(source)['native_configuration_present'])
        self.assertEqual(dress.protected(source), before)


def main():
    global OPTIONS, SCRATCH, cd, control, motion, skirt, physics, tuning, dress, tuning_fixture
    OPTIONS = arguments()
    output = OPTIONS.report_json.resolve()
    if output.exists():
        raise RuntimeError('Use a new report filename')
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'cd.animation-physics-native-history/1', 'passed': False,
              'validation_complete': False, 'tests_run': 0, 'success_count': 0,
              'observations': OBSERVATIONS,
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'limits': ['Only tiny synthetic background fixtures; no artist or GUI history acceptance.',
                         'No automatic post-history reconnect is enabled.',
                         'Native configuration may survive On-history without a Python session.']}
    registered = False
    previous_undo = bpy.context.preferences.edit.use_global_undo
    try:
        if not bpy.app.background or bpy.data.filepath or not os.environ.get('BLENDER_USER_CONFIG'):
            raise RuntimeError('Use a fresh empty background from the trusted isolated launcher')
        SCRATCH = output.parent / (output.stem + '_scratch')
        SCRATCH.mkdir(exist_ok=False)
        sys.path[:0] = [str(OPTIONS.addons_root.resolve()), str(ROOT / 'tests')]
        cd = importlib.import_module('character_designer')
        staged_modules()
        control = importlib.import_module('character_designer.animation_physics')
        motion = importlib.import_module('character_designer.skirt_wiggle')
        skirt = importlib.import_module('character_designer.skirt_rig')
        physics = importlib.import_module('character_designer.skirt_physics')
        tuning = importlib.import_module('character_designer.skirt_motion_tuning')
        dress = importlib.import_module('test_skirt_wiggle_blender')
        tuning_fixture = importlib.import_module('test_skirt_motion_tuning_blender')
        sys.path.insert(0, str(OPTIONS.addons_root.resolve()))
        staged_modules()
        cd.register()
        registered = True
        report.update(blender=bpy.app.version_string, addons_root=str(OPTIONS.addons_root.resolve()),
                      module_file=cd.__file__, addon_version=list(cd.bl_info['version']),
                      script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(PhysicsHistoryTests))
        report.update(passed=result.wasSuccessful(), tests_run=result.testsRun,
                      success_count=result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
                      validation_complete=result.wasSuccessful() and not result.skipped and result.testsRun == 2,
                      failed=[(str(test), trace) for test, trace in result.failures],
                      errors=[(str(test), trace) for test, trace in result.errors],
                      skipped=[(str(test), reason) for test, reason in result.skipped],
                      staged_modules=staged_modules())
    except BaseException:
        import traceback
        report['bootstrap_error'] = traceback.format_exc()
        raise
    finally:
        try:
            if registered:
                cd.unregister()
            bpy.context.preferences.edit.use_global_undo = previous_undo
            report['normal_unregister'] = True
        except Exception:
            import traceback
            report['cleanup_error'] = traceback.format_exc()
            report['passed'] = report['validation_complete'] = False
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
