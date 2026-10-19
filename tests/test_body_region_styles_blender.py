"""Durable selected-part styles in disposable native Blender fixtures.

Run only with --background --factory-startup.  The save/reopen case writes a
temporary fixture, never an artist scene.  A release wrapper may preload its
frozen character_designer package before importing these canonical fixtures.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import (body_calibration, body_original_owner,
    body_region as region, body_setup, control_colors, control_pose_assets,
    foot_controls, limb_fk_visuals, limb_ik, region_styles as styles,
    unity_animation)
import test_body_region_blender as regional
from test_body_rest_resync_blender import world_surfaces
from test_body_setup_removal_blender import bind_probe_surface
from test_foot_controls_rebuild_blender import customize_shapes as customize_feet
from test_original_return_owner_blender import retain_action


ATTRIBUTE = 'Artist regional profile'
NOTE = 'artist_regional_note'


def resources():
    return {kind: tuple(sorted(getattr(bpy.data, kind).keys()))
            for kind in ('objects', 'meshes', 'collections', 'materials')}


def durable(value):
    """Native ID addresses are not stable across an intentional file reopen."""
    if isinstance(value, (tuple, list)):
        if len(value) == 5 and value[0] == '@ID':
            return tuple(value[:3])
        return tuple(durable(item) for item in value)
    if isinstance(value, dict):
        return {name: durable(item) for name, item in value.items()}
    return value


def refs(rig):
    value = rig.data.get(styles.REFS_KEY)
    return dict(value.items()) if value is not None else {}


def backup_names():
    return {mesh.name for mesh in bpy.data.meshes
            if mesh.get(styles.OWNER_KEY) == styles.OWNER_VALUE}


def author_mesh(obj):
    """Name-based bindings remain comparable across native save/reopen."""
    result = regional.mesh_raw(obj)
    result['properties'] = regional.properties(obj)
    result['data_properties'] = regional.properties(obj.data)
    result['key_properties'] = regional.properties(obj.data.shape_keys) if obj.data.shape_keys else ()
    result['binding'] = tuple((mod.name, mod.type,
        getattr(mod, 'object', None).name if getattr(mod, 'object', None) else None,
        mod.show_viewport, mod.show_render) for mod in obj.modifiers)
    return result


def bound_author_meshes(rig):
    return {obj.name: author_mesh(obj) for obj in bpy.data.objects
            if obj.type == 'MESH' and any(mod.type == 'ARMATURE' and mod.object == rig
                                         for mod in obj.modifiers)}


def shape_state(rig):
    """All live styles include shared widgets and unrelated source displays."""
    result = {}
    for pb in rig.pose.bones:
        obj = pb.custom_shape
        if obj is None:
            continue
        mesh = obj.data
        attributes = {}
        for name in (ATTRIBUTE, 'Artist foot profile'):
            attribute = mesh.attributes.get(name)
            if attribute is not None:
                attributes[name] = (attribute.domain, attribute.data_type,
                                    tuple(item.value for item in attribute.data))
        result[pb.name] = {
            'names': (obj.name, mesh.name),
            'vertices': tuple(tuple(vertex.co) for vertex in mesh.vertices),
            'edges': tuple(tuple(edge.vertices) for edge in mesh.edges),
            'faces': tuple(tuple(face.vertices) for face in mesh.polygons),
            'attributes': attributes,
            'materials': tuple(material.name if material else None for material in mesh.materials),
            'object_display': (obj.display_type, obj.show_in_front, obj.hide_render,
                               tuple(obj.color), tuple(obj.location), tuple(obj.scale)),
            'display': copy.deepcopy(limb_ik._pose_shape_json_state(pb)),
            'colors': copy.deepcopy(control_colors.capture_bone(pb)),
            'locks': (tuple(pb.lock_location), tuple(pb.lock_rotation), tuple(pb.lock_scale)),
            'rotation_mode': pb.rotation_mode,
            'hidden': (pb.bone.hide, pb.bone.hide_select),
            'transform_flags': tuple((name, getattr(pb, name)) for name in
                ('use_transform_at_custom_shape', 'use_transform_around_custom_shape')
                if hasattr(pb, name)),
            'notes': tuple(owner.get(NOTE) for owner in (pb, pb.bone, obj, mesh)),
        }
    return result


def foot_fit_state(rig):
    return {side: {name: copy.deepcopy(record[name]) for name in
                  ('roll_visual_backup', 'roll_visual_fit') if name in record}
            for side, record in foot_controls.records(rig).items()}


def display_registry_flags(rig):
    rings = limb_fk_visuals.get_record(rig)
    source = limb_ik._load_source_widget_registry(rig)
    return {
        'rings': {(name, entry['kind'], entry['side'])
                  for name, entry in (rings or {}).get('bindings', {}).items()},
        'source': {(name, entry['side']) for name, entry in (source or {}).get('bones', {}).items()},
    }


def selected_generated(rig, selection):
    keys = set(region._keys(selection))
    return {bone.name for bone in rig.data.bones
            if ((bone.get(limb_ik.OWNER_KEY) == limb_ik.OWNER_VALUE
                 and (bone.get(limb_ik.KIND_KEY), bone.get(limb_ik.SIDE_KEY)) in keys)
                or (bone.get(foot_controls.OWNER_KEY) == foot_controls.OWNER_VALUE
                    and ('LEG', bone.get(foot_controls.SIDE_KEY)) in keys))}


def protected_names(rig, selection):
    inventory = limb_ik._validate_inventory(rig)
    excluded = selected_generated(rig, selection)
    for key in region._keys(selection):
        excluded.update(region._native_scope(rig, inventory['rigs'][key]['chain']))
    # Finished fingers must remain exact even though they are native descendants
    # of a hand and therefore fall inside the generic arm edit allowance.
    return (set(rig.data.bones.keys()) - excluded) | {'index01.L'}


def customize(rig):
    customize_feet(rig)
    inventory = limb_ik._validate_inventory(rig)
    seen = set()
    for index, (key, data) in enumerate(sorted(inventory['rigs'].items())):
        for role, name in (('target', data['target'].name), ('pole', data['pole'].name)):
            pb = rig.pose.bones[name]
            obj, mesh = pb.custom_shape, pb.custom_shape.data
            if mesh.name not in seen:
                seen.add(mesh.name)
                for vertex in mesh.vertices:
                    vertex.co.x = vertex.co.x * 1.13 + .007
                    vertex.co.y = vertex.co.y * .91 - .005
                    vertex.co.z += .002 * (vertex.index % 3)
                attribute = mesh.attributes.new(ATTRIBUTE, 'FLOAT', 'POINT')
                for item_index, item in enumerate(attribute.data):
                    item.value = .1875 + item_index * .015625
                material = bpy.data.materials.new('Artist regional widget material')
                mesh.materials.append(material)
                mesh[NOTE] = 'Keep this shared widget geometry'
                mesh.update()
            obj[NOTE] = 'Keep this widget object'
            pb[NOTE] = f'Keep {key!r} {role}'
            pb.bone[NOTE] = f'Keep {key!r} {role} bone'
            pb.custom_shape_scale_xyz = (1.1 + index * .025, .9, 1.05)
            pb.custom_shape_translation = (.011, -.017, .023)
            pb.custom_shape_rotation_euler = (.035, -.045, .055)
            pb.use_custom_shape_bone_size = False
            if hasattr(pb, 'custom_shape_wire_width'):
                pb.custom_shape_wire_width = 3.0
            pb.lock_location, pb.lock_rotation, pb.lock_scale = (
                (True, False, True), (False, True, False), (True, True, False))
            pb.rotation_mode = 'XYZ'
            pb.color.palette = 'CUSTOM'
            pb.color.custom.normal = (.13, .31, .49)
            pb.color.custom.select = (.23, .41, .59)
            pb.color.custom.active = (.33, .51, .69)
            for flag in ('use_transform_at_custom_shape', 'use_transform_around_custom_shape'):
                if hasattr(pb, flag):
                    setattr(pb, flag, not getattr(pb, flag))
    regional.update(rig)
    limb_ik._validate_inventory(rig)


class BodyRegionStylesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not bpy.app.background:
            raise RuntimeError('Regional style checks require isolated background Blender.')
        character_designer.register()

    def make(self):
        rig, mesh = regional.BodyRegionTests().make(corrective=False)
        bind_probe_surface(rig)
        customize(rig)
        return rig, mesh

    def snapshot(self, rig, mesh, selection):
        names = protected_names(rig, selection)
        regional.update(rig)
        return {
            'rest': copy.deepcopy(control_pose_assets.native_rest(rig)),
            'ids': regional.native_ids(rig, mesh),
            'protected': durable(regional.bone_state(rig, names)),
            'author': bound_author_meshes(rig),
            'shapes': shape_state(rig), 'fit': foot_fit_state(rig),
            'display_flags': display_registry_flags(rig),
            'actions': regional.actions(rig),
            'skin': {name: regional.matrix(matrix) for name, matrix in body_setup._native_skin(rig).items()},
            'surfaces': world_surfaces(rig), 'backups': backup_names(),
            'counts': {name: len(values) for name, values in resources().items()},
        }

    def assert_preserved(self, rig, mesh, selection, before, *, rest=None,
                         ids=True, resource_counts=True):
        regional.update(rig)
        self.assertEqual(region.state(rig, selection)['status'], 'BOUND')
        self.assertEqual(control_pose_assets.native_rest(rig), before['rest'] if rest is None else rest)
        if ids:
            self.assertEqual(regional.native_ids(rig, mesh), before['ids'])
        changes = regional.changed_fields(durable(regional.bone_state(rig, before['protected'])), before['protected'])
        self.assertFalse(changes, f'Protected hand/other region changed: {changes!r}')
        self.assertEqual(bound_author_meshes(rig), before['author'])
        self.assertEqual(shape_state(rig), before['shapes'])
        self.assertEqual(foot_fit_state(rig), before['fit'])
        self.assertEqual(display_registry_flags(rig), before['display_flags'])
        self.assertEqual(regional.actions(rig), before['actions'])
        self.assertFalse(refs(rig))
        self.assertEqual(backup_names(), before['backups'])
        if resource_counts:
            self.assertEqual({name: len(values) for name, values in resources().items()}, before['counts'])
        limb_ik._validate_inventory(rig)

    def test_one_click_rebuild_each_selected_arm_or_leg_keeps_styles_and_finished_hand(self):
        for selection in ('LEGS', 'LEFT_LEG', 'ARMS', 'LEFT_ARM'):
            with self.subTest(region=selection):
                rig, mesh = self.make()
                before = self.snapshot(rig, mesh, selection)
                region.rebuild(bpy.context, rig, selection)
                self.assert_preserved(rig, mesh, selection, before)
                regional.BodyRegionTests().assertDeformationRecovered(
                    rig, before['skin'], before['surfaces'])

    def test_durable_release_edit_save_reopen_bind_keeps_mesh_id_backups_and_styles(self):
        rig, mesh = self.make()
        selection = 'LEFT_LEG'
        before = self.snapshot(rig, mesh, selection)
        names = rig.name, mesh.name
        region.release(bpy.context, rig, selection)
        self.assertEqual(region.state(rig, selection)['status'], 'RELEASED')
        stored = refs(rig)
        self.assertTrue(stored)
        self.assertTrue(all(isinstance(value, bpy.types.Mesh) for value in stored.values()))
        self.assertTrue(all(not value.use_fake_user for value in stored.values()))
        receipt = json.loads(rig.data[region.RECORD_KEY])['limbs']['LEG:L']
        self.assertIsInstance(receipt['styles'], dict)
        regional.BodyRegionTests().shorten(rig, ('L',))
        # Compare the same complete native-Rest schema used by snapshot() and
        # assert_preserved(); calibration.native_rest stores head/tail/z rows
        # rather than control_pose_assets' matrix and relationship records.
        edited_rest = copy.deepcopy(control_pose_assets.native_rest(rig))
        saved_refs = {name: mesh.name for name, mesh in stored.items()}
        with tempfile.TemporaryDirectory(prefix='cd-region-styles-') as folder:
            filepath = str(Path(folder) / 'released_styled_leg.blend')
            self.assertEqual(bpy.ops.wm.save_as_mainfile(filepath=filepath), {'FINISHED'})
            self.assertEqual(bpy.ops.wm.open_mainfile(filepath=filepath), {'FINISHED'})
            rig, mesh = bpy.data.objects[names[0]], bpy.data.objects[names[1]]
            bpy.context.view_layer.objects.active = rig
            rig.select_set(True)
            if bpy.context.mode != 'POSE':
                bpy.ops.object.mode_set(mode='POSE')
            self.assertEqual(region.state(rig, selection)['status'], 'RELEASED')
            stored = refs(rig)
            self.assertEqual({name: mesh.name for name, mesh in stored.items()}, saved_refs)
            self.assertTrue(all(isinstance(value, bpy.types.Mesh) for value in stored.values()))
            self.assertTrue(all(not value.use_fake_user for value in stored.values()))
            # Native IDs are replaced by file loading; compare native inputs
            # and semantic references while guarding the new live IDs on bind.
            before['ids'] = regional.native_ids(rig, mesh)
            region.bind(bpy.context, rig, selection)
            self.assert_preserved(rig, mesh, selection, before,
                                  rest=edited_rest, resource_counts=False)

    def rollback_after(self, stage):
        rig, mesh = self.make()
        selection = 'LEGS'
        before, ids = regional.scene_state(rig, mesh), regional.native_ids(rig, mesh)
        shapes, fitting, names = shape_state(rig), foot_fit_state(rig), resources()
        backups = backup_names()
        recreated = selected_generated(rig, selection)
        skin = {name: regional.matrix(matrix) for name, matrix in body_setup._native_skin(rig).items()}
        surfaces = world_surfaces(rig)
        real = getattr(region, stage)
        called = []

        def fail_after_native_change(*args, **kwargs):
            result = real(*args, **kwargs)
            called.append(True)
            raise ValueError('Injected regional style failure after ' + stage)

        with patch.object(region, stage, side_effect=fail_after_native_change):
            with self.assertRaisesRegex(ValueError, 'Injected regional style failure'):
                region.rebuild(bpy.context, rig, selection)
        self.assertTrue(called)
        regional.BodyRegionTests().assertRecoveredScene(
            regional.scene_state(rig, mesh), before, recreated=recreated)
        regional.BodyRegionTests().assertDeformationRecovered(rig, skin, surfaces)
        self.assertEqual(regional.native_ids(rig, mesh), ids)
        self.assertEqual(shape_state(rig), shapes)
        self.assertEqual(foot_fit_state(rig), fitting)
        self.assertEqual(resources(), names)
        self.assertEqual(backup_names(), backups)
        self.assertFalse(refs(rig))
        self.assertIsNone(region._OWNER_OPERATION)
        limb_ik._validate_inventory(rig)

    def test_failure_after_release_recovers_styles_graph_and_resource_inventory(self):
        self.rollback_after('release')

    def test_failure_after_bind_recovers_original_styles_and_leaves_no_backup(self):
        self.rollback_after('bind')

    def assert_bad_backup_refused(self, damage):
        rig, mesh = self.make()
        region.release(bpy.context, rig, 'LEFT_LEG')
        values = refs(rig)
        self.assertTrue(values)
        backup = next(iter(values.values()))
        if damage == 'MISSING':
            bpy.data.meshes.remove(backup, do_unlink=True)
        else:
            self.assertTrue(backup.vertices)
            backup.vertices[0].co.x += .123
            backup.update()
        before, ids = regional.scene_state(rig, mesh), regional.native_ids(rig, mesh)
        names = resources()
        receipt = rig.data[region.RECORD_KEY]
        with self.assertRaises(ValueError):
            region.bind(bpy.context, rig, 'LEFT_LEG')
        self.assertEqual(rig.data[region.RECORD_KEY], receipt)
        self.assertEqual(region.state(rig, 'LEFT_LEG')['status'], 'RELEASED')
        self.assertEqual(regional.scene_state(rig, mesh), before)
        self.assertEqual(regional.native_ids(rig, mesh), ids)
        self.assertEqual(resources(), names)

    def test_missing_style_backup_refuses_without_consuming_release_receipt(self):
        self.assert_bad_backup_refused('MISSING')

    def test_modified_style_backup_refuses_without_consuming_release_receipt(self):
        self.assert_bad_backup_refused('MODIFIED')

    def test_real_retained_action_recovery_owner_and_historical_motion_stay_exact(self):
        rig, mesh = self.make()
        action, motion = retain_action(rig)
        action_before = body_original_owner._animation_fingerprint(action)
        motion_before = unity_animation._rest_state(motion)
        self.assertEqual(rig.data.users, 2)
        before = self.snapshot(rig, mesh, 'LEFT_LEG')
        region.rebuild(bpy.context, rig, 'LEFT_LEG')
        self.assert_preserved(rig, mesh, 'LEFT_LEG', before)
        self.assertEqual(rig.data.users, 2)
        self.assertTrue(region.verified_single_armature_user(rig))
        self.assertEqual(body_original_owner._animation_fingerprint(action), action_before)
        self.assertEqual(unity_animation._rest_state(motion), motion_before)

    def test_rejected_rebuild_retains_authenticated_current_revision_with_action_owners(self):
        from character_designer import body_original_mode as original, body_setup_removal
        from test_original_leg_rest_return_blender import make_fixture, edit_legs

        rig, mesh = make_fixture()
        action, motion = retain_action(rig)
        original.enter(bpy.context, rig)
        edit_legs(rig)
        self.assertTrue(original.leave(bpy.context, rig))
        bind_probe_surface(rig)
        customize(rig)
        self.assertEqual(rig.data.users, 2)
        self.assertTrue(limb_ik._single_armature_user(rig))
        self.assertTrue(region.verified_single_armature_user(rig))
        proof = rig.data[region.RECOVERY_OWNER_KEY]
        full_rest_before = copy.deepcopy(unity_animation._rest_state(rig.data))
        action_before = body_original_owner._animation_fingerprint(action)
        motion_before = unity_animation._rest_state(motion)
        before, ids = regional.scene_state(rig, mesh), regional.native_ids(rig, mesh)
        widget_styles, resources_before = shape_state(rig), resources()
        recreated = selected_generated(rig, 'LEGS')
        checked = body_setup_removal._check_surfaces
        calls = []

        def reject_after_real_surface_check(*args, **kwargs):
            try:
                result = checked(*args, **kwargs)
            except ValueError:
                calls.append('actual surface guard rejected')
                raise
            calls.append(result)
            if len(calls) == 1:
                raise ValueError('Injected retained-owner surface guard failure')
            return result

        with patch.object(body_setup_removal, '_check_surfaces', side_effect=reject_after_real_surface_check):
            with self.assertRaises(ValueError) as refusal:
                region.rebuild(bpy.context, rig, 'LEGS')
        self.assertTrue(calls)
        self.assertRegex(str(refusal.exception),
                         'Injected retained-owner surface guard failure|Removing controls would change')
        authenticated = limb_ik._single_armature_user(rig)
        revision_authenticated = region.verified_single_armature_user(rig)
        full_rest_after = unity_animation._rest_state(rig.data)
        proof_rest = json.loads(proof)['rest']
        exact_rest_changes = [name for name in full_rest_before
                              if full_rest_before[name] != full_rest_after.get(name)]
        rejected_proof_bones = [name for name in proof_rest if name not in full_rest_after
                               or not unity_animation._same_current_rest_bone(proof_rest[name], full_rest_after[name])]
        print('REGION_OWNER_ROLLBACK_AUTH', json.dumps({
            'native_users': rig.data.users, 'single_user_authenticated': authenticated,
            'revision_authenticated': revision_authenticated,
            'revision_raw_unchanged': rig.data[region.RECOVERY_OWNER_KEY] == proof,
            'refusal': str(refusal.exception),
            'all_native_and_generated_rest_bones': len(full_rest_before),
            'exact_rest_changed_bones': exact_rest_changes,
            'proof_rejected_bones': rejected_proof_bones,
            'artist_opened': False, 'artist_saved': False}), flush=True)
        self.assertEqual(regional.native_ids(rig, mesh), ids)
        self.assertEqual(rig.data[region.RECOVERY_OWNER_KEY], proof)
        regional.BodyRegionTests().assertRecoveredScene(
            regional.scene_state(rig, mesh), before, recreated=recreated)
        self.assertEqual(shape_state(rig), widget_styles)
        self.assertEqual(resources(), resources_before)
        self.assertFalse(refs(rig))
        self.assertFalse(body_setup._ATOMIC_STACK)
        self.assertIsNone(region._OWNER_OPERATION)
        self.assertEqual(body_original_owner._animation_fingerprint(action), action_before)
        self.assertEqual(unity_animation._rest_state(motion), motion_before)
        self.assertTrue(authenticated, 'Rejected rebuild invalidated the retained-user proof.')
        self.assertTrue(revision_authenticated, 'Rejected rebuild invalidated the saved current revision.')

    def test_public_rebuild_is_one_undoable_operation_with_exact_styles_and_no_backups(self):
        rig, mesh = self.make()
        rig_name, mesh_name = rig.name, mesh.name
        bpy.ops.object.mode_set(mode='OBJECT')
        previous_undo = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True

        def state(current_rig, current_mesh):
            result = regional.scene_state(current_rig, current_mesh)
            result['mesh'] = author_mesh(current_mesh)
            result['bound_meshes'] = bound_author_meshes(current_rig)
            result['all_widget_styles'] = shape_state(current_rig)
            result['foot_fit'] = foot_fit_state(current_rig)
            result['display_flags'] = display_registry_flags(current_rig)
            result['native_rest'] = control_pose_assets.native_rest(current_rig)
            result['native_id_labels'] = (
                current_rig.name_full, current_rig.data.name_full,
                current_mesh.name_full, current_mesh.data.name_full,
                current_mesh.data.shape_keys.name_full if current_mesh.data.shape_keys else None)
            result['style_references'] = regional.value(current_rig.data.get(styles.REFS_KEY))
            result['style_backups'] = backup_names()
            return durable(result)

        def reacquire():
            # Native Undo can replace complete ID allocations. Authenticate the
            # live named IDs and their bindings, not invalid pre-Undo wrappers.
            current_rig, current_mesh = bpy.data.objects[rig_name], bpy.data.objects[mesh_name]
            self.assertEqual([obj for obj in bpy.data.objects
                              if obj.type == 'ARMATURE' and obj.data == current_rig.data],
                             [current_rig])
            armature_modifiers = [mod for mod in current_mesh.modifiers if mod.type == 'ARMATURE']
            self.assertTrue(armature_modifiers)
            self.assertTrue(all(mod.object is current_rig for mod in armature_modifiers))
            self.assertFalse(refs(current_rig))
            return current_rig, current_mesh

        try:
            self.assertEqual(bpy.ops.ed.undo_push(message='Before public selected-part rebuild'), {'FINISHED'})
            before = state(rig, mesh)
            protected = self.snapshot(rig, mesh, 'LEFT_LEG')
            original_bones = set(rig.data.bones.keys())
            self.assertEqual(bpy.ops.character_designer.body_region(
                region='LEFT_LEG', action='REBUILD'), {'FINISHED'})
            self.assert_preserved(rig, mesh, 'LEFT_LEG', protected)
            self.assertEqual(set(rig.data.bones.keys()), original_bones)
            self.assertEqual(bpy.ops.ed.undo_push(message='After public selected-part rebuild'), {'FINISHED'})
            after = state(rig, mesh)

            self.assertEqual(bpy.ops.ed.undo(), {'FINISHED'})
            rig, mesh = reacquire()
            self.assertEqual(region.state(rig, 'LEFT_LEG')['status'], 'BOUND')
            regional.BodyRegionTests().assertRecoveredScene(state(rig, mesh), before)
            self.assert_preserved(rig, mesh, 'LEFT_LEG', protected, ids=False)

            self.assertEqual(bpy.ops.ed.redo(), {'FINISHED'})
            rig, mesh = reacquire()
            self.assertEqual(region.state(rig, 'LEFT_LEG')['status'], 'BOUND')
            regional.BodyRegionTests().assertRecoveredScene(state(rig, mesh), after)
            self.assert_preserved(rig, mesh, 'LEFT_LEG', protected, ids=False)
        finally:
            bpy.context.preferences.edit.use_global_undo = previous_undo

    def test_shared_widget_and_existing_fk_source_bindings_survive_single_side_rebuild(self):
        rig, mesh = self.make()
        inventory = limb_ik._validate_inventory(rig)
        pole_name = inventory['rigs'][('LEG', 'L')]['pole'].name
        target = rig.pose.bones[pole_name].custom_shape
        shared = [pb.name for pb in rig.pose.bones if pb.custom_shape == target]
        self.assertGreater(len(shared), 1, 'Fixture must exercise the actual shared Pole widget.')
        before = self.snapshot(rig, mesh, 'LEFT_LEG')
        self.assertTrue(before['display_flags']['rings'])
        self.assertTrue(before['display_flags']['source'])
        region.rebuild(bpy.context, rig, 'LEFT_LEG')
        self.assert_preserved(rig, mesh, 'LEFT_LEG', before)
        after_widget = rig.pose.bones[pole_name].custom_shape
        # Recreating one helper changes native collection iteration order.
        # Binding identity is the exact set of unique pose-bone names.
        self.assertEqual(sorted(pb.name for pb in rig.pose.bones if pb.custom_shape == after_widget),
                         sorted(shared))

    def test_one_outer_checkpoint_owns_success_and_rejected_release_or_bind_cleanup(self):
        for stage in ('success', 'success_object', 'release_failure', 'bind_failure'):
            fail = stage not in {'success', 'success_object'}
            with self.subTest(stage=stage):
                rig, mesh = self.make()
                bpy.ops.object.mode_set(mode='OBJECT' if stage == 'success_object' else 'POSE')
                original_mode = bpy.context.mode
                transaction = regional.transaction
                before = regional.scene_state(rig, mesh)
                ids, all_styles, names = regional.native_ids(rig, mesh), shape_state(rig), resources()
                recreated = selected_generated(rig, 'LEGS')
                skin = {name: regional.matrix(matrix) for name, matrix in body_setup._native_skin(rig).items()}
                surfaces = world_surfaces(rig)
                events = []
                real_capture, real_restore, real_discard = transaction.capture, transaction.restore, transaction.discard
                real_cleanup, real_release, real_bind = styles.cleanup, region._release_graph, region.bind

                def capture(*args, **kwargs):
                    events.append('capture')
                    return real_capture(*args, **kwargs)

                def restore(*args, **kwargs):
                    events.append('restore')
                    # Retain the newly captured style token until the outer
                    # recovery outcome is known; its native Mesh IDs are the
                    # fallback if the original-widget recovery itself fails.
                    self.assertTrue(refs(rig))
                    result = real_restore(*args, **kwargs)
                    events.append('restored')
                    return result

                def discard(*args, **kwargs):
                    events.append('discard')
                    return real_discard(*args, **kwargs)

                def cleanup(*args, **kwargs):
                    events.append('style_cleanup')
                    return real_cleanup(*args, **kwargs)

                def release_graph(*args, **kwargs):
                    result = real_release(*args, **kwargs)
                    if stage == 'release_failure':
                        raise ValueError('Injected rejected release after native graph change')
                    return result

                def bind(*args, **kwargs):
                    result = real_bind(*args, **kwargs)
                    if stage == 'bind_failure':
                        raise ValueError('Injected rejected bind after native graph change')
                    return result

                with patch.object(transaction, 'capture', side_effect=capture), \
                     patch.object(transaction, 'restore', side_effect=restore), \
                     patch.object(transaction, 'discard', side_effect=discard), \
                     patch.object(styles, 'cleanup', side_effect=cleanup), \
                     patch.object(region, '_release_graph', side_effect=release_graph), \
                     patch.object(region, 'bind', side_effect=bind):
                    if fail:
                        with self.assertRaisesRegex(ValueError, 'Injected rejected '):
                            region.rebuild(bpy.context, rig, 'LEGS')
                    else:
                        region.rebuild(bpy.context, rig, 'LEGS')
                self.assertEqual(events.count('capture'), 1, events)
                self.assertEqual(events.count('restore'), 1 if fail else 0, events)
                self.assertEqual(events.count('discard'), 1, events)
                self.assertFalse(body_setup._ATOMIC_STACK)
                self.assertIsNone(region._OWNER_OPERATION)
                self.assertEqual(bpy.context.mode, original_mode)
                self.assertFalse(refs(rig))
                self.assertFalse(backup_names())
                self.assertEqual(regional.native_ids(rig, mesh), ids)
                self.assertEqual(shape_state(rig), all_styles)
                self.assertEqual(resources(), names)
                if fail:
                    self.assertLess(events.index('restored'), events.index('discard'), events)
                    self.assertLess(events.index('discard'), events.index('style_cleanup'), events)
                    regional.BodyRegionTests().assertRecoveredScene(
                        regional.scene_state(rig, mesh), before, recreated=recreated)
                regional.BodyRegionTests().assertDeformationRecovered(rig, skin, surfaces)
                limb_ik._validate_inventory(rig)
                print('REGION_SINGLE_CHECKPOINT', json.dumps({
                    'stage': stage, 'events': events,
                    'artist_opened': False, 'artist_saved': False}), flush=True)


if __name__ == '__main__':
    if '--test' in sys.argv:
        selected = sys.argv[sys.argv.index('--test') + 1]
        if selected.startswith('test_'):
            selected = 'BodyRegionStylesTests.' + selected
        suite = unittest.defaultTestLoader.loadTestsFromName(selected, module=sys.modules[__name__])
    else:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(BodyRegionStylesTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError('Regional style preservation tests failed')
