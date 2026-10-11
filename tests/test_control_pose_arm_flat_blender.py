"""Native regression for a near-flat arm Pose followed by hand IK motion.

Run only in a disposable Blender process, for example:
blender --background --factory-startup --disable-autoexec --threads 2
        --python-exit-code 1 --python tests/test_control_pose_arm_flat_blender.py

This synthetic fixture contains no artist mesh or external asset. An explicitly
tagged asset includes healthy native IK reference bases. Its shallow downward
elbow residual resembles the Arm Flat regression: the saved gesture is almost
straight, but normalizing its few-millimetre residual gives a very different
plane from the arm's configured backward bend. The initial flat pose and the
meaningful bend after retracting its hand are measured separately. Untagged
assets remain exact, including an intentionally authored downward bend.
Asset Browser mouse events and native Undo are outside this suite.
"""
import json
import math
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import control_pose_assets as poses, limb_ik, limb_ik_fk as match
from character_designer import body_original_mode as original, bone_collections
import test_limb_ik_blender as base


def update(rig):
    rig.update_tag(refresh={'OBJECT', 'DATA', 'TIME'})
    bpy.context.view_layer.update()


def matrices(rig):
    update(rig)
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return {name: evaluated.pose.bones[name].matrix.copy()
            for name in poses.native_rest(rig)}


def matrix_error(left, right):
    return max(abs(a - b) for row_a, row_b in zip(left, right)
               for a, b in zip(row_a, row_b))


def arm_names(side):
    return tuple(part + '.' + side for part in ('upper_arm', 'forearm', 'hand'))


def geometry_from_matrices(evaluated, side):
    """An independent geometric oracle; no production Pole helpers are used."""
    start, joint, end = (evaluated[name].translation for name in arm_names(side))
    chord = end - start
    if chord.length < 1e-8:
        raise AssertionError('The fixture has coincident shoulder and wrist.')
    axis = chord.normalized()
    radial = joint - start - axis * (joint - start).dot(axis)
    back = Vector((0., 1., 0.))
    back -= axis * back.dot(axis)
    if radial.length < 1e-8 or back.length < 1e-8:
        raise AssertionError('The measured bend or posterior projection is degenerate.')
    return {'radial_ratio': radial.length / chord.length,
            'posterior_dot': radial.normalized().dot(back.normalized()),
            'radial_direction': tuple(radial.normalized())}


def bend_geometry(armature, side):
    return geometry_from_matrices(matrices(armature), side)


def frame(position, direction, roll=0.):
    """Construct an FK orientation from axes, independent of the IK matcher."""
    y_axis = Vector(direction).normalized()
    z_axis = Vector((0., 0., 1.))
    z_axis -= y_axis * z_axis.dot(y_axis)
    z_axis.normalize()
    x_axis = y_axis.cross(z_axis).normalized()
    z_axis = x_axis.cross(y_axis).normalized()
    rotation = Matrix((x_axis, y_axis, z_axis)).transposed().to_quaternion()
    if roll:
        rotation = Quaternion(y_axis, roll) @ rotation
    return Matrix.LocRotScale(position, rotation, Vector((1., 1., 1.)))


def transported_frame(position, direction, neutral_frame, *, twist=0.):
    """Keep the healthy axial frame while swinging its longitudinal axis."""
    original_y = neutral_frame.to_3x3().col[1].normalized()
    desired_y = Vector(direction).normalized()
    swing = original_y.rotation_difference(desired_y)
    rotation = swing @ neutral_frame.to_quaternion().normalized()
    if twist:
        rotation = Quaternion(desired_y, twist) @ rotation
    return Matrix.LocRotScale(position, rotation.normalized(), Vector((1., 1., 1.)))


