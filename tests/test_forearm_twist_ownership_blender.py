"""Foreign corrective-name collisions and interrupted-preview ownership.

Run in a disposable Blender --background --factory-startup process only.
Fixtures create their own bilateral mesh; production character files are never read.
"""
import math
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import forearm_twist as runtime
import test_forearm_twist_blender as fixtures
from test_forearm_twist_ranges_blender import bilateral_fixture


def key_snapshot(mesh, names=None):
    """Include identity and foreign settings as well as every coordinate."""
    if mesh.data.shape_keys is None:
        return {}
    return {
        key.name: (key.as_pointer(), key.value, key.mute, key.relative_key.as_pointer(),
                   key.slider_min, key.slider_max, key.vertex_group, key.interpolation,
                   tuple(tuple(point.co) for point in key.data))
        for key in mesh.data.shape_keys.key_blocks if names is None or key.name in names
    }


def collision_fixture(*, reserve_suffix=False):
    fixture = bilateral_fixture()  # Based on fixtures.make_fixture(..., build_ik=False).
    mesh = fixture['mesh']
    names = [runtime.KEY_PREFIX + side for side in ('L', 'R')]
    if reserve_suffix:
        names.append(runtime.KEY_PREFIX + 'L.001')
    for index, name in enumerate(names):
        key = mesh.shape_key_add(name=name, from_mix=False)
        key.data[index + 2].co += Vector((.003 * (index + 1), -.005, .009))
        key.value = 0.0
        key.mute = bool(index % 2)
        key.slider_min, key.slider_max = -.2, 1.4
    mesh.active_shape_key_index = len(mesh.data.shape_keys.key_blocks) - 1
    assert runtime.RECORD_KEY not in mesh and runtime.PREVIEW_KEY not in mesh
    return fixture, tuple(names)


class InitializationFailingKey:
    """Fail after Blender creates the real key, while forwarding its identity."""
    def __init__(self, original):
        self.original = original

    def __getattr__(self, name):
        return getattr(self.original, name)

    @property
    def relative_key(self):
        return self.original.relative_key

    @relative_key.setter
    def relative_key(self, _value):
        raise RuntimeError('injected key initialization failure')


