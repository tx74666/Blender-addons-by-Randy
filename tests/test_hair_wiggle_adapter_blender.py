"""Native tests using the external, unmodified Wiggle Bones 1.1.2 tag.

Run in an isolated factory-startup Blender, never the artist session:
  blender --background --factory-startup --disable-autoexec --python-exit-code 1
    --python tests/test_hair_wiggle_adapter_blender.py -- --wiggle-root <package-dir>
The directory must contain __init__.py and blender_manifest.toml from the
official 1.1.2 release. This test does not download/install an extension or
contain a substitute solver. The --wiggle-root package is imported/registered
only in this disposable native process. Without a real backend it fails.
"""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import hair_bones_binding as binding
from character_designer import hair_strand_registry as registry_service
from character_designer import hair_wiggle_adapter as adapter
from test_hair_bones_binding_blender import scene_fixture
from test_hair_bones_rig_blender import activate

_TEST_BACKEND = None


def real_backend():
    global _TEST_BACKEND
    info = adapter.available()
    if info['available']:
        return adapter._backend()
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    root = os.environ.get('CD_WIGGLE_TEST_ROOT')
    if '--wiggle-root' in argv:
        root = argv[argv.index('--wiggle-root') + 1]
    if not root:
        raise RuntimeError('Native adapter tests require --wiggle-root with official Wiggle 1.1.2.')
    path = Path(root).resolve()
    name = '_cd_native_wiggle_112'
    spec = importlib.util.spec_from_file_location(name, path / '__init__.py',
                                                 submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    module.register()
    _TEST_BACKEND = module
    # The UI query intentionally caches one second; start's full proof does not.
    return adapter._backend()


def fixture():
    adapter.stop_preview(reason='Test fixture reset.')
    source, plans, armature = scene_fixture()
    binding.bind_hair(bpy.context, source, plans, bone_count=4, armature=armature)
    registry_service.initialize(source)
    registry = registry_service.read(source, validate=True)
    bpy.context.scene.render.fps_base = 1.0
    bpy.context.scene.unit_settings.scale_length = 1.0
    bpy.context.scene.gravity = (0, 0, -9.81)
    bpy.context.scene.frame_set(12)
    activate(source)
    profiles = {item['strand_id']: {'recovery': 0.4, 'damping': 0.3,
        'gravity': 4.0, 'stretch': 0.0, 'depth': []} for item in registry['strands']}
    return source, armature, registry, profiles


def baseline(source, armature):
    scene = bpy.context.scene
    owners = [scene]
    pose_owners = [scene]
    channels = []
    for ob in scene.objects:
        channels.append(adapter._channels(ob, True))
        pose_owners.append(ob)
        if ob.type == 'ARMATURE':
            owners.append(ob)
            owners.extend(ob.pose.bones)
            pose_owners.extend(ob.pose.bones)
            channels.extend(adapter._channels(bone) for bone in ob.pose.bones)
    return {'raw': [adapter._raw_snapshot(owner) for owner in owners],
        'settings': [(owner, adapter._rna_snapshot(owner.wiggle)
                      if owner.is_property_set('wiggle') else None) for owner in owners],
        'custom': [adapter._pose_custom(owner) for owner in pose_owners],
        'channels': channels, 'actions': adapter._animation_proof(),
        'frame': (scene.frame_current, scene.frame_subframe),
        'autokey': scene.tool_settings.use_keyframe_insert_auto,
        'source': {key: adapter._clone(source[key]) for key in source.keys()},
        'groups': [(group.name, tuple((vertex.index, assignment.weight)
            for vertex in source.data.vertices for assignment in vertex.groups
            if assignment.group == group.index)) for group in source.vertex_groups],
        'shapes': tuple((key.name, key.value, tuple(tuple(point.co) for point in key.data))
            for key in source.data.shape_keys.key_blocks),
        'association': armature.animation_data.action if armature.animation_data else None,
        'action_count': len(bpy.data.actions)}


def assert_baseline(test, source, armature, saved):
    scene = bpy.context.scene
    test.assertEqual((scene.frame_current, scene.frame_subframe), saved['frame'])
    test.assertEqual(scene.tool_settings.use_keyframe_insert_auto, saved['autokey'])
    for owner, mode, channels in saved['channels']:
        test.assertEqual(owner.rotation_mode, mode)
        for name, values in channels.items():
            actual = tuple(getattr(owner, name))
            for first, second in zip(actual, values):
                test.assertAlmostEqual(first, second, places=6, msg=owner.name + '.' + name)
    for owner, exists, values in saved['raw']:
        test.assertEqual(owner.is_property_set('wiggle'), exists, msg=owner.name)
        if exists:
            test.assertEqual(adapter._clone(owner.wiggle), values, msg=owner.name)
    for owner, settings in saved['settings']:
        if settings is not None:
            test.assertEqual(adapter._rna_snapshot(owner.wiggle), settings, msg=owner.name + '.wiggle RNA')
    for owner, values in saved['custom']:
        for key, value in values.items():
            test.assertEqual(adapter._clone(owner[key]), value, msg=owner.name + '[' + key + ']')
    test.assertTrue(adapter._animation_same(saved['actions']))
    test.assertEqual(len(bpy.data.actions), saved['action_count'])
    test.assertEqual(armature.animation_data.action if armature.animation_data else None, saved['association'])
    current = baseline(source, armature)
    for key in ('source', 'groups', 'shapes'):
        test.assertEqual(current[key], saved[key], msg=key)


class NativeHairWiggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = real_backend()

    def tearDown(self):
        adapter.stop_preview(reason='Test cleanup.')
        adapter.unregister_guards()

    def test_real_schema_and_staged_tail_only_movement_restore(self):
        source, armature, registry, profiles = fixture()
        armature.pose.bones['Head'].rotation_euler.z = 0.13
        first, second = registry['strands']
        tip = armature.pose.bones[first['bones'][-1]]
        tip.rotation_mode = 'XYZ'
        tip.rotation_euler.x = 0.12
        tip.wiggle.stiff = 123.5
        tip.wiggle.velocity = (0.1, 0.2, 0.3)
        bpy.context.scene.wiggle.auto_sync = True
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
        saved = baseline(source, armature)
        info = adapter.start_preview(bpy.context, source, registry, profiles, [first['strand_id']])
        self.assertTrue(info['active'])
        self.assertFalse(bpy.context.scene.tool_settings.use_keyframe_insert_auto)
        self.assertFalse(armature.pose.bones['Head'].wiggle.head)
        self.assertFalse(armature.pose.bones['Head'].wiggle.tail)
        for item in registry['strands']:
            for name in item['bones']:
                bone = armature.pose.bones[name]
                self.assertFalse(bone.wiggle.head)
                self.assertEqual(bone.wiggle.tail, item is first)
        initial = tip.matrix.copy()
        for frame in range(13, 18):
            bpy.context.scene.frame_set(frame)
        self.assertGreater(max(abs(initial[row][col] - tip.matrix[row][col])
                               for row in range(4) for col in range(4)), 1e-6)
        adapter.stop_preview()
        assert_baseline(self, source, armature, saved)
        self.assertAlmostEqual(tip.wiggle.stiff, 123.5, places=5)
        for actual, expected in zip(tip.wiggle.velocity, (0.1, 0.2, 0.3)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertTrue(bpy.context.scene.wiggle.auto_sync)
        self.assertFalse(armature.pose.bones['Head'].wiggle.tail)
        # Reading an absent dynamic PointerProperty itself initializes it.
        if not next(exists for owner, exists, _ in saved['raw']
                    if owner == armature.pose.bones['Head']):
            armature.pose.bones['Head'].property_unset('wiggle')
        # A later stage selects all independent chains without replacing bones.
        adapter.start_preview(bpy.context, source, registry, profiles,
                              [first['strand_id'], second['strand_id']])
        self.assertTrue(all(armature.pose.bones[name].wiggle.tail
                            for item in registry['strands'] for name in item['bones']))
        adapter.stop_preview()
        assert_baseline(self, source, armature, saved)

    def test_world_gravity_depth_and_bone_count_are_separate(self):
        source, armature, registry, profiles = fixture()
        item = registry['strands'][0]
        bpy.context.scene.unit_settings.scale_length = 0.01
        bpy.context.scene.use_gravity = False  # Upstream actually ignores this switch.
        armature.scale = (2, 2, 2)
        bpy.context.view_layer.update()
        profiles[item['strand_id']]['depth'] = [
            {'position': 0, 'recovery': 0, 'damping': 0.1, 'mass': 0.2, 'gravity': 1},
            {'position': 1, 'recovery': 1, 'damping': 0.9, 'mass': 2, 'gravity': 7}]
        saved = baseline(source, armature)
        count = len(armature.data.bones)
        adapter.start_preview(bpy.context, source, registry, profiles, [item['strand_id']])
        tip = armature.pose.bones[item['bones'][-1]]
        self.assertAlmostEqual(tip.wiggle.stiff, 800, places=4)
        self.assertAlmostEqual(tip.wiggle.damp, 18, places=4)
        self.assertAlmostEqual(tip.wiggle.mass, 2, places=5)
        self.assertAlmostEqual(tip.wiggle.gravity, 7 / (9.81 * 0.01), places=3)
        self.assertEqual(len(armature.data.bones), count)
        self.assertFalse(bpy.context.scene.use_gravity)
        adapter.stop_preview()
        assert_baseline(self, source, armature, saved)

    def test_save_pre_ends_preview_before_file_write(self):
        source, armature, registry, profiles = fixture()
        item = registry['strands'][0]
        head = armature.pose.bones['Head']
        head.rotation_mode = 'XYZ'
        head.rotation_euler.x = 0.2
        head.keyframe_insert(data_path='rotation_euler', frame=12)
        head.rotation_euler.x = 0.4
        head.keyframe_insert(data_path='rotation_euler', frame=20)
        bpy.context.scene.frame_set(12)
        saved = baseline(source, armature)
        handlers = {name: tuple(getattr(bpy.app.handlers, name)) for name in
                    ('frame_change_pre', 'frame_change_post', 'load_post')}
        adapter.start_preview(bpy.context, source, registry, profiles, [item['strand_id']])
        bpy.context.scene.frame_set(13)
        with tempfile.TemporaryDirectory(prefix='cd_wiggle_native_') as folder:
            path = Path(folder) / 'preview_save_guard.blend'
            result = bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
            self.assertIn('FINISHED', result)
            self.assertTrue(path.is_file())
            self.assertFalse(adapter.status()['active'])
            assert_baseline(self, source, armature, saved)
            # Inspect the saved native pose through library loading, without
            # running load handlers or changing the test Scene.
            with bpy.data.libraries.load(str(path), link=False) as (data_from, data_to):
                data_to.objects = [armature.name]
            loaded = data_to.objects[0]
            self.assertAlmostEqual(loaded.pose.bones['Head'].rotation_euler.x, 0.2, places=5)
            self.assertFalse(any(bone.is_property_set('wiggle') and bone.wiggle.tail
                                 for bone in loaded.pose.bones))
            bpy.data.objects.remove(loaded, do_unlink=True)
        for name, functions in handlers.items():
            self.assertTrue(all(function in getattr(bpy.app.handlers, name) for function in functions))

    def test_start_failure_restores_actual_native_side_effects(self):
        source, armature, registry, profiles = fixture()
        saved = baseline(source, armature)
        original = adapter._configure
        def fail_after_real_configure(*args):
            original(*args)
            self.assertTrue(bpy.context.scene.wiggle.enable)
            raise RuntimeError('Injected after the official build_list reset.')
        with patch.object(adapter, '_configure', side_effect=fail_after_real_configure):
            with self.assertRaisesRegex(adapter.HairWiggleError, 'Injected'):
                adapter.start_preview(bpy.context, source, registry, profiles,
                                      [registry['strands'][0]['strand_id']])
        self.assertFalse(adapter.status()['active'])
        assert_baseline(self, source, armature, saved)

    def test_author_pose_switch_and_shape_values_override_keys_without_editing_actions(self):
        source, armature, registry, profiles = fixture()
        head = armature.pose.bones['Head']
        detail = source.data.shape_keys.key_blocks['Artist Detail']
        head['artist_pose_mix'] = 0.1
        head.keyframe_insert(data_path='["artist_pose_mix"]', frame=12)
        head['artist_pose_mix'] = 0.9
        head.keyframe_insert(data_path='["artist_pose_mix"]', frame=20)
        detail.value = 0.2
        detail.keyframe_insert(data_path='value', frame=12)
        detail.value = 0.6
        detail.keyframe_insert(data_path='value', frame=20)
        bpy.context.scene.frame_set(12)
        head['artist_pose_mix'] = 0.75
        detail.value = 0.83
        saved = baseline(source, armature)
        adapter.start_preview(bpy.context, source, registry, profiles,
                              [registry['strands'][0]['strand_id']])
        bpy.context.scene.frame_set(13)
        adapter.stop_preview()
        self.assertAlmostEqual(head['artist_pose_mix'], 0.75, places=6)
        self.assertAlmostEqual(detail.value, 0.83, places=6)
        assert_baseline(self, source, armature, saved)

    def test_idle_has_no_mode_timer_and_ui_cache_does_not_replace_start_proof(self):
        source, armature, registry, profiles = fixture()
        adapter.register_guards()
        self.assertFalse(bpy.app.timers.is_registered(adapter._mode_timer))
        adapter._AVAILABLE_CACHE = None
        with patch.object(adapter, '_backend', wraps=adapter._backend) as proof:
            self.assertTrue(adapter.available()['available'])
            self.assertTrue(adapter.available()['available'])
            self.assertEqual(proof.call_count, 1)
            adapter.start_preview(bpy.context, source, registry, profiles,
                                  [registry['strands'][0]['strand_id']])
            self.assertEqual(proof.call_count, 2)
        self.assertTrue(bpy.app.timers.is_registered(adapter._mode_timer))
        adapter.stop_preview()
        self.assertFalse(bpy.app.timers.is_registered(adapter._mode_timer))

    def test_full_character_designer_register_preview_and_unregister_keep_backend(self):
        import character_designer as main
        from character_designer import bone_collections, forearm_twist
        source, armature, registry, profiles = fixture()
        backend_handlers = {name: tuple(getattr(bpy.app.handlers, name)) for name in
                            ('frame_change_pre', 'frame_change_post', 'load_post')}
        registered = False
        try:
            main.register()
            registered = True
            main._validate_registration_integrity()
            for name, expected in main._WINDOW_MANAGER_POINTER_TYPES:
                prop = bpy.types.WindowManager.bl_rna.properties.get(name)
                self.assertIsNotNone(prop, msg=name)
                self.assertEqual(prop.type, 'POINTER', msg=name)
                self.assertEqual(prop.fixed_type, expected.bl_rna, msg=name)
                self.assertEqual(getattr(bpy.context.window_manager, name).bl_rna, expected.bl_rna)
            self.assertEqual(bpy.app.handlers.frame_change_post.count(bone_collections._frame_visibility), 1)
            self.assertEqual(bpy.app.handlers.frame_change_post.count(forearm_twist._frame_post), 1)
            self.assertFalse(forearm_twist._INITIALIZE_PENDING)
            self.assertFalse(forearm_twist._SCENE_CLEANUP_PENDING)
            self.assertFalse(bpy.app.timers.is_registered(adapter._mode_timer))
            # Idempotent full registration must not duplicate the callbacks.
            main.register()
            main._validate_registration_integrity()
            self.assertEqual(bpy.app.handlers.frame_change_pre.count(adapter._frame_guard), 1)
            self.assertEqual(bpy.app.handlers.frame_change_post.count(forearm_twist._frame_post), 1)
            saved = baseline(source, armature)
            ids = [item['strand_id'] for item in registry['strands']]
            with patch.object(forearm_twist, '_INITIALIZE_PENDING', True):
                with self.assertRaisesRegex(adapter.HairWiggleError, 'Forearm'):
                    adapter.start_preview(bpy.context, source, registry, profiles, ids)
            assert_baseline(self, source, armature, saved)
            adapter.start_preview(bpy.context, source, registry, profiles, ids)
            for frame in (13, 14, 15):
                bpy.context.scene.frame_set(frame)
                self.assertTrue(adapter.status()['active'])
            adapter.stop_preview()
            assert_baseline(self, source, armature, saved)
            self.assertFalse(bpy.app.timers.is_registered(adapter._mode_timer))
            main.unregister()
            registered = False
            for name, _expected in main._WINDOW_MANAGER_POINTER_TYPES:
                self.assertIsNone(bpy.types.WindowManager.bl_rna.properties.get(name), msg=name)
            self.assertNotIn(adapter._frame_guard, bpy.app.handlers.frame_change_pre)
            self.assertNotIn(bone_collections._frame_visibility, bpy.app.handlers.frame_change_post)
            self.assertNotIn(forearm_twist._frame_post, bpy.app.handlers.frame_change_post)
            self.assertFalse(bpy.app.timers.is_registered(adapter._mode_timer))
            for name, functions in backend_handlers.items():
                self.assertTrue(all(function in getattr(bpy.app.handlers, name) for function in functions))
            self.assertTrue(adapter._backend())
        finally:
            if registered:
                main.unregister()

    def test_animation_library_snapshot_stops_real_preview_before_native_write(self):
        import character_designer as main
        from character_designer import animation_export as exporter
        source, armature, registry, profiles = fixture()
        head = armature.pose.bones['Head']
        head.rotation_mode = 'XYZ'
        for frame, value in ((12, 0.15), (20, 0.35)):
            head.rotation_euler.x = value
            head.keyframe_insert(data_path='rotation_euler', frame=frame)
        bpy.context.scene.frame_set(12)
        action = armature.animation_data.action
        registered, job = False, None
        try:
            main.register()
            registered = True
            saved = baseline(source, armature)
            adapter.start_preview(bpy.context, source, registry, profiles,
                                  [registry['strands'][0]['strand_id']])
            bpy.context.scene.frame_set(13)
            events = []
            native_write = bpy.data.libraries.write
            def write_snapshot(*args, **kwargs):
                events.append('native write after actual stop')
                self.assertFalse(adapter.status()['active'])
                assert_baseline(self, source, armature, saved)
                return native_write(*args, **kwargs)
            class Delegate:
                def __init__(self, target, **overrides):
                    self.target, self.overrides = target, overrides
                def __getattr__(self, name):
                    return self.overrides[name] if name in self.overrides else getattr(self.target, name)
            observed = Delegate(bpy, data=Delegate(bpy.data,
                libraries=Delegate(bpy.data.libraries, write=write_snapshot)))
            process = Mock()
            process.poll.return_value = 0
            process.returncode = 0
            with tempfile.TemporaryDirectory(prefix='cd_native_action_snapshot_') as folder:
                with patch.object(exporter, 'bpy', observed), \
                        patch.object(exporter.subprocess, 'Popen', return_value=process) as spawn, \
                        patch.object(self.backend['module'].operators.WiggleBake, 'execute',
                                     side_effect=AssertionError('Wiggle baking is forbidden')) as bake:
                    job = exporter.begin_export(bpy.context, armature, action,
                        str(Path(folder) / 'Authored Action.fbx'), frame_start=12, frame_end=20)
                    self.assertEqual(events, ['native write after actual stop'])
                    self.assertTrue((job['root'] / 'animation.blend').is_file())
                    spawn.assert_called_once()  # Only the process launch is mocked.
                    bake.assert_not_called()
                    self.assertEqual(armature.animation_data.action, action)
                    assert_baseline(self, source, armature, saved)
                    exporter.cancel_export(job)
                    job = None
            self.assertIsNone(exporter.active_job())
        finally:
            if job is not None:
                exporter.cancel_export(job)
            if registered:
                main.unregister()

    def test_animation_worker_disables_actual_transient_rna_before_sampling(self):
        from character_designer import animation_export_worker as worker
        source, armature, registry, _profiles = fixture()
        head = armature.pose.bones['Head']
        head.rotation_euler.z = 0.2
        head.keyframe_insert(data_path='rotation_euler', frame=12)
        saved = baseline(source, armature)
        try:
            armature.pose.bones[registry['strands'][0]['bones'][0]].wiggle.tail = True
            bpy.context.scene.wiggle.enable = True
            armature.wiggle.freeze = False
            proof = adapter._animation_proof()
            with patch.object(self.backend['module'].operators.WiggleBake, 'execute',
                              side_effect=AssertionError('Wiggle baking is forbidden')) as bake:
                # Exercise the real worker's entry on a disposable scene. The
                # missing rig stops it immediately after its mandatory disable,
                # before skeleton cleanup, sampling, keys or FBX publication.
                with self.assertRaisesRegex(worker.AnimationExportError, 'missing'):
                    worker.export_job({'rig': 'Missing disposable worker rig'})
                self.assertFalse(bpy.context.scene.wiggle.enable)
                self.assertTrue(armature.wiggle.freeze)
                self.assertTrue(adapter._animation_same(proof))
                bake.assert_not_called()
        finally:
            for snapshot in reversed(saved['raw']):
                adapter._restore_raw(snapshot)
        assert_baseline(self, source, armature, saved)

    def test_constraints_drivers_stale_parent_competitors_refuse_atomically(self):
        for problem in ('constraint', 'driver', 'object_driver', 'object_constraint',
                        'parent', 'foreign_wiggle', 'zero_gravity'):
            with self.subTest(problem=problem):
                source, armature, registry, profiles = fixture()
                item = registry['strands'][0]
                bone = armature.pose.bones[item['bones'][0]]
                if problem == 'constraint':
                    bone.constraints.new('COPY_ROTATION').mute = True
                elif problem == 'driver':
                    bone.driver_add('rotation_euler', 0).driver.expression = '0.0'
                elif problem == 'object_driver':
                    armature.driver_add('location', 0).driver.expression = '0.0'
                elif problem == 'object_constraint':
                    armature.constraints.new('COPY_LOCATION').mute = True
                elif problem == 'parent':
                    activate(armature)
                    bpy.ops.object.mode_set(mode='EDIT')
                    armature.data.edit_bones[item['bones'][0]].parent = None
                    bpy.ops.object.mode_set(mode='OBJECT')
                    activate(source)
                elif problem == 'foreign_wiggle':
                    armature.pose.bones['Head'].wiggle.tail = True
                else:
                    bpy.context.scene.gravity = (0, 0, 0)
                saved = baseline(source, armature)
                with self.assertRaises(ValueError):
                    adapter.start_preview(bpy.context, source, registry, profiles, [item['strand_id']])
                self.assertFalse(adapter.status()['active'])
                assert_baseline(self, source, armature, saved)

    def test_undo_load_render_mode_and_loop_guards_preserve_backend_handlers(self):
        for operation in ('undo_pre', 'redo_pre', 'load_pre', 'render_pre', 'mode', 'loop'):
            with self.subTest(operation=operation):
                source, armature, registry, profiles = fixture()
                saved = baseline(source, armature)
                external = self.backend['engine'].wiggle_pre
                adapter.start_preview(bpy.context, source, registry, profiles,
                                      [registry['strands'][0]['strand_id']])
                bpy.context.scene.frame_set(13)
                if operation == 'mode':
                    activate(armature)
                    bpy.ops.object.mode_set(mode='EDIT')
                    adapter._mode_timer()
                    bpy.ops.object.mode_set(mode='OBJECT')
                    activate(source)
                elif operation == 'loop':
                    bpy.context.scene.frame_set(1)
                else:
                    self.assertIn(adapter._lifecycle_guard, getattr(bpy.app.handlers, operation))
                    adapter._lifecycle_guard(bpy.context.scene)
                self.assertFalse(adapter.status()['active'])
                self.assertIn(external, bpy.app.handlers.frame_change_pre)
                assert_baseline(self, source, armature, saved)

    def test_disjoint_callback_proof_and_unregistration_preserve_external_callback(self):
        source, armature, registry, profiles = fixture()
        def external_callback(scene, *args):
            pass
        bpy.app.handlers.frame_change_post.append(external_callback)
        try:
            saved = baseline(source, armature)
            ids = [registry['strands'][0]['strand_id']]
            with self.assertRaisesRegex(adapter.HairWiggleError, 'callback'):
                adapter.start_preview(bpy.context, source, registry, profiles, ids)
            assert_baseline(self, source, armature, saved)
            adapter.register_frame_callback_proof(external_callback, armature, ['Head'])
            adapter.start_preview(bpy.context, source, registry, profiles, ids)
            adapter.unregister_guards()
            self.assertIn(external_callback, bpy.app.handlers.frame_change_post)
            self.assertIn(self.backend['engine'].wiggle_post, bpy.app.handlers.frame_change_post)
            assert_baseline(self, source, armature, saved)
        finally:
            bpy.app.handlers.frame_change_post.remove(external_callback)
            adapter.unregister_frame_callback_proof(external_callback)


def main():
    try:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeHairWiggleTests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful():
            raise SystemExit(1)
        print('HAIR_WIGGLE_ADAPTER_NATIVE_OK', result.testsRun)
    finally:
        adapter.unregister_guards()
        if _TEST_BACKEND is not None:
            _TEST_BACKEND.unregister()


if __name__ == '__main__':
    main()
