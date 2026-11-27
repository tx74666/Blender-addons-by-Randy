"""Exercise Key Reduction with object and pose-bone numeric ID properties in Blender."""
import json
from pathlib import Path
import sys

import bpy

root, out, expected = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, root)
import character_designer
from character_designer import animation_preview_session as preview
from character_designer.animation_preview_curves import signature

character_designer.register()
scene = bpy.context.scene
armature = bpy.data.armatures.new('Reduction Rig Data')
rig = bpy.data.objects.new('Reduction Rig', armature)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
for name in ('Arm Control', 'Foot Control'):
    bone = armature.edit_bones.new(name)
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
bpy.ops.object.mode_set(mode='OBJECT')
arm, foot = (rig.pose.bones[name] for name in ('Arm Control', 'Foot Control'))
key = 'Rig "Float"'
root_path = '["' + bpy.utils.escape_identifier(key) + '"]'
rig[key] = .25
rig['Rig Array'] = [1., 2., 3.]
arm['Control Blend'] = .625
for frame in range(1, 10):
    arm.location.x = frame * .001
    foot.location.z = frame * .023
    rig[key] = frame / 16
    for bone in (arm, foot):
        bone.keyframe_insert('location', frame=frame)
    rig.keyframe_insert(root_path, frame=frame)
source = rig.animation_data.action
source.name = 'Walk_N Feet IK · Fixture'
slot = rig.animation_data.action_slot_handle
for bag in source.layers[0].strips[0].channelbags:
    for curve in bag.fcurves:
        for point in curve.keyframe_points:
            point.interpolation = 'LINEAR'
bpy.ops.object.mode_set(mode='POSE')
for bone in rig.pose.bones:
    if 'select' in bone.bl_rna.properties:
        bone.select = bone == arm
    else:
        bone.bone.select = bone == arm
armature.bones.active = arm.bone
scene.frame_set(4, subframe=.25)
bpy.context.view_layer.update()
assert list(bpy.context.selected_pose_bones) == [arm]
initial_signature = signature(source, slot)
ids = set(bpy.data.actions)
state = (scene.frame_current, scene.frame_subframe, rig.animation_data.action_slot_handle,
         bpy.context.mode, tuple(bpy.context.selected_pose_bones), rig[key], tuple(rig['Rig Array']),
         arm['Control Blend'], tuple(foot.location))

def check_source():
    assert rig.animation_data.action == source
    assert signature(source, slot) == initial_signature
    assert set(bpy.data.actions) == ids
    assert preview.SESSION not in rig
    assert (scene.frame_current, scene.frame_subframe, rig.animation_data.action_slot_handle,
            bpy.context.mode, tuple(bpy.context.selected_pose_bones), rig[key], tuple(rig['Rig Array']),
            arm['Control Blend'], tuple(foot.location)) == state

op = bpy.ops.character_designer.key_reduction_preview
if expected == 'baseline':
    try:
        op(operation='START')
    except RuntimeError as error:
        assert 'Object.path_from_id() does not support path creation for this type' in str(error), str(error)
    else:
        raise AssertionError('The old native operator did not report the expected error.')
    check_source()
    result = {'expected_original_error': True, 'rollback_preserved': True}
else:
    assert op(operation='START') == {'FINISHED'}
    report = preview.status(bpy.context)
    assert report['view'] == 'REDUCED' and report['before'] > report['after']
    inputs = json.loads(rig[preview.SESSION]['initial_inputs'])
    assert inputs[root_path] == [state[5]]
    assert inputs['["Rig Array"]'] == [1., 2., 3.]
    assert inputs[arm.path_from_id() + '["Control Blend"]'] == [.625]
    assert signature(source, slot) == initial_signature
    assert op(operation='ORIGINAL') == {'FINISHED'}
    assert rig.animation_data.action == source
    assert op(operation='REDUCED') == {'FINISHED'}
    assert op(operation='UPDATE', position_mm=.5, rotation_degrees=1.) == {'FINISHED'}
    assert op(operation='DISCARD') == {'FINISHED'}
    check_source()
    result = {'preview': report, 'original_restored': True, 'source_data_preserved': True}
result['version'] = character_designer.bl_info['version']
Path(out).write_text(json.dumps(result, indent=2), encoding='utf8')
print('PASS Key Reduction native operator', expected)
