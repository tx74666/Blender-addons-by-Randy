"""Public spine actions and coexistence with existing limb controls."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from character_designer import bone_collections, torso_controls, limb_ik, limb_ik_fk
import character_designer
import test_limb_ik_blender as base


character_designer.register()
for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
    base.reset_scene()
    rig = base.make_humanoid()
    bpy.ops.object.mode_set(mode='EDIT')
    bones = rig.data.edit_bones
    spine = bones['Chest']
    spine.name, spine.tail.z = 'spine', 1.28
    chest = base.add_bone(bones, 'Chest', spine.tail.copy(), (0, 0, 1.42), spine)
    upper = base.add_bone(bones, 'UpperChest', chest.tail.copy(), (0, 0, 1.55), chest)
    for side in ('L', 'R'):
        bones['shoulder.' + side].parent = upper
    bpy.ops.object.mode_set(mode='POSE')
    _, settings = base.analyze(rig)
    settings.build_method, settings.selected_limb = method, 'LEFT_LEG'
    if method == 'DIRECT_PREROLL':
        assert bpy.ops.character_designer.limb_ik_direct_preroll_check() == {'FINISHED'}
    assert bpy.ops.character_designer.limb_ik_build_selected() == {'FINISHED'}
    names = [b.name for b in rig.data.bones if not b.get(limb_ik.OWNER_KEY)]
    before = limb_ik_fk._matrices(rig, names)
    digest = limb_ik._armature_digest(rig)
    assert bpy.ops.character_designer.torso_controls(action='BUILD') == {'FINISHED'}
    record = torso_controls.get_record(rig)
    assert record['sources'] == ['spine', 'Chest', 'UpperChest']
    assert limb_ik._armature_digest(rig) == digest
    limb_ik_fk._verify(rig, before)
    assert bpy.ops.character_designer.torso_controls(action='SELECT', bone=record['bend']) == {'FINISHED'}
    assert rig.data.bones.active.name == record['bend']
    # Additional limbs can still be built without reanalyzing the unchanged native skeleton.
    settings.selected_limb = 'RIGHT_LEG'
    if method == 'DIRECT_PREROLL':
        assert bpy.ops.character_designer.limb_ik_direct_preroll_check() == {'FINISHED'}
    assert bpy.ops.character_designer.limb_ik_build_selected() == {'FINISHED'}
    # Direct Pre-Roll legitimately aligns the newly added right leg's rest roll.
    removal_digest = limb_ik._armature_digest(rig)
    rig.pose.bones[record['bend']].rotation_euler.x = 0.21
    limb_ik_fk._update(bpy.context, rig)
    assert base.cancelled_result(bpy.ops.character_designer.limb_ik_rebuild) == {'CANCELLED'}
    assert base.cancelled_result(lambda: bpy.ops.character_designer.limb_ik_remove('EXEC_DEFAULT')) == {'CANCELLED'}
    desired = limb_ik_fk._matrices(rig, names)
    assert bpy.ops.character_designer.torso_controls(action='REMOVE') == {'FINISHED'}
    limb_ik_fk._verify(rig, desired)
    assert set(record['sources']) <= {b.name for b in rig.data.collections['Animation'].bones}
    assert limb_ik._armature_digest(rig) == removal_digest
    limb_ik._validate_inventory(rig)
    print('PASS SPINE_UI', method, flush=True)
print('SPINE_UI_PASSED', flush=True)
