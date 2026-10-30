"""Real current-Rig checkpoint derivation in a small disposable Body fixture.

Uses existing native fixture constructors only after the frozen approved
package is imported. No product admission or authorization guard is mocked.
The sole I/O redirect confines existing Source-cache creation to this run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace as NS
from unittest.mock import patch

import bpy
from mathutils import Euler


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
    require(bpy.app.background and not bpy.data.filepath, 'Fresh private factory Blender required.')
    require(sha(args.projection) == args.projection_sha256, 'Projection pin differs.')
    manifest = json.loads(args.projection.read_text(encoding='utf8'))
    package = Path(manifest['source_root'])
    require(all(sha(package / name) == digest for name, digest in manifest['files'].items()), 'Frozen package changed.')
    sys.path.insert(0, str(package.parent))
    import character_designer
    from character_designer import control_pose_offset as offset, control_pose_assets as poses
    from character_designer import animation_worklist as worklist, animation_worklist_revision as revision
    from character_designer import body_rest_adoption as adoption, limb_ik, limb_ik_fk as match
    from character_designer import animation_retarget as retarget, animation_worklist_target as target
    from character_designer import unity_animation as ua
    report = dict(schema='character-designer.native-current-pose-offset/1', passed=False,
        checks={}, errors=[], cleanup_errors=[], projection_sha256=sha(args.projection),
        module_file=offset.__file__, autoexec_enabled='--enable-autoexec' in sys.argv,
        factory_saved_autoexec_preference=bool(bpy.context.preferences.filepaths.use_scripts_auto_execute),
        artist_operations=False, artist_file_opened=False, preferences_saved=False,
        limits=['Disposable current-Rig FK Body fixture; not the artist model, IK offset or FPS acceptance.'])
    checks = report['checks']
    registered = False
    try:
        character_designer.register()
        registered = True
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_current_leg_rest_adoption_blender import CurrentLegRestAdoptionTests, history, body_state
        require(Path(limb_ik.__file__).resolve() == (package / 'limb_ik.py').resolve(), 'Fixture selected canonical runtime.')
        rig, mesh, old_action, motion, _previous = CurrentLegRestAdoptionTests().make()
        adoption.accept_current_legs(bpy.context, rig)
        require(adoption.verified_revision(rig), 'Native explicit Rest adoption did not validate.')
        for side in ('L', 'R'):
            match.switch_limb(bpy.context, rig, ('ARM', side), 'FK', keyframe=False)
        inventory = limb_ik._validate_inventory(rig)
        upper_name = inventory['rigs'][('ARM', 'L')]['chain'][0]
        upper = rig.pose.bones[upper_name]
        slot = worklist._slot(old_action)
        bag = offset._bag(old_action, slot.handle)
        source_rotations = [Euler((0., 0., value), 'XYZ').to_quaternion() for value in (0., .2, 0.)]
        rotation_path = 'rotation_quaternion' if upper.rotation_mode == 'QUATERNION' else 'rotation_euler'
        for index in range(4 if rotation_path == 'rotation_quaternion' else 3):
            components = [q[index] if rotation_path == 'rotation_quaternion' else q.to_euler(upper.rotation_mode)[index] for q in source_rotations]
            retarget._write_curve(bag, upper.path_from_id(rotation_path), index, [1., 5., 9.], components)
        old_action.use_fake_user = True
        saved = bpy.context.scene.character_designer_animation_worklist
        saved.workspace_id = '12345678123456781234567812345678'
        saved.model_sha256 = 'a' * 64
        row = saved.items.add()
        row.item_id, row.name, row.rig, row.side = '11111111111111111111111111111111', 'Old historical local', rig, 'CUSTOM'
        row.custom_action, row.custom_slot = old_action, slot.handle
        saved.active_index = 0
        rig.animation_data.action = None
        real_resource = bpy.utils.user_resource
        def owned_resource(kind, *, path='', create=False):
            if kind == 'DATAFILES' and path == 'character_designer/animation_sources':
                directory = args.report.parent / 'owned_source_cache'
                if create:
                    directory.mkdir(parents=True, exist_ok=True)
                return str(directory)
            return real_resource(kind, path=path, create=create)
        with patch.object(bpy.utils, 'user_resource', owned_resource):
            current = revision.create(bpy.context, row.item_id, activate_new=True)
        value_before = revision.validate(bpy.context, current.item_id)
        source = current.source_action
        source_file = Path(current.source_file)
        require(source_file.resolve().is_relative_to(args.report.parent.resolve()), 'Source cache escaped private output.')
        source_pin = sha(source_file)
        current_id = current.item_id
        current_custom = current.custom_action
        old_checkpoint = current_custom[revision.CHECKPOINT]
        old_checkpoint_raw = old_checkpoint[revision.RECORD]
        old_checkpoint_refs = dict(old_checkpoint[revision.REFS])
        old_history = history(rig, old_action, motion)
        old_body = body_state(mesh)
        rest_before = ua._rest_state(rig.data)
        asset_action = bpy.data.actions.new('Managed arm relax')
        asset_slot, asset_bag = retarget._new_channelbag(asset_action, rig)
        quaternion = Euler((.3, .05, 0.), 'XYZ').to_quaternion()
        for index, value in enumerate(quaternion):
            retarget._write_curve(asset_bag, upper.path_from_id('rotation_quaternion'), index, [1.], [value])
        asset_action[poses.ASSET_METADATA] = json.dumps(dict(version=1, source_object=rig.name,
            source_slot=asset_slot.identifier, rest=poses.native_rest(rig), names=[upper_name],
            scope='SELECTED', include_fingers=False))
        asset_action.asset_mark()
        asset = NS(local_id=asset_action, name='Relax', full_library_path='')
        target_pb = rig.pose.bones[inventory['rigs'][('ARM', 'L')]['target'].name]
        old_mode = target_pb.get('ik_fk')
        mode_driver = target_pb.driver_add('["ik_fk"]')
        mode_driver.driver.expression = '1.0 if frame > 5 else 0.0'
        bpy.context.scene.frame_set(1)
        require(abs(float(target_pb['ik_fk'])) <= 1e-7, 'Driver fixture did not start in FK.')
        ids_before_refusal = {action.as_pointer() for action in bpy.data.actions}
        try:
            offset.create(bpy.context, rig, asset, bone_names=[upper_name])
            raise AssertionError('Frame-driven FK/IK mode was accepted.')
        except ValueError:
            pass
        require({action.as_pointer() for action in bpy.data.actions} == ids_before_refusal,
                'Driver refusal created an Action.')
        bpy.context.scene.frame_set(9)
        require(abs(float(target_pb['ik_fk']) - 1.) <= 1e-7, 'Driver fixture did not switch to IK later.')
        target_pb.driver_remove('["ik_fk"]')
        target_pb['ik_fk'] = old_mode
        checks['external_frame_driven_fk_ik_refused_while_current_fk'] = True
        for pb in rig.pose.bones:
            pb.select = False
        bpy.context.scene.frame_set(5)
        before_current_ids = {item.item_id for item in saved.items}
        result = offset.create(bpy.context, rig, asset, bone_names=[upper_name])
        new_id = result['item_id']
        new_row = worklist._item(saved, new_id)
        value_after = revision.validate(bpy.context, new_id)
        require(new_row.current_rig_revision != current.current_rig_revision
                and new_row.custom_action is not current_custom
                and new_row.custom_action[revision.CHECKPOINT] is not old_checkpoint,
                'The new variant reused old identity/checkpoint.')
        require(new_row.source_action is source and new_row.source_file == current.source_file
                and sha(source_file) == source_pin, 'The exact shared Source changed.')
        for key in ('history_raw', 'history_refs', 'historical_owners', 'current_rest', 'current_structure', 'capture', 'old_rows'):
            require(value_after[key] == value_before[key], 'Historical authority field changed: ' + key)
        require(old_checkpoint[revision.RECORD] == old_checkpoint_raw and dict(old_checkpoint[revision.REFS]) == old_checkpoint_refs,
                'The original checkpoint bytes/references changed.')
        checks['new_token_row_action_checkpoint_preserve_historical_authority'] = True
        for _ in range(2):
            worklist.activate(bpy.context, current_id, 'CUSTOM')
            require(rig.animation_data.action is current_custom, 'Old current Custom did not reactivate.')
            worklist.activate(bpy.context, new_id, 'CUSTOM')
            revision.validate(bpy.context, new_id)
        checks['new_current_variant_two_switches_and_old_return'] = True
        worklist.activate(bpy.context, current_id, 'CUSTOM')
        bpy.context.scene.frame_set(5)
        before_ids = {action.as_pointer() for action in bpy.data.actions}
        before_capture = target._capture(bpy.context, rig)
        real_activate = worklist.activate
        def fail_activate(*args, **kwargs):
            real_activate(*args, **kwargs)
            raise RuntimeError('injected current-variant activation failure')
        with patch.object(worklist, 'activate', fail_activate):
            try:
                offset.create(bpy.context, rig, asset, bone_names=[upper_name])
                raise AssertionError('Current variant activation fault did not raise.')
            except RuntimeError as error:
                require('injected' in str(error), str(error))
        require({action.as_pointer() for action in bpy.data.actions} == before_ids
                and target._capture(bpy.context, rig) == before_capture,
                'Current variant fault left owned IDs or changed the current capture.')
        revision.validate(bpy.context, current_id)
        checks['current_variant_failure_fresh_restore_and_owned_cleanup'] = True
        require(history(rig, old_action, motion) == old_history and body_state(mesh) == old_body
                and ua._rest_state(rig.data) == rest_before, 'Author/history protection changed.')
        checks['paint_keys_rest_old_actions_and_history_exact'] = True
        worklist.remove(bpy.context, new_id)
        require({item.item_id for item in saved.items} == before_current_ids and sha(source_file) == source_pin
                and current.source_action is source, 'Variant removal lost shared Source or original rows.')
        revision.validate(bpy.context, current_id)
        checks['remove_current_variant_shared_source_preserved'] = True
        serialized = offset.create(bpy.context, rig, asset, bone_names=[upper_name])
        serial_id = serialized['item_id']
        private_file = args.report.parent / 'current_offset_private.blend'
        require('FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(private_file)), 'Private current save failed.')
        require('FINISHED' in bpy.ops.wm.open_mainfile(filepath=str(private_file), use_scripts=True), 'Private current reopen failed.')
        for chosen in (serial_id, current_id, serial_id):
            revision.validate(bpy.context, chosen)
            worklist.activate(bpy.context, chosen, 'CUSTOM')
        checks['current_variant_private_save_reopen_validate_and_switch'] = True
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
        raise RuntimeError('Current Pose Offset native validation failed; preserve its receipt.')


if __name__ == '__main__':
    main()
