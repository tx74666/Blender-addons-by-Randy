"""Lightweight content/dependency proof checks without bpy or a native worker."""

import copy
import importlib.util
from pathlib import Path
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer/animation_worklist_fingerprint.py'
spec = importlib.util.spec_from_file_location('_cd_worklist_fingerprint_test', SOURCE)
fingerprints = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fingerprints)


class RNA(dict):
    """Small read-observable RNA facade, never emulating Blender evaluation."""

    def __init__(self, *, custom=None, kinds=None, **fields):
        super().__init__(custom or {})
        self.__dict__.update(fields)
        kinds = kinds or {}
        props = []
        for name, value in fields.items():
            kind = kinds.get(name)
            if kind is None:
                kind = ('BOOLEAN' if type(value) is bool else 'INT' if type(value) is int
                        else 'FLOAT' if type(value) is float else 'STRING' if type(value) is str
                        else 'FLOAT' if isinstance(value, tuple) else 'POINTER')
            array = isinstance(value, tuple) and kind in {'BOOLEAN', 'INT', 'FLOAT'}
            dimensions = (len(value), len(value[0]), 0) if array and value and isinstance(value[0], tuple) else ()
            props.append(SimpleNamespace(identifier=name, type=kind, is_array=array,
                                         array_dimensions=dimensions, is_readonly=False))
        self.bl_rna = SimpleNamespace(properties=props)


def key(frame, value):
    return RNA(co=(float(frame), float(value)), handle_left=(frame - .2, value - .1),
               handle_right=(frame + .2, value + .1), interpolation='BEZIER',
               handle_left_type='FREE', handle_right_type='FREE', type='KEYFRAME',
               easing='AUTO', amplitude=.8, back=.7, period=.6,
               select_control_point=False, select_left_handle=False, select_right_handle=False)


def curve(path, index=0):
    return RNA(data_path=path, array_index=index, mute=False, extrapolation='CONSTANT',
               auto_smoothing='NONE', keyframe_points=[key(1., .1), key(3., .2)],
               sampled_points=[], modifiers=[], driver=None, group=None, select=False,
               hide=False, lock=False, color=(.1, .2, .3), color_mode='AUTO_RGB',
               kinds={'keyframe_points': 'COLLECTION', 'sampled_points': 'COLLECTION', 'modifiers': 'COLLECTION'})


def action(curves=None, handle=4):
    if curves is None:
        curves = [curve('pose.bones["Hip"].' + field, i)
                  for field, count in (('location', 3), ('rotation_quaternion', 4), ('scale', 3))
                  for i in range(count)]
    bag = SimpleNamespace(slot_handle=handle, fcurves=curves)
    strip = RNA(type='KEYFRAME', frame_start=1., frame_end=3., channelbags=[bag],
                kinds={'channelbags': 'COLLECTION'})
    layer = RNA(name='Artist layer', influence=1., mix_mode='REPLACE', strips=[strip],
                kinds={'strips': 'COLLECTION'})
    result = RNA(name='Artist Custom', library=None, is_action_layered=True,
                 is_action_legacy=False, slots=[SimpleNamespace(handle=handle, target_id_type='OBJECT')],
                 layers=[layer], frame_range=(1., 3.), use_frame_range=False,
                 kinds={'slots': 'COLLECTION', 'layers': 'COLLECTION'})
    return result


def rig(active):
    rest = RNA(name='Hip', parent=None, matrix_local=((1., 0., 0., 0.), (0., 1., 0., 0.),
               (0., 0., 1., 0.), (0., 0., 0., 1.)), use_deform=True,
               inherit_scale='FULL', use_local_location=True, use_inherit_rotation=True)
    pose = RNA(name='Hip', rotation_mode='QUATERNION', location=(0., 0., 0.),
               rotation_quaternion=(1., 0., 0., 0.), scale=(1., 1., 1.), constraints=[],
               kinds={'constraints': 'COLLECTION'})
    pose.path_from_id = lambda: 'pose.bones["Hip"]'
    data = RNA(library=None, pose_position='POSE', animation_data=None, bones=[rest],
               kinds={'bones': 'COLLECTION'})
    ad = SimpleNamespace(action=active, action_slot=active.slots[0], drivers=[],
                         use_tweak_mode=False, use_nla=False, nla_tracks=[])
    return SimpleNamespace(type='ARMATURE', mode='OBJECT', library=None, parent=None,
                           data=data, pose=SimpleNamespace(bones=[pose]), animation_data=ad,
                           get=lambda field: None,
                           constraints=[], modifiers=[], rotation_mode='QUATERNION',
                           location=(0., 0., 0.), rotation_quaternion=(1., 0., 0., 0.),
                           scale=(1., 1., 1.), delta_location=(0., 0., 0.),
                           delta_rotation_quaternion=(1., 0., 0., 0.), delta_scale=(1., 1., 1.))


