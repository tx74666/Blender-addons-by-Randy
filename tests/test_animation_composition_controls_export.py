"""Cheap M1 contract/isolation guards; no bpy, Blender process or artist writes."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import uuid
from contextlib import contextmanager
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
MODULE_ROOT = ROOT.parent / 'addons' / 'character_designer'
sys.path.insert(0, str(MODULE_ROOT if MODULE_ROOT.is_dir() else ROOT))
import animation_composition_contract as contract
import animation_composition_controls as controls
import animation_composition_export as export

FIXTURE = ROOT / 'fixtures' / 'composition_rest_identity.json'
if not FIXTURE.is_file():
    FIXTURE = Path('D:/Unity Projects/RandomRealm2/.codex/task-staging/character-composition-20261011/rest-fixture.json')
IDENTITY = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]
UP = 'CoshaRig/Hips/spine'
RNA = 'pose.bones["spine"].rotation_euler'


def spec():
    value = {field: '' for field in contract.FIELDS}
    value.update(schema=contract.SCHEMA, compositionId='11111111-1111-1111-1111-111111111111',
        applicationRole=contract.ROLE, baseClipGuid='1' * 32, baseClipLocalId=74,
        baseClipSha256='a' * 64, referencePacketFile='D:/A.cdanim.json', referencePacketSha256='b' * 64,
        editWorkItemId='22222222-2222-2222-2222-222222222222', editNewActionId='', mask='UpperBody',
        blendMode='Override', weight=1, humanRoles=['Spine'], allowedBonePaths=[UP], editableControlIds=[],
        sourceRestSha256='c' * 64, timeMode='HoldLoop', referenceSpeed=1., editSpeed=1.,
        referencePhase=0., editPhase=0., fps=60., sampleStart=0., sampleEnd=1.,
        holdWorkItemId='', holdNewActionId='', weaponLocalMatrix=None, gripTargets=[])
    value.update(weaponContextFile='D:/weapon/context.json', weaponContextSha256='d' * 64)
    # New frozen optional mount matrices remain absent rather than invented.
    for field in contract.FIELDS:
        if field.endswith('Matrix') and field != 'weaponLocalMatrix':
            value[field] = IDENTITY[:]
    return contract.validate(value)


def bone(name, path, role, parent=-1):
    return dict(name=name, path=path, humanRole=role, restSource='bindpose', parent=parent, rest=IDENTITY[:])


def curve(path=RNA, component=0, values=(.2, .4), *, handles=None, modifier=False):
    points = []
    for frame, value in zip((1., 61.), values):
        left, right = handles if handles else (value, value)
        points.append(NS(co=[frame, value], handle_left=[frame - .3, left],
                         handle_right=[frame + .3, right], interpolation='BEZIER'))
    return NS(data_path=path, array_index=component, mute=False, modifiers=[object()] if modifier else [],
              sampled_points=[], is_driver=False, extrapolation='CONSTANT', keyframe_points=points)


def action(curves, **timing):
    values = dict(unity_sample_rate=60., unity_start_frame=1., unity_duration=1.)
    values.update(timing)
    return NS(is_action_layered=False, fcurves=curves, get=values.get)


def scope():
    return NS(channels=((RNA, 0),), defaults={(RNA, 0): 0.}, native_names=('spine',),
        path_by_native={'spine': UP}, input_by_native={'spine': 'spine'}, fixed_properties=(),
        admission_properties=(), auxiliary_names=(), canonical_defaults={(RNA, 0): 0.},
        proof={'sourceRest': 'c' * 64, 'canonicalInputs': [[RNA, 0, 0.]], 'effectiveInputs': [['spine', 'spine']]})


def fake_fp():
    def payload(c):
        return {'data_path': c.data_path, 'array_index': c.array_index, 'extrapolation': c.extrapolation,
            'keys': [[list(k.co), list(k.handle_left), list(k.handle_right), k.interpolation] for k in c.keyframe_points]}
    return NS(_slot=lambda a, s: None, _selected_curves=lambda a, s: a.fcurves, _curve_payload=payload)


class RestIdentityTests(unittest.TestCase):
    def test_shared_csharp_rest_fixture_exact(self):
        value = json.loads(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(controls.source_rest_sha256(value), value['expectedSha256'])
        self.assertEqual(value['expectedSha256'], '54011f87427adc4df9077943af35005933923d0ff4f2d918d0cd5dc30873da4d')

    def test_ordinal_utf16_order_not_python_codepoint_sort(self):
        rows = [bone('private', 'Root/\ue000', 'Spine'), bone('astral', 'Root/\U00010000', 'Chest')]
        def digest(order):
            h = hashlib.sha256()
            for b in order:
                for field in ('path', 'humanRole', 'restSource'):
                    raw = b[field].encode('utf-8')
                    h.update(struct.pack('<I', len(raw)) + raw)
                h.update(struct.pack('<i16f', b['parent'], *b['rest']))
            return h.hexdigest()
        actual = controls.source_rest_sha256({'bones': rows})
        self.assertEqual(actual, digest([rows[1], rows[0]]))
        self.assertNotEqual(actual, digest(sorted(rows, key=lambda b: b['path'])))

    def test_parsed_float32_rounding_and_rest_changes(self):
        value = json.loads(FIXTURE.read_text(encoding='utf-8'))
        base = controls.source_rest_sha256(value)
        value['bones'][0]['rest'][7] = struct.unpack('<f', struct.pack('<f', .123456789))[0]
        self.assertEqual(controls.source_rest_sha256(value), base)
        value['bones'][0]['rest'][7] += .001
        self.assertNotEqual(controls.source_rest_sha256(value), base)

    def test_duplicate_path_and_nonfinite_reject(self):
        for rows in ([bone('a', UP, 'Spine'), bone('b', UP, 'Chest')], [bone('a', UP, 'Spine')]):
            if len(rows) == 1:
                rows[0]['rest'][0] = float('nan')
            with self.assertRaises(ValueError):
                controls.source_rest_sha256({'bones': rows})

    def test_exact_authenticated_paths_no_suffix(self):
        packet = {'bones': [bone('spine', UP, 'Spine')]}
        value = spec()
        value['sourceRestSha256'] = controls.source_rest_sha256(packet)
        self.assertEqual(set(controls.resolve_packet_paths(value, packet, {'spine': 0})), {'spine'})
        value['allowedBonePaths'] = ['spine']
        with self.assertRaisesRegex(ValueError, 'absent'):
            controls.resolve_packet_paths(value, packet, {'spine': 0})

    def test_role_and_mapping_identity_cannot_be_guessed(self):
        packet = {'bones': [bone('spine', UP, 'Hips')]}
        value = spec()
        value['sourceRestSha256'] = controls.source_rest_sha256(packet)
        with self.assertRaises(ValueError):
            controls.resolve_packet_paths(value, packet, {'spine': 0})
        packet['bones'][0]['humanRole'] = 'Spine'
        value['sourceRestSha256'] = controls.source_rest_sha256(packet)
        with self.assertRaises(ValueError):
            controls.resolve_packet_paths(value, packet, {'guessedAlias': 0})

    def test_partial_coupled_scope_rejects(self):
        groups = [('left arm', ['upper', 'lower', 'hand'])]
        controls.require_complete_groups(['upper', 'lower', 'hand', 'shoulder'], groups)
        controls.require_complete_groups(['finger'], groups)
        with self.assertRaisesRegex(ValueError, 'partially'):
            controls.require_complete_groups(['hand'], groups)


class BContentTests(unittest.TestCase):
    def fingerprint(self, curves, value=None, selected=None):
        with patch.object(export, '_host', return_value=fake_fp()):
            return export.effective_fingerprint(action(curves), 0, selected or scope(), value or spec())

    def test_different_a_seek_reference_phase_never_changes_b(self):
        first = spec()
        second = copy.deepcopy(first)
        second.update(baseClipGuid='2' * 32, baseClipLocalId=88, baseClipSha256='d' * 64,
                      referencePacketSha256='e' * 64, referencePhase=.8, referenceSpeed=1.6)
        a, b = self.fingerprint([curve()], first), self.fingerprint([curve()], second)
        self.assertTrue(a['fingerprint_known'])
        self.assertEqual(a['authored_b_sha256'], b['authored_b_sha256'])
        self.assertEqual(a['effective_input_sha256'], b['effective_input_sha256'])
        self.assertEqual(a['fingerprint'], b['fingerprint'])

    def test_b_speed_changes_inputs_without_fake_b_edit(self):
        first, second = spec(), spec()
        second['editSpeed'] = 1.3
        a, b = self.fingerprint([curve()], first), self.fingerprint([curve()], second)
        self.assertEqual(a['authored_b_sha256'], b['authored_b_sha256'])
        self.assertNotEqual(a['effective_input_sha256'], b['effective_input_sha256'])
        self.assertNotEqual(a['fingerprint'], b['fingerprint'])

    def test_authored_handles_change_b_content(self):
        a = self.fingerprint([curve()])
        b = self.fingerprint([curve(handles=(.7, .9))])
        self.assertNotEqual(a['authored_b_sha256'], b['authored_b_sha256'])

    def test_fullbody_outside_curves_preserved_and_excluded(self):
        outside = curve('pose.bones["thigh.L"].rotation_euler', values=(.4, .8))
        original = copy.deepcopy(outside.keyframe_points[0].co)
        a = self.fingerprint([curve()])
        b = self.fingerprint([curve(), outside])
        self.assertEqual(a['authored_b_sha256'], b['authored_b_sha256'])
        self.assertEqual(outside.keyframe_points[0].co, original)

    def test_shared_bend_nonzero_and_fk_switch_one_reject(self):
        selected = scope()
        bend_path = 'pose.bones["CD Torso Bend"].rotation_euler'
        selected.auxiliary_names = ('CD Torso Bend',)
        selected.canonical_defaults[(bend_path, 0)] = 0.
        self.assertFalse(self.fingerprint([curve(), curve(bend_path, values=(.2, .2))], selected=selected)['fingerprint_known'])
        selected.auxiliary_names = ()
        selected.admission_properties = (('Arm Target', 'ik_fk', 0.),)
        switch = curve('pose.bones["Arm Target"]["ik_fk"]', values=(1., 1.))
        self.assertFalse(self.fingerprint([curve(), switch], selected=selected)['fingerprint_known'])

    def test_hold_pose_bezier_between_keys_motion_rejects(self):
        value = spec()
        value['timeMode'] = 'HoldPose'
        self.assertTrue(self.fingerprint([curve(values=(.3, .3))], value)['fingerprint_known'])
        self.assertFalse(self.fingerprint([curve(values=(.3, .3), handles=(.3, .8))], value)['fingerprint_known'])
        self.assertFalse(self.fingerprint([curve(values=(.3, .3), modifier=True)], value)['fingerprint_known'])

    def test_raise_requires_real_hold_identity(self):
        value = spec()
        value['timeMode'] = 'Raise'
        self.assertFalse(self.fingerprint([curve()], value)['fingerprint_known'])

    def test_rate_origin_and_unproved_timing_reject(self):
        with patch.object(export, '_host', return_value=fake_fp()):
            for timing in ({'unity_sample_rate': 30.}, {'unity_start_frame': 12.},
                           {'unity_sample_rate': float('nan')}, {'unity_duration': None}):
                result = export.effective_fingerprint(action([curve()], **timing), 0, scope(), spec())
                self.assertFalse(result['fingerprint_known'])

    def test_moving_b_requires_actual_keyed_range_and_source_duration(self):
        with patch.object(export, '_host', return_value=fake_fp()):
            short = curve()
            short.keyframe_points[-1].co[0] = 31.
            self.assertFalse(export.effective_fingerprint(action([short]), 0, scope(), spec())['fingerprint_known'])
            self.assertFalse(export.effective_fingerprint(action([curve()], unity_duration=.5), 0, scope(), spec())['fingerprint_known'])
        value = spec()
        value['sampleStart'] = -.1
        self.assertFalse(self.fingerprint([curve()], value)['fingerprint_known'])

    def test_real_constant_hold_can_extend_source_duration(self):
        value = spec()
        value.update(timeMode='HoldPose', sampleEnd=3.)
        self.assertTrue(self.fingerprint([curve(values=(.3, .3))], value)['fingerprint_known'])
        value['timeMode'] = 'HoldLoop'
        self.assertFalse(self.fingerprint([curve(values=(.3, .3))], value)['fingerprint_known'])

    def test_loop_range_accepts_native_float32_endpoint_without_resampling(self):
        value = spec()
        value['sampleEnd'] = 4.9333334
        keyed = curve()
        keyed.keyframe_points[-1].co[0] = 297.
        original_spec = copy.deepcopy(value)
        with patch.object(export, '_host', return_value=fake_fp()):
            proof = export.effective_fingerprint(
                action([keyed], unity_duration=value['sampleEnd']), 0, scope(), value)
        self.assertTrue(proof['fingerprint_known'], proof['proof_reason'])
        self.assertEqual(value, original_spec)
        self.assertEqual(export._frame_range(value)[1], 297.00000400000005)
        context = NS(scene=NS(unit_settings=NS(scale_length=1.)))
        self.assertEqual(export.export_timing(context, value)['frame_end'], 297.00000400000005)

    def test_loop_range_rejects_one_native_float32_coordinate_short(self):
        value = spec()
        value['sampleEnd'] = 4.9333334
        bits = struct.unpack('<I', struct.pack('<f', 297.))[0]
        keyed = curve()
        keyed.keyframe_points[-1].co[0] = struct.unpack('<f', struct.pack('<I', bits - 1))[0]
        with patch.object(export, '_host', return_value=fake_fp()):
            proof = export.effective_fingerprint(
                action([keyed], unity_duration=value['sampleEnd']), 0, scope(), value)
        self.assertFalse(proof['fingerprint_known'])
        self.assertIn('authored selected-slot FK range', proof['proof_reason'])

    def test_loop_native_coverage_does_not_extend_prepared_duration(self):
        value = spec()
        duration = 4.9333334
        value['sampleEnd'] = duration + 1. / value['fps']
        keyed = curve()
        keyed.keyframe_points[-1].co[0] = 298.
        with patch.object(export, '_host', return_value=fake_fp()):
            proof = export.effective_fingerprint(
                action([keyed], unity_duration=duration), 0, scope(), value)
        self.assertFalse(proof['fingerprint_known'])
        self.assertIn('prepared duration', proof['proof_reason'])

    def test_numeric_effective_hash_not_name_hash(self):
        names = [UP + '|localRotation|0']
        a = export.effective_values_proof(names, [0., 1.], {names[0]: [.2, .4]})
        b = export.effective_values_proof(names, [0., 1.], {names[0]: [.2, .5]})
        self.assertEqual(a['effectiveChannels'], b['effectiveChannels'])
        self.assertNotEqual(a['effectiveChannelsSha256'], b['effectiveChannelsSha256'])


class SealedPacketTests(unittest.TestCase):
    def test_source_dto_exact_after_normalization(self):
        value = spec()
        sealed = copy.deepcopy(value)
        sealed['baseClipGuid'] = sealed['baseClipGuid'].upper()
        self.assertEqual(controls.validate_packet_composition(value, {'composition': sealed}), value)

    def test_changed_a_weapon_or_missing_sealed_dto_rejects(self):
        value = spec()
        for key, changed in [('referencePhase', .4), ('weaponContextSha256', 'e' * 64),
                             ('editWorkItemId', '44444444-4444-4444-4444-444444444444')]:
            proposed = copy.deepcopy(value)
            proposed[key] = changed
            with self.assertRaises(ValueError):
                controls.validate_packet_composition(proposed, {'composition': value})
        with self.assertRaises(ValueError):
            controls.validate_packet_composition(value, {})

    def test_new_template_only_creation_ids_may_differ(self):
        sealed = spec()
        proposed = copy.deepcopy(sealed)
        proposed.update(compositionId='33333333-3333-3333-3333-333333333333',
            editWorkItemId='44444444-4444-4444-4444-444444444444',
            editNewActionId='55555555-5555-5555-5555-555555555555')
        self.assertEqual(controls.validate_packet_composition(proposed, {'composition': sealed}), proposed)
        for key, changed in [('editSpeed', 1.2), ('weaponContextSha256', 'e' * 64),
                             ('referencePhase', .4), ('sourceRestSha256', 'f' * 64)]:
            tampered = copy.deepcopy(proposed)
            tampered[key] = changed
            with self.assertRaises(ValueError):
                controls.validate_packet_composition(tampered, {'composition': sealed})

    def test_new_ids_alone_always_require_native_authentication(self):
        sealed, creation = spec(), object()
        proposed = copy.deepcopy(sealed)
        proposed['editNewActionId'] = '55555555-5555-5555-5555-555555555555'
        context, rig = object(), object()
        packet = {'composition': sealed}
        for proof in (None, creation):
            admission = Mock(side_effect=RuntimeError('native creation proof required'))
            with patch.object(controls, '_host', return_value=NS(authenticate_composition_bind_template=admission)):
                with self.assertRaisesRegex(RuntimeError, 'native creation proof'):
                    controls.resolve_scope(context, rig, proposed, packet, new_creation=proof)
            admission.assert_called_once_with(context, rig, proposed, packet, creation=proof)


class IsolationGuards(unittest.TestCase):
    def test_artist_snapshot_protects_object_root_motion_and_action_binding(self):
        fields = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale',
                  'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')
        rig = NS(**{field: [0., 0., 0.] for field in fields})
        rig.data, rig.pose = NS(as_pointer=lambda: 12), NS(bones=[])
        rig.matrix_world = [IDENTITY[i:i + 4] for i in range(0, 16, 4)]
        rig.animation_data = NS(action=NS(as_pointer=lambda: 24), action_slot=NS(handle=2),
            use_nla=True, use_tweak_mode=False, action_blend_type='REPLACE', action_influence=1.,
            action_extrapolation='HOLD')
        scene = NS(frame_current=25, frame_subframe=.5, render=NS(fps=60, fps_base=1.),
                   unit_settings=NS(system='METRIC', scale_length=1.))
        with patch.object(export, '_host', return_value=NS(_rest_state=lambda d: {'full': 'rest'})):
            original = export._artist_state(rig, scene)
            rig.location[0] = 3.
            self.assertNotEqual(original, export._artist_state(rig, scene))
            rig.location[0] = 0.
            rig.animation_data.action_slot.handle = 3
            self.assertNotEqual(original, export._artist_state(rig, scene))
            rig.animation_data.action_slot.handle = 2
            self.assertEqual(original, export._artist_state(rig, scene))
            for field, changed in (('system', 'NONE'), ('scale_length', .01)):
                previous = getattr(scene.unit_settings, field)
                setattr(scene.unit_settings, field, changed)
                self.assertNotEqual(original, export._artist_state(rig, scene))
                setattr(scene.unit_settings, field, previous)
            self.assertEqual(original, export._artist_state(rig, scene))

    def test_failed_data_copy_removes_owned_object_before_defer_exit(self):
        class ID:
            pass
        rig, clone, original_action, a_action = ID(), ID(), ID(), ID()
        rig.animation_data = NS(use_tweak_mode=False)
        rig.copy = lambda: clone
        def fail_copy():
            raise RuntimeError('disposable data copy failure')
        rig.data = NS(copy=fail_copy)
        selected, value = scope(), spec()
        item = NS(rig=rig, custom_action=original_action, custom_slot=0)
        session = dict(rig=rig, spec=value, custom_action=original_action, custom_slot=0,
            packet_path='D:/B.json', packet_sha256='packet', scope_proof=contract.digest(selected.proof),
            scope_channels=[list(pair) for pair in selected.channels], base_action=a_action,
            base_slot=0, base_hash='a' * 64)
        scene = NS(view_layers={'Layer': NS(name='Layer')})
        context = NS(window=NS(scene=scene), scene=scene, view_layer=NS(name='Layer'))
        events = []
        @contextmanager
        def deferred(_context, flush_on_exit):
            events.append('enter')
            try:
                yield
            finally:
                self.assertEqual(events[-1], 'remove clone')
                events.append('exit')
        def host(name):
            if name == 'unity_animation':
                return NS(load_package=lambda p: {'_sha256': 'packet'})
            if name == 'forearm_twist':
                return NS(defer_runtime=deferred)
            raise AssertionError(name)
        fake_bpy = NS(data=NS(objects=NS(remove=lambda obj, do_unlink: events.append('remove clone'))))
        with patch.dict(sys.modules, bpy=fake_bpy, mathutils=NS(Matrix=object)), \
             patch.object(export, '_host', side_effect=host), \
             patch.object(export.contract, 'validate_item', return_value=value), \
             patch.object(export, 'validate_scope', return_value=selected), \
             patch.object(export, 'effective_fingerprint', return_value={'fingerprint_known': True, 'proof_reason': ''}), \
             patch.object(export, '_action_hash', return_value='a' * 64), \
             patch.object(export, '_artist_state', return_value='unchanged'), \
             patch.object(export, '_source_wires', return_value=()), \
             patch.object(export, '_check_closed_dependencies'), \
             patch.object(export, 'baseline_worker'):
            with self.assertRaisesRegex(RuntimeError, 'copy failure'):
                with export.isolate_export(context, session, item):
                    self.fail('failed allocation cannot yield an export')
        self.assertEqual(events, ['enter', 'remove clone', 'exit'])
        self.assertIs(context.window.scene, scene)

    def test_failed_action_filter_removes_owned_copy_only(self):
        owned = NS(users=0, use_fake_user=False)
        source = NS(copy=lambda: owned)
        removed = []
        with patch.dict(sys.modules, bpy=NS(data=NS(actions=NS(remove=removed.append)))), \
             patch.object(export, '_strip_id_refs', side_effect=ValueError('filter failure')):
            with self.assertRaisesRegex(ValueError, 'filter failure'):
                export._copy_selected_action(source, 0, scope())
        self.assertEqual(removed, [owned])
        self.assertIsNot(source, owned)

    def test_invalid_worker_proof_rejected_before_publication(self):
        value = spec()
        metadata = {'invalid': 'not the mandatory native proof'}
        disposed, published = [], []
        backend = NS(_publish=lambda *a: published.append(a), _dispose=lambda j: disposed.append(j))
        process = NS(poll=lambda: 0, returncode=0)
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            (stage / 'result.json').write_text(json.dumps({'ok': True, 'composition': value,
                'composition_export': metadata}), encoding='utf-8')
            job = dict(process=process, stage=stage, composition=value, composition_export=metadata,
                       effective_values={}, started=0)
            with patch.object(export, '_host', return_value=backend):
                with self.assertRaises(ValueError):
                    export.poll_isolated_export(job)
        self.assertEqual(published, [])
        self.assertEqual(disposed, [job])

    def test_static_foreign_dependency_rejected_after_ordinary_guard(self):
        class ID:
            pass
        rig, foreign = ID(), object()
        rig.type, rig.constraints, rig.pose = 'ARMATURE', [], NS(bones=[])
        rig.data = object()
        called = []
        worker = NS(_check_external_dependencies=lambda r: called.append(r), _dependencies=lambda r: [foreign])
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            export._check_closed_dependencies(rig, (), worker)
        self.assertEqual(called, [rig])

    def test_action_constraint_reference_a_rejects(self):
        class ID:
            pass
        rig = ID()
        rig.type, rig.constraints, rig.pose, rig.data = 'ARMATURE', [NS(type='ACTION', action=object())], NS(bones=[]), object()
        worker = NS(_check_external_dependencies=lambda r: None, _dependencies=lambda r: [])
        with self.assertRaisesRegex(ValueError, 'Action constraint'):
            export._check_closed_dependencies(rig, (), worker)

    def test_id_ref_cleanup_preserves_primitive_control_markers(self):
        class ID:
            pass
        marker = 'character_designer_limb_ik_owner'
        copied = {marker: 'uuid', 'session': {'rig': ID(), 'raw': 'keep'}, 'original_data': ID()}
        with patch.dict(sys.modules, bpy=NS(types=NS(ID=ID))):
            export._strip_id_refs(copied)
        self.assertEqual(copied, {marker: 'uuid', 'session': {'raw': 'keep'}})

    def test_public_receipt_rejects_outside_alias_even_with_matching_claim(self):
        value = spec()
        metadata = dict(schema=contract.EXPORT_SCHEMA, compositionId=value['compositionId'],
            editWorkItemId=value['editWorkItemId'], editNewActionId='', sourceAction='B', sourceActionSlot=2,
            isolatedEditAction='B isolated', referenceActionUnchanged=True, referenceActionBeforeSha256='f' * 64,
            referenceActionAfterSha256='f' * 64, effectiveChannelsSha256='d' * 64, effectiveChannelCount=10,
            effectiveChannels=list(controls.effective_channel_names(scope())), sampleSpace='bone-local', excludedReferenceEvaluation=True)
        report = dict(composition=value, composition_export=metadata)
        export.validate_export_receipt(report, value, metadata)
        metadata['effectiveChannels'] = ['spine|localRotation|0']
        with self.assertRaises(ValueError):
            export.validate_export_receipt(report, value, metadata)


class RaiseDestinationIdentityTests(unittest.TestCase):
    def fixture(self, *, new=False):
        rig = object()
        hold = spec()
        hold['editNewActionId'] = '66666666-6666-6666-6666-666666666666' if new else ''
        row = NS(work_item_id=uuid.UUID(hold['editWorkItemId']).hex,
            new_action_id=uuid.UUID(hold['editNewActionId']).hex if new else '',
            new_animation=new, rig=rig, hold_spec=hold)
        raised = {**spec(), 'timeMode': 'Raise', 'holdWorkItemId': hold['editWorkItemId'],
                  'holdNewActionId': hold['editNewActionId']}
        return rig, row, raised

    def evaluate(self, rig, rows, raised):
        host = NS(state=lambda _context: NS(items=rows))
        module = NS(item_spec=lambda row: row.hold_spec)
        with patch.object(export, '_host', return_value=host), patch.dict(sys.modules, animation_composition=module):
            return export._raise_destination(object(), raised, rig)

    def test_source_native_hex_matches_dashed_dto_without_rewriting(self):
        rig, row, raised = self.fixture()
        original = copy.deepcopy(raised)
        self.evaluate(rig, [row], raised)
        self.assertEqual(raised, original)
        self.assertEqual(row.work_item_id, uuid.UUID(raised['holdWorkItemId']).hex)

    def test_actual_new_hold_native_hex_matches_both_dashed_ids(self):
        rig, row, raised = self.fixture(new=True)
        self.evaluate(rig, [row], raised)
        raised['holdWorkItemId'] = ''
        self.evaluate(rig, [row], raised)

    def test_foreign_rig_or_ambiguous_actual_owner_rejects(self):
        rig, row, raised = self.fixture()
        with self.assertRaisesRegex(ValueError, 'one actual'):
            self.evaluate(object(), [row], raised)
        duplicate = NS(**vars(row))
        with self.assertRaisesRegex(ValueError, 'one actual'):
            self.evaluate(rig, [row, duplicate], raised)

    def test_malformed_dto_or_native_identity_rejects_instead_of_being_ignored(self):
        for field in ('holdWorkItemId', 'holdNewActionId'):
            rig, row, raised = self.fixture()
            raised[field] = 'malformed'
            with self.assertRaisesRegex(ValueError, 'Invalid composition'):
                self.evaluate(rig, [row], raised)
        for field in ('work_item_id', 'new_action_id'):
            rig, row, raised = self.fixture()
            setattr(row, field, 'malformed')
            with self.assertRaisesRegex(ValueError, 'Invalid composition'):
                self.evaluate(rig, [row], raised)

    def test_unmatched_new_id_or_changed_scope_does_not_pass_matching_work_id(self):
        rig, row, raised = self.fixture()
        raised['holdNewActionId'] = '77777777-7777-7777-7777-777777777777'
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            self.evaluate(rig, [row], raised)
        raised['holdNewActionId'] = ''
        row.hold_spec['sourceRestSha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            self.evaluate(rig, [row], raised)


if __name__ == '__main__':
    unittest.main()