class InitializationFailingObject:
    """Exercise real shape-key allocation and deletion behind a narrow proxy."""
    def __init__(self, original):
        self.original = original
        self.added_names = []
        self.removed_names = []

    def __getattr__(self, name):
        return getattr(self.original, name)

    @property
    def active_shape_key_index(self):
        return self.original.active_shape_key_index

    @active_shape_key_index.setter
    def active_shape_key_index(self, value):
        self.original.active_shape_key_index = value

    def shape_key_add(self, *, name, from_mix):
        key = self.original.shape_key_add(name=name, from_mix=from_mix)
        self.added_names.append(key.name)
        return key if name == 'Basis' else InitializationFailingKey(key)

    def shape_key_remove(self, key):
        original = key.original if isinstance(key, InitializationFailingKey) else key
        self.removed_names.append(original.name)
        self.original.shape_key_remove(original)


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNone(runtime._SESSION, 'A preceding preview must finish before a new fixture.')
        runtime._KEY_REFERENCES.clear()
        runtime._CACHE.clear()
        runtime._OUTPUT_CACHE.clear()
        runtime._ERRORS.clear()

    def tearDown(self):
        # A failed assertion must not let its temporary session affect the next fixture.
        if runtime._SESSION is not None:
            runtime.finish_test(bpy.context, False)

    def assert_originals(self, fixture, snapshot, pose, structure):
        self.assertEqual(key_snapshot(fixture['mesh']), snapshot)
        self.assertEqual(fixtures.structure_snapshot(fixture['armature'], fixture['mesh']), structure)
        fixtures.assert_pose_snapshot(fixture['armature'], pose, 'Ownership recovery')
        self.assertIsNone(runtime._SESSION)
        self.assertNotIn(runtime.PREVIEW_KEY, fixture['mesh'])
        self.assertNotIn(runtime.RECORD_KEY, fixture['mesh'])

    def test_fresh_paired_collision_from_either_side_uses_unique_names(self):
        for side in ('L', 'R'):
            with self.subTest(side=side):
                fixture, foreign = collision_fixture(reserve_suffix=True)
                mesh, arm = fixture['mesh'], fixture['armature']
                originals = key_snapshot(mesh)
                pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
                record = runtime.start_test(bpy.context, mesh, side, symmetry=True, initial_angle=math.pi / 2)
                self.assertNotIn(record['key'], originals)
                self.assertTrue(record['key'].startswith(runtime.KEY_PREFIX + side + '.'))
                self.assertEqual(runtime._SESSION['key'], record['key'])
                self.assertEqual(json.loads(mesh[runtime.PREVIEW_KEY])['key'], record['key'])
                self.assertEqual(key_snapshot(mesh, foreign), {name: originals[name] for name in foreign})
                runtime.finish_test(bpy.context, True)
                records = runtime._records(mesh)
                self.assertEqual(set(records), {'L', 'R'})
                for which, saved in records.items():
                    self.assertNotIn(saved['key'], originals)
                    self.assertTrue(saved['key'].startswith(runtime.KEY_PREFIX + which + '.'))
                    self.assertIn(saved['key'], mesh.data.shape_keys.key_blocks)
                self.assertNotEqual(records['L']['key'], records['R']['key'])
                self.assertEqual(key_snapshot(mesh, foreign), {name: originals[name] for name in foreign})
                runtime.remove_paired_calibration(bpy.context, mesh)
                self.assert_originals(fixture, originals, pose, structure)

    def test_confirmed_reedit_and_remove_preserve_foreign_data(self):
        fixture, foreign = collision_fixture()
        mesh, arm = fixture['mesh'], fixture['armature']
        originals = key_snapshot(mesh)
        pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
        runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
        runtime.set_ratio(bpy.context, 3, .31)
        runtime.finish_test(bpy.context, True)
        owned_names = {side: record['key'] for side, record in runtime._records(mesh).items()}
        runtime._KEY_REFERENCES.clear()  # The persisted names must be sufficient.
        runtime.start_test(bpy.context, mesh, 'R', symmetry=True, initial_angle=math.pi / 2)
        runtime.set_ratio(bpy.context, 3, .69)
        runtime.finish_test(bpy.context, True)
        records = runtime._records(mesh)
        self.assertAlmostEqual(records['R']['rings'][3]['ratio'], .69)
        self.assertEqual({side: record['key'] for side, record in records.items()}, owned_names)
        self.assertEqual(key_snapshot(mesh, foreign), {name: originals[name] for name in foreign})
        runtime._KEY_REFERENCES.clear()
        runtime.remove_paired_calibration(bpy.context, mesh)
        self.assert_originals(fixture, originals, pose, structure)

    def test_cancel_after_reference_cache_loss_removes_only_actual_preview_key(self):
        fixture, _foreign = collision_fixture()
        mesh, arm = fixture['mesh'], fixture['armature']
        originals = key_snapshot(mesh)
        pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
        record = runtime.start_test(bpy.context, mesh, 'R', symmetry=True, initial_angle=math.pi / 2)
        actual = record['key']
        self.assertNotIn(actual, originals)
        runtime.set_ratio(bpy.context, 3, .48)
        runtime._KEY_REFERENCES.clear()
        runtime.finish_test(bpy.context, False)
        self.assertNotIn(actual, mesh.data.shape_keys.key_blocks)
        self.assert_originals(fixture, originals, pose, structure)

    def test_serialized_interruption_recovers_fresh_and_existing_suffixed_keys(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                fixture, foreign = collision_fixture()
                mesh, arm = fixture['mesh'], fixture['armature']
                originals = key_snapshot(mesh)
                pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
                if existing:
                    runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
                    runtime.set_ratio(bpy.context, 3, .28)
                    runtime.finish_test(bpy.context, True)
                baseline, old_raw = key_snapshot(mesh), mesh.get(runtime.RECORD_KEY)
                runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
                runtime.set_ratio(bpy.context, 3, .87)
                actual = runtime._records(mesh)['L']['key']
                marker = json.loads(mesh[runtime.PREVIEW_KEY])
                self.assertEqual(marker['key'], actual)
                runtime._SESSION = None
                runtime._KEY_REFERENCES.clear()
                runtime._CACHE.clear()
                runtime._OUTPUT_CACHE.clear()
                runtime._recover_previews()
                self.assertIsNone(runtime._SESSION)
                self.assertNotIn(runtime.PREVIEW_KEY, mesh)
                self.assertEqual(mesh.get(runtime.RECORD_KEY), old_raw)
                self.assertEqual(key_snapshot(mesh), baseline)
                self.assertEqual(key_snapshot(mesh, foreign), {name: originals[name] for name in foreign})
                fixtures.assert_pose_snapshot(arm, pose, 'Serialized suffixed-key recovery')
                if existing:
                    runtime.remove_paired_calibration(bpy.context, mesh)
                self.assert_originals(fixture, originals, pose, structure)

    def test_preview_creation_failure_rolls_back_without_adopting_foreign_keys(self):
        fixture, _foreign = collision_fixture()
        mesh, arm = fixture['mesh'], fixture['armature']
        originals = key_snapshot(mesh)
        pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
        with patch.object(runtime, '_test_pose', side_effect=RuntimeError('injected preview pose failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected preview pose failure'):
                runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
        self.assert_originals(fixture, originals, pose, structure)

    def test_key_initialization_failure_cleans_actual_key_and_new_basis_only(self):
        for has_foreign in (True, False):
            with self.subTest(has_foreign=has_foreign):
                if has_foreign:
                    fixture, _foreign = collision_fixture()
                else:
                    fixture = fixtures.make_fixture('DIRECT_PREROLL', build_ik=False)
                    fixture['mesh'].shape_key_clear()
                mesh, arm = fixture['mesh'], fixture['armature']
                originals = key_snapshot(mesh)
                pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
                old_index, old_busy = mesh.active_shape_key_index, runtime._BUSY
                proxy = InitializationFailingObject(mesh)
                record = {'key': runtime._new_key_name(mesh, 'L', {})}
                actual = record['key']
                self.assertNotIn(actual, originals)
                with self.assertRaisesRegex(RuntimeError, 'injected key initialization failure'):
                    runtime._new_output_key(proxy, record)
                self.assertEqual(proxy.added_names, [actual] if has_foreign else ['Basis', actual])
                self.assertEqual(proxy.removed_names, [actual] if has_foreign else [actual, 'Basis'])
                self.assertEqual(mesh.active_shape_key_index, old_index)
                self.assertEqual(runtime._BUSY, old_busy)
                if not has_foreign:
                    self.assertIsNone(mesh.data.shape_keys, 'A newly created Basis must also roll back.')
                self.assert_originals(fixture, originals, pose, structure)

    def test_late_paired_confirm_failure_cancels_or_recovers_both_new_owned_keys(self):
        for serialized in (False, True):
            with self.subTest(serialized=serialized):
                fixture, foreign = collision_fixture()
                mesh, arm = fixture['mesh'], fixture['armature']
                originals = key_snapshot(mesh)
                pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
                runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
                runtime.set_ratio(bpy.context, 3, .53)
                with patch.object(runtime, '_restore_selection',
                                  side_effect=ValueError('injected late selection restore failure')):
                    with self.assertRaisesRegex(ValueError, 'injected late selection restore failure'):
                        runtime.finish_test(bpy.context, True)
                records = runtime._records(mesh)
                self.assertEqual(set(records), {'L', 'R'}, 'Mirroring must complete before the injected failure.')
                owned = {side: record['key'] for side, record in records.items()}
                for side, name in owned.items():
                    self.assertNotIn(name, originals)
                    self.assertTrue(name.startswith(runtime.KEY_PREFIX + side + '.'))
                    self.assertIn(name, mesh.data.shape_keys.key_blocks)
                self.assertIsNotNone(runtime._SESSION)
                self.assertIn(runtime.PREVIEW_KEY, mesh)
                marker = json.loads(mesh[runtime.PREVIEW_KEY])
                self.assertEqual(runtime._SESSION['added_keys']['R'], owned['R'])
                self.assertEqual(marker['added_keys']['R'], owned['R'])
                self.assertEqual(key_snapshot(mesh, foreign), {name: originals[name] for name in foreign})
                runtime._KEY_REFERENCES.clear()
                if serialized:
                    runtime._SESSION = None
                    runtime._CACHE.clear()
                    runtime._OUTPUT_CACHE.clear()
                    runtime._recover_previews()
                else:
                    runtime.finish_test(bpy.context, False)
                for name in owned.values():
                    self.assertNotIn(name, mesh.data.shape_keys.key_blocks)
                self.assert_originals(fixture, originals, pose, structure)

    def test_failed_cancel_keeps_session_and_marker_for_retry(self):
        fixture, foreign = collision_fixture()
        mesh, arm = fixture['mesh'], fixture['armature']
        originals = key_snapshot(mesh)
        pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
        runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
        marker, actual = mesh[runtime.PREVIEW_KEY], runtime._SESSION['key']
        with patch.object(runtime, '_managed_key', side_effect=RuntimeError('injected ownership lookup failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected ownership lookup failure'):
                runtime.finish_test(bpy.context, False)
        self.assertIsNotNone(runtime._SESSION)
        self.assertEqual(runtime._SESSION['key'], actual)
        self.assertEqual(mesh[runtime.PREVIEW_KEY], marker)
        self.assertIn(actual, mesh.data.shape_keys.key_blocks)
        self.assertEqual(key_snapshot(mesh, foreign), {name: originals[name] for name in foreign})
        runtime._KEY_REFERENCES.clear()
        runtime.finish_test(bpy.context, False)
        self.assert_originals(fixture, originals, pose, structure)

    def test_cancel_refuses_artist_relative_reference_without_mutating_preview(self):
        fixture, _foreign = collision_fixture()
        mesh, arm = fixture['mesh'], fixture['armature']
        originals = key_snapshot(mesh)
        pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
        record = runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
        runtime.set_ratio(bpy.context, 3, .47)
        owned = mesh.data.shape_keys.key_blocks[record['key']]
        artist = mesh.shape_key_add(name='Artist relative to preview', from_mix=False)
        artist.data[5].co += Vector((.021, -.013, .037))
        artist.value, artist.mute = .39, True
        artist.relative_key = owned
        pending = key_snapshot(mesh)
        pending_pose = fixtures.pose_snapshot(arm)
        session, marker, raw = runtime._SESSION, mesh[runtime.PREVIEW_KEY], mesh[runtime.RECORD_KEY]
        try:
            with self.assertRaisesRegex(runtime.ForearmTwistError, 'references the corrective key'):
                runtime.finish_test(bpy.context, False)
            self.assertIs(runtime._SESSION, session)
            self.assertEqual(mesh[runtime.PREVIEW_KEY], marker)
            self.assertEqual(mesh[runtime.RECORD_KEY], raw)
            self.assertEqual(key_snapshot(mesh), pending)
            fixtures.assert_pose_snapshot(arm, pending_pose, 'Rejected referenced-key rollback')
            self.assertEqual(fixtures.structure_snapshot(arm, mesh), structure)
        finally:
            # Restore only the deliberate conflict, even if a preceding assertion fails.
            artist.relative_key = mesh.data.shape_keys.reference_key
        expected = dict(originals)
        expected.update(key_snapshot(mesh, (artist.name,)))
        runtime.finish_test(bpy.context, False)
        self.assertNotIn(record['key'], mesh.data.shape_keys.key_blocks)
        self.assert_originals(fixture, expected, pose, structure)

    def test_cancel_refuses_renamed_owned_key_collision_then_restores_original_calibration(self):
        fixture, _foreign = collision_fixture()
        mesh, arm = fixture['mesh'], fixture['armature']
        pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
        runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
        runtime.set_ratio(bpy.context, 3, .26)
        runtime.finish_test(bpy.context, True)
        baseline, old_raw = key_snapshot(mesh), mesh[runtime.RECORD_KEY]
        original_name = runtime._records(mesh)['L']['key']
        runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
        runtime.set_ratio(bpy.context, 3, .81)
        owned = runtime._managed_key(mesh, 'L', runtime._records(mesh)['L'])
        owned.name = 'Artist renamed owned corrective'
        artist = mesh.shape_key_add(name=original_name, from_mix=False)
        self.assertEqual(artist.name, original_name)
        artist.data[7].co += Vector((-.031, .027, .043))
        artist.value, artist.mute = .62, True
        pending = key_snapshot(mesh)
        pending_pose = fixtures.pose_snapshot(arm)
        session, marker, raw = runtime._SESSION, mesh[runtime.PREVIEW_KEY], mesh[runtime.RECORD_KEY]
        try:
            with self.assertRaisesRegex(runtime.ForearmTwistError, 'Another Shape Key uses'):
                runtime.finish_test(bpy.context, False)
            self.assertIs(runtime._SESSION, session)
            self.assertEqual(mesh[runtime.PREVIEW_KEY], marker)
            self.assertEqual(mesh[runtime.RECORD_KEY], raw)
            self.assertEqual(key_snapshot(mesh), pending)
            fixtures.assert_pose_snapshot(arm, pending_pose, 'Rejected renamed-key rollback')
            self.assertEqual(fixtures.structure_snapshot(arm, mesh), structure)
        finally:
            artist.name = 'Artist replacement preserved'
        artist_snapshot = key_snapshot(mesh, (artist.name,))
        runtime.finish_test(bpy.context, False)
        self.assertEqual(owned.name, original_name)
        self.assertEqual(key_snapshot(mesh, baseline), baseline)
        self.assertEqual(key_snapshot(mesh, (artist.name,)), artist_snapshot)
        self.assertEqual(set(key_snapshot(mesh)), set(baseline) | {artist.name})
        self.assertEqual(mesh[runtime.RECORD_KEY], old_raw)
        self.assertIsNone(runtime._SESSION)
        self.assertNotIn(runtime.PREVIEW_KEY, mesh)
        fixtures.assert_pose_snapshot(arm, pose, 'Retried renamed-key rollback')
        self.assertEqual(fixtures.structure_snapshot(arm, mesh), structure)

    def test_cancel_runtime_reentry_does_not_create_an_opposite_orphan(self):
        fixture, _foreign = collision_fixture()
        mesh, arm = fixture['mesh'], fixture['armature']
        originals = key_snapshot(mesh)
        pose, structure = fixtures.pose_snapshot(arm), fixtures.structure_snapshot(arm, mesh)
        runtime.start_test(bpy.context, mesh, 'L', symmetry=True, initial_angle=math.pi / 2)
        original_lookup = runtime._managed_key
        callbacks = []
        def evaluated_during_lookup(*args, **kwargs):
            if not callbacks:
                callbacks.append(True)
                runtime.update_runtime(bpy.context.scene)
            return original_lookup(*args, **kwargs)
        with patch.object(runtime, '_managed_key', side_effect=evaluated_during_lookup):
            runtime.finish_test(bpy.context, False)
        self.assertTrue(callbacks, 'Exercise evaluation while cancellation is resolving its key.')
        self.assert_originals(fixture, originals, pose, structure)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OwnershipTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