def fixture(*, sides='LR', fingers=False):
    base.reset_scene()
    rig = base.make_humanoid(roll_offset=.37)
    # The real right forearm/wrist also have an additional half turn in Rest.
    # Keeping it here makes mirror checks sensitive to local-axis assumptions.
    bpy.ops.object.mode_set(mode='EDIT')
    for name in ('forearm.R', 'hand.R'):
        rig.data.edit_bones[name].roll += math.pi
    if fingers:
        for side in 'LR':
            parent = rig.data.edit_bones['hand.' + side]
            direction = Vector((.022 if side == 'L' else -.022, 0., -.0015))
            for number in range(1, 4):
                head = parent.tail.copy()
                parent = base.add_bone(rig.data.edit_bones, f'f_index.{number:02d}.{side}',
                                       head, head + direction, parent)
                parent.use_connect = True
    bpy.ops.object.mode_set(mode='POSE')
    for bone in rig.pose.bones:
        bone.rotation_mode = 'QUATERNION'
    poses.capture_baseline(rig)
    result, settings = base.analyze(rig)
    if result != {'FINISHED'}:
        raise AssertionError(settings.last_message)
    settings.build_method = 'DIRECT_PREROLL'
    for side in sides:
        settings.selected_limb = 'LEFT_ARM' if side == 'L' else 'RIGHT_ARM'
        if bpy.ops.character_designer.limb_ik_direct_preroll_check() != {'FINISHED'}:
            raise AssertionError(settings.last_message)
        if bpy.ops.character_designer.limb_ik_build_selected() != {'FINISHED'}:
            raise AssertionError(settings.last_message)
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    update(rig)
    return rig


def authored_asset(rig, *, near_flat, roll=.37, stable_ik_bend=None):
    """Author complete channels with Blender on an unconstrained FK copy."""
    neutral = matrices(rig)
    healthy_seed = {'ARM/' + side: {
        name: {'rotation_mode': rig.pose.bones[name].rotation_mode,
               'matrix_basis': [list(row) for row in rig.pose.bones[name].matrix_basis]}
        for name in arm_names(side)} for side in 'LR'}
    reference = rig.copy()
    reference.data = rig.data.copy()
    bpy.context.scene.collection.objects.link(reference)
    reference.animation_data_clear()
    reference.data.animation_data_clear()
    action = None
    try:
        for pb in reference.pose.bones:
            for constraint in list(pb.constraints):
                pb.constraints.remove(constraint)
            pb.rotation_mode = 'QUATERNION'
        # Reconstruct the current evaluated parent pose before authoring the arm.
        for name in sorted(neutral, key=lambda n: len(reference.pose.bones[n].parent_recursive)):
            reference.pose.bones[name].matrix = neutral[name]
            update(reference)
        upper, lower, hand = (reference.pose.bones[name] for name in arm_names('L'))
        start = upper.matrix.translation.copy()
        if near_flat:
            upper_direction = Vector((1., 0., 0.))
            # Approximately 1.08% radial/chord ratio; posterior dot about .32.
            # It straddles the old 1% near-straight threshold in real Arm Flat.
            lower_direction = Vector((1., -.014, .041)).normalized()
        else:
            upper_direction = Vector((1., .55, .06)).normalized()
            lower_direction = Vector((1., -.55, -.06)).normalized()
        # A world-Z frame plus an arbitrary roll is not interchangeable with
        # this rig's healthy axial frame. Flat assets retain that frame through
        # an independently computed shortest swing, as a native T pose would.
        # Nondefault roll remains an explicit twist for exact-FK/rejection cases.
        if near_flat:
            wanted_frame = lambda position, direction, name: transported_frame(
                position, direction, neutral[name], twist=roll - .37)
        else:
            wanted_frame = lambda position, direction, name: frame(position, direction, roll)
        upper.matrix = wanted_frame(start, upper_direction, 'upper_arm.L')
        update(reference)
        joint = start + upper_direction * upper.bone.length
        lower.matrix = wanted_frame(joint, lower_direction, 'forearm.L')
        update(reference)
        end = joint + lower_direction * lower.bone.length
        hand.matrix = wanted_frame(end, lower_direction, 'hand.L')
        update(reference)
        expected = {name: reference.pose.bones[name].matrix.copy() for name in arm_names('L')}
        action = bpy.data.actions.new('Synthetic Arm Flat' if near_flat else 'Synthetic bent arm')
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        bag = action.layers.new(name='Pose').strips.new(type='KEYFRAME').channelbag(slot, ensure=True)
        for name in arm_names('L'):
            pb = reference.pose.bones[name]
            for prop in ('location', 'rotation_quaternion', 'scale'):
                for index, value in enumerate(getattr(pb, prop)):
                    curve = bag.fcurves.new(data_path=rig.pose.bones[name].path_from_id(prop), index=index)
                    curve.keyframe_points.insert(1., float(value)).interpolation = 'CONSTANT'
        metadata = {'version': 1, 'source_object': rig.name, 'source_slot': slot.identifier,
                    'rest': poses.native_rest(rig), 'scope': 'SELECTED',
                    'names': list(arm_names('L')), 'include_fingers': False,
                    'control_modes': {'ARM/L': {'mode': 'FK', 'value': 0.}}}
        if stable_ik_bend is not None:
            metadata['stable_ik_bend'] = stable_ik_bend
        if stable_ik_bend is True:
            # This is explicit provenance from the verified healthy IK input,
            # not the hidden FK channels left by a later Pose application.
            metadata['stable_ik_seed'] = healthy_seed
        action[poses.ASSET_METADATA] = json.dumps(metadata, separators=(',', ':'))
        action.asset_mark()
        action.use_fake_user = True
        return action, expected
    except Exception:
        if action is not None:
            bpy.data.actions.remove(action)
        raise
    finally:
        data = reference.data
        bpy.data.objects.remove(reference, do_unlink=True)
        bpy.data.armatures.remove(data)


