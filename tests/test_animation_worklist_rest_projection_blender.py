"""Fresh native IDs and Rest remain mandatory after the text-plan cache warms.

Run only with Blender --background --factory-startup --python this_file.
This script constructs small disposable native fixtures. It never opens or
saves an artist file, registers add-on handlers, or starts playback/workers.
"""

import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import animation_worklist_target as target
from character_designer import limb_ik, unity_animation as ua


def require_disposable_process():
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError('Use an isolated background factory process without an artist file.')


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


def custom_properties(owner):
    return tuple((key, token(owner[key])) for key in sorted(owner.keys()))


def pose_state(obj):
    if obj.type != 'ARMATURE':
        return ()
    if obj.pose is None:
        return None  # Newly linked native armatures can have no evaluated pose.
    return tuple((pb.name, tuple(tuple(row) for row in pb.matrix_basis),
                  custom_properties(pb)) for pb in obj.pose.bones)


def native_state():
    """Read content, native identities and context; exclude Python-only caches."""
    context = bpy.context
    return {
        'file': (bpy.data.filepath, bpy.data.is_dirty),
        'scene': (context.scene.as_pointer(), context.scene.frame_current,
                  context.scene.frame_subframe, custom_properties(context.scene)),
        'selection': (context.view_layer.objects.active.as_pointer()
                      if context.view_layer.objects.active else None,
                      tuple(sorted(obj.as_pointer() for obj in context.selected_objects)),
                      context.mode),
        'objects': tuple((obj.name, obj.as_pointer(), obj.type,
                          obj.data.as_pointer() if obj.data else None,
                          tuple(tuple(row) for row in obj.matrix_world),
                          custom_properties(obj),
                          obj.animation_data.action.as_pointer()
                          if obj.animation_data and obj.animation_data.action else None,
                          pose_state(obj))
                         for obj in sorted(bpy.data.objects, key=lambda item: item.name)),
        'armatures': tuple((data.name, data.as_pointer(), data.users,
                            token(ua._rest_state(data)), custom_properties(data),
                            tuple((bone.name, custom_properties(bone), bone.color.palette,
                                   bone.hide, bone.hide_select) for bone in data.bones),
                            tuple((group.name, group.is_visible, group.is_solo,
                                   tuple(sorted(bone.name for bone in group.bones)))
                                  for group in data.collections_all))
                           for data in sorted(bpy.data.armatures, key=lambda item: item.name)),
        'actions': tuple((action.name, action.as_pointer(), action.users,
                          custom_properties(action),
                          tuple((slot.handle, slot.identifier) for slot in action.slots))
                         for action in sorted(bpy.data.actions, key=lambda item: item.name)),
        'scenes': tuple((scene.name, scene.as_pointer(),
                         tuple(sorted(obj.as_pointer() for obj in scene.objects)))
                        for scene in sorted(bpy.data.scenes, key=lambda item: item.name)),
    }


