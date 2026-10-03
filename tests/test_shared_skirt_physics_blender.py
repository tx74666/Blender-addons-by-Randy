"""Two-frame native physics/bake lifecycle on a shared character armature.

Run in a separate Blender process. It saves only a disposable test scene and
never opens, writes or changes the artist's X scene.
"""
from pathlib import Path
import sys
import tempfile
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import skirt_rig as skirt, skirt_physics as physics
from test_skirt_shared_rig_blender import (
    fixture, mesh_content, rest_state, pose_channels, world_vertices,
    geometry_error, matrix_values,
)


def action_content(action):
    if action is None:
        return None
    curves = [curve for layer in action.layers for strip in layer.strips
              for bag in strip.channelbags for curve in bag.fcurves]
    return tuple((curve.data_path, curve.array_index,
                  tuple((tuple(key.co), tuple(key.handle_left), tuple(key.handle_right),
                         key.interpolation) for key in curve.keyframe_points)) for curve in curves)


def body_mesh(main):
    data = bpy.data.meshes.new('Protected Body Mesh')
    data.from_pydata([(-0.1, 0, 1), (0.1, 0, 1), (0.1, 0, 1.4), (-0.1, 0, 1.4)], [], [(0, 1, 2, 3)])
    source = bpy.data.objects.new('Protected Body', data)
    bpy.context.collection.objects.link(source)
    source.vertex_groups.new(name='Hips').add([0, 1, 2, 3], 0.7, 'REPLACE')
    source.vertex_groups.new(name='Body').add([0, 1, 2, 3], 0.3, 'REPLACE')
    source.shape_key_add(name='Basis')
    key = source.shape_key_add(name='Blink_L')
    key.data[2].co.x += 0.003
    key.value = 0.4
    data.uv_layers.new(name='Protected UV')
    source.parent = main
    modifier = source.modifiers.new('Body Skin', 'ARMATURE')
    modifier.object = main
    return source


class SharedPhysicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def test_default_shared_physics_native_bake_reopen_remove_preserves_character(self):
        source, old, main, record = fixture(posed=False)
        skirt.remove_skirt(bpy.context, source)
        record = skirt.build_skirt(bpy.context, source, chain_count=4, segment_count=3, armature=main)
        self.assertTrue(skirt.is_shared(record))
        self.assertIs(source[skirt.RIG_KEY], main)
        body = body_mesh(main)
        bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 2
        hips = main.pose.bones['Hips']
        hips.rotation_mode = 'XYZ'
        hips.keyframe_insert('rotation_euler', frame=1)
        hips.rotation_euler.z = 0.01
        hips.keyframe_insert('rotation_euler', frame=2)
        bpy.context.scene.frame_set(1)
        character_names = ('Hips', 'Body', 'Hair')
        before_body, before_dress = mesh_content(body), mesh_content(source)
        before_rest = rest_state(main, character_names)
        before_channels = pose_channels(main, character_names)
        before_action = main.animation_data.action
        before_action_content = action_content(before_action)
        before_world = world_vertices(body)
        body_modifier = body.modifiers['Body Skin']
        body_binding = (body.parent, body.parent_type, body.parent_bone,
                        matrix_values(body.matrix_parent_inverse), body_modifier.object)
        record = physics.add_physics(bpy.context, source)
        self.assertIs(source[skirt.RIG_KEY], main)
        waist_name = record['controls']['waist']
        main.data.bones[waist_name]['artist_note'] = 'Keep the Dress export note'
        main.pose.bones[waist_name]['artist_pose_note'] = 'Keep the Dress pose note'
        # Exercise migration-era ownership metadata on a PoseBone too. Export
        # may clear its reserved tags while retaining other artist properties.
        main.pose.bones[waist_name][skirt.OWNER_KEY] = record['owner']
        main.pose.bones[waist_name][skirt.SOURCE_KEY] = source
        influence, driver_id, path = skirt.physics_control(source)
        self.assertEqual(influence, main.pose.bones[record['controls']['waist']])
        self.assertEqual(driver_id, main)
        self.assertEqual(path, influence.path_from_id() + '["physics_influence"]')
        self.assertEqual(influence['physics_influence'], 1.0)
        self.assertNotIn('physics_influence', main)
        drivers = [curve for curve in main.animation_data.drivers
                   if 'Skirt physics delta' in curve.data_path]
        self.assertEqual(len(drivers), 12)
        for curve in drivers:
            self.assertIs(curve.driver.variables[0].targets[0].id, main)
            self.assertEqual(curve.driver.variables[0].targets[0].data_path, path)
        proxy, cloth = physics._cloth(record)
        self.assertEqual(next(mod for mod in proxy.modifiers if mod.type == 'ARMATURE').object, main)
        self.assertTrue(record['physics']['colliders'])
        for name in record['physics']['colliders']:
            collider = bpy.data.objects[name]
            self.assertIs(collider.parent, main)
            self.assertEqual(next(mod for mod in collider.modifiers if mod.type == 'ARMATURE').object, main)
        baked = physics.bake_animation(bpy.context, source, 1, 2)
        self.assertEqual((baked['frames'], baked['kind']), (2, 'ANIMATION'))
        self.assertTrue(cloth.point_cache.is_baked)
        output = bpy.data.objects[baked['rig']]
        mesh = bpy.data.objects[baked['mesh']]
        collection = bpy.data.collections[baked['collection']]
        collection.hide_viewport = False
        collection.hide_render = False
        self.assertIsNone(output.parent)
        self.assertIs(mesh.parent, output)
        self.assertIs(next(mod for mod in mesh.modifiers if mod.type == 'ARMATURE').object, output)
        keep = skirt._bone_collection_layout(record)[1] | {record['controls']['waist']}
        self.assertEqual(set(output.data.bones.keys()), keep)
        self.assertTrue(all(skirt.OWNER_KEY not in bone and skirt.SOURCE_KEY not in bone
                            for bone in output.data.bones))
        self.assertTrue(all(skirt.OWNER_KEY not in bone and skirt.SOURCE_KEY not in bone
                            for bone in output.pose.bones))
        self.assertTrue(all(skirt.OWNER_KEY not in group and skirt.SOURCE_KEY not in group
                            for group in output.data.collections_all))
        self.assertEqual(output.data.bones[waist_name]['artist_note'], 'Keep the Dress export note')
        self.assertEqual(output.pose.bones[waist_name]['artist_pose_note'], 'Keep the Dress pose note')
        self.assertEqual(main.pose.bones[waist_name][skirt.OWNER_KEY], record['owner'])
        self.assertEqual(main.pose.bones[waist_name][skirt.SOURCE_KEY], source)
        from character_designer import selected_bone_weights as weights
        self.assertEqual(set(weights._mesh_deform_bone_names(mesh, output)), keep)
        self.assertIsNotNone(output.animation_data.action)
        self.assertIsNot(output.animation_data.action, before_action)
        self.assertFalse(output.animation_data.drivers)
        self.assertTrue(all(not bone.constraints for bone in output.pose.bones))
        errors = {}
        for frame in (1, 2):
            bpy.context.scene.frame_set(frame)
            errors[frame] = geometry_error(world_vertices(source), world_vertices(mesh))
            self.assertLessEqual(errors[frame], 1e-4, (frame, errors[frame]))
        bpy.context.scene.frame_set(1)
        self.assertEqual(mesh_content(body), before_body)
        self.assertEqual(mesh_content(source), before_dress)
        self.assertEqual(rest_state(main, character_names), before_rest)
        self.assertEqual(pose_channels(main, character_names), before_channels)
        self.assertIs(main.animation_data.action, before_action)
        self.assertEqual(action_content(main.animation_data.action), before_action_content)
        self.assertLessEqual(geometry_error(before_world, world_vertices(body)), 1e-5)
        self.assertEqual((body.parent, body.parent_type, body.parent_bone,
                          matrix_values(body.matrix_parent_inverse), body_modifier.object), body_binding)
        names = main.name, source.name, body.name, output.name, mesh.name
        with tempfile.TemporaryDirectory() as directory:
            saved = Path(directory) / 'shared-physics-bake.blend'
            bpy.ops.wm.save_as_mainfile(filepath=str(saved))
            bpy.ops.wm.open_mainfile(filepath=str(saved))
            main, source, body, output, mesh = (bpy.data.objects[name] for name in names)
            self.assertIs(source[skirt.RIG_KEY], main)
            self.assertIs(next(mod for mod in mesh.modifiers if mod.type == 'ARMATURE').object, output)
            self.assertIsNotNone(output.animation_data.action)
            self.assertEqual(set(weights._mesh_deform_bone_names(mesh, output)), keep)
            self.assertEqual(mesh_content(body), before_body)
            self.assertEqual(mesh_content(source), before_dress)
            self.assertEqual(action_content(main.animation_data.action), before_action_content)
            owner = skirt.read_record(source)['owner']
            skirt.remove_skirt(bpy.context, source)
            self.assertIn(main.name, bpy.data.objects)
            self.assertIn(output.name, bpy.data.objects)
            self.assertIn(mesh.name, bpy.data.objects)
            self.assertEqual(rest_state(main), before_rest)
            self.assertEqual(mesh_content(body), before_body)
            self.assertEqual(action_content(main.animation_data.action), before_action_content)
            self.assertFalse(any(obj.get(skirt.OWNER_KEY) == owner for obj in bpy.data.objects))
        print('SHARED_PHYSICS_TWO_FRAME_BAKE', errors, flush=True)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SharedPhysicsTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
