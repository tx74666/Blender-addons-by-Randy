"""Original Rest proof accepts float roundtrips, never actual skeleton edits."""
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import body_original_mode as original, body_setup
from character_designer import control_pose_assets as poses, limb_ik
import test_body_calibration_blender as setup
from test_control_pose_weights_blender import matrices


class RestProofTests(unittest.TestCase):
    def setUp(self):
        self.rig = SimpleNamespace(data=SimpleNamespace(bones=SimpleNamespace(keys=lambda: ['Native', 'CTRL'])))
        self.current = {'Native': {
            'matrix': [[float(row == column) for column in range(4)] for row in range(4)],
            'length': .2, 'parent': None, 'connected': False, 'inherit_scale': 'FULL',
            'inherit_rotation': True, 'local_location': True,
        }}
        self.saved = {'version': 1, 'bones': ['CTRL', 'Native'], 'rest': copy.deepcopy(self.current)}

    def proof(self, saved=None, current=None):
        return original._validate_session_rest(self.rig, self.saved if saved is None else saved,
                                               self.current if current is None else current)

    def test_microscopic_roundtrip_reuses_current_without_mutation(self):
        self.saved['rest']['Native']['matrix'][0][1] += 4e-7
        self.saved['rest']['Native']['length'] += 2e-7
        before = copy.deepcopy((self.saved, self.current))
        self.assertIs(self.proof(), self.current)
        self.assertEqual((self.saved, self.current), before)

    def test_real_numeric_edits_are_named_and_not_blessed(self):
        for field in ('matrix', 'length'):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.current)
                if field == 'matrix':
                    changed['Native'][field][0][1] += .004
                else:
                    changed['Native'][field] += .004
                before = copy.deepcopy((self.saved, changed))
                with self.assertRaisesRegex(ValueError, r'Native Rest changed at Native.*Keep Original'):
                    self.proof(current=changed)
                self.assertEqual((self.saved, changed), before)

    def test_discrete_relations_remain_exact(self):
        for field, value in (('parent', 'Parent'), ('connected', True), ('inherit_scale', 'NONE'),
                             ('inherit_rotation', False), ('local_location', False)):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.current)
                changed['Native'][field] = value
                with self.assertRaisesRegex(ValueError, field):
                    self.proof(current=changed)

    def test_inventory_and_native_ownership_remain_exact(self):
        for names in (['Native'], ['Native', 'CTRL', 'New'], ['Native', 'Renamed']):
            with self.subTest(names=names):
                self.rig.data.bones.keys = lambda: names
                with self.assertRaisesRegex(ValueError, 'bone inventory changed'):
                    self.proof()
        self.rig.data.bones.keys = lambda: ['Native', 'CTRL']
        with self.assertRaisesRegex(ValueError, 'native bone ownership changed'):
            self.proof(current={})

    def test_nonfinite_or_malformed_rest_refuses_read_only(self):
        bad = []
        changed = copy.deepcopy(self.current)
        changed['Native']['matrix'][1][1] = float('nan')
        bad.append(changed)
        changed = copy.deepcopy(self.current)
        changed['Native']['length'] = float('inf')
        bad.append(changed)
        changed = copy.deepcopy(self.current)
        changed['Native']['matrix'].pop()
        bad.append(changed)
        changed = copy.deepcopy(self.current)
        changed['Native']['connected'] = 1
        bad.append(changed)
        for changed in bad:
            with self.subTest(changed=repr(changed)):
                with self.assertRaisesRegex(ValueError, 'invalid or non-finite at Native'):
                    self.proof(current=changed)
        saved = copy.deepcopy(self.saved)
        saved['version'] = True
        with self.assertRaisesRegex(ValueError, 'schema is unsupported'):
            self.proof(saved=saved)


class NativeOriginalRestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        self.rig, self.mesh = setup.fixture()
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        setup.prepare(self.rig)
        body_setup.generate(bpy.context, self.rig)
        original.enter(bpy.context, self.rig)

    def protected(self):
        return (self.rig.get(original.SESSION), poses.native_rest(self.rig),
                original._channels(self.rig), setup.mesh_state(self.mesh),
                tuple((pb.name, con.name, con.type, bool(con.mute), float(con.influence),
                       getattr(con, 'subtarget', None))
                      for pb in self.rig.pose.bones for con in pb.constraints))

    def assertPose(self, expected):
        actual = matrices(self.rig)
        self.assertLess(max(poses._difference(matrix, actual[name])
                            for name, matrix in expected.items()), 4e-4)

    def test_mesh_only_edits_survive_return_and_single_preflight(self):
        # Coordinate edits are independent of the skeleton proof. Preserve both
        # the changed mesh and its authored asymmetric key after the mode switch.
        self.mesh.data.vertices[0].co.x += .0004
        for key in self.mesh.data.shape_keys.key_blocks:
            key.data[0].co.x += .0004
        edited = setup.mesh_state(self.mesh)
        rest, pose = poses.native_rest(self.rig), matrices(self.rig)
        with patch.object(original, '_require', wraps=original._require) as preflight:
            with patch.object(poses, 'native_rest', wraps=poses.native_rest) as rest_read:
                self.assertTrue(original.leave(bpy.context, self.rig))
                self.assertEqual(preflight.call_count, 1)
                self.assertEqual(rest_read.call_count, 1)
        self.assertFalse(original.active(self.rig))
        self.assertEqual(setup.mesh_state(self.mesh), edited)
        self.assertEqual(poses.native_rest(self.rig), rest)
        self.assertPose(pose)
        limb_ik._validate_inventory(self.rig)

    def test_edit_mode_roundtrip_and_saved_float_roundoff_preserve_rest(self):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.mode_set(mode='POSE')
        saved = json.loads(self.rig[original.SESSION])
        saved['rest']['forearm.L']['matrix'][0][1] += 3e-7
        saved['rest']['forearm.L']['length'] += 2e-7
        self.rig[original.SESSION] = json.dumps(saved)
        rest, mesh, pose = poses.native_rest(self.rig), setup.mesh_state(self.mesh), matrices(self.rig)
        self.assertEqual(original._validate_session_rest(self.rig), rest)
        self.assertTrue(original.leave(bpy.context, self.rig))
        self.assertEqual(poses.native_rest(self.rig), rest)
        self.assertEqual(setup.mesh_state(self.mesh), mesh)
        self.assertPose(pose)
        limb_ik._validate_inventory(self.rig)

    def test_disconnected_rest_edit_refuses_without_resetting_artist_changes(self):
        bpy.ops.object.mode_set(mode='EDIT')
        self.rig.data.edit_bones['forearm.L'].use_connect = False
        self.rig.data.edit_bones['upper_arm.L'].tail += Vector((.004, 0, 0))
        bpy.ops.object.mode_set(mode='POSE')
        before = self.protected()
        with self.assertRaisesRegex(ValueError, r'(Rest|structure|connect)'):
            original.leave(bpy.context, self.rig)
        self.assertEqual(self.protected(), before)
        self.assertTrue(original.active(self.rig))

    def test_bone_inventory_edit_refuses_without_changing_session(self):
        self.rig.data.bones['upper_arm.L'].name = 'Artist Upper Arm'
        before = self.protected()
        with self.assertRaisesRegex(ValueError, r'bone inventory changed.*upper_arm.L.*Artist Upper Arm'):
            original.leave(bpy.context, self.rig)
        self.assertEqual(self.protected(), before)
        self.assertTrue(original.active(self.rig))

    def test_active_native_forearm_preview_blocks_switch_and_group_until_cancelled(self):
        from character_designer import forearm_twist as runtime
        import test_forearm_original_mode_blender as forearm_original
        import test_forearm_twist_blender as fixtures

        sleeve, _outside = forearm_original.sleeve(self.rig)
        before_preview = self.protected()
        keys_before = fixtures.key_snapshot(sleeve)
        fixtures.activate_mesh(sleeve)
        runtime.start_test(bpy.context, sleeve, 'L', symmetry=False, recapture=True)
        self.assertEqual(runtime._SESSION['target'], 'hand.L')

        def preview_state():
            return (self.protected(), fixtures.key_snapshot(sleeve),
                    sleeve.get(runtime.RECORD_KEY), sleeve.get(runtime.PREVIEW_KEY),
                    runtime._SESSION['target'], repr(runtime._SESSION['history']),
                    bpy.context.mode, bpy.context.object.name,
                    tuple(obj.name for obj in bpy.context.selected_objects))

        try:
            before = preview_state()
            for operation in (lambda: original.leave(bpy.context, self.rig),
                              lambda: original.show_group(bpy.context, self.rig, 'BODY'),
                              lambda: original.ensure_dress_editable(bpy.context, self.rig)):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(ValueError, 'Confirm or cancel the Forearm preview'):
                        operation()
                    self.assertEqual(preview_state(), before)
                    self.assertTrue(original.active(self.rig))
        finally:
            if runtime._SESSION is not None:
                runtime.finish_test(bpy.context, confirm=False)
        self.assertIsNone(runtime._SESSION)
        self.assertEqual(fixtures.key_snapshot(sleeve), keys_before)
        self.assertEqual(self.protected(), before_preview)
        self.assertTrue(original.leave(bpy.context, self.rig))
        self.assertFalse(original.active(self.rig))
        limb_ik._validate_inventory(self.rig)


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise RuntimeError('Body Original Rest tests failed')
