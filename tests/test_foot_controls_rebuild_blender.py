"""Foot-only rebuild contract in a disposable native Blender fixture.

Run only with --background --factory-startup, never against the artist scene.
No test scene is saved: the retained-Action case uses real native ID references.
"""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_mode as original, body_original_owner,
    body_region, body_setup, control_colors, control_pose_assets, foot_controls,
    foot_controls_rebuild as service, limb_ik, limb_ik_fk, unity_animation)
import test_body_region_blender as regional
from test_body_rest_resync_blender import mesh_raw, world_surfaces
from test_body_setup_removal_blender import bind_probe_surface
from test_original_leg_rest_return_blender import make_fixture, edit_legs
from test_original_return_owner_blender import retain_action


def shape_state(rig, sides):
    """Compare real edited mesh data and artist settings, allowing new owner IDs."""
    result = {}
    for side in sides:
        record = foot_controls.get_record(rig, side)
        for role in ('FOOT_ROLL', 'TOE_BEND'):
            pb = rig.pose.bones[record['bones'][role]]
            obj, mesh = pb.custom_shape, pb.custom_shape.data
            attribute = mesh.attributes['Artist foot profile']
            result[(side, role)] = {
                'names': (obj.name, mesh.name),
                'vertices': tuple(tuple(vertex.co) for vertex in mesh.vertices),
                'edges': tuple(tuple(edge.vertices) for edge in mesh.edges),
                'faces': tuple(tuple(face.vertices) for face in mesh.polygons),
                'artist_attribute': (attribute.data_type, attribute.domain,
                                     tuple(item.value for item in attribute.data)),
                'materials': tuple(material.name if material else None for material in mesh.materials),
                'object_display': (obj.display_type, obj.show_in_front, obj.hide_render,
                                   tuple(obj.color), tuple(obj.location), tuple(obj.scale)),
                'display': copy.deepcopy(limb_ik._pose_shape_json_state(pb)),
                'locks': (tuple(pb.lock_location), tuple(pb.lock_rotation), tuple(pb.lock_scale)),
                'colors': copy.deepcopy(control_colors.capture_bone(pb)),
                'hidden': (pb.bone.hide, pb.bone.hide_select),
                'artist_properties': (pb.get('artist_foot_note'), pb.bone.get('artist_foot_note'),
                                      obj.get('artist_foot_note'), mesh.get('artist_foot_note')),
                'roll_visual_metadata': {
                    field: copy.deepcopy(record[field]) for field in
                    ('roll_visual_backup', 'roll_visual_fit') if field in record},
            }
    return result


def customize_shapes(rig, sides=('L', 'R')):
    for side in sides:
        # Exercise a real Fit Arrow backup, including its previous fit record.
        foot_controls.fit_roll_visual(bpy.context, rig, ('LEG', side))
        record = foot_controls.get_record(rig, side)
        if not record.get('roll_visual_backup') or not record.get('roll_visual_fit'):
            raise AssertionError('The fixture must contain a real Roll Visual backup and fit.')
        for index, role in enumerate(('FOOT_ROLL', 'TOE_BEND')):
            pb = rig.pose.bones[record['bones'][role]]
            obj, mesh = pb.custom_shape, pb.custom_shape.data
            for vertex in mesh.vertices:
                vertex.co.x = vertex.co.x * 1.19 + .013
                vertex.co.y = vertex.co.y * .83 - .007
                vertex.co.z += .004 * (vertex.index % 3)
            attribute = mesh.attributes.new('Artist foot profile', 'FLOAT', 'POINT')
            for item_index, item in enumerate(attribute.data):
                item.value = .125 + item_index * .03125
            material = bpy.data.materials.new(f'Artist {side} {role} material')
            mesh.materials.append(material)
            obj.display_type, obj.show_in_front, obj.hide_render = 'WIRE', True, False
            obj.color, obj.location, obj.scale = (.2, .4, .6, 1.), (.01, -.02, .03), (1.1, .9, 1.05)
            pb.custom_shape_scale_xyz = (1.2, .8, 1.1)
            pb.custom_shape_translation, pb.custom_shape_rotation_euler = (.01, -.02, .03), (.05, -.04, .02)
            pb.use_custom_shape_bone_size = False
            if hasattr(pb, 'custom_shape_wire_width'):
                pb.custom_shape_wire_width = 3.25
            pb.lock_location, pb.lock_rotation, pb.lock_scale = (True, False, True), (False, True, False), (True, True, False)
            pb.color.palette = 'CUSTOM'
            pb.color.custom.normal, pb.color.custom.select, pb.color.custom.active = (.15, .35, .55), (.25, .45, .65), (.35, .55, .75)
            pb.bone.hide, pb.bone.hide_select = bool(index), not bool(index)
            for owner in (pb, pb.bone, obj, mesh):
                owner['artist_foot_note'] = f'Keep my {side} {role} customization'
            mesh.update()
    regional.update(rig)


