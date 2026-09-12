"""Removing body controls keeps a valid corrective working on native FK bones."""
import math
import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import body_setup, forearm_twist, limb_ik
import test_forearm_twist_blender as fixtures


def test_valid_correction_continues_on_native_fk_after_remove():
    for method in ('DIRECT_PREROLL', 'ROLL_DECOUPLED'):
        fixture = fixtures.make_fixture(method)
        rig, mesh = fixture['armature'], fixture['mesh']
        fixtures.pose_target(fixture, 25.)
        forearm_twist.start_test(bpy.context, mesh, side='L')
        forearm_twist.set_ratio(bpy.context, 3, .34)
        forearm_twist.finish_test(bpy.context, confirm=True)
        fixtures.assert_runtime_geometry(fixture, method + ' before removal')
        profile = forearm_twist._records(mesh)['L']['rings']
        key_name = forearm_twist._records(mesh)['L']['key']
        key_id = mesh.data.shape_keys.key_blocks[key_name].as_pointer()
        for obj in tuple(bpy.context.selected_objects):
            obj.select_set(False)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        result = body_setup.remove(bpy.context, rig, keep_native_rest=True)
        assert result['retained_rest'] and not limb_ik._validate_inventory(rig)['rigs']
        forearm_twist.update_runtime(bpy.context.scene)
        fixtures.assert_runtime_geometry(fixture, method + ' after removal')
        assert forearm_twist._resolve_rig(mesh, 'L')[1]['fk_source']
        assert forearm_twist._records(mesh)['L']['target'] == fixture['hand_name']
        assert forearm_twist._records(mesh)['L']['rings'] == profile
        assert mesh.data.shape_keys.key_blocks[key_name].as_pointer() == key_id
        hand = rig.pose.bones[fixture['hand_name']]
        hand.rotation_mode = 'XYZ'
        hand.rotation_euler.y += math.radians(25.)
        rig.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        forearm_twist.update_runtime(bpy.context.scene)
        fixtures.assert_runtime_geometry(fixture, method + ' native FK movement')
        print('PASS live corrective continues through remove to native FK', method, flush=True)


if __name__ == '__main__':
    test_valid_correction_continues_on_native_fk_after_remove()
    print('BODY_SETUP_FOREARM_RUNTIME_TESTS_PASS 2', flush=True)
