"""Pure bounded Arm selection handoff; no Blender or artist scene is opened."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'
spec = importlib.util.spec_from_file_location('limb_control_selection', SOURCE / 'limb_control_selection.py')
selection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(selection)


def ownership_api():
    """Reuse production ownership/registry decoding and constants."""
    names = {'_owned', '_constraint_registry', '_constraint_name'}
    constants = {'OWNER_KEY', 'OWNER_VALUE', 'VERSION_KEY', 'RIG_VERSION',
                 'ARMATURE_ID_KEY', 'RIG_ID_KEY', 'KIND_KEY', 'SIDE_KEY',
                 'CHAIN_KEY', 'ROLE_KEY', 'CONSTRAINT_REGISTRY_KEY', 'LIMB_SPEC',
                 'SCHEMA_KEY', 'DIRECT_REST_KEY', 'LEGACY_DIRECT_PREROLL_SCHEMA',
                 'DIRECT_PREROLL_SCHEMA', 'DIRECT_PREROLL_RESULT_VERSION'}
    tree = ast.parse((SOURCE / 'limb_ik.py').read_text(encoding='utf-8'))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in names
             or isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                 and target.id in constants for target in node.targets)]
    scope = dict(json=json, validation_json=NS(decode=json.loads), LimbIKError=ValueError)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE / 'limb_ik.py'), 'exec'), scope)
    return NS(**scope)


class NoTouch:
    def __getattr__(self, name):
        raise AssertionError('Unexpected Action/geometry/frame access: ' + name)


class ID(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)

    def __bool__(self):
        return True

    def __eq__(self, other):
        return self is other

    def __ne__(self, other):
        return not self == other


class Bones(dict):
    active = None

    def __iter__(self):
        raise AssertionError('Full rig enumeration is forbidden')

    def values(self):
        raise AssertionError('Full rig values scan is forbidden')

    def items(self):
        raise AssertionError('Full rig items scan is forbidden')


class Pose:
    def __init__(self, bone):
        self.name, self.bone = bone.name, bone
        self.hide, self.is_visible, self._selected = False, True, False
        self.properties, self.fail = {}, False

    def get(self, name, default=None):
        return self.properties.get(name, default)

    @property
    def select(self):
        return self._selected

    @select.setter
    def select(self, value):
        if self.fail:
            self.fail = False
            raise RuntimeError('Injected single selection write failure')
        self._selected = value


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.api = ownership_api()
        self.original_limb = selection._limb
        selection._limb = lambda: self.api
        self.addCleanup(setattr, selection, '_limb', self.original_limb)
        self.rig = ID(type='ARMATURE', data=ID(bones=Bones()), pose=NS(bones=Bones()),
                      animation_data=NoTouch())
        self.rig.data[self.api.ARMATURE_ID_KEY] = 'this-armature'
        self.context = NS(mode='POSE', selected_pose_bones=[], scene=NoTouch(),
                          view_layer=NS(objects=NS(active=self.rig)))
        self.arms = {}
        for side in ('L', 'R'):
            chain = tuple(name + '.' + side for name in ('upper_arm', 'forearm', 'hand'))
            target, pole = 'CTRL_hand_IK.' + side, 'CTRL_elbow_pole.' + side
            rig_id = 'owned-arm-' + side
            for name in (*chain, target, pole):
                self.add_bone(name)
            for name, role in ((target, 'HAND_IK'), (pole, 'POLE')):
                self.rig.data.bones[name].update({
                    self.api.OWNER_KEY: self.api.OWNER_VALUE,
                    self.api.VERSION_KEY: self.api.RIG_VERSION,
                    self.api.ARMATURE_ID_KEY: 'this-armature',
                    self.api.RIG_ID_KEY: rig_id, self.api.ROLE_KEY: role,
                    self.api.KIND_KEY: 'ARM', self.api.SIDE_KEY: side,
                    self.api.CHAIN_KEY: json.dumps(chain)})
            for name, role in zip(chain, ('SOURCE_UPPER_ROTATION', 'IK', 'END_ROTATION')):
                record = dict(owner=self.api.OWNER_VALUE, version=self.api.RIG_VERSION,
                              armature_id='this-armature', rig_id=rig_id, kind='ARM', side=side,
                              chain=list(chain), target=target, pole=pole, role=role)
                self.rig.pose.bones[name].properties[self.api.CONSTRAINT_REGISTRY_KEY] = json.dumps({
                    self.api._constraint_name(rig_id, role): record})
            self.arms[side] = dict(chain=chain, target=target, pole=pole, rig_id=rig_id)
        self.add_bone('head')
        self.add_bone('CTRL_foot_IK.L')

    def add_bone(self, name):
        bone = ID(name=name, hide=False, hide_select=False, is_visible=True,
                  collections=[NS(is_visible_effectively=True)])
        self.rig.data.bones[name] = bone
        self.rig.pose.bones[name] = Pose(bone)

    def choose(self, names, active=None):
        self.context.selected_pose_bones = [self.rig.pose.bones[name] for name in names]
        for pose in self.context.selected_pose_bones:
            pose.select = True
        self.rig.data.bones.active = self.rig.data.bones.get(active or next(iter(names), ''))
        return selection.capture(self.context, self.rig)

    def selected(self):
        return {name for name in self.rig.pose.bones.keys() if self.rig.pose.bones[name].select}

    def test_fk_target_and_pole_merge_to_wrist_preserving_other_selection(self):
        left = self.arms['L']
        before = self.choose([left['target'], left['pole'], 'head', 'CTRL_foot_IK.L'])
        # Visibility synchronization can hide/deselect a source before handoff.
        self.rig.pose.bones[left['target']].hide = True
        self.rig.pose.bones[left['target']].select = False
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 2)
        self.assertEqual(self.selected(), {left['chain'][2], 'head', 'CTRL_foot_IK.L'})
        self.assertIs(self.rig.data.bones.active, self.rig.data.bones[left['chain'][2]])

    def test_ik_three_fk_controls_merge_to_target(self):
        left = self.arms['L']
        before = self.choose([*left['chain'], 'head'], active=left['chain'][1])
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'IK'), 3)
        self.assertEqual(self.selected(), {left['target'], 'head'})
        self.assertEqual(self.rig.data.bones.active.name, left['target'])

    def test_both_arms_transfer_and_unrelated_active_stays(self):
        before = self.choose([self.arms['L']['target'], self.arms['R']['pole'], 'head'], active='head')
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 2)
        self.assertEqual(self.selected(), {'hand.L', 'hand.R', 'head'})
        self.assertEqual(self.rig.data.bones.active.name, 'head')

    def test_explicit_keys_limit_to_one_arm_and_never_leg(self):
        before = self.choose([self.arms['L']['target'], self.arms['R']['target'], 'CTRL_foot_IK.L'])
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK',
                                          keys=(('ARM', 'L'), ('LEG', 'L'))), 1)
        self.assertEqual(self.selected(), {'hand.L', self.arms['R']['target'], 'CTRL_foot_IK.L'})

    def test_fk_keeps_existing_native_bone_selection(self):
        before = self.choose(['upper_arm.L', 'head'])
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 0)
        self.assertEqual(self.selected(), {'upper_arm.L', 'head'})

    def test_unavailable_destination_preserves_source_and_active(self):
        for field in ('bone_hide', 'pose_hide', 'select_lock', 'bone_invisible',
                      'pose_invisible', 'collection_hidden'):
            with self.subTest(field=field):
                target, wrist = self.arms['L']['target'], self.rig.pose.bones['hand.L']
                before = self.choose([target])
                if field == 'bone_hide': wrist.bone.hide = True
                if field == 'pose_hide': wrist.hide = True
                if field == 'select_lock': wrist.bone.hide_select = True
                if field == 'bone_invisible': wrist.bone.is_visible = False
                if field == 'pose_invisible': wrist.is_visible = False
                if field == 'collection_hidden': wrist.bone.collections[0].is_visible_effectively = False
                self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 0)
                self.assertEqual(self.selected(), {target})
                self.assertEqual(self.rig.data.bones.active.name, target)
                wrist.bone.hide, wrist.hide, wrist.bone.hide_select = False, False, False
                wrist.bone.is_visible, wrist.is_visible = True, True
                wrist.bone.collections[0].is_visible_effectively = True

    def test_missing_destination_preserves_source(self):
        target = self.arms['L']['target']
        before = self.choose([target])
        self.rig.pose.bones.pop('hand.L')
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 0)
        self.assertTrue(self.rig.pose.bones[target].select)

    def test_metadata_change_after_capture_rejects_handoff(self):
        target = self.arms['L']['target']
        for field, value in ((self.api.OWNER_KEY, 'foreign'), (self.api.RIG_ID_KEY, 'replaced'),
                             (self.api.VERSION_KEY, 99), (self.api.SIDE_KEY, 'R'),
                             (self.api.CHAIN_KEY, json.dumps(['other', 'chain', 'bones']))):
            with self.subTest(field=field):
                original = self.rig.data.bones[target][field]
                before = self.choose([target])
                self.rig.data.bones[target][field] = value
                self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 0)
                self.assertEqual(self.selected(), {target})
                self.rig.data.bones[target][field] = original

    def test_malformed_or_foreign_native_registry_does_not_guess(self):
        pb = self.rig.pose.bones['upper_arm.L']
        for raw in ('broken json', '{}', json.dumps({'foreign': dict(owner='foreign')})):
            with self.subTest(raw=raw):
                pb.properties[self.api.CONSTRAINT_REGISTRY_KEY] = raw
                before = self.choose([pb.name])
                self.assertEqual(before['rigs'], {})
                self.assertEqual(selection.handoff(self.context, self.rig, before, 'IK'), 0)

    def test_generated_control_without_native_record_is_not_trusted(self):
        left = self.arms['L']
        for name in left['chain']:
            self.rig.pose.bones[name].properties.clear()
        before = self.choose([left['target']])
        self.assertEqual(before['rigs'], {})
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 0)

    def test_context_change_or_wrong_armature_is_noop(self):
        before = self.choose([self.arms['L']['target']])
        self.context.mode = 'OBJECT'
        self.assertIsNone(selection.capture(self.context, self.rig))
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 0)
        self.context.mode = 'POSE'
        self.context.view_layer.objects.active = NS()
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 0)

    def test_selection_write_failure_rolls_back_bounded_participants(self):
        target = self.arms['L']['target']
        before = self.choose([target, 'head'])
        # Destination has been selected before source deselection fails.
        self.rig.pose.bones[target].fail = True
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 0)
        self.assertEqual(self.selected(), {target, 'head'})
        self.assertEqual(self.rig.data.bones.active.name, target)

    def test_standard_preverified_inventory_can_be_passed(self):
        left = self.arms['L']
        before = self.choose([left['target']])
        inventory = dict(rigs={('ARM', 'L'): dict(chain=left['chain'],
                         target=self.rig.data.bones[left['target']], pole=self.rig.data.bones[left['pole']])})
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK', inventory=inventory), 1)
        self.assertEqual(self.selected(), {'hand.L'})

    def test_repeated_current_mode_repairs_stale_target_selection(self):
        # No changed-mode flag is required; an explicit repeated click can repair selection.
        before = self.choose([self.arms['L']['target']])
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 1)
        again = selection.capture(self.context, self.rig)
        self.assertEqual(selection.handoff(self.context, self.rig, again, 'FK'), 0)

    def test_unselected_active_is_transferred_without_selecting_it(self):
        target = self.arms['L']['target']
        self.rig.data.bones.active = self.rig.data.bones[target]
        before = selection.capture(self.context, self.rig)
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 1)
        self.assertEqual(self.selected(), set())
        self.assertEqual(self.rig.data.bones.active.name, 'hand.L')

    def test_foreign_chain_record_does_not_override_owned_side(self):
        target = self.arms['L']['target']
        left = self.rig.pose.bones['hand.L']
        right = self.rig.pose.bones['hand.R']
        records = json.loads(left.properties[self.api.CONSTRAINT_REGISTRY_KEY])
        conflicting = next(iter(json.loads(right.properties[self.api.CONSTRAINT_REGISTRY_KEY]).values()))
        # Side L with a different owned rig cannot be silently guessed as this arm.
        conflicting['side'] = 'L'
        records[self.api._constraint_name(conflicting['rig_id'], conflicting['role'])] = conflicting
        left.properties[self.api.CONSTRAINT_REGISTRY_KEY] = json.dumps(records)
        before = self.choose([target])
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'FK'), 1)
        self.assertEqual(self.selected(), {'hand.L'})

    def direct_record(self):
        left = self.arms['L']
        self.rig.pose.bones['upper_arm.L'].properties.clear()
        self.rig.data[self.api.SCHEMA_KEY] = self.api.DIRECT_PREROLL_SCHEMA
        return dict(version=self.api.DIRECT_PREROLL_RESULT_VERSION,
                    limbs={left['rig_id']: dict(kind='ARM', side='L', chain=list(left['chain']),
                                               original='not inspected', applied='not inspected')})

    def test_direct_upper_arm_uses_only_saved_chain_to_find_owned_records(self):
        payload = self.direct_record()
        self.rig.data[self.api.DIRECT_REST_KEY] = json.dumps(payload)
        before = self.choose(['upper_arm.L', 'head'])
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'IK'), 1)
        self.assertEqual(self.selected(), {self.arms['L']['target'], 'head'})

    def test_invalid_direct_registry_cannot_guess_an_upper_arm_mapping(self):
        original = self.direct_record()
        duplicate = dict(original, limbs=dict(original['limbs']))
        duplicate['limbs']['duplicate'] = next(iter(original['limbs'].values()))
        excessive = dict(original, limbs={str(i): {} for i in range(5)})
        mismatch = json.loads(json.dumps(original))
        next(iter(mismatch['limbs'].values()))['chain'] = ['upper_arm.L', 'forearm.R', 'hand.R']
        for raw in ('broken', json.dumps(duplicate), json.dumps(excessive),
                    json.dumps(mismatch), ' ' * (128 * 1024 + 1)):
            with self.subTest(raw=raw[:80]):
                self.rig.data[self.api.DIRECT_REST_KEY] = raw
                before = self.choose(['upper_arm.L'])
                self.assertEqual(selection.handoff(self.context, self.rig, before, 'IK'), 0)
                self.assertEqual(self.selected(), {'upper_arm.L'})

    def test_direct_registry_is_ignored_for_other_schemas(self):
        payload = self.direct_record()
        self.rig.data[self.api.DIRECT_REST_KEY] = json.dumps(payload)
        self.rig.data[self.api.SCHEMA_KEY] = 3
        before = self.choose(['upper_arm.L'])
        self.assertEqual(selection.handoff(self.context, self.rig, before, 'IK'), 0)


if __name__ == '__main__':
    unittest.main()
