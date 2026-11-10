"""One disposable paired sleeve tests strict current-native Forearm admission.

Launch through run_trusted_blender_validation.py with explicit --source-root,
--expected-current-sha and a fresh --result after the runner's -- separator.
This script loads no artist file and saves no scene or preferences. It invokes
the new read-only helper directly. The product runtime integration is a separate
check: no adapter or replacement of a historical Main validator is used here.
"""
import argparse
from array import array
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import traceback

import bpy


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--expected-current-sha', required=True)
    parser.add_argument('--result', type=Path, required=True)
    return parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])


def main():
    options = arguments()
    result = options.result.resolve()
    if result.exists():
        raise ValueError('Use a new result path; earlier evidence is retained.')
    source = options.source_root.resolve(strict=True)
    current_path = source / 'forearm_current_inventory.py'
    current_sha = hashlib.sha256(current_path.read_bytes()).hexdigest()
    if current_sha != options.expected_current_sha.lower():
        raise ValueError('The exact current-native candidate SHA did not match.')
    if not bpy.app.background or bpy.data.filepath:
        raise ValueError('This fixture requires a fresh private background Blender.')
    if any(name == 'character_designer' or name.startswith('character_designer.') for name in sys.modules):
        raise ValueError('Use a cold isolated namespace for the candidate package.')
    report = dict(schema='forearm.current-native.tiny/1', utc=datetime.now(timezone.utc).isoformat(),
        passed=False, checks=[], source_root=str(source), current_sha256=current_sha,
        blender=bpy.app.version_string, artist_file_loaded=False,
        scene_saved=False, preferences_saved=False, runtime_test_adapter=False,
        helper_called_directly=True, helper_scene_writes=0,
        runtime_numeric_functions_changed=False, cleanup_errors=[])
    package = runtime = None

    def require(name, passed, details=None):
        report['checks'].append(dict(name=name, passed=bool(passed), details=details))
        if not passed:
            raise AssertionError(name)

    def reject(name, operation, snapshot):
        before = snapshot()
        error = None
        try:
            operation()
        except ValueError as exc:
            error = str(exc)
        require(name, error is not None, error)
        require(name + ':author_exact', snapshot() == before)

    try:
        spec = importlib.util.spec_from_file_location('character_designer', source / '__init__.py',
                                                     submodule_search_locations=[str(source)])
        package = importlib.util.module_from_spec(spec)
        sys.modules['character_designer'] = package
        spec.loader.exec_module(package)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_forearm_twist_blender as fixtures
        from test_forearm_twist_ranges_blender import bilateral_fixture
        from character_designer import (forearm_current_inventory as current,
            forearm_twist, limb_ik,
            animation_worklist_target as target, unity_animation as ua)
        runtime = forearm_twist
        fixture = bilateral_fixture()
        rig, mesh = fixture['armature'], fixture['mesh']
        fixtures.activate_mesh(rig)
        settings = bpy.context.window_manager.character_designer_limb_ik
        settings.build_method = 'DIRECT_PREROLL'
        require('real_direct_controls_generated',
                bpy.ops.character_designer.limb_ik_build_all() == {'FINISHED'})
        fixtures.activate_mesh(mesh)
        route = limb_ik._validate_inventory(rig)['rigs'][('ARM', 'L')]
        fixture['rig'], fixture['target'] = route, rig.pose.bones[route['target'].name]
        runtime.start_test(bpy.context, mesh, 'L', symmetry=True)
        runtime.finish_test(bpy.context, confirm=True)
        require('both_real_calibrations', set(runtime._records(mesh)) == {'L', 'R'})
        require('small_generated_sleeve', len(mesh.data.vertices) == 246,
                {'vertices': len(mesh.data.vertices), 'bones': len(rig.data.bones)})
        require('all_product_modules_from_explicit_source', all(
            Path(module.__file__).resolve().is_relative_to(source)
            for name, module in tuple(sys.modules.items())
            if name == 'character_designer' or name.startswith('character_designer.')
            if getattr(module, '__file__', None)))

        action = bpy.data.actions.new('Disposable old captured Action')
        slot = action.slots.new(id_type='OBJECT', name=rig.name)
        ad = rig.animation_data_create()
        ad.action, ad.action_slot = action, slot
        fixtures.activate_mesh(rig)
        captured = target._capture(bpy.context, rig)
        target._store(rig, dict(version=1, rig=rig, scene=bpy.context.scene,
            view_layer=bpy.context.view_layer.name, workspace_identity='private native fixture',
            capture=captured, packet_path='unused private historical packet',
            packet_sha256='0' * 64, motion_data=rig.data,
            motion_rest=ua._rest_state(rig.data), mapped_names=sorted(rig.data.bones.keys())))
        ad.action = None
        fixtures.activate_mesh(mesh)
        fixtures.pose_target(fixture, 33., bend=.17, side_wave=-.08)
        runtime.update_runtime(bpy.context.scene)
        fixtures.assert_runtime_geometry(fixture, 'Healthy current-native reference')

        def outputs():
            values = []
            for side, record in sorted(runtime._records(mesh).items()):
                key = mesh.data.shape_keys.key_blocks[record['key']]
                coords = array('f', [0.]) * (len(key.data) * 3)
                key.data.foreach_get('co', coords)
                values.append((side, key.name, float(key.value), bool(key.mute), coords.tobytes()))
            return tuple(values)

        healthy_output = outputs()
        require('healthy_current_native_inventory_passes',
                current.validate_current_native(mesh, rig) is not None)
        require('healthy_reader_keeps_all_outputs', outputs() == healthy_output)
        # Model the established editing-file state: historical text remains,
        # while the entire Main reference table has been removed. Two passive
        # old Action references explain extra native data users without a
        # second Object binding. None grants current or restoration authority.
        del rig[target.REFS]
        passive = []
        for number in (1, 2):
            historical = bpy.data.actions.new('Passive historical data owner ' + str(number))
            historical[ua.ORIGINAL_DATA_KEY] = rig.data
            historical[target.ACTION_RIG_KEY] = rig
            passive.append(historical)
        require('entire_Main_reference_table_absent', target.REFS not in rig)
        require('passive_Action_data_references_retained', all(
            item.get(ua.ORIGINAL_DATA_KEY) is rig.data and item.get(target.ACTION_RIG_KEY) is rig
            for item in passive))

        def protected():
            return (runtime._records(mesh), rig[target.SESSION],
                tuple((key, None if value is None else value.as_pointer())
                      for key, value in rig.get(target.REFS, {}).items()),
                fixtures.pose_snapshot(rig), fixtures.structure_snapshot(rig, mesh),
                fixtures.key_snapshot(mesh, tuple(mesh.data.shape_keys.key_blocks.keys())))

        reject('ordinary_Main_Rest_still_refuses', lambda: target.control_rest_view(rig), protected)
        before = protected()
        before_outputs = outputs()
        inventory = current.validate_current_native(mesh, rig, sides={'L'})
        require('real_both_ARM_inventory_returned', inventory['scope'] == 'FOREARM_CURRENT_NATIVE'
                and set(inventory['rigs']) == {('ARM', 'L'), ('ARM', 'R')})
        require('new_inventory_is_read_only', protected() == before)

        require('helper_did_not_change_any_runtime_output', outputs() == before_outputs)
        reject('ordinary_Main_Rest_remains_refused', lambda: target.control_rest_view(rig), protected)

        # Each test changes one native input, then proves this read-only reader
        # rejects without changing any other current author or output values.
        relation = next(entry for entry in limb_ik._owned_constraint_records(rig)
                        if entry[2].get('kind') == 'ARM' and entry[2].get('side') == 'L'
                        and entry[2].get('role') == 'IK')
        owner, constraint, _record = relation
        driver = next(curve for curve in rig.animation_data.drivers
                      if curve.data_path == constraint.path_from_id('influence'))
        expression = driver.driver.expression
        driver.driver.expression = 'ik_fk * 0.5'
        try:
            reject('actual_FK_driver_edit_refused', lambda: current.validate_current_native(mesh, rig), protected)
        finally:
            driver.driver.expression = expression
        target_bone = rig.data.bones[route['target'].name]
        old_owner = target_bone[limb_ik.OWNER_KEY]
        target_bone[limb_ik.OWNER_KEY] = 'foreign fixture owner'
        try:
            reject('actual_control_owner_edit_refused', lambda: current.validate_current_native(mesh, rig), protected)
        finally:
            target_bone[limb_ik.OWNER_KEY] = old_owner
        old_subtarget = constraint.subtarget
        constraint.subtarget = 'foreign missing target'
        try:
            reject('actual_solver_binding_edit_refused', lambda: current.validate_current_native(mesh, rig), protected)
        finally:
            constraint.subtarget = old_subtarget
        registry_raw = rig.data[limb_ik.DIRECT_REST_KEY]
        registry = json.loads(registry_raw)
        direct = registry['limbs'][inventory['rigs'][('ARM', 'L')]['rig_id']]
        state = direct['applied'][fixture['lower_name']]
        direction = [state['tail'][i] - state['head'][i] for i in range(3)]
        state['tail'] = [state['tail'][i] + direction[i] * .01 for i in range(3)]
        rig.data[limb_ik.DIRECT_REST_KEY] = json.dumps(registry)
        try:
            reject('actual_Direct_Rest_metadata_edit_refused',
                   lambda: current.validate_current_native(mesh, rig), protected)
        finally:
            rig.data[limb_ik.DIRECT_REST_KEY] = registry_raw
        local_route = inventory['rigs'][('ARM', 'L')]
        endpoint_names = set(local_route['chain']) | {local_route['target'].name, local_route['pole'].name}
        endpoint_names.update(bone.name for field in ('line', 'display', 'solver_target',
            'mch_upper', 'mch_lower', 'ori_upper', 'ori_lower') if (bone := local_route.get(field)) is not None)
        if local_route.get('auto_rotation_space') == 'PARENT_DELTA':
            endpoint_names.add(limb_ik._wrist_helper_name('L'))
        for name in sorted(endpoint_names):
            pose = rig.pose.bones[name]
            extra = pose.constraints.new('LIMIT_ROTATION')
            extra.name = 'Unverified fixture constraint'
            try:
                reject('actual_extra_local_constraint_refused:' + name,
                       lambda: current.validate_current_native(mesh, rig), protected)
            finally:
                pose.constraints.remove(extra)
        shared = bpy.data.objects.new('Foreign actual data binding', rig.data)
        try:
            reject('second_actual_Object_binding_refused', lambda: current.validate_current_native(mesh, rig), protected)
        finally:
            bpy.data.objects.remove(shared)
        raw = mesh[runtime.RECORD_KEY]
        records = runtime._records(mesh)
        records['R']['rest'][0][1][0] += .1
        mesh[runtime.RECORD_KEY] = json.dumps(records)
        try:
            reject('right_Rest_checked_from_left_query',
                   lambda: current.validate_current_native(mesh, rig, sides={'L'}), protected)
        finally:
            mesh[runtime.RECORD_KEY] = raw
        require('current_inventory_recovers_after_each_restored_native_input',
                current.validate_current_native(mesh, rig) is not None)
        reject('ordinary_Main_Rest_still_refuses_after_all_checks',
               lambda: target.control_rest_view(rig), protected)
        native = bilateral_fixture()
        native_rig, native_mesh = native['armature'], native['mesh']
        runtime.start_test(bpy.context, native_mesh, 'L', symmetry=True)
        runtime.finish_test(bpy.context, confirm=True)
        require('bare_native_FK_has_paired_calibration',
                set(runtime._records(native_mesh)) == {'L', 'R'})
        native_before = (fixtures.pose_snapshot(native_rig),
            fixtures.structure_snapshot(native_rig, native_mesh),
            fixtures.key_snapshot(native_mesh, tuple(native_mesh.data.shape_keys.key_blocks.keys())),
            native_mesh[runtime.RECORD_KEY])
        require('bare_native_FK_keeps_original_resolver',
                current.validate_current_native(native_mesh, native_rig) is None)
        require('bare_native_FK_admission_is_read_only', native_before == (
            fixtures.pose_snapshot(native_rig), fixtures.structure_snapshot(native_rig, native_mesh),
            fixtures.key_snapshot(native_mesh, tuple(native_mesh.data.shape_keys.key_blocks.keys())),
            native_mesh[runtime.RECORD_KEY]))
        report['passed'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if runtime is not None and runtime._SESSION is not None:
            try:
                runtime.finish_test(bpy.context, confirm=False)
            except BaseException:
                report['cleanup_errors'].append(traceback.format_exc())
        if package is not None and hasattr(bpy.types.WindowManager, 'character_designer_forearm_twist'):
            try:
                package.unregister()
            except BaseException:
                report['cleanup_errors'].append(traceback.format_exc())
        if report['cleanup_errors']:
            report['passed'] = False
        result.parent.mkdir(parents=True, exist_ok=True)
        with result.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps(dict(passed=report['passed'], result=str(result), checks=len(report['checks']))), flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
