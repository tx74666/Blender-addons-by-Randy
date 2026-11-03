"""Pure lifecycle/admission contracts; no Blender, mathutils or native claims."""
import ast
from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / 'addons/character_designer'
PACKAGE = '_key_reduce_contract_test_package'
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
spec = importlib.util.spec_from_file_location(PACKAGE + '.animation_key_reduce', ROOT / 'animation_key_reduce.py')
reduce = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = reduce
spec.loader.exec_module(reduce)


class Action(dict):
    def __init__(self, name, keys=6):
        super().__init__()
        self.name = name
        self.use_fake_user = True
        self.hashes = {1: name + '-hash'}
        self.slots = [NS(handle=1, target_id_type='OBJECT')]
        self.curves = [NS(keyframe_points=list(range(keys)), data_path='pose.channel',
                          array_index=0, mute=False, evaluate=lambda frame: 0.)]
        self.pose_markers = []

    def as_pointer(self):
        return id(self)


class Rig(dict):
    pass


def fixture():
    original, source, candidate = Action('Before', 10), Action('Source'), Action('After')
    rig = Rig()
    rig.animation_data = NS(action=original, action_slot=NS(handle=1))
    rig.as_pointer = lambda: 37
    rig.path_resolve = lambda path: [0.]
    row = NS(item_id='a'*32, rig=rig, side='CUSTOM', custom_action=original, custom_slot=1,
             source_action=source, source_slot=1, identity='current')
    saved = NS(items=[row], active_index=0, status='before', has_error=False)
    context = NS(scene=NS(frame_current=7, frame_subframe=0.,
        tool_settings=NS(use_keyframe_insert_auto=True)), view_layer=NS(update=Mock()))
    api = NS(worklist=NS(_item=Mock(return_value=row), current=Mock(return_value=row),
        _check_baseline=Mock(), _slot=Mock(side_effect=lambda action, handle: NS(handle=handle))),
        revision=NS(_row=lambda row: {'identity': row.identity}, _IDENTITY_FIELDS={'identity'},
            validate=Mock(), _modules=Mock(return_value='revision-api'), _restore_current=Mock()),
        target=NS(_validate_capture=Mock()), ua=NS(_set_frame=Mock()), bpy=NS(data=NS(actions=[])))
    plan = dict(key_reduce_api=1, variant_api=1, phase='prepared', context=context, rig=rig,
        item=row, item_id=row.item_id, saved=saved, original=original, source=source, slot=1, source_slot=1,
        api=api, frame=7., row_identity={'identity': 'current'}, source_hash=source.hashes[1],
        hashes={1: original.hashes[1]}, capture={'targets': [{'target': rig, 'action': original,
            'action_fake_user': True, 'snapshot': {'fresh': 'raw-inputs'}}]}, previous=(0, 'before', False),
        errors=dict(position=.001, rotation=.1, scale=.001), frames=[1., 1.25, 1.5],
        before_keys=10, pending_keyed=False, candidates={('bone', 'location'): {'indices': (0, 1, 4, 5)}},
        unaffected='untouched-exact', skipped_groups=2, revision_value=None)
    offset = NS(_variant_owner=Mock(), _TRANSFORMS={'location': 3},
        _bag=Mock(side_effect=lambda action, slot: NS(fcurves=action.curves)),
        _unaffected=Mock(return_value='untouched-exact'),
        _action_hash=Mock(side_effect=lambda api, action, handle: action.hashes[handle]),
        rollback_variant=Mock())
    def stage(_plan):
        _plan.update(staged_action=candidate, phase='staged')
        return candidate
    offset.new_variant_action = Mock(side_effect=stage)
    offset.publish_variant = Mock(return_value={'item_id': 'b'*32, 'action': candidate, 'frame': 7.})
    physics = NS(status=Mock(return_value={'rig': rig, 'valid': True, 'hair': {}, 'dresses': []}))
    return NS(context=context, rig=rig, row=row, saved=saved, plan=plan, offset=offset,
              api=api, physics=physics, original=original, source=source, candidate=candidate)


