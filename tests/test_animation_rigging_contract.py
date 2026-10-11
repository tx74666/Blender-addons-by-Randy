"""Pure source-control protocol guards; no Blender, SDK, worker or file writes."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'addons' / 'character_designer' / 'animation_rigging_contract.py'
SPEC = importlib.util.spec_from_file_location('_test_animation_rigging_contract', SOURCE)
contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contract)

MATRIX = [1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.]
IDENTITY = {'source_packet_sha256': 'a' * 64,
    'work_item_id': '948e797b-8d70-44c4-9530-a820349e8968',
    'new_action_id': '33333333333333333333333333333333',
    'source_action': 'Original Walk_N base', 'source_action_slot': 929201142,
    'target_input_action': 'Independent Target input', 'target_input_action_slot': 929201143}


def capture():
    bindings = []
    for side, suffix in (('Right', 'R'), ('Left', 'L')):
        root = 'CoshaRig/Hips/spine/Chest/UpperChest/shoulder.' + suffix + '/upper_arm.' + suffix
        mid, parent = root + '/forearm.' + suffix, 'CoshaRig/Rig 1/' + side + 'Arm'
        target_parent = ('CoshaRig/Hips/spine/Chest/UpperChest/shoulder.R/upper_arm.R/forearm.R/hand.R'
            if side == 'Left' else parent)
        target_name, hint_name = ('LeftArm_target', 'LeftArm_hint') if side == 'Left' else ('Rig 1_target', 'Rig 1_hint')
        bindings.append({'rigPath': 'CoshaRig/Rig 1', 'rigWeight': 0.,
            'constraintPath': parent, 'rootPath': root,
            'midPath': mid, 'tipPath': mid + '/hand.' + suffix,
            'targetPath': target_parent + '/' + target_name, 'targetParentPath': target_parent,
            'hintPath': parent + '/' + hint_name, 'hintParentPath': parent,
            'constraintWeight': 1., 'targetPositionWeight': 1., 'targetRotationWeight': 1.,
            'hintWeight': 1., 'maintainTargetPositionOffset': False,
            'maintainTargetRotationOffset': False})
    frames = [{'time': time, 'controls': [
        {'targetPosition': [.1, .2, .3], 'targetRotation': [0., 0., 0., 1.],
         'hintPosition': [.4, .5, .6], 'targetParentRoot': MATRIX[:], 'hintParentRoot': MATRIX[:]}
        for _side in ('Right', 'Left')]} for time in (0., 1 / 60, 1 / 30)]
    return {'schema': contract.SCHEMA, 'targetGuid': '1' * 32, 'targetDependencyHash': '2' * 32,
        'sourceMode': 'clip', 'sourceClipPath': 'Assets/Animation/Aim.anim',
        'sourceClipGuid': '3' * 32, 'sourceClipLocalId': 7400000, 'sourceClipSha256': '4' * 64,
        'bindingsSha256': 'f92703dbdb8e10779e08434134fc7cca69d39d633dfccb48746a4aa087b92e73',
        'newActionId': IDENTITY['new_action_id'], 'duration': 1 / 30, 'sampleRate': 60., 'loopTime': True,
        'sourceTimeMode': 'normalized-base-phase', 'previewEnabled': True,
        'bindings': bindings, 'frames': frames}


def packet(value):
    bones, by_path = [], {}
    for binding in value['bindings']:
        parts = binding['tipPath'].split('/')
        for count in range(1, len(parts) + 1):
            path = '/'.join(parts[:count])
            if path not in by_path:
                parent = '/'.join(parts[:count - 1])
                by_path[path] = len(bones)
                bones.append({'path': path, 'parent': by_path.get(parent, -1)})
    return {'targetGuid': value['targetGuid'], 'duration': value['duration'],
        'sampleRate': value['sampleRate'], 'loopTime': value['loopTime'],
        'clipAssetPath': 'Assets/Animation/Walk_N.anim', 'clipGuid': '6' * 32,
        'clipLocalId': 3062299877480904481,
        'frames': [{'time': frame['time']} for frame in value['frames']], 'bones': bones}


def samples(value):
    return [{'time': frame['time'], 'controls': [
        {name: deepcopy(control[name]) for name in contract.LOCAL_FIELDS}
        for control in frame['controls']]} for frame in value['frames']]


def weapon_preview(value):
    mount = value['bindings'][0]['tipPath']
    return {'weaponContextFile': 'D:/Character/Gun/weapon_context.json',
        'weaponContextSha256': '6' * 64, 'weaponGuid': '7' * 32, 'weaponPrefabGuid': '8' * 32,
        'weaponModelFile': 'D:/Character/Gun/Gun.fbx', 'weaponModelSha256': '9' * 64,
        'weaponSocketPath': mount + '/GunSocket', 'weaponRigBonePath': mount,
        'weaponLocalMatrix': MATRIX[:], 'weaponPrefabLocalMatrix': MATRIX[:]}


APPLY_IDENTITY = {'targetLayoutSha256': '7' * 64, 'baseControllerGuid': '8' * 32,
    'baseControllerDependencyHash': '9' * 32, 'avatarDependencyHash': 'a' * 32}


class RiggingCaptureTests(unittest.TestCase):
    def test_optional_display_weapon_preserves_exact_packet_mount_and_owned_context(self):
        value = capture()
        value['weaponPreview'] = weapon_preview(value)
        actual = contract.validate_capture(value, packet=packet(value))
        self.assertEqual(set(actual['weaponPreview']), contract.WEAPON_PREVIEW_FIELDS)
        before = contract.capture_digest(value)
        value['weaponPreview']['weaponLocalMatrix'][3] += .1
        self.assertEqual(actual['weaponPreview']['weaponLocalMatrix'][3], 0.)
        self.assertNotEqual(before, contract.capture_digest(value))
        base = packet(value)
        mount = value['weaponPreview']['weaponRigBonePath']
        base['bones'] = [row for row in base['bones'] if row['path'] != mount]
        with self.assertRaises(ValueError):
            contract.validate_capture(value, packet=base)
        value['weaponPreview']['weaponRigBonePath'] = 'AnotherRig/Hand'
        with self.assertRaises(ValueError):
            contract.validate_capture(value, packet=packet(value))

    def test_old_capture_absent_or_null_weapon_remains_valid(self):
        value = capture()
        actual = contract.validate_capture(value, packet=packet(value))
        self.assertNotIn('weaponPreview', actual)
        self.assertEqual(contract.capture_digest(value), contract.capture_digest(actual))
        value['weaponPreview'] = None
        self.assertIsNone(contract.validate_capture(value, packet=packet(value))['weaponPreview'])

    def test_weapon_context_has_only_ten_typed_fields_and_no_legacy_roles(self):
        mutations = [('weaponContextFile', 'relative/context.json'), ('weaponModelFile', 'D:Gun.fbx'),
            ('weaponModelFile', 'D:/Gun/../Gun.fbx'), ('weaponContextSha256', '6' * 32),
            ('weaponGuid', '0' * 32), ('weaponPrefabGuid', 'bad'), ('weaponModelSha256', '9' * 32),
            ('weaponSocketPath', ''), ('weaponSocketPath', 'Rig/../Socket'),
            ('weaponSocketPath', capture()['bindings'][0]['tipPath']),
            ('weaponSocketPath', capture()['bindings'][0]['tipPath'] + '/Intermediate/GunSocket'),
            ('weaponRigBonePath', '/Absolute/Hand'), ('weaponLocalMatrix', [0.] * 16),
            ('weaponPrefabLocalMatrix', MATRIX[:12] + [.1, 0., 0., 1.]),
            ('gripTargets', []), ('role', 'A')]
        for name, replacement in mutations:
            with self.subTest(field=name):
                value = capture()
                value['weaponPreview'] = weapon_preview(value)
                value['weaponPreview'][name] = replacement
                with self.assertRaises(ValueError):
                    contract.validate_capture(value)
        value = capture()
        value['weaponPreview'] = weapon_preview(value)
        del value['weaponPreview']['weaponSocketPath']
        with self.assertRaises(ValueError):
            contract.validate_capture(value)
        value = capture()
        value['weaponPreview'] = weapon_preview(value)
        value['weaponPreview']['weaponRigBonePath'] = value['bindings'][1]['tipPath']
        value['weaponPreview']['weaponSocketPath'] = value['bindings'][1]['tipPath'] + '/GunSocket'
        with self.assertRaises(ValueError):
            contract.validate_capture(value, packet=packet(value))

    def test_apply_layout_identity_is_one_complete_typed_capture_group(self):
        value = capture()
        self.assertFalse(set(contract.validate_capture(value)) & contract.APPLY_IDENTITY_FIELDS)
        before = contract.capture_digest(value)
        value.update(APPLY_IDENTITY)
        actual = contract.validate_capture(value)
        self.assertNotEqual(before, contract.capture_digest(value))
        self.assertEqual({name: actual[name] for name in contract.APPLY_IDENTITY_FIELDS}, APPLY_IDENTITY)
        for field in contract.APPLY_IDENTITY_FIELDS:
            partial = deepcopy(value)
            del partial[field]
            with self.assertRaises(ValueError):
                contract.validate_capture(partial)
            invalid = deepcopy(value)
            invalid[field] = None
            with self.assertRaises(ValueError):
                contract.validate_capture(invalid)
        value['baseControllerGuid'] = '0' * 32
        with self.assertRaises(ValueError):
            contract.validate_capture(value)

    def test_authoritative_prefab_default_wire_shape(self):
        value = capture()
        value.update(sourceMode='prefab-default', sourceClipPath='', sourceClipGuid='',
            sourceClipLocalId=0, sourceClipSha256='', duration=.016666668)
        value['frames'] = [value['frames'][0], value['frames'][1]]
        value['frames'][1]['time'] = value['duration']
        actual = contract.validate_capture(value, packet=packet(value))
        self.assertEqual(actual['newActionId'], '33333333333333333333333333333333')
        self.assertNotIn('side', actual['bindings'][0])
        self.assertEqual(actual['bindings'][1]['targetParentPath'], actual['bindings'][0]['tipPath'])
        self.assertEqual([frame['time'] for frame in actual['frames']], [0., .016666668])

    def test_published_unity_binding_fingerprint_known_vector(self):
        # Exact bindings and SHA from the authoritative Source-only wire fixture.
        value = capture()
        self.assertEqual(contract.bindings_digest(value['bindings']), value['bindingsSha256'])
        value['bindings'][0]['hintWeight'] = .5
        with self.assertRaises(ValueError):
            contract.validate_capture(value)
        value = capture()
        value['bindingsSha256'] = '5' * 64
        with self.assertRaises(ValueError):
            contract.validate_capture(value)

    def test_independent_aim_clip_can_share_walk_packet_clock(self):
        value = capture()
        actual = contract.validate_capture(value, packet=packet(value))
        self.assertEqual(actual['sourceClipGuid'], '3' * 32)
        self.assertEqual(actual['bindings'][0]['rigWeight'], 0.)
        self.assertTrue(actual['previewEnabled'])

    def test_capture_is_owned_and_digest_is_not_a_unity_or_packet_hash(self):
        value = capture()
        actual = contract.validate_capture(value)
        before = contract.capture_digest(value)
        value['frames'][0]['controls'][0]['targetPosition'][0] += .1
        self.assertEqual(actual['frames'][0]['controls'][0]['targetPosition'][0], .1)
        self.assertNotEqual(before, contract.capture_digest(value))
        self.assertNotEqual(before, value['bindingsSha256'])
        self.assertNotEqual(before, IDENTITY['source_packet_sha256'])

    def test_prefab_default_allows_only_empty_or_omitted_clip_defaults(self):
        value = capture()
        value['sourceMode'] = 'prefab-default'
        for name in contract.CLIP_FIELDS:
            del value[name]
        actual = contract.validate_capture(value, packet=packet(value))
        self.assertEqual(actual['sourceClipLocalId'], 0)
        self.assertEqual(actual['sourceClipGuid'], '')
        value['sourceClipGuid'] = '3' * 32
        with self.assertRaises(ValueError):
            contract.validate_capture(value)

    def test_explicit_clip_requires_all_identity_fields(self):
        for field in contract.CLIP_FIELDS:
            with self.subTest(field=field):
                value = capture()
                del value[field]
                with self.assertRaises(ValueError):
                    contract.validate_capture(value)
        value = capture()
        del value['newActionId']
        with self.assertRaises(ValueError):
            contract.validate_capture(value)

    def test_guid_hash_and_signed_local_id_are_strict(self):
        mutations = [('targetGuid', '0' * 32), ('targetDependencyHash', '2' * 64),
            ('bindingsSha256', '5' * 32), ('sourceClipGuid', 'bad'),
            ('sourceClipSha256', '4' * 32), ('sourceClipLocalId', True),
            ('sourceClipLocalId', 2**63), ('sourceClipLocalId', 0),
            ('newActionId', '0' * 32), ('newActionId', '33333333-3333-3333-3333-333333333333')]
        for name, replacement in mutations:
            with self.subTest(field=name, value=replacement):
                value = capture()
                value[name] = replacement
                with self.assertRaises(ValueError):
                    contract.validate_capture(value)
        value = capture()
        value['sourceClipLocalId'] = -(2**63)
        self.assertEqual(contract.validate_capture(value)['sourceClipLocalId'], -(2**63))

    def test_no_unknown_capture_fields_or_implicit_preview(self):
        for name, replacement in (('previewEnabled', 1), ('sourceTimeMode', 'world'),
                                   ('sourceMode', 'guessed-clip'), ('weaponTargetFollow', True)):
            with self.subTest(field=name):
                value = capture()
                value[name] = replacement
                with self.assertRaises(ValueError):
                    contract.validate_capture(value)

    def test_side_order_and_control_index_shape_are_fixed(self):
        value = capture()
        value['bindings'].reverse()
        with self.assertRaises(ValueError):
            contract.validate_capture(value)
        value = capture()
        value['bindings'][0]['side'] = 'Right'
        with self.assertRaises(ValueError):
            contract.validate_capture(value)
        value = capture()
        value['frames'][0]['controls'][0]['side'] = 'Right'
        with self.assertRaises(ValueError):
            contract.validate_capture(value)

    def test_canonical_paths_parent_cycles_and_aliases_reject(self):
        for path in ('/ArmRig', 'ArmRig\\Right', 'ArmRig/../Right', 'ArmRig//Right'):
            with self.subTest(path=path):
                value = capture()
                value['bindings'][0]['rigPath'] = path
                with self.assertRaises(ValueError):
                    contract.validate_capture(value)
        value = capture()
        value['bindings'][0]['targetParentPath'] = value['bindings'][0]['targetPath']
        with self.assertRaises(ValueError):
            contract.validate_capture(value)
        value = capture()
        value['bindings'][0]['targetPath'] = value['bindings'][0]['tipPath']
        value['bindings'][0]['targetParentPath'] = value['bindings'][0]['midPath']
        with self.assertRaises(ValueError):
            contract.validate_capture(value)

    def test_same_rig_weight_and_each_weight_boolean_are_original_context(self):
        value = capture()
        value['bindings'][1]['rigWeight'] = 1.
        with self.assertRaises(ValueError):
            contract.validate_capture(value)
        for name, replacement in (('hintWeight', 1.01), ('targetPositionWeight', float('nan')),
                                   ('constraintWeight', True), ('maintainTargetPositionOffset', 0)):
            with self.subTest(field=name):
                value = capture()
                value['bindings'][0][name] = replacement
                with self.assertRaises(ValueError):
                    contract.validate_capture(value)

    def test_parent_matrices_are_affine_invertible_root_space_only(self):
        for replacement in ([0.] * 16, MATRIX[:-1], MATRIX[:12] + [.1, 0., 0., 1.]):
            value = capture()
            value['frames'][0]['controls'][0]['targetParentRoot'] = replacement
            with self.assertRaises(ValueError):
                contract.validate_capture(value)
        value = capture()
        control = value['frames'][0]['controls'][0]
        control['targetParentWorld'] = control.pop('targetParentRoot')
        with self.assertRaises(ValueError):
            contract.validate_capture(value)

    def test_controls_require_finite_vectors_and_unit_xyzw_quaternions(self):
        for field, replacement in (('targetPosition', [0., 1., float('inf')]),
            ('hintPosition', [False, 0., 1.]), ('targetRotation', [0., 0., 0., 0.]),
            ('targetRotation', [0., 0., 0., 2.]), ('targetRotation', [0., 0., 1.])):
            with self.subTest(field=field, value=replacement):
                value = capture()
                value['frames'][0]['controls'][0][field] = replacement
                with self.assertRaises(ValueError):
                    contract.validate_capture(value)

    def test_sample_grid_bounds_rate_and_finite_clock(self):
        for name, replacement in (('sampleRate', True), ('sampleRate', 241),
            ('duration', 0), ('duration', float('inf')), ('duration', 10**400),
            ('duration', 20002 / 60), ('loopTime', 1)):
            with self.subTest(field=name):
                value = capture()
                value[name] = replacement
                with self.assertRaises(ValueError):
                    contract.validate_capture(value)
        value = capture()
        value['frames'][1]['time'] += .001
        with self.assertRaises(ValueError):
            contract.validate_capture(value)
        value = capture()
        value['frames'].pop()
        with self.assertRaises(ValueError):
            contract.validate_capture(value)

    def test_packet_target_clock_and_real_bone_chain_cannot_drift(self):
        value = capture()
        for kind in ('target', 'duration', 'frame', 'parent', 'path'):
            with self.subTest(kind=kind):
                base = packet(value)
                if kind == 'target':
                    base['targetGuid'] = '7' * 32
                elif kind == 'duration':
                    base['duration'] += .1
                elif kind == 'frame':
                    base['frames'][1]['time'] += 1e-9
                elif kind == 'parent':
                    mid = value['bindings'][0]['midPath']
                    row = next(row for row in base['bones'] if row['path'] == mid)
                    row['parent'] = -1
                else:
                    root = value['bindings'][0]['rootPath']
                    row = next(row for row in base['bones'] if row['path'] == root)
                    row['path'] = 'Another/Root'
                with self.assertRaises(ValueError):
                    contract.validate_capture(value, packet=base)


class RiggingReturnTests(unittest.TestCase):
    def build(self, value=None, sample_values=None, identity=None):
        value = capture() if value is None else value
        sample_values = samples(value) if sample_values is None else sample_values
        return contract.build_export(value, sample_values, **(IDENTITY if identity is None else identity))

    def test_return_contains_only_declared_source_local_inputs(self):
        value = capture()
        sample_values = samples(value)
        sample_values[1]['controls'][0]['targetPosition'][0] += .02
        result = self.build(value, sample_values)
        self.assertEqual(set(result), contract.EXPORT_FIELDS)
        self.assertEqual(result['sourceAction'], IDENTITY['source_action'])
        self.assertEqual(result['targetInputAction'], IDENTITY['target_input_action'])
        self.assertEqual(result['sourcePacketSha256'], IDENTITY['source_packet_sha256'])
        self.assertEqual(result['workItemId'], '948e797b8d7044c49530a820349e8968')
        self.assertEqual(result['newActionId'], value['newActionId'])
        self.assertEqual(set(result['frames'][0]['controls'][0]), contract.LOCAL_FIELDS)
        sample_values[1]['controls'][0]['targetPosition'][0] = 999.
        self.assertAlmostEqual(result['frames'][1]['controls'][0]['targetPosition'][0], .12)
        self.assertEqual(value['bindings'][0]['rigWeight'], 0.)

    def test_display_weapon_and_apply_identity_are_capture_only_never_returned(self):
        value = capture()
        value['weaponPreview'] = weapon_preview(value)
        value.update(APPLY_IDENTITY)
        result = self.build(value)
        self.assertEqual(set(result), contract.EXPORT_FIELDS)
        self.assertNotIn('weaponPreview', result)
        self.assertFalse(set(result) & contract.APPLY_IDENTITY_FIELDS)
        for field, replacement in (('weaponPreview', value['weaponPreview']),
                                   ('targetLayoutSha256', value['targetLayoutSha256'])):
            altered = deepcopy(result)
            altered[field] = replacement
            with self.assertRaises(ValueError):
                contract.validate_export(altered, value, **IDENTITY)

    def test_external_packet_identity_and_actual_action_slots_are_mandatory(self):
        result = self.build()
        for key in ('sourcePacketSha256', 'bindingsSha256', 'targetGuid', 'workItemId',
                    'newActionId', 'sourceAction', 'sourceActionSlot',
                    'targetInputAction', 'targetInputActionSlot'):
            with self.subTest(field=key):
                altered = deepcopy(result)
                if key.endswith('Slot'):
                    altered[key] += 1
                elif key in ('workItemId', 'newActionId'):
                    altered[key] = '07e3fe2b25d645daafbccf120a6c1130'
                elif key in ('sourceAction', 'targetInputAction'):
                    altered[key] += ' foreign'
                else:
                    altered[key] = '7' * len(altered[key])
                with self.assertRaises(ValueError):
                    contract.validate_export(altered, capture(), **IDENTITY)

    def test_native_action_identity_slots_and_draft_uuid_do_not_alias(self):
        for field, replacement in (('source_action_slot', True), ('target_input_action_slot', -1),
            ('source_action_slot', -1), ('source_action_slot', 2**31),
            ('target_input_action_slot', 2**31), ('new_action_id', ''),
            ('new_action_id', '948e797b8d7044c49530a820349e8968'),
            ('new_action_id', '7' * 32), ('source_packet_sha256', 'a' * 32)):
            with self.subTest(field=field):
                identity = dict(IDENTITY)
                identity[field] = replacement
                with self.assertRaises(ValueError):
                    self.build(identity=identity)
        identity = dict(IDENTITY)
        identity.update(target_input_action=identity['source_action'],
            target_input_action_slot=identity['source_action_slot'])
        with self.assertRaises(ValueError):
            self.build(identity=identity)
        identity['target_input_action_slot'] += 1
        with self.assertRaises(ValueError):
            self.build(identity=identity)

    def test_exact_base_and_target_slot_zero_are_valid_and_mandatory(self):
        identity = dict(IDENTITY, source_action_slot=0, target_input_action_slot=0)
        result = self.build(identity=identity)
        self.assertEqual(result['sourceActionSlot'], 0)
        self.assertEqual(result['targetInputActionSlot'], 0)
        for field in ('sourceActionSlot', 'targetInputActionSlot'):
            altered = deepcopy(result)
            del altered[field]
            with self.assertRaises(ValueError):
                contract.validate_export(altered, capture(), **identity)

    def test_return_cannot_write_weights_parent_spaces_or_combined_bones(self):
        for field, replacement in (('targetParentRoot', MATRIX), ('constraintWeight', 1.),
            ('rootPath', capture()['bindings'][0]['rootPath']), ('hintRotation', [0., 0., 0., 1.])):
            with self.subTest(field=field):
                value = capture()
                altered = samples(value)
                altered[0]['controls'][0][field] = replacement
                with self.assertRaises(ValueError):
                    self.build(value, altered)
        result = self.build()
        result['bindings'] = capture()['bindings']
        with self.assertRaises(ValueError):
            contract.validate_export(result, capture(), **IDENTITY)

    def test_return_same_clock_is_exact_not_another_sampled_clip(self):
        value = capture()
        altered = samples(value)
        altered[1]['time'] += 1e-9
        with self.assertRaises(ValueError):
            self.build(value, altered)
        for field, replacement in (('duration', .2), ('sampleRate', 30.), ('loopTime', False)):
            with self.subTest(field=field):
                result = self.build()
                result[field] = replacement
                with self.assertRaises(ValueError):
                    contract.validate_export(result, capture(), **IDENTITY)

    def test_return_rejects_bad_rotation_or_control_count(self):
        value = capture()
        altered = samples(value)
        altered[0]['controls'][0]['targetRotation'] = [0., 0., 0., 0.]
        with self.assertRaises(ValueError):
            self.build(value, altered)
        altered = samples(value)
        altered[0]['controls'].pop()
        with self.assertRaises(ValueError):
            self.build(value, altered)


if __name__ == '__main__':
    unittest.main()
