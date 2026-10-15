"""Hair proofs use authenticated authored Rest for Worklist motion copies.

Run only in a serial disposable Blender background factory process. Native
fixtures exercise the coordinator and worker proof functions without starting
a worker, playback, FBX export, or add-on handlers. The one libraries.write /
reopen roundtrip uses a temporary snapshot and never opens an artist file.
"""

import json
from pathlib import Path
import re
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import animation_worklist_target as target
from character_designer import hair_bones_groups as groups, hair_bones_rig as hair
from character_designer import hair_motion_profiles as profiles
from character_designer import hair_strand_registry as registry
from character_designer import limb_ik, skirt_rig, unity_animation as ua
from character_designer import unity_export as exporter, unity_export_worker as worker
from test_hair_bones_rig_blender import activate, plain_snapshot
from test_hair_strand_registry_blender import bound_fixture, initialize_unpaired

_PRIVATE_SNAPSHOT = None


def require_disposable_process(*, allow_private_snapshot=False):
    owned_file = allow_private_snapshot and bpy.data.filepath == _PRIVATE_SNAPSHOT
    if (not bpy.app.background or '--factory-startup' not in sys.argv
            or bpy.data.filepath and not owned_file):
        raise RuntimeError('Use --background --factory-startup without an opened artist file.')


def token(value):
    if isinstance(value, bpy.types.ID):
        return (type(value).__name__, value.name, value.as_pointer())
    if hasattr(value, 'items'):
        return tuple((key, token(entry)) for key, entry in sorted(value.items()))
    if hasattr(value, 'to_list'):
        return tuple(token(entry) for entry in value.to_list())
    if isinstance(value, (list, tuple)):
        return tuple(token(entry) for entry in value)
    return value


def properties(owner):
    return tuple((key, token(owner[key])) for key in sorted(owner.keys()))


def proof_state(f):
    """Include current/source Rest, custom ID references and source mesh data."""
    return {
        'file': (bpy.data.filepath, bpy.data.is_dirty),
        'source': plain_snapshot(f.source),
        'objects': tuple((obj.name, token(obj.data), properties(obj),
                          tuple(tuple(vertex.co) for vertex in obj.data.vertices)
                          if obj.type == 'MESH' else None,
                          tuple((key.name, tuple(tuple(point.co) for point in key.data))
                                for key in obj.data.shape_keys.key_blocks)
                          if obj.type == 'MESH' and obj.data.shape_keys else None)
                         for obj in sorted(bpy.data.objects, key=lambda obj: obj.name)),
        'armatures': tuple((data.name, data.as_pointer(), data.users,
                            ua._rest_state(data), properties(data),
                            tuple((bone.name, properties(bone)) for bone in data.bones))
                           for data in sorted(bpy.data.armatures, key=lambda data: data.name)),
        'frame': (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe),
    }


def edit_data(f, data, callback):
    """Edit only a fixture datablock, preserving which copy the rig uses."""
    current = f.rig.data
    activate(f.rig)
    f.rig.data = data
    try:
        activate(f.rig, 'EDIT')
        callback(data.edit_bones)
    finally:
        activate(f.rig)
        f.rig.data = current
        activate(f.source)


def store_adoption(f, *, mapped_names=None):
    target._store(f.rig, {
        'version': 1, 'rig': f.rig, 'scene': bpy.context.scene,
        'view_layer': bpy.context.view_layer.name,
        'workspace_identity': 'native-hair-worklist-rest-fixture',
        'capture': f.artist_capture,
        'packet_path': 'unused-hair-fixture-packet.json', 'packet_sha256': 'f' * 64,
        'motion_data': f.motion, 'motion_rest': ua._rest_state(f.motion),
        'mapped_names': sorted(f.original.bones.keys()) if mapped_names is None else mapped_names,
    })


def fixture(*, adopted=True, anchor_connected=True):
    source, _plans, result = bound_fixture(strands=2, count=3, captured=True)
    rig = result['armature']
    f = SimpleNamespace(source=source, rig=rig, original=rig.data, motion=None)
    edit_data(f, f.original,
              lambda bones: setattr(bones['spine.006'], 'use_connect', anchor_connected))
    f.registry = initialize_unpaired(source)
    profiles.initialize(source, registry=f.registry)
    f.raw_source = properties(source)
    f.hair_names = [name for strand in f.registry['strands'] for name in strand['bones']]
    f.translated = [name for strand in f.registry['strands'] for name in strand['bones'][1:]]
    if adopted:
        f.artist_capture = target._capture(bpy.context, rig)
        f.motion = f.original.copy()
        f.motion.name = 'Hair Owned Worklist Motion'
        rig.data = f.motion
        def disconnect(bones):
            for name in f.translated + (['spine.006'] if anchor_connected else []):
                bones[name].use_connect = False
        edit_data(f, f.motion, disconnect)
        store_adoption(f)
    activate(source)
    assert properties(source) == f.raw_source, 'Fixture adoption rewrote Hair source metadata'
    return f