def actual_variant_owner():
    """Execute the real reused owner helper with only native hashing replaced."""
    tree = ast.parse((ROOT / 'control_pose_offset.py').read_text(encoding='utf8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_variant_owner')
    namespace = dict(_require=reduce._require, _action_hash=lambda api, action, handle: action.hashes[handle])
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(ROOT / 'control_pose_offset.py'), 'exec'), namespace)
    return namespace['_variant_owner']


class HelperContracts(unittest.TestCase):
    def test_analytic_quaternion_floor_includes_segment_interior(self):
        self.assertLessEqual(reduce._quaternion_floor([(1., 0., 0., 0.), (0., 1., 0., 0.)]), math.sqrt(.5))
        self.assertEqual(reduce._quaternion_floor([(1., 0., 0., 0.), (-1., 0., 0., 0.)]), 0.)

    def test_large_finite_float32_raw_quaternion_crossing_has_zero_floor(self):
        # These scalar raw quaternions necessarily pass through zero. A rounded
        # closest-point residual must not be presented as a positive lower bound.
        values = [(9.93431308878125e20, 0., 0., 0.), (-5.0370784470427605e20, 0., 0., 0.)]
        self.assertEqual(reduce._quaternion_floor(values), 0.)
        self.assertEqual(reduce._group_tolerance('rotation_quaternion', values,
            {'position': .001, 'rotation': .1, 'scale': .001}, 1), 0.)

    def test_euler_vector_margin_bounds_sum_of_axis_angles(self):
        error = {'position': .02, 'rotation': 1., 'scale': .01}
        bound = reduce._group_tolerance('rotation_euler', [], error, 2)
        self.assertLessEqual(math.sqrt(3.)*bound, math.radians(1.)/4)

    def test_loop_sides_and_marker_enclosing_knots_are_retained(self):
        times = tuple(map(float, range(8)))
        values = tuple((t, 2*t, 3*t) for t in times)
        before = deepcopy((times, values))
        indices, error = reduce._retained(times, values, .001, [3.5])
        self.assertEqual(indices, (0, 1, 3, 4, 6, 7))
        self.assertLessEqual(error, .001)
        self.assertEqual((times, values), before)

    def test_tolerances_reject_nonfinite_negative_bool_and_excess(self):
        for arguments in ((math.nan, .1, .001), (.001, math.inf, .001),
                          (-.001, .1, .001), (True, .1, .001), (.001, 11., .001),
                          (.001, .1, .11)):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    reduce._tolerances(*arguments)


