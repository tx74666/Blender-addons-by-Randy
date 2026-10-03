"""Direct native posing, persistent recovery, and exact control ownership."""
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
from character_designer import body_original_mode as original, body_setup, bone_collections
from character_designer import limb_ik, limb_ik_fk, control_pose_assets as poses, bone_display
import test_body_calibration_blender as setup
from test_control_pose_weights_blender import matrices


class OriginalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        character_designer.register()

    def setUp(self):
        self.rig, self.mesh = setup.fixture()
        bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
        setup.prepare(self.rig)
        body_setup.generate(bpy.context, self.rig)

    def assertPose(self, expected, limit=4e-4):
        actual = matrices(self.rig)
        errors = {name: poses._difference(matrix, actual[name]) for name, matrix in expected.items()}
        self.assertLess(max(errors.values()), limit, errors)

    def assertContentEqual(self, actual, expected):
        """Keep large protected-content failures useful without truncating evidence."""
        differences = []
        def compare(left, right, path='content'):
            if len(differences) >= 12:
                return
            if isinstance(left, dict) and isinstance(right, dict):
                for key in sorted(left.keys() | right.keys()):
                    child = f'{path}[{key!r}]'
                    if key not in left or key not in right:
                        differences.append(f'{child}: present actual={key in left}, expected={key in right}')
                    else:
                        compare(left[key], right[key], child)
            elif isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
                if len(left) != len(right):
                    differences.append(f'{path}: length actual={len(left)}, expected={len(right)}')
                for index, (left_item, right_item) in enumerate(zip(left, right)):
                    compare(left_item, right_item, f'{path}[{index}]')
            elif left != right:
                differences.append(f'{path}: actual={left!r}, expected={right!r}')
        compare(actual, expected)
        self.assertFalse(differences, '\n'.join(differences))

    def unified_fixture(self):
        """Add bound Hair/Dress and a foreign character to the calibrated Body."""
        import test_bone_display_blender as display_test
        from character_designer import skirt_rig
        rig = self.rig
        hair_mesh, plans = display_test.make_hair('Original Hair', strands=1)
        hair = display_test.build_hair(hair_mesh, plans, armature=rig, parent_bone='Chest')
        bone_collections.simplify_body_collections(rig)
        dress_mesh = display_test.frustum('Original Dress', rows=6, sides=16)
        record = skirt_rig.build_skirt(bpy.context, dress_mesh, armature=rig, shared=False)
        dress = dress_mesh[skirt_rig.RIG_KEY]
        other = display_test.base.make_humanoid('Foreign Original Character')
        bone_collections.simplify_body_collections(other)
        foreign_mesh = display_test.frustum('Foreign Original Dress', rows=6, sides=16)
        skirt_rig.build_skirt(bpy.context, foreign_mesh, armature=other, shared=False)
        foreign_dress = foreign_mesh[skirt_rig.RIG_KEY]
        display_test.activate(rig, 'POSE')
        expected = {
            'BODY': {rig: set(rig.data.collections_all['Original'].bones.keys())},
            'HAIR': {rig: {name for chain in hair['chains'] for name in chain['bones']}},
            'DRESS': {dress: skirt_rig._bone_collection_layout(record)[1] | {record['controls']['waist']}},
        }
        self.assertTrue(all(any(names for names in targets.values()) for targets in expected.values()))
        return display_test, (rig, dress, other, foreign_dress), expected

    def test_unified_original_groups_and_explicit_modes_preserve_pose(self):
        display_test, rigs, expected = self.unified_fixture()
        rig, dress, other, foreign_dress = rigs
        before_pose, before_view = matrices(rig), display_test.views(rigs)
        before_content = display_test.content(rigs)
        channels = original._channels(rig)
        dress_channels = original._channels(dress)
        foreign_view = display_test.views((other, foreign_dress))
        foreign_channels = {target.name: original._channels(target) for target in (other, foreign_dress)}
        self.assertEqual(bpy.ops.character_designer.body_original_mode(action='ORIGINAL'), {'FINISHED'})
        self.assertTrue(original.active(rig))
        self.assertPose(before_pose)
        native_main = expected['BODY'][rig] | expected['HAIR'][rig]
        self.assertEqual(display_test.visible(rig), native_main)
        self.assertEqual(display_test.visible(dress), expected['DRESS'][dress])
        self.assertFalse(rig.data.show_bone_custom_shapes or dress.data.show_bone_custom_shapes)
        self.assertEqual(display_test.views((other, foreign_dress)), foreign_view)
        self.assertEqual({target.name: original._channels(target) for target in (other, foreign_dress)}, foreign_channels)
        session = rig[original.SESSION]
        entered_channels = original._channels(rig)
        # Original exposes a native local correction basis for Dress; display
        # toggles must preserve that entered state. Controls restores the exact
        # preceding channels, which are asserted separately below.
        entered_dress_channels = original._channels(dress)
        # Clicking the already-blue option must not overwrite the saved Controls view.
        self.assertEqual(bpy.ops.character_designer.body_original_mode(action='ORIGINAL'), {'FINISHED'})
        self.assertEqual(session, rig[original.SESSION])
        for role, targets in expected.items():
            with self.subTest(role=role):
                self.assertTrue(original.group_visible(bpy.context, rig, role))
                original.show_group(bpy.context, rig, role)
                self.assertFalse(original.group_visible(bpy.context, rig, role))
                for other_role in expected:
                    if other_role != role:
                        self.assertTrue(original.group_visible(bpy.context, rig, other_role))
                for target, names in targets.items():
                    self.assertFalse(names & display_test.visible(target))
                original.show_group(bpy.context, rig, role)
                self.assertTrue(original.group_visible(bpy.context, rig, role))
                self.assertEqual(display_test.visible(rig), native_main)
                self.assertEqual(display_test.visible(dress), expected['DRESS'][dress])
                self.assertEqual(entered_channels, original._channels(rig))
                self.assertEqual(entered_dress_channels, original._channels(dress))
                self.assertEqual(display_test.views((other, foreign_dress)), foreign_view)
                self.assertEqual({target.name: original._channels(target) for target in (other, foreign_dress)}, foreign_channels)
                self.assertPose(before_pose)
        from character_designer import skirt_rig
        source = dress[skirt_rig.SOURCE_KEY]
        record = source[skirt_rig.RECORD_KEY]
        owner = skirt_rig.read_record(source)['owner']
        self.assertEqual(source[skirt_rig.OWNER_KEY], owner)
        self.assertEqual(dress[skirt_rig.OWNER_KEY], owner)
        active_view, active_content = display_test.views(rigs), display_test.content(rigs)
        active_session = rig[original.SESSION]
        guarded = (
            ('remove', lambda: skirt_rig.remove_skirt(bpy.context, source)),
            ('rebuild', lambda: skirt_rig.build_skirt(bpy.context, source, armature=rig)),
            ('reattach', lambda: skirt_rig.update_attachment(bpy.context, source, rig, 'Hips')),
            ('migrate', lambda: skirt_rig.migrate_skirt_bone_collections(dress)),
        )
        for operation, change in guarded:
            with self.subTest(skirt_setup=operation):
                with self.assertRaisesRegex(skirt_rig.SkirtRigError, 'Controls in Bone Display'):
                    change()
                self.assertEqual(source[skirt_rig.RECORD_KEY], record)
                self.assertEqual(rig[original.SESSION], active_session)
                self.assertEqual(display_test.views(rigs), active_view)
                self.assertContentEqual(display_test.content(rigs), active_content)
                self.assertEqual(original._channels(rig), entered_channels)
                self.assertEqual(original._channels(dress), entered_dress_channels)
        self.assertEqual(bpy.ops.character_designer.body_original_mode(action='CONTROLS'), {'FINISHED'})
        self.assertFalse(original.active(rig))
        self.assertEqual(channels, original._channels(rig))
        self.assertEqual(dress_channels, original._channels(dress))
        self.assertEqual(display_test.views(rigs), before_view)
        self.assertEqual(display_test.content(rigs), before_content)
        self.assertPose(before_pose)
        self.assertEqual(bpy.ops.character_designer.body_original_mode(action='CONTROLS'), {'FINISHED'})
        self.assertEqual(display_test.views(rigs), before_view)
        self.assertEqual(display_test.views((other, foreign_dress)),
                         {target.name: before_view[target.name] for target in (other, foreign_dress)})

    def test_unified_save_reopen_restores_renamed_dress_display(self):
        display_test, rigs, _expected = self.unified_fixture()
        rig, dress, other, foreign_dress = rigs
        dress.data.display_type = 'STICK'
        dress.data.show_bone_custom_shapes = False
        dress.hide_set(True)
        dress.hide_viewport = True
        probe = next(iter(dress.data.bones))
        probe.hide_select = True
        if hasattr(dress.pose.bones[probe.name], 'hide'):
            dress.pose.bones[probe.name].hide = True
        for collection in dress.data.collections_all:
            collection.is_visible = False
        before_view, before_content = display_test.views(rigs), display_test.content(rigs)
        before_pose = matrices(rig)
        original.enter(bpy.context, rig)
        original.show_group(bpy.context, rig, 'HAIR')
        old_name = dress.name
        dress.name = 'Artist Renamed Original Dress'
        # Object ID references must survive rename and a real file reload.
        before_view[dress.name] = before_view.pop(old_name)
        before_content['rigs'][dress.name] = before_content['rigs'].pop(old_name)
        for entry in before_content['rigs'].values():
            entry['bindings'] = tuple(
                binding[:7] + (tuple(
                    constraint[:3] + (dress.name if constraint[3] == old_name else constraint[3],) + constraint[4:]
                    for constraint in binding[7]),)
                for binding in entry['bindings'])
        for name, entry in before_content['meshes'].items():
            geometry, groups, weights, modifiers = entry
            before_content['meshes'][name] = (
                geometry, groups, weights,
                tuple((modifier_name, kind, dress.name if owner == old_name else owner)
                      for modifier_name, kind, owner in modifiers))
        names = tuple(target.name for target in rigs)
        self.assertContentEqual(display_test.content(rigs), before_content)
        with tempfile.TemporaryDirectory(prefix='cd-unified-original-') as directory:
            path = str(Path(directory) / 'original.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
            bpy.ops.wm.open_mainfile(filepath=path)
            rigs = tuple(bpy.data.objects[name] for name in names)
            self.rig = rig = rigs[0]
            self.assertTrue(original.active(rig))
            self.assertFalse(original.group_visible(bpy.context, rig, 'HAIR'))
            self.assertTrue(original.group_visible(bpy.context, rig, 'BODY'))
            self.assertTrue(original.group_visible(bpy.context, rig, 'DRESS'))
            self.assertTrue(bone_display._object_visible(rigs[1]))
            original.leave(bpy.context, rig)
            self.assertPose(before_pose)
            self.assertEqual(display_test.views(rigs), before_view)
            self.assertContentEqual(display_test.content(rigs), before_content)
            self.assertFalse(original.active(rig))

    def test_hidden_dress_reveal_preserves_other_objects_and_restores_flags(self):
        from character_designer import skirt_rig
        display_test, rigs, expected = self.unified_fixture()
        rig, dress, other, foreign_dress = rigs
        source = dress[skirt_rig.SOURCE_KEY]
        source.shape_key_add(name='Basis')
        key = source.shape_key_add(name='Artist asymmetric dress')
        key.data[0].co.x += .04
        shape_before = tuple(tuple(v.co) for block in source.data.shape_keys.key_blocks for v in block.data)
        helpers = [obj for obj in bpy.context.scene.objects if obj.parent == dress and obj != source]
        helper_flags = {obj.name: (obj.hide_get(), obj.hide_viewport, obj.hide_render) for obj in helpers}
        foreign = display_test.views((other, foreign_dress))
        protected = display_test.content(rigs)
        for viewport in (False, True):
            with self.subTest(disabled_viewport=viewport):
                dress.hide_set(True)
                dress.hide_viewport = viewport
                before = display_test.views((rig, dress))
                original.enter(bpy.context, rig)
                self.assertTrue(bone_display._object_visible(dress))
                self.assertTrue(original.group_visible(bpy.context, rig, 'DRESS'))
                self.assertEqual(display_test.visible(dress), expected['DRESS'][dress])
                dress.hide_set(True)
                self.assertFalse(original.group_visible(bpy.context, rig, 'DRESS'))
                original.show_group(bpy.context, rig, 'DRESS')
                self.assertTrue(original.group_visible(bpy.context, rig, 'DRESS'))
                original.show_group(bpy.context, rig, 'DRESS')
                self.assertFalse(original.group_visible(bpy.context, rig, 'DRESS'))
                self.assertTrue(bone_display._object_visible(rig))
                self.assertTrue(original.group_visible(bpy.context, rig, 'BODY'))
                self.assertTrue(original.group_visible(bpy.context, rig, 'HAIR'))
                original.leave(bpy.context, rig)
                self.assertEqual(display_test.views((rig, dress)), before)
                self.assertEqual(display_test.views((other, foreign_dress)), foreign)
                self.assertContentEqual(display_test.content(rigs), protected)
                self.assertEqual(tuple(tuple(v.co) for block in source.data.shape_keys.key_blocks for v in block.data),
                                 shape_before)
                self.assertEqual({obj.name: (obj.hide_get(), obj.hide_viewport, obj.hide_render)
                                  for obj in helpers}, helper_flags)

    def test_legacy_session_reveal_records_flags_and_rolls_back_failed_reveal(self):
        display_test, rigs, _expected = self.unified_fixture()
        rig, dress, *_ = rigs
        original.enter(bpy.context, rig)
        saved = json.loads(rig[original.SESSION])
        saved['display'].pop('object_visibility')
        for view in saved['extra_display'].values():
            view.pop('object_visibility')
        rig[original.SESSION] = json.dumps(saved, separators=(',', ':'))
        dress.hide_set(True)
        raw = rig[original.SESSION]
        views = display_test.views(rigs)
        reveal = bone_display._reveal_object
        def fail_after_reveal(target):
            reveal(target)
            raise RuntimeError('injected object reveal failure')
        with patch.object(bone_display, '_reveal_object', side_effect=fail_after_reveal):
            with self.assertRaisesRegex(RuntimeError, 'object reveal failure'):
                original.show_group(bpy.context, rig, 'DRESS')
        self.assertEqual(rig[original.SESSION], raw)
        self.assertEqual(display_test.views(rigs), views)
        original.show_group(bpy.context, rig, 'DRESS')
        self.assertTrue(original.group_visible(bpy.context, rig, 'DRESS'))
        recorded = json.loads(rig[original.SESSION])
        self.assertNotIn('object_visibility', recorded['display'])
        dress_key = next(key for key, target in rig[original.DISPLAY_REFS].items() if target == dress)
        self.assertTrue(recorded['extra_display'][dress_key]['object_visibility']['hidden'])
        self.assertEqual({key: value for key, value in recorded.items() if key != 'extra_display'},
                         {key: value for key, value in saved.items() if key != 'extra_display'})
        original.leave(bpy.context, rig)
        self.assertTrue(dress.hide_get())

    def test_original_collection_blocking_and_cross_layer_refusal_are_transactional(self):
        display_test, rigs, _expected = self.unified_fixture()
        rig, dress, *_ = rigs
        collection = dress.users_collection[0]
        collection.hide_viewport = True
        before = display_test.views(rigs)
        protected = display_test.content(rigs)
        channels = original._channels(rig)
        with self.assertRaisesRegex(ValueError, 'collection'):
            original.enter(bpy.context, rig)
        self.assertFalse(original.active(rig))
        self.assertEqual(display_test.views(rigs), before)
        self.assertEqual(original._channels(rig), channels)
        self.assertContentEqual(display_test.content(rigs), protected)
        self.assertTrue(collection.hide_viewport)
        collection.hide_viewport = False
        original.enter(bpy.context, rig)
        active_layer = bpy.context.view_layer
        other_layer = bpy.context.scene.view_layers.new('Artist other bone display layer')
        dress.hide_set(True, view_layer=other_layer)
        session = rig[original.SESSION]
        current_channels = original._channels(rig)
        try:
            bpy.context.window.view_layer = other_layer
            for operation in (lambda: original.show_group(bpy.context, rig, 'DRESS'),
                              lambda: original.leave(bpy.context, rig)):
                with self.assertRaisesRegex(ValueError, 'scene and view layer'):
                    operation()
                self.assertEqual(rig[original.SESSION], session)
                self.assertEqual(original._channels(rig), current_channels)
                self.assertTrue(dress.hide_get(view_layer=other_layer))
        finally:
            bpy.context.window.view_layer = active_layer
            original.leave(bpy.context, rig)

    def test_native_group_cache_keeps_live_references_and_isolated_name_sets(self):
        """Repeated redraws reuse text decoding without retaining artist scene state."""
        _display_test, rigs, expected = self.unified_fixture()
        rig, dress, _other, foreign_dress = rigs
        original.enter(bpy.context, rig)
        raw = rig[original.SESSION]
        saved = json.loads(raw)
        refs = dict(rig[original.DISPLAY_REFS])
        dress_key = next(key for key, target in refs.items() if target == dress)
        renamed = json.loads(raw)
        removed_name = renamed['native_groups']['BODY']['0'].pop()
        renamed_raw = json.dumps(renamed, separators=(',', ':'))
        legacy = dict(saved)
        legacy.pop('native_groups')
        legacy_raw = json.dumps(legacy, separators=(',', ':'))
        decoder = json.loads
        original._native_records.cache_clear()
        try:
            with patch.object(original.json, 'loads', wraps=decoder) as decode:
                for _redraw in range(5):
                    self.assertEqual(original._native_groups(bpy.context, rig), expected)
                self.assertEqual(decode.call_count, 1)
                # A consumer may change its returned sets; later draws must
                # still use the exact persistent session membership.
                changed = original._native_groups(bpy.context, rig)
                changed['BODY'][rig].clear()
                changed['DRESS'][dress].add('Caller-only bone')
                self.assertEqual(original._native_groups(bpy.context, rig), expected)
                self.assertEqual(decode.call_count, 1)
                # The same cached text must never retain an Object reference.
                del rig[original.DISPLAY_REFS][dress_key]
                with self.assertRaisesRegex(ValueError, 'display rig is missing'):
                    original._native_groups(bpy.context, rig)
                rig[original.DISPLAY_REFS][dress_key] = foreign_dress
                replacement = original._native_groups(bpy.context, rig)
                self.assertEqual(replacement['DRESS'], {foreign_dress: expected['DRESS'][dress]})
                rig[original.DISPLAY_REFS][dress_key] = dress
                self.assertEqual(original._native_groups(bpy.context, rig), expected)
                self.assertEqual(decode.call_count, 1)
                # Exact session text changes are visible immediately, and
                # old saved sessions remain Body-only and recoverable.
                rig[original.SESSION] = renamed_raw
                names = original._native_groups(bpy.context, rig)
                self.assertEqual(names['BODY'][rig], expected['BODY'][rig] - {removed_name})
                self.assertEqual(decode.call_count, 2)
                rig[original.SESSION] = legacy_raw
                self.assertEqual(original._native_groups(bpy.context, rig), {
                    'BODY': {rig: set(saved['names'])}, 'HAIR': {}, 'DRESS': {}})
                self.assertEqual(decode.call_count, 3)
                self.assertEqual(original._native_records.cache_info().currsize, 1)
        finally:
            rig[original.SESSION] = raw
            rig[original.DISPLAY_REFS] = refs
            original._native_records.cache_clear()
            original.leave(bpy.context, rig)

    def test_unified_display_failures_roll_back_all_character_rigs(self):
        display_test, rigs, _expected = self.unified_fixture()
        rig, dress, *_foreign = rigs
        before_pose, before_view = matrices(rig), display_test.views(rigs)
        before_channels = original._channels(rig)
        before_content = display_test.content(rigs)
        apply_view = original._set_native_view
        def fail_after_native_view(*args, **kwargs):
            apply_view(*args, **kwargs)
            raise RuntimeError('injected unified enter display failure')
        with patch.object(original, '_set_native_view', side_effect=fail_after_native_view):
            with self.assertRaisesRegex(RuntimeError, 'injected unified enter'):
                original.enter(bpy.context, rig)
        self.assertFalse(original.active(rig))
        self.assertEqual(display_test.views(rigs), before_view)
        self.assertEqual(before_channels, original._channels(rig))
        self.assertEqual(display_test.content(rigs), before_content)
        self.assertPose(before_pose)
        original.enter(bpy.context, rig)
        original.show_group(bpy.context, rig, 'DRESS')
        session, active_view = rig[original.SESSION], display_test.views(rigs)
        entered_channels = original._channels(rig)
        restore_view = bone_display._restore
        failed = False
        def fail_after_dress_restore(target, state):
            nonlocal failed
            restore_view(target, state)
            if target == dress and not failed:
                failed = True
                raise RuntimeError('injected unified return display failure')
        with patch.object(bone_display, '_restore', side_effect=fail_after_dress_restore):
            with self.assertRaisesRegex(RuntimeError, 'injected unified return'):
                original.leave(bpy.context, rig)
        self.assertTrue(failed)
        self.assertTrue(original.active(rig))
        self.assertEqual(session, rig[original.SESSION])
        self.assertEqual(display_test.views(rigs), active_view)
        self.assertEqual(entered_channels, original._channels(rig))
        self.assertPose(before_pose)
        original.leave(bpy.context, rig)
        self.assertEqual(display_test.views(rigs), before_view)
        self.assertEqual(display_test.content(rigs), before_content)

    def test_final_runtime_refresh_failures_use_full_switch_rollback(self):
        """A failed final synchronization must not leave an operator committed."""
        from character_designer import forearm_twist as runtime
        display_test, rigs, _expected = self.unified_fixture()
        rig, dress, _other, _foreign_dress = rigs
        before_channels = original._channels(rig)
        before_pose, before_views = matrices(rig), display_test.views(rigs)
        before_content = display_test.content(rigs)
        original_runtime = runtime.update_runtime
        def failing_refresh(phases):
            def update(scene, graph=None):
                if runtime._BUSY:
                    return original_runtime(scene, graph)
                phases.append(original.active(rig))
                original_runtime(scene, graph)
                if len(phases) == 1:
                    raise RuntimeError('injected final runtime refresh failure')
            return update
        phases = []
        with patch.object(runtime, 'update_runtime', side_effect=failing_refresh(phases)):
            with self.assertRaisesRegex(RuntimeError, 'final runtime refresh'):
                original.enter(bpy.context, rig)
        self.assertEqual(phases, [True, False], 'Refresh the restored Controls after failed Original commit')
        self.assertFalse(runtime._BUSY or original.active(rig))
        self.assertNotIn(original.DISPLAY_REFS, rig)
        self.assertNotIn(original.DISPLAY_OWNER, dress)
        self.assertEqual(original._channels(rig), before_channels)
        self.assertEqual(display_test.views(rigs), before_views)
        self.assertContentEqual(display_test.content(rigs), before_content)
        self.assertPose(before_pose)
        original.enter(bpy.context, rig)
        pb = rig.pose.bones['forearm.L']
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler.x += .03
        wanted = matrices(rig)
        channels, views = original._channels(rig), display_test.views(rigs)
        content = display_test.content(rigs)
        session, refs = rig[original.SESSION], dict(rig[original.DISPLAY_REFS])
        phases = []
        with patch.object(runtime, 'update_runtime', side_effect=failing_refresh(phases)):
            with self.assertRaisesRegex(RuntimeError, 'final runtime refresh'):
                original.leave(bpy.context, rig)
        self.assertEqual(phases, [False, True], 'Refresh the restored Original after failed Controls commit')
        self.assertFalse(runtime._BUSY)
        self.assertEqual(rig[original.SESSION], session)
        self.assertEqual(dict(rig[original.DISPLAY_REFS]), refs)
        self.assertEqual(dress[original.DISPLAY_OWNER], rig)
        self.assertEqual(original._channels(rig), channels)
        self.assertEqual(display_test.views(rigs), views)
        self.assertContentEqual(display_test.content(rigs), content)
        self.assertPose(wanted, 1e-6)
        original.leave(bpy.context, rig)
        self.assertFalse(runtime._BUSY or original.active(rig))
        self.assertPose(wanted)

    def test_no_edit_roundtrip_exact_config_and_source_rotation(self):
        rig = self.rig
        before, rest, mesh = matrices(rig), poses.native_rest(rig), setup.mesh_state(self.mesh)
        channels, view = original._channels(rig), bone_display._snapshot(rig)
        rig.pose.bones['forearm.L'].lock_rotation = (True, True, True)
        result = original.enter(bpy.context, rig)
        self.assertGreater(result['constraints'], 0)
        self.assertPose(before)
        self.assertEqual(bpy.context.mode, 'POSE')
        self.assertFalse(any(rig.pose.bones['forearm.L'].lock_rotation))
        original.leave(bpy.context, rig)
        self.assertFalse(original.active(rig))
        self.assertPose(before)
        self.assertEqual(channels, original._channels(rig))
        self.assertEqual(view, bone_display._snapshot(rig))
        self.assertEqual(tuple(rig.pose.bones['forearm.L'].lock_rotation), (True, True, True))
        self.assertEqual(rest, poses.native_rest(rig))
        self.assertEqual(mesh, setup.mesh_state(self.mesh))
        limb_ik._validate_inventory(rig)
        original.enter(bpy.context, rig)
        pb = rig.pose.bones['forearm.L']
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler.x += .08
        posed = matrices(rig)
        self.assertGreater(poses._difference(before['forearm.L'], posed['forearm.L']), .01)

    def test_existing_pose_mode_keeps_active_bone_and_selection_without_mode_switch(self):
        """Clicking a view in Pose Mode must not round-trip through Object Mode."""
        rig = self.rig
        limb_ik._mode_set(bpy.context, rig, 'POSE')
        rig.data.collections_all['Original'].is_visible = True
        for pb in rig.pose.bones:
            pb.select = False
        for name in ('hand.L', 'forearm.L'):
            bone_display._set_hidden(rig, rig.data.bones[name], False)
            rig.pose.bones[name].select = True
        rig.data.bones.active = rig.data.bones['hand.L']
        before_context = limb_ik._capture_context(bpy.context, rig)
        before_channels, before_view = original._channels(rig), bone_display._snapshot(rig)
        before_pose = matrices(rig)
        with patch.object(limb_ik, '_mode_set', side_effect=AssertionError('Unnecessary mode switch')):
            self.assertEqual(bpy.ops.character_designer.body_original_mode(action='ORIGINAL'), {'FINISHED'})
            self.assertEqual(limb_ik._capture_context(bpy.context, rig), before_context)
            self.assertPose(before_pose)
            session, entered_channels = rig[original.SESSION], original._channels(rig)
            # The already-selected option is also a no-op for the session and
            # selection, rather than reentering or replacing its saved view.
            self.assertEqual(bpy.ops.character_designer.body_original_mode(action='ORIGINAL'), {'FINISHED'})
            self.assertEqual(rig[original.SESSION], session)
            self.assertEqual(original._channels(rig), entered_channels)
            self.assertEqual(limb_ik._capture_context(bpy.context, rig), before_context)
            self.assertEqual(bpy.ops.character_designer.body_original_mode(action='CONTROLS'), {'FINISHED'})
        self.assertEqual(limb_ik._capture_context(bpy.context, rig), before_context)
        self.assertEqual(original._channels(rig), before_channels)
        self.assertEqual(bone_display._snapshot(rig), before_view)
        self.assertPose(before_pose)

    def test_large_connected_native_chain_preserves_pose_with_bounded_updates(self):
        """A switch evaluates phases, rather than reevaluating once per native bone."""
        self.rig, self.mesh = setup.fixture()
        rig = self.rig
        limb_ik._mode_set(bpy.context, rig, 'EDIT')
        parent = rig.data.edit_bones['hand.L']
        names = []
        for index in range(192):
            bone = rig.data.edit_bones.new(f'Native Detail {index:03d}')
            bone.head = parent.tail
            bone.tail = bone.head + Vector((.002, .007, .001))
            bone.parent = parent
            bone.use_connect = True
            bone.inherit_scale = ('NONE', 'FIX_SHEAR', 'FULL')[index % 3]
            names.append(bone.name)
            parent = bone
        limb_ik._mode_set(bpy.context, rig, 'OBJECT')
        setup.prepare(rig)
        body_setup.generate(bpy.context, rig)
        self.assertTrue(set(names) <= set(rig.data.collections_all['Original'].bones.keys()))
        inventory = limb_ik._validate_inventory(rig)
        target = rig.pose.bones[inventory['rigs'][('ARM', 'L')]['target'].name]
        self.assertIn(target.bone.get(limb_ik.OWNER_KEY), limb_ik.GENERATED_CONTROL_OWNERS)
        target.rotation_mode = 'XYZ'
        target.location += Vector((.018, -.026, .011))
        target.rotation_euler = (.09, -.04, .07)
        for index, name in enumerate(names):
            pb = rig.pose.bones[name]
            pb.rotation_mode = 'XYZ'
            pb.rotation_euler = (.001 * (index % 5), -.002 * (index % 3), .001)
            if index % 7 == 0:
                pb.scale = (1.04, .98, 1.01)
        before_pose = matrices(rig)
        before_channels, before_view = original._channels(rig), bone_display._snapshot(rig)
        before_rest, before_mesh = poses.native_rest(rig), setup.mesh_state(self.mesh)
        self.assertGreater(len(before_pose), 200)
        # This guards against O(native bones) dependency-graph evaluations. Wall
        # time is deliberately not asserted: hardware and background load vary.
        with patch.object(original, '_update', wraps=original._update) as update:
            original.enter(bpy.context, rig)
        self.assertLessEqual(update.call_count, 8)
        self.assertPose(before_pose)
        self.assertTrue(all(rig.data.bones[name].use_connect for name in names))
        original.leave(bpy.context, rig)
        self.assertPose(before_pose)
        self.assertEqual(original._channels(rig), before_channels)
        self.assertEqual(bone_display._snapshot(rig), before_view)
        self.assertEqual(poses.native_rest(rig), before_rest)
        self.assertEqual(setup.mesh_state(self.mesh), before_mesh)
        limb_ik._validate_inventory(rig)

    def test_authored_fk_rotation_and_weights_survive_return(self):
        rig = self.rig
        for key in limb_ik._validate_inventory(rig)['rigs']:
            limb_ik_fk.switch_limb(bpy.context, rig, key, 'FK', keyframe=False)
        rest = poses.native_rest(rig)
        modes = {pb.name: pb.get('ik_fk') for pb in rig.pose.bones if 'ik_fk' in pb}
        original.enter(bpy.context, rig)
        rig.pose.bones['forearm.L'].rotation_mode = 'XYZ'
        rig.pose.bones['forearm.L'].rotation_euler.x += .1
        self.mesh.vertex_groups['hand.L'].add([2], .43, 'REPLACE')
        wanted = matrices(rig)
        mesh = setup.mesh_state(self.mesh)
        original.leave(bpy.context, rig)
        self.assertPose(wanted)
        self.assertEqual(rest, poses.native_rest(rig))
        self.assertEqual(mesh, setup.mesh_state(self.mesh))
        self.assertEqual(modes, {pb.name: pb.get('ik_fk') for pb in rig.pose.bones if 'ik_fk' in pb})
        limb_ik._validate_inventory(rig)

    def test_save_reopen_keeps_original_and_recoverable_return(self):
        rig = self.rig
        name = rig.name
        before = matrices(rig)
        original.enter(bpy.context, rig)
        with tempfile.TemporaryDirectory(prefix='cd-original-') as directory:
            path = str(Path(directory) / 'original.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path, copy=True)
            bpy.ops.wm.open_mainfile(filepath=path)
            self.rig = rig = bpy.data.objects[name]
            self.assertTrue(original.active(rig))
            self.assertTrue(all(con.mute for con, _entry in original._resolve(rig, json.loads(rig[original.SESSION])['constraints'])))
            original.leave(bpy.context, rig)
            self.assertPose(before)
            limb_ik._validate_inventory(rig)

    def test_mixed_modes_keep_hand_pose_and_actual_weight_workspace(self):
        from character_designer import control_weight_paint as weights
        rig = self.rig
        limb_ik_fk.switch_limb(bpy.context, rig, ('ARM', 'R'), 'FK', keyframe=False)
        modes = {pb.name: pb.get('ik_fk') for pb in rig.pose.bones if 'ik_fk' in pb}
        original.enter(bpy.context, rig)
        rig.pose.bones['hand.L'].rotation_mode = 'XYZ'
        rig.pose.bones['hand.L'].rotation_euler.y += .03
        wanted = matrices(rig)
        rig.data.bones.active = rig.data.bones['hand.L']
        weights.enter(bpy.context)
        self.assertEqual(bpy.context.mode, 'PAINT_WEIGHT')
        self.mesh.vertex_groups['hand.L'].add([2], .31, 'REPLACE')
        with self.assertRaisesRegex(ValueError, 'weight session|Edit Weights'):
            original.leave(bpy.context, rig)
        weights.leave(bpy.context)
        self.assertTrue(original.active(rig))
        original.leave(bpy.context, rig)
        self.assertPose(wanted)
        self.assertAlmostEqual(self.mesh.vertex_groups['hand.L'].weight(2), .31)
        self.assertEqual(modes, {pb.name: pb.get('ik_fk') for pb in rig.pose.bones if 'ik_fk' in pb})

    def test_operator_undo_redo_and_refresh_keep_session(self):
        import importlib
        rig = self.rig
        name = rig.name
        bpy.context.preferences.edit.use_global_undo = True
        bpy.ops.ed.undo_push(message='Before explicit Original')
        self.assertEqual(bpy.ops.character_designer.body_original_mode(), {'FINISHED'})
        bpy.ops.ed.undo_push(message='Explicit Original')
        self.assertTrue(original.active(rig))
        bpy.ops.ed.undo()
        self.rig = rig = bpy.data.objects[name]
        self.assertFalse(original.active(rig))
        bpy.ops.ed.redo()
        self.rig = rig = bpy.data.objects[name]
        self.assertTrue(original.active(rig))
        importlib.reload(original)
        self.assertTrue(original.active(rig))
        original.leave(bpy.context, rig)
        limb_ik._validate_inventory(rig)

    def test_return_failure_rolls_back_authored_pose_and_session(self):
        rig = self.rig
        original.enter(bpy.context, rig)
        rig.pose.bones['forearm.L'].rotation_mode = 'XYZ'
        rig.pose.bones['forearm.L'].rotation_euler.x += .12
        wanted, channels = matrices(rig), original._channels(rig)
        session, view = rig[original.SESSION], bone_display._snapshot(rig)
        with patch.object(poses, '_match', side_effect=ValueError('injected match failure')):
            with self.assertRaisesRegex(ValueError, 'injected'):
                original.leave(bpy.context, rig)
        self.assertPose(wanted, 1e-6)
        self.assertEqual(channels, original._channels(rig))
        self.assertEqual(session, rig[original.SESSION])
        self.assertEqual(view, bone_display._snapshot(rig))
        self.assertTrue(all(con.mute for con, _entry in original._resolve(rig, json.loads(session)['constraints'])))

    def test_enter_failure_rolls_back_and_blocks_structural_updates(self):
        rig = self.rig
        before, channels, view = matrices(rig), original._channels(rig), bone_display._snapshot(rig)
        with patch.object(original, '_verify', side_effect=ValueError('injected enter failure')):
            with self.assertRaisesRegex(ValueError, 'injected'):
                original.enter(bpy.context, rig)
        self.assertFalse(original.active(rig))
        self.assertEqual(channels, original._channels(rig))
        self.assertEqual(view, bone_display._snapshot(rig))
        self.assertPose(before, 1e-6)
        limb_ik._validate_inventory(rig)
        original.enter(bpy.context, rig)
        with self.assertRaisesRegex(ValueError, 'Controls'):
            body_setup.generate(bpy.context, rig)
        with self.assertRaisesRegex(ValueError, 'Controls'):
            bone_collections.simplify_body_collections(rig)
        membership = tuple(bone_collections.body_collection(rig).bones.keys())
        bpy.context.scene.frame_set(8)
        self.assertEqual(membership, tuple(bone_collections.body_collection(rig).bones.keys()))

    def test_full_body_extensions_no_edit_and_authored_fk_pose(self):
        import test_root_control_blender as full
        from character_designer import root_control
        self.rig = rig = full.fixture()
        root_control.build(bpy.context, rig)
        before, rest = matrices(rig), poses.native_rest(rig)
        original.enter(bpy.context, rig)
        self.assertPose(before)
        original.leave(bpy.context, rig)
        self.assertPose(before)
        from character_designer import spine_ik_fk, torso_controls
        spine_ik_fk.switch(bpy.context, rig, 'FK')
        for key in limb_ik._validate_inventory(rig)['rigs']:
            limb_ik_fk.switch_limb(bpy.context, rig, key, 'FK', keyframe=False)
        original.enter(bpy.context, rig)
        torso = torso_controls.get_record(rig)
        for name in (torso['sources'][0], 'toe.L', 'forearm.L'):
            if name in rig.pose.bones:
                pb = rig.pose.bones[name]
                matrix = pb.matrix.copy()
                pb.rotation_mode = 'XYZ'
                pb.rotation_euler.x += .02
                limb_ik_fk._update(bpy.context, rig)
                self.assertGreater(poses._difference(matrix, pb.matrix), .001, name)
        wanted = matrices(rig)
        original.leave(bpy.context, rig)
        self.assertPose(wanted)
        self.assertEqual(rest, poses.native_rest(rig))
        limb_ik._validate_inventory(rig)


if __name__ == '__main__':
    result = unittest.main(argv=[__file__], exit=False).result
    if not result.wasSuccessful():
        raise RuntimeError('Body Original tests failed')
