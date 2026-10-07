"""Independent managed-Pose mirror checks in an isolated Blender process.

The oracle reflects evaluated skin deformation (Pose @ Rest.inverse), never
the production mirror helper or its desired-pose conversion. The right lower
arm and wrist deliberately have an additional pi of Rest roll.
"""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import control_pose_assets as poses
from character_designer import control_pose_mirror as mirror

REFLECTION = Matrix.Diagonal((-1., 1., 1., 1.))
PARTS = ('shoulder', 'upper_arm', 'forearm', 'hand')
TOLERANCE = 8e-6


def matrix_rows(matrix):
    return tuple(tuple(row) for row in matrix)


def matrix_error(left, right):
    return max(abs(a - b) for x, y in zip(left, right) for a, b in zip(x, y))


def update(rig):
    rig.update_tag(refresh={'OBJECT', 'DATA', 'TIME'})
    bpy.context.view_layer.update()


def evaluated_matrices(rig):
    update(rig)
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {bone.name: bone.matrix.copy() for bone in evaluated.pose.bones}


def skin_matrices(rig):
    return {name: matrix @ rig.data.bones[name].matrix_local.inverted()
            for name, matrix in evaluated_matrices(rig).items()}


def mesh_points(mesh):
    bpy.context.view_layer.update()
    obj = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return [vertex.co.copy() for vertex in obj.data.vertices]


def full_fields(rotation=(0., 0., 0.), *, location=(0., 0., 0.), scale=(1., 1., 1.)):
    quaternion = Euler(rotation, 'XYZ').to_quaternion()
    return {prop: dict(enumerate(value)) for prop, value in
            (('location', location), ('rotation_quaternion', quaternion), ('scale', scale))}


def set_fields(rig, values):
    """Use Blender's native channels/evaluation, independent of Pose matching."""
    for name, fields in values.items():
        bone = rig.pose.bones[name]
        bone.rotation_mode = 'QUATERNION'
        for prop, entries in fields.items():
            for index, value in entries.items():
                getattr(bone, prop)[index] = value
    update(rig)


def managed_action(rig, values):
    action = bpy.data.actions.new('Managed deformation mirror evidence')
    slot = action.slots.new(id_type='OBJECT', name=rig.name)
    layer = action.layers.new(name='Pose')
    bag = layer.strips.new(type='KEYFRAME').channelbag(slot, ensure=True)
    for name, fields in values.items():
        for prop, entries in fields.items():
            for index, value in entries.items():
                curve = bag.fcurves.new(data_path=rig.pose.bones[name].path_from_id(prop), index=index)
                curve.keyframe_points.insert(1., float(value)).interpolation = 'CONSTANT'
    metadata = {'version': 1, 'source_object': rig.name, 'source_slot': slot.identifier,
                'rest': poses.native_rest(rig), 'scope': 'SELECTED',
                'names': sorted(values), 'include_fingers': False, 'control_modes': {}}
    action[poses.ASSET_METADATA] = json.dumps(metadata, separators=(',', ':'))
    action.asset_mark()
    action.use_fake_user = True
    return action


def action_state(action, rig):
    return (action.as_pointer(), action.name, action.use_fake_user,
            action[poses.ASSET_METADATA],
            tuple((curve.data_path, curve.array_index, curve.mute,
                   tuple((tuple(point.co), point.interpolation,
                          tuple(point.handle_left), tuple(point.handle_right))
                         for point in curve.keyframe_points))
                  for curve in poses._curves(action, rig)))


def rig_state(rig):
    update(rig)
    return (poses.native_rest(rig),
            tuple((bone.name, bone.rotation_mode, tuple(bone.location),
                   tuple(bone.rotation_euler), tuple(bone.rotation_quaternion),
                   tuple(bone.rotation_axis_angle), tuple(bone.scale),
                   matrix_rows(bone.matrix_basis), matrix_rows(bone.matrix))
                  for bone in rig.pose.bones),
            tuple(sorted(bpy.data.actions.keys())),
            bpy.context.mode, bpy.context.scene.frame_current,
            bpy.context.scene.tool_settings.use_keyframe_insert_auto)