class AdmissionContracts(unittest.TestCase):
    def test_actual_owner_rejects_stale_inputs_before_candidate_creation(self):
        def other_active(f):
            f.api.worklist.current.return_value = NS(item_id='c'*32)
        cases = {
            'Source selected': lambda f: setattr(f.row, 'side', 'SOURCE'),
            'foreign row rig': lambda f: setattr(f.row, 'rig', object()),
            'replaced Custom': lambda f: setattr(f.row, 'custom_action', Action('foreign')),
            'replaced Source': lambda f: setattr(f.row, 'source_action', Action('foreign')),
            'row slot changed': lambda f: setattr(f.row, 'custom_slot', 2),
            'source slot changed': lambda f: setattr(f.row, 'source_slot', 2),
            'row identity changed': lambda f: setattr(f.row, 'identity', 'stale'),
            'another row selected': other_active,
            'active Action changed': lambda f: setattr(f.rig.animation_data, 'action', Action('foreign')),
            'active slot changed': lambda f: setattr(f.rig.animation_data.action_slot, 'handle', 2),
            'frame changed': lambda f: setattr(f.context.scene, 'frame_subframe', .25),
            'closed session': lambda f: f.plan.update(phase='published'),
            'Custom content changed': lambda f: f.original.hashes.update({1: 'edited'}),
            'Source content changed': lambda f: f.source.hashes.update({1: 'edited'}),
            'Rest capture changed': lambda f: setattr(f.api.target._validate_capture, 'side_effect', ValueError('Rest changed')),
            'Source library changed': lambda f: setattr(f.api.worklist._check_baseline, 'side_effect', ValueError('Source cache changed')),
        }
        owner = actual_variant_owner()
        for label, mutate in cases.items():
            with self.subTest(label=label):
                f = fixture()
                f.offset._variant_owner.side_effect = lambda plan, final=False: owner(plan, final=final)
                mutate(f)
                with patch.object(reduce, '_backend', return_value=(f.offset, f.api, f.physics)):
                    with self.assertRaises(ValueError):
                        reduce.apply(f.context, f.plan)
                f.offset.new_variant_action.assert_not_called()
                f.offset.publish_variant.assert_not_called()
                f.offset.rollback_variant.assert_not_called()

    def test_prepare_skips_unsupported_groups_without_changing_source_curves(self):
        def curve(prop, index):
            return NS(parsed=('bone', prop, index), data_path=prop, array_index=index,
                evaluate=lambda frame: 0., mute=False, lock=False, modifiers=[], sampled_points=[],
                keyframe_points=[NS(co=NS(x=float(i), y=float(i + index)), interpolation='LINEAR') for i in range(7)])
        variants = ('bezier', 'modifier', 'muted', 'locked', 'sampled', 'short',
                    'incomplete', 'different_times', 'axis_angle', 'wrong_rotation_mode')
        for kind in variants:
            with self.subTest(kind=kind):
                f = fixture()
                f.rig.pose = NS(bones={'bone': NS(name='bone', rotation_mode='QUATERNION')})
                f.rig.data = NS(bones=[NS(name='bone', parent=None)])
                f.rig.path_resolve = lambda path: [0., 0., 0., 0.]
                prop = 'rotation_axis_angle' if kind == 'axis_angle' else 'rotation_euler' if kind == 'wrong_rotation_mode' else 'scale'
                unsupported = [curve(prop, i) for i in range(4 if kind == 'axis_angle' else 3)]
                if kind == 'bezier': unsupported[0].keyframe_points[2].interpolation = 'BEZIER'
                if kind == 'modifier': unsupported[0].modifiers = ['owned modifier']
                if kind == 'muted': unsupported[0].mute = True
                if kind == 'locked': unsupported[0].lock = True
                if kind == 'sampled': unsupported[0].sampled_points = ['sample']
                if kind == 'short': unsupported[0].keyframe_points = unsupported[0].keyframe_points[:4]
                if kind == 'incomplete': unsupported.pop()
                if kind == 'different_times': unsupported[0].keyframe_points[2].co.x += .25
                f.original.curves = [curve('location', i) for i in range(3)] + unsupported
                before = deepcopy([[(p.co.x, p.co.y, p.interpolation) for p in c.keyframe_points] for c in f.original.curves])
                f.offset._TRANSFORMS = {'location': 3, 'scale': 3, 'rotation_euler': 3, 'rotation_quaternion': 4}
                f.offset._curve_name = lambda c: c.parsed
                f.offset.prepare_variant = Mock(return_value=f.plan)
                f.offset._native_fk = Mock()
                f.api.revision._validate_paths = Mock()
                with patch.object(reduce, '_backend', return_value=(f.offset, f.api, f.physics)):
                    plan = reduce.prepare(f.context)
                self.assertEqual(set(plan['candidates']), {('bone', 'location')})
                self.assertEqual(plan['skipped_groups'], 1)
                self.assertEqual(before, [[(p.co.x, p.co.y, p.interpolation) for p in c.keyframe_points] for c in f.original.curves])
                f.offset.new_variant_action.assert_not_called()
                f.offset.publish_variant.assert_not_called()

    def test_wrong_context_refuses_before_backend(self):
        f = fixture()
        with patch.object(reduce, '_backend') as backend:
            with self.assertRaises(ValueError):
                reduce.apply(NS(), f.plan)
        backend.assert_not_called()

    def test_physics_running_or_recovery_refuses_without_mutation(self):
        for state in ({'hair': {'running': True}}, {'hair': {'pending_recovery': True}},
                      {'dresses': [{'running': True}]}, {'dresses': [{'pending_recovery': True}]},
                      {'valid': False}):
            with self.subTest(state=state):
                f = fixture()
                f.physics.status.return_value.update(state)
                with patch.object(reduce, '_backend', return_value=(f.offset, f.api, f.physics)):
                    with self.assertRaises(ValueError):
                        reduce.apply(f.context, f.plan)
                f.offset.new_variant_action.assert_not_called()


