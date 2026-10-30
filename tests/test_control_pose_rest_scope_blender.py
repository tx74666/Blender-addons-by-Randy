"""Native regional Pose proof, including strict Original and reached rollback."""
from copy import deepcopy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import control_pose_assets as poses, body_original_mode as original
from character_designer import body_setup, limb_ik, limb_ik_fk
import test_body_calibration_blender as setup


class RegionalPoseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        self.rig, self.mesh = setup.fixture()
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        bpy.ops.object.mode_set(mode='EDIT')
        bones = self.rig.data.edit_bones
        parent = bones['hand.L']
        for index in range(1, 4):
            bone = bones.new(f'f_index.{index:02d}.L')
            bone.head = parent.tail
            bone.tail = bone.head + Vector((.06, -.005, 0.))
            bone.parent, bone.use_connect = parent, True
            parent = bone
        neck = bones.new('Neck')
        neck.head, neck.tail = (0., 0., 1.55), (0., 0., 1.7)
        neck.parent = bones['Chest']
        neck.use_connect = True
        bpy.ops.object.mode_set(mode='POSE')
        setup.prepare(self.rig)
        body_setup.generate(bpy.context, self.rig)
        self.names = [f'f_index.{index:02d}.L' for index in range(1, 4)]
        self.values = {name: {'location': {0: 0., 1: 0., 2: 0.},
                              'rotation_euler': {0: .25 + index * .1, 1: .03, 2: -.04},
                              'scale': {0: 1., 1: 1., 2: 1.}}
                       for index, name in enumerate(self.names)}
        self.metadata = {'version': 1, 'source_object': self.rig.name, 'source_slot': 'OB' + self.rig.name,
                         'names': self.names, 'scope': 'SELECTED', 'rest': poses.native_rest(self.rig)}
        for name in ('Neck', 'forearm.L', 'hand.L', *self.names):
            self.metadata['rest'][name]['connected'] = False
        self.metadata['rest']['thigh.L']['matrix'][0][3] += .06
        self.metadata['rest']['thigh.L']['length'] += .04

    def protected(self):
        return dict(rest=poses.native_rest(self.rig), mesh=setup.mesh_state(self.mesh),
                    basis={pb.name: (pb.rotation_mode, tuple(tuple(row) for row in pb.matrix_basis),
                                    tuple(pb.location), tuple(pb.rotation_euler), tuple(pb.rotation_quaternion),
                                    tuple(pb.rotation_axis_angle), tuple(pb.scale), pb.get('ik_fk'))
                           for pb in self.rig.pose.bones},
                    session=self.rig.get(original.SESSION),
                    constraints=tuple((pb.name, c.name, c.mute, c.influence)
                                      for pb in self.rig.pose.bones for c in pb.constraints))

    def reference(self):
        """Blender evaluates the source flags on a constraint-free native copy."""
        rig = self.rig
        clone = rig.copy()
        clone.data = rig.data.copy()
        bpy.context.scene.collection.objects.link(clone)
        clone.animation_data_clear()
        for pb in clone.pose.bones:
            for con in tuple(pb.constraints):
                pb.constraints.remove(con)
        bpy.ops.object.mode_set(mode='OBJECT')
        rig.select_set(False)
        clone.select_set(True)
        bpy.context.view_layer.objects.active = clone
        bpy.ops.object.mode_set(mode='EDIT')
        for name, state in self.metadata['rest'].items():
            clone.data.edit_bones[name].use_connect = state['connected']
        bpy.ops.object.mode_set(mode='POSE')
        # Start from each current evaluated basis. The asset applies only to the
        # requested fingers; current ancestor poses are inputs to this test.
        for name in sorted(poses.native_rest(clone), key=lambda n: len(clone.pose.bones[n].parent_recursive)):
            clone.pose.bones[name].matrix = rig.pose.bones[name].matrix
            limb_ik_fk._update(bpy.context, clone)
        for name, fields in self.values.items():
            pb = clone.pose.bones[name]
            pb.rotation_mode = 'XYZ'
            pb.location = [fields['location'][i] for i in range(3)]
            pb.rotation_euler = [fields['rotation_euler'][i] for i in range(3)]
            pb.scale = [fields['scale'][i] for i in range(3)]
        limb_ik_fk._update(bpy.context, clone)
        expected = {name: clone.pose.bones[name].matrix.copy() for name in self.names}
        data = clone.data
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(clone, do_unlink=True)
        bpy.data.armatures.remove(data)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='POSE')
        return expected

    def test_regions_connection_zero_translation_match_native_source_and_preserve_rest_mesh(self):
        expected = self.reference()
        before = self.protected()
        poses.apply_channels(bpy.context, self.rig, self.values, metadata=self.metadata)
        for name, matrix in expected.items():
            self.assertLess(poses._difference(matrix, self.rig.pose.bones[name].matrix), 2e-6, name)
        after = self.protected()
        self.assertEqual(after['rest'], before['rest'])
        self.assertEqual(after['mesh'], before['mesh'])
        self.assertEqual(after['constraints'], before['constraints'])
        for name in set(before['basis']) - set(self.names):
            self.assertEqual(after['basis'][name], before['basis'][name], name)

    def test_nonzero_partial_and_legacy_connection_changes_refuse_before_writes(self):
        values = deepcopy(self.values)
        values[self.names[0]]['location'][0] = .002
        before = self.protected()
        with self.assertRaisesRegex(ValueError, r'connected'):
            poses.apply_channels(bpy.context, self.rig, values, metadata=self.metadata)
        self.assertEqual(self.protected(), before)
        partial = deepcopy(self.values)
        del partial[self.names[0]]['location'][2]
        with self.assertRaisesRegex(ValueError, r'connected'):
            poses.apply_channels(bpy.context, self.rig, partial, metadata=self.metadata)
        self.rig[poses.BASELINE] = json.dumps({'version': 1, 'object': self.rig.name,
                                             'rest': self.metadata['rest']})
        with self.assertRaisesRegex(ValueError, 'Rest or structure changed'):
            poses.apply_channels(bpy.context, self.rig, self.values)

    def test_relevant_parent_geometry_and_invalid_unrelated_schema_still_refuse(self):
        for name, field in (('hand.L', 'matrix'), ('forearm.L', 'length'), ('f_index.02.L', 'parent')):
            metadata = deepcopy(self.metadata)
            if field == 'matrix':
                metadata['rest'][name][field][0][3] += .01
            elif field == 'length':
                metadata['rest'][name][field] += .01
            else:
                metadata['rest'][name][field] = 'Neck'
            before = self.protected()
            with self.assertRaisesRegex(ValueError, 'Rest or structure changed'):
                poses.apply_channels(bpy.context, self.rig, self.values, metadata=metadata)
            self.assertEqual(self.protected(), before)
        metadata = deepcopy(self.metadata)
        metadata['rest']['Neck']['matrix'][0][0] = float('nan')
        with self.assertRaisesRegex(ValueError, 'saved Pose has invalid native Rest'):
            poses.apply_channels(bpy.context, self.rig, self.values, metadata=metadata)

    def test_original_session_stays_strict_and_success_keeps_its_current_rest(self):
        original.enter(bpy.context, self.rig)
        session_rest = json.loads(self.rig[original.SESSION])['rest']
        expected = self.reference()
        poses.apply_channels(bpy.context, self.rig, self.values, metadata=self.metadata)
        self.assertTrue(original.active(self.rig))
        self.assertEqual(json.loads(self.rig[original.SESSION])['rest'], session_rest)
        original._validate_session_rest(self.rig)
        for name, matrix in expected.items():
            self.assertLess(poses._difference(matrix, self.rig.pose.bones[name].matrix), 2e-6, name)
        saved = json.loads(self.rig[original.SESSION])
        saved['rest']['Neck']['connected'] = not saved['rest']['Neck']['connected']
        self.rig[original.SESSION] = json.dumps(saved)
        before = self.protected()
        with self.assertRaisesRegex(ValueError, 'Native Rest changed at Neck.*connected'):
            poses.apply_channels(bpy.context, self.rig, self.values, metadata=self.metadata)
        self.assertEqual(self.protected(), before)

    def test_fault_after_real_matching_restores_channels_and_session(self):
        original.enter(bpy.context, self.rig)
        before = self.protected()
        actual = poses._match_original
        reached = []
        def fault(*args, **kwargs):
            result = actual(*args, **kwargs)
            reached.append(True)
            raise ValueError('injected after regional Original match')
        with patch.object(poses, '_match_original', side_effect=fault):
            with self.assertRaisesRegex(ValueError, 'injected after regional Original match'):
                poses.apply_channels(bpy.context, self.rig, self.values, metadata=self.metadata)
        self.assertEqual(reached, [True])
        self.assertEqual(self.protected(), before)


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise RuntimeError('Regional Pose native tests failed')
