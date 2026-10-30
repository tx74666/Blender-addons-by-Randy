"""Focused native author-channel regression on an explicit frozen projection.

Run only through tools/run_trusted_blender_validation.py --background. The
native12 fixture is pinned; this derivative does not change that earlier harness
or its failed/passed evidence. No artist file or preferences are opened/saved.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bpy

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / 'tests/validate_hair_resegment_blender52.py'
FIXTURE_SHA = '0d29f26abfda2e172eeacf8fcc4c2c549f4ff23abe71c758ecc264c224620166'
sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
assert sha(FIXTURE) == FIXTURE_SHA, 'The pinned native12 fixture changed.'
scope = {'__name__': 'hair_author_channels_native_fixture', '__file__': str(FIXTURE)}
fixture_text = FIXTURE.read_text(encoding='utf-8')
prefix = fixture_text.split('\nresult = None\n', 1)[0]
assert prefix != fixture_text
exec(compile(prefix, str(FIXTURE), 'exec'), scope)
args, cd, service, retarget, PACKAGE = (scope[name] for name in
    ('args', 'cd', 'service', 'retarget', 'PACKAGE'))
manifest = scope['manifest']
report = dict(schema='character-designer.hair-author-channels-native/1', passed=False,
    started_utc=datetime.now(timezone.utc).isoformat(), harness_sha256=sha(__file__),
    fixture=str(FIXTURE), fixture_sha256=FIXTURE_SHA,
    projection=str(args.projection), projection_sha256=args.projection_sha,
    source_package=str(PACKAGE), source_pins=manifest['files'], cases=[], errors=[],
    artist_file_opened=False, artist_modified=False, scene_saved=False,
    preferences_saved=False, autoexec_enabled=True, background=bpy.app.background,
    limits=['Synthetic native fixture only; no artist/full-character/dynamics/FPS acceptance.',
            'Native GUI Undo/Redo is a separate check; this harness verifies the transaction and reached rollback.'])


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def keyed_custom(owner, name, start, end):
    owner[name] = start
    owner.keyframe_insert(data_path='["' + name + '"]', frame=1.)
    owner[name] = end
    owner.keyframe_insert(data_path='["' + name + '"]', frame=8.)


def driver_metadata(owner):
    ad = owner.animation_data
    if ad is None:
        return ()
    def target_metadata(target):
        result = {}
        for field in ('id_type', 'data_path', 'bone_target', 'transform_type',
                      'transform_space', 'rotation_mode', 'use_fallback_value', 'fallback_value'):
            if hasattr(target, field):
                result[field] = scope['plain'](getattr(target, field))
        result['id'] = scope['plain'](target.id)
        return result
    return tuple((curve.data_path, curve.array_index, curve.mute, curve.extrapolation,
        curve.driver.type, curve.driver.expression, curve.driver.use_self,
        tuple((variable.name, variable.type, tuple(target_metadata(t) for t in variable.targets))
              for variable in curve.driver.variables)) for curve in ad.drivers)


def owner_metadata(owners):
    return {owner.name_full + ':' + owner.bl_rna.identifier: dict(
        animation=scope['animation'](owner), drivers=driver_metadata(owner)) for owner in owners}


def action_state():
    return {action.as_pointer(): dict(curves=scope['curves'](action),
        slots=tuple((slot.handle, slot.identifier, slot.target_id_type) for slot in action.slots))
        for action in bpy.data.actions}


def external_mesh(absolute):
    data = bpy.data.meshes.new('External Author Mesh Data')
    data.from_pydata([(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new('External Author Mesh', data)
    bpy.context.scene.collection.objects.link(obj)
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='External Author Key')
    key.data[0].co.z = .15
    keys = data.shape_keys
    keys.use_relative = not absolute
    if absolute:
        keys.eval_time = 1.
        keys.keyframe_insert(data_path='eval_time', frame=1.)
        keys.eval_time = 9.
        keys.keyframe_insert(data_path='eval_time', frame=8.)
    else:
        key.value = .1
        key.keyframe_insert(data_path='value', frame=1.)
        key.value = .4
        key.keyframe_insert(data_path='value', frame=8.)
    return obj, keys, key


def multi_slot_action(owner, name, *, nla):
    """The first bag deliberately has an unresolvable path for this owner."""
    action = bpy.data.actions.new(name)
    unused = action.slots.new(id_type='OBJECT', name='Unrelated unused slot')
    selected = action.slots.new(id_type='OBJECT', name=owner.name)
    layer = action.layers.new('Author Slot Isolation')
    keystrip = layer.strips.new(type='KEYFRAME')
    unused_bag = keystrip.channelbags.new(unused)
    selected_bag = keystrip.channelbags.new(selected)
    retarget._write_curve(unused_bag, '["UnusedSlotOnlySentinel"]', 0, [1., 8.], [2., 3.])
    retarget._write_curve(selected_bag, 'location', 1 if nla else 0,
                          [1., 8.], [.1, .4])
    if not nla:
        retarget._write_curve(selected_bag, 'delta_location', 0, [1., 8.], [.03, .09])
    ad = owner.animation_data_create()
    if nla:
        track = ad.nla_tracks.new()
        track.name = 'External retained scaled NLA'
        strip = track.strips.new('External sampled NLA clip', 1, action)
        strip.action_slot = selected
        strip.blend_type = 'ADD'
        strip.influence = .7
        strip.scale = 1.25
        strip.repeat = 1.5
        ad.use_nla = True
    else:
        ad.action, ad.action_slot = action, selected
    return action, selected, unused


def author_values(empty, rig, pose, constraint, keys, key, absolute, include_nla):
    result = dict(external_empty_location_x=float(empty.location.x),
        external_empty_delta_location_x=float(empty.delta_location.x),
        hair_rig_custom=float(rig['ArtistControl']),
        hair_pose_custom=float(pose['ArtistControl']),
        hair_constraint_influence=float(constraint.influence),
        external_driver_output_scale_z=float(empty.scale.z))
    result['external_key_eval_time' if absolute else 'external_key_value'] = float(
        keys.eval_time if absolute else key.value)
    if include_nla:
        result['external_nla_location_y'] = float(empty.location.y)
    return result


def set_external_temporary_values(empty, keys, key, absolute, include_nla):
    empty.location.x, empty.delta_location.x = .777, .919
    if absolute:
        keys.eval_time = 17.75
    else:
        key.value = .727
    if include_nla:
        empty.location.y = .515


def run_case(label, *, rollback, absolute=False, include_nla=False, activate=True):
    source, rig, record = scope['fixture']()
    empty = bpy.data.objects.new('External Author Empty', None)
    bpy.context.scene.collection.objects.link(empty)
    direct_action, selected_slot, unused_slot = multi_slot_action(empty, 'External Direct Slotted Action', nla=False)
    nla_action = multi_slot_action(empty, 'External NLA Slotted Action', nla=True) if include_nla else None
    keyed_custom(rig, 'ArtistControl', .1, .4)
    pose = rig.pose.bones['Arm']
    keyed_custom(pose, 'ArtistControl', .2, .5)
    constraint = pose.constraints.new('LIMIT_ROTATION')
    constraint.name = 'Authored Influence Regression'
    constraint.owner_space = 'LOCAL'
    constraint.use_limit_x = True
    constraint.min_x, constraint.max_x = 0., .01
    constraint.influence = .2
    constraint.keyframe_insert(data_path='influence', frame=1.)
    constraint.influence = .6
    constraint.keyframe_insert(data_path='influence', frame=8.)
    external, keys, key = external_mesh(absolute)
    curve = empty.driver_add('scale', 2)
    curve.driver.type = 'SCRIPTED'
    curve.driver.expression = '1 + control'
    variable = curve.driver.variables.new()
    variable.name, variable.type = 'control', 'SINGLE_PROP'
    variable.targets[0].id = rig
    variable.targets[0].data_path = '["ArtistControl"]'
    bpy.context.scene.frame_set(3, subframe=.25)
    evaluated = author_values(empty, rig, pose, constraint, keys, key, absolute, include_nla)
    set_external_temporary_values(empty, keys, key, absolute, include_nla)
    rig['ArtistControl'], pose['ArtistControl'], constraint.influence = .888, .666, .555
    bpy.context.view_layer.update()
    values_before = author_values(empty, rig, pose, constraint, keys, key, absolute, include_nla)
    owners = (source, source.data, source.data.shape_keys, rig, rig.data, empty, external, external.data, keys)
    metadata_before = owner_metadata(owners)
    source_before = scope['source_author'](source, rig)
    external_properties_before = scope['plain'](empty)
    action_before, ids_before, context_before = action_state(), scope['ids'](), scope['context_state']()
    frame_before = (bpy.context.scene.frame_current, bpy.context.scene.frame_subframe)
    evaluated_rig = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    author_arm_world = (evaluated_rig.matrix_world @ evaluated_rig.pose.bones['Arm'].matrix).copy()
    reached = []
    operation_error = None
    result = None
    def fail_publish(*unused, **unused_kw):
        reached.append(True)
        raise RuntimeError('author-channel regression reached publish rollback')
    if rollback:
        with patch.object(service, '_publish', side_effect=fail_publish):
            try:
                service.create(bpy.context, source, bone_count=7, frame_start=1., frame_end=8., activate=activate)
            except service.HairResegmentError as error:
                operation_error = str(error)
    else:
        result = service.create(bpy.context, source, bone_count=6, frame_start=1., frame_end=8., activate=activate)
    actual = author_values(empty, rig, pose, constraint, keys, key, absolute, include_nla)
    action_after = action_state()
    metadata_after = owner_metadata(owners)
    arm_error = None
    if result:
        evaluated_copy = result['armature'].evaluated_get(bpy.context.evaluated_depsgraph_get())
        sampled_arm_world = evaluated_copy.matrix_world @ evaluated_copy.pose.bones['Arm'].matrix
        arm_error = max(abs(author_arm_world[row][col] - sampled_arm_world[row][col])
                        for row in range(4) for col in range(4))
    checks = dict(author_channels=actual == values_before,
        original_source_author=scope['source_author'](source, rig) == source_before,
        original_action_slots_curves=all(action_after.get(key) == value for key, value in action_before.items()),
        original_action_nla_driver_metadata=metadata_after == metadata_before,
        unused_slot_property_not_created=scope['plain'](empty) == external_properties_before
            and 'UnusedSlotOnlySentinel' not in empty,
        frame=(bpy.context.scene.frame_current, bpy.context.scene.frame_subframe) == frame_before,
        auto_key_preserved=bpy.context.scene.tool_settings.use_keyframe_insert_auto,
        rollback_reached=(bool(reached) and operation_error is not None
            and 'reached publish rollback' in operation_error) if rollback else True,
        rollback_owned_ids=scope['ids']() == ids_before if rollback else True,
        context=scope['context_state']() == context_before if rollback or not activate else
            (bpy.context.mode == 'POSE' and bpy.context.active_object is result['armature']),
        sampled_current_author_constraint=arm_error is None or arm_error <= 3e-5)
    case = dict(case=label, rollback=rollback, absolute_keys=absolute,
        external_nla=include_nla, activate=activate, passed=all(checks.values()), checks=checks,
        expected_current_author_values=values_before, actual_values_after=actual,
        native_evaluated_values_before_temporary_edit=evaluated,
        channel_preserved={key: actual[key] == value for key, value in values_before.items()},
        author_arm_world_matrix_max_error=arm_error, operation_error=operation_error,
        action_slot_handles=dict(selected=selected_slot.handle, unused=unused_slot.handle,
            nla_selected=nla_action[1].handle if nla_action else None),
        original_metadata_sha256=checksum(metadata_before),
        metadata_after_sha256=checksum(metadata_after),
        original_action_slots_curves_sha256=checksum(action_before),
        original_source_author_sha256=checksum(source_before),
        context_before=scope['plain'](context_before), context_after=scope['plain'](scope['context_state']()))
    if result:
        switches = []
        for switch_name, switch in (
            ('show_original', lambda: service.show_original(bpy.context, result['mesh'])),
            ('show_six', lambda: service.show_version(bpy.context, source, result['collection']))):
            try:
                switch()
                current = author_values(empty, rig, pose, constraint, keys, key, absolute, include_nla)
                switch_checks = dict(author_channels=current == values_before,
                    source_author=scope['source_author'](source, rig) == source_before,
                    action_nla_driver_metadata=owner_metadata(owners) == metadata_before,
                    original_actions=all(action_state().get(key) == value
                                         for key, value in action_before.items()))
                switches.append(dict(switch=switch_name, passed=all(switch_checks.values()),
                    checks=switch_checks, actual_values=current))
            except Exception:
                switches.append(dict(switch=switch_name, passed=False, error=traceback.format_exc()))
        case['version_switches'] = switches
        checks['version_switches'] = all(switch['passed'] for switch in switches)

        # This transition deliberately finalizes only the private family's
        # temporary keyed channels, so a cleanup refusal cannot be attributed to
        # its already-covered internal offsets. External offsets are then set
        # again. The sampled-current-frame assertion above made no frame change.
        bpy.context.scene.frame_set(3, subframe=.25)
        empty.driver_remove('scale', 2)
        set_external_temporary_values(empty, keys, key, absolute, include_nla)
        bpy.context.view_layer.update()
        cleanup_values = author_values(empty, rig, pose, constraint, keys, key, absolute, include_nla)
        cleanup_source = scope['source_author'](source, rig)
        cleanup_metadata = owner_metadata(owners)
        cleanup_actions, cleanup_ids = action_state(), scope['ids']()
        cleanup_context, cleanup_visibility = scope['context_state'](), scope['visibility']()
        cleanup_error = None
        try:
            scope['binding'].remove_generated_copies(bpy.context, source)
        except (ValueError, RuntimeError) as error:
            cleanup_error = str(error)
        cleanup_checks = dict(refused=cleanup_error is not None,
            author_channels=author_values(empty, rig, pose, constraint, keys, key, absolute, include_nla) == cleanup_values,
            source_author=scope['source_author'](source, rig) == cleanup_source,
            action_nla_metadata=owner_metadata(owners) == cleanup_metadata,
            actions=action_state() == cleanup_actions, owned_ids=scope['ids']() == cleanup_ids,
            context=scope['context_state']() == cleanup_context,
            visibility=scope['visibility']() == cleanup_visibility)
        case['external_temporary_cleanup_refusal'] = dict(passed=all(cleanup_checks.values()),
            checks=cleanup_checks, error=cleanup_error,
            family_current_channels_explicitly_native_finalized=True,
            private_driver_explicitly_removed_before_cleanup_probe=True,
            expected_current_values=cleanup_values,
            actual_values=author_values(empty, rig, pose, constraint, keys, key, absolute, include_nla))
        checks['external_temporary_cleanup_refusal'] = all(cleanup_checks.values())
    case['passed'] = all(checks.values())
    report['cases'].append(case)


registered = False
try:
    cd.register()
    registered = True
    for label, absolute, nla, activate in (
        ('relative_keys_direct_slots', False, False, True),
        ('absolute_keys_direct_slots', True, False, False),
        ('relative_keys_external_nla_slots', False, True, True)):
        for rollback in (False, True):
            try:
                run_case(label + ('_rollback' if rollback else '_success'), rollback=rollback,
                         absolute=absolute, include_nla=nla, activate=activate)
            except Exception:
                report['cases'].append(dict(case=label + ('_rollback' if rollback else '_success'),
                    passed=False, unexpected_error=traceback.format_exc()))
    report['passed'] = all(case['passed'] for case in report['cases']) and len(report['cases']) == 6
except Exception:
    report['errors'].append(traceback.format_exc())
finally:
    if registered:
        try:
            cd.unregister()
        except Exception:
            report['errors'].append(traceback.format_exc())
    report['source_pins_unchanged'] = all(sha(PACKAGE / name) == pin for name, pin in manifest['files'].items())
    report['passed'] = report['passed'] and not report['errors'] and report['source_pins_unchanged']
    report['finished_utc'] = datetime.now(timezone.utc).isoformat()
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], cases=len(report['cases']), errors=report['errors'],
        source_pins_unchanged=report['source_pins_unchanged'])))
if not report['passed']:
    raise RuntimeError('Hair author-channel native regression failed; inspect the retained report.')