class WriteAndPublicationContracts(unittest.TestCase):
    def setUp(self):
        preserved = patch.dict(reduce._PRESERVED_POSES, {}, clear=True)
        preserved.start(); self.addCleanup(preserved.stop)

    def execute(self, f, **replacements):
        defaults = dict(_write=Mock(), _verify=Mock(return_value={'position': 0., 'rotation': 0., 'scale': 0.}))
        defaults.update(replacements)
        with patch.object(reduce, '_backend', return_value=(f.offset, f.api, f.physics)), patch.multiple(reduce, **defaults):
            return reduce.apply(f.context, f.plan)

    def test_partial_staging_failure_rolls_back_owned_action(self):
        f = fixture()
        def fail_after_copy(plan):
            plan.update(staged_action=f.candidate, phase='staged')
            raise ValueError('Copied Action fingerprint failed')
        f.offset.new_variant_action.side_effect = fail_after_copy
        with self.assertRaisesRegex(ValueError, 'fingerprint failed'):
            self.execute(f)
        f.offset.rollback_variant.assert_called_once_with(f.plan)
        f.offset.publish_variant.assert_not_called()

    def test_untouched_curve_mismatch_refuses_before_native_or_publication(self):
        f = fixture()
        f.offset._unaffected.return_value = 'unexpected-change'
        verify = Mock()
        with self.assertRaisesRegex(ValueError, 'untouched curve changed'):
            self.execute(f, _verify=verify)
        verify.assert_not_called()
        f.offset.publish_variant.assert_not_called()
        f.offset.rollback_variant.assert_called_once_with(f.plan)

    def test_native_quality_refusal_rolls_back_without_publication(self):
        f = fixture()
        with self.assertRaisesRegex(ValueError, 'native quality refusal'):
            self.execute(f, _verify=Mock(side_effect=ValueError('native quality refusal')))
        f.offset.publish_variant.assert_not_called()
        f.offset.rollback_variant.assert_called_once_with(f.plan)

    def test_owned_recovery_failure_is_explicit(self):
        f = fixture()
        f.offset.rollback_variant.side_effect = RuntimeError('raw restore refused')
        with self.assertRaisesRegex(RuntimeError, 'owned recovery was kept: raw restore refused'):
            self.execute(f, _write=Mock(side_effect=ValueError('write fault')))

    def test_postpublication_fault_uses_owned_rollback(self):
        f = fixture()
        def fault(phase):
            if phase == 'after_publication':
                raise RuntimeError('after publication fault')
        with self.assertRaisesRegex(RuntimeError, 'after publication fault'):
            self.execute(f, _fault=fault)
        f.offset.publish_variant.assert_called_once()
        f.offset.rollback_variant.assert_called_once_with(f.plan)
        self.assertNotIn(reduce.RECORD, f.candidate)

    def test_record_explicitly_limits_world_verification_to_samples(self):
        f = fixture()
        result = self.execute(f)
        record = json.loads(f.candidate[reduce.RECORD])
        self.assertFalse(record['continuous_world_error_proven'])
        self.assertEqual(record['native_sample_count'], len(f.plan['frames']))
        self.assertEqual((record['before_keys'], record['after_keys']), (10, 6))
        self.assertIs(result['action'], f.candidate)
        self.assertNotIn(reduce.RECORD, f.original)
        self.assertNotIn(reduce.RECORD, f.source)

    def test_write_preserves_unsupported_curve_exactly_and_authored_retained_values(self):
        class Points(list):
            def remove(self, point, *, fast=False):
                super().remove(point)
        def curve(path, index, interpolation='LINEAR'):
            return NS(data_path=path, array_index=index, keyframe_points=Points([
                NS(co=(i/4., i*3. + index), interpolation=interpolation, tag=('raw', i)) for i in range(7)]),
                update=Mock(), modifiers=[], marker='preserved')
        channels = [curve('location', i) for i in range(3)]
        unsupported = curve('custom-property', 0, 'BEZIER')
        action = NS(curves=channels + [unsupported])
        before = deepcopy(unsupported.keyframe_points)
        offset = NS(_bag=lambda action, slot: NS(fcurves=action.curves),
                    _curve_name=lambda curve: ('bone', 'location', curve.array_index) if curve.data_path == 'location' else None)
        plan = {'slot': 1, 'candidates': {('bone', 'location'): {'indices': (0, 1, 5, 6)}}}
        with patch.object(reduce, '_backend', return_value=(offset, None, None)):
            reduce._write(plan, action)
        self.assertEqual(unsupported.keyframe_points, before)
        unsupported.update.assert_not_called()
        for index, channel in enumerate(channels):
            self.assertEqual([point.co for point in channel.keyframe_points],
                             [(i/4., i*3. + index) for i in (0, 1, 5, 6)])