def apply_wrapper(rig, action, *, flipped=False, expected_result=None):
    """Run the production Apply routing with a local Action, without GUI mocks."""
    context = SimpleNamespace(object=rig, mode='POSE', scene=bpy.context.scene,
                              view_layer=bpy.context.view_layer,
                              evaluated_depsgraph_get=bpy.context.evaluated_depsgraph_get,
                              asset=SimpleNamespace(id_type='ACTION', local_id=action,
                                                    name=action.name, full_library_path=''))
    operator = SimpleNamespace(flipped=flipped, report=Mock())
    result = poses.CHARACTERDESIGNER_OT_apply_control_pose.execute(operator, context)
    if result != (expected_result if expected_result is not None else {'FINISHED'}):
        raise AssertionError(operator.report.call_args_list)
    update(rig)
    return operator


def asset_curves(action, rig):
    return tuple((curve.data_path, curve.array_index,
                  tuple((tuple(point.co), point.interpolation) for point in curve.keyframe_points))
                 for layer in action.layers for strip in layer.strips
                 for bag in strip.channelbags for curve in bag.fcurves)


def raw_pose(rig):
    """Native channels and public switch values, independent of apply internals."""
    return {pb.name: {'rotation_mode': pb.rotation_mode,
                     'location': tuple(pb.location), 'scale': tuple(pb.scale),
                     'rotation_quaternion': tuple(pb.rotation_quaternion),
                     'rotation_euler': tuple(pb.rotation_euler),
                     'rotation_axis_angle': tuple(pb.rotation_axis_angle),
                     'ik_fk': pb.get(match.PROPERTY)} for pb in rig.pose.bones}


def leave_authored_sources_with_cleared_controls(rig, action, side='L'):
    """Reproduce stale FK input with native channel writes, without matching."""
    if side != 'L':
        raise ValueError('The synthetic authored source channels are left-only.')
    fields = poses.channels(action, rig)
    for name, properties in fields.items():
        pb = rig.pose.bones[name]
        pb.rotation_mode = 'QUATERNION'
        for prop in ('location', 'rotation_quaternion', 'scale'):
            setattr(pb, prop, [properties[prop][index]
                               for index in range(len(properties[prop]))])
    entry = limb_ik._validate_inventory(rig)['rigs'][('ARM', side)]
    for field in ('target', 'pole'):
        rig.pose.bones[entry[field].name].matrix_basis = Matrix.Identity(4)
    update(rig)


def finger_channels(rig, names):
    """Read complete native transform representations and B-Bone channels."""
    result = {}
    for name in names:
        pb = rig.pose.bones[name]
        result[name] = {'rotation_mode': pb.rotation_mode,
                        'location': tuple(pb.location), 'scale': tuple(pb.scale),
                        'rotation_quaternion': tuple(pb.rotation_quaternion),
                        'rotation_euler': tuple(pb.rotation_euler),
                        'rotation_axis_angle': tuple(pb.rotation_axis_angle)}
        for prop, count in poses._BBONE.items():
            value = getattr(pb, prop)
            result[name][prop] = (float(value),) if count == 1 else tuple(value)
    return result


