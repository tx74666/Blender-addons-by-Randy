"""Character placement proofs in an empty disposable native Blender process.

Run with --background --factory-startup --python; never pass an artist .blend.
"""
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import character_ground as placement, animation_worklist_target, skirt_rig
from test_limb_ik_blender import reset_scene, make_humanoid, analyze
from test_skirt_topology_blender import frustum


def matrix(value):
    return tuple(tuple(row) for row in value)


def activate(rig):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.objects.active = rig
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    rig.select_set(True)


def mesh(name, z, rig=None, parent=None):
    data = bpy.data.meshes.new(name)
    data.from_pydata(((-.1, 0., z), (.1, 0., z), (0., .1, z + .2)), (), ((0, 1, 2),))
    obj = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(obj)
    if rig:
        obj.modifiers.new('Character skin', 'ARMATURE').object = rig
        obj.vertex_groups.new(name='Hips').add([0, 1, 2], 1., 'REPLACE')
    if parent:
        world = obj.matrix_world.copy()
        obj.parent = parent
        obj.matrix_world = world
    return obj


def raw_mesh(obj):
    keys = obj.data.shape_keys
    return (tuple(tuple(v.co) for v in obj.data.vertices),
            tuple((group.name, group.index) for group in obj.vertex_groups),
            tuple(tuple((g.group, g.weight) for g in v.groups) for v in obj.data.vertices),
            tuple((key.name, key.value, tuple(tuple(p.co) for p in key.data))
                  for key in keys.key_blocks) if keys else None)


class CharacterPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        reset_scene()
        self.rig = make_humanoid('Ground Character')
        self.body = mesh('Body', .45, self.rig, self.rig)
        self.shoes = mesh('Detached shoes', .25, self.rig)
        self.body.shape_key_add(name='Basis')
        key = self.body.shape_key_add(name='Artist correction')
        key.data[2].co.x += .025
        key.value = .4
        activate(self.rig)
        bpy.context.view_layer.update()

    def assertMatrix(self, actual, expected, tolerance=3e-5):
        self.assertLess(max(abs(a - b) for row_a, row_b in zip(actual, expected)
                            for a, b in zip(row_a, row_b)), tolerance)

    def test_ground_moves_parented_detached_and_bone_children_exactly_once(self):
        accessory = mesh('Bone accessory', .8)
        accessory.parent = self.rig
        accessory.parent_type = 'BONE'
        accessory.parent_bone = 'hand.L'
        bpy.context.view_layer.update()
        scope = (self.rig, self.body, self.shoes, accessory)
        before = {obj: obj.matrix_world.copy() for obj in scope}
        parents = {obj: (obj.parent, obj.parent_type, obj.parent_bone) for obj in scope}
        internal = placement._rig_state(self.rig)
        result = placement.ground(bpy.context, self.rig)
        self.assertAlmostEqual(result['lowest_after'], 0., delta=3e-5)
        self.assertEqual(set(result['roots']), {self.rig.name, self.shoes.name})
        translation = Matrix.Translation((0., 0., result['delta_z']))
        for obj in scope:
            self.assertMatrix(obj.matrix_world, translation @ before[obj])
            self.assertEqual((obj.parent, obj.parent_type, obj.parent_bone), parents[obj])
        self.assertEqual(placement._rig_state(self.rig), internal)

    def test_ground_moves_up_when_character_is_below_floor(self):
        placement.move(bpy.context, self.rig, -1.5)
        result = placement.ground(bpy.context, self.rig)
        self.assertGreater(result['delta_z'], 0.)
        self.assertAlmostEqual(result['lowest_after'], 0., delta=3e-5)

    def test_signed_world_distance_below_rotated_nonuniform_external_parent(self):
        parent = bpy.data.objects.new('Artist placement parent', None)
        bpy.context.scene.collection.objects.link(parent)
        parent.location = (2., -1., .4)
        parent.rotation_euler = (.3, -.4, .8)
        parent.scale = (1.7, .8, 1.2)
        self.rig.parent = parent
        self.rig.location = (.3, .1, .6)
        self.rig.rotation_euler = (.2, .3, -.1)
        self.rig.scale = (.9, 1.1, .8)
        self.rig.delta_location = (.03, -.02, .01)
        self.rig.delta_rotation_euler = (.02, .01, -.03)
        self.rig.delta_scale = (1.01, .99, 1.02)
        bpy.context.view_layer.update()
        before = {obj: obj.matrix_world.copy() for obj in (self.rig, self.body, self.shoes)}
        parent_before = matrix(parent.matrix_world)
        channels = placement._channels(self.rig)
        result = placement.move(bpy.context, self.rig, .35)
        translation = Matrix.Translation((0., 0., .35))
        self.assertEqual(result['distance'], .35)
        for obj, world in before.items():
            self.assertMatrix(obj.matrix_world, translation @ world)
        self.assertEqual(matrix(parent.matrix_world), parent_before)
        after = placement._channels(self.rig)
        for name in channels['channels']:
            if name != 'location':
                self.assertEqual(after['channels'][name], channels['channels'][name])

    def test_ground_ignores_hidden_backup_reference_and_widget_source_meshes(self):
        hidden = mesh('Hidden character mesh', -90., self.rig)
        hidden.hide_set(True)
        source = mesh('Widget source', -80., parent=self.rig)
        source['character_designer_role'] = 'WIDGET'
        shape = mesh('Artist custom shape', -70., parent=self.rig)
        self.rig.pose.bones['hand.L'].custom_shape = shape
        reference = mesh('Reference plane', -60., parent=self.rig)
        reference['cdesigner_reference_generator'] = 'cdesigner_reference_views_v1'
        backup = mesh('Old body', -50., self.rig, self.rig)
        folder = bpy.data.collections.new('Backup')
        bpy.context.scene.collection.children.link(folder)
        for collection in tuple(backup.users_collection):
            collection.objects.unlink(backup)
        folder.objects.link(backup)
        bpy.context.view_layer.update()
        result = placement.ground(bpy.context, self.rig)
        self.assertAlmostEqual(result['lowest_before'], .25, delta=3e-5)
        self.assertEqual(set(result['visible_meshes']), {self.body.name, self.shoes.name})
        self.assertEqual(set(result['meshes']), {self.body.name, self.shoes.name, hidden.name})
        self.assertEqual(result['hidden_meshes'], [hidden.name])

    def test_hidden_global_displace_is_refused_before_any_placement(self):
        hidden = mesh('Hidden world-space displaced mesh', -2., self.rig)
        modifier = hidden.modifiers.new('World displacement', 'DISPLACE')
        modifier.texture = bpy.data.textures.new('World displacement noise', type='CLOUDS')
        modifier.texture_coords = 'GLOBAL'
        modifier.strength = .25
        hidden.hide_set(True)
        bpy.context.view_layer.update()
        objects = (self.rig, self.body, self.shoes, hidden)
        channels = {obj: placement._channels(obj) for obj in objects}
        world = {obj: obj.matrix_world.copy() for obj in objects}
        raw = raw_mesh(hidden)
        for operation in (lambda: placement.move(bpy.context, self.rig, .3),
                          lambda: placement.ground(bpy.context, self.rig)):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(ValueError, 'Hidden mesh .* uses DISPLACE'):
                    operation()
                for obj in objects:
                    self.assertEqual(placement._channels(obj), channels[obj])
                    self.assertMatrix(obj.matrix_world, world[obj])
                self.assertEqual(raw_mesh(hidden), raw)
        self.assertTrue(hidden.hide_get())

    def test_hidden_local_mesh_is_also_included_in_shape_proof_and_rollback(self):
        hidden = mesh('Hidden local mesh', -2., self.rig)
        hidden.hide_set(True)
        bpy.context.view_layer.update()
        actual = placement._points
        calls = [0]

        def changed(obj, depsgraph):
            points = actual(obj, depsgraph)
            if obj == hidden:
                calls[0] += 1
                if calls[0] > 1:
                    points[0] += .1
            return points

        before = {obj: placement._channels(obj) for obj in (self.rig, self.shoes, hidden)}
        with patch.object(placement, '_points', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'Hidden local mesh changed its shape'):
                placement.move(bpy.context, self.rig, .3)
        self.assertGreater(calls[0], 1)
        for obj, channels in before.items():
            self.assertEqual(placement._channels(obj), channels)
        self.assertTrue(hidden.hide_get())

    def test_pose_animation_hand_rest_channels_weights_and_keys_survive(self):
        hand = self.rig.pose.bones['hand.L']
        hand.rotation_mode = 'XYZ'
        for frame, value in ((1, .1), (5, .4)):
            hand.rotation_euler.x = value
            hand.keyframe_insert('rotation_euler', frame=frame)
        bpy.context.scene.frame_set(3)
        before_rig = placement._rig_state(self.rig)
        before_mesh = raw_mesh(self.body), raw_mesh(self.shoes)
        action = self.rig.animation_data.action
        result = placement.move(bpy.context, self.rig, -.3)
        self.assertAlmostEqual(result['lowest_after'], result['lowest_before'] - .3, delta=3e-5)
        self.assertEqual(placement._rig_state(self.rig), before_rig)
        self.assertEqual((raw_mesh(self.body), raw_mesh(self.shoes)), before_mesh)
        self.assertIs(self.rig.animation_data.action, action)

    def test_released_region_metadata_does_not_prevent_whole_character_placement(self):
        # Placement never interprets or changes the regional Rest-edit receipt.
        self.rig.data['character_designer_body_region_release_v1'] = 'unchanged release witness'
        result = placement.move(bpy.context, self.rig, .12)
        self.assertEqual(result['delta_z'], .12)
        self.assertEqual(self.rig.data['character_designer_body_region_release_v1'], 'unchanged release witness')

    def test_generated_hand_controls_and_their_configuration_are_kept(self):
        _result, settings = analyze(self.rig)
        settings.selected_limb = 'LEFT_ARM'
        settings.build_method = 'DIRECT_PREROLL'
        self.assertEqual(bpy.ops.character_designer.limb_ik_build_selected(), {'FINISHED'})
        before = placement._rig_state(self.rig)
        self.assertTrue(any(bone.get('character_designer_owner') for bone in self.rig.data.bones))
        placement.move(bpy.context, self.rig, .2)
        self.assertEqual(placement._rig_state(self.rig), before)

    def test_validated_attached_dress_and_its_detached_mesh_move_with_main(self):
        dress = frustum('Attached dress', rows=6, sides=16)
        skirt_rig.build_skirt(bpy.context, dress, armature=self.rig, shared=False)
        dress_rig = dress[skirt_rig.RIG_KEY]
        activate(self.rig)
        bpy.context.view_layer.update()
        before_rig = dress_rig.matrix_world.copy()
        before_mesh = dress.matrix_world.copy()
        internal = placement._rig_state(dress_rig)
        result = placement.move(bpy.context, self.rig, -.2)
        self.assertIn(dress_rig.name, result['objects'])
        self.assertIn(dress.name, result['objects'])
        translation = Matrix.Translation((0., 0., -.2))
        self.assertMatrix(dress_rig.matrix_world, translation @ before_rig)
        self.assertMatrix(dress.matrix_world, translation @ before_mesh)
        self.assertEqual(placement._rig_state(dress_rig), internal)
        proof = result['mesh_proof'][dress.name]
        self.assertGreater(proof['tolerance'], 2e-5)
        self.assertLessEqual(proof['max_abs_world_component_error'], proof['tolerance'])
        self.assertAlmostEqual(proof['tolerance'], 2e-5 + proof['bbox_diagonal'] * 1e-4)

    def test_object_transform_animation_is_refused_without_mutation(self):
        self.rig.keyframe_insert('location', frame=1)
        before = placement._channels(self.rig)
        with self.assertRaisesRegex(ValueError, 'object transform animation'):
            placement.ground(bpy.context, self.rig)
        self.assertEqual(placement._channels(self.rig), before)

    def test_keyed_child_mesh_keeps_authored_animation_and_local_transform(self):
        from character_designer import limb_ik
        for frame, x, z in ((1, -.04, .02), (5, .08, .06)):
            self.body.location = (x, .03, z)
            self.body.keyframe_insert('location', frame=frame)
        bpy.context.scene.frame_set(3)
        bpy.context.view_layer.update()
        action = self.body.animation_data.action

        def keys():
            return tuple((curve.data_path, curve.array_index,
                          tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                                 point.interpolation) for point in curve.keyframe_points))
                         for curve in limb_ik._fcurves_for_action(action))

        before_keys = keys()
        local = placement._channels(self.body)
        world = self.body.matrix_world.copy()
        result = placement.move(bpy.context, self.rig, .2)
        self.assertEqual(result['distance'], .2)
        self.assertMatrix(self.body.matrix_world, Matrix.Translation((0., 0., .2)) @ world)
        self.assertEqual(placement._channels(self.body), local)
        self.assertEqual(keys(), before_keys)
        self.assertIs(self.body.animation_data.action, action)

    def test_keyed_detached_bound_mesh_root_is_refused_without_mutation(self):
        self.shoes.keyframe_insert('location', frame=1)
        before = placement._channels(self.rig), placement._channels(self.shoes)
        with self.assertRaisesRegex(ValueError, 'object transform animation'):
            placement.move(bpy.context, self.rig, .2)
        self.assertEqual((placement._channels(self.rig), placement._channels(self.shoes)), before)

    def test_driven_child_mesh_keeps_local_driver_and_transform(self):
        self.body['artist_height'] = .06
        driver = self.body.driver_add('location', 2).driver
        variable = driver.variables.new()
        variable.type = 'SINGLE_PROP'
        variable.targets[0].id = self.body
        variable.targets[0].data_path = '["artist_height"]'
        driver.expression = variable.name
        bpy.context.view_layer.update()
        local = placement._channels(self.body)
        world = self.body.matrix_world.copy()
        expression = driver.expression
        placement.move(bpy.context, self.rig, .2)
        self.assertEqual(placement._channels(self.body), local)
        self.assertMatrix(self.body.matrix_world, Matrix.Translation((0., 0., .2)) @ world)
        self.assertEqual(driver.expression, expression)
        self.assertEqual(variable.targets[0].data_path, '["artist_height"]')

    def test_object_transform_driver_is_refused_without_mutation(self):
        self.rig.driver_add('location', 2).driver.expression = '.5'
        bpy.context.view_layer.update()
        before = placement._channels(self.rig)
        with self.assertRaisesRegex(ValueError, 'object transform driver'):
            placement.ground(bpy.context, self.rig)
        self.assertEqual(placement._channels(self.rig), before)

    def test_external_bone_constraint_target_is_refused(self):
        target = bpy.data.objects.new('Outside hand target', None)
        bpy.context.scene.collection.objects.link(target)
        con = self.rig.pose.bones['hand.L'].constraints.new('COPY_LOCATION')
        con.target = target
        before = placement._channels(self.rig)
        with self.assertRaisesRegex(ValueError, 'external transform'):
            placement.ground(bpy.context, self.rig)
        self.assertEqual(placement._channels(self.rig), before)

    def test_external_transform_driver_read_is_refused(self):
        target = bpy.data.objects.new('Outside driver target', None)
        bpy.context.scene.collection.objects.link(target)
        self.rig['artist_driver'] = 0.
        driver = self.rig.driver_add('["artist_driver"]').driver
        variable = driver.variables.new()
        variable.type = 'TRANSFORMS'
        variable.targets[0].id = target
        driver.expression = variable.name
        with self.assertRaisesRegex(ValueError, 'external transform'):
            placement.ground(bpy.context, self.rig)

    def test_external_modifier_target_is_refused(self):
        target = bpy.data.objects.new('Outside mirror origin', None)
        bpy.context.scene.collection.objects.link(target)
        self.body.modifiers.new('Artist mirror', 'MIRROR').mirror_object = target
        with self.assertRaisesRegex(ValueError, 'external modifier target'):
            placement.ground(bpy.context, self.rig)

    def test_adopted_model_pin_session_is_refused(self):
        self.rig[animation_worklist_target.SESSION] = '{}'
        with self.assertRaisesRegex(ValueError, 'Restore Main Rig'):
            placement.ground(bpy.context, self.rig)

    def test_rollback_restores_every_root_channel_exactly(self):
        self.rig.location = (.13, -.17, .4)
        self.rig.rotation_euler = (.13, -.2, .31)
        self.rig.delta_location = (.03, -.02, .01)
        self.shoes.location = (.1, .2, .3)
        bpy.context.view_layer.update()
        channels = {obj: placement._channels(obj) for obj in (self.rig, self.shoes)}
        world = {obj: obj.matrix_world.copy() for obj in (self.rig, self.body, self.shoes)}
        internal = placement._rig_state(self.rig)
        with patch.object(placement, '_verify', side_effect=ValueError('Injected verification failure')):
            with self.assertRaisesRegex(ValueError, 'Injected verification failure'):
                placement.move(bpy.context, self.rig, .3)
        for obj, before in channels.items():
            self.assertEqual(placement._channels(obj), before)
        for obj, before in world.items():
            self.assertMatrix(obj.matrix_world, before)
        self.assertEqual(placement._rig_state(self.rig), internal)

    def _assert_injected_shape_refuses(self, amplitude):
        # A modifier's world-space dependence is deliberately invisible to the
        # structural admission; the evaluated-point proof is still authoritative.
        actual = placement._points
        calls = [0]

        def changed(obj, depsgraph):
            points = actual(obj, depsgraph)
            calls[0] += 1
            if calls[0] > 2 and points:
                points[0] += amplitude
            return points

        before = placement._channels(self.rig), placement._channels(self.shoes)
        with patch.object(placement, '_points', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed its shape'):
                placement.move(bpy.context, self.rig, .3)
        self.assertEqual((placement._channels(self.rig), placement._channels(self.shoes)), before)

    def test_failed_actual_shape_proof_rolls_back(self):
        self._assert_injected_shape_refuses(.1)

    def test_small_real_shape_change_above_native_limit_still_rolls_back(self):
        self._assert_injected_shape_refuses(.002)

    def test_no_visible_mesh_and_nonfinite_distance_are_refused(self):
        self.body.hide_set(True)
        self.shoes.hide_set(True)
        with self.assertRaisesRegex(ValueError, 'No visible character mesh'):
            placement.ground(bpy.context, self.rig)
        with self.assertRaisesRegex(ValueError, 'finite'):
            placement.move(bpy.context, self.rig, math.inf)

    def test_linked_main_rig_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'linked_character.blend')
            bpy.data.libraries.write(path, {self.rig})
            with bpy.data.libraries.load(path, link=True) as (source, target):
                target.objects = [self.rig.name]
            linked = target.objects[0]
            bpy.context.scene.collection.objects.link(linked)
            self.assertIsNotNone(linked.library)
            with self.assertRaisesRegex(ValueError, 'local Main Rig'):
                placement.ground(bpy.context, linked)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(CharacterPlacementTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
