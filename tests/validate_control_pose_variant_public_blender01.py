"""Disposable native offset/worklist acceptance; never opens an artist file.

Launch only with run_trusted_blender_validation.py and a complete approved117
projection plus this new module as an explicit overlay. Native FK fixture only;
not an artist, IK solver, FPS or full current-Rig-history acceptance.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace as NS
from unittest.mock import patch

import bpy
from mathutils import Euler, Quaternion


def require(value, message):
    if not value:
        raise AssertionError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--projection', required=True, type=Path)
    parser.add_argument('--projection-sha256', required=True)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    require(bpy.app.background and not bpy.data.filepath, 'Fresh private background process required.')
    require(sha(args.projection) == args.projection_sha256, 'Projection pin differs.')
    manifest = json.loads(args.projection.read_text(encoding='utf8'))
    package = Path(manifest['source_root'])
    require(all(sha(package / name) == digest for name, digest in manifest['files'].items()), 'Frozen source differs.')
    sys.path.insert(0, str(package.parent))
    import character_designer
    from character_designer import control_pose_offset as offset, control_pose_assets as poses
    from character_designer import animation_retarget as retarget, animation_worklist as worklist
    from character_designer import animation_link as links, animation_worklist_fingerprint as fingerprint
    from character_designer import animation_worklist_target as target, unity_animation as ua
    require(Path(offset.__file__).resolve() == (package / 'control_pose_offset.py').resolve(), 'Offset loaded from wrong root.')
    report = dict(schema='character-designer.native-pose-offset/1', passed=False,
        projection=str(args.projection), projection_sha256=sha(args.projection),
        module_file=offset.__file__, checks={}, errors=[], cleanup_errors=[],
        artist_file_opened=False, artist_operations=False, preferences_saved=False,
        autoexec_enabled='--enable-autoexec' in sys.argv,
        factory_saved_autoexec_preference=bool(bpy.context.preferences.filepaths.use_scripts_auto_execute),
        limits=['Small free-FK fixture; not current-Rig checkpoint derivation or IK solver acceptance.',
                'Changed rotation channels are sampled; no subframe Bezier equivalence or FPS claim.'])
    checks = report['checks']
    registered = False
    try:
        character_designer.register()
        registered = True
        data = bpy.data.armatures.new('OffsetNativeData')
        rig = bpy.data.objects.new('OffsetNativeRig', data)
        bpy.context.scene.collection.objects.link(rig)
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        rig.select_set(True)
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        previous = None
        for index, name in enumerate(('Upper.L', 'Lower.L', 'Hand.L')):
            bone = data.edit_bones.new(name)
            bone.head, bone.tail = (0., index, 0.), (0., index + 1., 0.)
            bone.parent = previous
            previous = bone
        torso = data.edit_bones.new('Torso')
        torso.head, torso.tail = (1., 0., 0.), (1., 1., 0.)
        bpy.ops.object.mode_set(mode='OBJECT')
        for pb in rig.pose.bones:
            pb.rotation_mode = 'XYZ'
            pb.select = False
        rig.pose.bones['Upper.L'].rotation_mode = 'QUATERNION'
        poses.capture_baseline(rig)
        bpy.context.scene.character_designer_setup.rig = rig
        original = bpy.data.actions.new('Offset original Custom')
        original.use_fake_user = True
        slot, bag = retarget._new_channelbag(original, rig)
        frames = [1., 5., 9.]
        quaternions = [Euler((0., 0., angle), 'XYZ').to_quaternion() for angle in (0., .4, 0.)]
        for index in range(4):
            retarget._write_curve(bag, rig.pose.bones['Upper.L'].path_from_id('rotation_quaternion'), index,
                                  frames, [value[index] for value in quaternions])
        retarget._write_curve(bag, rig.pose.bones['Lower.L'].path_from_id('rotation_euler'), 2,
                              frames, [0., -.3, 0.])
        unrelated = bag.fcurves.new(rig.pose.bones['Torso'].path_from_id('location'), index=0)
        for frame, value in zip(frames, (0., .125, 0.)):
            point = unrelated.keyframe_points.insert(frame, value)
            point.interpolation = 'BEZIER'
            point.handle_left_type = point.handle_right_type = 'FREE'
            point.handle_left = frame - .3, value - .07
            point.handle_right = frame + .4, value + .03
        unrelated.update()
        extra_slot = original.slots.new(id_type='OBJECT', name='OtherSlot')
        extra_bag = original.layers[0].strips[0].channelbags.new(extra_slot)
        retarget._write_curve(extra_bag, 'location', 1, [1., 9.], [.2, .3])
        ad = rig.animation_data_create()
        ad.action, ad.action_slot = original, slot
        ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.
        other = bpy.data.objects.new('Other Action User', None)
        bpy.context.scene.collection.objects.link(other)
        other.animation_data_create().action = original
        other.animation_data.action_slot = extra_slot
        source_export = original.copy()
        source_export.name = 'Offset immutable Source'
        cache = args.report.parent / 'source_cache.blend'
        require(not cache.exists(), 'Use a new native fixture directory.')
        bpy.data.libraries.write(str(cache), {source_export}, fake_user=True)
        with bpy.data.libraries.load(str(cache), link=True) as (src, dst):
            dst.actions = [source_export.name]
        source = dst.actions[0]
        saved = bpy.context.scene.character_designer_animation_worklist
        saved.rig = rig
        row = saved.items.add()
        row.item_id, row.name, row.rig, row.side = '11111111111111111111111111111111', 'Walk', rig, 'CUSTOM'
        row.custom_action, row.custom_slot = original, slot.handle
        row.source_action, row.source_slot = source, slot.handle
        row.source_file, row.source_hash = str(cache), sha(cache)
        row.link_identity = json.dumps(dict(schema=links.SCHEMA,
            linkId='22222222222222222222222222222222', targetGuid='3' * 32, clipGuid='4' * 32,
            clipLocalId=1, sourcePackage=str(args.report.parent / 'unused_packet.json'), sourcePackageSha256='5' * 64))
        saved.active_index = 0
        asset_action = bpy.data.actions.new('Relax pose asset')
        asset_slot, asset_bag = retarget._new_channelbag(asset_action, rig)
        rotations = {'Upper.L': Euler((.8, 0., 0.), 'XYZ').to_quaternion(),
                     'Lower.L': Euler((0., .4, 0.), 'XYZ').to_quaternion()}
        for name, quaternion in rotations.items():
            for index, value in enumerate(quaternion):
                retarget._write_curve(asset_bag, rig.pose.bones[name].path_from_id('rotation_quaternion'), index, [1.], [value])
        asset_action[poses.ASSET_METADATA] = json.dumps(dict(version=1, source_object=rig.name,
            source_slot=asset_slot.identifier, rest=poses.native_rest(rig), scope='SELECTED',
            names=list(rotations), include_fingers=False))
        asset_action.asset_mark()
        asset = NS(local_id=asset_action, name='Relax', full_library_path='')
        bpy.context.scene.frame_set(5)
        original_hashes = {s.handle: fingerprint.fingerprint_action(original, s.handle)['action_fingerprint'] for s in original.slots}
        rest_before = ua._rest_state(data)
        asset_before = fingerprint.fingerprint_action(asset_action, asset_slot.handle)['action_fingerprint']
        result = offset.create(bpy.context, rig, asset, bone_names=list(rotations))
        copy_action, variant_id = result['action'], result['item_id']
        require(copy_action is not original and len(saved.items) == 2, 'New editable variant was not retained.')
        for frame, source_q in zip(frames, quaternions):
            bpy.context.scene.frame_set(int(frame))
            expected = Quaternion((1., 0., 0., 0.)).slerp(rotations['Upper.L'], .5) @ source_q
            actual = rig.pose.bones['Upper.L'].rotation_quaternion.copy()
            require(actual.rotation_difference(expected).angle < 2e-5, 'Fixed quaternion delta did not retain the original swing.')
        checks['managed_fixed_delta_motion_loop_and_default_half_strength'] = True
        for _ in range(2):
            worklist.activate(bpy.context, row.item_id, 'CUSTOM')
            require(rig.animation_data.action is original, 'Original row did not reactivate.')
            worklist.activate(bpy.context, variant_id, 'CUSTOM')
            require(rig.animation_data.action is copy_action, 'Offset row did not reactivate.')
        checks['ordinary_worklist_two_switches'] = True
        require(all(fingerprint.fingerprint_action(original, handle)['action_fingerprint'] == digest
                    for handle, digest in original_hashes.items()), 'Original Custom curves changed.')
        require(ua._rest_state(data) == rest_before and other.animation_data.action is original,
                'Rest or another Action user changed.')
        require(fingerprint.fingerprint_action(asset_action, asset_slot.handle)['action_fingerprint'] == asset_before,
                'Pose asset changed.')
        checks['original_source_asset_other_user_rest_and_unaffected_handles_exact'] = True
        worklist.activate(bpy.context, row.item_id, 'CUSTOM')
        bpy.context.scene.frame_set(5)
        before_ids = {action.as_pointer() for action in bpy.data.actions}
        real_write = offset._write
        def fail_write(*args):
            real_write(*args)
            raise RuntimeError('injected after new-Action curve rewrite')
        with patch.object(offset, '_write', fail_write):
            try:
                offset.create(bpy.context, rig, asset, bone_names=list(rotations))
                raise AssertionError('Fault did not raise.')
            except RuntimeError as error:
                require('injected' in str(error), str(error))
        require({action.as_pointer() for action in bpy.data.actions} == before_ids
                and rig.animation_data.action is original and bpy.context.scene.frame_current == 5,
                'Failed rewrite left owned IDs or changed the live binding.')
        checks['rewrite_failure_owned_cleanup'] = True
        capture_before = target._capture(bpy.context, rig)
        real_activate = worklist.activate
        def fail_activate(*args, **kwargs):
            real_activate(*args, **kwargs)
            raise RuntimeError('injected after new-variant activation')
        with patch.object(worklist, 'activate', fail_activate):
            try:
                offset.create(bpy.context, rig, asset, bone_names=list(rotations))
                raise AssertionError('Activation fault did not raise.')
            except RuntimeError as error:
                require('injected' in str(error), str(error))
        require(rig.animation_data.action is original and bpy.context.scene.frame_current == 5
                and {action.as_pointer() for action in bpy.data.actions} == before_ids,
                'Activation failure did not restore binding/frame/owned IDs.')
        require(target._capture(bpy.context, rig) == capture_before, 'Fresh current capture did not restore exactly.')
        checks['late_activation_failure_fresh_restore'] = True
        zero = offset.create(bpy.context, rig, asset, strength=0, bone_names=list(rotations), activate=False)
        require(all(fingerprint.fingerprint_action(zero['action'], handle)['action_fingerprint'] == digest
                    for handle, digest in original_hashes.items()), 'Zero strength changed original curve content.')
        worklist.remove(bpy.context, zero['item_id'])
        checks['zero_strength_exact_copy'] = True
        managed_metadata = asset_action[poses.ASSET_METADATA]
        del asset_action[poses.ASSET_METADATA]
        legacy = offset.create(bpy.context, rig, asset, bone_names=list(rotations), activate=False)
        require(legacy['compatibility'] == 'legacy_rig_baseline_and_slot_only', 'Legacy limit was not reported.')
        worklist.remove(bpy.context, legacy['item_id'])
        asset_action[poses.ASSET_METADATA] = managed_metadata
        checks['standard_legacy_asset_uses_existing_baseline_and_slot_gate'] = True
        source_pin = sha(cache)
        source_pointer = source.as_pointer()
        worklist.remove(bpy.context, variant_id)
        require(sha(cache) == source_pin and source.as_pointer() == source_pointer and row.source_action is source,
                'Removing a variant deleted or reassigned the shared Source.')
        worklist.activate(bpy.context, row.item_id, 'CUSTOM')
        checks['remove_variant_preserves_shared_source_cache_and_original'] = True
        shared = offset.create(bpy.context, rig, asset, bone_names=list(rotations), activate=False)
        worklist.activate(bpy.context, row.item_id, 'SOURCE')
        bound_slot = rig.animation_data.action_slot.handle
        worklist.remove(bpy.context, shared['item_id'])
        require(rig.animation_data.action is source and rig.animation_data.action_slot.handle == bound_slot,
                'Deleting an inactive variant cleared the selected original Source binding.')
        worklist.activate(bpy.context, row.item_id, 'CUSTOM')
        current_variant = offset.create(bpy.context, rig, asset, bone_names=list(rotations))
        worklist.remove(bpy.context, current_variant['item_id'])
        require(rig.animation_data.action is None and sha(cache) == source_pin,
                'Deleting the current variant did not clear only its owned binding.')
        worklist.activate(bpy.context, row.item_id, 'CUSTOM')
        checks['source_active_remove_other_variant_and_current_variant_cleanup'] = True
        serialized = offset.create(bpy.context, rig, asset, bone_names=list(rotations))
        serialized_id = serialized['item_id']
        private_file = args.report.parent / 'offset_private.blend'
        require('FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(private_file)), 'Private save failed.')
        require('FINISHED' in bpy.ops.wm.open_mainfile(filepath=str(private_file), use_scripts=True), 'Private reopen failed.')
        saved = bpy.context.scene.character_designer_animation_worklist
        rig = saved.rig
        worklist.activate(bpy.context, serialized_id, 'CUSTOM')
        worklist.activate(bpy.context, '11111111111111111111111111111111', 'CUSTOM')
        worklist.activate(bpy.context, serialized_id, 'CUSTOM')
        checks['private_save_reopen_and_worklist_variants'] = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from control_pose_variant_native_cases import run_public_variant_cases
        run_public_variant_cases(report, args.report)
        report['passed'] = True
    except Exception:
        report['errors'].append(traceback.format_exc())
    finally:
        if registered:
            try:
                character_designer.unregister()
            except Exception:
                report['cleanup_errors'].append(traceback.format_exc())
        report['passed'] = report['passed'] and not report['cleanup_errors']
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'passed': report['passed'], 'checks': checks, 'errors': report['errors'], 'cleanup_errors': report['cleanup_errors']}), flush=True)
    if not report['passed']:
        raise RuntimeError('Native Pose Offset validation failed; preserve its receipt.')


if __name__ == '__main__':
    main()
