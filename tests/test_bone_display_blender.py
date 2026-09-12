"""Disposable character display views: isolation, persistence and rollback."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import bone_display as display, bone_collections as groups
from character_designer import foot_controls, hair_bones_rig as hair, limb_ik, skirt_rig
import test_limb_ik_blender as base
from test_limb_ik_fk_blender import build as build_leg
from test_hair_bones_rig_blender import activate, build as build_hair, make_hair
from test_skirt_topology_blender import frustum


def matrix(value):
    return tuple(tuple(row) for row in value)


def views(rigs):
    return {rig.name: display._snapshot(rig) for rig in rigs}


def content(rigs):
    """Channels, binding/rest data and geometry deliberately exclude visibility."""
    def constraints(owner):
        return tuple((c.name, c.type, c.influence,
                      getattr(getattr(c, 'target', None), 'name', None),
                      getattr(c, 'subtarget', None)) for c in owner.constraints)

    return {
        'rigs': {rig.name: {
            'object': (matrix(rig.matrix_basis), matrix(rig.matrix_parent_inverse),
                       rig.parent.name if rig.parent else None, rig.parent_type, rig.parent_bone),
            'rest': tuple((b.name, b.parent.name if b.parent else None, matrix(b.matrix_local),
                           b.length, b.use_deform, b.use_connect) for b in rig.data.bones),
            'bindings': tuple((pb.name, pb.custom_shape.name if pb.custom_shape else None,
                               pb.custom_shape_transform.name if pb.custom_shape_transform else None,
                               tuple(pb.custom_shape_translation), tuple(pb.custom_shape_rotation_euler),
                               tuple(pb.custom_shape_scale_xyz), pb.use_custom_shape_bone_size,
                               constraints(pb)) for pb in rig.pose.bones),
            'membership': tuple((c.name, c.parent.name if c.parent else None,
                                 tuple(c.bones.keys())) for c in rig.data.collections_all),
            'action': tuple((action.name, tuple((curve.data_path, curve.array_index,
                        tuple((tuple(k.co), tuple(k.handle_left), tuple(k.handle_right), k.interpolation)
                              for k in curve.keyframe_points))
                        for curve in limb_ik._fcurves_for_action(action)))
                        for action in limb_ik._actions_for_id(rig)),
        } for rig in rigs},
        'meshes': {obj.name: (tuple(tuple(v.co) for v in obj.data.vertices),
                  tuple((g.name, g.index) for g in obj.vertex_groups),
                  tuple(tuple((g.group, g.weight) for g in v.groups) for v in obj.data.vertices),
                  tuple((m.name, m.type, getattr(getattr(m, 'object', None), 'name', None))
                        for m in obj.modifiers))
                   for obj in bpy.data.objects if obj.type == 'MESH'},
    }


def sampled_poses(rigs):
    frame = bpy.context.scene.frame_current
    result = {}
    for value in (1, 4, 9):
        bpy.context.scene.frame_set(value)
        bpy.context.view_layer.update()
        result[value] = {rig.name: (matrix(rig.matrix_world),
                          tuple((pb.name, matrix(pb.matrix), matrix(pb.matrix_basis))
                                for pb in rig.pose.bones)) for rig in rigs}
    bpy.context.scene.frame_set(frame)
    return result


def visible(rig):
    return {b.name for b in rig.data.bones if not getattr(rig.pose.bones[b.name], 'hide', b.hide) and
            (not b.collections or any(c.is_visible_effectively for c in b.collections))}


def fixture():
    base.ensure_registered()
    base.reset_scene()
    main = base.make_humanoid('Display Character', roll_offset=.21)
    main.location, main.rotation_euler, main.scale = (.2, -.1, .3), (.1, -.08, .2), (.78,) * 3
    _, settings = base.analyze(main)
    settings.selected_limb = 'LEFT_ARM'
    assert bpy.ops.character_designer.limb_ik_build_selected() == {'FINISHED'}, settings.last_message
    hair_mesh, plans = make_hair('Character Hair', strands=1)
    hair_result = build_hair(hair_mesh, plans, armature=main, parent_bone='Chest')
    activate(main, 'EDIT')
    helper = base.add_bone(main.data.edit_bones, 'Hair Test Helper', (0, 0, 2), (0, 0, 2.1),
                           main.data.edit_bones['Chest'], deform=False)
    helper[hair.OWNER_KEY] = hair.OWNER_VALUE
    bpy.ops.object.mode_set(mode='OBJECT')
    hair_collection = next(c for c in main.data.collections_all if c.get(hair.OWNER_KEY) == hair.OWNER_VALUE)
    hair_collection.assign(main.data.bones['Hair Test Helper'])
    groups.simplify_body_collections(main)
    widget_mesh = bpy.data.meshes.new('Artist Hand Shape Mesh')
    widget_mesh.from_pydata(((0, 0, 0), (.1, 0, 0)), ((0, 1),), ())
    widget = bpy.data.objects.new('Artist Hand Shape', widget_mesh)
    bpy.context.scene.collection.objects.link(widget)
    hand = main.pose.bones['hand.R']
    hand.custom_shape, hand.custom_shape_transform = widget, main.pose.bones['Chest']
    hand.custom_shape_translation, hand.custom_shape_scale_xyz = (.03, .04, -.02), (1.2, .8, 1.1)
    hand.custom_shape_rotation_euler = (.1, .2, .3)
    for frame, x in ((1, 0), (9, .08)):
        main.pose.bones['Hips'].location.x = x
        main.pose.bones['Hips'].keyframe_insert('location', frame=frame)
    dress_mesh = frustum('Character Dress', rows=6, sides=16)
    dress_record = skirt_rig.build_skirt(bpy.context, dress_mesh, armature=main)
    dress = dress_mesh[skirt_rig.RIG_KEY]
    other = base.make_humanoid('Other Character')
    groups.simplify_body_collections(other)
    other_mesh = frustum('Other Dress', rows=6, sides=16)
    skirt_rig.build_skirt(bpy.context, other_mesh, armature=other)
    other_dress = other_mesh[skirt_rig.RIG_KEY]
    activate(main, 'POSE')
    bpy.context.scene.frame_set(4)
    bpy.context.view_layer.update()
    return main, dress, other, other_dress, hair_mesh, dress_mesh, hair_result, dress_record


class BoneDisplayTests(unittest.TestCase):
    def test_show_controls_hides_registered_wrist_helpers_in_artist_collections(self):
        base.ensure_registered()
        for method in ('ROLL_DECOUPLED', 'DIRECT_PREROLL'):
            for selected in ('LEFT_ARM', 'RIGHT_ARM'):
                with self.subTest(method=method, selected=selected):
                    rig, key, data = build_leg(method, selected)
                    groups.simplify_body_collections(rig)
                    helper = rig.pose.bones[limb_ik._wrist_helper_name(key[1])]
                    target = rig.pose.bones[data['target'].name]
                    self.assertEqual(helper.bone.get(limb_ik.ROLE_KEY), 'HAND_ROTATION')
                    self.assertEqual(helper.parent, target)
                    groups.body_collection(rig).assign(helper.bone)
                    artist = rig.data.collections.new('AltH')
                    artist.assign(helper.bone)
                    artist.assign(rig.data.bones['Hips'])
                    selector_flags = {b.name: b.hide_select for b in rig.data.bones}
                    original_content = content((rig,))
                    original_pose = sampled_poses((rig,))
                    for action in ('BODY', 'ALL'):
                        helper.bone.hide = helper.hide = False
                        target.bone.hide = target.hide = True
                        rig.data.bones['Hips'].hide = rig.pose.bones['Hips'].hide = False
                        self.assertIn(helper.name, visible(rig))
                        display.show_controls(bpy.context, rig, action)
                        self.assertTrue(helper.bone.hide and helper.hide)
                        self.assertNotIn(helper.name, visible(rig))
                        self.assertIn(target.name, visible(rig))
                        self.assertIn('Hips', visible(rig))
                        self.assertEqual({b.name: b.hide_select for b in rig.data.bones}, selector_flags)
                        self.assertEqual(content((rig,)), original_content)
                        self.assertEqual(sampled_poses((rig,)), original_pose)

    def test_show_controls_hides_registered_foot_helpers_in_visible_collections(self):
        for method, side in (('ROLL_DECOUPLED', 'L'), ('DIRECT_PREROLL', 'R')):
            with self.subTest(method=method, side=side):
                main, key, _ = build_leg(method, 'LEFT_LEG' if side == 'L' else 'RIGHT_LEG', toes=True)
                record = foot_controls.build(bpy.context, main, key)
                groups.simplify_body_collections(main)
                body = groups.body_collection(main)
                extra = main.data.collections.new('Artist Foot Inspection')
                helpers = set(record['bones'].values()) - {record['roll'], record['toe_control']}
                for name in helpers:
                    body.assign(main.data.bones[name])
                    extra.assign(main.data.bones[name])
                # The display action must leave collection membership and
                # selectability alone, including artist inspection choices.
                probe = main.data.bones[record['bones']['TOE_SPACE']]
                probe.hide_select = False
                selector_flags = {b.name: b.hide_select for b in main.data.bones}
                content_before, pose_before = content((main,)), sampled_poses((main,))
                record_before = main.data[foot_controls.RECORD_KEY]
                guides = {b.name for b in main.data.bones if b.get(limb_ik.ROLE_KEY) == 'POLE_LINE' and not b.hide}
                for action in ('BODY', 'ALL'):
                    for name in helpers:
                        main.data.bones[name].hide = False
                        if hasattr(main.pose.bones[name], 'hide'):
                            main.pose.bones[name].hide = False
                    for name in (record['roll'], record['toe_control']):
                        main.data.bones[name].hide = True
                        if hasattr(main.pose.bones[name], 'hide'):
                            main.pose.bones[name].hide = True
                    self.assertTrue(helpers <= visible(main))
                    display.show_controls(bpy.context, main, action)
                    self.assertTrue(all(main.data.bones[name].hide for name in helpers))
                    self.assertTrue(all(getattr(main.pose.bones[name], 'hide', True) for name in helpers))
                    self.assertFalse(helpers & visible(main))
                    self.assertTrue({record['roll'], record['toe_control']} <= visible(main))
                    self.assertTrue(guides <= visible(main))
                    self.assertEqual({b.name: b.hide_select for b in main.data.bones}, selector_flags)
                    self.assertEqual(content((main,)), content_before)
                    self.assertEqual(sampled_poses((main,)), pose_before)
                    self.assertEqual(main.data[foot_controls.RECORD_KEY], record_before)
                # A stale record must not claim an unrelated or repurposed bone.
                for field, value in ((foot_controls.OWNER_KEY, 'artist'),
                                     (foot_controls.ID_KEY, 'different setup'),
                                     (foot_controls.ROLE_KEY, 'TOE_BEND'),
                                     (foot_controls.SIDE_KEY, 'R' if side == 'L' else 'L')):
                    old = probe[field]
                    try:
                        probe[field], probe.hide = value, False
                        display.show_controls(bpy.context, main, 'BODY')
                        self.assertFalse(probe.hide)
                    finally:
                        probe[field] = old
                probe.use_deform, probe.hide = True, False
                try:
                    display.show_controls(bpy.context, main, 'ALL')
                    self.assertFalse(probe.hide)
                finally:
                    probe.use_deform = False
                display.show_controls(bpy.context, main, 'BODY')
                self.assertTrue(probe.hide)
                foot_controls.validate(main, limb_ik._validate_inventory(main))

    def test_independent_pose_hide_restores_and_show_controls_reveals(self):
        base.ensure_registered()
        base.reset_scene()
        main = base.make_humanoid('Independent Pose Visibility')
        groups.simplify_body_collections(main)
        shin, hand = main.pose.bones['shin.L'], main.pose.bones['hand.R']
        if not hasattr(shin, 'hide'):
            self.skipTest('This Blender version uses data-bone visibility for Pose Mode')
        # Reproduce the real file: the data bone is visible but its pose is hidden.
        shin.bone.hide, shin.hide = False, True
        hand.bone.hide, hand.hide = True, False
        shin.bone.hide_select = True
        initial = display._snapshot(main)
        initial_content, initial_poses = content((main,)), sampled_poses((main,))
        original = set(main.data.collections_all['Original'].bones.keys())
        for mode in ('OBJECT', 'POSE'):
            with self.subTest(mode=mode):
                activate(main, mode)
                display.show_native(bpy.context, main, 'ORIGINAL')
                self.assertFalse(shin.hide)
                self.assertEqual(visible(main), original)
                self.assertTrue(display.restore_view(main))
                self.assertEqual(display._snapshot(main), initial)
        # Explicit Show Controls should reveal an artist-hidden control in both
        # visibility stores, without modifying its selection lock.
        display.show_controls(bpy.context, main, 'BODY')
        self.assertFalse(shin.bone.hide)
        self.assertFalse(shin.hide)
        self.assertTrue(shin.bone.hide_select)
        self.assertEqual(content((main,)), initial_content)
        self.assertEqual(sampled_poses((main,)), initial_poses)

    def test_legacy_view_without_pose_flags_does_not_overwrite_them(self):
        base.ensure_registered()
        base.reset_scene()
        main = base.make_humanoid('Legacy Pose Visibility')
        groups.simplify_body_collections(main)
        shin = main.pose.bones['shin.L']
        if not hasattr(shin, 'hide'):
            self.skipTest('This Blender version uses data-bone visibility for Pose Mode')
        display.show_native(bpy.context, main, 'ORIGINAL')
        payload = json.loads(main.data[display.VIEW_KEY])
        for saved in payload['rigs'].values():
            saved.pop('pose_hidden')
        main.data[display.VIEW_KEY] = json.dumps(payload)
        shin.hide = True
        self.assertTrue(display.restore_view(main))
        self.assertTrue(shin.hide)
        self.assertIsNone(display.view_mode(main))

    def test_native_groups_preserve_rig_and_other_character(self):
        main, dress, other, other_dress, _hair_mesh, dress_mesh, hair_result, dress_record = fixture()
        rigs = (main, dress, other, other_dress)
        initial_view, initial_content, initial_poses = views(rigs), content(rigs), sampled_poses(rigs)
        native = set(main.data.collections_all['Original'].bones.keys())
        hair_names = {name for chain in hair_result['chains'] for name in chain['bones']}
        _, deform, _ = skirt_rig._bone_collection_layout(dress_record)
        expectations = {'ORIGINAL': (native, set()), 'HAIR': (hair_names, set()),
                        'DRESS': (set(), deform | {dress_record['controls']['waist']})}
        for mode, (body_names, dress_names) in expectations.items():
            with self.subTest(mode=mode):
                display.show_native(bpy.context, main, mode)
                self.assertEqual(visible(main), body_names)
                self.assertEqual(visible(dress), dress_names)
                self.assertFalse(main.data.show_bone_custom_shapes)
                self.assertFalse(dress.data.show_bone_custom_shapes)
                self.assertEqual(display.view_mode(main), mode)
                self.assertEqual(display.view_mode(dress), mode)
                self.assertEqual(views((other, other_dress)), {r.name: initial_view[r.name] for r in (other, other_dress)})
                self.assertEqual(content(rigs), initial_content)
                self.assertEqual(sampled_poses(rigs), initial_poses)
                self.assertEqual(visible(main), body_names, 'Frame changes must keep isolation')
                self.assertTrue(display.restore_view(main))
                self.assertFalse(display.restore_view(main))
                self.assertEqual(views(rigs), initial_view)
        activate(dress_mesh)
        self.assertEqual(display.character_rig(bpy.context), main)
        activate(dress)
        self.assertEqual(display.character_rig(bpy.context), main)

    def test_save_reopen_restore_keeps_artist_edits(self):
        main, dress, other, other_dress, hair_mesh, *_ = fixture()
        rigs = (main, dress, other, other_dress)
        # Non-default visibility must survive restoration exactly.
        main.data.bones['hand.R'].hide_select = True
        if hasattr(main.pose.bones['shin.L'], 'hide'):
            main.pose.bones['shin.L'].hide = True
            dress.pose.bones[next(iter(dress.pose.bones.keys()))].hide = True
        main.data.collections_all['Original'].is_solo = True
        main.data.display_type = 'STICK'
        before_view = views(rigs)
        display.show_native(bpy.context, main, 'HAIR')
        first_backup = main.data[display.VIEW_KEY]
        display.show_native(bpy.context, main, 'ORIGINAL')
        self.assertEqual(json.loads(main.data[display.VIEW_KEY])['rigs'], json.loads(first_backup)['rigs'])
        hand = main.pose.bones['hand.R']
        hand.rotation_mode = 'XYZ'
        hand.rotation_euler.x = .23
        hair_mesh.vertex_groups.new(name='Artist Painted While Isolated').add([0, 1], .37, 'REPLACE')
        old_name = dress.name
        dress.name = 'Renamed Attached Dress'
        before_view[dress.name] = before_view.pop(old_name)
        edited_content, edited_poses = content(rigs), sampled_poses(rigs)
        names = tuple(r.name for r in rigs)
        with tempfile.TemporaryDirectory(prefix='cd-bone-display-') as folder:
            path = str(Path(folder) / 'display.blend')
            bpy.ops.wm.save_as_mainfile(filepath=path)
            bpy.ops.wm.open_mainfile(filepath=path)
            rigs = tuple(bpy.data.objects[name] for name in names)
            main, dress, *_ = rigs
            self.assertEqual(display.view_mode(main), 'ORIGINAL')
            self.assertEqual(main.data[display.REFS_KEY]['1'], dress)
            # Restore also works while selected on the independently stored dress.
            self.assertTrue(display.restore_view(dress))
            self.assertEqual(views(rigs), before_view)
            self.assertEqual(content(rigs), edited_content)
            self.assertEqual(sampled_poses(rigs), edited_poses)
            self.assertNotIn(display.VIEW_KEY, main.data)
            self.assertNotIn(display.REFS_KEY, dress.data)

    def test_control_group_toggles_are_independent(self):
        main, dress, other, other_dress, *_ = fixture()
        rigs = (main, dress, other, other_dress)
        before_content, before_poses = content(rigs), sampled_poses(rigs)
        others = views((other, other_dress))
        collections = {role: display._control_collections(bpy.context, main, role)
                       for role in ('BODY', 'HAIR', 'DRESS')}
        for role, pairs in collections.items():
            with self.subTest(role=role):
                display.show_controls(bpy.context, main, 'ALL')
                display.show_controls(bpy.context, main, role, toggle=True)
                self.assertTrue(all(not c.is_visible for _rig, c in pairs))
                self.assertTrue(all(c.is_visible for other_role, other_pairs in collections.items()
                                    if other_role != role for _rig, c in other_pairs))
                display.show_controls(bpy.context, main, role, toggle=True)
                self.assertTrue(all(c.is_visible for _rig, c in pairs))
                self.assertEqual(views((other, other_dress)), others)
        display.show_native(bpy.context, main, 'ORIGINAL')
        display.show_controls(bpy.context, main, 'BODY', toggle=True)
        self.assertTrue(groups.body_collection(main).is_visible)
        self.assertTrue(main.data.show_bone_custom_shapes)
        self.assertIsNone(display.view_mode(main))
        self.assertNotIn(display.VIEW_KEY, main.data)
        display.show_native(bpy.context, main, 'DRESS')
        display.show_controls(bpy.context, main, 'ALL')
        self.assertIsNone(display.view_mode(main))
        self.assertTrue(main.data.show_bone_custom_shapes and dress.data.show_bone_custom_shapes)
        self.assertFalse(main.data.collections_all['Original'].is_visible)
        self.assertFalse(main.data.collections_all['_Internal'].is_visible)
        self.assertEqual(content(rigs), before_content)
        self.assertEqual(sampled_poses(rigs), before_poses)

    def test_readonly_refusal_retains_active_view(self):
        main, dress, other, other_dress, *_ = fixture()
        rigs = (main, dress, other, other_dress)
        display.show_native(bpy.context, main, 'ORIGINAL')
        before_view, before_content = views(rigs), content(rigs)
        backup = main.data[display.VIEW_KEY]
        shared = bpy.data.objects.new('Shared Readonly Guard', dress.data)
        bpy.context.scene.collection.objects.link(shared)
        try:
            for operation in (lambda: display.show_native(bpy.context, main, 'HAIR'),
                              lambda: display.restore_view(main),
                              lambda: display.show_controls(bpy.context, main, 'BODY', toggle=True)):
                with self.assertRaisesRegex(ValueError, 'single-user'):
                    operation()
                self.assertEqual(views(rigs), before_view)
                self.assertEqual(content(rigs), before_content)
                self.assertEqual(main.data[display.VIEW_KEY], backup)
        finally:
            bpy.data.objects.remove(shared, do_unlink=True)
        self.assertTrue(display.restore_view(main))

    def test_failed_switch_retains_previous_isolation(self):
        main, dress, other, other_dress, *_ = fixture()
        rigs = (main, dress, other, other_dress)
        display.show_native(bpy.context, main, 'ORIGINAL')
        before_view, before_content = views(rigs), content(rigs)
        backup = main.data[display.VIEW_KEY]
        dumps = display.json.dumps
        def fail_metadata(*_args, **_kwargs):
            raise RuntimeError('Injected display metadata failure')
        display.json.dumps = fail_metadata
        try:
            with self.assertRaisesRegex(RuntimeError, 'metadata failure'):
                display.show_native(bpy.context, main, 'HAIR')
        finally:
            display.json.dumps = dumps
        self.assertEqual(views(rigs), before_view)
        self.assertEqual(content(rigs), before_content)
        self.assertEqual(main.data[display.VIEW_KEY], backup)

    def test_missing_control_group_keeps_active_view(self):
        base.ensure_registered()
        base.reset_scene()
        main = base.make_humanoid('Body Only Character')
        groups.simplify_body_collections(main)
        display.show_native(bpy.context, main, 'ORIGINAL')
        before, backup = views((main,)), main.data[display.VIEW_KEY]
        for role in ('HAIR', 'DRESS'):
            with self.assertRaisesRegex(ValueError, 'controls in this group'):
                display.show_controls(bpy.context, main, role, toggle=True)
            self.assertEqual(views((main,)), before)
            self.assertEqual(main.data[display.VIEW_KEY], backup)

    def test_restore_immediately_refreshes_keyed_fk_membership(self):
        main, dress, *_ = fixture()
        limb = limb_ik._validate_inventory(main)['rigs'][('ARM', 'L')]
        target = main.pose.bones[limb['target'].name]
        for frame, value in ((1, 1.0), (9, 0.0)):
            target['ik_fk'] = value
            target.keyframe_insert('["ik_fk"]', frame=frame)
        path = target.path_from_id('["ik_fk"]')
        for action in limb_ik._actions_for_id(main):
            for curve in limb_ik._fcurves_for_action(action):
                if curve.data_path == path:
                    for key in curve.keyframe_points:
                        key.interpolation = 'CONSTANT'
        bpy.context.scene.frame_set(1)
        body = groups.body_collection(main)
        ik_members = set(body.bones.keys())
        self.assertIn(target.name, ik_members)
        self.assertFalse(set(limb['chain']) & ik_members)
        original = set(main.data.collections_all['Original'].bones.keys())
        display.show_native(bpy.context, main, 'ORIGINAL')
        bpy.context.scene.frame_set(9)
        bpy.context.view_layer.update()
        self.assertEqual(target['ik_fk'], 0.0)
        self.assertEqual(visible(main), original)
        self.assertEqual(set(body.bones.keys()), ik_members, 'Isolation must defer automatic membership changes')
        before_pose = {rig.name: tuple((pb.name, matrix(pb.matrix), matrix(pb.matrix_basis))
                                      for pb in rig.pose.bones) for rig in (main, dress)}
        self.assertTrue(display.restore_view(main))
        # No frame_set/update call here: Restore must refresh membership itself.
        self.assertEqual(bpy.context.scene.frame_current, 9)
        self.assertTrue(set(limb['chain']) <= set(body.bones.keys()))
        self.assertNotIn(target.name, body.bones)
        self.assertNotIn(limb['pole'].name, body.bones)
        self.assertEqual({rig.name: tuple((pb.name, matrix(pb.matrix), matrix(pb.matrix_basis))
                                         for pb in rig.pose.bones) for rig in (main, dress)}, before_pose)


if __name__ == '__main__':
    groups.register_handlers()
    try:
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(BoneDisplayTests))
    finally:
        groups.unregister_handlers()
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('BONE_DISPLAY_PASSED', result.testsRun, flush=True)
