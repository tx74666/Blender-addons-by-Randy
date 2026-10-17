"""Bounded schema reuse keeps native ownership proofs exact; disposable Blender only."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_region as region, limb_ik,
    unity_animation as ua, widget_collections)
from test_original_leg_rest_return_blender import make_fixture
from test_original_return_owner_blender import retain_action


def legacy_ownership(bone):
    """Pre-optimization field/value contract, independent of new helpers."""
    keys = {limb_ik.OWNER_KEY, limb_ik.VERSION_KEY, limb_ik.ARMATURE_ID_KEY,
            limb_ik.RIG_ID_KEY, limb_ik.ROLE_KEY, limb_ik.KIND_KEY,
            limb_ik.SIDE_KEY, limb_ik.CHAIN_KEY}
    for module in widget_collections._modules():
        for field in ('OWNER_KEY', 'ID_KEY', 'RIG_KEY', 'ROLE_KEY', 'SIDE_KEY'):
            if key := getattr(module, field, None):
                keys.add(key)
    return {key: bone[key] for key in keys if key in bone}


class BodyRegionOwnerProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def make(self):
        rig, _mesh = make_fixture()
        action, motion = retain_action(rig)
        return rig, action, motion

    def pin(self, rig):
        proof = region._ownership_seed(rig)
        region._finish_owner_revision(rig, proof)
        self.assertTrue(region.verified_single_armature_user(rig))
        return proof

    def test_seed_revision_and_proof_collect_fields_once_and_keep_exact_hashes(self):
        rig, action, motion = self.make()
        expected = {bone.name: region._hash(legacy_ownership(bone)) for bone in rig.data.bones}
        before = (region._hash(ua._rest_state(rig.data)),
                  region._hash(dict(action.items())), region._hash(ua._rest_state(motion)))
        modules = widget_collections._modules
        with patch.object(widget_collections, '_modules', wraps=modules) as scan:
            proof = region._ownership_seed(rig)
            self.assertEqual(scan.call_count, 1)
        self.assertEqual(proof['bone_properties'], expected)
        with patch.object(widget_collections, '_modules', wraps=modules) as scan:
            region._finish_owner_revision(rig, proof)
            self.assertEqual(scan.call_count, 1)
        accepted = json.loads(rig.data[region.RECOVERY_OWNER_KEY])
        self.assertEqual(accepted['bone_properties'], expected)
        with patch.object(widget_collections, '_modules', wraps=modules) as scan:
            self.assertTrue(region.verified_single_armature_user(rig))
            self.assertEqual(scan.call_count, 1)
            self.assertTrue(region.verified_single_armature_user(rig))
            self.assertEqual(scan.call_count, 2)
        self.assertEqual(before, (region._hash(ua._rest_state(rig.data)),
                                 region._hash(dict(action.items())),
                                 region._hash(ua._rest_state(motion))))

    def test_each_proof_reads_current_native_values_and_module_fields(self):
        rig, _action, _motion = self.make()
        self.pin(rig)
        bone = next(b for b in rig.data.bones if limb_ik.ROLE_KEY in b)
        old = bone[limb_ik.ROLE_KEY]
        bone[limb_ik.ROLE_KEY] = old + ' changed outside the operation'
        self.assertFalse(region.verified_single_armature_user(rig))
        bone[limb_ik.ROLE_KEY] = old
        self.assertTrue(region.verified_single_armature_user(rig))
        new_key = 'regional_test_new_provenance_field'
        bone[new_key] = 'a new immutable value'
        with patch.object(widget_collections, '_modules', return_value=(
                type('FreshComponentSchema', (), {'OWNER_KEY': new_key}),)):
            self.assertFalse(region.verified_single_armature_user(rig))
        del bone[new_key]
        self.assertTrue(region.verified_single_armature_user(rig))

    def test_unknown_native_id_owner_still_refuses_without_authoring_changes(self):
        rig, action, motion = self.make()
        self.pin(rig)
        before = (rig.data[region.RECOVERY_OWNER_KEY], region._hash(dict(action.items())),
                  region._hash(ua._rest_state(rig.data)), region._hash(ua._rest_state(motion)))
        foreign = bpy.data.objects.new('Unrecognized native Armature reference', None)
        bpy.context.scene.collection.objects.link(foreign)
        foreign['borrowed_armature'] = rig.data
        self.assertFalse(region.verified_single_armature_user(rig))
        self.assertEqual(before, (rig.data[region.RECOVERY_OWNER_KEY],
                                 region._hash(dict(action.items())),
                                 region._hash(ua._rest_state(rig.data)),
                                 region._hash(ua._rest_state(motion))))
        del foreign['borrowed_armature']
        self.assertTrue(region.verified_single_armature_user(rig))

    def test_native_user_map_subset_keeps_action_object_and_foreign_references(self):
        rig, action, _motion = self.make()
        data = rig.data
        foreign = bpy.data.objects.new('Foreign property owner for subset comparison', None)
        bpy.context.scene.collection.objects.link(foreign)
        foreign['borrowed_armature'] = data
        second = bpy.data.objects.new('Second actual binding for subset comparison', data)
        bpy.context.scene.collection.objects.link(second)
        expected = {rig, action, foreign, second}
        complete = bpy.data.user_map().get(data, set())
        subset = bpy.data.user_map(subset={data}).get(data, set())
        self.assertEqual(complete, expected)
        self.assertEqual(subset, complete)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BodyRegionOwnerProofTests))
    if not result.wasSuccessful():
        raise RuntimeError('Regional native ownership proof tests failed')
