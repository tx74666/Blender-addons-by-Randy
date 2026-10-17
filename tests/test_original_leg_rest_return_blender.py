"""In-place Original -> Controls after native leg/foot edits; disposable native tests.

No Release/Rebuild, no artist input and no .blend save. Foot pivot Rest stays
fixed: this proves pose recovery and a small control exercise, not a new fit.
"""
from array import array
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_original_mode as original, body_rest_resync as resync,
    body_calibration, control_pose_assets as poses, foot_controls, forearm_twist,
    limb_ik, limb_ik_fk)
import test_body_region_blender as regional

POSE_LIMIT = 4e-4
OTHER_POSE_LIMIT = 2e-5


def update(rig):
    rig.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()


def make_fixture():
    """Full body in FK, ready for callers to attach owner refs before Original."""
    rig, mesh = regional.BodyRegionTests().make(corrective=False)
    for side in ('L', 'R'):
        limb_ik_fk.switch_limb(bpy.context, rig, ('LEG', side), 'FK', keyframe=False)
    update(rig)
    return rig, mesh


def edit_legs(rig, kind='ALL'):
    if not original.active(rig):
        raise ValueError('Enter Original before editing this fixture.')
    if kind == 'ALL':
        regional.BodyRegionTests().shorten(rig)
    else:
        bpy.ops.object.mode_set(mode='EDIT')
        role = {'FOOT_ONLY': 'foot.L', 'TOE_ONLY': 'toe.L'}[kind]
        rig.data.edit_bones[role].roll += .045
        bpy.ops.object.mode_set(mode='POSE')
        update(rig)


def rest_all(rig):
    return {bone.name: (tuple(bone.head_local), tuple(bone.tail_local), regional.matrix(bone.matrix_local),
        bone.parent.name if bone.parent else None, bool(bone.use_connect), bool(bone.use_deform),
        bone.inherit_scale, regional.properties(bone)) for bone in rig.data.bones}


def identities(rig):
    return {'rig': (rig.as_pointer(), rig.data.as_pointer()),
        'bones': {bone.name: bone.as_pointer() for bone in rig.data.bones},
        'objects': {obj.name: (obj.as_pointer(), obj.data.as_pointer() if obj.data else None)
                    for obj in bpy.context.scene.objects},
        'keys': {obj.name: obj.data.shape_keys.as_pointer() if obj.data.shape_keys else None
                 for obj in bpy.context.scene.objects if obj.type == 'MESH'}}


def native_matrices(rig):
    update(rig)
    return {name: rig.pose.bones[name].matrix.copy() for name in poses.native_rest(rig)}


def matrix_error(before, after, names=None):
    selected = set(before) if names is None else set(names)
    if not selected <= set(after):
        raise AssertionError('A protected pose bone disappeared.')
    return max((max(abs(a - b) for row_a, row_b in zip(before[name], after[name])
                    for a, b in zip(row_a, row_b)) for name in selected), default=0.)


def controller_settings(rig, names, *, saved_original=None):
    """Raw author settings are distinct from solver-produced pose numbers."""
    result = regional.bone_state(rig, names)
    mutes = {(entry['bone'], entry['name']): entry['mute']
             for entry in (saved_original or {}).get('constraints', ())}
    for name, entry in result.items():
        for field in ('rest', 'basis', 'channels'):
            del entry[field]
        pb = rig.pose.bones[name]
        entry['locks'] = {'lock_location': tuple(pb.lock_location),
            'lock_rotation': tuple(pb.lock_rotation), 'lock_scale': tuple(pb.lock_scale),
            'lock_rotation_w': pb.lock_rotation_w, 'lock_rotations_4d': pb.lock_rotations_4d}
        if saved_original:
            if name in saved_original['channels']:
                entry['rotation_mode'] = saved_original['channels'][name]['mode']
            for field, value in saved_original.get('locks', {}).get(name, {}).items():
                entry['locks'][field] = tuple(value) if isinstance(value, list) else value
            restored = []
            for con in entry['constraints']:
                rna = []
                for key, value in con[2]:
                    if (name, con[0]) in mutes:
                        if key == 'mute':
                            value = mutes[(name, con[0])]
                        elif key == 'enabled':
                            # Blender 5.2 exposes both writable aliases for
                            # the same constraint switch. Original saves mute.
                            value = not mutes[(name, con[0])]
                    rna.append((key, value))
                rna = tuple(rna)
                restored.append(con[:2] + (rna,) + con[3:])
            entry['constraints'] = tuple(restored)
    return result


