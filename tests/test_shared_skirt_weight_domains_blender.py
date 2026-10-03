"""Native Automatic Weights keeps shared Body and Dress solve domains separate.

Run with --background --factory-startup --python-exit-code 1 --python this file.
The real weight operator and recovery run; only Bone Heat's numeric output is
replaced so the domain flags can be inspected at the native-call boundary.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import selected_bone_weights as weights, skirt_rig
import test_selected_bone_weights_blender as fixture


def snapshot(mesh, rig):
    return {
        'groups': weights._capture_vertex_groups(mesh),
        'structure': weights._capture_structure(mesh, rig),
        'context': weights._capture_context_state(bpy.context, mesh, rig),
        'flags': (mesh.data.use_mirror_x, mesh.data.use_paint_mask,
                  mesh.data.use_paint_mask_vertex),
    }


class SharedWeightDomains(unittest.TestCase):
    def setUp(self):
        self.mesh, self.rig, self.modifier = fixture.make_fixture(with_stack=True)
        # The simple foreign bone exercises the exact shared ownership boundary
        # without creating cages, physics or a second Armature.
        self.rig.data.bones['Bone.R'][skirt_rig.OWNER_KEY] = 'dress_test_owner'
        self.mesh.data.use_mirror_x = True
        self.mesh.data.use_paint_mask = True
        self.mesh.data.use_paint_mask_vertex = True
        fixture.prepare_pose_context(self.mesh, self.rig, ('Bone.L',))
        self.before = snapshot(self.mesh, self.rig)
        self.calls = 0

    def inspect_domain(self):
        self.calls += 1
        self.assertFalse(self.rig.data.bones['Bone.R'].use_deform)
        self.assertTrue(self.rig.data.bones['Bone.L'].use_deform)
        self.assertTrue(self.rig.data.bones['Spine'].use_deform)
        self.assertFalse(self.rig.data.bones['Helper'].use_deform)
        self.assertEqual({bone.name for bone in bpy.context.selected_pose_bones},
                         {'Bone.L', 'Spine'})

    def native_output(self):
        self.inspect_domain()
        indices = tuple(range(len(self.mesh.data.vertices)))
        self.mesh.vertex_groups['Bone.L'].add(indices, .8, 'REPLACE')
        self.mesh.vertex_groups['Spine'].add(indices, .2, 'REPLACE')
        return {'FINISHED'}

    def execute(self, callback):
        with patch.object(weights, '_run_native_auto_weights', callback):
            return bpy.ops.character_designer.auto_weight_selected_bones(
                normalize_affected_deform_weights=True)

    def assert_state_restored(self, *, groups=True):
        after = snapshot(self.mesh, self.rig)
        for field in ('structure', 'context', 'flags'):
            self.assertEqual(self.before[field], after[field], field)
        if groups:
            self.assertEqual(self.before['groups'], after['groups'])

    def test_body_full_solve_hides_foreign_deform_and_preserves_its_weights(self):
        self.assertEqual(self.execute(self.native_output), {'FINISHED'})
        self.assertEqual(self.calls, 1)
        self.assert_state_restored(groups=False)
        before = weights._group_state_map(self.before['groups'])
        after = weights._group_state_map(weights._capture_vertex_groups(self.mesh))
        for name in ('Bone.R', 'Helper', 'Artist'):
            self.assertEqual(before[name], after[name], name)
        self.assertNotEqual(before['Bone.L']['weights'], after['Bone.L']['weights'])
        for vertex in self.mesh.data.vertices:
            total = sum(fixture._effective_group_weight(self.mesh, name, vertex.index)
                        for name in ('Bone.L', 'Spine'))
            self.assertAlmostEqual(total, 1.0, places=5)

    def test_native_cancel_restores_weights_deform_flags_and_workspace(self):
        def cancel():
            self.native_output()
            return {'CANCELLED'}
        self.assertEqual(self.execute(cancel), {'CANCELLED'})
        self.assertEqual(self.calls, 1)
        self.assert_state_restored()

    def test_native_error_restores_weights_deform_flags_and_workspace(self):
        def fail():
            self.native_output()
            raise RuntimeError('Injected shared-domain Bone Heat failure')
        self.assertEqual(self.execute(fail), {'CANCELLED'})
        self.assertEqual(self.calls, 1)
        self.assert_state_restored()

    def test_foreign_selection_refused_before_native_or_weight_changes(self):
        fixture.prepare_pose_context(self.mesh, self.rig, ('Bone.R',))
        self.before = snapshot(self.mesh, self.rig)
        self.assertEqual(self.execute(self.native_output), {'CANCELLED'})
        self.assertEqual(self.calls, 0)
        self.assert_state_restored()

    def dress_record(self):
        # Ownership validation is covered by the migration suite. This isolated
        # native-operator test supplies its already-validated source contract.
        self.mesh[skirt_rig.RIG_KEY] = self.rig
        return {'owner': 'dress_test_owner', 'shared': {'names': ['Bone.R']},
                'controls': {'waist': 'Bone.R', 'mid': 'Helper', 'hem': 'Helper', 'chains': []},
                'chains': [{'def': ['Bone.R'], 'manual': [], 'phys': []}]}

    def test_dress_full_solve_hides_body_deform_and_preserves_body_weights(self):
        record = self.dress_record()
        fixture.prepare_pose_context(self.mesh, self.rig, ('Bone.R',))
        self.before = snapshot(self.mesh, self.rig)

        def dress_output():
            self.calls += 1
            self.assertFalse(self.rig.data.bones['Bone.L'].use_deform)
            self.assertFalse(self.rig.data.bones['Spine'].use_deform)
            self.assertTrue(self.rig.data.bones['Bone.R'].use_deform)
            self.assertEqual({bone.name for bone in bpy.context.selected_pose_bones}, {'Bone.R'})
            self.mesh.vertex_groups['Bone.R'].add(tuple(range(len(self.mesh.data.vertices))), 1.0, 'REPLACE')
            return {'FINISHED'}

        with patch.object(skirt_rig, 'read_record', lambda source: record if source == self.mesh else None):
            self.assertEqual(self.execute(dress_output), {'FINISHED'})
        self.assertEqual(self.calls, 1)
        self.assert_state_restored(groups=False)
        before = weights._group_state_map(self.before['groups'])
        after = weights._group_state_map(weights._capture_vertex_groups(self.mesh))
        for name in ('Bone.L', 'Spine', 'Helper', 'Artist'):
            self.assertEqual(before[name], after[name], name)
        self.assertNotEqual(before['Bone.R']['weights'], after['Bone.R']['weights'])

    def test_body_selection_for_dress_refused_before_changes(self):
        record = self.dress_record()
        self.before = snapshot(self.mesh, self.rig)
        with patch.object(skirt_rig, 'read_record', lambda source: record if source == self.mesh else None):
            self.assertEqual(self.execute(self.native_output), {'CANCELLED'})
        self.assertEqual(self.calls, 0)
        self.assert_state_restored()

    def test_standalone_skirt_rig_retains_legacy_full_rig_domain(self):
        self.rig[skirt_rig.OWNER_KEY] = 'legacy_dress_owner'
        self.assertEqual(set(weights._mesh_deform_bone_names(self.mesh, self.rig)),
                         {'Bone.L', 'Spine', 'Bone.R'})


def main():
    character_designer.register()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(SharedWeightDomains))
        if not result.wasSuccessful():
            raise SystemExit(1)
        print(f'PASS Shared Skirt Weight Domains {result.testsRun} tests')
    finally:
        character_designer.unregister()


if __name__ == '__main__':
    main()