def scene():
    return SimpleNamespace(render=SimpleNamespace(fps=60, fps_base=1.),
                           unit_settings=SimpleNamespace(scale_length=1.))


def timing():
    return dict(frame_start=1., frame_end=3., fps=60, fps_base=1., sample_rate=60., unit_scale=1., loop=False)


class LazyKeys:
    """Generate a native-sized key sequence without a preallocated payload."""
    def __init__(self, count):
        self.count = count
        self.last_value = .2
        self.visited = 0

    def __len__(self):
        return self.count

    def __iter__(self):
        for index in range(self.count):
            self.visited += 1
            yield key(index + 1., self.last_value if index == self.count - 1 else .1)


class MetadataProbe:
    def __init__(self, prop, reads):
        self.values = vars(prop).copy()
        self.reads = reads

    def __getattr__(self, name):
        self.reads[name] = self.reads.get(name, 0) + 1
        return self.values[name]


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.action = action()
        self.rig = rig(self.action)
        self.scene = scene()

    def check(self, action_arg=None, **kwargs):
        return fingerprints.fingerprint_native(action_arg or self.action, self.rig, self.scene, 4, **kwargs)

    def test_known_plain_fk_and_pure_roundtrip(self):
        payload = fingerprints.collect_native_payload(self.action, self.rig, self.scene, 4)
        result = self.check()
        self.assertTrue(result['fingerprint_known'], result['proof_reason'])
        self.assertEqual(result, fingerprints.fingerprint_serialized(copy.deepcopy(payload)))
        self.assertEqual(len(result['fingerprint']), 64)

    def test_streamed_action_bytes_match_complete_canonical_payload(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        first.sampled_points.append(RNA(co=(2., .6), select=False))
        first.modifiers.append(RNA(type='ENVELOPE', control_points=[RNA(frame=2., min=-.2, max=.5)],
                                   kinds={'control_points': 'COLLECTION'}))
        first.group = RNA(name='Artist group', mute=False)
        payload = fingerprints.collect_action_payload(self.action, 4)
        expected = fingerprints._digest({'schema': fingerprints.SCHEMA, 'action': payload})
        self.assertEqual(fingerprints.fingerprint_action(self.action, 4)['action_fingerprint'], expected)
        self.assertEqual(fingerprints.fingerprint_serialized(
            fingerprints.collect_native_payload(self.action, self.rig, self.scene, 4)), self.check())

    def test_native_full_proof_reuses_action_hash_without_collecting_payload_again(self):
        with (patch.object(fingerprints, 'collect_action_payload', side_effect=AssertionError('Action copied')),
             patch.object(fingerprints, 'collect_native_payload', side_effect=AssertionError('Action recollected')),
              patch.object(fingerprints, 'fingerprint_action', wraps=fingerprints.fingerprint_action) as motion):
            result = self.check()
        self.assertTrue(result['fingerprint_known'], result['proof_reason'])
        self.assertEqual(motion.call_count, 1)

    def test_metadata_plan_reused_within_hash_and_reset_for_next_hash(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        baseline = fingerprints.fingerprint_action(self.action, 4)
        reads = {}
        descriptor = SimpleNamespace(properties=[MetadataProbe(prop, reads)
            for prop in first.keyframe_points[0].bl_rna.properties], as_pointer=lambda: 991001)
        for point in first.keyframe_points:
            point.bl_rna = descriptor
        once = fingerprints.fingerprint_action(self.action, 4)
        self.assertEqual(once, baseline)
        per_plan = reads['identifier']
        self.assertEqual(per_plan, len(descriptor.properties))
        self.assertEqual(reads['type'], len(descriptor.properties) - 3)  # Three UI-only fields.
        self.assertEqual(fingerprints.fingerprint_action(self.action, 4), baseline)
        self.assertEqual(reads['identifier'], per_plan * 2)
        # The same descriptor identity may gain new RNA between operations.
        # A fresh operation must compile it again rather than trust old metadata.
        descriptor.properties.append(MetadataProbe(SimpleNamespace(identifier='future_target',
            type='POINTER', is_array=False, array_dimensions=()), reads))
        for point in first.keyframe_points:
            point.future_target = object()
        unknown = fingerprints.fingerprint_action(self.action, 4)
        self.assertFalse(unknown['fingerprint_known'])
        self.assertIn('future_target', unknown['proof_reason'])

    def test_metadata_cache_keeps_current_values_and_exclusion_plans_separate(self):
        point = key(1., .1)
        descriptor = SimpleNamespace(properties=point.bl_rna.properties, as_pointer=lambda: 991002)
        point.bl_rna = descriptor
        cache = {}
        first = fingerprints._rna_settings(point, exclude=fingerprints._KEY_UI, schema_cache=cache)
        point.co = (1., .7)
        second = fingerprints._rna_settings(point, exclude=fingerprints._KEY_UI, schema_cache=cache)
        self.assertEqual(second['co'], [1., .7])
        self.assertNotEqual(first, second)
        included_ui = fingerprints._rna_settings(point, schema_cache=cache)
        self.assertIn('select_control_point', included_ui)
        self.assertNotIn('select_control_point', first)
        self.assertEqual(len(cache), 2)

    def test_unreliable_schema_identity_falls_back_to_uncached_metadata(self):
        for identity in (None, False, 0, 'unproven', RuntimeError('Unavailable pointer')):
            with self.subTest(identity=identity):
                point = key(1., .1)
                reads = {}
                properties = [MetadataProbe(prop, reads) for prop in point.bl_rna.properties]
                def pointer():
                    if isinstance(identity, Exception):
                        raise identity
                    return identity
                point.bl_rna = SimpleNamespace(properties=properties, as_pointer=pointer)
                cache = {}
                first = fingerprints._rna_settings(point, exclude=fingerprints._KEY_UI, schema_cache=cache)
                second = fingerprints._rna_settings(point, exclude=fingerprints._KEY_UI, schema_cache=cache)
                self.assertEqual(first, second)
                self.assertEqual(reads['identifier'], len(properties) * 2)
                self.assertEqual(cache, {})

    def test_large_action_exceeds_old_aggregate_budget_and_streams_with_small_peak(self):
        curves = [curve('pose.bones["Hip"].location', index) for index in range(3)]
        lazy = [LazyKeys(19_000) for _ in curves]
        for item, points in zip(curves, lazy):
            item.keyframe_points = points
        candidate = action(curves)
        counter = [fingerprints.MAX_ITEMS]
        fingerprints._plain(fingerprints._rna_settings(key(1., .1), exclude=fingerprints._KEY_UI), budget=counter)
        nodes_per_key = fingerprints.MAX_ITEMS - counter[0]
        self.assertGreater(nodes_per_key * sum(len(points) for points in lazy), fingerprints.MAX_ITEMS)
        tracemalloc.start()
        try:
            with patch.object(fingerprints, 'collect_action_payload', side_effect=AssertionError('Action copied')):
                before = fingerprints.fingerprint_action(candidate, 4)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        type(self).large_action_peak_bytes = peak
        self.assertTrue(before['fingerprint_known'], before['proof_reason'])
        self.assertEqual([points.visited for points in lazy], [19_000] * 3)
        self.assertLess(peak, 8 * 1024 * 1024, 'The streamed hash retained an Action-sized allocation.')
        lazy[-1].last_value = .7
        after = fingerprints.fingerprint_action(candidate, 4)
        self.assertTrue(after['fingerprint_known'], after['proof_reason'])
        self.assertNotEqual(before['action_fingerprint'], after['action_fingerprint'])

    def test_total_point_cap_checks_actual_items_before_visiting_native_keys(self):
        curves = [curve('pose.bones["Hip"].location', index) for index in range(3)]
        points = [LazyKeys(12) for _ in curves]
        for item, keys in zip(curves, points):
            item.keyframe_points = keys
        with patch.object(fingerprints, 'MAX_ACTION_POINTS', 30):
            result = fingerprints.fingerprint_action(action(curves), 4)
        self.assertFalse(result['fingerprint_known'])
        self.assertIn('total key/sample point bound', result['proof_reason'])
        self.assertEqual([keys.visited for keys in points], [0, 0, 0])
        payload = fingerprints.collect_native_payload(self.action, self.rig, self.scene, 4)
        source = payload['action']['layers'][0]['strips'][0]['curves'][0]
        source['keyframe_points'] = [source['keyframe_points'][0]] * fingerprints.MAX_CURVE_POINTS
        # Shared references make this >1,000,000-point attestation cheap to
        # construct. The preflight must reject before copying/traversing keys.
        payload['action']['layers'][0]['strips'][0]['curves'] = [source] * 51
        with patch.object(fingerprints, '_stream_digest', side_effect=AssertionError('Oversized Action streamed')):
            result = fingerprints.fingerprint_serialized(payload)
        self.assertFalse(result['fingerprint_known'])
        self.assertIn('total key/sample point bound', result['proof_reason'])

    def test_curve_point_bound_rejects_before_reading_native_or_pure_points(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        for field in ('keyframe_points', 'sampled_points'):
            with self.subTest(field=field):
                previous = getattr(first, field)
                oversized = LazyKeys(fingerprints.MAX_CURVE_POINTS + 1)
                setattr(first, field, oversized)
                result = fingerprints.fingerprint_action(self.action, 4)
                self.assertFalse(result['fingerprint_known'])
                self.assertEqual(oversized.visited, 0)
                self.assertIn('bound', result['proof_reason'])
                setattr(first, field, previous)
        payload = fingerprints.collect_native_payload(self.action, self.rig, self.scene, 4)
        points = payload['action']['layers'][0]['strips'][0]['curves'][0]['keyframe_points']
        points[:] = [points[0]] * (fingerprints.MAX_CURVE_POINTS + 1)
        self.assertFalse(fingerprints.fingerprint_serialized(payload)['fingerprint_known'])

    def test_layer_curve_modifier_and_settings_chunk_bounds_remain_finite(self):
        bag = self.action.layers[0].strips[0].channelbags[0]
        first = bag.fcurves[0]
        examples = [(self.action, 'layers', [self.action.layers[0]] * (fingerprints.MAX_LAYERS + 1)),
                    (bag, 'fcurves', [first] * (fingerprints.MAX_CURVES + 1)),
                    (first, 'modifiers', [RNA(type='NOISE')] * (fingerprints.MAX_MODIFIERS + 1))]
        for block, field, oversized in examples:
            with self.subTest(field=field):
                old = getattr(block, field)
                setattr(block, field, oversized)
                result = fingerprints.fingerprint_action(self.action, 4)
                self.assertFalse(result['fingerprint_known'])
                self.assertIn('bound', result['proof_reason'])
                setattr(block, field, old)
        with patch.object(fingerprints, 'MAX_ITEMS', 12):
            self.assertFalse(fingerprints.fingerprint_action(self.action, 4)['fingerprint_known'])

    def test_streaming_policy_rejects_old_dependency_proof_and_unknown_extra_fields(self):
        payload = fingerprints.collect_native_payload(self.action, self.rig, self.scene, 4)
        payload['proof']['policy'] = 'plain-fk-slot-v1'
        self.assertFalse(fingerprints.fingerprint_serialized(payload)['fingerprint_known'])
        payload['proof']['policy'] = fingerprints.PROOF_POLICY
        payload['external'] = object()
        self.assertFalse(fingerprints.fingerprint_serialized(payload)['fingerprint_known'])

    def test_action_rename_slot_handle_and_ui_selection_do_not_change_motion(self):
        before = fingerprints.fingerprint_action(self.action, 4)
        self.action.name = 'Renamed by artist'
        self.action.layers[0].name = 'Another label'
        self.action.slots[0].handle = 91
        bag = self.action.layers[0].strips[0].channelbags[0]
        bag.slot_handle = 91
        bag.fcurves[0].select = True
        bag.fcurves[0].keyframe_points[0].select_control_point = True
        self.assertEqual(before, fingerprints.fingerprint_action(self.action, 91))

    def test_linked_source_provenance_does_not_change_action_content(self):
        source = copy.deepcopy(self.action)
        source.bl_rna.properties.extend([
            SimpleNamespace(identifier=field, type='BOOLEAN', is_array=False,
                            array_dimensions=(), is_readonly=True)
            for field in ('is_library_indirect', 'is_linked_packed')])
        source.is_library_indirect, source.is_linked_packed = True, True
        source.library = object()
        local = copy.deepcopy(source)
        local.is_library_indirect, local.is_linked_packed, local.library = False, False, None
        self.assertEqual(fingerprints.fingerprint_action(source, 4),
                         fingerprints.fingerprint_action(local, 4))

    def test_non_leaf_pose_child_pointer_uses_recorded_rest_hierarchy(self):
        pose = self.rig.pose.bones[0]
        pose.bl_rna.properties.append(SimpleNamespace(identifier='child', type='POINTER',
            is_array=False, array_dimensions=(), is_readonly=True))
        pose.child = object()  # Native child is a redundant relationship pointer.
        baseline = self.check()
        self.assertTrue(baseline['fingerprint_known'], baseline['proof_reason'])
        rest = self.rig.data.bones[0]
        parent = copy.deepcopy(rest)
        parent.name = 'Root'
        parent.parent = None
        self.rig.data.bones.append(parent)
        rest.parent = parent
        self.assertNotEqual(baseline['fingerprint'], self.check()['fingerprint'])

    def test_native_selection_and_display_settings_do_not_false_change(self):
        blocks = [(self.rig.pose.bones[0], {'select': False, 'hide': False}),
                  (self.rig.data.bones[0], {'hide': False, 'hide_select': False}),
                  (self.rig.data, {'show_bone_colors': True, 'relation_line_position': 'TAIL',
                                   'use_mirror_x': False})]
        for block, fields in blocks:
            for field, value in fields.items():
                setattr(block, field, value)
                block.bl_rna.properties.append(SimpleNamespace(identifier=field,
                    type='BOOLEAN' if type(value) is bool else 'ENUM', is_array=False,
                    array_dimensions=(), is_readonly=False))
        before = self.check()
        for block, fields in blocks:
            for field, value in fields.items():
                setattr(block, field, not value if type(value) is bool else 'HEAD')
        self.assertEqual(before, self.check())

    def test_muted_group_cannot_hide_unkeyed_snapshot_changes(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        first.group = RNA(name='Muted group', mute=True)
        before = self.check()
        self.assertTrue(before['fingerprint_known'], before['proof_reason'])
        self.rig.pose.bones[0].location = (.7, 0., 0.)
        self.assertNotEqual(before['fingerprint'], self.check()['fingerprint'])

    def test_invalid_fcurve_is_unknown_even_for_a_valid_path(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        first.is_valid = False
        result = self.check()
        self.assertFalse(result['fingerprint_known'])
        self.assertEqual(result['fingerprint'], '')
        self.assertIn('invalid FCurve', result['proof_reason'])

    def test_accessory_export_ownership_is_part_of_full_dependencies(self):
        before = self.check()
        for field in ('character_designer_skirt_owner', 'character_designer_hair_bones_owner',
                      'character_designer_hair_variant_version'):
            with self.subTest(field=field):
                self.rig.get = lambda requested, field=field: 'owned' if requested == field else None
                result = self.check()
                self.assertNotEqual(before['fingerprint'], result['fingerprint'])
                self.assertEqual(before['action_fingerprint'], result['action_fingerprint'])

    def test_every_key_setting_and_sample_point_changes_motion(self):
        point = self.action.layers[0].strips[0].channelbags[0].fcurves[0].keyframe_points[0]
        before = fingerprints.fingerprint_action(self.action, 4)['action_fingerprint']
        changes = {'co': (1., .15), 'handle_left': (.7, .0), 'handle_right': (1.4, .3),
                   'interpolation': 'SINE', 'handle_left_type': 'VECTOR', 'handle_right_type': 'AUTO',
                   'type': 'BREAKDOWN', 'easing': 'EASE_IN', 'amplitude': 2., 'back': 2., 'period': 2.}
        for field, value in changes.items():
            with self.subTest(field=field):
                old = getattr(point, field)
                setattr(point, field, value)
                self.assertNotEqual(before, fingerprints.fingerprint_action(self.action, 4)['action_fingerprint'])
                setattr(point, field, old)
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        first.sampled_points.append(RNA(co=(2., .3), select=False))
        self.assertNotEqual(before, fingerprints.fingerprint_action(self.action, 4)['action_fingerprint'])

    def test_all_modifier_settings_and_nested_control_points_are_included(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        envelope = RNA(type='ENVELOPE', mute=False, influence=.4, use_influence=True,
                       control_points=[RNA(frame=1., min=-.5, max=.8)],
                       kinds={'control_points': 'COLLECTION'})
        first.modifiers.append(envelope)
        before = fingerprints.fingerprint_action(self.action, 4)['action_fingerprint']
        envelope.control_points[0].max = .9
        self.assertNotEqual(before, fingerprints.fingerprint_action(self.action, 4)['action_fingerprint'])
        envelope.control_points[0].max = .8
        envelope.influence = .5
        self.assertNotEqual(before, fingerprints.fingerprint_action(self.action, 4)['action_fingerprint'])

    def test_unproven_modifier_pointer_and_driver_are_unknown(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        first.modifiers.append(RNA(type='FUTURE_MODIFIER', target=object()))
        result = self.check()
        self.assertFalse(result['fingerprint_known'])
        self.assertEqual(result['fingerprint'], '')
        self.assertIn('pointer', result['proof_reason'])
        first.modifiers.clear()
        first.driver = object()
        self.assertFalse(self.check()['fingerprint_known'])

    def test_non_finite_and_unknown_rna_kind_cannot_be_silently_skipped(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        first.keyframe_points[0].amplitude = float('nan')
        self.assertFalse(self.check()['fingerprint_known'])
        first.keyframe_points[0].amplitude = .8
        first.modifiers.append(RNA(type='FUTURE', surprise=7, kinds={'surprise': 'UNKNOWN'}))
        self.assertFalse(self.check()['fingerprint_known'])

    def test_keyed_pose_evaluation_and_other_active_action_do_not_false_change(self):
        before = self.check()
        pose = self.rig.pose.bones[0]
        pose.location, pose.rotation_quaternion, pose.scale = (5., 6., 7.), (.5, .5, .5, .5), (2., 3., 4.)
        foreign = action(handle=88)
        self.rig.animation_data.action, self.rig.animation_data.action_slot = foreign, foreign.slots[0]
        self.assertEqual(before, self.check())

    def test_unkeyed_pose_inputs_change_hash_or_foreign_driven_become_unknown(self):
        curves = self.action.layers[0].strips[0].channelbags[0].fcurves
        curves[:] = [item for item in curves if (item.data_path, item.array_index) != ('pose.bones["Hip"].location', 2)]
        before = self.check()
        self.assertTrue(before['fingerprint_known'])
        self.rig.pose.bones[0].location = (0., 0., .2)
        self.assertNotEqual(before['fingerprint'], self.check()['fingerprint'])
        other = action(handle=90)
        self.rig.animation_data.action, self.rig.animation_data.action_slot = other, other.slots[0]
        result = self.check()
        self.assertFalse(result['fingerprint_known'])
        self.assertIn('foreign Action', result['proof_reason'])

    def test_rest_rotation_mode_and_export_timing_affect_full_hash(self):
        before = self.check()
        self.rig.data.bones[0].inherit_scale = 'NONE'
        changed = self.check()
        self.assertNotEqual(before['fingerprint'], changed['fingerprint'])
        self.assertEqual(before['action_fingerprint'], changed['action_fingerprint'])
        before = self.check(timing=timing())
        # Timing within the strip's coverage remains provable; widening beyond
        # its range is explicitly Unknown in the separate guard test below.
        for field, value in (('frame_end', 2.5), ('fps', 30), ('fps_base', 1.001),
                             ('sample_rate', 30.), ('unit_scale', .01), ('loop', True)):
            config = timing()
            config[field] = value
            with self.subTest(field=field):
                changed = self.check(timing=config)
                self.assertNotEqual(before['fingerprint'], changed['fingerprint'])
                self.assertEqual(before['action_fingerprint'], changed['action_fingerprint'])

    def test_unsupported_dependencies_preserve_motion_hash_and_return_unknown(self):
        baseline = fingerprints.fingerprint_action(self.action, 4)['action_fingerprint']
        for owner, field, value in ((self.rig, 'constraints', [object()]),
                                   (self.rig, 'parent', object()),
                                   (self.rig.pose.bones[0], 'constraints', [object()]),
                                   (self.rig.animation_data, 'drivers', [object()]),
                                   (self.rig.data, 'animation_data', object())):
            with self.subTest(field=field):
                previous = getattr(owner, field)
                setattr(owner, field, value)
                result = self.check()
                self.assertFalse(result['fingerprint_known'])
                self.assertEqual(result['fingerprint'], '')
                self.assertEqual(result['action_fingerprint'], baseline)
                setattr(owner, field, previous)

    def test_object_motion_is_unknown_due_to_export_snapshot_reference(self):
        self.action.layers[0].strips[0].channelbags[0].fcurves.append(curve('location'))
        result = self.check()
        self.assertFalse(result['fingerprint_known'])
        self.assertIn('stable export reference', result['proof_reason'])

    def test_missing_slot_partial_blend_and_bad_timing_are_unknown(self):
        self.assertFalse(fingerprints.fingerprint_action(self.action, True)['fingerprint_known'])
        self.assertFalse(fingerprints.fingerprint_action(self.action, 999)['fingerprint_known'])
        self.action.layers[0].influence = .5
        self.assertFalse(self.check()['fingerprint_known'])
        self.action.layers[0].influence = 1.
        self.assertFalse(self.check(timing={})['fingerprint_known'])
        config = timing()
        config['loop'] = 1
        self.assertFalse(self.check(timing=config)['fingerprint_known'])

    def test_unsupported_path_or_partial_strip_coverage_is_unknown(self):
        first = self.action.layers[0].strips[0].channelbags[0].fcurves[0]
        old = first.data_path
        first.data_path = 'pose.bones["Missing"].location'
        self.assertFalse(self.check()['fingerprint_known'])
        first.data_path = old
        config = timing()
        config['frame_end'] = 4.
        self.assertFalse(self.check(timing=config)['fingerprint_known'])

    def test_foreign_slot_on_same_action_is_not_treated_as_own_keyed_input(self):
        bag = self.action.layers[0].strips[0].channelbags[0]
        bag.fcurves[:] = [item for item in bag.fcurves if (item.data_path, item.array_index)
                          != ('pose.bones["Hip"].location', 2)]
        self.action.slots.append(SimpleNamespace(handle=92, target_id_type='OBJECT'))
        other = SimpleNamespace(slot_handle=92, fcurves=[curve('pose.bones["Hip"].location', 2)])
        self.action.layers[0].strips[0].channelbags.append(other)
        self.rig.animation_data.action_slot = self.action.slots[1]
        self.assertFalse(self.check()['fingerprint_known'])

    def test_pure_schema_unknown_proof_and_unserializable_objects_fail_closed(self):
        payload = fingerprints.collect_native_payload(self.action, self.rig, self.scene, 4)
        payload['proof']['complete'] = False
        result = fingerprints.fingerprint_serialized(payload)
        self.assertFalse(result['fingerprint_known'])
        self.assertTrue(result['action_fingerprint'])
        payload['proof']['complete'] = True
        payload['rig']['external'] = object()
        self.assertFalse(fingerprints.fingerprint_serialized(payload)['fingerprint_known'])
        self.assertFalse(fingerprints.fingerprint_serialized({'schema': 'future'})['fingerprint_known'])

    def test_native_collection_never_writes_action_rig_or_scene(self):
        before = copy.deepcopy((self.action, self.rig, self.scene))
        self.assertTrue(self.check()['fingerprint_known'])
        # The facade contains callables; compare stable RNA and native values,
        # not pointer identity or callable deepcopy behavior.
        self.assertEqual(fingerprints.collect_action_payload(before[0], 4),
                         fingerprints.collect_action_payload(self.action, 4))
        self.assertEqual(before[1].pose.bones[0].location, self.rig.pose.bones[0].location)
        self.assertEqual(before[1].animation_data.action.name, self.rig.animation_data.action.name)
        self.assertEqual(before[2].render.fps, self.scene.render.fps)


if __name__ == '__main__':
    unittest.main(verbosity=2)
