"""Persistent child-Mesh placement with an unchanged constant Object Action.

Only disposable background fixtures are used. Save/reopen writes a fresh
temporary .blend, never an artist/input. No animation is deleted or rekeyed.
"""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_owner, body_setup_transaction as transaction,
    character_ground as placement, floor_widgets, limb_ik, placement_offsets)
import test_character_ground_blender as original
import test_body_region_blender as regional
import test_body_region_styles_blender as styles
from test_body_setup_removal_blender import bind_probe_surface


def action_state(action):
    return (body_original_owner._animation_fingerprint(action), transaction._rna(action),
        transaction._properties(action), tuple((curve.data_path, curve.array_index,
            transaction._rna(curve), tuple(transaction._rna(key) for key in curve.keyframe_points))
            for curve in limb_ik._fcurves_for_action(action)))


def world_points(obj):
    graph = bpy.context.evaluated_depsgraph_get()
    return tuple(placement._points(obj, graph))


class PlacementOffsetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Placement-offset tests require isolated background Blender.')
        if not hasattr(bpy.types.Scene, 'character_designer_ground'):
            character_designer.register()

    def make(self, *, controls=False):
        if controls:
            rig, body = regional.BodyRegionTests().make(corrective=False)
            shoes = bind_probe_surface(rig)
            original.activate(rig)
        else:
            fixture = original.CharacterPlacementTests()
            fixture.setUp()
            rig, body, shoes = fixture.rig, fixture.body, fixture.shoes
        # Ground's nonroot path: the shoe already follows the rig, and its
        # Object Action stores a static location rather than a motion clip.
        shoes.parent = rig
        shoes.name = 'Shoes'
        shoes.rotation_mode = 'XYZ'
        shoes.rotation_euler = (.08, -.04, .05)
        shoes.location, shoes.delta_location = (.021, -.031, .17562288), (.01, -.012, .005)
        self.assertTrue(shoes.keyframe_insert(data_path='location', frame=0))
        self.assertTrue(shoes.keyframe_insert(data_path='rotation_euler', frame=0))
        action = shoes.animation_data.action
        action.name = 'ShoesAction constant Object placement'
        shoes.animation_data.action_blend_type = 'REPLACE'
        shoes.animation_data.action_influence = 1.
        shoes.animation_data.action_extrapolation = 'HOLD'
        for curve in limb_ik._fcurves_for_action(action):
            curve.extrapolation = 'CONSTANT'
        bpy.context.scene.frame_set(20, subframe=.375)
        keyed = tuple(shoes.location)
        shoes.location += Vector((.006, -.008, .0670116246))
        shoes.update_tag(refresh={'OBJECT'})
        bpy.context.view_layer.update()
        self.assertNotEqual(tuple(shoes.location), keyed)
        original.activate(rig)
        return rig, body, shoes

    def snapshot(self, rig, body, shoes):
        bpy.context.view_layer.update()
        return {'channels': placement._channels(shoes),
            'world': original.matrix(shoes.matrix_world), 'basis': original.matrix(shoes.matrix_basis),
            'points': world_points(shoes), 'raw': (original.raw_mesh(body), original.raw_mesh(shoes)),
            'rig': placement._rig_state(rig),
            'root_channels': placement._channels(rig), 'root_world': original.matrix(rig.matrix_world),
            'action': shoes.animation_data.action,
            'action_state': action_state(shoes.animation_data.action),
            'frame': (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe),
            'resources': {kind: tuple(sorted(getattr(bpy.data, kind).keys()))
                for kind in ('objects', 'meshes', 'armatures', 'actions')}}

    def assertMatrix(self, actual, expected):
        self.assertLessEqual(max(abs(a-b) for row_a,row_b in zip(actual, expected)
                                for a,b in zip(row_a, row_b)), 1e-6)

    def assertContent(self, rig, body, shoes, before, *, reopening=False):
        self.assertEqual((original.raw_mesh(body), original.raw_mesh(shoes)), before['raw'])
        self.assertEqual(placement._rig_state(rig), before['rig'])
        self.assertEqual(action_state(shoes.animation_data.action), before['action_state'])
        self.assertEqual((bpy.context.scene.frame_current, bpy.context.scene.frame_subframe), before['frame'])
        self.assertEqual({kind: tuple(sorted(getattr(bpy.data, kind).keys()))
            for kind in ('objects', 'meshes', 'armatures', 'actions')}, before['resources'])
        if not reopening:
            self.assertIs(shoes.animation_data.action, before['action'])

    def state(self, rig, body, shoes):
        return placement_offsets.prepare(bpy.context, {rig, body, shoes}, {rig})

    def test_constant_location_becomes_persistent_delta_without_moving_current_shoe(self):
        rig, body, shoes = self.make()
        before = self.snapshot(rig, body, shoes)
        state = self.state(rig, body, shoes)
        self.assertEqual([entry['object'] for entry in state['entries']], [shoes])
        self.assertEqual(set(state['expected_channels']), {shoes})
        proof = placement_offsets.apply(bpy.context, state)
        self.assertEqual(proof['count'], 1)
        self.assertEqual(tuple(shoes.location), state['entries'][0]['planned']['location'])
        self.assertNotEqual(tuple(shoes.delta_location), before['channels']['channels']['delta_location'])
        self.assertMatrix(shoes.matrix_basis, before['basis'])
        self.assertMatrix(shoes.matrix_world, before['world'])
        self.assertLessEqual(max(abs(a-b) for a,b in zip(world_points(shoes), before['points'])), 1e-6)
        self.assertContent(rig, body, shoes, before)
        self.assertEqual(self.state(rig, body, shoes)['entries'], [], 'Normalized channels must be a no-op.')
        placement_offsets.restore(bpy.context, state)
        self.assertEqual(placement._channels(shoes), before['channels'])
        self.assertContent(rig, body, shoes, before)

    def test_ground_offset_survives_trusted_save_reopen_with_action_unchanged(self):
        rig, body, shoes = self.make()
        before = self.snapshot(rig, body, shoes)
        result = placement.ground(bpy.context, rig)
        self.assertAlmostEqual(result['lowest_after'], 0., delta=result['lowest_tolerance'])
        normalized = placement._channels(shoes)
        grounded_world, grounded_basis, grounded_points = (
            original.matrix(shoes.matrix_world), original.matrix(shoes.matrix_basis), world_points(shoes))
        # Store a durable Action payload rather than native pointers across an
        # intentional native reopen. No slots, animation binding or keys change.
        semantic = body_original_owner._animation_fingerprint(shoes.animation_data.action)
        rest = original.matrix(rig.data.bones['Hips'].matrix_local)
        names = rig.name, body.name, shoes.name
        with tempfile.TemporaryDirectory(prefix='cd_placement_offset_') as folder:
            target = Path(folder) / 'isolated_persistent_shoe.blend'
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=str(target), copy=True, check_existing=False), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=str(target), load_ui=False, use_scripts=True), {'FINISHED'})
            rig, body, shoes = (bpy.data.objects[name] for name in names)
            bpy.context.view_layer.update()
            self.assertEqual(placement._channels(shoes), normalized)
            self.assertMatrix(shoes.matrix_world, grounded_world)
            self.assertMatrix(shoes.matrix_basis, grounded_basis)
            self.assertEqual(body_original_owner._animation_fingerprint(shoes.animation_data.action), semantic)
            self.assertEqual((original.raw_mesh(body), original.raw_mesh(shoes)), before['raw'])
            self.assertEqual(original.matrix(rig.data.bones['Hips'].matrix_local), rest)
            self.assertLessEqual(max(abs(a-b) for a,b in zip(world_points(shoes), grounded_points)), 1e-6)

    def test_apply_failure_restores_both_original_storage_fields(self):
        rig, body, shoes = self.make()
        before = self.snapshot(rig, body, shoes)
        state = self.state(rig, body, shoes)
        with patch.object(placement_offsets, 'verify', side_effect=ValueError('Injected storage verification failure')):
            with self.assertRaisesRegex(ValueError, 'Injected storage verification failure'):
                placement_offsets.apply(bpy.context, state)
        self.assertEqual(placement._channels(shoes), before['channels'])
        self.assertMatrix(shoes.matrix_world, before['world'])
        self.assertContent(rig, body, shoes, before)

    def test_roots_and_detached_mesh_roots_are_never_reparameterized(self):
        rig, body, shoes = self.make()
        before = placement._channels(shoes)
        state = placement_offsets.prepare(bpy.context, {rig, body, shoes}, {rig, shoes})
        self.assertEqual(state['entries'], [])
        self.assertEqual(placement_offsets.apply(bpy.context, state)['count'], 0)
        self.assertEqual(placement._channels(shoes), before)

    def test_two_key_motion_is_refused_before_any_storage_change(self):
        rig, body, shoes = self.make()
        shoes.location.z += .1
        self.assertTrue(shoes.keyframe_insert(data_path='location', frame=25))
        bpy.context.view_layer.update()
        shoes.location.z += .03
        before = self.snapshot(rig, body, shoes)
        with self.assertRaisesRegex(ValueError, 'constant single-key location curves'):
            self.state(rig, body, shoes)
        self.assertEqual(placement._channels(shoes), before['channels'])
        self.assertContent(rig, body, shoes, before)

    def test_delta_location_animation_is_refused_without_touching_action(self):
        rig, body, shoes = self.make()
        shoes.keyframe_insert(data_path='delta_location', frame=0)
        bpy.context.view_layer.update()
        shoes.location.z += .03
        before = self.snapshot(rig, body, shoes)
        with self.assertRaisesRegex(ValueError, 'ambiguous Object transform animation'):
            self.state(rig, body, shoes)
        self.assertEqual(placement._channels(shoes), before['channels'])
        self.assertContent(rig, body, shoes, before)

    def test_property_driver_reading_storage_channels_is_refused(self):
        rig, body, shoes = self.make()
        reader = bpy.data.objects.new('Storage reader', None)
        bpy.context.scene.collection.objects.link(reader)
        reader['read_shoe'] = 0.
        driver = reader.driver_add('["read_shoe"]').driver
        driver.type = 'SCRIPTED'
        variable = driver.variables.new()
        variable.name, variable.type = 'shoe_z', 'SINGLE_PROP'
        variable.targets[0].id, variable.targets[0].data_path = shoes, 'location[2]'
        driver.expression = 'shoe_z'
        bpy.context.view_layer.update()
        shoes.location.z += .03
        before = self.snapshot(rig, body, shoes)
        with self.assertRaisesRegex(ValueError, 'read by a property driver'):
            self.state(rig, body, shoes)
        self.assertEqual(placement._channels(shoes), before['channels'])
        self.assertContent(rig, body, shoes, before)

    def test_second_unkeyed_rotation_is_refused_before_location_normalization(self):
        rig, body, shoes = self.make()
        shoes.rotation_euler.x += .2
        before = self.snapshot(rig, body, shoes)
        with self.assertRaisesRegex(ValueError, 'another unkeyed animated transform'):
            self.state(rig, body, shoes)
        self.assertEqual(placement._channels(shoes), before['channels'])
        self.assertContent(rig, body, shoes, before)

    def test_ground_failure_after_root_move_restores_original_storage_and_world(self):
        rig, body, shoes = self.make()
        before = self.snapshot(rig, body, shoes)
        actual = placement._verify
        calls = []

        def fail_after_actual_ground(context, state, distance):
            result = actual(context, state, distance)
            calls.append(result)
            if len(calls) == 1:
                self.assertNotEqual(placement._channels(rig), before['root_channels'])
                self.assertNotEqual(placement._channels(shoes), before['channels'])
                raise ValueError('Injected after actual Ground and persistent offset')
            return result

        with patch.object(placement, '_verify', side_effect=fail_after_actual_ground):
            with self.assertRaisesRegex(ValueError, 'Injected after actual Ground and persistent offset'):
                placement.ground(bpy.context, rig)
        self.assertEqual(len(calls), 1)
        self.assertEqual(placement._channels(rig), before['root_channels'])
        self.assertEqual(placement._channels(shoes), before['channels'])
        self.assertMatrix(shoes.matrix_world, before['world'])
        self.assertMatrix(rig.matrix_world, before['root_world'])
        self.assertContent(rig, body, shoes, before)

    def test_ground_display_failure_restores_owned_styles_root_and_shoe_storage(self):
        rig, body, shoes = self.make(controls=True)
        before = self.snapshot(rig, body, shoes)
        before_styles = styles.shape_state(rig)
        actual = floor_widgets.verify
        calls = []

        def fail_after_actual_displays(context, state):
            proof = actual(context, state)
            calls.append(proof)
            if len(calls) == 1:
                self.assertEqual(proof['count'], 3)
                self.assertTrue(all(row['all_vertices_on_floor'] for row in proof['controls'].values()))
                self.assertNotEqual(styles.shape_state(rig), before_styles)
                self.assertNotEqual(placement._channels(rig), before['root_channels'])
                self.assertNotEqual(placement._channels(shoes), before['channels'])
                raise ValueError('Injected after owned planar display fitting and persistent offset')
            return proof

        with patch.object(floor_widgets, 'verify', side_effect=fail_after_actual_displays):
            with self.assertRaisesRegex(ValueError, 'Injected after owned planar display fitting'):
                placement.ground(bpy.context, rig)
        # Check only after the entire Ground rollback and its final graph tag;
        # an Action resample after helper.restore must not escape unnoticed.
        self.assertEqual(len(calls), 1)
        self.assertEqual(styles.shape_state(rig), before_styles)
        self.assertEqual(placement._channels(shoes), before['channels'])
        self.assertEqual(placement._channels(rig), before['root_channels'])
        self.assertMatrix(shoes.matrix_world, before['world'])
        self.assertMatrix(rig.matrix_world, before['root_world'])
        self.assertContent(rig, body, shoes, before)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
