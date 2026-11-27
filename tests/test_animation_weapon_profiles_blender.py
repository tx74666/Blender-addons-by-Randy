"""Native asset import, calibration, ownership and persistence regression."""

from pathlib import Path
import json
import sys
import time

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index('--') + 1:]
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, args[1] if len(args) > 1 else str(root / 'addons'))
import character_designer
from character_designer import animation_weapon_profiles as weapon
from character_designer import animation_composition_weapon as display
from character_designer import animation_composition_local as local
from character_designer import animation_weapon_grip as grip

out = Path(args[0])
out.mkdir(exist_ok=False)
bpy.ops.wm.read_factory_settings(use_empty=True)
character_designer.register()
ctx = bpy.context
scene = ctx.scene
arm = bpy.data.armatures.new('Fixture Skeleton')
rig = bpy.data.objects.new('Fixture Character', arm)
scene.collection.objects.link(rig)
ctx.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')
for name, head, tail, parent in (
    ('LeftLeg', (.15, 0, 0), (.15, 0, -1), None),
    ('Spine', (0, 0, 0), (0, 0, 1), None),
    ('RightUpperArm', (0, 0, 1), (-0.4, 0, 1), 'Spine'),
    ('RightLowerArm', (-0.4, 0, 1), (-0.8, 0.15, 1), 'RightUpperArm'),
    ('RightHand', (-0.8, 0.15, 1), (-0.9, 0.15, 1), 'RightLowerArm'),
    ('LeftUpperArm', (0, 0, 1), (0.4, 0, 1), 'Spine'),
    ('LeftLowerArm', (0.4, 0, 1), (0.8, 0.15, 1), 'LeftUpperArm'),
    ('LeftHand', (0.8, 0.15, 1), (0.9, 0.15, 1), 'LeftLowerArm'),
):
    item = arm.edit_bones.new(name)
    item.head, item.tail = head, tail
    if parent:
        item.parent = arm.edit_bones[parent]
bpy.ops.object.mode_set(mode='OBJECT')
scene.frame_set(17, subframe=.25)
rig.pose.bones['RightHand'].rotation_mode = 'XYZ'
rig.pose.bones['RightHand'].rotation_euler.z = .3
rig.pose.bones['RightHand'].keyframe_insert(data_path='rotation_euler', frame=17)
active = rig.animation_data.action
slot = rig.animation_data.action_slot.handle
assert bpy.ops.character_designer.set_ui_page(page='EQUIPMENT') == {'FINISHED'}
assert character_designer.equipment_ui.CHARACTERDESIGNER_PT_equipment.poll(ctx)
assert not character_designer.animation.CHARACTERDESIGNER_PT_animation.poll(ctx)
assert bpy.ops.character_designer.set_ui_page(page='ANIMATION') == {'FINISHED'}
assert character_designer.animation.CHARACTERDESIGNER_PT_animation.poll(ctx)

mesh = bpy.data.meshes.new('Model')
mesh.from_pydata([(0, 0, 0), (.4, 0, 0), (0, .1, .1)], [], [(0, 1, 2)])
obj = bpy.data.objects.new('Model', mesh)
scene.collection.objects.link(obj)
ctx.view_layer.objects.active = obj
rig.select_set(False)
obj.select_set(True)
assert 'FINISHED' in bpy.ops.export_scene.fbx(filepath=str(out / 'mesh.fbx'), use_selection=True, bake_anim=False)
assert 'FINISHED' in bpy.ops.export_scene.gltf(filepath=str(out / 'mesh.gltf'), export_format='GLTF_SEPARATE', use_selection=True)
bpy.data.objects.remove(obj, do_unlink=True)
bpy.data.meshes.remove(mesh)
(out / 'mesh.obj').write_text('v 0 0 0\nv 0.4 0 0\nv 0 0.1 0.1\nf 1 2 3\n')
ctx.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode='POSE')
scene.frame_set(17, subframe=.25)
for ext in ('obj', 'fbx', 'gltf'):
    result = bpy.ops.character_designer.weapon_import(filepath=str(out / ('mesh.' + ext)))
    assert result == {'FINISHED'}, result
    profile = ctx.window_manager.character_designer_animation.weapon_profile
    assert weapon.is_profile(profile) and profile.asset_data and profile.use_fake_user
    assert len(profile.objects) == 1 and len(profile.objects[0].data.polygons) == 1
    assert ctx.mode == 'POSE' and ctx.object == rig and set(ctx.selected_objects) == {rig}
    assert (scene.frame_current, scene.frame_subframe) == (17, .25), (scene.frame_current, scene.frame_subframe)