class RestoreAndComparisonContracts(unittest.TestCase):
    def setUp(self):
        preserved = patch.dict(reduce._PRESERVED_POSES, {}, clear=True)
        preserved.start(); self.addCleanup(preserved.stop)

    def test_restore_with_action_changes_only_detached_binding_view(self):
        f = fixture()
        original_entry = f.plan['capture']['targets'][0]
        original_snapshot = dict(original_entry['snapshot'])
        reduce._restore_with_action(f.context, f.plan, f.candidate)
        execution = f.api.revision._restore_current.call_args.args[2]
        self.assertIs(execution['targets'][0]['action'], f.candidate)
        self.assertEqual(execution['targets'][0]['snapshot']['slot'], 1)
        self.assertIs(original_entry['action'], f.original)
        self.assertEqual(original_entry['snapshot'], original_snapshot)

    def test_verify_always_restores_fresh_capture_after_evaluation_failure(self):
        f = fixture()
        error = ValueError('native evaluation failed')
        with patch.object(reduce, '_native_pose', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'native evaluation failed'):
                reduce._verify(f.context, f.plan, f.candidate)
        f.api.revision._restore_current.assert_called_once_with(
            f.context, f.rig, f.plan['capture'], 'revision-api')

    def test_native_pose_refuses_nonfinite_determinant_before_decomposition(self):
        class Matrix:
            def __iter__(self):
                return iter(((1., 0., 0., 0.), (0., 1., 0., 0.),
                             (0., 0., 1., 0.), (0., 0., 0., 1.)))
            def determinant(self): return math.inf
            def __matmul__(self, other): return self
            def to_quaternion(self): raise AssertionError('Invalid determinant must stop before decomposition')
        rig = NS(matrix_world=Matrix(), pose=NS(bones=[NS(matrix=object(), head=object(), tail=object())]))
        with self.assertRaises(ValueError):
            reduce._native_pose(NS(), rig)

    def test_each_native_sample_quality_metric_can_refuse_and_exact_identity_passes_zero(self):
        class Vector:
            def __init__(self, *values): self.values = values
            def __iter__(self): return iter(self.values)
            def __sub__(self, other): return Vector(*(a-b for a, b in zip(self.values, other.values)))
            @property
            def length(self): return math.hypot(*self.values)
        class Rotation:
            def __init__(self, degrees): self.degrees = degrees
            def normalized(self): return self
            def conjugated(self): return Rotation(-self.degrees)
            def __matmul__(self, other):
                half = math.radians(self.degrees + other.degrees)/2
                return NS(x=math.sin(half), y=0., z=0., w=math.cos(half))
        baseline = (Vector(0., 0., 0.), Vector(0., 1., 0.), Rotation(0.), Vector(1., 1., 1.))
        changed = {
            'position': (Vector(.01, 0., 0.), baseline[1], baseline[2], baseline[3]),
            'rotation': (baseline[0], baseline[1], Rotation(2.), baseline[3]),
            'scale': (baseline[0], baseline[1], baseline[2], Vector(1.02, 1.02, 1.02)),
        }
        for field, sample in changed.items():
            with self.subTest(field=field):
                f = fixture(); f.plan['frames'] = [1.]
                with patch.object(reduce, '_native_pose', side_effect=[[baseline], [sample]]):
                    with self.assertRaisesRegex(ValueError, 'pose check exceeded'):
                        reduce._verify(f.context, f.plan, f.candidate)
                f.api.revision._restore_current.assert_called_once()
        f = fixture(); f.plan.update(frames=[1.], errors={'position': 0., 'rotation': 0., 'scale': 0.})
        with patch.object(reduce, '_native_pose', side_effect=[[baseline], [baseline]]):
            self.assertEqual(reduce._verify(f.context, f.plan, f.candidate), f.plan['errors'])
        f.api.revision._restore_current.assert_called_once()
        f = fixture(); f.plan['frames'] = [1.]
        invalid = (Vector(math.nan, 0., 0.), baseline[1], baseline[2], baseline[3])
        with patch.object(reduce, '_native_pose', side_effect=[[baseline], [invalid]]):
            with self.assertRaisesRegex(ValueError, 'non-finite'):
                reduce._verify(f.context, f.plan, f.candidate)
        f.api.revision._restore_current.assert_called_once()

    def test_stale_before_action_content_blocks_compare_before_binding(self):
        f = fixture()
        after = NS(item_id='b'*32, rig=f.rig, custom_action=f.candidate, custom_slot=1,
                   source_action=f.source, source_slot=1)
        data = dict(before_hashes={'1': 'stale-hash'}, after_hashes={'1': f.candidate.hashes[1]},
                    source_hash=f.source.hashes[1], source_slot=1)
        f.offset.prepare_variant = Mock(return_value=f.plan)
        binding = f.rig.animation_data.action, f.rig.animation_data.action_slot
        with patch.object(reduce, '_pair', return_value=(f.row, after, data)), \
             patch.object(reduce, '_backend', return_value=(f.offset, f.api, f.physics)):
            with self.assertRaisesRegex(ValueError, 'Action was edited'):
                reduce.compare(f.context, original=True)
        self.assertEqual((f.rig.animation_data.action, f.rig.animation_data.action_slot), binding)

    def test_compare_binding_fault_restores_capture_and_list_state(self):
        f = fixture()
        after = NS(item_id='b'*32, rig=f.rig, custom_action=f.candidate, custom_slot=1,
                   source_action=f.source, source_slot=1)
        f.saved.items.append(after)
        data = dict(before_hashes={'1': f.original.hashes[1]}, after_hashes={'1': f.candidate.hashes[1]},
                    source_hash=f.source.hashes[1], source_slot=1)
        f.offset.prepare_variant = Mock(return_value=f.plan)
        with patch.object(reduce, '_pair', return_value=(f.row, after, data)), \
             patch.object(reduce, '_backend', return_value=(f.offset, f.api, f.physics)), \
             patch.object(reduce, '_fault', side_effect=RuntimeError('compare binding fault')):
            with self.assertRaisesRegex(RuntimeError, 'compare binding fault'):
                reduce.compare(f.context, original=False)
        f.api.revision._restore_current.assert_called_once()
        self.assertEqual((f.saved.active_index, f.saved.status, f.saved.has_error), f.plan['previous'])

    def test_compare_refuses_unkeyed_pose_edits_without_changing_binding(self):
        f = fixture()
        after = NS(item_id='b'*32, rig=f.rig, custom_action=f.candidate, custom_slot=1,
                   source_action=f.source, source_slot=1)
        f.rig.path_resolve = lambda path: [.25]
        f.offset.prepare_variant = Mock(return_value=f.plan)
        before = f.rig.animation_data.action, f.saved.active_index
        with patch.object(reduce, '_pair', return_value=(f.row, after, {})), \
             patch.object(reduce, '_backend', return_value=(f.offset, f.api, f.physics)):
            with self.assertRaisesRegex(ValueError, 'pending pose edits'):
                reduce.compare(f.context, original=False)
        self.assertEqual((f.rig.animation_data.action, f.saved.active_index), before)
        f.api.revision._restore_current.assert_not_called()

    def test_known_preserved_source_evaluation_can_compare_but_changed_stamp_cannot(self):
        for actual, pending, allowed in ((.25, False, True), (.5, False, False), (.25, True, False)):
            with self.subTest(actual=actual, pending=pending):
                f = fixture()
                after = NS(item_id='b'*32, rig=f.rig, custom_action=f.candidate, custom_slot=1,
                           source_action=f.source, source_slot=1)
                f.saved.items.append(after)
                f.api.worklist.current.return_value = after
                f.rig.animation_data.action = f.candidate
                f.rig.path_resolve = lambda path: [actual]
                f.offset.prepare_variant = Mock(return_value=f.plan)
                data = dict(before_hashes={'1': f.original.hashes[1]}, after_hashes={'1': f.candidate.hashes[1]},
                            source_hash=f.source.hashes[1], source_slot=1)
                reduce._PRESERVED_POSES[f.rig.as_pointer()] = dict(item_id=after.item_id,
                    frame=7., pending=pending, values=(('pose.channel', 0, .25),))
                with patch.object(reduce, '_pair', return_value=(f.row, after, data)), \
                     patch.object(reduce, '_backend', return_value=(f.offset, f.api, f.physics)):
                    if allowed:
                        self.assertIs(reduce.compare(f.context, original=True), f.original)
                    else:
                        with self.assertRaisesRegex(ValueError, 'pending pose edits'):
                            reduce.compare(f.context, original=True)
                        self.assertIs(f.rig.animation_data.action, f.candidate)

    def test_status_reads_pair_without_backend_or_action_fingerprint(self):
        f = fixture()
        after = NS(item_id='b'*32, rig=f.rig, custom_action=f.candidate, custom_slot=1,
                   source_action=f.source, source_slot=1)
        f.saved.items.append(after)
        f.candidate[reduce.RECORD] = json.dumps(dict(schema=reduce.SCHEMA, before_item=f.row.item_id,
            after_item=after.item_id, before_keys=10, after_keys=6, slot=1))
        worklist = ModuleType(PACKAGE + '.animation_worklist')
        worklist.state = lambda context: f.saved
        worklist.current = lambda context: f.row
        with patch.dict(sys.modules, {worklist.__name__: worklist}), \
             patch.object(package, 'animation_worklist', worklist, create=True), \
             patch.object(reduce, '_backend', side_effect=AssertionError('No drawing-time scan')):
            status = reduce.status(f.context)
        self.assertTrue(status['active_before'])
        self.assertEqual((status['before_keys'], status['after_keys']), (10, 6))


if __name__ == '__main__':
    unittest.main()
