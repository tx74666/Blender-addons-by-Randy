"""Planar Foot/Master display alignment without functional rig edits.

Isolated native background fixtures only. These extend (and do not rewrite)
the original offset-only floor proofs after real-model tilted soles exposed
the need to rotate planar display outlines as well as translate them.
"""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import character_ground as placement, floor_widgets, limb_ik
import test_body_region_blender as regional
import test_body_region_styles_blender as styles
import test_floor_widgets_blender as offsets


def display_styles(rig, names):
    result = offsets.style_state(rig)
    for name in names:
        result[name]['display'].pop('translation')
        result[name]['display'].pop('rotation')
    return result


def rotations(rig):
    return {pb.name: tuple(pb.custom_shape_rotation_euler) for pb in rig.pose.bones}


def headings(rig, names):
    result = {}
    for name in names:
        points = offsets.display_points(rig, name)
        # The source plane uses the first nondegenerate edge from vertex zero.
        # Fixture edges are not vertically viewed, so this directly tests the
        # preserved projected heading, independently of the fitting helper.
        first = Vector(points[0])
        direction = next(Vector(point) - first for point in points[1:]
                         if (Vector(point) - first).length > 1e-10)
        direction.z = 0.
        if direction.length < 1e-7:
            raise AssertionError('Orientation fixture needs a projected first edge: ' + name)
        result[name] = tuple(direction.normalized())
    return result


class FloorWidgetsOrientationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Planar floor-display checks require isolated background Blender.')
        # A combined runner imports the original ten tests first. Avoid
        # replacing native RNA classes already registered by that fixture.
        if not hasattr(bpy.types.Scene, 'character_designer_ground'):
            character_designer.register()

    def make(self):
        rig, mesh, names = offsets.FloorWidgetsTests().make()
        for index, name in enumerate(sorted(names)):
            pb = rig.pose.bones[name]
            pb.custom_shape_rotation_euler = (.39 + .06 * index, -.24, .19)
        regional.update(rig)
        return rig, mesh, names

    def snapshot(self, rig, mesh, names):
        regional.update(rig)
        return {'functional': copy.deepcopy(offsets.functional_state(rig)),
            'styles': copy.deepcopy(display_styles(rig, names)),
            'all_styles': copy.deepcopy(offsets.style_state(rig)),
            'offsets': offsets.offsets(rig), 'rotations': rotations(rig),
            'meshes': copy.deepcopy(styles.bound_author_meshes(rig)),
            'ids': regional.native_ids(rig, mesh), 'surfaces': offsets.surfaces(rig),
            'resources': styles.resources(),
            'object_channels': {obj.name: placement._channels(obj) for obj in (rig, mesh)},
            'rig_world': regional.matrix(rig.matrix_world)}

    def assertProtected(self, rig, mesh, names, before, *, root_moved=False):
        self.assertEqual(offsets.functional_state(rig), before['functional'])
        self.assertEqual(display_styles(rig, names), before['styles'])
        self.assertEqual(regional.native_ids(rig, mesh), before['ids'])
        self.assertEqual(styles.resources(), before['resources'])
        actual = styles.bound_author_meshes(rig)
        previous = copy.deepcopy(before['meshes'])
        if root_moved:
            for records in (actual, previous):
                for record in records.values():
                    record.pop('matrix')
        self.assertEqual(actual, previous)
        for name in set(before['offsets']) - names:
            self.assertEqual(tuple(rig.pose.bones[name].custom_shape_translation), before['offsets'][name])
            self.assertEqual(tuple(rig.pose.bones[name].custom_shape_rotation_euler), before['rotations'][name])

    def assertPlanarFloor(self, rig, names):
        for name in names:
            points = offsets.display_points(rig, name)
            self.assertLessEqual(max(abs(point[2]) for point in points), 1e-5, name)
            self.assertLessEqual(max(point[2] for point in points) - min(point[2] for point in points), 1e-5, name)

    def assertHeadings(self, rig, expected):
        for name, direction in headings(rig, expected).items():
            self.assertGreaterEqual(Vector(direction).dot(Vector(expected[name])), 1. - 2e-6, name)

    def test_ground_levels_all_planar_displays_and_preserves_functional_inputs(self):
        rig, mesh, names = self.make()
        before = self.snapshot(rig, mesh, names)
        # Ground translates the root only; expected horizontal headings do not
        # change under that translation in this fixture.
        expected = headings(rig, names)
        result = placement.ground(bpy.context, rig)
        self.assertEqual(result['floor_widgets']['align_planes'], True)
        self.assertEqual(set(result['floor_widgets']['changed_fields_only']),
                         {'custom_shape_translation', 'custom_shape_rotation_euler'})
        self.assertTrue(all(row['planar_alignment'] and row['all_vertices_on_floor']
                            for row in result['floor_widgets']['controls'].values()))
        self.assertAlmostEqual(result['lowest_after'], 0., delta=result['lowest_tolerance'])
        self.assertNotEqual(rotations(rig), before['rotations'])
        self.assertPlanarFloor(rig, names)
        self.assertHeadings(rig, expected)
        self.assertProtected(rig, mesh, names, before, root_moved=True)

    def test_nonuniform_sheared_world_preserves_projected_heading_and_styles(self):
        rig, mesh, names = self.make()
        parent = bpy.data.objects.new('Artist sheared display parent', None)
        bpy.context.scene.collection.objects.link(parent)
        parent.location, parent.rotation_euler, parent.scale = (1.3, -.6, .8), (.37, -.43, .29), (1.6, .7, 1.25)
        rig.parent = parent
        rig.location, rig.rotation_euler, rig.scale = (.2, -.1, .3), (-.23, .34, -.16), (.8, 1.3, .9)
        for name in names:
            pb = rig.pose.bones[name]
            pb.custom_shape_scale_xyz = (.71, 1.19, .83)
        # A planar shape need not lie through its raw origin. Translation of
        # that plane is handled after rotation without rewriting the vertices.
        left = limb_ik._validate_inventory(rig)['rigs'][('LEG', 'L')]['target'].name
        for vertex in rig.pose.bones[left].custom_shape.data.vertices:
            vertex.co.z += .14
        before = self.snapshot(rig, mesh, names)
        expected = headings(rig, names)
        parent_before = regional.matrix(parent.matrix_world)
        state = floor_widgets.prepare(bpy.context, rig, align_planes=True)
        floor_widgets.apply(bpy.context, state)
        self.assertPlanarFloor(rig, names)
        self.assertHeadings(rig, expected)
        self.assertProtected(rig, mesh, names, before)
        self.assertEqual(regional.matrix(parent.matrix_world), parent_before)
        self.assertEqual(regional.matrix(rig.matrix_world), before['rig_world'])
        self.assertEqual(offsets.surfaces(rig), before['surfaces'])

    def test_nonplanar_artist_shape_keeps_rotation_and_only_fits_bottom(self):
        rig, mesh, names = self.make()
        left = limb_ik._validate_inventory(rig)['rigs'][('LEG', 'L')]['target'].name
        rig.pose.bones[left].custom_shape.data.vertices[0].co.z += .083
        before = self.snapshot(rig, mesh, names)
        state = floor_widgets.prepare(bpy.context, rig, align_planes=True)
        proof = floor_widgets.apply(bpy.context, state)
        row = proof['controls'][left]
        self.assertFalse(row['planar_alignment'])
        self.assertFalse(row['all_vertices_on_floor'])
        self.assertGreater(row['z_span'], .01)
        self.assertAlmostEqual(row['minimum_world_z'], 0., delta=1e-5)
        self.assertEqual(tuple(rig.pose.bones[left].custom_shape_rotation_euler), before['rotations'][left])
        self.assertPlanarFloor(rig, names - {left})
        self.assertProtected(rig, mesh, names, before)

    def test_ground_failure_restores_both_display_fields_and_root_exactly(self):
        rig, mesh, names = self.make()
        before = self.snapshot(rig, mesh, names)
        original = floor_widgets.verify
        calls = []

        def fail_after_fit(context, state):
            proof = original(context, state)
            calls.append(proof)
            if len(calls) == 1:
                self.assertNotEqual(rotations(rig), before['rotations'])
                self.assertNotEqual(offsets.offsets(rig), before['offsets'])
                self.assertNotEqual(regional.matrix(rig.matrix_world), before['rig_world'])
                raise ValueError('Injected after planar Ground rotation')
            return proof

        with patch.object(floor_widgets, 'verify', side_effect=fail_after_fit):
            with self.assertRaisesRegex(ValueError, 'Injected after planar Ground rotation'):
                placement.ground(bpy.context, rig)
        self.assertEqual(len(calls), 1)
        self.assertEqual(rotations(rig), before['rotations'])
        self.assertEqual(offsets.offsets(rig), before['offsets'])
        self.assertEqual(offsets.style_state(rig), before['all_styles'])
        self.assertEqual(regional.matrix(rig.matrix_world), before['rig_world'])
        self.assertEqual({obj.name: placement._channels(obj) for obj in (rig, mesh)}, before['object_channels'])
        self.assertEqual(offsets.surfaces(rig), before['surfaces'])
        self.assertProtected(rig, mesh, names, before)

    def test_second_planar_fit_keeps_euler_and_floor(self):
        rig, mesh, names = self.make()
        floor_widgets.apply(bpy.context, floor_widgets.prepare(bpy.context, rig, align_planes=True))
        before = self.snapshot(rig, mesh, names)
        floor_widgets.apply(bpy.context, floor_widgets.prepare(bpy.context, rig, align_planes=True))
        self.assertEqual(rotations(rig), before['rotations'])
        self.assertPlanarFloor(rig, names)
        self.assertProtected(rig, mesh, names, before)
        self.assertLessEqual(max(abs(a-b) for name in names for a,b in zip(
            rig.pose.bones[name].custom_shape_translation, before['offsets'][name])), 1e-6)


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]], verbosity=2)
