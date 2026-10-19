"""Display-only floor fitting in disposable native Blender fixtures.

Run with --background --factory-startup; never pass an artist .blend.  These
checks independently transform actual custom-shape vertices and protect every
functional bone, all author meshes, custom styles and animation inputs.
"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Matrix, Euler, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (character_ground as placement, floor_widgets,
    limb_ik, root_control)
import test_body_region_blender as regional
import test_body_region_styles_blender as styles
import test_body_setup_plan_blender as planning
from test_body_rest_resync_blender import world_surfaces


def offsets(rig):
    return {pb.name: tuple(pb.custom_shape_translation) for pb in rig.pose.bones}


def selected_names(rig):
    inventory = limb_ik._validate_inventory(rig)
    return {data['target'].name for key, data in inventory['rigs'].items() if key[0] == 'LEG'} | {
        root_control.validate(rig, inventory=inventory)['master']}


def style_state(rig, *, allow_offset=()):
    result = styles.shape_state(rig)
    for name in allow_offset:
        result[name]['display'].pop('translation')
    return result


def functional_state(rig):
    """Native Rest, authored pose, constraints and all ownership/default data."""
    result = regional.bone_state(rig, rig.data.bones.keys())
    for record in result.values():
        # Display state is checked separately, allowing only three live offsets.
        record.pop('shape')
    return {'bones': result, 'rig_properties': regional.properties(rig),
        'data_properties': regional.properties(rig.data), 'actions': regional.actions(rig)}


def display_points(rig, name):
    """Independent explicit T @ R @ S custom-shape transform, in world units."""
    bpy.context.view_layer.update()
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    pb = rig.pose.bones[name]
    anchor_name = pb.custom_shape_transform.name if pb.custom_shape_transform else pb.name
    matrix = (rig.data.bones[anchor_name].matrix_local if rig.data.pose_position == 'REST'
              else evaluated.pose.bones[anchor_name].matrix)
    size = pb.bone.length if pb.use_custom_shape_bone_size else 1.
    scaling = Matrix.Diagonal((*[component * size for component in pb.custom_shape_scale_xyz], 1.))
    visual = (Matrix.Translation(pb.custom_shape_translation)
              @ Euler(tuple(pb.custom_shape_rotation_euler)).to_matrix().to_4x4() @ scaling)
    world = evaluated.matrix_world @ matrix @ visual
    return tuple(tuple(world @ vertex.co) for vertex in pb.custom_shape.data.vertices)


def surfaces(rig):
    return {name: tuple(tuple(point) for point in points)
            for name, points in world_surfaces(rig).items()}


class FloorWidgetsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Floor-outline checks require isolated background Blender.')
        character_designer.register()

    def make(self):
        rig, mesh = regional.BodyRegionTests().make(corrective=False)
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        # Distinct, artist-owned live offsets/defaults must not be normalized.
        names = selected_names(rig)
        for index, name in enumerate(sorted(names)):
            pb = rig.pose.bones[name]
            pb.custom_shape_translation = (.011 * index, -.023, .31 + .027 * index)
        regional.update(rig)
        return rig, mesh, names

    def snapshot(self, rig, mesh, names):
        regional.update(rig)
        return {'functional': copy.deepcopy(functional_state(rig)),
            'styles': copy.deepcopy(style_state(rig, allow_offset=names)),
            'all_styles': copy.deepcopy(style_state(rig)), 'offsets': offsets(rig),
            'meshes': copy.deepcopy(styles.bound_author_meshes(rig)),
            'ids': regional.native_ids(rig, mesh), 'surfaces': surfaces(rig),
            'resources': styles.resources(), 'rig_world': regional.matrix(rig.matrix_world),
            'object_channels': {obj.name: placement._channels(obj) for obj in (rig, mesh)}}

    def assertProtected(self, rig, mesh, names, before, *, root_moved=False):
        self.assertEqual(functional_state(rig), before['functional'])
        self.assertEqual(style_state(rig, allow_offset=names), before['styles'])
        author = styles.bound_author_meshes(rig)
        previous = copy.deepcopy(before['meshes'])
        if root_moved:
            for record in author.values():
                record.pop('matrix')
            for record in previous.values():
                record.pop('matrix')
        self.assertEqual(author, previous)
        self.assertEqual(regional.native_ids(rig, mesh), before['ids'])
        self.assertEqual(styles.resources(), before['resources'])
        for name, translation in before['offsets'].items():
            if name not in names:
                self.assertEqual(tuple(rig.pose.bones[name].custom_shape_translation), translation, name)

    def assertFloor(self, rig, names):
        for name in names:
            points = display_points(rig, name)
            self.assertAlmostEqual(min(point[2] for point in points), 0., delta=1e-5, msg=name)

    def test_horizontal_outlines_use_offsets_only_and_restore_exactly(self):
        rig, mesh, names = self.make()
        rig.rotation_euler, rig.scale, rig.location = (0., 0., 0.), (1., 1., 1.), (0., 0., .4)
        # Give the Master a demonstrably horizontal display without replacing
        # its artist binding, scale or mesh; all three are protected afterward.
        master = rig.pose.bones[root_control.validate(rig)['master']]
        master.custom_shape_rotation_euler = (1.5707963267948966, 0., 0.)
        before = self.snapshot(rig, mesh, names)
        state = floor_widgets.prepare(bpy.context, rig)
        self.assertEqual({entry['name'] for entry in state['entries']}, names)
        proof = floor_widgets.apply(bpy.context, state)
        self.assertEqual(proof['count'], 3)
        self.assertEqual(proof['changed_fields_only'], ['custom_shape_translation'])
        self.assertNotEqual(offsets(rig), before['offsets'])
        self.assertFloor(rig, names)
        self.assertProtected(rig, mesh, names, before)
        self.assertEqual(surfaces(rig), before['surfaces'])
        floor_widgets.restore(bpy.context, state)
        self.assertEqual(offsets(rig), before['offsets'])
        self.assertEqual(style_state(rig), before['all_styles'])

    def test_rotated_nonuniform_world_and_display_rotation_are_preserved(self):
        rig, mesh, names = self.make()
        parent = bpy.data.objects.new('Artist transformed character parent', None)
        bpy.context.scene.collection.objects.link(parent)
        parent.location, parent.rotation_euler, parent.scale = (1.7, -.8, .65), (.31, -.47, .22), (1.3, .8, 1.6)
        rig.parent = parent
        rig.location, rig.rotation_euler, rig.scale = (.2, -.3, .4), (-.21, .32, -.15), (.7, 1.2, .9)
        for name in names:
            pb = rig.pose.bones[name]
            pb.custom_shape_scale_xyz = (.73, 1.14, .89)
            pb.custom_shape_rotation_euler = (.17, -.29, .11)
        before = self.snapshot(rig, mesh, names)
        parent_before = regional.matrix(parent.matrix_world)
        state = floor_widgets.prepare(bpy.context, rig)
        floor_widgets.apply(bpy.context, state)
        self.assertFloor(rig, names)
        self.assertProtected(rig, mesh, names, before)
        self.assertEqual(regional.matrix(parent.matrix_world), parent_before)
        self.assertEqual(regional.matrix(rig.matrix_world), before['rig_world'])
        self.assertEqual(surfaces(rig), before['surfaces'])

    def test_nonplanar_artist_widget_preserves_geometry_and_fits_bottom(self):
        rig, mesh, names = self.make()
        target = rig.pose.bones[limb_ik._validate_inventory(rig)['rigs'][('LEG', 'L')]['target'].name]
        target.custom_shape.data.vertices[0].co.z += .083
        target.custom_shape.data.update()
        before = self.snapshot(rig, mesh, names)
        state = floor_widgets.prepare(bpy.context, rig)
        proof = floor_widgets.apply(bpy.context, state)
        self.assertFloor(rig, names)
        self.assertGreater(proof['controls'][target.name]['z_span'], .01)
        self.assertProtected(rig, mesh, names, before)

    def test_animated_display_is_rejected_before_ground_moves_any_root(self):
        rig, mesh, names = self.make()
        target = rig.pose.bones[limb_ik._validate_inventory(rig)['rigs'][('LEG', 'L')]['target'].name]
        target.keyframe_insert(data_path='custom_shape_translation', frame=bpy.context.scene.frame_current)
        before = self.snapshot(rig, mesh, names)
        with self.assertRaisesRegex(ValueError, 'animated or driven display'):
            placement.ground(bpy.context, rig)
        self.assertEqual(offsets(rig), before['offsets'])
        self.assertEqual(regional.matrix(rig.matrix_world), before['rig_world'])
        self.assertEqual(surfaces(rig), before['surfaces'])
        self.assertProtected(rig, mesh, names, before)

    def test_singular_world_display_is_rejected_before_writes(self):
        rig, _mesh, _names = self.make()
        rig.scale = (0., 1., 1.)
        regional.update(rig)
        before = offsets(rig)
        with self.assertRaisesRegex(ValueError, 'singular or non-finite display frame'):
            floor_widgets.prepare(bpy.context, rig)
        self.assertEqual(offsets(rig), before)

    def test_nonfinite_widget_is_rejected_before_writes(self):
        rig, _mesh, names = self.make()
        rig.pose.bones[sorted(names)[0]].custom_shape.data.vertices[0].co.z = float('nan')
        before = offsets(rig)
        with self.assertRaisesRegex(ValueError, 'finite, nonempty widget geometry'):
            floor_widgets.prepare(bpy.context, rig)
        self.assertEqual(offsets(rig), before)

    def test_modified_widget_geometry_is_refused_without_baking_or_moving(self):
        rig, mesh, names = self.make()
        target = rig.pose.bones[limb_ik._validate_inventory(rig)['rigs'][('LEG', 'L')]['target'].name]
        target.custom_shape.modifiers.new('Artist outline offset', 'SOLIDIFY')
        before = self.snapshot(rig, mesh, names)
        with self.assertRaisesRegex(ValueError, 'modified, keyed or animated widget geometry'):
            placement.ground(bpy.context, rig)
        self.assertEqual(offsets(rig), before['offsets'])
        self.assertEqual(regional.matrix(rig.matrix_world), before['rig_world'])
        self.assertEqual(surfaces(rig), before['surfaces'])
        self.assertProtected(rig, mesh, names, before)

    def test_service_failure_restores_every_live_offset(self):
        rig, mesh, names = self.make()
        before = self.snapshot(rig, mesh, names)
        state = floor_widgets.prepare(bpy.context, rig)
        with patch.object(floor_widgets, 'verify', side_effect=ValueError('Injected display verification failure')):
            with self.assertRaisesRegex(ValueError, 'Injected display verification failure'):
                floor_widgets.apply(bpy.context, state)
        self.assertEqual(offsets(rig), before['offsets'])
        self.assertEqual(style_state(rig), before['all_styles'])
        self.assertEqual(surfaces(rig), before['surfaces'])
        self.assertProtected(rig, mesh, names, before)

    def test_integrated_ground_failure_restores_root_offsets_and_mesh_positions(self):
        rig, mesh, names = self.make()
        before = self.snapshot(rig, mesh, names)
        original = floor_widgets.verify
        calls = []

        def reject_first(context, state):
            proof = original(context, state)
            calls.append(proof)
            if len(calls) == 1:
                self.assertNotEqual(regional.matrix(rig.matrix_world), before['rig_world'])
                self.assertNotEqual(offsets(rig), before['offsets'])
                raise ValueError('Injected after actual Ground and display fitting')
            return proof

        with patch.object(floor_widgets, 'verify', side_effect=reject_first):
            with self.assertRaisesRegex(ValueError, 'Injected after actual Ground'):
                placement.ground(bpy.context, rig)
        self.assertEqual(len(calls), 1)
        self.assertEqual(offsets(rig), before['offsets'])
        self.assertEqual(style_state(rig), before['all_styles'])
        self.assertEqual(regional.matrix(rig.matrix_world), before['rig_world'])
        self.assertEqual({obj.name: placement._channels(obj) for obj in (rig, mesh)}, before['object_channels'])
        self.assertEqual(surfaces(rig), before['surfaces'])
        self.assertProtected(rig, mesh, names, before)

    def test_plain_native_rig_is_empty_but_malformed_owned_rig_is_refused(self):
        rig, _mesh = planning.fixture()
        before = offsets(rig)
        state = floor_widgets.prepare(bpy.context, rig)
        self.assertEqual(state['entries'], [])
        self.assertEqual(floor_widgets.apply(bpy.context, state)['count'], 0)
        self.assertEqual(offsets(rig), before)
        rig.data.bones['foot.L'][limb_ik.OWNER_KEY] = limb_ik.OWNER_VALUE
        with self.assertRaises(limb_ik.LimbIKError):
            floor_widgets.prepare(bpy.context, rig)
        self.assertEqual(offsets(rig), before)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
