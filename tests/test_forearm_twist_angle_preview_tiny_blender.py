"""Tiny native space/authentication proof; run in an isolated owned Blender."""
import importlib.util
import json
import math
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Euler, Quaternion, Vector


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'addons' / 'character_designer' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preview = load('forearm_twist_angle_preview')
geometry = load('forearm_twist_math')
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
data = bpy.data.armatures.new('Tiny arm data')
arm = bpy.data.objects.new('Tiny arm', data)
bpy.context.scene.collection.objects.link(arm)
bpy.context.view_layer.objects.active = arm
arm.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
last = None
for index, name in enumerate(('upper', 'lower', 'hand')):
    bone = data.edit_bones.new(name)
    bone.head, bone.tail = (0, index, 0), (0, index + 1, 0)
    bone.parent = last
    last = bone
bpy.ops.object.mode_set(mode='OBJECT')
arm.location = (1.2, -2.0, .4)
arm.rotation_euler = (.2, -.3, .1)
arm.scale = (1.7, .8, 1.2)
arm.pose.bones['lower'].rotation_mode = 'QUATERNION'
arm.pose.bones['lower'].rotation_quaternion = Euler((.13, -.17, .09)).to_quaternion()
hand = arm.pose.bones['hand']
hand.rotation_mode = 'QUATERNION'
hand.rotation_quaternion = Euler((.21, .19, -.12)).to_quaternion()
hand.keyframe_insert('rotation_quaternion', frame=1)
hand.rotation_quaternion = Euler((-.13, .42, .19)).to_quaternion()
hand.keyframe_insert('rotation_quaternion', frame=2)
bpy.context.scene.frame_set(1)
original_constraint = hand.constraints.new('LIMIT_ROTATION')
original_constraint.name = 'Author animated limit'
original_constraint.owner_space = 'LOCAL'
original_constraint.influence = .2
original_constraint.keyframe_insert('influence', frame=1)
original_constraint.influence = .8
original_constraint.keyframe_insert('influence', frame=2)
bpy.context.scene.frame_set(1)
mesh = bpy.data.objects.new('Tiny source', bpy.data.meshes.new('Tiny source data'))
bpy.context.scene.collection.objects.link(mesh)
runtime = SimpleNamespace(PREVIEW_KEY='_tiny_preview', _BUSY=False, twist_angle=geometry.twist_angle,
                          update_runtime=lambda *_args: None)
session = {'mesh': mesh, 'armature': arm, 'chain': ['upper', 'lower', 'hand'], 'target': 'hand'}
mesh[runtime.PREVIEW_KEY] = json.dumps({'target': 'hand'})


def current(_context):
    evaluated = arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
    transforms = [evaluated.pose.bones[name].matrix @ data.bones[name].matrix_local.inverted()
                  for name in ('lower', 'hand')]
    return geometry.twist_angle(*transforms, Vector((0, 1, 0)))


runtime.current_twist_angle = current
bpy.context.view_layer.update()
before_pose = preview._author_pose(hand)
action = arm.animation_data.action
records = []
for degrees in (90, -90, 120, -120, 0, 45, 90):
    try:
        result = preview.set_angle(bpy.context, runtime, session, math.radians(degrees))
    except Exception:
        helper = mesh.get(preview.SOURCE_REF)
        if helper is not None:
            print('HELPER_USERS', helper.users, [(item.bl_rna.identifier, item.name)
                  for item in bpy.data.user_map(subset={helper}).get(helper, set())], flush=True)
            receipt = session.get('angle_preview')
            if receipt:
                for index, con in enumerate(hand.constraints):
                    saved = (receipt['constraint_signature'] if index == len(hand.constraints)-1
                             else receipt['original_constraints'][index])
                    current_signature = preview._constraint_signature(con)
                    print('CONSTRAINT_DIFF', con.name, {key: (saved[key], current_signature[key])
                          for key in saved if saved[key] != current_signature[key]}, flush=True)
        raise
    assert abs(result['angle'] - math.radians(degrees)) < preview.TOLERANCE, result
    assert preview._author_pose(hand) == before_pose
    assert arm.animation_data.action == action
    records.append({'degrees': degrees, **result})
assert preview.release(bpy.context, runtime, session)
assert preview.SOURCE_REF not in mesh and 'angle_preview' not in session
assert list(hand.constraints) == [original_constraint]
assert preview._author_pose(hand) == before_pose
assert arm.animation_data.action == action
preview.set_angle(bpy.context, runtime, session, .7)
previous_record = json.loads(json.dumps(session['angle_preview']))
previous_rotation = tuple(mesh[preview.SOURCE_REF].rotation_quaternion)
with patch.object(preview, '_check_reached', side_effect=RuntimeError('injected reached failure')):
    try:
        preview.set_angle(bpy.context, runtime, session, -.8)
        raise AssertionError('Injected failure was swallowed')
    except RuntimeError:
        pass
assert session['angle_preview'] == previous_record
assert tuple(mesh[preview.SOURCE_REF].rotation_quaternion) == previous_rotation
assert runtime._BUSY is False
bpy.context.scene.frame_set(2)
new_pose = preview._author_pose(hand)
assert new_pose != before_pose
assert abs(original_constraint.influence - .8) < 1.e-6
preview.release(bpy.context, runtime, session)
assert preview._author_pose(hand) == new_pose
assert abs(original_constraint.influence - .8) < 1.e-6
preview.set_angle(bpy.context, runtime, session, .4)
with patch.object(preview, '_clear_receipt', side_effect=RuntimeError('injected receipt cleanup failure')):
    try:
        preview.release(bpy.context, runtime, session)
        raise AssertionError('Injected cleanup failure was swallowed')
    except RuntimeError:
        pass
assert session['angle_preview']['phase'] == 'releasing'
assert mesh.get(preview.SOURCE_REF) is None
assert preview.release(bpy.context, runtime, session)
assert preview.SOURCE_REF not in mesh and 'angle_preview' not in session
del mesh[runtime.PREVIEW_KEY]
assert preview.release(bpy.context, runtime, session) is False
print('FOREARM_ANGLE_TINY_PASS', json.dumps(records), flush=True)
