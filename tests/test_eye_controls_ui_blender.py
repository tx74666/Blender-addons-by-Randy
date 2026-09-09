"""Public eye actions, color/collection integration and base-rig protection."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import character_designer
from character_designer import eye_controls, limb_ik
import test_eye_controls_blender as fixture
import test_limb_ik_blender as base


character_designer.register()
rig = fixture.fixture(posed=True)
before = fixture.poses(rig)
digest = limb_ik._armature_digest(rig)
assert bpy.ops.character_designer.eye_controls(action='BUILD') == {'FINISHED'}
record = eye_controls.get_record(rig)
eye_controls._verify_pose(rig, before)
assert limb_ik._armature_digest(rig) == digest
assert bpy.ops.character_designer.eye_controls(action='BUILD') == {'FINISHED'}
assert eye_controls.get_record(rig) == record
animation = {bone.name for bone in rig.data.collections['Animation'].bones}
assert set(record['bones'].values()) <= animation
assert not set(record['sources']) & animation
for name in record['bones'].values():
    assert bpy.ops.character_designer.eye_controls(action='SELECT', bone=name) == {'FINISHED'}
    assert bpy.context.mode == 'POSE' and rig.data.bones.active.name == name
    assert {pb.name for pb in rig.pose.bones if pb.select} == {name}
    color = rig.pose.bones[name].color
    assert color.palette == 'CUSTOM'
    assert max(color.custom.normal) - min(color.custom.normal) > .05
    assert max(color.custom.active) > max(color.custom.normal)
assert base.cancelled_result(lambda: bpy.ops.character_designer.eye_controls(action='SELECT', bone='Head')) == {'CANCELLED'}
rig.pose.bones[record['master']].location.x += .025
fixture.update(rig)
desired = fixture.poses(rig, before)
assert base.cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) == {'CANCELLED'}
assert base.cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove('EXEC_DEFAULT')) == {'CANCELLED'}
assert bpy.ops.character_designer.eye_controls(action='REMOVE') == {'FINISHED'}
eye_controls._verify_pose(rig, desired)
assert set(record['sources']) <= {bone.name for bone in rig.data.collections['Animation'].bones}
assert limb_ik._armature_digest(rig) == digest
limb_ik._validate_inventory(rig)
print('EYE_UI_PASSED', flush=True)
