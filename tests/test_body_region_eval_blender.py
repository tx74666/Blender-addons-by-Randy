"""Pending native evaluation must not become a regional rebuild's old pose.

Run only in disposable Blender --background --factory-startup fixtures. The
last authored control writes before each operation deliberately have no
view_layer/DG update. An independently evaluated reference is prepared first,
then the exact same writes are repeated against an older evaluated state.

Post-return fresh-DG and same-time frame evaluation cover native invalidation,
not GUI timer dispatch or native Undo. They must not be reported as a complete
GUI/Undo reproduction of the artist crash.
"""
import copy
import json
from pathlib import Path
import sys
import unittest

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_region, control_pose_assets, foot_controls,
                               limb_ik)
import test_body_region_blender as regional
from test_body_rest_resync_blender import bound_meshes, mesh_raw
from test_body_setup_removal_blender import bind_probe_surface


TOLERANCE = 1e-4


def matrix(value):
    return tuple(tuple(row) for row in value)


def matrix_error(first, second):
    return max(abs(a - b) for row_a, row_b in zip(first, second)
               for a, b in zip(row_a, row_b))


def _update(rig):
    rig.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    graph.update()
    return graph


def evaluated_state(rig):
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated_rig = rig.evaluated_get(graph)
    native = control_pose_assets.native_rest(rig)
    result = {
        'pose': {name: matrix(evaluated_rig.pose.bones[name].matrix) for name in native},
        'skin': {name: matrix(evaluated_rig.pose.bones[name].matrix
                              @ rig.data.bones[name].matrix_local.inverted()) for name in native},
        'world_objects': {rig.name: matrix(evaluated_rig.matrix_world)}, 'surfaces': {},
    }
    for obj in bound_meshes(rig):
        evaluated = obj.evaluated_get(graph)
        result['world_objects'][obj.name] = matrix(evaluated.matrix_world)
        data = evaluated.to_mesh()
        try:
            result['surfaces'][obj.name] = [tuple(evaluated.matrix_world @ vertex.co)
                                            for vertex in data.vertices]
        finally:
            evaluated.to_mesh_clear()
    return result


def authored_meshes(rig):
    return {obj.name: mesh_raw(obj) for obj in bound_meshes(rig)}


def channel_state(rig, names):
    return {name: {'rotation_mode': rig.pose.bones[name].rotation_mode,
                   'location': tuple(rig.pose.bones[name].location),
                   'rotation_euler': tuple(rig.pose.bones[name].rotation_euler),
                   'rotation_quaternion': tuple(rig.pose.bones[name].rotation_quaternion),
                   'rotation_axis_angle': tuple(rig.pose.bones[name].rotation_axis_angle),
                   'scale': tuple(rig.pose.bones[name].scale)} for name in names}


def set_channels(rig, state):
    for name, values in state.items():
        pb = rig.pose.bones[name]
        pb.rotation_mode = values['rotation_mode']
        for field, value in values.items():
            if field != 'rotation_mode':
                setattr(pb, field, value)


def apply_edit(rig, edit):
    """Native property writes only: no tag/update/evaluated reads here."""
    for name, field, index, value in edit:
        getattr(rig.pose.bones[name], field)[index] = value


class BodyRegionEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Pending-evaluation checks require disposable background Blender.')
        character_designer.register()

    def make(self, *, mode='POSE'):
        rig, mesh = regional.BodyRegionTests().make(corrective=False)
        shoes = bind_probe_surface(rig)
        shoes.name = 'Pending evaluation shoes'
        # A legal authored mesh/object offset is not an instruction to move
        # either geometry back to a controller's old origin during rebuilding.
        shoes.location.z += .0745561
        for vertex in shoes.data.vertices:
            vertex.co.z += .013
        bpy.context.view_layer.objects.active = rig
        rig.select_set(True)
        bpy.ops.object.mode_set(mode=mode)
        self.assertEqual(rig.data.pose_position, 'POSE')
        _update(rig)
        return rig, mesh, shoes

    def prepare_pending(self, rig, *, sides=('L', 'R'), target=True, toe=True, roll=True):
        inventory = limb_ik._validate_inventory(rig)
        edits, names = [], set()
        for side in sides:
            entry = inventory['rigs'][('LEG', side)]
            foot = foot_controls.get_record(rig, side)
            if target:
                name = entry['target'].name
                edits.append((name, 'location', 2, rig.pose.bones[name].location.z + .021))
                names.add(name)
            if toe:
                name = foot['toe_control']
                edits.append((name, 'location', 2, rig.pose.bones[name].location.z + .018))
                names.add(name)
            if roll:
                name = foot['roll']
                edits.append((name, 'rotation_euler', 0,
                              rig.pose.bones[name].rotation_euler.x + .035))
                names.add(name)
        baseline = channel_state(rig, names)
        # Reference is the evaluated result of the intended new inputs.
        apply_edit(rig, edits)
        _update(rig)
        expected = evaluated_state(rig)
        # Restore the previous evaluation and leave the new inputs pending.
        set_channels(rig, baseline)
        _update(rig)
        previous_original = {name: matrix(rig.pose.bones[name].matrix) for name in expected['pose']}
        return edits, expected, previous_original

    def assert_evaluated_equal(self, rig, expected, *, stage):
        actual = evaluated_state(rig)
        self.assertEqual(actual['world_objects'].keys(), expected['world_objects'].keys())
        for name, wanted in expected['world_objects'].items():
            self.assertLessEqual(matrix_error(actual['world_objects'][name], wanted), 2e-6,
                                 (stage, 'world object', name))
        for name, wanted in expected['skin'].items():
            self.assertLessEqual(matrix_error(actual['skin'][name], wanted), 2e-4,
                                 (stage, 'evaluated skin', name))
        self.assertEqual(actual['surfaces'].keys(), expected['surfaces'].keys())
        for name, wanted in expected['surfaces'].items():
            observed = actual['surfaces'][name]
            self.assertEqual(len(observed), len(wanted), (stage, name))
            error = max(((Vector(a) - Vector(b)).length for a, b in zip(observed, wanted)), default=0.)
            self.assertLessEqual(error, TOLERANCE, (stage, 'world surface', name, error))

    def exercise(self, *, mode='POSE', selection='LEGS', public=False,
                 hidden=False, animated_subframe=False):
        rig, mesh, shoes = self.make(mode=mode)
        scene = bpy.context.scene
        if animated_subframe:
            rig.location.z = .04
            rig.keyframe_insert(data_path='location', frame=2)
            rig.location.z = .21
            rig.keyframe_insert(data_path='location', frame=3)
            scene.frame_set(2, subframe=.375)
            _update(rig)
        sides = ('L',) if selection == 'LEFT_LEG' else ('L', 'R')
        edits, expected, previous_original = self.prepare_pending(rig, sides=sides)
        if hidden:
            shoes.hide_set(True)
            _update(rig)
        time_before = (scene.frame_current, scene.frame_subframe)
        rest_before = copy.deepcopy(control_pose_assets.native_rest(rig))
        author_before = authored_meshes(rig)
        mode_before = rig.mode
        # No evaluator, frame setter, mode change, or update is permitted
        # between this write and the operation under test.
        apply_edit(rig, edits)
        pending_original = {name: matrix(rig.pose.bones[name].matrix) for name in expected['pose']}
        print('BODY_REGION_PENDING_INPUT', json.dumps({
            'selection': selection, 'mode': mode, 'public': public, 'hidden': hidden,
            'subframe': time_before[1],
            'original_cache_unchanged': pending_original == previous_original,
            'original_vs_expected_max': max(matrix_error(pending_original[n], expected['pose'][n])
                                             for n in expected['pose']),
        }), flush=True)
        if public:
            result = bpy.ops.character_designer.body_region(action='REBUILD', region=selection)
            self.assertEqual(result, {'FINISHED'})
        else:
            body_region.rebuild(bpy.context, rig, selection)
        self.assertEqual((scene.frame_current, scene.frame_subframe), time_before)
        self.assertEqual(rig.mode, mode_before)
        self.assertEqual(rig.data.pose_position, 'POSE')
        self.assertEqual(control_pose_assets.native_rest(rig), rest_before)
        self.assertEqual(authored_meshes(rig), author_before)
        self.assertEqual(shoes.hide_get(), hidden)
        # Revealing a previously hidden bound mesh must evaluate to the same
        # intended pose; an obsolete hidden display is not a valid reference.
        if hidden:
            shoes.hide_set(False)
        _update(rig)
        self.assert_evaluated_equal(rig, expected, stage='after public return and fresh graph')
        scene.frame_set(time_before[0], subframe=time_before[1])
        _update(rig)
        self.assert_evaluated_equal(rig, expected, stage='after same-time frame reevaluation')
        self.assertEqual((scene.frame_current, scene.frame_subframe), time_before)
        self.assertEqual(authored_meshes(rig), author_before)

    def test_pending_target_roll_and_toe_inputs_preserve_all_world_meshes(self):
        self.exercise()

    def test_public_pose_operator_preserves_pending_single_leg_inputs(self):
        self.exercise(selection='LEFT_LEG', public=True)

    def test_object_operator_keeps_hidden_bound_shoes_at_the_same_intended_pose(self):
        self.exercise(mode='OBJECT', public=True, hidden=True)

    def test_subframe_and_animated_world_placement_survive_public_rebuild(self):
        self.exercise(public=True, animated_subframe=True)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BodyRegionEvaluationTests))
    if not result.wasSuccessful():
        raise RuntimeError('Pending evaluation regional tests failed')