class HairWorklistRestTests(unittest.TestCase):
    def setUp(self):
        global _PRIVATE_SNAPSHOT
        # A previous test may have reopened its own temporary snapshot.
        require_disposable_process(allow_private_snapshot=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        _PRIVATE_SNAPSHOT = None
        target._compile_rest_record.cache_clear()

    def assert_accepted(self, f, *, captured=None, objects=None):
        objects = [f.source, f.rig] if objects is None else objects
        before = proof_state(f)
        self.assertEqual(registry.read(f.source, validate=True), f.registry)
        current = exporter._capture_hair_motion(objects)
        self.assertEqual(current['registry'], f.registry)
        if captured is not None:
            self.assertEqual(current, captured)
        self.assertEqual(worker._capture_hair_snapshot({'hair_motion': current}, objects), current)
        self.assertEqual(proof_state(f), before, 'Read/capture/recheck mutated native fixture data')
        self.assertEqual(properties(f.source), f.raw_source, 'Hair source JSON or native references changed')
        return current

    def assert_rejected(self, f, captured, message):
        before = proof_state(f)
        objects = [f.source, f.rig]
        for service, error in (
            (lambda: registry.read(f.source, validate=True), registry.HairStrandRegistryError),
            (lambda: exporter._capture_hair_motion(objects), exporter.ExportError),
            (lambda: worker._capture_hair_snapshot({'hair_motion': captured}, objects), worker.ExportError),
        ):
            with self.subTest(service=error.__module__):
                with self.assertRaisesRegex(error, re.escape(message)):
                    service()
                self.assertEqual(proof_state(f), before, 'A refused proof wrote native data')
        self.assertEqual(properties(f.source), f.raw_source, 'A refusal rewrote source JSON')

    def test_owned_connected_translation_reads_authored_chain_and_anchor_without_json_rewrite(self):
        f = fixture()
        view = target.control_rest_view(f.rig)
        self.assertIs(view.original_data, f.original)
        self.assertIs(view.motion_data, f.motion)
        for name in f.translated + ['spine.006']:
            self.assertTrue(f.original.bones[name].use_connect)
            self.assertFalse(f.motion.bones[name].use_connect)
            source_bone = view.source_bone(f.motion.bones[name])
            self.assertEqual(source_bone.as_pointer(), f.original.bones[name].as_pointer())
            self.assertEqual(source_bone.id_data.as_pointer(), f.original.as_pointer())
            self.assertEqual(source_bone.name, name)
        self.assertTrue(all(strand['anchor']['connected'] for strand in f.registry['strands']))
        self.assert_accepted(f)
        self.assertIs(f.rig.data, f.motion, 'Proof switched the rig to its authored data')

    def test_basis_and_unrelated_shoes_coordinate_edits_keep_capture_and_worker_proof(self):
        f = fixture()
        captured = self.assert_accepted(f)
        f.source.shape_key_add(name='Basis')
        expression = f.source.shape_key_add(name='Artist asymmetry')
        expression.data[5].co.y += .03
        expression.value = .35
        f.source.data.shape_keys.reference_key.data[4].co.x += .001
        f.source.data.vertices[4].co.x += .001
        mesh = bpy.data.meshes.new('Shoes Mesh')
        mesh.from_pydata(((0., 0., 0.), (1., 0., 0.), (0., 1., 0.)), (), ((0, 1, 2),))
        shoes = bpy.data.objects.new('Shoes', mesh)
        bpy.context.scene.collection.objects.link(shoes)
        shoes.data.vertices[1].co.z += .002
        f.source.data.update()
        shoes.data.update()
        bpy.context.view_layer.update()
        self.assert_accepted(f, captured=captured, objects=[f.source, f.rig, shoes])

    def test_unmanaged_connected_change_keeps_ordinary_strict_refusal(self):
        f = fixture(adopted=False)
        captured = self.assert_accepted(f)
        self.assertFalse(target.is_adopted(f.rig))
        edit_data(f, f.original, lambda bones: setattr(bones[f.translated[0]], 'use_connect', False))
        self.assert_rejected(f, captured, 'Hair connection or ordered parent relationships changed')

    def test_unmapped_connected_translation_is_rejected_with_matching_saved_motion_rest(self):
        f = fixture()
        captured = self.assert_accepted(f)
        names = sorted(set(f.original.bones.keys()) - {f.translated[0]})
        store_adoption(f, mapped_names=names)
        self.assert_rejected(f, captured, 'unapproved connected-bone flag')

    def test_reverse_connected_translation_is_rejected_with_matching_saved_motion_rest(self):
        f = fixture(anchor_connected=False)
        captured = self.assert_accepted(f)
        self.assertFalse(f.original.bones['spine.006'].use_connect)
        edit_data(f, f.motion, lambda bones: setattr(bones['spine.006'], 'use_connect', True))
        store_adoption(f)
        self.assert_rejected(f, captured, 'unapproved connected-bone flag')

    def test_changed_live_parent_cannot_be_blessed_by_updating_motion_rest(self):
        f = fixture()
        captured = self.assert_accepted(f)
        root = f.registry['strands'][0]['bones'][0]
        def reparent(bones):
            bones[root].parent = bones['Arm']
        edit_data(f, f.motion, reparent)
        store_adoption(f)
        self.assert_rejected(f, captured, 'Rest geometry, parents or inheritance')

    def test_changed_authored_or_motion_rest_is_rejected_after_a_warm_read(self):
        for data_label in ('original', 'motion'):
            for part in ('chain tip', 'anchor'):
                with self.subTest(data=data_label, part=part):
                    f = fixture()
                    captured = self.assert_accepted(f)
                    name = f.registry['strands'][0]['bones'][-1] if part == 'chain tip' else 'spine.006'
                    def change_tail(bones):
                        bones[name].tail.x += .002
                    edit_data(f, getattr(f, data_label), change_tail)
                    self.assert_rejected(f, captured, 'saved artist or owned motion-copy Rest changed')

    def assert_motion_microedit_rejected(self, *, anchor):
        f = fixture()
        captured = self.assert_accepted(f)
        name = 'spine.006' if anchor else f.registry['strands'][0]['bones'][-1]
        axis = 2 if anchor else 0
        before_motion = ua._rest_state(f.motion)
        before_original = ua._rest_state(f.original)
        saved_motion = target._load(f.rig)['motion_rest']
        session_raw = f.rig[target.SESSION]
        coordinate = before_motion[name]['tail'][axis]
        self.assertGreater(coordinate, 0.0, 'Fixture requires a positive native float32 coordinate')
        bits = struct.unpack('<I', struct.pack('<f', coordinate))[0]
        next_coordinate = struct.unpack('<f', struct.pack('<I', bits + 1))[0]
        # Advance exactly one native float32 step. Do not store a replacement
        # motion_rest: the original adoption record must accept this only by
        # its existing tolerance, while Hair keeps its exact geometry rule.
        def nudge_tail(bones):
            bones[name].tail[axis] = next_coordinate
        edit_data(f, f.motion, nudge_tail)
        after_motion = ua._rest_state(f.motion)
        actual_delta = abs(after_motion[name]['tail'][axis] - coordinate)
        self.assertGreater(actual_delta, 0.0, 'The native microedit rounded away')
        self.assertLess(actual_delta, 1e-6)
        self.assertNotEqual(after_motion[name], before_motion[name])
        for field in ('head', 'tail', 'matrix'):
            self.assertLess(max(abs(a - b) for a, b in
                                zip(after_motion[name][field], before_motion[name][field])), 1e-6)
        self.assertTrue(ua._same_current_rest_bone(before_motion[name], after_motion[name]),
                        'Microedit escaped the existing Worklist proof tolerance')
        before_lookup = proof_state(f)
        view = target.control_rest_view(f.rig)
        self.assertIsNotNone(view, 'The tolerance-boundary case must pass the Worklist Rest proof')
        self.assertIs(view.original_data, f.original)
        self.assertIs(view.motion_data, f.motion)
        self.assertEqual(proof_state(f), before_lookup, 'Worklist proof changed native data')
        self.assertEqual(ua._rest_state(f.original), before_original, 'Microedit touched authored Rest')
        self.assertEqual(f.rig[target.SESSION], session_raw, 'Microedit replaced saved adoption text')
        self.assertEqual(target._load(f.rig)['motion_rest'], saved_motion,
                         'Microedit widened the saved motion Rest')
        self.assert_rejected(f, captured, 'Hair chain Rest or segmentation changed')

    def test_motion_tip_sub_tolerance_native_microedit_still_fails_exact_hair_rest(self):
        self.assert_motion_microedit_rejected(anchor=False)

    def test_motion_anchor_sub_tolerance_native_microedit_still_fails_exact_hair_anchor(self):
        self.assert_motion_microedit_rejected(anchor=True)

    def test_live_hair_ownership_source_and_signature_remain_strict(self):
        f = fixture()
        captured = self.assert_accepted(f)
        bone = f.motion.bones[f.translated[0]]
        for key, value, message in (
            (hair.OWNER_KEY, 'foreign-owner', 'Hair bone source ownership changed'),
            (hair.SIGNATURE_KEY, 'foreign-signature', 'Hair bone source ownership changed'),
            (hair.SOURCE_KEY, None, 'missing, extra or reassigned'),
        ):
            with self.subTest(key=key):
                saved = bone[key]
                if value is None:
                    del bone[key]
                else:
                    bone[key] = value
                try:
                    self.assert_rejected(f, captured, message)
                finally:
                    bone[key] = saved
                self.assert_accepted(f, captured=captured)

    def test_generated_control_or_dress_ownership_never_gets_connection_exception(self):
        f = fixture()
        captured = self.assert_accepted(f)
        for data in (f.original, f.motion):
            for key in (limb_ik.OWNER_KEY, skirt_rig.OWNER_KEY):
                with self.subTest(data=data.name, owner=key):
                    bone = data.bones[f.translated[0]]
                    self.assertNotIn(key, bone)
                    bone[key] = 'generated-owner'
                    try:
                        self.assert_rejected(f, captured, 'unapproved connected-bone flag')
                    finally:
                        del bone[key]
                    self.assert_accepted(f, captured=captured)

    def test_unrecorded_motion_copy_or_missing_native_reference_is_rejected(self):
        f = fixture()
        captured = self.assert_accepted(f)
        replacement = f.motion.copy()
        replacement.name = 'Unrecorded Hair Motion Copy'
        f.rig.data = replacement
        self.assert_rejected(f, captured, 'saved artist or owned motion-copy Rest changed')
        f.rig.data = f.motion
        self.assert_accepted(f, captured=captured)
        encoded = json.loads(f.rig[target.SESSION])
        key = encoded['capture']['targets'][0]['data']['$id']
        saved = f.rig[target.REFS][key]
        del f.rig[target.REFS][key]
        try:
            self.assert_rejected(f, captured, 'recovery data block was removed')
        finally:
            f.rig[target.REFS][key] = saved
        self.assert_accepted(f, captured=captured)

    def test_model_library_snapshot_reopen_retains_authored_data_and_native_source_links(self):
        global _PRIVATE_SNAPSHOT
        f = fixture()
        captured = self.assert_accepted(f)
        source_name, rig_name = f.source.name, f.rig.name
        original_name, motion_name = f.original.name, f.motion.name
        raw = {key: f.source[key] for key in
               (groups.GROUPS_KEY, hair.RECORD_KEY, registry.REGISTRY_KEY, profiles.PROFILE_KEY)}
        session_raw = f.rig[target.SESSION]
        with tempfile.TemporaryDirectory(prefix='cd_hair_worklist_rest_') as directory:
            path = str(Path(directory) / 'model_snapshot.blend')
            before = proof_state(f)
            bpy.data.libraries.write(path, exporter._model_snapshot_datablocks([f.source, f.rig], []),
                                     path_remap='ABSOLUTE', fake_user=False, compress=True)
            self.assertEqual(proof_state(f), before, 'Snapshot write mutated source scene data')
            _PRIVATE_SNAPSHOT = path
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path, load_ui=False), {'FINISHED'})
            source, rig = bpy.data.objects[source_name], bpy.data.objects[rig_name]
            reloaded = target._load(rig)
            original = reloaded['capture']['targets'][0]['data']
            motion = reloaded['motion_data']
            self.assertIs(original, bpy.data.armatures[original_name])
            self.assertIs(motion, bpy.data.armatures[motion_name])
            self.assertIsNot(original, motion)
            self.assertIs(rig.data, motion)
            self.assertEqual(rig[target.SESSION], session_raw)
            for key, value in raw.items():
                self.assertEqual(source[key], value)
            self.assertIs(source[hair.RIG_KEY], rig)
            for data in (original, motion):
                for name in f.hair_names:
                    self.assertIs(data.bones[name][hair.SOURCE_KEY], source)
            restored = SimpleNamespace(source=source, rig=rig, original=original, motion=motion,
                                       registry=captured['registry'], raw_source=properties(source))
            activate(source)
            self.assert_accepted(restored, captured=captured)
            view = target.control_rest_view(rig)
            self.assertIs(view.original_data, original)
            for name in f.translated:
                self.assertTrue(original.bones[name].use_connect)
                self.assertFalse(motion.bones[name].use_connect)


if __name__ == '__main__':
    require_disposable_process()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(HairWorklistRestTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('HAIR_WORKLIST_REST_BLENDER_OK', result.testsRun, flush=True)