def hand_channels(rig, *, source=None, category='ANIMATOR'):
    names = {bone.name for bone in rig.data.bones if bone.get(limb_ik.KIND_KEY) == 'ARM'}
    animator = {bone.name for bone in rig.data.bones if bone.get(limb_ik.KIND_KEY) == 'ARM'
                and bone.get(limb_ik.ROLE_KEY) in {'HAND_IK', 'POLE'}}
    state = original._channels(rig) if source is None else source
    for entry in limb_ik._load_direct_rest_registry(rig)['limbs'].values():
        if entry['kind'] == 'ARM':
            names.update(entry['chain'])
            descendants = {bone.name for bone in rig.data.bones[entry['chain'][-1]].children_recursive}
            names.update(descendants)
            animator.update(name for name in descendants
                if rig.data.bones[name].get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS)
            targets = [bone.name for bone in rig.data.bones if bone.get(limb_ik.KIND_KEY) == 'ARM'
                and bone.get(limb_ik.SIDE_KEY) == entry['side'] and bone.get(limb_ik.ROLE_KEY) == 'HAND_IK']
            if targets and targets[0] in state and state[targets[0]]['ik_fk'] == 0:
                animator.update(entry['chain'])  # Native FK sources are actual inputs in FK.
    if category == 'ANIMATOR':
        names &= animator
    elif category == 'NATIVE_INTERNAL':
        names -= animator
    elif category != 'ALL':
        raise ValueError('Unknown hand channel category.')
    return {name: state[name] for name in sorted(names) if name in state}


def hand_channel_changes(before, after, limit=12):
    changes = []
    for name in set(before) & set(after):
        for field, values in before[name]['channels'].items():
            maximum = max(abs(a - b) for a, b in zip(values, after[name]['channels'][field]))
            if maximum:
                changes.append({'name': name, 'field': field, 'max_difference': maximum})
    return sorted(changes, key=lambda entry: entry['max_difference'], reverse=True)[:limit]


def hand_channel_error(before, after):
    if set(before) != set(after):
        raise AssertionError('Hand controller channel inventory changed.')
    maximum = 0.
    for name, saved in before.items():
        current = after[name]
        if saved['mode'] != current['mode'] or saved['ik_fk'] != current['ik_fk']:
            raise AssertionError('Hand controller rotation mode or IK/FK setting changed: ' + name)
        for field, values in saved['channels'].items():
            maximum = max(maximum, max(abs(a - b) for a, b in zip(values, current['channels'][field])))
    return maximum


def _buffer(collection, field, width, code='f'):
    data = array(code, [0]) * (len(collection) * width)
    collection.foreach_get(field, data)
    return hashlib.sha256(data.tobytes()).hexdigest()


def author_meshes():
    """Compact exact raw author proofs, including the real-model runner."""
    result = {}
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
            continue
        mesh = obj.data
        weights = hashlib.sha256()
        for vertex in mesh.vertices:
            weights.update(struct.pack('<II', vertex.index, len(vertex.groups)))
            for group in vertex.groups:
                weights.update(struct.pack('<If', group.group, group.weight))
        managed = {record.get('key') for record in forearm_twist._records(obj).values()}
        keys = [(key.name, None if key.name in managed else _buffer(key.data, 'co', 3),
                 key.value, bool(key.mute), key.relative_key.name, key.vertex_group,
                 key.interpolation, key.slider_min, key.slider_max)
                for key in mesh.shape_keys.key_blocks] if mesh.shape_keys else []
        result[obj.name] = (obj.as_pointer(), mesh.as_pointer(),
            _buffer(mesh.vertices, 'co', 3), _buffer(mesh.edges, 'vertices', 2, 'i'),
            _buffer(mesh.loops, 'vertex_index', 1, 'i'), _buffer(mesh.polygons, 'loop_start', 1, 'i'),
            _buffer(mesh.polygons, 'loop_total', 1, 'i'), weights.hexdigest(),
            tuple((group.name, group.index, bool(group.lock_weight)) for group in obj.vertex_groups),
            tuple(keys), obj.get(forearm_twist.RECORD_KEY))
    return result


def restore_input(pb, saved):
    pb.rotation_mode = saved['mode']
    for field, values in saved['channels'].items():
        setattr(pb, field, values)