def fixture():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    data = bpy.data.armatures.new('Managed mirror native skeleton')
    rig = bpy.data.objects.new('Managed mirror native skeleton', data)
    bpy.context.scene.collection.objects.link(rig)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    hips = data.edit_bones.new('Hips')
    hips.head, hips.tail = (0., 0., 0.), (0., 0., 1.)
    left_points = (((.12, 0., .8), (.32, 0., .8)),
                   ((.32, 0., .8), (.72, .12, .65)),
                   ((.72, .12, .65), (1.08, .08, .42)),
                   ((1.08, .08, .42), (1.24, .10, .40)))
    for side in 'LR':
        parent = hips
        for part, (head, tail) in zip(PARTS, left_points):
            bone = data.edit_bones.new(part + '.' + side)
            bone.head = Vector(head) if side == 'L' else REFLECTION @ Vector(head)
            bone.tail = Vector(tail) if side == 'L' else REFLECTION @ Vector(tail)
            bone.parent = parent
            bone.use_connect = part != 'shoulder'
            if side == 'L':
                bone.align_roll(Vector((0., 0., 1.)))
            else:
                bone.align_roll(REFLECTION.to_3x3() @ data.edit_bones[part + '.L'].z_axis)
                if part in {'forearm', 'hand'}:
                    bone.roll += math.pi
            parent = bone
    bpy.ops.object.mode_set(mode='OBJECT')
    for bone in rig.pose.bones:
        bone.rotation_mode = 'QUATERNION'
    update(rig)

    points, pairs = [], []
    for part in PARTS:
        bone = data.bones[part + '.L']
        for offset in ((.025, bone.length * .35, .018),
                       (-.018, bone.length * .70, -.022)):
            left = bone.matrix_local @ Vector(offset)
            index = len(points)
            points.extend((left, REFLECTION @ left))
            pairs.append((index, index + 1, part))
    mesh_data = bpy.data.meshes.new('Managed mirror paired skin points')
    mesh_data.from_pydata(points, [], [])
    mesh = bpy.data.objects.new('Managed mirror paired skin points', mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.modifiers.new('Native skin evaluation', 'ARMATURE').object = rig
    for part in PARTS:
        for side, pair_index in (('L', 0), ('R', 1)):
            group = mesh.vertex_groups.new(name=part + '.' + side)
            group.add([pair[pair_index] for pair in pairs if pair[2] == part], 1., 'REPLACE')
    update(rig)
    return rig, mesh, pairs


class ManagedDeformationMirror(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        bpy.context.scene.frame_set(1)
        self.actions_before = set(bpy.data.actions.keys())
        self.rig, self.mesh, self.pairs = fixture()

    def tearDown(self):
        for action in list(bpy.data.actions):
            if action.name not in self.actions_before:
                bpy.data.actions.remove(action)

    def assertMatrix(self, left, right, message=''):
        self.assertLess(matrix_error(left, right), TOLERANCE, message)

    def mirror_read_only(self, values):
        action = managed_action(self.rig, values)
        metadata = poses.asset_metadata(action)
        channels = poses.channels(action, self.rig)
        original = copy.deepcopy(channels)
        before = rig_state(self.rig), action_state(action, self.rig)
        flipped = mirror.mirrored_channels(self.rig, channels, metadata=metadata)
        self.assertEqual(channels, original)
        self.assertEqual(before, (rig_state(self.rig), action_state(action, self.rig)))
        return flipped

    def assert_rejected_read_only(self, values):
        action = managed_action(self.rig, values)
        metadata = poses.asset_metadata(action)
        channels = poses.channels(action, self.rig)
        original = copy.deepcopy(channels)
        before = rig_state(self.rig), action_state(action, self.rig)
        with self.assertRaises(ValueError):
            mirror.mirrored_channels(self.rig, channels, metadata=metadata)
        self.assertEqual(channels, original)
        self.assertEqual(before, (rig_state(self.rig), action_state(action, self.rig)))

    def edit(self, callback):
        bpy.ops.object.mode_set(mode='EDIT')
        callback(self.rig.data.edit_bones)
        bpy.ops.object.mode_set(mode='OBJECT')
        update(self.rig)

    def test_complete_neutral_chain_keeps_identity_skin_despite_pi_roll(self):
        values = {part + '.L': full_fields() for part in PARTS}
        before_points = mesh_points(self.mesh)
        flipped = self.mirror_read_only(values)
        self.assertEqual(set(flipped), {part + '.R' for part in PARTS})
        set_fields(self.rig, flipped)
        skin = skin_matrices(self.rig)
        for part in PARTS:
            self.assertMatrix(skin[part + '.R'], Matrix.Identity(4), part)
            self.assertMatrix(self.rig.pose.bones[part + '.R'].matrix_basis,
                              Matrix.Identity(4), part)
        for before, after in zip(before_points, mesh_points(self.mesh)):
            self.assertLess((before - after).length, TOLERANCE)

    def test_complete_posed_chain_mirrors_independent_skin_and_mesh_oracles(self):
        rotations = ((.09, -.05, .11), (-.35, .18, -.12), (.42, .08, -.16), (.21, -.17, .14))
        values = {part + '.L': full_fields(rotation)
                  for part, rotation in zip(PARTS, rotations)}
        set_fields(self.rig, values)
        source_skin = skin_matrices(self.rig)
        source_pose = evaluated_matrices(self.rig)
        source_points = mesh_points(self.mesh)
        flipped = self.mirror_read_only(values)
        set_fields(self.rig, flipped)
        result = skin_matrices(self.rig)
        for part in PARTS:
            self.assertMatrix(result[part + '.R'],
                              REFLECTION @ source_skin[part + '.L'] @ REFLECTION, part)
            self.assertMatrix(evaluated_matrices(self.rig)[part + '.L'],
                              source_pose[part + '.L'], 'Unchanged source ' + part)
        result_points = mesh_points(self.mesh)
        for left, right, part in self.pairs:
            self.assertLess((result_points[right] - REFLECTION @ source_points[left]).length,
                            TOLERANCE, part)
            self.assertLess((result_points[left] - source_points[left]).length, TOLERANCE, part)

    def test_hand_only_mirrors_local_deformation_without_copying_either_parent_pose(self):
        parents = {'upper_arm.L': full_fields((.18, -.11, .09)),
                   'forearm.L': full_fields((.22, -.13, .14)),
                   'upper_arm.R': full_fields((.08, .24, -.12)),
                   'forearm.R': full_fields((-.19, .17, .07))}
        values = {'hand.L': full_fields((.25, -.13, .16))}
        set_fields(self.rig, parents | values)
        source_skin = skin_matrices(self.rig)
        local_oracle = source_skin['forearm.L'].inverted() @ source_skin['hand.L']
        # The Action is complete: the live source wrist may now differ from it.
        action = managed_action(self.rig, values)
        set_fields(self.rig, {'hand.L': full_fields((-.51, .38, .06))})
        unchanged = evaluated_matrices(self.rig)
        before = rig_state(self.rig), action_state(action, self.rig)
        flipped = mirror.mirrored_channels(self.rig, poses.channels(action, self.rig),
                                          metadata=poses.asset_metadata(action))
        self.assertEqual(before, (rig_state(self.rig), action_state(action, self.rig)))
        self.assertEqual(set(flipped), {'hand.R'})
        set_fields(self.rig, flipped)
        result = skin_matrices(self.rig)
        self.assertMatrix(result['forearm.R'].inverted() @ result['hand.R'],
                          REFLECTION @ local_oracle @ REFLECTION)
        current = evaluated_matrices(self.rig)
        for name in ('upper_arm.L', 'forearm.L', 'hand.L', 'upper_arm.R', 'forearm.R', 'Hips'):
            self.assertMatrix(current[name], unchanged[name], name)

    def test_center_bone_reflects_skin_without_a_side_name(self):
        values = {'Hips': full_fields((.12, .08, -.07), location=(.02, -.03, .01),
                                     scale=(1.04, 1.04, 1.04))}
        set_fields(self.rig, values)
        expected = REFLECTION @ skin_matrices(self.rig)['Hips'] @ REFLECTION
        flipped = self.mirror_read_only(values)
        self.assertEqual(set(flipped), {'Hips'})
        set_fields(self.rig, flipped)
        self.assertMatrix(skin_matrices(self.rig)['Hips'], expected)

    def test_nonstandard_inheritance_refuses_without_action_or_rig_mutation(self):
        for property_name, value in (('inherit_scale', 'NONE'),
                                     ('use_inherit_rotation', False),
                                     ('use_local_location', False)):
            with self.subTest(property_name=property_name):
                bone = self.rig.data.bones['hand.R']
                previous = getattr(bone, property_name)
                setattr(bone, property_name, value)
                self.assert_rejected_read_only({'hand.L': full_fields((.1, -.2, .3))})
                setattr(bone, property_name, previous)
                update(self.rig)

    def test_asymmetric_rest_head_refuses_without_action_or_rig_mutation(self):
        def displace(bones):
            bone = bones['shoulder.R']
            bone.head += Vector((.003, 0., 0.))
            bone.tail += Vector((.003, 0., 0.))
        self.edit(displace)
        self.assert_rejected_read_only({'shoulder.L': full_fields((.1, -.2, .3))})

    def test_connected_local_displacement_refuses_without_action_or_rig_mutation(self):
        self.assert_rejected_read_only({'hand.L': full_fields((.1, -.2, .3),
                                                             location=(.003, 0., 0.))})

    def test_incompatible_connection_refuses_without_action_or_rig_mutation(self):
        self.edit(lambda bones: setattr(bones['hand.R'], 'use_connect', False))
        self.assert_rejected_read_only({'hand.L': full_fields((.1, -.2, .3))})

    def test_unrepresentable_shear_refuses_without_action_or_rig_mutation(self):
        def offset_roll(bones):
            bones['hand.R'].roll += .37
        self.edit(offset_roll)
        self.assert_rejected_read_only({'hand.L': full_fields((.23, -.17, .11),
                                                             scale=(1.12, .81, 1.03))})

    def test_incomplete_managed_transforms_refuse_without_action_or_rig_mutation(self):
        for missing_property, missing_index in (('location', 1), ('rotation_quaternion', 2),
                                                ('scale', 0), ('rotation_quaternion', None)):
            with self.subTest(property=missing_property, index=missing_index):
                fields = full_fields((.1, -.2, .3))
                if missing_index is None:
                    del fields[missing_property]
                else:
                    del fields[missing_property][missing_index]
                self.assert_rejected_read_only({'hand.L': fields})


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False, verbosity=2).result
    if not result.wasSuccessful():
        raise RuntimeError('Managed deformation mirror integration tests failed')
