"""Reversible connection removal, actual skin deformation, persistence and undo."""

import sys
import tempfile
import unittest
from pathlib import Path

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
import character_designer
from character_designer import character_setup, quick_bind as service
from character_designer.selected_bone_weights import _capture_vertex_groups


def fixture():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    rig = bpy.data.objects.new('Rig', bpy.data.armatures.new('Rig'))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = rig.data.edit_bones.new('Arm')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 2)
    bpy.ops.object.mode_set(mode='OBJECT')
    meshes = []
    for name, z in [('Body', 0), ('Stocking', .1)]:
        data = bpy.data.meshes.new(name)
        data.from_pydata([(.1, .1, z), (.9, .1, z), (.1, .9, z)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.vertex_groups.new(name='Arm').add([0, 1, 2], 1, 'REPLACE')
        obj.modifiers.new('Artist Armature', 'ARMATURE').object = rig
        meshes.append(obj)
    body, target = meshes
    state = character_setup.settings(bpy.context)
    state.rig, state.body = rig, body
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.context.view_layer.update()
    return target, body, rig


def evaluated_vertices(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def artist_state(obj):
    keys = obj.data.shape_keys
    return (
        obj.data.as_pointer(),
        tuple(tuple(v.co) for v in obj.data.vertices),
        tuple(tuple(p.vertices) for p in obj.data.polygons),
        _capture_vertex_groups(obj),
        tuple((key.name, key.value, tuple(tuple(v.co) for v in key.data))
              for key in keys.key_blocks) if keys else (),
        obj.animation_data.action.as_pointer() if obj.animation_data else None,
        tuple((mod.name, mod.type, mod.persistent_uid, mod.as_pointer()) for mod in obj.modifiers),
        obj.get('artist_note'),
    )


class RemoveBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def assert_positions(self, actual, expected, epsilon=1e-5):
        self.assertEqual(len(actual), len(expected))
        self.assertLess(max((a-b).length for a, b in zip(actual, expected)), epsilon)

    def assert_matrix(self, actual, expected):
        self.assertLess(max(abs(a-b) for ra, rb in zip(actual, expected)
                            for a, b in zip(ra, rb)), 1e-5)

    def test_existing_binding_deforms_disconnects_restores_without_data_loss(self):
        target, body, rig = fixture()
        modifier = target.modifiers[0]
        mirror = target.modifiers.new('Artist Mirror', 'MIRROR')
        mirror.show_viewport = False
        target.modifiers.move(1, 0)
        smooth = target.modifiers.new('Artist Subdivision', 'SUBSURF')
        smooth.levels = 0
        target.shape_key_add(name='Basis')
        key = target.shape_key_add(name='ArtistExpression')
        key.data[1].co.z += .2
        key.value = .5
        target.vertex_groups.new(name='ArtistMask').add([0, 2], .3, 'REPLACE')
        target['artist_note'] = 'Keep this'
        target.keyframe_insert(data_path='location', frame=1)
        self.assertFalse(service.has_binding_backup(target))
        rest = evaluated_vertices(target)
        rig.pose.bones['Arm'].location.x = 1.25
        posed = evaluated_vertices(target)
        self.assertGreater((posed[0]-rest[0]).length, 1)
        before = artist_state(target)
        for _ in range(2):
            service.remove_binding(bpy.context, target, rig)
            self.assertIsNone(modifier.object)
            self.assertIsNone(service.binding_modifier(target, rig))
            self.assertTrue(service.has_removed_binding(target))
            self.assertEqual(artist_state(target), before)
            self.assert_positions(evaluated_vertices(target), rest)
            service.restore_removed_binding(bpy.context, target)
            self.assertEqual(modifier.object, rig)
            self.assertFalse(service.has_removed_binding(target))
            self.assertEqual(artist_state(target), before)
            self.assert_positions(evaluated_vertices(target), posed)
        self.assertFalse(service.has_binding_backup(target))

    def test_topology_changed_since_weight_backup_connection_still_restores(self):
        target, body, rig = fixture()
        service.bind_weights(bpy.context, target, rig, body=body)
        saved = target[service.BACKUP_KEY]
        target.data.vertices.add(1)
        target.data.vertices[-1].co = (1, 1, 1)
        target.vertex_groups['Arm'].add([3], .7, 'REPLACE')
        target.data.update()
        weights = _capture_vertex_groups(target)
        service.remove_binding(bpy.context, target, rig)
        service.restore_removed_binding(bpy.context, target)
        self.assertEqual(target[service.BACKUP_KEY], saved)
        self.assertEqual(_capture_vertex_groups(target), weights)
        with self.assertRaisesRegex(service.QuickBindError, 'topology'):
            service.restore_binding(bpy.context, target)
        self.assertEqual(target.modifiers[0].object, rig)

    def test_quick_bind_first_baseline_survives_and_is_restorable(self):
        target, body, rig = fixture()
        target.modifiers.remove(target.modifiers[0])
        target.vertex_groups['Arm'].add([0, 1, 2], .4, 'REPLACE')
        before = _capture_vertex_groups(target)
        service.bind_weights(bpy.context, target, rig, body=body)
        saved = target[service.BACKUP_KEY]
        service.remove_binding(bpy.context, target, rig)
        with self.assertRaises(service.QuickBindError):
            service.restore_binding(bpy.context, target)
        self.assertEqual(target[service.BACKUP_KEY], saved)
        service.restore_removed_binding(bpy.context, target)
        self.assertEqual(target[service.BACKUP_KEY], saved)
        service.restore_binding(bpy.context, target)
        self.assertFalse(service.has_binding_backup(target))
        self.assertEqual(_capture_vertex_groups(target), before)
        self.assertFalse(target.modifiers)

    def test_object_and_bone_parent_keep_world_and_reconnect(self):
        for parent_type in ('OBJECT', 'BONE'):
            target, body, rig = fixture()
            rig.location = (2, -3, 1)
            rig.rotation_euler = (.2, .1, .5)
            bpy.context.view_layer.update()
            target.parent = rig
            target.parent_type = parent_type
            target.parent_bone = 'Arm' if parent_type == 'BONE' else ''
            target.matrix_parent_inverse = rig.matrix_world.inverted()
            target.matrix_world = Matrix.Translation((1, 2, 3))
            bpy.context.view_layer.update()
            world = target.matrix_world.copy()
            inverse = target.matrix_parent_inverse.copy()
            service.remove_binding(bpy.context, target, rig)
            bpy.context.view_layer.update()
            self.assertIsNone(target.parent)
            self.assert_matrix(target.matrix_world, world)
            service.restore_removed_binding(bpy.context, target)
            bpy.context.view_layer.update()
            self.assertEqual(target.parent, rig)
            self.assertEqual(target.parent_type, parent_type)
            self.assert_matrix(target.matrix_world, world)
            self.assert_matrix(target.matrix_parent_inverse, inverse)

    def test_save_reopen_rename_and_detached_weight_edits(self):
        target, body, rig = fixture()
        target.modifiers[0].show_render = False
        uid = target.modifiers[0].persistent_uid
        service.remove_binding(bpy.context, target, rig)
        target.modifiers[0].name = 'Renamed while detached'
        rig.name = 'RenamedRig'
        target.vertex_groups['Arm'].add([1], .43, 'REPLACE')
        with tempfile.TemporaryDirectory(prefix='cdesigner-unbind-') as directory:
            filepath = str(Path(directory) / 'unbind.blend')
            bpy.ops.wm.save_as_mainfile(filepath=filepath, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=filepath)
            target, rig = bpy.data.objects['Stocking'], bpy.data.objects['RenamedRig']
            self.assertTrue(service.has_removed_binding(target))
            service.restore_removed_binding(bpy.context, target)
            modifier = target.modifiers[0]
            self.assertEqual(modifier.object, rig)
            self.assertEqual(modifier.persistent_uid, uid)
            self.assertEqual(modifier.name, 'Renamed while detached')
            self.assertFalse(modifier.show_render)
            self.assertAlmostEqual(target.vertex_groups['Arm'].weight(1), .43, places=6)

    def test_conflicts_and_damaged_records_leave_scene_unchanged(self):
        target, body, rig = fixture()
        extra = target.modifiers.new('Another Armature', 'ARMATURE')
        extra.object = rig
        before = artist_state(target)
        with self.assertRaisesRegex(service.QuickBindError, 'conflicting'):
            service.remove_binding(bpy.context, target, rig)
        self.assertEqual(artist_state(target), before)
        self.assertFalse(service.has_removed_binding(target))
        target.modifiers.remove(extra)
        service.remove_binding(bpy.context, target, rig)
        saved = target[service.REMOVED_KEY]
        target.modifiers[0].object = rig
        with self.assertRaisesRegex(service.QuickBindError, 'reassigned'):
            service.restore_removed_binding(bpy.context, target)
        self.assertEqual(target[service.REMOVED_KEY], saved)
        self.assertEqual(target.modifiers[0].object, rig)
        target.modifiers[0].object = None
        target[service.REMOVED_KEY] = '{}'
        with self.assertRaisesRegex(service.QuickBindError, 'damaged'):
            service.restore_removed_binding(bpy.context, target)
        self.assertEqual(target[service.REMOVED_KEY], '{}')
        self.assertIsNone(target.modifiers[0].object)

    def test_parent_conflict_and_cycle_refused_without_partial_reconnect(self):
        target, body, rig = fixture()
        target.parent = rig
        service.remove_binding(bpy.context, target, rig)
        saved = target[service.REMOVED_KEY]
        target.parent = body
        with self.assertRaisesRegex(service.QuickBindError, 'new parent'):
            service.restore_removed_binding(bpy.context, target)
        self.assertIsNone(target.modifiers[0].object)
        self.assertEqual(target.parent, body)
        self.assertEqual(target[service.REMOVED_KEY], saved)
        target.parent = None
        rig.parent = target
        with self.assertRaisesRegex(service.QuickBindError, 'cycle'):
            service.restore_removed_binding(bpy.context, target)
        self.assertIsNone(target.modifiers[0].object)
        self.assertIsNone(target.parent)
        self.assertEqual(target[service.REMOVED_KEY], saved)

    def test_operators_real_undo_redo_and_reconnect(self):
        target, body, rig = fixture()
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message='Before remove binding')
        self.assertEqual(bpy.ops.character_designer.remove_quick_binding(), {'FINISHED'})
        bpy.ops.ed.undo_push(message='After remove binding')
        self.assertIsNone(target.modifiers[0].object)
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        target, rig = bpy.data.objects['Stocking'], bpy.data.objects['Rig']
        self.assertEqual(target.modifiers[0].object, rig)
        self.assertFalse(service.has_removed_binding(target))
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        target = bpy.data.objects['Stocking']
        self.assertIsNone(target.modifiers[0].object)
        self.assertTrue(service.has_removed_binding(target))
        self.assertEqual(bpy.ops.character_designer.restore_removed_quick_binding(), {'FINISHED'})
        self.assertEqual(target.modifiers[0].object, bpy.data.objects['Rig'])
        self.assertFalse(service.has_removed_binding(target))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RemoveBindingTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    print('QUICK_BIND_REMOVE_OK')