def foot_names(rig, sides):
    return {name for side in sides for name in foot_controls.get_record(rig, side)['bones'].values()}


class FootControlsRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def make(self):
        rig, mesh = regional.BodyRegionTests().make(corrective=False)
        for side in ('L', 'R'):
            limb_ik_fk.switch_limb(bpy.context, rig, ('LEG', side), 'IK', keyframe=False)
        bind_probe_surface(rig)
        customize_shapes(rig)
        return rig, mesh

    def test_transaction_presence_distinguishes_live_ids_from_reused_addresses(self):
        transaction = regional.transaction
        for label, collection, create in (
                ('Object', bpy.data.objects, lambda name: bpy.data.objects.new(name, None)),
                ('Mesh', bpy.data.meshes, bpy.data.meshes.new),
                ('Collection', bpy.data.collections, bpy.data.collections.new)):
            with self.subTest(datablock=label):
                original = fresh = None
                try:
                    original = create('Foot rebuild presence original ' + label)
                    fresh = create('Foot rebuild presence fresh ' + label)
                    originals = {original.as_pointer(): original}
                    self.assertTrue(transaction._was_present(original, originals))
                    self.assertFalse(transaction._was_present(fresh, originals))
                    collection.remove(original)
                    with self.assertRaises(ReferenceError):
                        _name = original.name
                    # Deterministically model the native allocator reusing an
                    # old address. The stale RNA reference must win over a
                    # matching numeric key: fresh is a new owned resource.
                    reused_address = {fresh.as_pointer(): original}
                    self.assertFalse(transaction._was_present(fresh, reused_address))
                    self.assertTrue(transaction._was_present(fresh, {fresh.as_pointer(): fresh}))
                finally:
                    for resource in (fresh, original):
                        if transaction._pointer(resource):
                            collection.remove(resource)

    def protected_inputs(self, rig, mesh, sides):
        untouched = set(rig.data.bones.keys()) - regional.leg_names(rig, sides)
        inventory = limb_ik._validate_inventory(rig)
        poles = {inventory['rigs'][('LEG', side)]['pole'].name for side in sides}
        untouched |= poles
        regional.update(rig)
        return {
            'rest': control_pose_assets.native_rest(rig),
            'ids': regional.native_ids(rig, mesh),
            # Bone wrappers are reconstructed by Blender during native mode
            # changes; their pointers are not persistent datablock identities.
            # Preserve the exact native inventory, provenance and Rest instead.
            'native_provenance': {bone.name: regional.properties(bone) for bone in rig.data.bones
                                  if bone.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS},
            'mesh': {obj.name: mesh_raw(obj) for obj in bpy.data.objects if obj.type == 'MESH'
                     and any(mod.type == 'ARMATURE' and mod.object == rig for mod in obj.modifiers)},
            'untouched': regional.bone_state(rig, untouched),
            'shapes': shape_state(rig, sides),
            'skin': {name: regional.matrix(matrix) for name, matrix in body_setup._native_skin(rig).items()},
            'surfaces': world_surfaces(rig),
        }

    def assert_rebuilt(self, rig, mesh, sides, before, result):
        regional.update(rig)
        self.assertEqual(result['sides'], sides)
        self.assertFalse(result['pole_rebuilt'])
        self.assertTrue(result['custom_shapes_preserved'])
        self.assertEqual(control_pose_assets.native_rest(rig), before['rest'])
        self.assertEqual(regional.native_ids(rig, mesh), before['ids'])
        self.assertEqual({bone.name: regional.properties(bone) for bone in rig.data.bones
                          if bone.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS},
                         before['native_provenance'])
        untouched = regional.bone_state(rig, before['untouched'])
        changes = regional.changed_fields(untouched, before['untouched'])
        self.assertFalse(changes, f'Untouched bone inputs differ (path, actual, expected): {changes!r}')
        self.assertEqual(shape_state(rig, sides), before['shapes'])
        self.assertEqual({name: mesh_raw(bpy.data.objects[name]) for name in before['mesh']}, before['mesh'])
        current_skin = body_setup._native_skin(rig)
        maximum = max(abs(a-b) for name, matrix in before['skin'].items()
                      for row_a, row_b in zip(matrix, current_skin[name]) for a, b in zip(row_a, row_b))
        self.assertLessEqual(maximum, 3e-4)
        surfaces = world_surfaces(rig)
        self.assertEqual(set(surfaces), set(before['surfaces']))
        for name, points in before['surfaces'].items():
            self.assertEqual(len(surfaces[name]), len(points))
            self.assertLessEqual(max(((a-b).length for a, b in zip(surfaces[name], points)), default=0.), 3e-4, name)
        # Check independent evaluated ankle/ball/toe points, not only the receipt.
        for side in sides:
            record = foot_controls.get_record(rig, side)
            foot, toe = rig.pose.bones[record['chain'][2]], rig.pose.bones[record['toe']]
            for role, point in (('ANKLE_SOLVER', foot.head), ('BALL_PIVOT', toe.head), ('TOE_TIP_PIVOT', toe.tail)):
                helper = rig.pose.bones[record['bones'][role]]
                self.assertLessEqual((rig.matrix_world @ helper.head - rig.matrix_world @ point).length, 3e-4)
            self.assertLessEqual(max(result['anchor_errors_world'][side].values()), 3e-4)
        limb_ik._validate_inventory(rig)

    def exercise_rebuild(self, sides):
        rig, mesh = self.make()
        # An unkeyed Roll/Toe input is absorbed into the new helper zero pose.
        for side in sides:
            record = foot_controls.get_record(rig, side)
            rig.pose.bones[record['roll']].rotation_euler.x = .035
            rig.pose.bones[record['toe_control']].rotation_euler.x = .025
        regional.update(rig)
        before = self.protected_inputs(rig, mesh, sides)
        result = service.rebuild(bpy.context, rig, sides=sides)
        self.assert_rebuilt(rig, mesh, sides, before, result)

    def test_one_foot_preserves_finished_hand_other_leg_and_edited_widgets(self):
        self.exercise_rebuild(('L',))

    def test_both_feet_preserve_native_rest_skin_and_edited_widgets(self):
        self.exercise_rebuild(('L', 'R'))

    def rollback_after(self, stage):
        rig, mesh = self.make()
        sides = ('L', 'R')
        before, ids = regional.scene_state(rig, mesh), regional.native_ids(rig, mesh)
        shapes, recreated = shape_state(rig, sides), foot_names(rig, sides)
        skin = {name: regional.matrix(matrix) for name, matrix in body_setup._native_skin(rig).items()}
        surfaces = world_surfaces(rig)
        real, calls = getattr(foot_controls, stage), []

        def fail_after_native_change(*args, **kwargs):
            result = real(*args, **kwargs)
            calls.append(args[2])
            # Fail on R after L has already completed its rebuild.
            if len(calls) == 2:
                raise ValueError('Injected failure after native foot ' + stage)
            return result

        with patch.object(foot_controls, stage, side_effect=fail_after_native_change):
            with self.assertRaisesRegex(ValueError, 'Injected failure'):
                service.rebuild(bpy.context, rig, sides=sides)
        self.assertEqual(len(calls), 2)
        regional.BodyRegionTests().assertRecoveredScene(regional.scene_state(rig, mesh), before, recreated=recreated)
        regional.BodyRegionTests().assertDeformationRecovered(rig, skin, surfaces)
        self.assertEqual(regional.native_ids(rig, mesh), ids)
        self.assertEqual(shape_state(rig, sides), shapes)
        self.assertIsNone(body_region._OWNER_OPERATION)
        limb_ik._validate_inventory(rig)

    def test_failure_after_remove_restores_complete_native_graph_and_custom_shapes(self):
        self.rollback_after('remove')

    def test_failure_after_build_restores_complete_native_graph_and_custom_shapes(self):
        self.rollback_after('build')

    def assert_refused(self, rig, mesh):
        before, ids = regional.scene_state(rig, mesh), regional.native_ids(rig, mesh)
        shapes = shape_state(rig, ('L', 'R'))
        with self.assertRaises(ValueError):
            service.rebuild(bpy.context, rig, sides=('L',))
        self.assertEqual(regional.scene_state(rig, mesh), before)
        self.assertEqual(regional.native_ids(rig, mesh), ids)
        self.assertEqual(shape_state(rig, ('L', 'R')), shapes)

    def test_selected_control_or_native_toe_animation_refuses_unchanged(self):
        for control in (True, False):
            with self.subTest(control=control):
                rig, mesh = self.make()
                record = foot_controls.get_record(rig, 'L')
                pb = rig.pose.bones[record['roll'] if control else record['toe']]
                self.assertTrue(pb.keyframe_insert('rotation_euler', frame=1))
                self.assert_refused(rig, mesh)

    def test_real_external_driver_and_hook_to_native_toe_refuse_unchanged(self):
        for dependency in ('DRIVER', 'HOOK'):
            with self.subTest(dependency=dependency):
                rig, mesh = self.make()
                if dependency == 'DRIVER':
                    obj = bpy.data.objects.new('Artist reads toe input', None)
                    bpy.context.scene.collection.objects.link(obj)
                    rig.pose.bones['toe.L']['artist_value'] = .25
                    curve = obj.driver_add('location', 0)
                    curve.driver.type = 'SCRIPTED'
                    for variable in tuple(curve.driver.variables):
                        curve.driver.variables.remove(variable)
                    variable = curve.driver.variables.new()
                    variable.name, variable.type = 'toe_value', 'SINGLE_PROP'
                    variable.targets[0].id = rig
                    variable.targets[0].data_path = rig.pose.bones['toe.L'].path_from_id() + '["artist_value"]'
                    curve.driver.expression = variable.name
                    obj.update_tag(refresh={'OBJECT'})
                    regional.update(rig)
                    self.assertTrue(curve.driver.is_valid)
                    self.assertAlmostEqual(obj.location.x, .25, places=7)
                    reader_before = regional.value(regional.transaction._driver(curve))
                else:
                    data = bpy.data.meshes.new('Artist Hook surface')
                    data.from_pydata(((.1, .1, .1), (.2, .1, .1), (.1, .2, .1)), (), ((0, 1, 2),))
                    obj = bpy.data.objects.new(data.name, data)
                    bpy.context.scene.collection.objects.link(obj)
                    modifier = obj.modifiers.new('Artist Hook reads native toe', 'HOOK')
                    modifier.object, modifier.subtarget = rig, 'toe.L'
                    modifier.vertex_indices_set([0, 1, 2])
                    regional.update(rig)
                    reader_before = regional.value(regional.transaction._rna(modifier))
                    reader_mesh = mesh_raw(obj)
                self.assert_refused(rig, mesh)
                if dependency == 'DRIVER':
                    self.assertEqual(regional.value(regional.transaction._driver(curve)), reader_before)
                    self.assertAlmostEqual(obj.location.x, .25, places=7)
                else:
                    self.assertEqual(regional.value(regional.transaction._rna(modifier)), reader_before)
                    self.assertEqual(mesh_raw(obj), reader_mesh)

    def test_external_constraint_custom_space_refuses_before_mutation(self):
        rig, mesh = self.make()
        obj = bpy.data.objects.new('Artist controller uses foot space', None)
        target = bpy.data.objects.new('Independent copy location target', None)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.scene.collection.objects.link(target)
        target.location = (.1, .2, .3)
        con = obj.constraints.new('COPY_LOCATION')
        con.target, con.owner_space, con.target_space = target, 'CUSTOM', 'WORLD'
        con.space_object = rig
        con.space_subtarget = foot_controls.get_record(rig, 'L')['roll']
        regional.update(rig)
        before = regional.constraint(con)
        object_matrix = regional.matrix(obj.matrix_world)
        self.assert_refused(rig, mesh)
        self.assertEqual(regional.constraint(con), before)
        self.assertEqual(regional.matrix(obj.matrix_world), object_matrix)

    def test_public_operator_native_undo_redo_restores_semantic_scene(self):
        rig, mesh = self.make()
        name, mesh_name = rig.name, mesh.name
        bpy.ops.object.mode_set(mode='OBJECT')
        previous_undo = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True

        def durable(value):
            # The transaction encodes native references with an address for
            # in-place rollback. Native Undo may replace entire IDs, so use
            # the preserved ID type/name while keeping every other field.
            if isinstance(value, (list, tuple)):
                if len(value) == 5 and value[0] == '@ID':
                    return tuple(value[:3])
                return tuple(durable(item) for item in value)
            if isinstance(value, dict):
                return {key: durable(item) for key, item in value.items()}
            return value

        def durable_mesh(obj):
            result = mesh_raw(obj)
            result['properties'] = regional.properties(obj)
            result['data_properties'] = regional.properties(obj.data)
            result['key_properties'] = regional.properties(obj.data.shape_keys) if obj.data.shape_keys else ()
            result['binding'] = tuple((mod.name, mod.type,
                getattr(mod, 'object', None).name if getattr(mod, 'object', None) else None,
                mod.show_viewport, mod.show_render) for mod in obj.modifiers)
            return result

        def state(current_rig, current_mesh):
            result = regional.scene_state(current_rig, current_mesh)
            result['mesh'] = durable_mesh(current_mesh)
            result['bound_meshes'] = {obj.name: durable_mesh(obj) for obj in bpy.data.objects
                if obj.type == 'MESH' and any(mod.type == 'ARMATURE' and mod.object == current_rig
                                             for mod in obj.modifiers)}
            result['custom_foot_shapes'] = shape_state(current_rig, ('L', 'R'))
            result['native_rest'] = control_pose_assets.native_rest(current_rig)
            return durable(result)

        try:
            bpy.ops.ed.undo_push(message='Before public Foot Controls rebuild')
            before = state(rig, mesh)
            old_records = copy.deepcopy(foot_controls.records(rig))
            self.assertEqual(bpy.ops.character_designer.fix_rebuild_foot_controls(side='L'), {'FINISHED'})
            bpy.ops.ed.undo_push(message='After public Foot Controls rebuild')
            after = state(rig, mesh)
            new_records = copy.deepcopy(foot_controls.records(rig))
            self.assertNotEqual(new_records['L']['id'], old_records['L']['id'])
            self.assertEqual(new_records['R'], old_records['R'])
            self.assertEqual(after['native_rest'], before['native_rest'])
            self.assertEqual(after['custom_foot_shapes'], before['custom_foot_shapes'])
            self.assertEqual(after['bound_meshes'], before['bound_meshes'])

            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            rig, mesh = bpy.data.objects[name], bpy.data.objects[mesh_name]
            self.assertEqual(foot_controls.records(rig), old_records)
            regional.BodyRegionTests().assertRecoveredScene(state(rig, mesh), before)
            limb_ik._validate_inventory(rig)

            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            rig, mesh = bpy.data.objects[name], bpy.data.objects[mesh_name]
            self.assertEqual(foot_controls.records(rig), new_records)
            regional.BodyRegionTests().assertRecoveredScene(state(rig, mesh), after)
            self.assertEqual(durable(shape_state(rig, ('L', 'R'))), before['custom_foot_shapes'])
            limb_ik._validate_inventory(rig)
            record = foot_controls.get_record(rig, 'L')
            foot, toe = rig.pose.bones[record['chain'][2]], rig.pose.bones[record['toe']]
            for role, point in (('ANKLE_SOLVER', foot.head), ('BALL_PIVOT', toe.head), ('TOE_TIP_PIVOT', toe.tail)):
                helper = rig.pose.bones[record['bones'][role]]
                self.assertLessEqual((rig.matrix_world @ helper.head - rig.matrix_world @ point).length, 3e-4)
        finally:
            bpy.context.preferences.edit.use_global_undo = previous_undo

    def test_shortened_original_return_with_real_retained_action_owner_stays_authenticated(self):
        rig, mesh = make_fixture()
        action, motion = retain_action(rig)
        action_before, motion_before = body_original_owner._animation_fingerprint(action), unity_animation._rest_state(motion)
        original.enter(bpy.context, rig)
        edit_legs(rig)
        self.assertTrue(original.leave(bpy.context, rig))
        self.assertEqual(rig.data.users, 2)
        self.assertIn(action, bpy.data.user_map().get(rig.data, set()))
        self.assertTrue(body_region.verified_single_armature_user(rig))
        for side in ('L', 'R'):
            limb_ik_fk.switch_limb(bpy.context, rig, ('LEG', side), 'IK', keyframe=False)
        bind_probe_surface(rig)
        customize_shapes(rig)
        sides = ('L', 'R')
        before = self.protected_inputs(rig, mesh, sides)
        result = service.rebuild(bpy.context, rig, sides=sides)
        self.assert_rebuilt(rig, mesh, sides, before, result)
        self.assertEqual(rig.data.users, 2)
        self.assertTrue(body_region.verified_single_armature_user(rig))
        self.assertEqual(body_original_owner._animation_fingerprint(action), action_before)
        self.assertEqual(unity_animation._rest_state(motion), motion_before)
        self.assertIsNone(body_region._OWNER_OPERATION)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(FootControlsRebuildTests))
    if not result.wasSuccessful():
        raise RuntimeError('Foot Controls rebuild tests failed')
