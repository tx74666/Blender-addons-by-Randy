"""Regional release/edit/rebind contract in a disposable native Blender process.

Run: blender --background --factory-startup --python tests/test_body_region_blender.py
Never run against an artist file. These fixtures exercise generated Controls,
not an adopted animation Worklist session or an in-progress Original workspace.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_region as region, body_setup, body_calibration,
    body_setup_transaction as transaction, forearm_twist, foot_controls,
    limb_ik, limb_fk_visuals, root_control)
import test_body_setup_plan_blender as planning
import test_body_calibration_blender as calibration
import test_limb_ik_fk_blender as single
from test_body_rest_resync_blender import mesh_raw, world_surfaces
from test_body_setup_removal_blender import bind_probe_surface


def value(item):
    """Durable comparisons use reference names; identity is checked separately."""
    if isinstance(item, bpy.types.ID):
        return (type(item).__name__, item.name_full)
    if isinstance(item, (bpy.types.PoseBone, bpy.types.Bone)):
        return (type(item).__name__, item.name)
    if hasattr(item, 'items'):
        return tuple((key, value(entry)) for key, entry in sorted(item.items()))
    if isinstance(item, (str, bool, int, float, type(None))):
        return item
    try:
        return tuple(value(entry) for entry in item)
    except TypeError:
        return item


def properties(owner):
    try:
        keys = sorted(owner.keys())
    except TypeError:
        # Several native constraint RNA types do not allow ID properties.
        return ()
    return tuple((key, value(owner[key])) for key in keys)


def matrix(item):
    return tuple(tuple(row) for row in item)


def constraint(con):
    # The complete writable native RNA includes spaces, targets, mix modes,
    # limits and IK settings, rather than only counting constraint names.
    return (con.name, con.type, value(transaction._rna(con)), properties(con),
            tuple(value(transaction._rna(target)) for target in getattr(con, 'targets', ())))


def bone_state(rig, names):
    result = {}
    for name in sorted(names):
        bone, pb = rig.data.bones[name], rig.pose.bones[name]
        result[name] = {
            'rest': (tuple(bone.head_local), tuple(bone.tail_local), matrix(bone.matrix_local),
                     bone.parent.name if bone.parent else None, bool(bone.use_connect),
                     bool(bone.use_deform), bone.inherit_scale),
            'bone_properties': properties(bone), 'pose_properties': properties(pb),
            'basis': matrix(pb.matrix_basis), 'rotation_mode': pb.rotation_mode,
            'channels': (tuple(pb.location), tuple(pb.rotation_euler),
                         tuple(pb.rotation_quaternion), tuple(pb.rotation_axis_angle), tuple(pb.scale)),
            'shape': (value(pb.custom_shape), value(pb.custom_shape_transform),
                      value(limb_ik._pose_shape_json_state(pb))),
            'constraints': tuple(constraint(con) for con in pb.constraints),
        }
    return result


def actions(rig):
    result = []
    for owner in (rig, rig.data):
        for action in limb_ik._actions_for_id(owner):
            result.append((owner.name, action.name,
                tuple((curve.data_path, curve.array_index,
                       tuple((tuple(point.co), tuple(point.handle_left), tuple(point.handle_right),
                              point.interpolation) for point in curve.keyframe_points))
                      for curve in limb_ik._fcurves_for_action(action))))
    return tuple(result)


def native_ids(rig, mesh):
    return rig.as_pointer(), rig.data.as_pointer(), mesh.as_pointer(), mesh.data.as_pointer(), (
        mesh.data.shape_keys.as_pointer() if mesh.data.shape_keys else None)


def update(rig):
    rig.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()


def scene_state(rig, mesh):
    update(rig)
    return {
        'rig_properties': properties(rig), 'data_properties': properties(rig.data),
        'bones': bone_state(rig, rig.data.bones.keys()), 'mesh': mesh_raw(mesh),
        'actions': actions(rig),
        # FCurve collection order has no driver dependency semantics. A
        # rollback may reinsert a deleted owned curve later in the collection;
        # its path, index and complete writable contents must remain exact.
        'drivers': tuple((curve.data_path, curve.array_index, value(transaction._driver(curve)))
                         for curve in sorted(rig.animation_data.drivers,
                             key=lambda curve: (curve.data_path, curve.array_index))) if rig.animation_data else (),
        'objects': tuple(sorted(bpy.data.objects.keys())),
        'meshes': tuple(sorted(bpy.data.meshes.keys())),
        'collections': tuple(sorted(bpy.data.collections.keys())),
    }


def changed_fields(actual, expected, path=(), limit=12):
    """Compact exact field diagnostics; never dump the complete native scene."""
    changes = []
    def walk(first, second, current):
        if len(changes) >= limit or first == second:
            return
        if isinstance(first, dict) and isinstance(second, dict) and set(first) == set(second):
            for key in sorted(first):
                walk(first[key], second[key], current + (key,))
        elif isinstance(first, (list, tuple)) and isinstance(second, (list, tuple)) and len(first) == len(second):
            for index, (a, b) in enumerate(zip(first, second)):
                walk(a, b, current + (index,))
        else:
            changes.append((current, first, second))
    walk(actual, expected, path)
    return changes


def leg_names(rig, sides=('L', 'R')):
    """Native lower body plus only the generated limb/foot helpers for it."""
    selected = {prefix + '.' + side for side in sides
                for prefix in ('thigh', 'shin', 'foot', 'toe')}
    for bone in rig.data.bones:
        if (bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                and bone.get(limb_ik.KIND_KEY) == 'LEG'
                and bone.get(limb_ik.SIDE_KEY) in sides):
            selected.add(bone.name)
        if (bone.get(limb_ik.OWNER_KEY) == foot_controls.OWNER_VALUE
                and bone.get(foot_controls.SIDE_KEY) in sides):
            selected.add(bone.name)
    return selected & set(rig.data.bones.keys())


class BodyRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def make(self, *, corrective=True):
        rig, mesh = planning.fixture()
        # planning.fixture intentionally poses the Hips; calibration requires
        # an unconstrained neutral source before its first confirmed build.
        rig.pose.bones['Hips'].rotation_euler = (0, 0, 0)
        update(rig)
        # A hand already authored by the artist must survive a leg operation.
        bpy.ops.object.mode_set(mode='EDIT')
        hand = rig.data.edit_bones['hand.L']
        planning.base.add_bone(rig.data.edit_bones, 'index01.L', hand.tail,
                               hand.tail + Vector((.07, 0, 0)), hand)
        bpy.ops.object.mode_set(mode='POSE')
        widget_data = bpy.data.meshes.new('Artist finished finger widget')
        widget_data.from_pydata([(0, 0, 0), (.02, .01, 0)], [(0, 1)], [])
        widget = bpy.data.objects.new(widget_data.name, widget_data)
        bpy.context.scene.collection.objects.link(widget)
        finger = rig.pose.bones['index01.L']
        finger.custom_shape = widget
        finger.custom_shape_translation = (.01, -.02, .03)
        finger['artist_hand_finished'] = 'keep this setup'
        mesh.shape_key_add(name='Basis')
        key = mesh.shape_key_add(name='Artist Smile')
        key.data[0].co.x += .008
        key.value = .3
        calibration.palms(rig)
        calibration.prepare(rig)
        body_setup.generate(bpy.context, rig)
        self.assertEqual(set(limb_ik._validate_inventory(rig)['rigs']),
                         {('ARM', 'L'), ('ARM', 'R'), ('LEG', 'L'), ('LEG', 'R')})
        self.assertIsNotNone(root_control.validate(rig))
        self.assertEqual(set(foot_controls.records(rig)), {'L', 'R'})
        self.assertIsNotNone(limb_fk_visuals.validate(rig))
        # The managed output is disabled but retains its real Rest/topology
        # signature, author key and metadata. Leg release must not rebase it.
        chain = limb_ik._validate_inventory(rig)['rigs'][('ARM', 'L')]['chain']
        if corrective:
            mesh[forearm_twist.RECORD_KEY] = json.dumps({'L': {
                'armature': rig.name, 'chain': list(chain),
                'rest': forearm_twist._rest_signature(rig, chain),
                'topology': forearm_twist._topology(mesh.data),
                'key': key.name, 'enabled': False,
            }}, sort_keys=True)
        update(rig)
        return rig, mesh

    def assertMatrices(self, rig, expected):
        update(rig)
        for name, before in expected.items():
            actual = rig.pose.bones[name].matrix
            error = max(abs(a - b) for row_a, row_b in zip(before, actual)
                        for a, b in zip(row_a, row_b))
            self.assertLessEqual(error, 2e-5, name)

    def assertUntouched(self, rig, mesh, names, before, geometry, identities):
        update(rig)
        self.assertEqual(bone_state(rig, names), before)
        self.assertEqual(mesh_raw(mesh), geometry)
        self.assertEqual(native_ids(rig, mesh), identities)

    def assertRecoveredScene(self, actual, expected, *, recreated=()):
        # matrix_basis is evaluated from the authored channels. A restored
        # native graph may recompute its last float bits; the channels, native
        # Rest geometry/matrices, constraints, metadata and mesh remain exact.
        # In particular, this does not tolerate changed location/rotation/scale.
        current = dict(actual, bones={name: dict(entry) for name, entry in actual['bones'].items()})
        previous = dict(expected, bones={name: dict(entry) for name, entry in expected['bones'].items()})
        self.assertEqual(set(current['bones']), set(previous['bones']))
        recreated = set(recreated)
        self.assertTrue(recreated <= set(current['bones']))
        recreated_errors = {}
        recreated_translation_errors = {}
        for name in current['bones']:
            first, second = current['bones'][name].pop('basis'), previous['bones'][name].pop('basis')
            error = max(abs(a - b) for row_a, row_b in zip(first, second) for a, b in zip(row_a, row_b))
            self.assertLessEqual(error, 1e-6, f'{name}.matrix_basis differs by {error}')
            if name in recreated:
                # Only helpers actually deleted by this regional operation
                # can need an EditBone roll reconstruction. Native author
                # Rest and every surviving generated Rest remain exact.
                # Heads, tails, parent/connect/deform/inherit flags stay exact
                # even for these recreated helpers; only their derived matrix
                # rotation/translation is compared numerically. Short helpers amplify
                # float32 head/tail/z -> EditBone roll round trips. The matrix
                # translation also passes through the recreated parent Rest.
                # The homogeneous row remains exact.
                first, second = current['bones'][name]['rest'], previous['bones'][name]['rest']
                recreated_errors[name] = max(abs(first[2][i][j] - second[2][i][j])
                                             for i in range(3) for j in range(3))
                recreated_translation_errors[name] = max(abs(first[2][i][3] - second[2][i][3])
                                                         for i in range(3))
                current_derived = tuple(tuple(None if i < 3 else entry
                                               for j, entry in enumerate(row))
                                         for i, row in enumerate(first[2]))
                previous_derived = tuple(tuple(None if i < 3 else entry
                                                for j, entry in enumerate(row))
                                          for i, row in enumerate(second[2]))
                current['bones'][name]['rest'] = first[:2] + (current_derived,) + first[3:]
                previous['bones'][name]['rest'] = second[:2] + (previous_derived,) + second[3:]
        if recreated_errors:
            name = max(recreated_errors, key=recreated_errors.get)
            maximum = recreated_errors[name]
            translation_name = max(recreated_translation_errors, key=recreated_translation_errors.get)
            translation_maximum = recreated_translation_errors[translation_name]
            print('REGION_RECREATED_REST_ROTATION', json.dumps({
                'maximum_error': maximum, 'bone': name, 'bones_checked': len(recreated_errors),
                'limit': 1e-5, 'scope': 'only removed/recreated selected generated helpers',
                'max_translation_error': translation_maximum, 'translation_bone': translation_name,
                'translation_limit': 1e-6,
            }), flush=True)
            self.assertLessEqual(maximum, 1e-5, f'{name}.recreated Rest rotation differs by {maximum}')
            self.assertLessEqual(translation_maximum, 1e-6,
                                 f'{translation_name}.recreated Rest translation differs by {translation_maximum}')
        changes = changed_fields(current, previous)
        self.assertFalse(changes, f'Recovered native scene differs (path, actual, expected): {changes!r}')

    def assertDeformationRecovered(self, rig, skin, surfaces):
        update(rig)
        after = body_setup._native_skin(rig)
        self.assertEqual(set(after), set(skin))
        skin_errors = {}
        for name, before in skin.items():
            skin_errors[name] = max(abs(a - b) for row_a, row_b in zip(before, after[name])
                                    for a, b in zip(row_a, row_b))
        after = world_surfaces(rig)
        self.assertEqual(set(after), set(surfaces))
        surface_errors = {}
        for name, before in surfaces.items():
            self.assertEqual(len(after[name]), len(before), name)
            surface_errors[name] = max(((a - b).length for a, b in zip(after[name], before)), default=0.)
        skin_max = max(skin_errors.values(), default=0.)
        surface_max = max(surface_errors.values(), default=0.)
        print('REGION_ROLLBACK_DEFORMATION', json.dumps({
            'max_native_skin_matrix_error': skin_max, 'native_bones_checked': len(skin_errors),
            'skin_matrix_limit': 2e-5, 'max_world_surface_error': surface_max,
            'bound_meshes_checked': len(surface_errors), 'surface_limit': 1e-4,
        }), flush=True)
        self.assertLessEqual(skin_max, 2e-5, f'Native skin matrix drift: {skin_errors!r}')
        self.assertLessEqual(surface_max, 1e-4, f'Evaluated world surface drift: {surface_errors!r}')

    def shorten(self, rig, sides=('L', 'R')):
        """Upper body fixed; knee/ankle move up and the foot/toe translate."""
        bpy.ops.object.mode_set(mode='EDIT')
        for side in sides:
            upper, lower, foot, toe = (rig.data.edit_bones[name + '.' + side]
                                      for name in ('thigh', 'shin', 'foot', 'toe'))
            hip, knee, ankle = upper.head.copy(), upper.tail.copy(), lower.tail.copy()
            foot_vector, toe_head_offset, toe_vector = (
                foot.tail - foot.head, toe.head - foot.head, toe.tail - toe.head)
            new_knee = hip + .85 * (knee - hip)
            new_ankle = new_knee + .85 * (ankle - knee)
            upper.tail = new_knee
            lower.head, lower.tail = new_knee, new_ankle
            foot.head, foot.tail = new_ankle, new_ankle + foot_vector
            toe.head, toe.tail = new_ankle + toe_head_offset, new_ankle + toe_head_offset + toe_vector
        bpy.ops.object.mode_set(mode='POSE')
        update(rig)

    def test_release_and_rebind_only_legs_preserves_finished_hand_and_arm_animation(self):
        rig, mesh = self.make()
        inventory = limb_ik._validate_inventory(rig)
        arm = rig.pose.bones[inventory['rigs'][('ARM', 'R')]['target'].name]
        arm.keyframe_insert('location', frame=1)
        arm.location.x += .01
        arm.keyframe_insert('location', frame=12)
        bpy.context.scene.frame_set(1)
        unrelated = set(rig.data.bones.keys()) - leg_names(rig)
        before, geometry, identities = bone_state(rig, unrelated), mesh_raw(mesh), native_ids(rig, mesh)
        pose = {name: rig.pose.bones[name].matrix.copy() for name in unrelated}
        clips, calibration_data = actions(rig), rig.get(body_calibration.KEY)
        rest = body_calibration.native_rest(rig)
        region.release(bpy.context, rig, 'LEGS')
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'RELEASED')
        self.assertEqual(region.state(rig, 'ARMS')['status'], 'BOUND')
        self.assertEqual(set(limb_ik._validate_inventory(rig)['rigs']), {('ARM', 'L'), ('ARM', 'R')})
        self.assertFalse(foot_controls.records(rig))
        self.assertEqual(body_calibration.native_rest(rig), rest)
        self.assertUntouched(rig, mesh, unrelated, before, geometry, identities)
        self.assertMatrices(rig, pose)
        self.shorten(rig)
        edited = body_calibration.native_rest(rig)
        self.assertNotEqual(edited['foot.L'], rest['foot.L'])
        region.bind(bpy.context, rig, 'LEGS')
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')
        self.assertEqual(body_calibration.native_rest(rig), edited)
        self.assertEqual(set(foot_controls.records(rig)), {'L', 'R'})
        self.assertIsNotNone(limb_fk_visuals.validate(rig))
        self.assertIsNotNone(root_control.validate(rig))
        self.assertUntouched(rig, mesh, unrelated, before, geometry, identities)
        self.assertMatrices(rig, pose)
        self.assertEqual(actions(rig), clips)
        self.assertEqual(rig.get(body_calibration.KEY), calibration_data)

    def test_left_leg_release_preserves_right_leg_graph(self):
        rig, mesh = self.make()
        untouched = set(rig.data.bones.keys()) - leg_names(rig, ('L',))
        before, geometry, identities = bone_state(rig, untouched), mesh_raw(mesh), native_ids(rig, mesh)
        region.release(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(region.state(rig, 'LEFT_LEG')['status'], 'RELEASED')
        self.assertEqual(region.state(rig, 'RIGHT_LEG')['status'], 'BOUND')
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'MIXED')
        self.assertEqual(set(foot_controls.records(rig)), {'R'})
        self.assertUntouched(rig, mesh, untouched, before, geometry, identities)
        self.shorten(rig, ('L',))
        region.bind(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')
        self.assertUntouched(rig, mesh, untouched, before, geometry, identities)

    def test_each_arm_region_roundtrip_keeps_leg_and_finished_finger_sources(self):
        for selection, removed in (('LEFT_ARM', {('ARM', 'L')}),
                                   ('RIGHT_ARM', {('ARM', 'R')}),
                                   ('ARMS', {('ARM', 'L'), ('ARM', 'R')})):
            with self.subTest(region=selection):
                rig, mesh = self.make(corrective=False)
                untouched = leg_names(rig) | {'index01.L'}
                before, geometry, identities = bone_state(rig, untouched), mesh_raw(mesh), native_ids(rig, mesh)
                rest = body_calibration.native_rest(rig)
                keys = set(limb_ik._validate_inventory(rig)['rigs'])
                region.release(bpy.context, rig, selection)
                self.assertEqual(region.state(rig, selection)['status'], 'RELEASED')
                self.assertEqual(set(limb_ik._validate_inventory(rig)['rigs']), keys - removed)
                self.assertUntouched(rig, mesh, untouched, before, geometry, identities)
                self.assertEqual(body_calibration.native_rest(rig), rest)
                region.bind(bpy.context, rig, selection)
                self.assertEqual(region.state(rig, selection)['status'], 'BOUND')
                self.assertEqual(set(limb_ik._validate_inventory(rig)['rigs']), keys)
                self.assertUntouched(rig, mesh, untouched, before, geometry, identities)
                self.assertEqual(body_calibration.native_rest(rig), rest)

    def test_bound_or_already_released_selection_does_not_rebuild_other_parts(self):
        rig, mesh = self.make()
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        with self.assertRaises(ValueError):
            region.bind(bpy.context, rig, 'LEGS')
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)
        region.release(bpy.context, rig, 'LEFT_LEG')
        before = scene_state(rig, mesh)
        with self.assertRaises(ValueError):
            region.release(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)

    def test_released_native_sources_are_visible_and_editable_without_original_workspace(self):
        rig, _mesh = self.make()
        region.release(bpy.context, rig, 'LEGS')
        self.assertNotIn('character_designer_body_original_mode_v1', rig)
        for side in ('L', 'R'):
            for role in ('thigh', 'shin', 'foot', 'toe'):
                bone = rig.data.bones[role + '.' + side]
                self.assertFalse(bone.hide, bone.name)
                self.assertFalse(bone.hide_select, bone.name)
                self.assertTrue(any(collection.is_visible_effectively for collection in bone.collections), bone.name)
        # Editing is an ordinary native operation; rebind never requires a
        # global Original/Controls round-trip that would resync other limbs.
        bpy.ops.object.mode_set(mode='EDIT')
        self.assertIsNotNone(rig.data.edit_bones.get('foot.L'))
        self.assertIsNotNone(rig.data.edit_bones.get('toe.R'))
        bpy.ops.object.mode_set(mode='POSE')
        region.bind(bpy.context, rig, 'LEGS')
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')

    def test_released_edit_survives_save_reopen_before_rebind(self):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEGS')
        self.shorten(rig)
        rest, geometry = body_calibration.native_rest(rig), mesh_raw(mesh)
        name, mesh_name = rig.name, mesh.name
        with tempfile.TemporaryDirectory(prefix='cd-body-region-') as folder:
            path = str(Path(folder) / 'released_edit.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=path), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=path), {'FINISHED'})
            rig, mesh = bpy.data.objects[name], bpy.data.objects[mesh_name]
            # WM mapping is transient and may have been cleared by load.
            bpy.context.view_layer.objects.active = rig
            rig.select_set(True)
            if bpy.context.mode != 'POSE':
                bpy.ops.object.mode_set(mode='POSE')
            self.assertEqual(region.state(rig, 'LEGS')['status'], 'RELEASED')
            self.assertEqual(body_calibration.native_rest(rig), rest)
            # Raw coordinates, weights and authored key data are durable;
            # armature modifier identity is intentionally process-local.
            persisted = mesh_raw(mesh)
            for key in ('vertices', 'edges', 'faces', 'groups', 'weights', 'keys', 'key_properties'):
                self.assertEqual(persisted[key], geometry[key], key)
            region.bind(bpy.context, rig, 'LEGS')
            self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')
            self.assertEqual(body_calibration.native_rest(rig), rest)
            self.assertEqual(set(foot_controls.records(rig)), {'L', 'R'})

    def test_selected_animation_refuses_before_mutation(self):
        for native in (False, True):
            with self.subTest(native=native):
                rig, mesh = self.make()
                data = limb_ik._validate_inventory(rig)['rigs'][('LEG', 'L')]
                name = data['chain'][0] if native else data['target'].name
                rig.pose.bones[name].keyframe_insert('location', frame=1)
                before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
                with self.assertRaises(ValueError):
                    region.release(bpy.context, rig, 'LEFT_LEG')
                self.assertEqual(scene_state(rig, mesh), before)
                self.assertEqual(native_ids(rig, mesh), identities)

    def test_external_controller_dependency_refuses_before_mutation(self):
        rig, mesh = self.make()
        target = limb_ik._validate_inventory(rig)['rigs'][('LEG', 'L')]['target'].name
        obj = bpy.data.objects.new('Artist attachment to leg controller', None)
        bpy.context.scene.collection.objects.link(obj)
        con = obj.constraints.new('COPY_LOCATION')
        con.target, con.subtarget = rig, target
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        with self.assertRaises(ValueError):
            region.release(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)

    def test_selected_bone_data_property_animation_or_external_driver_refuses(self):
        for external_reader in (False, True):
            with self.subTest(external_reader=external_reader):
                rig, mesh = self.make()
                target = limb_ik._validate_inventory(rig)['rigs'][('LEG', 'L')]['target'].name
                bone = rig.data.bones[target]
                bone['artist_parameter'] = .25
                path = bone.path_from_id() + '["artist_parameter"]'
                if external_reader:
                    obj = bpy.data.objects.new('Artist reads controller bone data', None)
                    bpy.context.scene.collection.objects.link(obj)
                    curve = obj.driver_add('location', 0)
                    curve.driver.type = 'SCRIPTED'
                    for existing in tuple(curve.driver.variables):
                        curve.driver.variables.remove(existing)
                    variable = curve.driver.variables.new()
                    variable.name, variable.type = 'artist_value', 'SINGLE_PROP'
                    variable.targets[0].id_type = 'ARMATURE'
                    variable.targets[0].id = rig.data
                    variable.targets[0].data_path = path
                    curve.driver.expression = variable.name
                    # Evaluate a complete valid reader before the baseline;
                    # first evaluation is allowed to update runtime is_valid.
                    # Removing default variables also avoids a dormant missing
                    # target making this a nonfunctional dependency fixture.
                    rig.data.update_tag()
                    obj.update_tag(refresh={'OBJECT'})
                    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
                    update(rig)
                    self.assertTrue(curve.driver.is_valid)
                    self.assertAlmostEqual(obj.location.x, .25, places=7)
                    reader_before = value(transaction._driver(curve))
                else:
                    self.assertTrue(bone.keyframe_insert('["artist_parameter"]', frame=1))
                    self.assertTrue(any(curve.data_path == path
                        for action in limb_ik._actions_for_id(rig.data)
                        for curve in limb_ik._fcurves_for_action(action)))
                before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
                with self.assertRaises(ValueError):
                    region.release(bpy.context, rig, 'LEFT_LEG')
                self.assertEqual(scene_state(rig, mesh), before)
                self.assertEqual(native_ids(rig, mesh), identities)
                self.assertEqual(region.state(rig, 'LEFT_LEG')['status'], 'BOUND')
                if external_reader:
                    self.assertEqual(value(transaction._driver(curve)), reader_before)

    def test_selected_sole_widget_external_constraint_refuses_before_mutation(self):
        rig, mesh = self.make()
        aid = rig.data[limb_ik.ARMATURE_ID_KEY]
        matches = [obj for obj in bpy.data.objects
                   if limb_ik._owned(obj, aid, role='WIDGET')
                   and obj.get(limb_ik.KIND_KEY) == 'FOOT_L']
        self.assertEqual(len(matches), 1, 'Fixture must exercise the left sole widget being deleted')
        widget = matches[0]
        obj = bpy.data.objects.new('Artist follows sole display', None)
        bpy.context.scene.collection.objects.link(obj)
        con = obj.constraints.new('COPY_LOCATION')
        con.target = widget
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        widget_pointer = widget.as_pointer()
        con_before = constraint(con)
        with self.assertRaises(ValueError):
            region.release(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)
        self.assertEqual(widget.as_pointer(), widget_pointer)
        self.assertEqual(con.target, widget)
        self.assertEqual(constraint(con), con_before)

    def test_regular_builder_refuses_released_region_without_saved_rebind_admission(self):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEFT_LEG')
        receipt = rig.data[region.RECORD_KEY]
        entry = json.loads(receipt)['limbs']['LEG:L']
        chain = limb_ik.LimbChain('LEG', 'L', *entry['chain'])
        _shoulder, _elbow, _wrist, direction = body_calibration.direction(rig, chain)
        plan = limb_ik._build_plan(rig, chain, entry['pole_distance'], direction)
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        with self.assertRaises(ValueError):
            limb_ik._build_plans(bpy.context, rig, [plan],
                schema=limb_ik.DIRECT_PREROLL_SCHEMA,
                target_rotation_version=entry['feature'])
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)
        self.assertEqual(rig.data[region.RECORD_KEY], receipt)
        # Also exercise the public Advanced builder, rather than only its
        # internal helper. Mapping is transient WindowManager state.
        result, settings = planning.base.analyze(rig)
        self.assertEqual(result, {'FINISHED'})
        settings.build_method, settings.selected_limb = 'DIRECT_PREROLL', 'LEFT_LEG'
        before = scene_state(rig, mesh)
        self.assertEqual(bpy.ops.character_designer.limb_ik_build_selected(), {'CANCELLED'})
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)
        self.assertEqual(rig.data[region.RECORD_KEY], receipt)
        self.assertEqual(region.state(rig, 'LEFT_LEG')['status'], 'RELEASED')
        region.bind(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(region.state(rig, 'LEFT_LEG')['status'], 'BOUND')

    def test_whole_body_generate_remove_and_legacy_edit_keep_pending_release(self):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEFT_LEG')
        receipt = rig.data[region.RECORD_KEY]
        result, settings = planning.base.analyze(rig)
        self.assertEqual(result, {'FINISHED'})
        settings.build_method, settings.selected_limb = 'DIRECT_PREROLL', 'LEFT_LEG'
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        for operation in (body_setup.generate, body_setup.remove):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, r'(released|Edit One Part)'):
                    operation(bpy.context, rig)
                self.assertEqual(scene_state(rig, mesh), before)
                self.assertEqual(native_ids(rig, mesh), identities)
                self.assertEqual(rig.data[region.RECORD_KEY], receipt)
        for name in ('limb_ik_rebuild', 'limb_ik_remove'):
            with self.subTest(operation=name):
                try:
                    result = getattr(bpy.ops.character_designer, name)()
                except RuntimeError as exc:
                    # Blender's bpy.ops wrapper raises when the operator emits
                    # an ERROR report even if execute returned CANCELLED.
                    self.assertIn('Rebuild released parts in Edit One Part before rebuilding or removing the complete rig.', str(exc))
                else:
                    self.assertEqual(result, {'CANCELLED'})
                self.assertEqual(scene_state(rig, mesh), before)
                self.assertEqual(native_ids(rig, mesh), identities)
                self.assertEqual(rig.data[region.RECORD_KEY], receipt)
        self.assertEqual(region.state(rig, 'LEFT_LEG')['status'], 'RELEASED')

    def test_enlarged_leg_receipt_cannot_authorize_editing_finished_hand(self):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEFT_LEG')
        receipt = rig.data[region.RECORD_KEY]
        forged = json.loads(receipt)
        entry = forged['limbs']['LEG:L']
        entry['allowed'].append('hand.L')
        entry['protected_rest'].pop('hand.L')
        entry['relations'].pop('hand.L', None)
        rig.data[region.RECORD_KEY] = json.dumps(forged, sort_keys=True, separators=(',', ':'))
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['hand.L'].tail += Vector((.01, 0, 0))
        bpy.ops.object.mode_set(mode='POSE')
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        with self.assertRaises(ValueError):
            region.bind(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)
        self.assertFalse(any(bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
            and bone.get(limb_ik.KIND_KEY) == 'LEG' and bone.get(limb_ik.SIDE_KEY) == 'L'
            for bone in rig.data.bones))
        # Restoring an authentic receipt does not approve an unrelated hand
        # edit made while that receipt was tampered with.
        rig.data[region.RECORD_KEY] = receipt
        before = scene_state(rig, mesh)
        with self.assertRaises(ValueError):
            region.bind(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)
        self.assertEqual(rig.data[region.RECORD_KEY], receipt)
        self.assertFalse(any(bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
            and bone.get(limb_ik.KIND_KEY) == 'LEG' and bone.get(limb_ik.SIDE_KEY) == 'L'
            for bone in rig.data.bones))

    def test_unrelated_rest_edit_while_released_refuses_rebind(self):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEGS')
        bpy.ops.object.mode_set(mode='EDIT')
        rig.data.edit_bones['index01.L'].tail += Vector((.01, 0, 0))
        bpy.ops.object.mode_set(mode='POSE')
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        with self.assertRaises(ValueError):
            region.bind(bpy.context, rig, 'LEGS')
        self.assertEqual(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'RELEASED')

    def test_release_failure_recovers_graph_and_original_datablock_identities(self):
        rig, mesh = self.make()
        # Exercise deformation at off-axis samples weighted to every native
        # bone, including the legs and feet absent from the small body fixture.
        bind_probe_surface(rig)
        recreated = {name for name in leg_names(rig)
                     if rig.data.bones[name].get(limb_ik.OWNER_KEY)
                     in {limb_ik.OWNER_VALUE, foot_controls.OWNER_VALUE}}
        self.assertTrue(recreated)
        skin = {name: matrix(item) for name, item in body_setup._native_skin(rig).items()}
        surfaces = world_surfaces(rig)
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        real = region._release_graph
        def fail_after_write(*args, **kwargs):
            real(*args, **kwargs)
            raise ValueError('injected regional release failure')
        with patch.object(region, '_release_graph', side_effect=fail_after_write):
            with self.assertRaisesRegex(ValueError, 'injected regional release failure'):
                region.release(bpy.context, rig, 'LEGS')
        self.assertDeformationRecovered(rig, skin, surfaces)
        self.assertRecoveredScene(scene_state(rig, mesh), before, recreated=recreated)
        self.assertEqual(native_ids(rig, mesh), identities)
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')
        limb_ik._validate_inventory(rig)
        root_control.validate(rig)
        limb_fk_visuals.validate(rig)

    def test_bind_failure_keeps_released_edited_state_and_native_ids(self):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEGS')
        self.shorten(rig)
        before, identities = scene_state(rig, mesh), native_ids(rig, mesh)
        real = region._save
        calls = []
        def fail_once(*args, **kwargs):
            real(*args, **kwargs)
            calls.append(True)
            if len(calls) == 1:
                raise ValueError('injected regional bind failure')
        with patch.object(region, '_save', side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, 'injected regional bind failure'):
                region.bind(bpy.context, rig, 'LEGS')
        self.assertTrue(calls)
        self.assertRecoveredScene(scene_state(rig, mesh), before)
        self.assertEqual(native_ids(rig, mesh), identities)
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'RELEASED')
        region.bind(bpy.context, rig, 'LEGS')
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')

    def test_only_limb_without_root_can_be_released_and_rebound(self):
        rig, key, _ = single.build('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
        native = body_calibration.native_rest(rig)
        foot_controls.build(bpy.context, rig, key, toe_name='toe.L')
        limb_fk_visuals.build(bpy.context, rig)
        region.release(bpy.context, rig, 'LEFT_LEG')
        self.assertFalse(limb_ik._validate_inventory(rig)['rigs'])
        self.assertFalse(foot_controls.records(rig))
        self.assertEqual(body_calibration.native_rest(rig), native)
        self.shorten(rig, ('L',))
        edited = body_calibration.native_rest(rig)
        region.bind(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(set(limb_ik._validate_inventory(rig)['rigs']), {('LEG', 'L')})
        self.assertEqual(body_calibration.native_rest(rig), edited)
        self.assertEqual(set(foot_controls.records(rig)), {'L'})
        self.assertIsNotNone(limb_fk_visuals.validate(rig))

    def test_real_operator_undo_redo_release_and_rebind(self):
        rig, mesh = self.make()
        name, mesh_name = rig.name, mesh.name
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.context.preferences.edit.use_global_undo = True
        rest = body_calibration.native_rest(rig)
        arm_names = set(rig.data.bones.keys()) - leg_names(rig)
        arm_before = bone_state(rig, arm_names)
        forearm_before = mesh.get(forearm_twist.RECORD_KEY)
        bpy.ops.ed.undo_push(message='Before regional release')
        self.assertEqual(bpy.ops.character_designer.body_region(action='RELEASE', region='LEGS'), {'FINISHED'})
        bpy.ops.ed.undo_push(message='After regional release')
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'RELEASED')
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        rig, mesh = bpy.data.objects[name], bpy.data.objects[mesh_name]
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')
        self.assertEqual(body_calibration.native_rest(rig), rest)
        self.assertEqual(bone_state(rig, arm_names), arm_before)
        self.assertEqual(mesh.get(forearm_twist.RECORD_KEY), forearm_before)
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        rig, mesh = bpy.data.objects[name], bpy.data.objects[mesh_name]
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'RELEASED')
        self.assertEqual(bone_state(rig, arm_names), arm_before)
        self.assertEqual(bpy.ops.character_designer.body_region(action='BIND', region='LEGS'), {'FINISHED'})
        bpy.ops.ed.undo_push(message='After regional rebind')
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')
        self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
        rig = bpy.data.objects[name]
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'RELEASED')
        self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
        rig, mesh = bpy.data.objects[name], bpy.data.objects[mesh_name]
        self.assertEqual(region.state(rig, 'LEGS')['status'], 'BOUND')
        self.assertEqual(body_calibration.native_rest(rig), rest)
        self.assertEqual(bone_state(rig, arm_names), arm_before)
        self.assertEqual(mesh.get(forearm_twist.RECORD_KEY), forearm_before)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BodyRegionTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('Regional Body release/rebind tests failed')