def skin_mirror_reference(rig, expected):
    """Reflect parent-relative skin increments with no production mirror calls."""
    reflection = Matrix.Diagonal((-1., 1., 1., 1.))
    current, wanted = matrices(rig), {}
    for left, right in zip(arm_names('L'), arm_names('R')):
        left_bone = rig.data.bones[left]
        right_bone = rig.data.bones[right]
        source_skin = expected[left] @ left_bone.matrix_local.inverted()
        source_parent_skin = (expected[left_bone.parent.name] @ left_bone.parent.matrix_local.inverted()
                              if left_bone.parent.name in expected else
                              current[left_bone.parent.name] @ left_bone.parent.matrix_local.inverted())
        increment = source_parent_skin.inverted() @ source_skin
        target_parent_skin = (wanted[right_bone.parent.name] @ right_bone.parent.matrix_local.inverted()
                              if right_bone.parent.name in wanted else
                              current[right_bone.parent.name] @ right_bone.parent.matrix_local.inverted())
        wanted[right] = target_parent_skin @ reflection @ increment @ reflection @ right_bone.matrix_local
    return wanted


class ArmFlatContinuation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('This suite must run in an isolated background Blender.')
        character_designer.register()

    def setUp(self):
        bpy.context.scene.frame_set(1)
        self.rig = fixture()
        self.actions = []

    def tearDown(self):
        if original.active(self.rig):
            # A disposable test must not run an unrelated return-to-Controls
            # match while cleaning up a failed Original application.
            del self.rig[original.SESSION]
        for action in self.actions:
            bpy.data.actions.remove(action)

    def asset(self, **kwargs):
        action, expected = authored_asset(self.rig, **kwargs)
        self.actions.append(action)
        return action, expected

    def entry(self, side):
        return limb_ik._validate_inventory(self.rig)['rigs'][('ARM', side)]

    def assert_pose(self, expected):
        current = matrices(self.rig)
        errors = {name: matrix_error(current[name], matrix) for name, matrix in expected.items()}
        self.assertLess(max(errors.values()), 4e-4, errors)

    def assert_bounded_flat_pose(self, expected, *, mirrored_expected=None):
        # The opt-in contract allows a bounded shallow-plane adjustment while
        # retaining the exact wrist. Ordinary and FK assets stay exact below.
        current = matrices(self.rig)
        start, joint, end = (expected[name].translation for name in arm_names('L'))
        length = (joint - start).length + (end - joint).length
        authored = geometry_from_matrices(expected, 'L')
        self.assertGreater(authored['radial_ratio'], .01, authored)
        self.assertLess(authored['radial_ratio'], .02, authored)
        self.assertLess(authored['posterior_dot'], .5, authored)
        wanted_pose = mirrored_expected if mirrored_expected is not None else expected
        for name, wanted in wanted_pose.items():
            position_error = (current[name].translation - wanted.translation).length
            delta = current[name].to_quaternion().normalized().rotation_difference(
                wanted.to_quaternion().normalized())
            rotation_error = 2. * math.atan2(Vector((delta.x, delta.y, delta.z)).length, abs(delta.w))
            with self.subTest(flat_pose_bone=name):
                self.assertLess(position_error, length * .02)
                self.assertLess(rotation_error, math.radians(3.))
        hand_name = 'hand.R' if mirrored_expected is not None else 'hand.L'
        self.assertLess((current[hand_name].translation - wanted_pose[hand_name].translation).length, 1e-5)

    def assert_raw_pose_equal(self, expected):
        actual = raw_pose(self.rig)
        self.assertEqual(actual.keys(), expected.keys())
        for name, state in expected.items():
            for prop, wanted in state.items():
                with self.subTest(raw_bone=name, property=prop):
                    if isinstance(wanted, tuple):
                        self.assertLessEqual(max(abs(a - b) for a, b in zip(actual[name][prop], wanted)), 1e-6)
                    else:
                        self.assertEqual(actual[name][prop], wanted)

    def assert_healthy_seed(self, action, side):
        seed = json.loads(action[poses.ASSET_METADATA])['stable_ik_seed']['ARM/' + side]
        for name, state in seed.items():
            with self.subTest(seed_bone=name):
                self.assertEqual(self.rig.pose.bones[name].rotation_mode, state['rotation_mode'])
                self.assertLessEqual(matrix_error(self.rig.pose.bones[name].matrix_basis,
                                                  Matrix(state['matrix_basis'])), 1e-6)

    def retract_measurements(self, side):
        entry = self.entry(side)
        basis = {pb.name: pb.matrix_basis.copy() for pb in self.rig.pose.bones}
        angles = {pb.name: tuple((con.name, con.pole_angle) for con in pb.constraints if con.type == 'IK')
                  for pb in self.rig.pose.bones}
        results = []
        try:
            for fraction in (.02, .15, .30):
                for name, value in basis.items():
                    self.rig.pose.bones[name].matrix_basis = value
                update(self.rig)
                start = matrices(self.rig)[arm_names(side)[0]].translation
                target = self.rig.pose.bones[entry['target'].name]
                wanted = target.matrix.copy()
                wanted.translation += (start - wanted.translation) * fraction
                target.matrix = wanted
                measured = bend_geometry(self.rig, side)
                with self.subTest(side=side, retract=fraction):
                    # Measure a clear bend, not a normalized near-zero residual.
                    self.assertGreater(measured['radial_ratio'], .02, measured)
                    self.assertEqual(angles, {
                        pb.name: tuple((con.name, con.pole_angle) for con in pb.constraints if con.type == 'IK')
                        for pb in self.rig.pose.bones})
                results.append((fraction, measured))
        finally:
            for name, value in basis.items():
                self.rig.pose.bones[name].matrix_basis = value
            update(self.rig)
        return results

    def assert_retracts_backwards(self, side):
        for fraction, measured in self.retract_measurements(side):
            with self.subTest(side=side, retract=fraction):
                self.assertGreater(measured['posterior_dot'], .97, measured)

    def test_near_flat_apply_then_retract_keeps_backward_bend(self):
        action, expected = self.asset(near_flat=True, stable_ik_bend=True)
        original_rest = poses.native_rest(self.rig)
        curves = asset_curves(action, self.rig)
        sources = {name: self.rig.pose.bones[name].matrix_basis.copy() for name in arm_names('L')}
        apply_wrapper(self.rig, action)
        # The gesture's wrist position is useful even when its tiny elbow
        # residual is an unreliable choice for subsequent deep bending.
        self.assert_bounded_flat_pose(expected)
        self.assertEqual(match.mode_for_rig(self.rig, self.entry('L')), 'IK')
        self.assertEqual(sources, {name: self.rig.pose.bones[name].matrix_basis for name in sources})
        self.assert_healthy_seed(action, 'L')
        self.assert_retracts_backwards('L')
        self.assertEqual(original_rest, poses.native_rest(self.rig))
        self.assertEqual(curves, asset_curves(action, self.rig))

    def test_near_flat_mirror_then_retract_keeps_backward_bend(self):
        action, expected = self.asset(near_flat=True, stable_ik_bend=True)
        left = {name: value for name, value in matrices(self.rig).items() if name.endswith('.L')}
        wanted = skin_mirror_reference(self.rig, expected)
        sources = {name: self.rig.pose.bones[name].matrix_basis.copy() for name in arm_names('R')}
        apply_wrapper(self.rig, action, flipped=True)
        self.assert_bounded_flat_pose(expected, mirrored_expected=wanted)
        self.assert_pose(left)
        self.assertEqual(match.mode_for_rig(self.rig, self.entry('R')), 'IK')
        self.assertEqual(sources, {name: self.rig.pose.bones[name].matrix_basis for name in sources})
        self.assert_healthy_seed(action, 'R')
        self.assert_retracts_backwards('R')

    def test_fk_apply_preserves_authored_flat_pose_exactly(self):
        action, expected = self.asset(near_flat=True, roll=.61, stable_ik_bend=True)
        match.switch_limb(bpy.context, self.rig, ('ARM', 'L'), 'FK', keyframe=False)
        apply_wrapper(self.rig, action)
        self.assertEqual(match.mode_for_rig(self.rig, self.entry('L')), 'FK')
        self.assert_pose(expected)

    def test_default_flat_asset_retains_exact_saved_pose_and_plane(self):
        action, expected = self.asset(near_flat=True)
        self.assertNotIn('stable_ik_bend', poses.asset_metadata(action))
        apply_wrapper(self.rig, action)
        self.assert_pose(expected)
        for fraction, measured in self.retract_measurements('L'):
            with self.subTest(retract=fraction):
                # An untagged asset deliberately preserves its authored plane.
                self.assertAlmostEqual(measured['posterior_dot'],
                                       geometry_from_matrices(expected, 'L')['posterior_dot'], delta=.01)
                self.assertLess(measured['radial_direction'][2], -.8, measured)

    def test_nonboolean_stable_bend_metadata_is_rejected_without_pose_writes(self):
        action, _expected = self.asset(near_flat=True)
        original_metadata = poses.asset_metadata(action)
        pose_before, rest_before = raw_pose(self.rig), poses.native_rest(self.rig)
        for invalid in (1, 'true', None, []):
            with self.subTest(invalid=invalid):
                metadata = dict(original_metadata, stable_ik_bend=invalid)
                action[poses.ASSET_METADATA] = json.dumps(metadata, separators=(',', ':'))
                operator = apply_wrapper(self.rig, action, expected_result={'CANCELLED'})
                self.assertTrue(operator.report.called)
                self.assert_raw_pose_equal(pose_before)
                self.assertEqual(rest_before, poses.native_rest(self.rig))

    def test_opt_in_incompatible_twist_rolls_back_pose_and_controls(self):
        action, _expected = self.asset(near_flat=True, roll=.37 + math.pi / 2., stable_ik_bend=True)
        # Restoring the healthy reference changes these native inputs before
        # the bounded-pose check fails. Rollback must recover the dirty inputs,
        # not leave the healthy seed behind after cancelling the application.
        leave_authored_sources_with_cleared_controls(self.rig, action)
        pose_before, rest_before = raw_pose(self.rig), poses.native_rest(self.rig)
        evaluated_before = matrices(self.rig)
        curves = asset_curves(action, self.rig)
        operator = apply_wrapper(self.rig, action, expected_result={'CANCELLED'})
        self.assertTrue(operator.report.called)
        self.assert_raw_pose_equal(pose_before)
        self.assert_pose(evaluated_before)
        self.assertEqual(rest_before, poses.native_rest(self.rig))
        self.assertEqual(curves, asset_curves(action, self.rig))

    def test_dirty_source_seed_is_repaired_before_matching_flat_pose(self):
        action, expected = self.asset(near_flat=True, stable_ik_bend=True)
        # Reproduce the old apply->clear-visible-controls mismatch directly:
        # authored native FK channels remain, but Hand/Pole return to Rest.
        # This preparation does not call the production matching branch.
        leave_authored_sources_with_cleared_controls(self.rig, action)
        dirty_sources = {name: self.rig.pose.bones[name].matrix_basis.copy()
                         for name in arm_names('L')}
        healthy = json.loads(action[poses.ASSET_METADATA])['stable_ik_seed']['ARM/L']
        self.assertGreater(max(matrix_error(dirty_sources[name], Matrix(healthy[name]['matrix_basis']))
                               for name in dirty_sources), .01)
        before = self.retract_measurements('L')
        self.assertTrue(any(measured['posterior_dot'] < .9 for _fraction, measured in before), before)
        apply_wrapper(self.rig, action)
        self.assert_bounded_flat_pose(expected)
        self.assert_healthy_seed(action, 'L')
        self.assert_retracts_backwards('L')

    def test_left_generated_arm_ignores_unused_right_reference_seed(self):
        self.rig = fixture(sides='L')
        inventory = limb_ik._validate_inventory(self.rig)
        self.assertIn(('ARM', 'L'), inventory['rigs'])
        self.assertNotIn(('ARM', 'R'), inventory['rigs'])
        self.assertTrue(all(name in self.rig.data.bones for name in arm_names('R')))
        action, expected = self.asset(near_flat=True, stable_ik_bend=True)
        metadata = json.loads(action[poses.ASSET_METADATA])
        self.assertEqual(set(metadata['stable_ik_seed']), {'ARM/L', 'ARM/R'})
        right_before = {name: value for name, value in matrices(self.rig).items() if name.endswith('.R')}
        apply_wrapper(self.rig, action)
        self.assert_bounded_flat_pose(expected)
        self.assert_healthy_seed(action, 'L')
        self.assert_pose(right_before)
        self.assert_retracts_backwards('L')

    def test_failure_after_stable_matching_restores_dirty_pose_and_controls(self):
        action, _expected = self.asset(near_flat=True, stable_ik_bend=True)
        leave_authored_sources_with_cleared_controls(self.rig, action)
        pose_before, rest_before = raw_pose(self.rig), poses.native_rest(self.rig)
        evaluated_before, curves = matrices(self.rig), asset_curves(action, self.rig)
        original_match = poses._match_stable_ik_bend
        reached = []

        def fail_after_match(*args, **kwargs):
            result = original_match(*args, **kwargs)
            reached.append(result)
            raise ValueError('injected after stable match')

        with patch.object(poses, '_match_stable_ik_bend', side_effect=fail_after_match):
            operator = apply_wrapper(self.rig, action, expected_result={'CANCELLED'})
        self.assertEqual(reached, [True], 'The stable matching branch must complete before injection.')
        self.assertTrue(any('injected after stable match' in str(call)
                            for call in operator.report.call_args_list))
        self.assert_raw_pose_equal(pose_before)
        self.assert_pose(evaluated_before)
        self.assertEqual(rest_before, poses.native_rest(self.rig))
        self.assertEqual(curves, asset_curves(action, self.rig))

    def test_tagged_asset_in_original_keeps_exact_native_pose_and_session(self):
        bone_collections.simplify_body_collections(self.rig)
        action, expected = self.asset(near_flat=True, stable_ik_bend=True)
        curves = asset_curves(action, self.rig)
        original.enter(bpy.context, self.rig)
        self.assertTrue(original.active(self.rig))
        saved = json.loads(self.rig[original.SESSION])
        owned = {(entry['bone'], entry['name']) for entry in saved['constraints']}
        muted_before = {(name, constraint_name): self.rig.pose.bones[name].constraints[constraint_name].mute
                        for name, constraint_name in owned}
        self.assertTrue(owned, 'The test must enter a real owned-constraint Original session.')
        apply_wrapper(self.rig, action)
        self.assertTrue(original.active(self.rig))
        self.assert_pose(expected)
        self.assertEqual(muted_before, {
            (name, constraint_name): self.rig.pose.bones[name].constraints[constraint_name].mute
            for name, constraint_name in owned})
        current_session = json.loads(self.rig[original.SESSION])
        self.assertEqual(saved['rest'], current_session['rest'])
        self.assertEqual(saved['constraints'], current_session['constraints'])
        self.assertEqual(curves, asset_curves(action, self.rig))

    def assert_fist_channels_survive_arm_flat(self, *, flipped):
        self.rig = fixture(fingers=True)
        names = [f'f_index.{number:02d}.{side}' for side in 'LR' for number in range(1, 4)]
        for index, name in enumerate(names):
            pb = self.rig.pose.bones[name]
            self.assertTrue(pb.bone.use_connect, name)
            pb.rotation_mode = 'QUATERNION'
            pb.location = (.003, -.012, .002)
            pb.rotation_quaternion = Quaternion(Vector((1., .15, .03)).normalized(), .65 + index * .04)
            pb.scale = (1.01, 1., .99)
            pb.bbone_curveinx = .002
            pb.bbone_curveinz = -.003
            pb.bbone_rollin = .08
            pb.bbone_rollout = -.06
            pb.bbone_easein = .85
            pb.bbone_scalein = (1.02, .97, 1.01)
        update(self.rig)
        before = matrices(self.rig)
        channels_before = finger_channels(self.rig, names)
        action, expected = self.asset(near_flat=True, stable_ik_bend=True)
        self.assertEqual(set(poses.channels(action, self.rig)), set(arm_names('L')),
                         'The arm preset must contain no finger channels.')
        curves = asset_curves(action, self.rig)
        side = 'R' if flipped else 'L'
        wrist = 'hand.' + side
        wanted_arm = skin_mirror_reference(self.rig, expected) if flipped else expected
        hand_delta = wanted_arm[wrist] @ before[wrist].inverted()
        expected_fingers = {name: (hand_delta @ before[name] if name.endswith('.' + side)
                                   else before[name]) for name in names}
        apply_wrapper(self.rig, action, flipped=flipped)
        channels_after = finger_channels(self.rig, names)
        for name, fields in channels_before.items():
            for prop, wanted in fields.items():
                with self.subTest(flipped=flipped, finger=name, channel=prop):
                    if prop == 'rotation_mode':
                        self.assertEqual(channels_after[name][prop], wanted)
                    else:
                        self.assertLessEqual(max(abs(a - b) for a, b in zip(channels_after[name][prop], wanted)), 1e-7)
        self.assert_pose(expected_fingers)
        changed = max(matrix_error(matrices(self.rig)[name], before[name])
                      for name in names if name.endswith('.' + side))
        self.assertGreater(changed, 1e-4, 'The preserved fist must follow a meaningfully moved wrist.')
        self.assertEqual(curves, asset_curves(action, self.rig))

    def test_flat_apply_preserves_connected_fist_channels_while_wrist_moves(self):
        self.assert_fist_channels_survive_arm_flat(flipped=False)

    def test_flat_mirror_preserves_connected_fist_channels_while_wrist_moves(self):
        self.assert_fist_channels_survive_arm_flat(flipped=True)

    def test_malformed_reference_seed_is_rejected_without_pose_writes(self):
        action, _expected = self.asset(near_flat=True, stable_ik_bend=True)
        original_metadata = json.loads(action[poses.ASSET_METADATA])
        pose_before, rest_before = raw_pose(self.rig), poses.native_rest(self.rig)
        evaluated_before, curves = matrices(self.rig), asset_curves(action, self.rig)
        for fault in ('shape', 'bone_name', 'nan', 'rotation_mode'):
            with self.subTest(seed_fault=fault):
                metadata = json.loads(json.dumps(original_metadata))
                arm = metadata['stable_ik_seed']['ARM/L']
                if fault == 'shape':
                    arm['upper_arm.L']['matrix_basis'] = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
                elif fault == 'bone_name':
                    arm['upper_arm.R'] = arm.pop('upper_arm.L')
                elif fault == 'nan':
                    arm['upper_arm.L']['matrix_basis'][0][0] = float('nan')
                else:
                    arm['upper_arm.L']['rotation_mode'] = 'NOT_A_MODE'
                action[poses.ASSET_METADATA] = json.dumps(metadata, separators=(',', ':'))
                operator = apply_wrapper(self.rig, action, expected_result={'CANCELLED'})
                self.assertTrue(operator.report.called)
                self.assert_raw_pose_equal(pose_before)
                self.assert_pose(evaluated_before)
                self.assertEqual(rest_before, poses.native_rest(self.rig))
                self.assertEqual(curves, asset_curves(action, self.rig))

    def test_fully_bent_ik_preserves_native_fk_pose_and_direction(self):
        action, expected = self.asset(near_flat=False, roll=.61)
        apply_wrapper(self.rig, action)
        self.assert_pose(expected)
        self.assertGreater(bend_geometry(self.rig, 'L')['posterior_dot'], .97)

    def test_fully_bent_mirror_preserves_independent_skin_reflection(self):
        action, expected = self.asset(near_flat=False, roll=.61)
        # Parent-relative skin increments are reflected, not raw local Euler
        # or quaternion channels; the right Rest half turns remain intact.
        wanted = skin_mirror_reference(self.rig, expected)
        apply_wrapper(self.rig, action, flipped=True)
        self.assert_pose(wanted)
        self.assertGreater(bend_geometry(self.rig, 'R')['posterior_dot'], .97)


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    print('ARM_FLAT_BOUNDARY: native synthetic Direct IK/FK and Apply routing; '
          'artist model, Asset Browser events, Undo and reset shortcuts not tested.', flush=True)
    if not result.wasSuccessful():
        raise RuntimeError('Arm Flat continuation regression failed')