assert rig.animation_data.action == active and rig.animation_data.action_slot.handle == slot
assert weapon.right_hand(rig) == 'RightHand'
bad = out / 'bad.obj'
bad.write_text('v 0 0 0\n')
objects_before = set(bpy.data.objects)
collections_before = set(bpy.data.collections)
try:
    weapon.import_profile(ctx, str(bad))
except (ValueError, RuntimeError):
    pass
else:
    raise AssertionError('Imported a file without surfaces.')
assert set(bpy.data.objects) == objects_before
assert set(bpy.data.collections) == collections_before
assert ctx.mode == 'POSE' and ctx.object == rig
assert bpy.ops.character_designer.weapon_attach(hand='RightHand') == {'FINISHED'}
assert bpy.ops.character_designer.weapon_calibrate(
    two_handed=True, mount_location=(.1, .2, .3), mount_rotation=(0, .5, 0),
    grip_location=(.3, .2, .1), grip_rotation=(.1, .2, .3)) == {'FINISHED'}
record, _ = display.validate_owned(rig)
def check_mount(rig, record):
    deps = bpy.context.evaluated_depsgraph_get()
    hand = rig.evaluated_get(deps).pose.bones['RightHand'].matrix
    hand_world = rig.evaluated_get(deps).matrix_world @ hand
    geometry = record['geometry_root'].evaluated_get(deps).matrix_world
    grip = record['grip'].evaluated_get(deps).matrix_world
    assert (geometry.translation - (hand_world @ record['geometry_root'].matrix_basis).translation).length < 1e-5
    assert (grip.translation - (geometry @ record['grip'].matrix_basis).translation).length < 1e-5
    assert all(abs(a-b) < 1e-5 for row_a, row_b in zip(grip, geometry @ record['grip'].matrix_basis)
               for a, b in zip(row_a, row_b))
    assert record['geometry_root'].instance_collection == record[weapon.LOCAL]
    assert record[weapon.LOCAL].get('two_handed')
    assert any(i.is_instance and i.parent and i.parent.original == record['geometry_root']
               for i in deps.object_instances)
check_mount(rig, record)
file = out / 'profile.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(file))
assert bpy.ops.wm.open_mainfile(filepath=str(file), use_scripts=True) == {'FINISHED'}
rig = bpy.data.objects['Fixture Character']
record, _ = display.validate_owned(rig)
check_mount(rig, record)
profile = record[weapon.LOCAL]
assert bpy.ops.character_designer.weapon_detach() == {'FINISHED'}
assert not weapon.attachment(rig) and weapon.is_profile(profile)
assert bpy.ops.character_designer.weapon_attach(profile_name=profile.name, hand='RightHand') == {'FINISHED'}
record, _ = display.validate_owned(rig)
check_mount(rig, record)
print('PASS: OBJ, FBX, glTF, failure rollback, ownership, mount, calibration, save/reopen, detach and reuse.')




def create_action(name, channels):
    action = bpy.data.actions.new(name)
    slot = action.slots.new('OBJECT', 'Fixture')
    strip = action.layers.new('Layer').strips.new(type='KEYFRAME')
    bag = strip.channelbag(slot, ensure=True)
    for bone, prop, index, points in channels:
        curve = bag.fcurves.new(rig.pose.bones[bone].path_from_id(prop), index=index)
        for frame, value in points:
            curve.keyframe_points.insert(frame, value).interpolation = 'LINEAR'
    return action, slot


base, base_slot = create_action('Run', [('LeftLeg', 'rotation_euler', 0, [(1, .2), (30, 1.0)]),
    ('RightUpperArm', 'rotation_euler', 2, [(1, 0), (30, 0)])])
overlay, overlay_slot = create_action('Aim', [('LeftLeg', 'rotation_euler', 0, [(1, 1.5), (10, -1.5)]),
    ('RightUpperArm', 'rotation_euler', 2, [(1, .25), (10, .25)])])
for bone in rig.pose.bones:
    bone.rotation_mode = 'XYZ'
rig.animation_data.action, rig.animation_data.action_slot = base, base_slot
bpy.context.scene.frame_set(9)
ctx = bpy.context
record = weapon.attachment(rig)
geometry = record['geometry_root'].evaluated_get(ctx.evaluated_depsgraph_get()).matrix_world
position = geometry.inverted() @ Vector((.56, .26, 1.))
assert bpy.ops.character_designer.weapon_calibrate(
    profile_name=record[weapon.LOCAL].name, two_handed=True, mount_location=(.1, .2, .3),
    mount_rotation=(0, .5, 0), grip_location=position, grip_rotation=(.1, .2, .3)) == {'FINISHED'}