class NativeRestProjectionTests(unittest.TestCase):
    def setUp(self):
        require_disposable_process()
        bpy.ops.wm.read_factory_settings(use_empty=True)
        target._compile_rest_record.cache_clear()
        self.original = bpy.data.armatures.new('Projection Original')
        self.rig = bpy.data.objects.new('Projection Artist Rig', self.original)
        bpy.context.scene.collection.objects.link(self.rig)
        bpy.context.view_layer.objects.active = self.rig
        self.rig.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        upper = self.original.edit_bones.new('upperarm.L')
        upper.head, upper.tail = (0, 0, 0), (0, 1, 0)
        forearm = self.original.edit_bones.new('forearm.L')
        forearm.head, forearm.tail = (0, 1, 0), (0, 2, 0)
        forearm.parent, forearm.use_connect = upper, True
        bpy.ops.object.mode_set(mode='OBJECT')
        self.saved_action = bpy.data.actions.new('Projection Recovery Action')
        slot = self.saved_action.slots.new(id_type='OBJECT', name=self.rig.name)
        self.rig.animation_data_create()
        self.rig.animation_data.action = self.saved_action
        self.rig.animation_data.action_slot = slot
        capture = target._capture(bpy.context, self.rig)
        self.motion = self.original.copy()
        self.motion.name = 'Projection Owned Motion'
        self.rig.data = self.motion
        bpy.ops.object.mode_set(mode='EDIT')
        self.motion.edit_bones['forearm.L'].use_connect = False
        bpy.ops.object.mode_set(mode='OBJECT')
        target._store(self.rig, {
            'version': 1, 'rig': self.rig, 'scene': bpy.context.scene,
            'view_layer': bpy.context.view_layer.name,
            'workspace_identity': 'native-projection-fixture', 'capture': capture,
            'packet_path': 'unused-native-fixture-packet.json', 'packet_sha256': 'f' * 64,
            'motion_data': self.motion, 'motion_rest': ua._rest_state(self.motion),
            'mapped_names': ['forearm.L'],
        })

    def encoded(self):
        return json.loads(self.rig[target.SESSION])

    def write_encoded(self, encoded, *, rehash=False):
        if rehash:
            encoded['capture_sha256'] = hashlib.sha256(json.dumps(encoded['capture'],
                ensure_ascii=False, allow_nan=False, sort_keys=True,
                separators=(',', ':')).encode('utf-8')).hexdigest()
        self.rig[target.SESSION] = json.dumps(encoded, ensure_ascii=False)

    def lookup(self, *, legacy=False, validation=False):
        reader = (lambda: target.validation_rest_view(self.rig, ('forearm.L',))) if validation else (
            lambda: target.control_rest_view(self.rig))
        before = native_state()
        try:
            if legacy:
                with patch.object(target, '_load_rest_record',
                                  side_effect=lambda rig, **_kw: target._load(rig)):
                    view = reader()
            else:
                view = reader()
            result = ('ok', None if view is None else
                      (view.original_data.as_pointer(), view.motion_data.as_pointer(), view.names))
        except Exception as exc:
            result = ('error', type(exc), str(exc))
        self.assertEqual(native_state(), before, 'Rest lookup wrote native scene/data content.')
        return result

    def assert_parity(self, *, error=None, validation=False):
        actual = self.lookup(validation=validation)
        previous = self.lookup(legacy=True, validation=validation)
        self.assertEqual(actual, previous)
        if error:
            self.assertEqual(actual[0], 'error')
            self.assertIs(actual[1], ValueError)
            self.assertIn(error, actual[2])
        else:
            self.assertEqual(actual[0], 'ok')
        return actual

    def warm(self):
        self.assert_parity()

    def edit_rest(self, data):
        helper = bpy.data.objects.new('Projection Native Edit Helper', data)
        bpy.context.scene.collection.objects.link(helper)
        self.rig.select_set(False)
        bpy.context.view_layer.objects.active = helper
        helper.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        data.edit_bones['forearm.L'].tail.x += .025
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(helper, do_unlink=True)
        bpy.context.view_layer.objects.active = self.rig
        self.rig.select_set(True)

    def test_exact_raw_plan_warms_with_fresh_native_proof_and_no_writes(self):
        self.warm()
        compiled = target._compile_rest_record.cache_info()
        self.assert_parity()
        after = target._compile_rest_record.cache_info()
        self.assertEqual(after.misses, compiled.misses)
        self.assertGreater(after.hits, compiled.hits)
        self.assertEqual(after.currsize, 1)

    def test_warm_removed_native_action_outside_projection_refuses(self):
        self.warm()
        bpy.data.actions.remove(self.saved_action, do_unlink=True)
        self.assert_parity(error='removed')

    def test_warm_unused_id_replacement_keeps_complete_loader_semantics(self):
        self.warm()
        key = self.encoded()['capture']['targets'][0]['action']['$id']
        replacement = bpy.data.actions.new('Projection Replacement Recovery Action')
        self.rig[target.REFS][key] = replacement
        # A present replacement in an unused field has always been accepted by
        # _load; the optimization must not invent a new ID-authenticity rule.
        self.assert_parity()
        del self.rig[target.REFS][key]
        self.assert_parity(error='removed')

    def test_warm_current_native_original_reference_is_reacquired(self):
        self.warm()
        replacement = self.original.copy()
        replacement.name = 'Projection Fresh Original'
        key = self.encoded()['capture']['targets'][0]['data']['$id']
        self.rig[target.REFS][key] = replacement
        result = self.assert_parity()
        self.assertEqual(result[1][0], replacement.as_pointer())
        view = target.control_rest_view(self.rig)
        self.assertEqual(view.source_bone(self.motion.bones['forearm.L']).as_pointer(),
                         replacement.bones['forearm.L'].as_pointer())

    def test_warm_replaced_native_record_rig_refuses(self):
        self.warm()
        foreign = bpy.data.objects.new('Projection Foreign Rig', self.original)
        bpy.context.scene.collection.objects.link(foreign)
        self.rig[target.REFS][self.encoded()['rig']['$id']] = foreign
        self.assert_parity(error='incomplete')

    def test_warm_replaced_native_first_artist_refuses(self):
        self.warm()
        foreign = bpy.data.objects.new('Projection Foreign Artist', self.original)
        bpy.context.scene.collection.objects.link(foreign)
        key = self.encoded()['capture']['targets'][0]['target']['$id']
        self.rig[target.REFS][key] = foreign
        self.assert_parity(error='another rig')

    def test_warm_native_original_edit_bones_refuses(self):
        self.warm()
        self.edit_rest(self.original)
        self.assert_parity(error='owned motion-copy Rest changed')

    def test_warm_native_motion_edit_bones_refuses(self):
        self.warm()
        self.edit_rest(self.motion)
        self.assert_parity(error='owned motion-copy Rest changed')

    def test_warm_native_ownership_change_refuses(self):
        self.warm()
        self.motion.bones['forearm.L'][limb_ik.OWNER_KEY] = 'projection-unapproved-owner'
        self.assert_parity(error='unapproved connected-bone flag')

    def test_warm_changed_session_hash_refuses(self):
        self.warm()
        encoded = self.encoded()
        encoded['capture']['targets'][0]['rest']['forearm.L']['tail'][0] += .1
        self.write_encoded(encoded)
        self.assert_parity(error='changed or was truncated')

    def test_warm_changed_saved_rest_with_recomputed_hash_still_refuses(self):
        self.warm()
        encoded = self.encoded()
        encoded['capture']['targets'][0]['rest']['forearm.L']['tail'][0] += .1
        self.write_encoded(encoded, rehash=True)
        self.assert_parity(error='owned motion-copy Rest changed')

    def test_warm_changed_native_session_text_compiles_new_plan(self):
        self.warm()
        before = target._compile_rest_record.cache_info()
        encoded = self.encoded()
        encoded['packet_path'] = 'another-unused-native-packet.json'
        self.write_encoded(encoded)
        self.assert_parity()
        after = target._compile_rest_record.cache_info()
        self.assertEqual(after.misses, before.misses + 1)
        self.assertEqual(after.currsize, 1)

    def test_warm_malformed_or_foreign_saved_mapping_refuses(self):
        self.warm()
        raw = self.rig[target.SESSION]
        for names in (None, [], ['forearm.L', 'forearm.L'], ['missing native bone']):
            with self.subTest(names=names):
                encoded = json.loads(raw)
                encoded['mapped_names'] = names
                self.write_encoded(encoded)
                self.assert_parity(error='mapped')

    def test_warm_schema_change_uses_full_loader_failure(self):
        self.warm()
        encoded = self.encoded()
        encoded['extra'] = 'invalid top-level schema'
        self.write_encoded(encoded)
        self.assert_parity(error='incomplete')

    def test_native_missing_id_precedes_later_nonfinite_failure_cold_and_warm(self):
        raw = self.rig[target.SESSION]
        scene_key = self.encoded()['scene']['$id']
        for warm in (False, True):
            with self.subTest(warm=warm):
                self.rig[target.SESSION] = raw
                self.rig[target.REFS][scene_key] = bpy.context.scene
                target._compile_rest_record.cache_clear()
                if warm:
                    self.warm()
                del self.rig[target.REFS][scene_key]
                encoded = json.loads(raw)
                encoded['packet_path'] = float('nan')
                self.write_encoded(encoded)
                self.assert_parity(error='removed')

    def test_validation_view_preserves_its_external_mapping_boundary(self):
        self.warm()
        encoded = self.encoded()
        encoded['mapped_names'] = None
        self.write_encoded(encoded)
        self.assert_parity(validation=True)
        self.assert_parity(error='mapped')


if __name__ == '__main__':
    require_disposable_process()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NativeRestProjectionTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
