"""Display-only FK controls on an ordered RNA model; no Blender worker needed.

The product build/display transactions and its real source-shape helpers run
against small ID collections. Native RNA/selection/keying remains a separate
Blender test; these checks cover migration, ownership and bounded idle work.
"""
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


SOURCE = Path(__file__).resolve().parents[1] / 'addons/character_designer'


class ID(dict):
    def __init__(self, **fields):
        super().__init__()
        self.__dict__.update(fields)


class Named(dict):
    def __iter__(self):
        return iter(self.values())


class PoseBone:
    def __init__(self, name, length):
        self.name, self.bone = name, ID(name=name, length=length)
        self.custom_shape, self.custom_shape_transform = None, None
        self.use_custom_shape_bone_size = True
        self.custom_shape_scale_xyz = (1., 1., 1.)
        self.custom_shape_translation = (.01, .02, .03)
        self.custom_shape_rotation_euler = (.1, .2, .3)
        self.custom_shape_wire_width = 3.
        self.matrix = [[float(i == j) for j in range(4)] for i in range(4)]

    def path_from_id(self, field):
        return f'pose.bones["{self.name}"].{field}'


class LimbFKVisualTests(unittest.TestCase):
    def setUp(self):
        self.objects, self.meshes, self.collections = Named(), Named(), Named()
        self.bpy = NS(data=NS(objects=self.objects, meshes=self.meshes,
                              collections=self.collections))
        package = ModuleType('_fk_visuals_test')
        package.__path__ = []
        package.generated_names = NS(
            collection_name=lambda rig, role: role,
            widget_name=lambda rig, role: role)
        package.validation_json = NS(decode=json.loads)
        # These transaction tests model the wrist planner as an independent
        # service. Its actual source/geometry guards have their own pure/native
        # tests; here the non-default vectors prove build uses its result.
        self.hand_display = {'custom_shape': '', 'custom_shape_transform': '',
            'use_bone_size': False, 'scale': [.12] * 3,
            'translation': [.002, .003, .004], 'rotation': [.2, .3, .4], 'wire_width': 2.}
        self.hand_plan = Mock(side_effect=lambda armature, rig: deepcopy(self.hand_display))
        package.limb_fk_wrist_visuals = NS(plan=self.hand_plan, UnsupportedSource=ValueError)
        self.mode = Mock(side_effect=lambda rig, item: item['mode'])
        package.limb_ik_fk = NS(mode_for_rig=self.mode)
        modules = patch.dict(sys.modules, {'bpy': self.bpy, package.__name__: package})
        modules.start()
        self.addCleanup(modules.stop)
        spec = importlib.util.spec_from_file_location(
            package.__name__ + '.limb_fk_visuals', SOURCE / 'limb_fk_visuals.py')
        self.visuals = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.visuals)

        # Reuse real display serialization/validation/restore, not a no-op model.
        helper_names = {'_pose_shape_json_state', '_pose_shape_runtime_state',
                        '_shape_vector', '_validate_shape_state', '_shape_values_match',
                        '_restore_pose_shape_state', '_apply_source_widget_generated_state'}
        tree = ast.parse((SOURCE / 'limb_ik.py').read_text(encoding='utf-8-sig'))
        definitions = [node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name in helper_names]
        self.assertEqual({node.name for node in definitions}, helper_names)
        self.limb = NS(LimbIKError=ValueError, ARMATURE_ID_KEY='armature_id',
                       GENERATED_CONTROL_OWNERS=set(), math=math, bpy=self.bpy)
        exec(compile(ast.Module(body=definitions, type_ignores=[]),
                     str(SOURCE / 'limb_ik.py'), 'exec'), self.limb.__dict__)
        self.curves = []
        self.limb._actions_for_id = Mock(return_value=['Existing Action'])
        self.limb._fcurves_for_action = Mock(side_effect=lambda action: self.curves)
        self.visuals._limb = lambda: self.limb
        self.visuals._active, self.visuals._update = Mock(), Mock()
        self.created = []
        self.visuals._create_widget = self.create_widget
        self.visuals._delete_resources = self.delete_resources
        self.rig = ID(data=ID(), pose=NS(bones=Named()), animation_data=NS(drivers=[]))
        self.rig.data['armature_id'] = 'rig-1'
        self.inventory = {'armature_id': 'rig-1', 'rigs': {}}
        for kind in ('ARM', 'LEG'):
            for side in ('L', 'R'):
                chain = [f'{kind}_{side}_{index}' for index in range(3)]
                self.inventory['rigs'][kind, side] = {'chain': chain, 'mode': 'FK'}
                for index, name in enumerate(chain):
                    self.rig.pose.bones[name] = PoseBone(name, .3 if index < 2 else .04)
        self.limb._validate_inventory = Mock(return_value=self.inventory)

    def create_widget(self, context, armature, record, name, entry):
        visuals = self.visuals
        if record['collection'] not in self.collections:
            collection = ID(name=record['collection'])
            visuals._tag(collection, record, 'COLLECTION')
            self.collections[collection.name] = collection
        mesh = ID(name=entry['mesh'])
        widget = ID(name=entry['object'], type='MESH', data=mesh)
        visuals._tag(widget, record, name)
        visuals._tag(mesh, record, name)
        self.objects[widget.name], self.meshes[mesh.name] = widget, mesh
        pb = armature.pose.bones[name]
        pb.custom_shape = widget
        self.limb._apply_source_widget_generated_state(pb, entry['generated'])
        self.created.append(name)

    def delete_resources(self, record, names):
        for name in names:
            entry = record['bindings'][name]
            self.objects.pop(entry['object'], None)
            self.meshes.pop(entry['mesh'], None)
        if not self.objects:
            self.collections.pop(record['collection'], None)

    def displays(self):
        return {name: self.limb._pose_shape_json_state(pb)
                for name, pb in self.rig.pose.bones.items()}

    def build(self):
        return self.visuals.build(None, self.rig)

    def legacy(self):
        # Create the exact old two-joint registry using the existing setup API.
        policy = lambda kind, chain: chain[:2]
        with patch.object(self.visuals, 'binding_names', side_effect=policy):
            record = self.build()
        self.assertEqual(len(record['bindings']), 8)
        return record

    def detach(self, record, names):
        marker = {}
        for name in names:
            self.rig.pose.bones[name].custom_shape = None
            marker[name] = record['bindings'][name]['object']
        self.rig.data[self.visuals.NATIVE_DISPLAY_KEY] = json.dumps(marker)

    def test_new_build_adds_native_arm_wrist_without_pose_or_existing_id_changes(self):
        before_bones = dict(self.rig.pose.bones)
        before_pose = {name: deepcopy(pb.matrix) for name, pb in before_bones.items()}
        before_displays = self.displays()
        record = self.build()
        self.assertEqual(len(record['bindings']), 10)
        self.assertEqual(dict(self.rig.pose.bones), before_bones)
        self.assertEqual({name: pb.matrix for name, pb in before_bones.items()}, before_pose)
        for (kind, _side), item in self.inventory['rigs'].items():
            wrist = item['chain'][2]
            if kind == 'ARM':
                self.assertIn(wrist, record['bindings'])
                pb = self.rig.pose.bones[wrist]
                self.assertEqual(record['bindings'][wrist]['shape_kind'], 'HAND')
                self.assertEqual(record['bindings'][wrist]['generated'], self.hand_display)
                self.assertEqual(list(pb.custom_shape_scale_xyz), self.hand_display['scale'])
                self.assertEqual(record['bindings'][wrist]['original'], before_displays[wrist])
            else:
                self.assertNotIn(wrist, record['bindings'])
        self.assertEqual(self.visuals.validate(self.rig, self.inventory), record)

    def test_legacy_registry_upgrade_adds_only_two_wrists_and_is_idempotent(self):
        old = self.legacy()
        old_entries, old_widgets = deepcopy(old['bindings']), dict(self.objects)
        self.detach(old, old['bindings'])
        marker = self.rig.data[self.visuals.NATIVE_DISPLAY_KEY]
        self.created.clear()
        upgraded = self.build()
        wrists = {item['chain'][2] for key, item in self.inventory['rigs'].items() if key[0] == 'ARM'}
        self.assertEqual(set(self.created), wrists)
        self.assertEqual({name: upgraded['bindings'][name] for name in old_entries}, old_entries)
        self.assertTrue(all(self.objects[name] is obj for name, obj in old_widgets.items()))
        self.assertEqual(self.rig.data[self.visuals.NATIVE_DISPLAY_KEY], marker)
        self.created.clear()
        self.limb._actions_for_id.reset_mock()
        self.assertEqual(self.build(), upgraded)
        self.assertEqual(self.created, [])
        self.limb._actions_for_id.assert_not_called()
        self.assertEqual(self.visuals.sync_native_display(self.rig, self.inventory), 0)
        remaining = json.loads(self.rig.data[self.visuals.NATIVE_DISPLAY_KEY])
        self.assertEqual(set(remaining), set(old_entries))
        for name in wrists:
            self.assertIs(self.rig.pose.bones[name].custom_shape,
                          self.objects[upgraded['bindings'][name]['object']])

    def test_upgrade_failure_restores_old_registry_marker_display_and_resources(self):
        old = self.legacy()
        self.detach(old, old['bindings'])
        before_data, before_displays = dict(self.rig.data), self.displays()
        before_objects, before_meshes = dict(self.objects), dict(self.meshes)
        create = self.visuals._create_widget
        def fail_after_create(*args):
            create(*args)
            raise RuntimeError('injected wrist creation failure')
        with patch.object(self.visuals, '_create_widget', side_effect=fail_after_create):
            with self.assertRaisesRegex(RuntimeError, 'injected'):
                self.build()
        self.assertEqual(dict(self.rig.data), before_data)
        self.assertEqual(self.displays(), before_displays)
        self.assertEqual(dict(self.objects), before_objects)
        self.assertEqual(dict(self.meshes), before_meshes)
        self.visuals.validate(self.rig, self.inventory)

    def test_existing_wrist_shape_transform_and_animation_are_preserved(self):
        wrists = [item['chain'][2] for key, item in self.inventory['rigs'].items() if key[0] == 'ARM']
        left, right = (self.rig.pose.bones[name] for name in wrists)
        artist = ID(name='Artist wrist')
        left.custom_shape = artist
        right.custom_shape_transform = left
        before = self.displays()
        record = self.build()
        self.assertEqual(set(record['skipped']), set(wrists))
        self.assertTrue(all(name not in record['bindings'] for name in wrists))
        self.assertEqual({name: self.displays()[name] for name in wrists},
                         {name: before[name] for name in wrists})
        self.assertIs(left.custom_shape, artist)
        self.assertIs(right.custom_shape_transform, left)
        # A previously skipped wrist becomes eligible only after explicit artist
        # removal and another build; animated display remains protected.
        left.custom_shape, right.custom_shape_transform = None, None
        self.curves.append(NS(data_path=left.path_from_id('custom_shape_scale_xyz')))
        updated = self.build()
        self.assertIn(wrists[0], updated['skipped'])
        self.assertNotIn(wrists[0], updated['bindings'])
        self.assertIn(wrists[1], updated['bindings'])

    def test_arm_upper_lower_use_native_fk_while_wrist_widgets_remain_attached(self):
        record = self.build()
        arm_keys = [key for key in self.inventory['rigs'] if key[0] == 'ARM']
        before = self.displays()
        names = {name for key in arm_keys for name in self.inventory['rigs'][key]['chain']}
        native_names = {name for key in arm_keys for name in self.inventory['rigs'][key]['chain'][:2]}
        self.assertEqual(len(names), 6)
        self.assertEqual(len(native_names), 4)
        resources = {name: id(obj) for name, obj in self.objects.items()}
        was_native = False
        for mode in ('FK', 'IK', 'BLEND', 'FK'):
            for key in arm_keys:
                self.inventory['rigs'][key]['mode'] = mode
            native = mode == 'FK'
            self.assertEqual(self.visuals.sync_native_display(
                self.rig, self.inventory, keys=arm_keys), 4 if native != was_native else 0)
            for name in names:
                pb = self.rig.pose.bones[name]
                self.assertIs(pb.custom_shape, None if native and name in native_names
                              else self.objects[record['bindings'][name]['object']])
                current = self.displays()[name]
                self.assertEqual({key: value for key, value in current.items() if key != 'custom_shape'},
                                 {key: value for key, value in before[name].items() if key != 'custom_shape'})
            marker = json.loads(self.rig.data.get(self.visuals.NATIVE_DISPLAY_KEY, '{}'))
            self.assertEqual(set(marker), native_names if native else set())
            # Repeated display/key/frame consumers do not revisit Actions when
            # the mode and exact owned shape assignment already agree.
            self.limb._actions_for_id.reset_mock()
            self.assertEqual(self.visuals.sync_native_display(
                self.rig, self.inventory, keys=arm_keys), 0)
            self.limb._actions_for_id.assert_not_called()
            self.assertEqual({name: id(obj) for name, obj in self.objects.items()}, resources)
            was_native = native
        self.assertEqual(self.visuals.validate(self.rig, self.inventory), record)

    def test_fk_detach_guard_and_owned_artist_transform_refuse_without_partial_changes(self):
        record = self.build()
        arm_keys = tuple(key for key in self.inventory['rigs'] if key[0] == 'ARM')
        name = self.inventory['rigs'][('ARM', 'R')]['chain'][1]
        pb = self.rig.pose.bones[name]
        before = self.displays()
        self.curves.append(NS(data_path=pb.path_from_id('custom_shape_scale_xyz')))
        with self.assertRaisesRegex(ValueError, 'animated'):
            self.visuals.sync_native_display(self.rig, self.inventory, keys=arm_keys)
        self.assertEqual(self.displays(), before)
        self.assertNotIn(self.visuals.NATIVE_DISPLAY_KEY, self.rig.data)
        self.curves.clear()
        self.assertEqual(self.visuals.sync_native_display(self.rig, self.inventory, keys=arm_keys), 4)
        raw = self.rig.data[self.visuals.NATIVE_DISPLAY_KEY]
        anchor = self.rig.pose.bones[self.inventory['rigs'][('ARM', 'L')]['chain'][0]]
        pb.custom_shape_transform = anchor
        for key in arm_keys:
            self.inventory['rigs'][key]['mode'] = 'IK'
        self.limb._actions_for_id.reset_mock()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.visuals.sync_native_display(self.rig, self.inventory, keys=arm_keys)
        self.assertIs(pb.custom_shape_transform, anchor)
        self.assertEqual(self.rig.data[self.visuals.NATIVE_DISPLAY_KEY], raw)
        self.assertTrue(all(self.rig.pose.bones[source].custom_shape is None
                            for key in arm_keys for source in self.inventory['rigs'][key]['chain'][:2]))
        self.assertTrue(all(self.rig.pose.bones[self.inventory['rigs'][key]['chain'][2]].custom_shape
                            is self.objects[record['bindings'][self.inventory['rigs'][key]['chain'][2]]['object']]
                            for key in arm_keys))
        pb.custom_shape_transform = None
        self.visuals.validate(self.rig, self.inventory)

    def test_legacy_detached_wrist_is_repaired_in_fk_with_selected_scope_only(self):
        record = self.build()
        self.detach(record, record['bindings'])
        key = ('ARM', 'R')
        wrist = self.inventory['rigs'][key]['chain'][2]
        before = self.displays()
        before_marker = json.loads(self.rig.data[self.visuals.NATIVE_DISPLAY_KEY])
        resources = {name: id(obj) for name, obj in self.objects.items()}
        self.assertEqual(self.visuals.sync_native_display(self.rig, self.inventory, keys=(key,)), 1)
        expected = deepcopy(before)
        expected[wrist]['custom_shape'] = record['bindings'][wrist]['object']
        self.assertEqual(self.displays(), expected)
        before_marker.pop(wrist)
        self.assertEqual(json.loads(self.rig.data[self.visuals.NATIVE_DISPLAY_KEY]), before_marker)
        self.assertEqual({name: id(obj) for name, obj in self.objects.items()}, resources)
        self.visuals.validate(self.rig, self.inventory)
        self.limb._actions_for_id.reset_mock()
        self.assertEqual(self.visuals.sync_native_display(self.rig, self.inventory, keys=(key,)), 0)
        self.limb._actions_for_id.assert_not_called()

    def test_animated_legacy_wrist_migration_refuses_without_partial_changes(self):
        record = self.build()
        self.detach(record, record['bindings'])
        wrist = self.inventory['rigs'][('ARM', 'R')]['chain'][2]
        self.curves.append(NS(data_path=self.rig.pose.bones[wrist].path_from_id('custom_shape_scale_xyz')))
        before, data = self.displays(), dict(self.rig.data)
        resources = {name: id(obj) for name, obj in self.objects.items()}
        with self.assertRaisesRegex(ValueError, 'animated'):
            self.visuals.sync_native_display(self.rig, self.inventory)
        self.assertEqual(self.displays(), before)
        self.assertEqual(dict(self.rig.data), data)
        self.assertEqual({name: id(obj) for name, obj in self.objects.items()}, resources)
        self.visuals.validate(self.rig, self.inventory)

    def test_mixed_left_ik_right_fk_and_legs_fk_respects_selected_limb_scope(self):
        record = self.build()
        left, right = ('ARM', 'L'), ('ARM', 'R')
        self.inventory['rigs'][left]['mode'] = 'IK'
        self.visuals.sync_native_display(self.rig, self.inventory)
        marker = json.loads(self.rig.data[self.visuals.NATIVE_DISPLAY_KEY])
        expected_native = {name for key, item in self.inventory['rigs'].items()
                           if key != left for name in item['chain'][:2]}
        self.assertEqual(set(marker), expected_native)
        for key, item in self.inventory['rigs'].items():
            for name in self.visuals.binding_names(key[0], item['chain']):
                self.assertIs(self.rig.pose.bones[name].custom_shape,
                              None if name in expected_native else self.objects[record['bindings'][name]['object']])
        # Change one limb's evaluated mode and apply only that limb's display;
        # the left arm and both legs retain their exact display and marker data.
        unrelated = {name: state for name, state in self.displays().items()
                     if name not in self.inventory['rigs'][right]['chain']}
        self.inventory['rigs'][right]['mode'] = 'IK'
        self.assertEqual(self.visuals.sync_native_display(self.rig, self.inventory, keys=(right,)), 2)
        self.assertEqual({name: self.displays()[name] for name in unrelated}, unrelated)
        self.assertEqual(set(json.loads(self.rig.data[self.visuals.NATIVE_DISPLAY_KEY])),
                         expected_native - set(self.inventory['rigs'][right]['chain'][:2]))
        self.visuals.validate(self.rig, self.inventory)

    def test_legacy_display_migration_is_scoped_and_preserves_animated_shapes(self):
        record = self.build()
        self.detach(record, record['bindings'])
        before, raw = self.displays(), self.rig.data[self.visuals.NATIVE_DISPLAY_KEY]
        left_key = ('ARM', 'L')
        self.inventory['rigs'][left_key]['mode'] = 'IK'
        source = self.inventory['rigs'][left_key]['chain'][2]
        self.curves.append(NS(data_path=self.rig.pose.bones[source].path_from_id('custom_shape_scale_xyz')))
        with self.assertRaisesRegex(ValueError, 'animated'):
            self.visuals.sync_native_display(self.rig, self.inventory, keys=(left_key,))
        self.assertEqual(self.displays(), before)
        self.assertEqual(self.rig.data[self.visuals.NATIVE_DISPLAY_KEY], raw)
        self.curves.clear()
        self.assertEqual(self.visuals.sync_native_display(self.rig, self.inventory, keys=(left_key,)), 3)
        after = self.displays()
        for name, entry in record['bindings'].items():
            if (entry['kind'], entry['side']) == left_key:
                self.assertEqual(after[name]['custom_shape'], entry['object'])
            else:
                self.assertEqual(after[name], before[name])
        self.visuals.validate(self.rig, self.inventory)

    def test_display_failure_rolls_back_and_original_snapshot_restores_exactly(self):
        record = self.build()
        self.detach(record, record['bindings'])
        for key, item in self.inventory['rigs'].items():
            if key[0] == 'ARM':
                item['mode'] = 'IK'
        saved = self.visuals.native_display_snapshot(self.rig)
        before = self.displays()
        data_type = type(self.rig.data)
        class FailOnce(data_type):
            fail = True
            def __setitem__(self, key, value):
                if key == 'character_designer_limb_fk_native_display_v1' and self.fail:
                    self.fail = False
                    raise RuntimeError('injected marker assignment')
                super().__setitem__(key, value)
        data = FailOnce()
        dict.update(data, self.rig.data)
        self.rig.data = data
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            self.visuals.sync_native_display(self.rig, self.inventory)
        self.assertEqual(self.displays(), before)
        self.assertEqual(self.visuals.native_display_snapshot(self.rig), saved)
        self.visuals.sync_native_display(self.rig, self.inventory)
        self.visuals.restore_native_display(self.rig, saved)
        self.assertEqual(self.displays(), before)
        self.assertEqual(self.visuals.native_display_snapshot(self.rig), saved)

    def test_fk_detach_write_failure_restores_owned_pointers_and_absent_marker(self):
        record = self.build()
        before = self.displays()
        shapes = {name: self.rig.pose.bones[name].custom_shape for name in record['bindings']}
        with patch.object(self.visuals.json, 'dumps', side_effect=RuntimeError('injected FK marker write')):
            with self.assertRaisesRegex(RuntimeError, 'injected FK marker write'):
                self.visuals.sync_native_display(self.rig, self.inventory)
        self.assertEqual(self.displays(), before)
        self.assertNotIn(self.visuals.NATIVE_DISPLAY_KEY, self.rig.data)
        self.assertTrue(all(self.rig.pose.bones[name].custom_shape is widget for name, widget in shapes.items()))
        self.visuals.validate(self.rig, self.inventory)

    def test_registry_accepts_old_arm_bindings_but_never_a_leg_end_or_foreign_shape(self):
        record = self.legacy()
        self.assertEqual(self.visuals.get_record(self.rig), record)
        leg = self.inventory['rigs']['LEG', 'L']['chain']
        record['bindings'][leg[2]] = deepcopy(record['bindings'][leg[1]])
        self.rig.data[self.visuals.RECORD_KEY] = json.dumps(record)
        with self.assertRaisesRegex(ValueError, 'invalid'):
            self.visuals.get_record(self.rig)
        del record['bindings'][leg[2]]
        self.rig.data[self.visuals.RECORD_KEY] = json.dumps(record)
        source = self.inventory['rigs']['ARM', 'L']['chain'][0]
        artist = ID(name='Foreign replacement')
        self.rig.pose.bones[source].custom_shape = artist
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.visuals.sync_native_display(self.rig, self.inventory, keys=(('ARM', 'L'),))
        self.assertIs(self.rig.pose.bones[source].custom_shape, artist)


if __name__ == '__main__':
    unittest.main()
