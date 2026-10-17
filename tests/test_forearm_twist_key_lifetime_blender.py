"""Native lifetime regression for deleted KeyBlock caches; no artist files.

Run in a disposable Blender --background --factory-startup process.
"""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import forearm_twist as runtime


def fixture(name, output='CD Forearm Twist.L'):
    mesh = bpy.data.meshes.new(name + ' Mesh')
    mesh.from_pydata([(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name=output)
    return obj, key, {'key': 'CD Forearm Twist.L'}


class LiveKeyView:
    def __init__(self, keys):
        self.keys = keys
        self.session_uid = keys.session_uid
        self.key_blocks = self

    def as_pointer(self):
        return self.keys.as_pointer()

    def get(self, name):
        return self.keys.key_blocks.get(name)

    def __iter__(self):
        raise AssertionError('An ordinary cache hit must not scan all Shape Keys.')


class KeyLifetimeTests(unittest.TestCase):
    def test_cache_stores_only_primitives_and_normal_hit_uses_name_index(self):
        obj, key, record = fixture('Indexed live key')
        self.assertEqual(runtime._managed_key(obj, 'L', record), key)
        token = runtime._KEY_REFERENCES[(obj.as_pointer(), 'L')]
        self.assertIs(type(token), tuple)
        self.assertTrue(all(type(value) is int for value in token))
        proxy = SimpleNamespace(as_pointer=obj.as_pointer, session_uid=obj.session_uid,
                                data=SimpleNamespace(shape_keys=LiveKeyView(obj.data.shape_keys)))
        self.assertEqual(runtime._managed_key(proxy, 'L', record), key)

    def test_deleted_mesh_cache_slot_cannot_claim_new_objects_key(self):
        old, _old_key, record = fixture('Deleted owner')
        runtime._managed_key(old, 'L', record)
        old_pointer = old.as_pointer()
        token = runtime._KEY_REFERENCES[(old_pointer, 'L')]
        mesh = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        # The old primitive cache intentionally survives real native deletion.
        # Force its dictionary slot onto the new pointer to cover allocator
        # address reuse deterministically, without dereferencing the freed RNA.
        obj, foreign, _ = fixture('New owner', 'Author key')
        runtime._KEY_REFERENCES[(obj.as_pointer(), 'L')] = token
        self.assertIsNone(runtime._managed_key(obj, 'L', record, required=False))
        self.assertEqual(foreign.name, 'Author key')
        self.assertNotIn((obj.as_pointer(), 'L'), runtime._KEY_REFERENCES)

    def test_replaced_native_key_datablock_invalidates_the_old_identity(self):
        obj, _old_key, record = fixture('Replaced Key data')
        runtime._managed_key(obj, 'L', record)
        token = runtime._KEY_REFERENCES[(obj.as_pointer(), 'L')]
        old_uid = obj.data.shape_keys.session_uid
        obj.shape_key_clear()
        obj.shape_key_add(name='Basis')
        foreign = obj.shape_key_add(name='A different artist key')
        self.assertNotEqual(obj.data.shape_keys.session_uid, old_uid)
        runtime._KEY_REFERENCES[(obj.as_pointer(), 'L')] = token
        self.assertIsNone(runtime._managed_key(obj, 'L', record, required=False))
        self.assertEqual(foreign.name, 'A different artist key')

    def test_legacy_poisoned_rna_cache_is_discarded_without_reading_it(self):
        class RemovedRNA:
            def __getattribute__(self, name):
                raise AssertionError('A removed RNA cache was dereferenced: ' + name)
        obj, key, record = fixture('Legacy stale cache')
        runtime._KEY_REFERENCES[(obj.as_pointer(), 'L')] = RemovedRNA()
        self.assertEqual(runtime._managed_key(obj, 'L', record), key)
        self.assertIs(type(runtime._KEY_REFERENCES[(obj.as_pointer(), 'L')]), tuple)

    def test_live_renames_repair_but_foreign_name_collisions_still_refuse(self):
        obj, key, record = fixture('Renamed live owner')
        runtime._managed_key(obj, 'L', record)
        owned_pointer = key.as_pointer()
        key.name = 'Artist renamed the owned output'
        self.assertEqual(runtime._managed_key(obj, 'L', record, repair_name=False), key)
        self.assertEqual(key.name, 'Artist renamed the owned output')
        self.assertEqual(runtime._managed_key(obj, 'L', record), key)
        self.assertEqual(key.name, record['key'])
        key.name = 'Owned output before collision'
        foreign = obj.shape_key_add(name=record['key'])
        foreign_pointer = foreign.as_pointer()
        with self.assertRaisesRegex(runtime.ForearmTwistError, 'Another Shape Key'):
            runtime._managed_key(obj, 'L', record)
        self.assertEqual(key.as_pointer(), owned_pointer)
        self.assertEqual(key.name, 'Owned output before collision')
        self.assertEqual(foreign.as_pointer(), foreign_pointer)
        self.assertEqual(foreign.name, record['key'])

    def test_deleted_key_with_live_key_datablock_is_not_dereferenced(self):
        obj, key, record = fixture('Deleted output only')
        runtime._managed_key(obj, 'L', record)
        obj.shape_key_remove(key)
        self.assertIsNone(runtime._managed_key(obj, 'L', record, required=False))
        self.assertEqual(tuple(obj.data.shape_keys.key_blocks.keys()), ('Basis',))

    def test_rest_resync_rollback_resolves_tokens_to_live_owned_outputs(self):
        from character_designer import body_rest_resync as resync
        obj, key, record = fixture('Corrective rollback consumer')
        rig = bpy.data.objects.new('Rollback armature', bpy.data.armatures.new('Rollback armature data'))
        bpy.context.scene.collection.objects.link(rig)
        obj.modifiers.new('Armature', 'ARMATURE').object = rig
        raw = json.dumps({'L': record})
        obj[runtime.RECORD_KEY] = raw
        key.value, key.mute = .3, True
        coordinates = tuple(tuple(point.co) for point in key.data)
        saved = resync._corrective_checkpoint(rig)
        self.assertEqual(len(saved), 1)
        self.assertNotIn('key', saved[0]['outputs'][0], 'Checkpoint outputs must not retain KeyBlock RNA.')
        self.assertTrue(all(type(token) is tuple for token in saved[0]['references'].values()))
        key.data[0].co.x += .25
        key.value, key.mute = .7, False
        right = obj.shape_key_add(name='CD Forearm Twist.R')
        obj[runtime.RECORD_KEY] = json.dumps({'L': record, 'R': {'key': right.name}})
        runtime._managed_key(obj, 'R', {'key': right.name})
        resync.restore_correctives({'correctives': saved})
        self.assertEqual(obj[runtime.RECORD_KEY], raw)
        self.assertIsNone(obj.data.shape_keys.key_blocks.get('CD Forearm Twist.R'))
        restored = obj.data.shape_keys.key_blocks[record['key']]
        self.assertEqual(tuple(tuple(point.co) for point in restored.data), coordinates)
        self.assertAlmostEqual(restored.value, .3)
        self.assertTrue(restored.mute)
        self.assertEqual({identity: token for identity, token in runtime._KEY_REFERENCES.items()
                          if identity[0] == obj.as_pointer()}, saved[0]['references'])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(KeyLifetimeTests))
    if not result.wasSuccessful():
        raise RuntimeError('Forearm native Key lifetime regression failed.')