assert bpy.ops.character_designer.weapon_grip() == {'FINISHED'}


def pose_values(frame):
    scene = bpy.context.scene
    scene.frame_set(frame)
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = rig.evaluated_get(deps)
    return (evaluated.pose.bones['LeftLeg'].rotation_euler.x,
            evaluated.pose.bones['RightUpperArm'].rotation_euler.z,
            evaluated.matrix_world @ evaluated.pose.bones['LeftHand'].head,
            record['grip'].evaluated_get(deps).matrix_world.translation)


def set_grip(weight):
    rig[grip.WEIGHT] = weight
    rig.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()


set_grip(0.)
free = pose_values(9)
set_grip(1.)
full = pose_values(9)
assert (full[2] - full[3]).length < .002, (full[2], full[3])
assert (free[2] - full[2]).length > .05
assert abs(free[0] - full[0]) < 1e-6
set_grip(0.)
expected_legs = {frame: pose_values(frame)[0] for frame in range(1, 31)}
assert bpy.ops.character_designer.weapon_overlay(action=overlay.name, frame=5, blend_in=2,
    blend_out=2, use_grip=True, grip_weight=1) == {'FINISHED'}
assert all(abs(expected - pose_values(frame)[0]) < 1e-6 for frame, expected in expected_legs.items())
assert abs(pose_values(3)[1]) < 1e-5 and abs(pose_values(18)[1]) < 1e-5
assert .05 < pose_values(6)[1] < .20
assert abs(pose_values(9)[1] - .25) < 1e-5
composed = pose_values(9)
assert (composed[2] - composed[3]).length < .002, composed
assert len(local.tracks_for(rig, exact=True)) == 2
assert len(local.composition._curves(overlay, overlay_slot.handle)) == 2
bpy.ops.wm.save_as_mainfile(filepath=str(out / 'overlay.blend'))
assert bpy.ops.wm.open_mainfile(filepath=str(out / 'overlay.blend'), use_scripts=True) == {'FINISHED'}
rig = bpy.data.objects['Fixture Character']
record = weapon.attachment(rig)
assert len(local.tracks_for(rig, exact=True)) == 2
assert abs(pose_values(9)[1] - .25) < 1e-5

from character_designer import animation_export
export_path = out / 'Run Aim.fbx'
assert bpy.ops.character_designer.animation_export(
    filepath=str(export_path), rig_name=rig.name, frame_start=1, frame_end=30,
    composition=True) == {'FINISHED'}
job = animation_export.active_job()
job['process'].wait(timeout=60)
while (result := animation_export.poll_export(job)) is None:
    time.sleep(.01)
assert result['composition_tracks'] and len(result['composition_tracks']) == 2
assert result['maximum_bake_matrix_error'] < 2e-5
assert Path(result['filepath']).is_file()
assert (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe) == (9, 0.)
assert len(local.tracks_for(rig, exact=True)) == 2
(out / 'result.json').write_text(json.dumps(result, indent=2))

assert weapon.attachment(rig)['scene'] == bpy.context.scene
assert bpy.data.actions['Aim'].use_fake_user
assert bpy.ops.character_designer.weapon_close_overlay() == {'FINISHED'}
assert rig.animation_data.action.name == 'Run' and len(rig.animation_data.nla_tracks) == 0
assert bpy.ops.character_designer.weapon_detach() == {'FINISHED'}
assert len(rig.animation_data.drivers) == 0
print('PASS: left-hand grip, grip animation, lower-body preservation, fades, original overlay, reopen, export and close.')

character_designer.animation._poll_action_export()
assert bpy.ops.character_designer.weapon_overlay(action='Aim', frame=27, blend_in=1,
    blend_out=0, hold=True, loop_base=True) == {'FINISHED'}
tracks = local.tracks_for(rig, exact=True)
assert tracks[0].strips[0].repeat == 2
assert tracks[1].strips[0].extrapolation == 'HOLD_FORWARD'
bpy.context.scene.frame_set(33)
evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
assert abs(evaluated.pose.bones['LeftLeg'].rotation_euler.x - expected_legs[4]) < 1e-6
assert bpy.ops.character_designer.weapon_overlay(action='Aim', frame=35,
    blend_in=2, blend_out=2) == {'FINISHED'}
assert len(local.tracks_for(rig, exact=True)) == 3
print('PASS: looping Base, persistent hold and subsequent overlay.')