def exercise_feet(rig):
    """Match FK -> IK, small Foot Roll/Toe Bend, restore inputs, match back."""
    before_rest, before_raw = rest_all(rig), author_meshes()
    original_pose = native_matrices(rig)
    results = []
    for side in ('L', 'R'):
        key = ('LEG', side)
        inventory = limb_ik._validate_inventory(rig)
        data = inventory['rigs'][key]
        mode = limb_ik_fk.mode_for_rig(rig, data)
        if mode not in {'FK', 'IK'} or not data.get('foot_controls'):
            raise ValueError('This small exercise requires an existing FK/IK leg and Foot controls.')
        limb_ik_fk.switch_limb(bpy.context, rig, key, 'IK', keyframe=False)
        ik_pose = native_matrices(rig)
        switch_error = matrix_error(original_pose, ik_pose)
        if switch_error > POSE_LIMIT:
            raise AssertionError(f'{side} FK to IK changed native pose {switch_error}.')
        foot = foot_controls.records(rig)[side]
        roll, toe = rig.pose.bones[foot['roll']], rig.pose.bones[foot['toe_control']]
        if roll.rotation_mode != 'XYZ' or toe.rotation_mode != 'XYZ':
            raise ValueError('Foot controls do not use the expected native XYZ input channels.')
        inputs = original._channels(rig)
        affected = set(limb_ik_fk.pose_names(data))
        other = set(ik_pose) - affected
        roll.rotation_euler.x += .04
        active = native_matrices(rig)
        roll_response = matrix_error(ik_pose, active, {data['chain'][-1], foot['toe']})
        other_roll_error = matrix_error(ik_pose, active, other)
        if roll_response <= 1e-6 or other_roll_error > OTHER_POSE_LIMIT:
            raise AssertionError(f'{side} Foot Roll response/isolation failed: {roll_response}, {other_roll_error}.')
        restore_input(roll, inputs[roll.name])
        native_matrices(rig)
        toe.rotation_euler.x += .04
        active = native_matrices(rig)
        toe_response = matrix_error(ik_pose, active, {foot['toe']})
        foot_isolation_error = matrix_error(ik_pose, active, {data['chain'][-1]})
        other_toe_error = matrix_error(ik_pose, active, other)
        if toe_response <= 1e-6 or max(foot_isolation_error, other_toe_error) > OTHER_POSE_LIMIT:
            raise AssertionError(f'{side} Toe Bend response/isolation failed: {toe_response}, {foot_isolation_error}, {other_toe_error}.')
        restore_input(toe, inputs[toe.name])
        restore_error = matrix_error(ik_pose, native_matrices(rig))
        if restore_error > OTHER_POSE_LIMIT:
            raise AssertionError(f'{side} Foot inputs did not restore their pose: {restore_error}.')
        limb_ik_fk.switch_limb(bpy.context, rig, key, mode, keyframe=False)
        final_error = matrix_error(original_pose, native_matrices(rig))
        if final_error > POSE_LIMIT:
            raise AssertionError(f'{side} Switching back did not restore native pose: {final_error}.')
        results.append({'side': side, 'starting_mode': mode, 'angle_radians': .04,
            'switch_to_ik_max_pose_error': switch_error, 'roll_native_response': roll_response,
            'roll_other_native_pose_error': other_roll_error, 'toe_native_response': toe_response,
            'toe_foot_isolation_error': foot_isolation_error, 'toe_other_native_pose_error': other_toe_error,
            'restore_input_max_pose_error': restore_error, 'switch_back_max_pose_error': final_error,
            'fixed_pivot_names': sorted(foot['bones'].values())})
    if rest_all(rig) != before_rest or author_meshes() != before_raw:
        raise AssertionError('The Foot control exercise changed Rest or raw author inputs.')
    return {'sides': results, 'all_rest_exact': True, 'raw_author_meshes_exact': True,
        'native_pose_limit': POSE_LIMIT, 'other_limb_pose_limit': OTHER_POSE_LIMIT,
        'limitation': 'Existing Foot pivot Rest is preserved, not refitted to new leg proportions. Only +/-0.04-radian control response and return pose are exercised; no full gait or contact acceptance.'}


class OriginalLegRestReturnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def cycle(self, kind='ALL'):
        rig, mesh = make_fixture()
        untouched = set(rig.data.bones.keys()) - regional.leg_names(rig)
        settings_before = controller_settings(rig, untouched)
        hand_before = hand_channels(rig)
        native_internal_before = hand_channels(rig, category='NATIVE_INTERNAL')
        untouched_pose = {name: rig.pose.bones[name].matrix.copy() for name in untouched}
        original.enter(bpy.context, rig)
        edit_legs(rig, kind)
        edited, bone_rest = poses.native_rest(rig), rest_all(rig)
        ids, raw, desired = identities(rig), author_meshes(), native_matrices(rig)
        with patch.object(poses, '_match', wraps=poses._match) as matching:
            self.assertTrue(original.leave(bpy.context, rig))
        self.assertFalse(original.active(rig))
        self.assertEqual(poses.native_rest(rig), edited)
        self.assertEqual(rest_all(rig), bone_rest)
        self.assertEqual(identities(rig), ids)
        self.assertEqual(author_meshes(), raw)
        settings_after = controller_settings(rig, untouched)
        differences = regional.changed_fields(settings_after, settings_before, limit=16)
        hand_error = hand_channel_error(hand_before, hand_channels(rig))
        other_pose_error = matrix_error(untouched_pose,
            {name: rig.pose.bones[name].matrix.copy() for name in untouched})
        print('ORIGINAL_UNRELATED_CONTRACT ' + json.dumps({'kind': kind,
            'author_settings_differences': differences, 'hand_raw_channel_max_error': hand_error,
            'animator_hand_channel_changes': hand_channel_changes(hand_before, hand_channels(rig)),
            'native_internal_channel_changes': hand_channel_changes(native_internal_before,
                hand_channels(rig, category='NATIVE_INTERNAL')),
            'unrelated_evaluated_pose_max_error': other_pose_error, 'pose_limit': POSE_LIMIT}), flush=True)
        self.assertFalse(differences, 'Unrelated raw author settings changed: ' + repr(differences))
        self.assertLessEqual(hand_error, POSE_LIMIT)
        self.assertLessEqual(other_pose_error, POSE_LIMIT)
        self.assertLessEqual(matrix_error(desired, native_matrices(rig)), POSE_LIMIT)
        self.assertTrue(matching.called, 'Geometry-only foot/toe edits must force control matching.')
        forced = set().union(*(set(call.args[3]) for call in matching.call_args_list))
        self.assertIn('foot.L' if kind != 'TOE_ONLY' else 'toe.L', forced)
        event = json.loads(rig[resync.PROVENANCE_KEY])['events'][-1]
        expected = {name + '.' + side for side in ('L', 'R') for name in ('thigh', 'shin', 'foot', 'toe')} if kind == 'ALL' else {'foot.L' if kind == 'FOOT_ONLY' else 'toe.L'}
        self.assertEqual(set(event['changed']), expected)
        limb_ik._validate_inventory(rig)
        self.assertEqual({limb_ik_fk.mode_for_rig(rig, data) for key, data in limb_ik._validate_inventory(rig)['rigs'].items() if key[0] == 'LEG'}, {'FK'})
        return rig, mesh

    def test_eight_leg_foot_toe_rest_edits_return_in_place_then_exercise_controls(self):
        rig, _mesh = self.cycle()
        report = exercise_feet(rig)
        print('ORIGINAL_LEG_FOOT_RETURN_PASS ' + json.dumps(report), flush=True)

    def test_foot_only_and_toe_only_edits_force_matching(self):
        for kind in ('FOOT_ONLY', 'TOE_ONLY'):
            with self.subTest(kind=kind):
                self.cycle(kind)

    def test_data_bone_action_or_external_driver_refuses_edited_return(self):
        for dependency in ('DATA_ACTION', 'EXTERNAL_DATA_DRIVER'):
            with self.subTest(dependency=dependency):
                rig, mesh = make_fixture()
                original.enter(bpy.context, rig)
                edit_legs(rig, 'FOOT_ONLY')
                bone = rig.data.bones['foot.L']
                bone['artist_parameter'] = .25
                path = bone.path_from_id() + '["artist_parameter"]'
                if dependency == 'DATA_ACTION':
                    self.assertTrue(bone.keyframe_insert('["artist_parameter"]', frame=1))
                else:
                    obj = bpy.data.objects.new('External native foot reader', None)
                    bpy.context.scene.collection.objects.link(obj)
                    curve = obj.driver_add('location', 0)
                    for var in tuple(curve.driver.variables):
                        curve.driver.variables.remove(var)
                    var = curve.driver.variables.new()
                    var.name, var.type = 'native_value', 'SINGLE_PROP'
                    var.targets[0].id_type, var.targets[0].id = 'ARMATURE', rig.data
                    var.targets[0].data_path = path
                    curve.driver.type, curve.driver.expression = 'SCRIPTED', var.name
                    obj.update_tag(refresh={'OBJECT'})
                    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
                update(rig)
                before = regional.scene_state(rig, mesh)
                edited, raw, ids = poses.native_rest(rig), author_meshes(), identities(rig)
                with self.assertRaises(ValueError):
                    original.leave(bpy.context, rig)
                self.assertTrue(original.active(rig))
                self.assertEqual(regional.scene_state(rig, mesh), before)
                self.assertEqual(poses.native_rest(rig), edited)
                self.assertEqual(author_meshes(), raw)
                self.assertEqual(identities(rig), ids)

    def test_native_foot_toe_attachments_modifiers_and_authored_constraint_refuse(self):
        for dependency in ('BONE_PARENT_FOOT', 'BONE_PARENT_TOE', 'HOOK_TOE', 'WARP_FOOT', 'WRITE_TOE'):
            with self.subTest(dependency=dependency):
                rig, mesh = make_fixture()
                original.enter(bpy.context, rig)
                edit_legs(rig)
                modifier = constraint = attachment = None
                if dependency.startswith('BONE_PARENT_'):
                    attachment = bpy.data.objects.new('Artist native bone attachment', None)
                    bpy.context.scene.collection.objects.link(attachment)
                    attachment.parent, attachment.parent_type = rig, 'BONE'
                    attachment.parent_bone = 'foot.L' if dependency.endswith('FOOT') else 'toe.L'
                elif dependency in {'HOOK_TOE', 'WARP_FOOT'}:
                    data = bpy.data.meshes.new('Artist modifier dependency')
                    data.from_pydata(((.1, .1, .1), (.2, .1, .1), (.1, .2, .1)), (), ((0, 1, 2),))
                    obj = bpy.data.objects.new(data.name, data)
                    bpy.context.scene.collection.objects.link(obj)
                    if dependency == 'HOOK_TOE':
                        modifier = obj.modifiers.new('Artist Hook reads native toe', 'HOOK')
                        modifier.object, modifier.subtarget = rig, 'toe.L'
                        modifier.vertex_indices_set([0, 1, 2])
                    else:
                        modifier = obj.modifiers.new('Artist Warp reads native foot', 'WARP')
                        modifier.object_from, modifier.bone_from = rig, 'foot.L'
                        modifier.object_to, modifier.bone_to = rig, 'Hips'
                else:
                    constraint = rig.pose.bones['toe.L'].constraints.new('LIMIT_ROTATION')
                    constraint.name = 'Artist native toe input'
                    constraint.owner_space = 'LOCAL'
                    constraint.use_limit_x = True
                    constraint.min_x, constraint.max_x = -.2, .2
                update(rig)
                before = regional.scene_state(rig, mesh)
                edited, raw, ids = poses.native_rest(rig), author_meshes(), identities(rig)
                modifier_before = regional.value(regional.transaction._rna(modifier)) if modifier else None
                constraint_before = regional.constraint(constraint) if constraint else None
                attachment_before = (attachment.parent, attachment.parent_type, attachment.parent_bone,
                                     regional.matrix(attachment.matrix_parent_inverse)) if attachment else None
                with self.assertRaises(ValueError):
                    original.leave(bpy.context, rig)
                self.assertTrue(original.active(rig))
                self.assertEqual(regional.scene_state(rig, mesh), before)
                self.assertEqual(poses.native_rest(rig), edited)
                self.assertEqual(author_meshes(), raw)
                self.assertEqual(identities(rig), ids)
                if modifier:
                    self.assertEqual(regional.value(regional.transaction._rna(modifier)), modifier_before)
                if constraint:
                    self.assertEqual(regional.constraint(constraint), constraint_before)
                if attachment:
                    self.assertEqual((attachment.parent, attachment.parent_type, attachment.parent_bone,
                                      regional.matrix(attachment.matrix_parent_inverse)), attachment_before)


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OriginalLegRestReturnTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise RuntimeError('Original leg/foot Rest return tests failed')
