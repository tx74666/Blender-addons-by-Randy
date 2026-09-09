"""Public spine matching actions, current-mode selection and shared collections."""
import sys
from types import SimpleNamespace
from pathlib import Path

import bpy
from mathutils import Matrix

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'addons'), str(Path(__file__).resolve().parent)]
import character_designer
from character_designer import spine_ik_fk as spine, torso_controls, torso_ui, limb_ik
import test_spine_ik_fk_blender as fixtures
import test_limb_ik_blender as base


character_designer.register()
rig, chain, torso = fixtures.fixture(posed=True)
assert bpy.ops.character_designer.limb_ik_analyze() == {'FINISHED'}
original = fixtures.native(rig)
assert bpy.ops.character_designer.spine_ik_fk(action='BUILD') == {'FINISHED'}
record = spine.validate(rig)
assert spine.mode_for_rig(rig) == 'FK'
assert bpy.ops.character_designer.spine_ik_fk(action='BUILD') == {'FINISHED'}
assert spine.get_record(rig) == record
spine._verify_pose(rig, original)
assert bpy.ops.character_designer.spine_ik_fk(action='SWITCH', mode='IK') == {'FINISHED'}
assert rig.data.bones.active.name == record['chest']
assert spine.mode_for_rig(rig) == 'IK'
assert {record['chest'], record['shape']} <= set(rig.data.collections['Animation'].bones.keys())
spine._verify_pose(rig, original)
for name in (record['chest'], record['shape']):
    assert bpy.ops.character_designer.spine_ik_fk(action='SELECT', bone=name) == {'FINISHED'}
    assert {pb.name for pb in rig.pose.bones if pb.select} == {name}
    color = rig.pose.bones[name].color
    assert color.palette == 'CUSTOM' and max(color.custom.normal)-min(color.custom.normal) > .05
assert base.cancelled_result(lambda: bpy.ops.character_designer.spine_ik_fk(action='SELECT', bone='Hips')) == {'CANCELLED'}
rig.pose.bones[record['chest']].location.y -= .025
rig.pose.bones[record['shape']].rotation_euler.x += .06
desired = fixtures.native(rig)
bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
assert base.cancelled_result(lambda: bpy.ops.character_designer.spine_ik_fk(action='SWITCH', mode='FK')) == {'CANCELLED'}
spine._verify_pose(rig, desired)
assert spine.mode_for_rig(rig) == 'IK'
bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
rig.pose.bones[record['chest']][spine.PROPERTY] = .5
desired = fixtures.native(rig)
class Layout:
    def __init__(self):
        self.labels, self.buttons = [], []
    def row(self, **kwargs): return self
    def box(self): return self
    def label(self, **kwargs): self.labels.append(kwargs.get('text', ''))
    def operator(self, identifier, **kwargs):
        self.buttons.append(kwargs.get('text', ''))
        return SimpleNamespace()
layout = Layout()
torso_ui.CHARACTERDESIGNER_PT_torso_controls.draw(SimpleNamespace(layout=layout), bpy.context)
assert any('Blended pose (0.5)' in text for text in layout.labels)
assert {'Match to FK', 'Match to IK'} <= set(layout.buttons)
assert bpy.ops.character_designer.spine_ik_fk(action='SWITCH', mode='FK') == {'FINISHED'}
spine._verify_pose(rig, desired)
assert rig.data.bones.active.name == record['fk_controls'][chain[-1]]
assert base.cancelled_result(lambda: bpy.ops.character_designer.torso_controls(action='REMOVE')) == {'CANCELLED'}
assert base.cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) == {'CANCELLED'}
hips_before = rig.pose.bones['Hips'].matrix.copy()
assert bpy.ops.character_designer.spine_ik_fk(action='RESET') == {'FINISHED'}
assert spine.mode_for_rig(rig) == 'FK'
assert rig.pose.bones[torso['bend']].matrix_basis == Matrix.Identity(4)
spine._verify_pose(rig, {'Hips': hips_before})
desired = fixtures.native(rig)
assert bpy.ops.character_designer.spine_ik_fk(action='REMOVE') == {'FINISHED'}
spine._verify_pose(rig, desired)
assert rig.data.bones.active.name == torso['bend']
assert spine.get_record(rig) is None and torso_controls.get_record(rig) == torso
limb_ik._validate_inventory(rig)
print('SPINE_IK_FK_UI_PASSED', flush=True)
