"""Scene-less placement branches, in a disposable native Blender process."""
import sys
import unittest
from pathlib import Path

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import character_ground as placement
import test_character_ground_blender as fixtures

matrix, raw_mesh = fixtures.matrix, fixtures.raw_mesh


def orphan(name, parent, *, empty=False):
    data = None
    if not empty:
        data = bpy.data.meshes.new(name + ' data')
        data.from_pydata(((-.1, 0., -90.), (.1, 0., -90.), (0., .1, -89.8)),
                        (), ((0, 1, 2),))
    obj = bpy.data.objects.new(name, data)
    obj.parent = parent
    obj.location = (.13, -.17, .29)
    obj.rotation_mode = 'XYZ'
    obj.rotation_euler = (.1, -.2, .3)
    obj.delta_location = (.02, -.01, .04)
    obj['artist_marker'] = 'keep this unlinked object'
    return obj


def inputs(obj):
    return (obj.as_pointer(), obj.data.as_pointer() if obj.data else None,
            obj.parent.as_pointer() if obj.parent else None,
            obj.parent_type, obj.parent_bone, tuple(obj.parent_vertices),
            placement._channels(obj), matrix(obj.matrix_basis),
            tuple(sorted(scene.as_pointer() for scene in obj.users_scene)),
            tuple(sorted(collection.as_pointer() for collection in obj.users_collection)),
            obj.get('artist_marker'), raw_mesh(obj) if obj.type == 'MESH' else None)


def resources():
    return tuple(tuple(sorted(item.as_pointer() for item in collection))
                 for collection in (bpy.data.objects, bpy.data.meshes,
                                    bpy.data.collections, bpy.data.scenes))


class CharacterGroundScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.CharacterPlacementTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.CharacterPlacementTests()
        self.fixture.setUp()
        self.rig = self.fixture.rig

    def assert_ignored_branch_grounded(self, branch):
        bpy.context.view_layer.update()
        before = {obj: inputs(obj) for obj in branch}
        ids = resources()
        scene_objects = (self.rig, self.fixture.body, self.fixture.shoes)
        world = {obj: obj.matrix_world.copy() for obj in scene_objects}
        internal = placement._rig_state(self.rig)
        raw = raw_mesh(self.fixture.body), raw_mesh(self.fixture.shoes)
        self.assertTrue(placement._scene_less_subtree(branch[0]))
        result = placement.ground(bpy.context, self.rig)
        self.assertAlmostEqual(result['lowest_before'], .25, delta=3e-5)
        self.assertAlmostEqual(result['lowest_after'], 0., delta=3e-5)
        self.assertEqual(set(result['roots']), {self.rig.name, self.fixture.shoes.name})
        self.assertTrue(all(obj.name not in result['objects'] for obj in branch))
        translation = Matrix.Translation((0., 0., result['distance']))
        for obj in scene_objects:
            self.fixture.assertMatrix(obj.matrix_world, translation @ world[obj])
        for obj, state in before.items():
            # No-scene IDs need not have an evaluated world matrix. Their
            # unchanged local inputs and parent chain keep inheritance intact;
            # no second root offset or scene link is written to these objects.
            self.assertEqual(inputs(obj), state)
            self.assertFalse(obj.users_scene)
        self.assertEqual(resources(), ids)
        self.assertEqual(placement._rig_state(self.rig), internal)
        self.assertEqual((raw_mesh(self.fixture.body), raw_mesh(self.fixture.shoes)), raw)

    def test_orphan_mesh_leaf_does_not_block_or_supply_the_lowest_point(self):
        child = orphan('Artist orphan mesh', self.rig)
        self.assertFalse(child.users_collection)
        self.assert_ignored_branch_grounded([child])

    def test_entire_scene_less_subtree_keeps_ids_channels_and_parent_links(self):
        parent = orphan('Unlinked subtree', self.rig, empty=True)
        child = orphan('Unlinked mesh', parent)
        grandchild = orphan('Unlinked grandchild', child, empty=True)
        # An unlinked collection is still not scene membership. Do not delete
        # it or rely on generated ownership/name heuristics to allow the branch.
        collection = bpy.data.collections.new('Artist stored branch')
        collection.objects.link(child)
        self.assertTrue(child.users_collection)
        self.assert_ignored_branch_grounded([parent, child, grandchild])

    def assert_refused_without_placement(self, branch, witness):
        bpy.context.view_layer.update()
        objects = tuple(bpy.data.objects)
        before = {obj: inputs(obj) for obj in objects}
        current_world = {obj: matrix(obj.matrix_world) for obj in bpy.context.scene.objects}
        ids = resources()
        internal = placement._rig_state(self.rig)
        self.assertFalse(placement._scene_less_subtree(branch))
        for operation in (lambda: placement.move(bpy.context, self.rig, .3),
                          lambda: placement.ground(bpy.context, self.rig)):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError) as caught:
                    operation()
                self.assertIn(branch.name, str(caught.exception))
                self.assertIn('outside the current scene', str(caught.exception))
                for obj, state in before.items():
                    self.assertEqual(inputs(obj), state)
                for obj, state in current_world.items():
                    self.assertEqual(matrix(obj.matrix_world), state)
                self.assertEqual(resources(), ids)
                self.assertEqual(placement._rig_state(self.rig), internal)
                self.assertIs(witness.parent, branch if witness != branch else self.rig)

    def test_child_in_another_scene_is_refused_before_root_movement(self):
        child = orphan('Other scene child', self.rig)
        other = bpy.data.scenes.new('Separate artist scene')
        other.collection.objects.link(child)
        self.assert_refused_without_placement(child, child)

    def test_scene_less_intermediary_cannot_hide_other_scene_descendants(self):
        bridge = orphan('Unlinked external bridge', self.rig, empty=True)
        child = orphan('External descendant', bridge)
        other = bpy.data.scenes.new('Separate descendant scene')
        other.collection.objects.link(child)
        self.assertFalse(bridge.users_scene)
        self.assert_refused_without_placement(bridge, child)

    def test_scene_less_intermediary_with_current_scene_child_is_refused(self):
        bridge = orphan('Unlinked current bridge', self.rig, empty=True)
        child = orphan('Current scene descendant', bridge)
        bpy.context.scene.collection.objects.link(child)
        self.assertFalse(bridge.users_scene)
        self.assertIn(child.name, bpy.context.scene.objects)
        self.assert_refused_without_placement(bridge, child)


if __name__ == '__main__':
    if not bpy.app.background or bpy.data.filepath or '--factory-startup' not in sys.argv:
        raise RuntimeError('Use an empty background factory process, never an artist file.')
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(CharacterGroundScopeTests))
    if not result.wasSuccessful():
        raise SystemExit(1)
