"""Native BVH preview isolation at positive and negative fractional times.

The stage wrapper must load the explicit Character Designer package first.
This module never changes the active product import path. Only two generated
bones, a preexisting keyed Action/slot and an original tiny local BVH are used;
no network, model runtime, artist file, scene save or preference save is used.
"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy
from mathutils import Quaternion

from character_designer import animation
from io_anim_bvh import import_bvh


BVH = """HIERARCHY
ROOT Hips
{
 OFFSET 0 100 0
 CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation
 JOINT Spine1
 {
  OFFSET 0 20 0
  CHANNELS 3 Zrotation Xrotation Yrotation
  End Site
  {
   OFFSET 0 20 0
  }
 }
}
MOTION
Frames: 3
Frame Time: 0.03333333333333333
0 100 0 0 0 0 0 0 0
1 102 7 12 -8 4 9 3 -2
3 104 20 28 -12 11 17 6 -5
"""


def identity(value):
    return None if value is None else (value.as_pointer(),
        getattr(value, 'name_full', getattr(value, 'name', getattr(value, 'identifier', None))))


def matrix_values(value):
    return tuple(float(component) for row in value for component in row)


def native_selection(bone):
    return tuple((name, bool(getattr(bone, name)))
        for name in ('select', 'select_head', 'select_tail')
        if bone.bl_rna.properties.get(name) is not None)


def action_state(action):
    """Keep the actual layered Action/slot/FCurve contents in the witness."""
    layers = []
    for layer in action.layers:
        strips = []
        for strip in layer.strips:
            bags = []
            for bag in getattr(strip, 'channelbags', ()):
                curves = tuple((curve.as_pointer(), curve.data_path, curve.array_index,
                    curve.extrapolation, curve.mute, curve.lock,
                    tuple((tuple(key.co), tuple(key.handle_left), tuple(key.handle_right),
                        key.handle_left_type, key.handle_right_type, key.interpolation,
                        key.easing, key.type) for key in curve.keyframe_points))
                    for curve in bag.fcurves)
                bags.append((bag.as_pointer(), bag.slot_handle, curves))
            strips.append((strip.as_pointer(), strip.type, tuple(bags)))
        layers.append((layer.as_pointer(), layer.name, tuple(strips)))
    return (identity(action), action.use_fake_user, action.users,
        tuple((slot.as_pointer(), slot.handle, slot.identifier, slot.target_id_type)
              for slot in action.slots), tuple(layers))


def pose_state(rig):
    return tuple((bone.name, bone.rotation_mode,
        tuple(bone.location), tuple(bone.rotation_euler), tuple(bone.rotation_quaternion),
        tuple(bone.rotation_axis_angle), tuple(bone.scale), matrix_values(bone.matrix_basis),
        matrix_values(bone.matrix), tuple(bone.lock_location), tuple(bone.lock_rotation),
        tuple(bone.lock_scale), native_selection(bone), native_selection(bone.bone))
        for bone in rig.pose.bones)


def author_context(rig, companion):
    scene, ad = bpy.context.scene, rig.animation_data
    return dict(
        time=(scene.frame_current, scene.frame_subframe),
        timing=(scene.render.fps, scene.render.fps_base, scene.frame_start, scene.frame_end,
            scene.use_preview_range, scene.frame_preview_start, scene.frame_preview_end),
        autokey=scene.tool_settings.use_keyframe_insert_auto,
        selected=tuple(sorted(obj.as_pointer() for obj in bpy.context.selected_objects)),
        active=identity(bpy.context.view_layer.objects.active), mode=rig.mode,
        active_bone=identity(rig.data.bones.active),
        rig=(identity(rig), identity(rig.data), rig.data.pose_position,
            tuple(rig.location), tuple(rig.rotation_euler), tuple(rig.scale),
            matrix_values(rig.matrix_world),
            tuple((bone.name, identity(bone.parent), tuple(bone.head_local),
                tuple(bone.tail_local), matrix_values(bone.matrix_local), bone.use_deform)
                for bone in rig.data.bones)),
        companion=(identity(companion), tuple(companion.location),
            tuple(companion.rotation_euler), tuple(companion.scale)),
        animation=(identity(ad.action), identity(ad.action_slot), ad.action_slot_handle,
            ad.use_nla, ad.action_blend_type, ad.action_extrapolation,
            ad.action_influence, ad.use_tweak_mode),
        action=action_state(ad.action), pose=pose_state(rig))


def inventories():
    return {name: tuple(sorted((item.as_pointer(), item.name_full) for item in getattr(bpy.data, name)))
        for name in ('objects', 'armatures', 'actions', 'collections', 'meshes')}


def make_fixture(frame, subframe):
    if not bpy.app.background or bpy.data.filepath:
        raise RuntimeError('Motion preview tests require a fresh private background Blender')
    if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    # This suite owns its fresh fixture scene. It never opens an artist file.
    for obj in tuple(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in (bpy.data.armatures, bpy.data.actions, bpy.data.meshes):
        for item in tuple(collection):
            if item.users == 0:
                collection.remove(item)
    scene = bpy.context.scene
    scene.tool_settings.use_keyframe_insert_auto = False
    data = bpy.data.armatures.new('Preserved fractional rig data')
    rig = bpy.data.objects.new('Preserved fractional rig', data)
    scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    root = data.edit_bones.new('Root')
    root.head, root.tail = (0., 0., 0.), (0., 0., 1.)
    child = data.edit_bones.new('Child')
    child.head, child.tail, child.parent = (0., 0., 1.), (0., 0., 2.), root
    bpy.ops.object.mode_set(mode='OBJECT')
    root, child = rig.pose.bones['Root'], rig.pose.bones['Child']
    root.rotation_mode, child.rotation_mode = 'QUATERNION', 'XYZ'
    for keyed_frame, factor in ((-30, -1.), (30, 1.)):
        scene.frame_set(keyed_frame)
        rig.location = (.2 * factor, -.13 * factor, .07 * factor)
        rig.keyframe_insert(data_path='location', frame=keyed_frame)
        root.location = (.11 * factor, -.23 * factor, .17 * factor)
        root.rotation_quaternion = Quaternion((0., 1., 0.), .45 * factor)
        root.keyframe_insert(data_path='location', frame=keyed_frame)
        root.keyframe_insert(data_path='rotation_quaternion', frame=keyed_frame)
        child.rotation_euler = (.23 * factor, -.31 * factor, .19 * factor)
        child.keyframe_insert(data_path='rotation_euler', frame=keyed_frame)
    action = rig.animation_data.action
    action.name = 'Preserved fractional Action'
    action.slots.new(id_type='OBJECT', name='Preserved unbound slot')
    for layer in action.layers:
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                for curve in bag.fcurves:
                    for point in curve.keyframe_points:
                        point.interpolation = 'LINEAR'
    root.lock_location = (True, False, True)
    child.lock_rotation = (False, True, False)
    companion = bpy.data.objects.new('Preserved companion selection', None)
    scene.collection.objects.link(companion)
    companion.location = (1.25, -.5, .375)
    companion.select_set(True)
    data.bones.active = data.bones['Child']
    for pose in rig.pose.bones:
        owner = pose if pose.bl_rna.properties.get('select') is not None else pose.bone
        owner.select = pose.name == 'Child'
    bpy.ops.object.mode_set(mode='POSE')
    scene.render.fps, scene.render.fps_base = 48, 1.001
    scene.frame_start, scene.frame_end = -50, 70
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 2, 9
    scene.frame_set(frame, subframe=subframe)
    bpy.context.view_layer.update()
    scene.tool_settings.use_keyframe_insert_auto = True
    return rig, companion


class MotionPreviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='cd-tiny-motion-preview-')
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'authored_tiny_preview.bvh'
        self.path.write_text(BVH, encoding='utf-8')

    def test_actual_bvh_success_keeps_fractional_author_context_and_separate_preview(self):
        for frame, subframe in ((12, .375), (-13, .625)):
            with self.subTest(frame=frame, subframe=subframe):
                rig, companion = make_fixture(frame, subframe)
                before, old_ids = author_context(rig, companion), inventories()
                source = animation.import_motion_preview(bpy.context, self.path, 4)
                self.assertEqual(author_context(rig, companion), before)
                self.assertEqual(source.type, 'ARMATURE')
                self.assertTrue(source.get('character_designer_motion_preview'))
                self.assertTrue(source.show_in_front)
                self.assertIsNot(source, rig)
                self.assertNotEqual(source.data.as_pointer(), rig.data.as_pointer())
                self.assertNotEqual(source.animation_data.action.as_pointer(), rig.animation_data.action.as_pointer())
                self.assertIsNotNone(source.animation_data.action_slot)
                self.assertEqual(set(source.data.bones.keys()), {'Hips', 'Spine1'})
                after = inventories()
                for name in ('objects', 'armatures', 'actions'):
                    self.assertEqual(len(after[name]), len(old_ids[name]) + 1)
                    self.assertTrue(set(old_ids[name]).issubset(after[name]))
                self.assertEqual(after['meshes'], old_ids['meshes'])
                self.assertEqual(after['collections'], old_ids['collections'])
                preview_action = source.animation_data.action
                self.assertGreater(preview_action.frame_range[1], preview_action.frame_range[0])
                self.assertAlmostEqual(preview_action.frame_range[0], 4., places=5)
                # Preserve the new preview while proving its animation is real;
                # the original fractional scene position is restored afterwards.
                saved_time = before['time']
                try:
                    bpy.context.scene.frame_set(4)
                    bpy.context.view_layer.update()
                    first = pose_state(source)
                    bpy.context.scene.frame_set(7)
                    bpy.context.view_layer.update()
                    self.assertNotEqual(pose_state(source), first)
                finally:
                    restore_frame, restore_subframe = saved_time
                    if restore_subframe < 0.0:
                        restore_frame -= 1
                        restore_subframe += 1.0
                    bpy.context.scene.frame_set(restore_frame, subframe=restore_subframe)
                    bpy.context.view_layer.update()
                self.assertEqual(author_context(rig, companion), before)

    def test_actual_bvh_then_loader_failure_removes_only_owned_ids_and_restores_fraction(self):
        actual_load = import_bvh.load
        for frame, subframe in ((12, .375), (-13, .625)):
            with self.subTest(frame=frame, subframe=subframe):
                rig, companion = make_fixture(frame, subframe)
                before, old_ids = author_context(rig, companion), inventories()
                created = {}

                def load_then_fail(*args, **kwargs):
                    result = actual_load(*args, **kwargs)
                    created.update({name: tuple(set(inventories()[name]) - set(old_ids[name]))
                                    for name in ('objects', 'armatures', 'actions')})
                    self.assertTrue(all(len(rows) == 1 for rows in created.values()),
                        'Fault injection must follow the real native BVH allocation')
                    self.assertEqual(result, {'FINISHED'})
                    raise RuntimeError('injected failure after real tiny BVH import')

                with patch.object(import_bvh, 'load', side_effect=load_then_fail) as loader:
                    with self.assertRaisesRegex(RuntimeError, 'after real tiny BVH import'):
                        animation.import_motion_preview(bpy.context, self.path, 4)
                    self.assertEqual(loader.call_count, 1)
                self.assertEqual(set(created), {'objects', 'armatures', 'actions'})
                self.assertEqual(inventories(), old_ids,
                    'Failed import retained a temporary ID or removed a preexisting one')
                self.assertEqual(author_context(rig, companion), before)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
