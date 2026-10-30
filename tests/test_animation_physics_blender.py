"""Small, real-RNA Physics permission checks in one disposable Blender process.

Use tools/run_trusted_blender_validation.py with --background, --addons-root,
--report-json, --wiggle-file and --wiggle-root.  No artist file is opened.
The two external backends are exercised sequentially and remain unmodified.
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
ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--addons-root', type=Path, required=True)
    parser.add_argument('--report-json', type=Path, required=True)
    parser.add_argument('--wiggle-file', type=Path, required=True)
    parser.add_argument('--wiggle-root', type=Path, required=True)
    return parser.parse_args(ARGS)


def assert_staged_modules():
    package_root = (OPTIONS.addons_root / 'character_designer').resolve()
    checked = []
    for name, module in tuple(sys.modules.items()):
        if name != 'character_designer' and not name.startswith('character_designer.'):
            continue
        path = getattr(module, '__file__', None)
        if path:
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(package_root):
                raise AssertionError('Non-staged product module: ' + name + ': ' + str(resolved))
            checked.append(name)
    return sorted(checked)


def select_main(rig):
    cd.register()
    bpy.context.scene.character_designer_setup.rig = rig
    bpy.context.scene.character_designer_animation_worklist.workspace_path = ''
    assert control._selected_rig(bpy.context) == rig
    assert_staged_modules()


def current_frame():
    return bpy.context.scene.frame_current, bpy.context.scene.frame_subframe


def record_frame_callbacks(label):
    """Primitive callback provenance only; never call, remove or approve one."""
    snapshot = {'label': label}
    for event in ('frame_change_pre', 'frame_change_post'):
        rows = []
        for callback in tuple(getattr(bpy.app.handlers, event)):
            code = getattr(callback, '__code__', None)
            row = {'module': getattr(callback, '__module__', None),
                   'name': getattr(callback, '__name__', None),
                   'qualname': getattr(callback, '__qualname__', None),
                   'type': type(callback).__name__, 'process_identity': id(callback),
                   'code_file': getattr(code, 'co_filename', None),
                   'code_first_line': getattr(code, 'co_firstlineno', None)}
            path = Path(row['code_file']) if row['code_file'] else None
            if path is not None and path.is_file():
                row['file_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append(row)
        snapshot[event] = rows
    CALLBACKS.append(snapshot)
    return snapshot


def cache_config(cloth):
    cache = cloth.point_cache
    # Runtime is_outdated may legitimately change when evaluation flags change.
    return (cache.frame_start, cache.frame_end, cache.frame_step, cache.use_disk_cache,
            cache.use_external, cache.is_baked, cache.is_baking)


def cloth_coefficients(cloth):
    return tuple(getattr(cloth.settings, key) for key in
                 ('quality', 'mass', 'tension_stiffness', 'compression_stiffness',
                  'shear_stiffness', 'bending_stiffness', 'air_damping'))


def hair_channels():
    result = []
    for obj in bpy.context.scene.objects:
        result.append((obj.name, adapter._channels(obj, True)[1:]))
        if obj.type == 'ARMATURE':
            result.extend((obj.name + '/' + bone.name, adapter._channels(bone)[1:])
                          for bone in obj.pose.bones)
    return result


class NativeCase(unittest.TestCase):
    def tearDown(self):
        adapter.stop_preview(reason='Animation Physics native fixture cleanup.')
        adapter.unregister_guards()
        motion.unregister()


class DressPhysicsNativeTests(NativeCase):
    @classmethod
    def setUpClass(cls):
        record_frame_callbacks('before_dress_backend_registration')
        motion._BACKEND_CACHE = None
        cls.backend, cls.owns_backend = dress_fixture.real_backend()
        BACKENDS.append({'kind': 'DRESS', 'file': cls.backend.__file__,
                         'version': motion.BACKEND_VERSION, 'registered_here': cls.owns_backend})
        record_frame_callbacks('after_dress_backend_registration')

    @classmethod
    def tearDownClass(cls):
        motion.unregister()
        if cls.owns_backend:
            record_frame_callbacks('before_owned_dress_backend_unregister')
            cls.backend.unregister()
            record_frame_callbacks('after_owned_dress_backend_unregister')
            if (cls.backend.wiggle_pre in bpy.app.handlers.frame_change_pre
                    or cls.backend.wiggle_post in bpy.app.handlers.frame_change_post):
                raise AssertionError('The owned Wiggle 2 backend left its exact frame callbacks registered')
            BACKENDS[-1]['frame_callbacks_removed_after_unregister'] = True
        motion._BACKEND_CACHE = None

    def make_wiggle(self):
        sources, rig = dress_fixture.fixture()
        source = sources[0]
        select_main(rig)
        record = skirt.read_record(source)
        waist = rig.pose.bones[record['controls']['waist']]
        waist.keyframe_insert(data_path='location', frame=8)
        waist.keyframe_insert(data_path='location', frame=16)
        return source, rig

    def test_wiggle_master_pause_resume_keeps_mode_phys_frame_and_author(self):
        source, rig = self.make_wiggle()
        before = dress_fixture.protected(source)
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        for frame in (9, 10):
            bpy.context.scene.frame_set(frame)
        # Capture at the same evaluated time on both sides of the transition.
        before = dress_fixture.protected(source)
        phys, frame = dress_fixture.phys_channels(source), current_frame()
        control.set_enabled(bpy.context, False)
        self.assertEqual(motion.mode(source), 'AUTOMATIC')
        self.assertNotIn(source.as_pointer(), motion._SESSIONS)
        self.assertEqual(dress_fixture.phys_channels(source), phys)
        self.assertEqual(dress_fixture.protected(source), before)
        self.assertEqual(current_frame(), frame)
        control.set_enabled(bpy.context, True)
        self.assertTrue(motion.status(bpy.context, source)['running'])
        self.assertEqual(motion.mode(source), 'AUTOMATIC')
        self.assertEqual(dress_fixture.phys_channels(source), phys)
        self.assertEqual(dress_fixture.protected(source), before)
        self.assertEqual(current_frame(), frame)

    def test_master_off_blocks_existing_rig_start_entry(self):
        source, _rig = self.make_wiggle()
        control.set_enabled(bpy.context, False)
        before = dress_fixture.protected(source)
        with self.assertRaisesRegex(ValueError, 'Enable.*Physics'):
            motion._start(bpy.context, source)
        self.assertNotIn(source.as_pointer(), motion._SESSIONS)
        self.assertEqual(dress_fixture.protected(source), before)
        self.assertFalse(control.is_enabled(source, component='SKIRT'))

    def test_saved_off_permission_reopen_does_not_resume_automatic(self):
        source, rig = self.make_wiggle()
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        control.set_enabled(bpy.context, False)
        source_name, rig_name = source.name, rig.name
        before = dress_fixture.protected(source)
        phys, frame = dress_fixture.phys_channels(source), current_frame()
        path = SCRATCH / 'master_off_fixture.blend'
        if path.exists():
            raise AssertionError('Refusing to replace prior fixture evidence: ' + str(path))
        result = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, compress=False)
        self.assertIn('FINISHED', result)
        self.assertFalse(motion._SESSIONS)
        result = bpy.ops.wm.open_mainfile(filepath=str(path), use_scripts=True)
        self.assertIn('FINISHED', result)
        source, rig = bpy.data.objects[source_name], bpy.data.objects[rig_name]
        self.assertEqual(bpy.context.scene.character_designer_setup.rig, rig)
        self.assertFalse(control.is_enabled(source, component='SKIRT'))
        # Execute the real bounded post-load callback synchronously; do not wait
        # for a timer or claim that a second Blender process was launched.
        motion._resume_saved()
        self.assertFalse(motion._SESSIONS)
        self.assertEqual(motion.mode(source), 'AUTOMATIC')
        record = skirt.read_record(source)
        self.assertFalse(any(rig.pose.bones[name].wiggle_head or rig.pose.bones[name].wiggle_tail
                             for chain in record['chains'] for name in chain['phys']))
        self.assertEqual(dress_fixture.phys_channels(source), phys)
        self.assertEqual(dress_fixture.protected(source), before)
        self.assertEqual(current_frame(), frame)
        assert_staged_modules()
        SERIALIZED.append({'path': str(path), 'bytes': path.stat().st_size,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'native_reopen': True, 'separate_process': False})

    def test_legacy_cloth_native_flags_restore_without_mode_or_cache_config_change(self):
        motion.unregister()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 2
        bpy.context.scene.frame_set(1)
        maker = tuning_fixture.DressMotionTuningTests()
        with motion._native_writes(self.backend):
            sources, rig = maker.make()
        source = sources[0]
        select_main(rig)
        record, _rig, _proxy, cloth = physics.validate_physics(source)
        self.assertEqual(physics.backend(record), physics.LEGACY_BACKEND)
        tuning.initialize(source)
        mode = control._mode(source, record)
        before = dress_fixture.protected(source)
        settings, cache, frame = cloth_coefficients(cloth), cache_config(cloth), current_frame()
        flags = (cloth.show_viewport, cloth.show_render)
        control.set_enabled(bpy.context, False)
        self.assertEqual((cloth.show_viewport, cloth.show_render), (False, False))
        self.assertEqual(control._mode(source, record), mode)
        self.assertEqual(cloth_coefficients(cloth), settings)
        self.assertEqual(cache_config(cloth), cache)
        self.assertEqual(dress_fixture.protected(source), before)
        control.set_enabled(bpy.context, True)
        self.assertEqual((cloth.show_viewport, cloth.show_render), flags)
        self.assertEqual(control._mode(source, record), mode)
        self.assertEqual(cloth_coefficients(cloth), settings)
        self.assertEqual(cache_config(cloth), cache)
        self.assertEqual(current_frame(), frame)
        self.assertEqual(dress_fixture.protected(source), before)
        physics.validate_physics(source)


class HairPhysicsNativeTests(NativeCase):
    @classmethod
    def setUpClass(cls):
        record_frame_callbacks('before_hair_backend_registration')
        adapter._AVAILABLE_CACHE = None
        cls.backend = hair_fixture.real_backend()
        BACKENDS.append({'kind': 'HAIR', 'file': cls.backend['module'].__file__,
                         'version': adapter.BACKEND_VERSION,
                         'registered_here': hair_fixture._TEST_BACKEND is not None})
        record_frame_callbacks('after_hair_backend_registration')

    @classmethod
    def tearDownClass(cls):
        adapter.unregister_guards()
        if hair_fixture._TEST_BACKEND is not None:
            record_frame_callbacks('before_owned_hair_backend_unregister')
            hair_fixture._TEST_BACKEND.unregister()
            record_frame_callbacks('after_owned_hair_backend_unregister')
            engine = cls.backend['engine']
            if (engine.wiggle_pre in bpy.app.handlers.frame_change_pre
                    or engine.wiggle_post in bpy.app.handlers.frame_change_post):
                raise AssertionError('The owned Wiggle 1.1.2 backend left its exact frame callbacks registered')
            BACKENDS[-1]['frame_callbacks_removed_after_unregister'] = True
            hair_fixture._TEST_BACKEND = None
        adapter._AVAILABLE_CACHE = None

    def make_hair(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        source, rig, registry, profiles = hair_fixture.fixture()
        select_main(rig)
        head = rig.pose.bones['Head']
        head.keyframe_insert(data_path='rotation_euler', frame=12)
        head.keyframe_insert(data_path='rotation_euler', frame=16)
        return source, rig, registry, profiles

    def start_hair(self):
        source, rig, registry, profiles = self.make_hair()
        record_frame_callbacks(self.id() + ':before_hair_start')
        try:
            adapter.start_preview(bpy.context, source, registry, profiles,
                                  [item['strand_id'] for item in registry['strands']])
        except BaseException:
            record_frame_callbacks(self.id() + ':hair_start_refused')
            raise
        self.assertTrue(adapter.status()['active'])
        return source, rig

    def test_hair_native_pause_api_does_not_rewind_or_restore_author_channels(self):
        source, rig = self.start_hair()
        bpy.context.scene.frame_set(13)
        before = hair_channels()
        frame, actions = current_frame(), adapter._animation_proof()
        mesh, rest = shared.mesh_content(source), shared.rest_state(rig)
        playing = bool(bpy.context.screen and bpy.context.screen.is_animation_playing)
        for paused in (True, False):
            adapter.set_preview_paused(bpy.context, source, paused)
            self.assertEqual(rig.wiggle.freeze, paused)
            self.assertEqual(adapter.status()['paused'], paused)
            self.assertEqual(current_frame(), frame)
            self.assertEqual(hair_channels(), before)
            self.assertTrue(adapter._animation_same(actions))
            self.assertEqual(shared.mesh_content(source), mesh)
            self.assertEqual(shared.rest_state(rig), rest)
            self.assertEqual(bool(bpy.context.screen and bpy.context.screen.is_animation_playing), playing)

    def test_hair_master_off_on_preserves_live_preview_and_time(self):
        source, rig = self.start_hair()
        before, frame = hair_channels(), current_frame()
        actions = adapter._animation_proof()
        for enabled in (False, True):
            result = control.set_enabled(bpy.context, enabled)
            self.assertTrue(adapter.status()['active'])
            self.assertEqual(adapter.status()['paused'], not enabled)
            self.assertEqual(rig.wiggle.freeze, not enabled)
            self.assertEqual(result['hair']['paused'], not enabled)
            self.assertEqual(hair_channels(), before)
            self.assertEqual(current_frame(), frame)
            self.assertTrue(adapter._animation_same(actions))

    def test_hair_master_off_blocks_new_rig_preview_entry(self):
        source, rig, registry, profiles = self.make_hair()
        control.set_enabled(bpy.context, False)
        before, frame = hair_channels(), current_frame()
        with self.assertRaisesRegex(ValueError, 'Enable.*Physics'):
            adapter.start_preview(bpy.context, source, registry, profiles,
                                  [item['strand_id'] for item in registry['strands']])
        self.assertFalse(adapter.status()['active'])
        self.assertEqual(hair_channels(), before)
        self.assertEqual(current_frame(), frame)

    def test_hair_component_toggle_pauses_without_restarting_session(self):
        source, rig = self.start_hair()
        session = adapter._SESSION
        before, frame = hair_channels(), current_frame()
        for enabled in (False, True):
            control.set_component(bpy.context, 'HAIR', enabled)
            self.assertIs(adapter._SESSION, session)
            self.assertEqual(rig.wiggle.freeze, not enabled)
            self.assertEqual(hair_channels(), before)
            self.assertEqual(current_frame(), frame)

    def test_hair_unknown_frame_pose_writer_is_refused(self):
        source, rig, registry, profiles = self.make_hair()
        before, frame = hair_channels(), current_frame()
        called = []

        def foreign_pose_writer(scene, *_unused):
            called.append(scene.name)
            rig.pose.bones['Head'].rotation_euler.x = .77

        handlers = bpy.app.handlers.frame_change_pre
        handlers.append(foreign_pose_writer)
        try:
            record_frame_callbacks(self.id() + ':unknown_writer_present')
            with self.assertRaisesRegex(adapter.HairWiggleError, 'unverified frame callback'):
                adapter.start_preview(bpy.context, source, registry, profiles,
                                      [item['strand_id'] for item in registry['strands']])
            self.assertFalse(adapter.status()['active'])
            self.assertEqual(called, [])
            self.assertEqual(hair_channels(), before)
            self.assertEqual(current_frame(), frame)
        finally:
            # Remove only the exact callback added by this test.
            if foreign_pose_writer in handlers:
                handlers.remove(foreign_pose_writer)


class RecordedResult(unittest.TextTestResult):
    def addSuccess(self, test):
        super().addSuccess(test)
        OUTCOMES.append({'test': test.id(), 'outcome': 'passed'})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        OUTCOMES.append({'test': test.id(), 'outcome': 'failed'})
        record_frame_callbacks(test.id() + ':failed')

    def addError(self, test, error):
        super().addError(test, error)
        OUTCOMES.append({'test': test.id(), 'outcome': 'error'})
        record_frame_callbacks(test.id() + ':error')


def main():
    global OPTIONS, SCRATCH, cd, control, motion, adapter, skirt, physics, tuning
    global dress_fixture, hair_fixture, tuning_fixture, shared
    OPTIONS = arguments()
    report_path = OPTIONS.report_json.resolve()
    if report_path.exists():
        raise RuntimeError('Use a new report filename; prior evidence is preserved.')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'cd.animation-physics-native/1', 'passed': False,
              'tests_run': 0, 'success_count': 0, 'outcomes': OUTCOMES,
              'backends': BACKENDS, 'serialized': SERIALIZED,
              'frame_callbacks': CALLBACKS,
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'limitations': ['No Direct Cloth fixture in this bounded harness.',
                             'No artist scene, GUI/FPS or hardware performance measurement.',
                             'Native reopen uses the same isolated process, not a fresh process.',
                             'Native Undo/Redo is not exercised by this background harness.']}
    registered = False
    try:
        if not bpy.app.background:
            raise RuntimeError('This harness requires a new isolated background Blender.')
        if not os.environ.get('BLENDER_USER_CONFIG'):
            raise RuntimeError('Launch with the trusted isolated CONFIG launcher.')
        SCRATCH = report_path.parent / (report_path.stem + '_scratch')
        SCRATCH.mkdir(exist_ok=False)
        for key in ('TEMP', 'TMP'):
            os.environ[key] = str(SCRATCH)
        bpy.context.preferences.filepaths.temporary_directory = str(SCRATCH)
        sys.path[:0] = [str(OPTIONS.addons_root.resolve()), str(ROOT / 'tests')]
        cd = importlib.import_module('character_designer')
        assert_staged_modules()
        control = importlib.import_module('character_designer.animation_physics')
        motion = importlib.import_module('character_designer.skirt_wiggle')
        adapter = importlib.import_module('character_designer.hair_wiggle_adapter')
        skirt = importlib.import_module('character_designer.skirt_rig')
        physics = importlib.import_module('character_designer.skirt_physics')
        tuning = importlib.import_module('character_designer.skirt_motion_tuning')
        # Helpers may prepend canonical sys.path entries.  The staged package is
        # already loaded; never delete/reload it, and verify all product paths.
        dress_fixture = importlib.import_module('test_skirt_wiggle_blender')
        hair_fixture = importlib.import_module('test_hair_wiggle_adapter_blender')
        tuning_fixture = importlib.import_module('test_skirt_motion_tuning_blender')
        shared = importlib.import_module('test_skirt_shared_rig_blender')
        sys.path.insert(0, str(OPTIONS.addons_root.resolve()))
        assert_staged_modules()
        cd.register()
        registered = True
        cd._validate_registration_integrity()
        report.update(blender=bpy.app.version_string, addon_version=list(cd.bl_info['version']),
                      addons_root=str(OPTIONS.addons_root.resolve()),
                      module_file=cd.__file__, autoexec_enabled=True)
        suite = unittest.TestSuite([
            unittest.defaultTestLoader.loadTestsFromTestCase(DressPhysicsNativeTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(HairPhysicsNativeTests),
        ])
        result = unittest.TextTestRunner(verbosity=2, resultclass=RecordedResult).run(suite)
        report.update(passed=result.wasSuccessful(), tests_run=result.testsRun,
                      success_count=sum(row['outcome'] == 'passed' for row in OUTCOMES),
                      failed=[(str(test), trace) for test, trace in result.failures],
                      errors=[(str(test), trace) for test, trace in result.errors],
                      skipped=[(str(test), reason) for test, reason in result.skipped],
                      staged_modules=assert_staged_modules())
    except BaseException:
        import traceback
        report['bootstrap_error'] = traceback.format_exc()
        raise
    finally:
        try:
            if registered:
                cd.unregister()
            report['cleanup'] = 'own product registration released'
        except Exception:
            import traceback
            report['cleanup_error'] = traceback.format_exc()
            report['passed'] = False
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not report['passed']:
        raise SystemExit(1)


OUTCOMES, BACKENDS, SERIALIZED, CALLBACKS = [], [], [], []

if __name__ == '__main__':
    main()
