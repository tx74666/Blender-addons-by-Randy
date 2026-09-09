"""Display spacing preserves animated gaze and restores artist display offsets."""
import json
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import character_designer as cd
from character_designer import eye_controls as eyes, limb_ik
import test_eye_controls_blender as fixture

cd.register()
rig = fixture.fixture(posed=True)
record = eyes.build(bpy.context, rig)
distance = eyes.recommended_display_spacing(rig)
assert eyes.display_spacing(rig) == distance
assert eyes.build(bpy.context, rig) == record
for name in record['bones'].values():
    assert abs(rig.pose.bones[name].custom_shape_translation.y + distance) < 1e-7
eyes.set_display_spacing(bpy.context, rig, 0)
original = {}
for index, (role, name) in enumerate(record['bones'].items()):
    pb = rig.pose.bones[name]
    pb.custom_shape_translation = (.013 * (index + 1), .025, -.009)
    pb.custom_shape_rotation_euler = (.3, .2, -.4)
    pb.custom_shape_scale_xyz = (2, .7, 1.4)
    pb.use_custom_shape_bone_size = bool(index % 2)
    original[role] = tuple(pb.custom_shape_translation)
master = rig.pose.bones[record['master']]
for frame, x in ((1, -.02), (10, .04), (20, -.01)):
    master.location.x = x
    master.keyframe_insert(data_path='location', frame=frame)
    rig.pose.bones['Head'].rotation_euler.z = x * 3
    rig.pose.bones['Head'].keyframe_insert(data_path='rotation_euler', frame=frame)
frames = (1, 6, 10, 15, 20)
before = {}
for frame in frames:
    bpy.context.scene.frame_set(frame)
    before[frame] = fixture.poses(rig)
weights = fixture.weights()
rest = limb_ik._armature_digest(rig)
assert bpy.ops.character_designer.eye_controls(action='SPACING', distance=distance) == {'FINISHED'}
first = {n: tuple(rig.pose.bones[n].custom_shape_translation) for n in record['bones'].values()}
eyes.set_display_spacing(bpy.context, rig, distance)
assert first == {n: tuple(rig.pose.bones[n].custom_shape_translation) for n in first}
for frame in frames:
    bpy.context.scene.frame_set(frame)
    fixture.update(rig)
    eyes._verify_pose(rig, before[frame])
    displacements = []
    for role, name in record['bones'].items():
        pb = rig.pose.bones[name]
        displacements.append(pb.matrix.to_3x3() @
                             (pb.custom_shape_translation - Vector(original[role])))
    assert all((d - displacements[0]).length < 1e-6 for d in displacements)
assert fixture.weights() == weights and limb_ik._armature_digest(rig) == rest
with tempfile.TemporaryDirectory(prefix='cd-eye-spacing-') as tmp:
    path = str(Path(tmp) / 'spacing.blend')
    name = rig.name
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
    rig = bpy.data.objects[name]
    assert eyes.display_spacing(rig) == distance
    assert bpy.ops.character_designer.eye_controls(action='SPACING', distance=0) == {'FINISHED'}
    for role, name in record['bones'].items():
        assert tuple(rig.pose.bones[name].custom_shape_translation) == original[role]
    for frame in frames:
        bpy.context.scene.frame_set(frame)
        fixture.update(rig)
        eyes._verify_pose(rig, before[frame])
    assert fixture.weights() == weights and limb_ik._armature_digest(rig) == rest
    # Only display-position animation blocks a display edit; pose keys above work.
    master = rig.pose.bones[record['master']]
    master.keyframe_insert(data_path='custom_shape_translation', frame=20)
    raw = rig.data[eyes.RECORD_KEY]
    fixture.expect_refusal(lambda: eyes.set_display_spacing(bpy.context, rig, distance), 'display positions')
    assert rig.data[eyes.RECORD_KEY] == raw
print('EYE_DISPLAY_SPACING_PASSED', flush=True)
