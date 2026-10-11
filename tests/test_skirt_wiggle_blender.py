"""Real external Wiggle 2 Dress mode tests in disposable native scenes.

Run only in isolated factory-startup Blender, sequentially. --addons-root
selects a reviewed release projection, --wiggle-file names the real external
2.2.4 source, and --report-json records the native receipt. No artist scene
is opened. The backend is imported/registered only inside this test process.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def argument(name, default=None):
    return ARGS[ARGS.index(name) + 1] if name in ARGS else default


sys.path[:0] = [str(argument('--addons-root', ROOT / 'addons')), str(ROOT / 'tests')]
import character_designer
from character_designer import skirt_wiggle as motion, skirt_rig as skirt
from test_skirt_shared_rig_blender import rig_fixture, mesh_content, rest_state, pose_channels
from test_skirt_topology_blender import frustum


def real_backend():
    try:
        return motion._backend(), False
    except motion.SkirtWiggleError:
        path = argument('--wiggle-file')
        if not path:
            raise RuntimeError('Native Dress tests require --wiggle-file for real Wiggle 2 2.2.4.')
        name = '_cd_native_dress_wiggle_224'
        spec = importlib.util.spec_from_file_location(name, Path(path).resolve())
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        module.register()
        motion._BACKEND_CACHE = None
        return motion._backend(), True


def fixture(*, shared=True, count=1):
    # The released general-purpose rig copier copies external RNA defaults.
    # Legacy Wiggle's UI callback recursively propagates those setters in Pose
    # Mode. No simulation exists during synthetic construction: guard only its
    # unrelated UI propagation here, then restore it before feature validation.
    backend = motion._backend()
    motion.unregister()
    with motion._native_writes(backend):
        return _fixture(shared=shared, count=count)


def _fixture(*, shared=True, count=1):
    motion.unregister()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps_base = 1.0
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 24
    bpy.context.scene.frame_set(8)
    main = rig_fixture()
    sources = []
    for index in range(count):
        source = frustum('Artist Dress ' + str(index + 1), rows=6, sides=16)
        source.shape_key_add(name='Basis')
        key = source.shape_key_add(name='Artist fold')
        key.data[3].co.x += .009
        key.value = .32
        group = source.vertex_groups.new(name='Artist mask')
        group.add([1, 2, 3], .27, 'REPLACE')
        skirt.build_skirt(bpy.context, source, chain_count=4, segment_count=3,
                          armature=main, parent_bone='Hips', shared=shared)
        sources.append(source)
    source = sources[0]
    rig = source[skirt.RIG_KEY]
    record = skirt.read_record(source)
    hem = rig.pose.bones[record['controls']['hem']]
    hem.location.x = .013
    hem.rotation_euler.y = .04
    rig.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    return tuple(sources), main


def protected(source):
    rig = source[skirt.RIG_KEY]
    record = skirt.read_record(source)
    physics = {name for chain in record['chains'] for name in chain['phys']}
    from character_designer import limb_ik
    actions = tuple((action.name, tuple((curve.data_path, curve.array_index,
                    tuple(tuple(point.co) for point in curve.keyframe_points))
                    for curve in limb_ik._fcurves_for_action(action))) for action in bpy.data.actions)
    return {'mesh': mesh_content(source), 'rest': rest_state(rig),
            'inputs': pose_channels(rig, tuple(name for name in rig.pose.bones.keys() if name not in physics)),
            'actions': actions, 'action': rig.animation_data.action.name if rig.animation_data and rig.animation_data.action else None}


def evaluated(source):
    record = skirt.read_record(source)
    rig = source[skirt.RIG_KEY].evaluated_get(bpy.context.evaluated_depsgraph_get())
    names = [name for chain in record['chains'] for name in chain['def']]
    return {name: tuple(tuple(row) for row in rig.pose.bones[name].matrix) for name in names}


def maximum_error(first, second):
    return max(abs(a - b) for name in first for ar, br in zip(first[name], second[name]) for a, b in zip(ar, br))


def phys_channels(source):
    record = skirt.read_record(source)
    return pose_channels(source[skirt.RIG_KEY], tuple(name for chain in record['chains'] for name in chain['phys']))


def native_fields(owner, **values):
    with motion._native_writes(motion._backend()):
        for name, value in values.items():
            motion._native_set(owner, name, value)


class DressWiggleNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Use an isolated factory-startup background Blender for these tests.')
        cls.backend, cls.registered_backend = real_backend()

    @classmethod
    def tearDownClass(cls):
        motion.unregister()
        if cls.registered_backend:
            cls.backend.unregister()

    def tearDown(self):
        motion.unregister()

    def test_real_motion_freeze_resume_retains_artist_inputs(self):
        for shared in (True, False):
            with self.subTest(shared=shared):
                sources, main = fixture(shared=shared)
                source = sources[0]
                rig = source[skirt.RIG_KEY]
                record = skirt.read_record(source)
                waist = rig.pose.bones[record['controls']['waist']]
                waist.keyframe_insert(data_path='location', frame=8)
                waist.keyframe_insert(data_path='location', frame=16)
                before = protected(source)
                pose = evaluated(source)
                frame = (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe)
                motion.set_mode(bpy.context, source, 'AUTOMATIC')
                self.assertLessEqual(maximum_error(pose, evaluated(source)), 5e-5)
                self.assertEqual(frame, (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe))
                self.assertEqual(protected(source), before)
                initial = phys_channels(source)
                for index in range(9, 14):
                    bpy.context.scene.frame_set(index)
                self.assertEqual(motion.mode(source), 'AUTOMATIC')
                self.assertTrue(motion.status(bpy.context, source)['running'])
                self.assertNotEqual(phys_channels(source), initial, 'The real external solver must move PHYS.')
                self.assertEqual(protected(source), before)
                pose = evaluated(source)
                motion.set_mode(bpy.context, source, 'MANUAL')
                self.assertLessEqual(maximum_error(pose, evaluated(source)), 5e-5)
                frozen = phys_channels(source)
                for index in range(14, 17):
                    bpy.context.scene.frame_set(index)
                self.assertEqual(phys_channels(source), frozen)
                self.assertEqual(protected(source), before)
                # Editing the existing manual input does not replace frozen PHYS.
                hem = rig.pose.bones[record['controls']['hem']]
                hem.location.y += .011
                bpy.context.view_layer.update()
                authored = protected(source)
                pose = evaluated(source)
                motion.set_mode(bpy.context, source, 'AUTOMATIC')
                self.assertLessEqual(maximum_error(pose, evaluated(source)), 5e-5)
                self.assertEqual(protected(source), authored)
                motion.set_mode(bpy.context, source, 'MANUAL')
                self.assertEqual(protected(source), authored)

    def test_same_rig_multiple_dresses_restore_original_enable_in_both_orders(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                sources, rig = fixture(count=2)
                before = {source.name: protected(source) for source in sources}
                self.assertFalse(rig.wiggle_enable)
                self.assertFalse(bpy.context.scene.wiggle_enable)
                for source in sources:
                    motion.set_mode(bpy.context, source, 'AUTOMATIC')
                order = sources[::-1] if reverse else sources
                motion.set_mode(bpy.context, order[0], 'MANUAL')
                self.assertTrue(motion.status(bpy.context, order[1])['running'])
                self.assertTrue(rig.wiggle_enable)
                motion.set_mode(bpy.context, order[1], 'MANUAL')
                self.assertFalse(rig.wiggle_enable)
                self.assertFalse(bpy.context.scene.wiggle_enable)
                self.assertEqual(len(bpy.context.scene.wiggle.list), 0)
                for source in sources:
                    self.assertEqual(protected(source), before[source.name])

    def test_custom_driver_and_existing_simulation_are_refused_before_mutation(self):
        sources, rig = fixture()
        source = sources[0]
        record = skirt.read_record(source)
        protected_before = protected(source)
        delta = rig.pose.bones[record['chains'][0]['def'][0]].constraints['Skirt physics delta']
        curve = rig.animation_data.drivers.find(delta.path_from_id('influence'))
        target = curve.driver.variables[0].targets[0]
        old_path = target.data_path
        target.data_path = '["artist_physics_switch"]'
        with self.assertRaisesRegex(motion.SkirtWiggleError, 'driver'):
            motion.set_mode(bpy.context, source, 'AUTOMATIC')
        self.assertNotIn(motion.STATE_KEY, source)
        self.assertEqual(protected(source), protected_before)
        target.data_path = old_path
        delta.invert_x = True
        with self.assertRaisesRegex(motion.SkirtWiggleError, 'delta'):
            motion.set_mode(bpy.context, source, 'AUTOMATIC')
        delta.invert_x = False
        hair = rig.pose.bones['Hair']
        native_fields(hair, wiggle_tail=True)
        with self.assertRaisesRegex(motion.SkirtWiggleError, 'Another Wiggle'):
            motion.set_mode(bpy.context, source, 'AUTOMATIC')
        self.assertTrue(hair.wiggle_tail)
        self.assertNotIn(motion.STATE_KEY, source)

    def test_start_failure_restores_pose_flags_actions_and_metadata(self):
        sources, rig = fixture()
        source = sources[0]
        before, pose, physics = protected(source), evaluated(source), phys_channels(source)
        callback = self.backend.update_prop
        with patch.object(self.backend, 'reset_bone', side_effect=RuntimeError('injected native seed failure')):
            with self.assertRaises(motion.SkirtWiggleError):
                motion.set_mode(bpy.context, source, 'AUTOMATIC')
        self.assertEqual(protected(source), before)
        self.assertEqual(phys_channels(source), physics)
        self.assertLessEqual(maximum_error(pose, evaluated(source)), 5e-5)
        self.assertNotIn(motion.STATE_KEY, source)
        self.assertNotIn(motion.STATE_SCENE_KEY, source)
        self.assertFalse(rig.wiggle_enable)
        self.assertFalse(bpy.context.scene.wiggle_enable)
        self.assertEqual(len(bpy.context.scene.wiggle.list), 0)
        self.assertIs(self.backend.update_prop, callback)

    def test_save_reopen_automatic_reseeds_current_pose_and_tuning(self):
        sources, rig = fixture()
        source = sources[0]
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        record = skirt.read_record(source)
        physics = rig.pose.bones[record['chains'][0]['phys'][0]]
        native_fields(physics, wiggle_damp=7.0)
        for index in range(9, 12):
            bpy.context.scene.frame_set(index)
        before, pose, frozen = protected(source), evaluated(source), phys_channels(source)
        name, frame = source.name, bpy.context.scene.frame_current
        with tempfile.TemporaryDirectory(prefix='cd-dress-wiggle-') as directory:
            path = str(Path(directory) / 'Automatic.blend')
            result = bpy.ops.wm.save_as_mainfile(filepath=path)
            self.assertIn('FINISHED', result)
            self.assertEqual(motion.mode(source), 'AUTOMATIC')
            self.assertTrue(motion.status(bpy.context, source)['running'])
            self.assertEqual(physics.wiggle_damp, 7.0)
            self.assertEqual(phys_channels(source), frozen)
            self.assertLessEqual(maximum_error(pose, evaluated(source)), 5e-5)
            bpy.ops.wm.open_mainfile(filepath=path, load_ui=False, use_scripts=False)
            source = bpy.data.objects[name]
            self.assertEqual(motion.mode(source), 'AUTOMATIC')
            self.assertFalse(motion.status(bpy.context, source)['running'])
            self.assertEqual(phys_channels(source), frozen)
            self.assertEqual(bpy.context.scene.frame_current, frame)
            motion._resume_saved()  # Deterministic dispatch of the real load-post timer.
            self.assertTrue(motion.status(bpy.context, source)['running'])
            self.assertEqual(phys_channels(source), frozen)
            self.assertLessEqual(maximum_error(pose, evaluated(source)), 5e-5)
            self.assertEqual(protected(source), before)
            loaded = source[skirt.RIG_KEY].pose.bones[record['chains'][0]['phys'][0]]
            self.assertEqual(loaded.wiggle_damp, 7.0)
            bpy.context.scene.frame_set(frame + 1)
            self.assertTrue(motion.status(bpy.context, source)['running'])
            self.assertLess(maximum_error(pose, evaluated(source)), .05, 'Reseeding must not snap the loaded skirt.')

    def test_undo_restored_native_flags_are_owned_recovered_and_reseeded(self):
        sources, rig = fixture()
        source = sources[0]
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        state, scene_ref = source[motion.STATE_KEY], source[motion.STATE_SCENE_KEY]
        record = skirt.read_record(source)
        names = tuple(name for chain in record['chains'] for name in chain['phys'])
        motion.set_mode(bpy.context, source, 'MANUAL')
        # Model Blender's Undo contract exactly: ID state is restored while
        # Python globals remain at the later Manual state. The real native
        # flags/list, not a substitute solver, are restored in this fixture.
        source[motion.STATE_KEY], source[motion.STATE_SCENE_KEY] = state, scene_ref
        for name in names:
            native_fields(rig.pose.bones[name], wiggle_tail=True, wiggle_enable=True)
        native_fields(rig, wiggle_enable=True)
        native_fields(bpy.context.scene, wiggle_enable=True)
        motion._list_add(bpy.context.scene, rig, names)
        frozen = phys_channels(source)
        motion._undo_post()
        self.assertFalse(motion._SESSIONS)
        self.assertTrue(all(not rig.pose.bones[name].wiggle_tail for name in names))
        self.assertEqual(len(bpy.context.scene.wiggle.list), 0)
        self.assertEqual(phys_channels(source), frozen)
        motion._resume_saved()
        self.assertTrue(motion.status(bpy.context, source)['running'])
        self.assertEqual(phys_channels(source), frozen)

    def test_manual_parameter_edit_and_other_scene_flags_are_retained(self):
        sources, rig = fixture()
        source = sources[0]
        record = skirt.read_record(source)
        bone = rig.pose.bones[record['chains'][0]['phys'][0]]
        native_fields(bone, wiggle_damp=2.0)
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        native_fields(bone, wiggle_damp=7.0)
        motion.set_mode(bpy.context, source, 'MANUAL')
        self.assertEqual(bone.wiggle_damp, 7.0)
        native_fields(bone, wiggle_damp=9.0)
        second = bpy.data.scenes.new('Unrelated Scene')
        second.collection.objects.link(rig)
        native_fields(second, wiggle_enable=True)
        motion.suspend(bpy.context, source, reason='Test Original pause.')
        self.assertEqual(bone.wiggle_damp, 9.0)
        self.assertTrue(second.wiggle_enable)
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        self.assertEqual(bone.wiggle_damp, 9.0)
        self.assertTrue(second.wiggle_enable)

    def test_foreign_simulation_enabled_during_preview_survives_own_stop(self):
        sources, rig = fixture()
        source = sources[0]
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        hair = rig.pose.bones['Hair']
        native_fields(hair, wiggle_tail=True, wiggle_enable=True)
        self.backend.build_list()  # The external native UI rebuilds its own list.
        self.backend.reset_bone(hair)
        bpy.context.scene.frame_set(9)
        self.assertEqual(motion.mode(source), 'MANUAL')
        self.assertTrue(hair.wiggle_tail)
        self.assertTrue(rig.wiggle_enable)
        self.assertTrue(bpy.context.scene.wiggle_enable)
        self.assertIsNotNone(bpy.context.scene.wiggle.list.get(rig.name).list.get('Hair'))

    def test_timeline_wrap_keeps_automatic_without_resetting_local_pose(self):
        sources, rig = fixture()
        source = sources[0]
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        bpy.context.scene.frame_set(9)
        bpy.context.scene.frame_set(10)
        pose = phys_channels(source)
        bpy.context.scene.frame_set(1)
        self.assertEqual(motion.mode(source), 'AUTOMATIC')
        self.assertTrue(motion.status(bpy.context, source)['running'])
        self.assertEqual(phys_channels(source), pose)
        bpy.context.scene.frame_set(2)
        self.assertTrue(motion.status(bpy.context, source)['running'])

    def test_manual_works_without_registered_backend_and_cloth_is_refused(self):
        sources, rig = fixture()
        source = sources[0]
        before = protected(source)
        with patch.object(motion, '_backend', side_effect=motion.SkirtWiggleError('unavailable')):
            motion.set_mode(bpy.context, source, 'MANUAL')
            with self.assertRaises(motion.SkirtWiggleError):
                motion.set_mode(bpy.context, source, 'AUTOMATIC')
        self.assertEqual(protected(source), before)
        record = skirt.read_record(source)
        record['physics'] = {'proxy': 'Artist retained Cloth'}
        skirt.write_record(source, record)
        with self.assertRaisesRegex(motion.SkirtWiggleError, 'Cloth'):
            motion.set_mode(bpy.context, source, 'AUTOMATIC')
        self.assertEqual(skirt.read_record(source)['physics'], record['physics'])

    def test_original_entry_pauses_own_simulation_before_native_pose_snapshot(self):
        from character_designer import body_original_mode, bone_collections
        sources, rig = fixture()
        source = sources[0]
        group = rig.data.collections.new('Original')
        group[bone_collections.GROUP_KEY] = 'Original'
        for name in ('Hips', 'Body', 'Hair'):
            group.assign(rig.data.bones[name])
        motion.set_mode(bpy.context, source, 'AUTOMATIC')
        bpy.context.scene.frame_set(9)
        pose = evaluated(source)
        body_original_mode.enter(bpy.context, rig)
        self.assertEqual(motion.mode(source), 'MANUAL')
        self.assertFalse(motion.status(bpy.context, source)['running'])
        self.assertLessEqual(maximum_error(pose, evaluated(source)), 5e-5)
        body_original_mode.leave(bpy.context, rig)
        self.assertLessEqual(maximum_error(pose, evaluated(source)), 5e-5)

    def test_export_pauses_only_collected_source_before_snapshot_barrier(self):
        from character_designer import unity_export
        sources, rig = fixture(count=2)
        for source in sources:
            motion.set_mode(bpy.context, source, 'AUTOMATIC')
        with tempfile.TemporaryDirectory(prefix='cd-dress-export-gate-') as directory:
            with patch.object(unity_export, 'collect_character',
                              return_value={'objects': [sources[0], rig], 'warnings': []}), \
                 patch.object(unity_export, '_target', return_value=(Path(directory), 'Dress.fbx')), \
                 patch.object(unity_export, '_previous', return_value=None), \
                 patch.object(unity_export, '_capture_hair_motion',
                              side_effect=RuntimeError('snapshot barrier')):
                with self.assertRaisesRegex(RuntimeError, 'snapshot barrier'):
                    unity_export.begin_export(bpy.context, rig, SimpleNamespace(asset_id='native-test'))
        self.assertEqual(motion.mode(sources[0]), 'MANUAL')
        self.assertEqual(motion.mode(sources[1]), 'AUTOMATIC')
        self.assertTrue(motion.status(bpy.context, sources[1])['running'])
        self.assertIsInstance(sources[0][motion.STATE_SCENE_KEY], str)
        self.assertFalse(any(isinstance(source.get(key), bpy.types.Scene)
                             for source in sources for key in source.keys()))

    def test_remove_active_dress_cleans_owned_motion_and_allows_rebuild(self):
        for shared in (True, False):
            with self.subTest(shared=shared):
                sources, main = fixture(shared=shared)
                source = sources[0]
                motion.set_mode(bpy.context, source, 'AUTOMATIC')
                skirt.remove_skirt(bpy.context, source)
                self.assertNotIn(motion.STATE_KEY, source)
                self.assertNotIn(motion.STATE_SCENE_KEY, source)
                self.assertFalse(motion._SESSIONS)
                self.assertFalse(bpy.context.scene.wiggle_enable)
                self.assertFalse(bpy.context.scene.wiggle.list)
                with motion._native_writes(self.backend):
                    skirt.build_skirt(bpy.context, source, chain_count=4, segment_count=3,
                                      armature=main, parent_bone='Hips', shared=shared)
                motion.set_mode(bpy.context, source, 'AUTOMATIC')
                self.assertTrue(motion.status(bpy.context, source)['running'])


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DressWiggleNativeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'passed': result.wasSuccessful(), 'tests_run': result.testsRun,
              'failures': [(str(test), error) for test, error in result.failures],
              'errors': [(str(test), error) for test, error in result.errors],
              'blender': bpy.app.version_string, 'backend': motion.BACKEND_NAME,
              'backend_version': motion.BACKEND_VERSION, 'backend_sha256': motion.BACKEND_SHA256,
              'source_file': motion.__file__}
    if argument('--report-json'):
        Path(argument('--report-json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not result.wasSuccessful():
        raise SystemExit(1)
