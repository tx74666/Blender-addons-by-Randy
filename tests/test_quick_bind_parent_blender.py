"""Real Quick Bind parenting: transforms, persistence, failure, and undo.

Run with Blender --background --factory-startup --python-exit-code 1.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
import character_designer
from character_designer import character_setup, quick_bind as service
from character_designer import quick_bind_parenting as parenting
from character_designer.selected_bone_weights import _capture_vertex_groups

PARENT_KEYS = (parenting.PARENT_KEY, parenting.PARENT_RIG_KEY, parenting.OLD_PARENT_KEY)


def fixture(*, sphere=False):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    rig = bpy.data.objects.new('Rig', bpy.data.armatures.new('Rig'))
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = rig.data.edit_bones.new('Deform')
    bone.head, bone.tail = (0, 0, -.5), (0, 0, .5)
    bpy.ops.object.mode_set(mode='OBJECT')
    meshes = []
    for name, z in (('Body', 0), ('Clothes', .1)):
        data = bpy.data.meshes.new(name)
        data.from_pydata([(0, 0, z), (2, 0, z), (0, 2, z)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        obj.vertex_groups.new(name='Deform').add((0, 1, 2), 1 if name == 'Body' else .4, 'REPLACE')
        meshes.append(obj)
    body, target = meshes
    body.modifiers.new('Body Armature', 'ARMATURE').object = rig
    if sphere:
        bpy.data.objects.remove(target, do_unlink=True)
        bpy.ops.mesh.primitive_uv_sphere_add(segments=12, ring_count=8, radius=1)
        target = bpy.context.object
        target.name = 'Clothes'
    target.vertex_groups.new(name='ArtistMask').add((0,), .27, 'REPLACE')
    old_parent = bpy.data.objects.new('Artist Parent', None)
    bpy.context.scene.collection.objects.link(old_parent)
    state = character_setup.settings(bpy.context)
    state.rig, state.body = rig, body
    for obj in tuple(bpy.context.selected_objects):
        obj.select_set(False)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.context.view_layer.update()
    return target, body, rig, old_parent


def matrix_state(matrix):
    return tuple(tuple(row) for row in matrix)


def channels(obj):
    return tuple(tuple(getattr(obj, name)) for name in
                 ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle',
                  'scale', 'delta_location', 'delta_rotation_euler',
                  'delta_rotation_quaternion', 'delta_scale')) + (obj.rotation_mode,)


def metadata(obj):
    result = {}
    for key in (*PARENT_KEYS, service.BACKUP_KEY, service.RIG_KEY):
        if key in obj:
            value = obj[key]
            result[key] = ('OBJECT', value.name) if isinstance(value, bpy.types.Object) else value
    return result


def full_state(obj):
    return (
        obj.parent.name if obj.parent else None, obj.parent_type, obj.parent_bone,
        matrix_state(obj.matrix_parent_inverse), matrix_state(obj.matrix_world), channels(obj),
        _capture_vertex_groups(obj), metadata(obj), obj.vertex_groups.active_index,
        tuple((mod.name, mod.type, mod.object.name if mod.object else None,
               mod.use_vertex_groups, mod.use_bone_envelopes)
              for mod in obj.modifiers if mod.type == 'ARMATURE'),
    )


def evaluated_vertices(obj):
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return tuple(evaluated.matrix_world @ vertex.co for vertex in evaluated.data.vertices)


class QuickBindParentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def assert_matrix(self, actual, expected, epsilon=5e-5):
        self.assertLess(max(abs(a - b) for ra, rb in zip(actual, expected)
                            for a, b in zip(ra, rb)), epsilon)

    def assert_positions(self, actual, expected, epsilon=5e-5):
        self.assertEqual(len(actual), len(expected))
        self.assertLess(max((a - b).length for a, b in zip(actual, expected)), epsilon)

    def test_transfer_parents_rotated_scaled_rig_preserving_world_and_delta_channels(self):
        target, body, rig, old_parent = fixture()
        rig.location = (3, -2, 1)
        rig.rotation_euler = (.37, -.21, .8)
        rig.scale = (-1.4, .7, 2.1)
        old_parent.location = (-1, 2, .5)
        old_parent.rotation_euler = (-.2, .41, -.7)
        old_parent.scale = (1.3, .8, 1.7)
        target.parent = old_parent
        target.location = (.2, .1, .3)
        target.rotation_euler = (.4, -.13, .25)
        target.scale = (.9, 1.1, .7)
        target.delta_location = (.07, -.04, .09)
        target.delta_rotation_euler = (.05, .08, -.04)
        target.delta_scale = (1.1, .95, 1.05)
        bpy.context.view_layer.update()
        world, basis = target.matrix_world.copy(), target.matrix_basis.copy()
        channel_values = channels(target)
        source_world = body.matrix_world.copy()
        service.bind_weights(bpy.context, target, rig, body=body)
        bpy.context.view_layer.update()
        self.assertEqual(target.parent, rig)
        self.assertEqual(target.parent_type, 'OBJECT')
        self.assert_matrix(target.matrix_world, world)
        self.assert_matrix(target.matrix_basis, basis)
        self.assertEqual(channels(target), channel_values)
        self.assert_matrix(body.matrix_world, source_world)
        self.assertIn(parenting.PARENT_KEY, target)
        self.assertEqual(target[parenting.OLD_PARENT_KEY], old_parent)

    def test_automatic_weights_parent_and_keep_artist_channels(self):
        target, body, rig, _ = fixture(sphere=True)
        # A common world transform keeps the native heat solve well conditioned.
        transform = Matrix.Translation((1, -2, 3)) @ Matrix.Rotation(.31, 4, 'Z')
        rig.matrix_world = transform
        target.matrix_world = transform
        bpy.context.view_layer.update()
        world, basis, channel_values = target.matrix_world.copy(), target.matrix_basis.copy(), channels(target)
        result = service.bind_weights(bpy.context, target, rig, mode='AUTO')
        bpy.context.view_layer.update()
        self.assertEqual(result['group_count'], 1)
        self.assertEqual(target.parent, rig)
        self.assert_matrix(target.matrix_world, world)
        self.assert_matrix(target.matrix_basis, basis)
        self.assertEqual(channels(target), channel_values)
        self.assertTrue(all(abs(target.vertex_groups['Deform'].weight(vertex.index) - 1) < 1e-6
                            for vertex in target.data.vertices))
        self.assertAlmostEqual(target.vertex_groups['ArtistMask'].weight(0), .27, places=6)

    def test_existing_rig_parent_keeps_artist_inverse_through_rebind_and_restore(self):
        target, body, rig, _ = fixture()
        rig.location = (2, -3, 1)
        rig.rotation_euler = (.3, .2, -.1)
        target.parent = rig
        target.matrix_parent_inverse = Matrix.Translation((.3, -.7, 1.2)) @ Matrix.Rotation(.23, 4, 'Y')
        bpy.context.view_layer.update()
        world, inverse, basis = (target.matrix_world.copy(), target.matrix_parent_inverse.copy(),
                                 target.matrix_basis.copy())
        for _ in range(2):
            service.bind_weights(bpy.context, target, rig, body=body)
            bpy.context.view_layer.update()
            self.assertEqual(target.parent, rig)
            self.assert_matrix(target.matrix_parent_inverse, inverse)
            self.assert_matrix(target.matrix_world, world)
            self.assert_matrix(target.matrix_basis, basis)
        service.restore_binding(bpy.context, target)
        bpy.context.view_layer.update()
        self.assertEqual(target.parent, rig)
        self.assert_matrix(target.matrix_parent_inverse, inverse)
        self.assert_matrix(target.matrix_world, world)

    def test_rig_object_translation_moves_evaluated_mesh_exactly_once(self):
        target, body, rig, _ = fixture()
        rig.location = (2, 1, -.5)
        rig.rotation_euler = (.2, -.3, .4)
        rig.scale = (-1.1, .8, 1.7)
        bpy.context.view_layer.update()
        service.bind_weights(bpy.context, target, rig, body=body)
        before = evaluated_vertices(target)
        delta = Vector((.6, -1.2, .4))
        rig.location += delta
        self.assert_positions(evaluated_vertices(target), tuple(point + delta for point in before))

    def test_attach_partial_failure_rolls_back_weights_parent_and_metadata(self):
        for repeated in (False, True):
            with self.subTest(repeated=repeated):
                target, body, rig, old_parent = fixture()
                target.parent = old_parent
                target.matrix_parent_inverse = Matrix.Translation((.2, .3, .4))
                target.delta_location = (.1, .2, .3)
                bpy.context.view_layer.update()
                if repeated:
                    service.bind_weights(bpy.context, target, rig, body=body)
                    target.vertex_groups['Deform'].add((0,), .73, 'REPLACE')
                    target.modifiers[0].use_vertex_groups = False
                    target.modifiers[0].use_bone_envelopes = True
                before = full_state(target)
                objects_before = set(bpy.data.objects.keys())
                real_attach = parenting.attach

                def fail_after_attach(*args):
                    real_attach(*args)
                    raise RuntimeError('injected parent commit failure')

                with patch.object(parenting, 'attach', side_effect=fail_after_attach):
                    with self.assertRaisesRegex(service.QuickBindError, 'injected parent commit failure'):
                        service.bind_weights(bpy.context, target, rig, body=body)
                bpy.context.view_layer.update()
                self.assertEqual(full_state(target), before)
                self.assertEqual(set(bpy.data.objects.keys()), objects_before)

    def test_previous_parent_restore_partial_failure_retains_complete_binding(self):
        target, body, rig, old_parent = fixture()
        target.parent = old_parent
        bpy.context.view_layer.update()
        service.bind_weights(bpy.context, target, rig, body=body)
        target.vertex_groups['Deform'].add((0,), .73, 'REPLACE')
        bpy.context.view_layer.update()
        before = full_state(target)
        real_restore = parenting.restore

        def fail_after_restore(*args):
            real_restore(*args)
            raise RuntimeError('injected parent restore failure')

        with patch.object(parenting, 'restore', side_effect=fail_after_restore):
            with self.assertRaisesRegex(service.QuickBindError, 'injected parent restore failure'):
                service.restore_binding(bpy.context, target)
        bpy.context.view_layer.update()
        self.assertEqual(full_state(target), before)

    def test_legacy_weight_backup_gains_parent_record_once_and_survives_save_reopen(self):
        target, body, rig, old_parent = fixture()
        target.parent = old_parent
        bpy.context.view_layer.update()
        original_weights = _capture_vertex_groups(target)
        # Simulate the earlier release: it wrote exactly the same weight backup,
        # but never attached the real object or created parenting metadata.
        with patch.object(parenting, 'attach', return_value=None):
            service.bind_weights(bpy.context, target, rig, body=body)
        legacy_raw = target[service.BACKUP_KEY].encode('utf-8')
        self.assertFalse(any(key in target for key in PARENT_KEYS))
        self.assertEqual(target.parent, old_parent)
        service.bind_weights(bpy.context, target, rig, body=body)
        parent_raw = target[parenting.PARENT_KEY]
        for _ in range(2):
            service.bind_weights(bpy.context, target, rig, body=body)
            self.assertEqual(target[service.BACKUP_KEY].encode('utf-8'), legacy_raw)
            self.assertEqual(target[parenting.PARENT_KEY], parent_raw)
        old_parent.name = 'Renamed artist parent'
        with tempfile.TemporaryDirectory(prefix='quick-bind-parent-') as directory:
            path = str(Path(directory) / 'parent.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=path)
            target, rig = bpy.data.objects['Clothes'], bpy.data.objects['Rig']
            old_parent = bpy.data.objects['Renamed artist parent']
            self.assertEqual(target.parent, rig)
            self.assertEqual(target[parenting.OLD_PARENT_KEY], old_parent)
            self.assertEqual(target[service.BACKUP_KEY].encode('utf-8'), legacy_raw)
            self.assertEqual(target[parenting.PARENT_KEY], parent_raw)
            rig.location += Vector((1, -.5, .3))
            old_parent.rotation_euler = (.2, .1, -.3)
            bpy.context.view_layer.update()
            current_world, basis = target.matrix_world.copy(), target.matrix_basis.copy()
            service.restore_binding(bpy.context, target)
            bpy.context.view_layer.update()
            self.assertEqual(target.parent, old_parent)
            self.assert_matrix(target.matrix_world, current_world)
            self.assert_matrix(target.matrix_basis, basis)
            self.assertEqual(_capture_vertex_groups(target), original_weights)
            self.assertFalse(any(key in target for key in PARENT_KEYS))
            self.assertFalse(service.has_binding_backup(target))

    def test_previous_weights_restore_unparented_origin_preserves_current_world(self):
        target, body, rig, _ = fixture()
        service.bind_weights(bpy.context, target, rig, body=body)
        rig.location = (1.4, -.8, .5)
        bpy.context.view_layer.update()
        world = target.matrix_world.copy()
        service.restore_binding(bpy.context, target)
        bpy.context.view_layer.update()
        self.assertIsNone(target.parent)
        self.assert_matrix(target.matrix_world, world)
        self.assertFalse(any(key in target for key in PARENT_KEYS))

    def test_artist_changed_parent_refuses_restore_without_partial_weight_change(self):
        target, body, rig, old_parent = fixture()
        service.bind_weights(bpy.context, target, rig, body=body)
        target.vertex_groups['Deform'].add((0,), .73, 'REPLACE')
        target.parent = old_parent
        bpy.context.view_layer.update()
        before = full_state(target)
        with self.assertRaisesRegex(service.QuickBindError, '(?i)parent'):
            service.restore_binding(bpy.context, target)
        self.assertEqual(full_state(target), before)

    def test_cycle_and_active_constraint_fail_before_transfer(self):
        for case in ('cycle', 'constraint'):
            with self.subTest(case=case):
                target, body, rig, old_parent = fixture()
                if case == 'cycle':
                    rig.parent = target
                else:
                    constraint = target.constraints.new('COPY_LOCATION')
                    constraint.target = old_parent
                bpy.context.view_layer.update()
                before = full_state(target)
                with patch.object(service, '_interpolate') as solve:
                    with self.assertRaisesRegex(service.QuickBindError, '(?i)' + case):
                        service.bind_weights(bpy.context, target, rig, body=body)
                    solve.assert_not_called()
                self.assertEqual(full_state(target), before)

    def test_remove_restore_connection_keeps_parenting_history_for_previous_weights(self):
        target, body, rig, old_parent = fixture()
        target.parent = old_parent
        bpy.context.view_layer.update()
        service.bind_weights(bpy.context, target, rig, body=body)
        records = metadata(target)
        world = target.matrix_world.copy()
        service.remove_binding(bpy.context, target, rig)
        self.assertIsNone(target.parent)
        self.assertEqual(metadata(target), records)
        service.restore_removed_binding(bpy.context, target)
        bpy.context.view_layer.update()
        self.assertEqual(target.parent, rig)
        self.assertEqual(metadata(target), records)
        self.assert_matrix(target.matrix_world, world)
        service.restore_binding(bpy.context, target)
        bpy.context.view_layer.update()
        self.assertEqual(target.parent, old_parent)
        self.assert_matrix(target.matrix_world, world)

    def test_operator_undo_redo_restores_parent_and_original_weights(self):
        target, body, rig, old_parent = fixture()
        target.parent = old_parent
        bpy.context.view_layer.update()
        before = full_state(target)
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message='Before Quick Bind parent')
        self.assertEqual(bpy.ops.character_designer.quick_bind(mode='TRANSFER'), {'FINISHED'})
        bpy.context.view_layer.update()
        self.assertEqual(target.parent, rig)
        after = full_state(target)
        bpy.ops.ed.undo_push(message='After Quick Bind parent')
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        target = bpy.data.objects['Clothes']
        self.assertEqual(full_state(target), before)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        target = bpy.data.objects['Clothes']
        self.assertEqual(full_state(target), after)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(QuickBindParentTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    print('QUICK_BIND_PARENT_OK')
