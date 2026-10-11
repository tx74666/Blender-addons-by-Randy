"""Staged isolated B export and numeric provenance. No import-time Blender work.

Artist A/C are never sampled to derive B. The source graph is authenticated
first, then a disposable copy receives canonical inputs and only B's exact
selected FK channels. Only the proved manual Dress wires may be copied as
external evaluation dependencies. The ordinary exporter dependency guard runs
unchanged before and in the disposable background worker.
"""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import math
import os
import re
from pathlib import Path
import struct
import uuid
import subprocess
import tempfile
import time

try:
    from . import animation_composition_contract as contract
    from .animation_composition_controls import (Scope, resolve_scope, validate_scope,
        validate_packet_composition, effective_channel_names, source_rest_sha256, _host, _require)
except ImportError:
    import animation_composition_contract as contract
    from animation_composition_controls import (Scope, resolve_scope, validate_scope,
        validate_packet_composition, effective_channel_names, source_rest_sha256, _host, _require)

SCHEMA = 'cdesigner.composition-b-content/1'
VALUES_SCHEMA = 'cdesigner.composition-effective-values/1'
STAGING = Path(__file__).resolve().parent
# Production files live beside the existing exporter in the add-on package.
# The explicit mount is only for isolated fixtures which import these drafts
# flat before a release projection is constructed. It never selects a runtime
# by artist filename, current working directory or an X validation path.
TEST_ADDON_ROOT = os.environ.get('CHARACTER_DESIGNER_COMPOSITION_TEST_ADDON_ROOT', '')
BASELINE = Path(TEST_ADDON_ROOT).resolve() if TEST_ADDON_ROOT else STAGING
APPROVED_WORKER_SHA256 = 'b2668e204e4e75e71af165e3427eed8a3e2387c2dd2635cbd757d27d748a97be'
APPROVED_MODEL_SHA256 = 'ade4b7627365371877c76d03edcde1229148671c1e7dfdcfe7f64d708f628622'


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def baseline_worker():
    """Use the frozen .24 worker, never an unrelated canonical draft exporter."""
    path = BASELINE / 'animation_export_worker.py'
    _require(path.is_file(), 'Frozen .24 animation exporter is missing; restore the validated fixture runtime.')
    _require(_sha256(path) == APPROVED_WORKER_SHA256 and
             _sha256(path.with_name('unity_export_worker.py')) == APPROVED_MODEL_SHA256,
             'Animation exporter bytes differ from the approved .24 worker/model projection.')
    spec = importlib.util.spec_from_file_location('_cd_composition_baseline_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _curves(action, slot):
    return _host('animation_worklist_fingerprint')._selected_curves(action, slot)


def _action_hash(action, slot):
    proof = _host('animation_worklist_fingerprint').fingerprint_action(action, slot)
    _require(proof['fingerprint_known'], 'Action content is unproved: ' + proof['proof_reason'])
    return proof['action_fingerprint']


def _simple_action(action, slot):
    """M1 has one effective FK layer; multilayer inversion is not implied."""
    _host('animation_worklist_fingerprint')._slot(action, slot)
    if action.is_action_layered:
        _require(len(action.layers) == 1, 'Composition requires one supported B Action layer.')
        layer = action.layers[0]
        _require(getattr(layer, 'influence', 1.) == 1. and
                 getattr(layer, 'mix_mode', 'REPLACE') == 'REPLACE' and
                 len(layer.strips) == 1 and layer.strips[0].type == 'KEYFRAME',
                 'Composition B layer must be a full-weight keyframe replacement.')
    curves = _curves(action, slot)
    keys = [(curve.data_path, curve.array_index) for curve in curves]
    _require(len(set(keys)) == len(keys), 'B has duplicate selected-slot channels.')
    return curves


def _validate_curve(curve):
    _require(not curve.mute and not curve.modifiers and not curve.sampled_points
             and not getattr(curve, 'is_driver', False) and curve.keyframe_points,
             'B editable FK channels need enabled, modifier-free authored keys.')
    _require(curve.extrapolation in {'CONSTANT', 'LINEAR'}, 'Unsupported B extrapolation.')
    for key in curve.keyframe_points:
        _require(key.interpolation in {'CONSTANT', 'LINEAR', 'BEZIER'} and
                 all(math.isfinite(v) for vector in (key.co, key.handle_left, key.handle_right) for v in vector),
                 'B has an unsupported or non-finite effective key.')


def assert_constant_curve(curve, expected=None):
    _validate_curve(curve)
    value = float(curve.keyframe_points[0].co[1])
    _require(expected is None or value == expected, 'B changes an excluded coupled controller input.')
    _require(all(float(key.co[1]) == value and (key.interpolation != 'BEZIER' or
                 float(key.handle_left[1]) == value == float(key.handle_right[1]))
                 for key in curve.keyframe_points),
             'Hold Pose must have constant effective curves, including between keys; prepare a real Hold or use Hold Loop.')


def _validate_action_timing(action, selected, spec):
    """M1 uses the prepared packet's native Action time; it never rescales B."""
    values = {key: action.get(key) for key in
              ('unity_sample_rate', 'unity_start_frame', 'unity_duration')}
    _require(all(type(value) in (int, float) and math.isfinite(value)
                 for value in values.values()),
             'B needs the finite native timing metadata of its prepared source packet.')
    rate, start, duration = (values[key] for key in
                            ('unity_sample_rate', 'unity_start_frame', 'unity_duration'))
    _require(rate == spec['fps'] and start == 1. and duration > 0.,
             'B native sample rate/origin differs from the composition; prepare matching timing before editing.')
    _require(spec['sampleStart'] >= 0., 'B local sample time cannot start before its prepared source.')
    if spec['timeMode'] == 'HoldPose':
        # Each effective authored curve was proved constant before this call;
        # extending a real Hold does not evaluate an unproved moving interval.
        return
    frames = [float(key.co[0]) for curve in selected for key in curve.keyframe_points]
    first, last = min(frames), max(frames)
    # Blender stores FCurve key coordinates as float32. Compare coverage in
    # that native precision; the sealed seconds and export sampling stay exact.
    try:
        requested_first, requested_last = (
            struct.unpack('<f', struct.pack('<f', frame))[0]
            for frame in _frame_range(spec))
    except (OverflowError, struct.error) as error:
        raise ValueError('B sample bounds exceed finite native frame coordinates.') from error
    _require(math.isfinite(requested_first) and math.isfinite(requested_last),
             'B sample bounds exceed finite native frame coordinates.')
    tolerance = 1e-6
    _require(spec['sampleEnd'] <= duration + tolerance / rate and
             requested_first >= first - tolerance and requested_last <= last + tolerance,
             'Moving B sample bounds exceed its prepared duration or authored selected-slot FK range.')


def validate_action_scope(action, slot, scope, spec):
    """Read-only B admission before any filtering or native ID copying."""
    spec = contract.validate(spec)
    curves = _simple_action(action, slot)
    selected, allowed = [], set(scope.channels)
    fixed = {}
    for name, prop, value in scope.admission_properties:
        prefix = _pose_path(name, 'location').rsplit('.', 1)[0]
        fixed[(prefix + '[' + json.dumps(prop) + ']', 0)] = value
    auxiliary = {key: value for key, value in scope.canonical_defaults.items()
                 if any(key[0].startswith(_pose_path(name, 'location').rsplit('.', 1)[0] + '.')
                        for name in scope.auxiliary_names)}
    for curve in curves:
        key = (curve.data_path, curve.array_index)
        if key in allowed:
            _validate_curve(curve)
            if spec['timeMode'] == 'HoldPose':
                assert_constant_curve(curve)
            selected.append(curve)
        elif key in fixed or key in auxiliary:
            assert_constant_curve(curve, (fixed | auxiliary)[key])
    _require(selected, 'B has no authored effective upperbody FK channels.')
    _validate_action_timing(action, selected, spec)
    if spec['timeMode'] == 'Raise':
        _require(spec['holdWorkItemId'] or spec['holdNewActionId'],
                 'Prepared Raise requires an actual following Hold identity.')
    return selected


def _pose_path(name, property_name):
    return 'pose.bones[' + json.dumps(name, ensure_ascii=False) + '].' + property_name


def effective_fingerprint(action, slot, scope, spec):
    """Authored B proof, independent of A identity, C evaluation, seek and phase.

    Numeric effective export contents are a separate sampled proof. This hash
    includes key handles/interpolation and the exact canonical input policy;
    a changed A or viewing speed does not manufacture a changed B revision.
    """
    result = {'schema': SCHEMA, 'fingerprint_known': False, 'authored_b_sha256': '',
              'effective_input_sha256': '', 'fingerprint': '', 'proof_reason': ''}
    try:
        spec = contract.validate(spec)
        selected = validate_action_scope(action, slot, scope, spec)
        fp = _host('animation_worklist_fingerprint')
        content = [fp._curve_payload(curve) for curve in sorted(selected,
                   key=lambda curve: (curve.data_path, curve.array_index))]
        # Unkeyed selected channels always resolve to this same explicit seed.
        authored = contract.digest({'curves': content})
        timing = {key: spec[key] for key in ('timeMode', 'sampleStart', 'sampleEnd', 'fps', 'editSpeed', 'editPhase')}
        # Speed/phase affect B application provenance even though the exported
        # authored cycle is sampled in its own unchanged local time domain.
        inputs = contract.digest({'scope': scope.proof, 'bTiming': timing})
        result.update(fingerprint_known=True, authored_b_sha256=authored,
                      effective_input_sha256=inputs,
                      fingerprint=contract.digest({'schema': SCHEMA, 'authored': authored,
                                                    'inputs': inputs, 'timing': timing}),
                      proof_reason='Exact selected B FK contents and canonical inputs; reference A excluded.')
    except (ValueError, AttributeError, TypeError, ReferenceError, RuntimeError) as error:
        result['proof_reason'] = str(error)
    return result


def effective_values_proof(names, times, channels):
    """Canonical finite float32 numeric values, not a channel-name digest."""
    _require(tuple(sorted(channels)) == tuple(sorted(names)) and len(names) == len(set(names))
             and 2 <= len(times) <= 20000 and all(type(t) in (int, float) and math.isfinite(t) for t in times)
             and all(a < b for a, b in zip(times, times[1:])), 'Incomplete effective B sample channels.')
    rows = []
    for name in sorted(names):
        values = channels[name]
        _require(len(values) == len(times), 'Incomplete effective B sample values.')
        numbers = []
        for value in values:
            _require(type(value) in (int, float) and math.isfinite(value), 'Non-finite effective B sample.')
            try:
                value = struct.unpack('<f', struct.pack('<f', value))[0]
            except (OverflowError, struct.error) as error:
                raise ValueError('Effective B sample exceeds finite float32.') from error
            _require(math.isfinite(value), 'Effective B sample exceeds finite float32.')
            numbers.append(0. if value == 0. else value)
        rows.append([name, numbers])
    payload = {'schema': VALUES_SCHEMA, 'timesSeconds': list(times), 'channels': rows}
    return {'effectiveChannelsSha256': contract.digest(payload),
            'effectiveChannelCount': len(names), 'effectiveChannels': list(sorted(names)),
            'effectiveValues': payload}


def _frame_range(spec):
    return 1. + spec['sampleStart'] * spec['fps'], 1. + spec['sampleEnd'] * spec['fps']


def export_timing(context, spec):
    spec = contract.validate(spec)
    start, end = _frame_range(spec)
    fps = max(1, int(round(spec['fps'])))
    return {'frame_start': start, 'frame_end': end, 'fps': fps,
            'fps_base': fps / spec['fps'], 'sample_rate': spec['fps'],
            'unit_scale': context.scene.unit_settings.scale_length,
            'loop': spec['timeMode'] == 'HoldLoop'}


def _source_wires(rig, scope):
    """This is called only after the complete exact Dress validator passes."""
    import bpy
    skirt = _host('skirt_rig')
    result = []
    for source in skirt.shared_sources(rig):
        record = skirt.read_record(source)
        _require(record and skirt.is_shared(record) and record.get('physics') is None,
                 'Canonical B export supports only the proved neutral manual Dress backend.')
        for chain in record['chains']:
            wire = bpy.data.objects.get(chain['curve'])
            _require(wire is not None and wire.parent is rig and wire.parent_type == 'BONE',
                     'The exact owned Dress wire attachment changed.')
            result.append(wire)
    _require(len(result) == len(set(result)), 'An owned Dress wire is shared by two records.')
    return tuple(result)


def _check_closed_dependencies(rig, wires, worker):
    """Ordinary guard unchanged, plus ownership of even static dependencies.

    Static targets parented to A-driven bones are dependencies too. Action
    constraints cannot sneak A's Action into an otherwise static graph.
    """
    worker._check_external_dependencies(rig)
    allowed = {rig, rig.data, *wires, *(wire.data for wire in wires)}
    for block in (rig, *wires):
        _require(all(dep in allowed for dep in worker._dependencies(block)),
                 'Unknown or external dependency drives B; bake it into a supported FK B work item first.')
        constraints = list(block.constraints)
        if block.type == 'ARMATURE':
            constraints += [con for pb in block.pose.bones for con in pb.constraints]
        _require(all(con.type != 'ACTION' and getattr(con, 'action', None) is None for con in constraints),
                 'An Action constraint can evaluate reference A; prepare an independent FK B first.')
    for wire in wires:
        _require(wire.parent is rig and not wire.animation_data and not wire.data.animation_data
                 and all(mod.type == 'HOOK' and mod.object is rig for mod in wire.modifiers),
                 'Owned Dress B dependencies must be closed static wires with exact self Hooks.')


def _strip_id_refs(block):
    """Drop recovery/editor native references only on disposable copied IDs."""
    import bpy
    def clean(group):
        for key, value in tuple(group.items()):
            if isinstance(value, bpy.types.ID):
                del group[key]
            elif hasattr(value, 'items'):
                clean(value)
    clean(block)


def _copy_selected_action(action, slot, scope):
    action_copy = action.copy()
    try:
        action_copy.name = 'Composition · Isolated B'
        action_copy.use_fake_user = False
        _strip_id_refs(action_copy)
        allowed = set(scope.channels)
        if action_copy.is_action_layered:
            for layer in action_copy.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        for curve in tuple(bag.fcurves):
                            if bag.slot_handle != slot or (curve.data_path, curve.array_index) not in allowed:
                                bag.fcurves.remove(curve)
        else:
            for curve in tuple(action_copy.fcurves):
                if (curve.data_path, curve.array_index) not in allowed:
                    action_copy.fcurves.remove(curve)
        return action_copy
    except Exception:
        import bpy
        action_copy.use_fake_user = False
        if action_copy.users == 0:
            bpy.data.actions.remove(action_copy)
        raise


def _sample_effective(context, rig, packet, scope, spec):
    """Measure native B local transforms after complete isolated evaluation."""
    worker, ua = baseline_worker(), _host('unity_animation')
    duration = spec['sampleEnd'] - spec['sampleStart']
    intervals = max(1, math.ceil(duration * spec['fps'] - 1e-9))
    _require(intervals + 1 <= 20000, 'B exceeds the existing export sample bound.')
    times = [spec['sampleStart'] + duration * i / intervals for i in range(intervals + 1)]
    values = {name: [] for name in effective_channel_names(scope)}
    c, ci, previous = scope.mapping.conversion, scope.mapping.conversion.inverted(), {}
    for time in times:
        worker._frame(context.scene, 1. + time * spec['fps'])
        context.view_layer.update()
        evaluated = rig.evaluated_get(context.evaluated_depsgraph_get())
        poses = {}
        for name, index in scope.mapping.indices.items():
            rest = ua._matrix(packet['bones'][index]['rest'], name)
            poses[index] = (ci @ evaluated.matrix_world @ evaluated.pose.bones[name].matrix
                            @ scope.mapping.rest_world[name].inverted() @ c @ rest)
        for name in scope.native_names:
            index = scope.mapping.indices[name]
            parent = packet['bones'][index]['parent']
            parent_pose = (poses.get(parent) or ua._matrix(packet['bones'][parent]['rest'], name)) if parent >= 0 else None
            local = parent_pose.inverted() @ poses[index] if parent_pose is not None else poses[index]
            position, rotation, scale = worker._trs(local, name + ' isolated Unity local')
            if name in previous and rotation.dot(previous[name]) < 0:
                rotation.negate()
            elif name not in previous and rotation.w < 0:
                rotation.negate()
            previous[name] = rotation.copy()
            path = scope.path_by_native[name]
            for prop, numbers in (('localPosition', position), ('localRotation',
                    (rotation.x, rotation.y, rotation.z, rotation.w)), ('localScale', scale)):
                for component, value in enumerate(numbers):
                    values[path + '|' + prop + '|' + str(component)].append(float(value))
    if spec['timeMode'] == 'HoldPose':
        _require(all(max(numbers) - min(numbers) <= 2e-6 for numbers in values.values()),
                 'Hold Pose has moving effective native B transforms; no runtime freezing was used.')
    return effective_values_proof(effective_channel_names(scope), times, values)


def _artist_state(rig, scene):
    ua = _host('unity_animation')
    ad = rig.animation_data
    animation = None if ad is None else {
        'action': ad.action.as_pointer() if ad.action is not None else None,
        'slot': ad.action_slot.handle if ad.action_slot is not None else 0,
        'use_nla': ad.use_nla, 'use_tweak_mode': ad.use_tweak_mode,
        'blend': ad.action_blend_type, 'influence': ad.action_influence,
        'extrapolation': ad.action_extrapolation}
    return contract.digest({'rest': ua._rest_state(rig.data), 'data': rig.data.as_pointer(),
        'object': {field: list(getattr(rig, field)) for field in
            ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale',
             'delta_location', 'delta_rotation_euler', 'delta_rotation_quaternion', 'delta_scale')},
        'world': [float(value) for row in rig.matrix_world for value in row],
        'animation': animation,
        'pose': {pb.name: {field: list(getattr(pb, field)) for field in
            ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale')}
            for pb in rig.pose.bones},
        'frame': scene.frame_current, 'subframe': scene.frame_subframe,
        'fps': scene.render.fps, 'fps_base': scene.render.fps_base,
        'units': {'system': scene.unit_settings.system,
                  'scale_length': scene.unit_settings.scale_length}})


def _raise_destination(context, spec, rig):
    if spec['timeMode'] != 'Raise':
        return
    try:
        from . import animation_composition
    except ImportError:
        import animation_composition
    saved = _host('animation_worklist').state(context)
    # RNA stores UUID hex while the frozen DTO keeps canonical dashed UUIDs.
    # Authenticate the identities without rewriting either persisted form.
    hold_work = contract._uuid(spec['holdWorkItemId'], 'following Hold work item', empty=True)
    hold_new = contract._uuid(spec['holdNewActionId'], 'following Hold new Action', empty=True)
    matches = []
    for item in saved.items:
        work_id = contract._uuid(item.work_item_id, 'native Hold work item', empty=True)
        new_id = contract._uuid(item.new_action_id, 'native Hold new Action', empty=True)
        if hold_work and work_id == hold_work or hold_new and new_id == hold_new:
            matches.append(item)
    _require(len(matches) == 1 and matches[0].rig is rig,
             'Raise must reference one actual prepared Hold item on this exact native rig.')
    hold = contract.validate(animation_composition.item_spec(matches[0]))
    contract.validate_item(hold, matches[0])
    _require(hold['timeMode'] in {'HoldPose', 'HoldLoop'} and
             hold['sourceRestSha256'] == spec['sourceRestSha256'] and
             set(hold['allowedBonePaths']) == set(spec['allowedBonePaths']) and
             set(hold['humanRoles']) == set(spec['humanRoles']) and
             (not hold_work or hold['editWorkItemId'] == hold_work) and
             (not hold_new or hold['editNewActionId'] == hold_new),
             'The actual Hold destination has incompatible timing role, Rest or effective native scope.')


@contextmanager
def isolate_export(context, session, item):
    """Yield a disposable canonical B; restore artist/C exactly on all outcomes."""
    import bpy
    from mathutils import Matrix
    rig, spec = session['rig'], contract.validate_item(session['spec'], item)
    _raise_destination(context, spec, rig)
    _require(item.rig is rig and item.custom_action is session['custom_action']
             and item.custom_slot == session['custom_slot'] and not rig.animation_data.use_tweak_mode,
             'Leave composition Tweak through isolated_sync before exporting its exact B.')
    ua = _host('unity_animation')
    packet = ua.load_package(session['packet_path'])
    _require(packet['_sha256'] == session['packet_sha256'], 'The prepared B packet was replaced.')
    scope = validate_scope(context, rig, spec, packet)
    _require(contract.digest(scope.proof) == session['scope_proof'] and
             [list(pair) for pair in scope.channels] == session['scope_channels'],
             'Composition scope differs from its prepared session.')
    proof = effective_fingerprint(item.custom_action, item.custom_slot, scope, spec)
    _require(proof['fingerprint_known'], proof['proof_reason'])
    before_a = _action_hash(session['base_action'], session['base_slot'])
    _require(before_a == session['base_hash'], 'Protected reference A changed before isolated export.')
    # Include the entire selected Custom slot, including outside-mask curves
    # which remain on the original Action but never enter isolated B.
    before_b = _action_hash(item.custom_action, item.custom_slot)
    worker = baseline_worker()
    originals = _source_wires(rig, scope)
    _check_closed_dependencies(rig, originals, worker)
    old_scene, old_view, old_state = context.window.scene, context.view_layer.name, _artist_state(rig, context.scene)
    source_scene = context.scene
    clone = data = action = scene = None
    objects, curve_data = [], []
    defer = _host('forearm_twist').defer_runtime(context, flush_on_exit=False)
    # Cleanup and original-context restoration occur before the deferral exits.
    with defer:
        try:
            clone = rig.copy()
            data = rig.data.copy()
            clone.data, data.use_fake_user = data, False
            clone.name = 'Composition · B Evaluation'
            scene = bpy.data.scenes.new('Composition · B Isolation')
            scene.unit_settings.system = source_scene.unit_settings.system
            scene.unit_settings.scale_length = source_scene.unit_settings.scale_length
            timing = export_timing(context, spec)
            scene.render.fps, scene.render.fps_base = timing['fps'], timing['fps_base']
            scene.collection.objects.link(clone)
            # All copied Rest properties must match the protected live copy.
            _require(ua._rest_state(data) == ua._rest_state(rig.data), 'Copying B changed full source Rest.')
            for block in (clone, data, *data.bones, *clone.pose.bones):
                _strip_id_refs(block)
            clone.rotation_mode = 'QUATERNION'
            clone.delta_location, clone.delta_rotation_euler = (0., 0., 0.), (0., 0., 0.)
            clone.delta_rotation_quaternion, clone.delta_scale = (1., 0., 0., 0.), (1., 1., 1.)
            clone.matrix_world = scope.object_matrix.copy()
            ad = clone.animation_data_create()
            ad.action, ad.use_nla = None, False
            for track in tuple(ad.nla_tracks):
                ad.nla_tracks.remove(track)
            replacements = {rig: clone, rig.data: data}
            for wire in originals:
                copied = wire.copy()
                objects.append(copied)
                copied_data = wire.data.copy()
                curve_data.append(copied_data)
                copied.data, copied_data.use_fake_user = copied_data, False
                replacements[wire], replacements[wire.data] = copied, copied_data
                copied.parent = clone
                for mod in copied.modifiers:
                    mod.object = clone
                for block in (copied, copied_data):
                    _strip_id_refs(block)
                scene.collection.objects.link(copied)
            for pb in clone.pose.bones:
                pb.matrix_basis = Matrix.Identity(4)
                pb.custom_shape = None
                for con in pb.constraints:
                    for field in ('target', 'pole_target', 'space_object'):
                        value = getattr(con, field, None)
                        if value in replacements:
                            setattr(con, field, replacements[value])
            for block in (clone, data):
                animation = getattr(block, 'animation_data', None)
                for curve in animation.drivers if animation else ():
                    for variable in curve.driver.variables:
                        for binding in variable.targets:
                            if binding.id in replacements:
                                binding.id = replacements[binding.id]
            for name, prop, value in scope.fixed_properties:
                _require(prop in clone.pose.bones[name], 'A proved B switch input is missing.')
                clone.pose.bones[name][prop] = value
            action = _copy_selected_action(item.custom_action, item.custom_slot, scope)
            ad.action = action
            slot = worker._slot(clone, action, item.custom_slot)
            if slot is not None:
                ad.action_slot = slot
            ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.
            ad.action_extrapolation = 'HOLD'
            context.window.scene = scene
            context.view_layer.update()
            _check_closed_dependencies(clone, tuple(objects), worker)
            measured = _sample_effective(context, clone, packet, scope, spec)
            after_a = _action_hash(session['base_action'], session['base_slot'])
            _require(before_a == after_a, 'Reference A changed while preparing isolated B.')
            metadata = {'schema': contract.EXPORT_SCHEMA, 'compositionId': spec['compositionId'],
                'editWorkItemId': spec['editWorkItemId'], 'editNewActionId': spec['editNewActionId'],
                'sourceAction': item.custom_action.name, 'sourceActionSlot': item.custom_slot,
                'isolatedEditAction': action.name, 'referenceActionUnchanged': True,
                'referenceActionBeforeSha256': before_a, 'referenceActionAfterSha256': after_a,
                'sampleSpace': 'bone-local', 'excludedReferenceEvaluation': True,
                **{key: measured[key] for key in ('effectiveChannelsSha256', 'effectiveChannelCount', 'effectiveChannels')}}
            # Reproducible worker input, and its measured values, remain private
            # job evidence. Public composition_export stays the frozen DTO.
            yield {'rig': clone, 'action': action, 'action_slot': item.custom_slot,
                'source_rig': rig, 'source_scene': source_scene, 'source_action': item.custom_action,
                'source_action_slot': item.custom_slot, 'scope': scope, 'timing': timing,
                'composition': spec, 'composition_export': metadata, 'b_fingerprint': proof,
                'effective_values': measured['effectiveValues'], 'packet': packet,
                'dependency_objects': tuple(objects), 'worker_module_path': str(STAGING / 'animation_composition_export_worker.py'),
                'baseline_worker_file': str(BASELINE / 'animation_export_worker.py')}
        finally:
            context.window.scene = old_scene
            if old_view in old_scene.view_layers:
                context.window.view_layer = old_scene.view_layers[old_view]
            for obj in reversed(objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            if clone is not None:
                bpy.data.objects.remove(clone, do_unlink=True)
            if action is not None and action.users == 0:
                bpy.data.actions.remove(action)
            for block in curve_data:
                if block.users == 0:
                    bpy.data.curves.remove(block)
            if data is not None and data.users == 0:
                bpy.data.armatures.remove(data)
            if scene is not None:
                bpy.data.scenes.remove(scene)
            _require(_action_hash(session['base_action'], session['base_slot']) == before_a,
                     'Protected A changed during B export; publication must fail.')
            _require(_action_hash(item.custom_action, item.custom_slot) == before_b,
                     'Original Custom B Action changed during isolation; publication must fail.')
            _require(_artist_state(rig, source_scene) == old_state,
                     'Artist/C data, Rest, object/pose transforms, Action binding or time changed during B isolation; publication must fail.')


def _worker_scope(scope):
    """Only fixed mathematical/native mapping data crosses the worker boundary."""
    return {'native_names': list(scope.native_names), 'path_by_native': scope.path_by_native,
        'channels': [list(pair) for pair in scope.channels],
        'canonical_defaults': [[path, index, value] for (path, index), value in sorted(scope.canonical_defaults.items())],
        'fixed_properties': [list(row) for row in scope.fixed_properties],
        'object_matrix': [list(row) for row in scope.object_matrix],
        'mapping': {'indices': scope.mapping.indices,
            'conversion': [list(row) for row in scope.mapping.conversion],
            'rest_world': {name: [list(row) for row in matrix]
                           for name, matrix in scope.mapping.rest_world.items()}},
        'structural_proof': scope.proof}


def validate_export_receipt(report, spec, expected, values=None):
    """Pure mandatory public DTO check before any filesystem publication."""
    spec = contract.validate(spec)
    proof = report.get('composition_export') if isinstance(report, dict) else None
    fields = {'schema', 'compositionId', 'editWorkItemId', 'editNewActionId', 'sourceAction',
        'sourceActionSlot', 'isolatedEditAction', 'referenceActionUnchanged',
        'referenceActionBeforeSha256', 'referenceActionAfterSha256', 'effectiveChannelsSha256',
        'effectiveChannelCount', 'effectiveChannels', 'sampleSpace', 'excludedReferenceEvaluation'}
    _require(isinstance(proof, dict) and set(proof) == fields and proof == expected,
             'Worker composition provenance differs from the prepared exact B export.')
    _require(proof['schema'] == contract.EXPORT_SCHEMA and proof['compositionId'] == spec['compositionId']
             and proof['editWorkItemId'] == spec['editWorkItemId'] and proof['editNewActionId'] == spec['editNewActionId']
             and proof['referenceActionUnchanged'] is True and proof['excludedReferenceEvaluation'] is True
             and proof['sampleSpace'] == 'bone-local' and type(proof['sourceActionSlot']) is int
             and type(proof['effectiveChannelCount']) is int and proof['effectiveChannelCount'] > 0
             and proof['referenceActionBeforeSha256'] == proof['referenceActionAfterSha256'],
             'Worker cannot prove exact isolated B identity and exclusion of A.')
    names = proof['effectiveChannels']
    _require(isinstance(names, list) and len(names) == len(set(names)) == proof['effectiveChannelCount'],
             'Worker effective channels are incomplete or ambiguous.')
    exhaustive = sorted(path + '|' + prop + '|' + str(index)
        for path in spec['allowedBonePaths']
        for prop, count in (('localPosition', 3), ('localRotation', 4), ('localScale', 3))
        for index in range(count))
    _require(names == exhaustive, 'Worker must report all native local TRS components in the declared upperbody scope.')
    _require(all(isinstance(proof[key], str) and re.fullmatch('[0-9a-f]{64}', proof[key])
                 for key in ('referenceActionBeforeSha256', 'referenceActionAfterSha256', 'effectiveChannelsSha256'))
             and all(isinstance(proof[key], str) and proof[key] for key in ('sourceAction', 'isolatedEditAction'))
             and proof['sourceActionSlot'] >= 0,
             'Worker has an invalid Action identity or content fingerprint.')
    for name in names:
        try:
            path, prop, component = name.rsplit('|', 2)
            count = {'localPosition': 3, 'localRotation': 4, 'localScale': 3}[prop]
            _require(path in spec['allowedBonePaths'] and component in {str(i) for i in range(count)},
                     'Worker effective channel is outside the exact native upperbody mask.')
        except (AttributeError, KeyError, ValueError) as error:
            raise ValueError('Worker has an invalid effective native channel identity.') from error
    if values is not None:
        _require(type(values) is dict and set(values) == {'schema', 'timesSeconds', 'channels'}
                 and values['schema'] == VALUES_SCHEMA and type(values['channels']) is list
                 and len(values['channels']) == len(names)
                 and [row[0] for row in values['channels']] == names,
                 'Worker numeric B values are not a complete canonical channel payload.')
        actual = effective_values_proof(names, values['timesSeconds'], dict(values['channels']))
        _require(actual['effectiveChannelsSha256'] == proof['effectiveChannelsSha256'],
                 'Worker effective numeric B contents differ from the measured proof.')
    _require(report.get('composition') == spec, 'Worker composition DTO changed.')
    return proof


def _snapshot_editor_state(rig):
    """Read the saved export-panel preferences without following their ID links."""
    import bpy
    field = 'character_designer_unity_export'
    if not rig.is_property_set(field):
        return None
    def capture(group):
        result = []
        for prop in group.bl_rna.properties:
            if prop.identifier == 'rna_type':
                continue
            if prop.type == 'POINTER' and not group.is_property_set(prop.identifier):
                result.append((prop.identifier, None))
                continue
            value = getattr(group, prop.identifier)
            if prop.type == 'POINTER':
                value = (None if value is None else
                         ('ID', value.as_pointer()) if isinstance(value, bpy.types.ID) else capture(value))
            elif prop.type == 'COLLECTION':
                value = tuple(capture(entry) for entry in value)
            elif getattr(prop, 'is_array', False):
                value = tuple(value)
            result.append((prop.identifier, value))
        return tuple(result)
    return capture(getattr(rig, field))


def _prepare_snapshot_editor(rig, original):
    """Remove only the copied export-panel RNA storage from the worker input.

    Blender 5.2 keeps registered RNA properties in separate system storage,
    outside ID.items(). Its mesh/material picker references are editor state;
    serializing them would pull the original character and its A Actions into B.
    The isolated evaluator and the exact worker inventory guard stay unchanged.
    """
    _require(rig.as_pointer() != original.as_pointer() and
             rig.data.as_pointer() != original.data.as_pointer() and not rig.library,
             'Snapshot editor cleanup requires the disposable B object and Armature copies.')
    field = 'character_designer_unity_export'
    _require(rig.bl_rna.properties.get(field) is not None,
             'Register the export-panel RNA property before preparing the B snapshot.')
    for block in (rig, rig.data):
        ad = getattr(block, 'animation_data', None)
        for curve in ad.drivers if ad else ():
            _require(not curve.data_path.startswith(field) and
                     not (curve.driver.use_self and field in curve.driver.expression) and
                     all(not (binding.id is not None and binding.id.as_pointer() == rig.as_pointer()
                              and binding.data_path.startswith(field))
                         for variable in curve.driver.variables for binding in variable.targets),
                     'Export-panel RNA state is an evaluation input; refuse snapshot cleanup.')
    before = _snapshot_editor_state(original)
    rig.property_unset(field)
    _require(not rig.is_property_set(field) and _snapshot_editor_state(original) == before,
             'Snapshot cleanup did not preserve the original export-panel preferences and references.')


def begin_isolated_export(context, inputs, destination, export_rig_name=None, *, launch_proof=None):
    """Frozen .24 launch policy, with proof/worker fixed before Popen.

    No worker is launched by importing this module. The caller owns normal UI
    authorization and the shared serial export slot. Snapshot is durable before
    returning, so isolate_export can dispose its scene/IDs immediately after.
    """
    import bpy
    exporter, model = _host('animation_export'), _host('unity_export')
    _require(not exporter._ACTIVE_JOB and not model._ACTIVE_JOB,
             'Wait for the current character export to finish.')
    rig, action, timing = inputs['rig'], inputs['action'], inputs['timing']
    _require(rig.type == 'ARMATURE' and rig.name in context.scene.objects and rig.mode != 'EDIT'
             and not rig.library and not rig.data.library and not action.library
             and not rig.animation_data.use_tweak_mode, 'Choose the prepared local isolated B rig and Action.')
    worker = baseline_worker()
    worker._number(timing['frame_start'], 'frame_start', -1048574, 1048574)
    worker._number(timing['frame_end'], 'frame_end', -1048574, 1048574)
    fps = timing['fps'] / timing['fps_base']
    _require(timing['frame_end'] > timing['frame_start'] and 1 <= timing['sample_rate'] <= 240
             and math.ceil((timing['frame_end'] - timing['frame_start']) / fps * timing['sample_rate']) <= 20000,
             'Choose a positive supported B sampling range.')
    slot = worker._slot(rig, action, inputs['action_slot'])
    worker._validate_action(rig, action, slot)
    _check_closed_dependencies(rig, inputs['dependency_objects'], worker)
    helpers = worker._model_helpers()
    # Audit the original reverse skin links, not only the disposable clone.
    original = inputs['source_rig']
    discarded = {bone.name for bone in original.data.bones if bone.use_deform and helpers._is_control(bone)}
    for mesh in inputs['source_scene'].objects:
        if mesh.type == 'MESH' and any(mod.type == 'ARMATURE' and mod.object is original for mod in mesh.modifiers):
            indices = {group.index for group in mesh.vertex_groups if group.name in discarded}
            _require(not any(entry.group in indices and entry.weight > 1e-8
                             for vertex in mesh.data.vertices for entry in vertex.groups),
                     'A generated control has real skin weights and cannot be omitted.')
    name = model._filename(Path(destination).name, original)
    destination = Path(bpy.path.abspath(str(destination))).expanduser().resolve().with_name(name)
    metadata = destination.with_suffix('.animation.json')
    _require(not destination.exists() and not metadata.exists(),
             'That animation already exists; choose a new filename to preserve it.')
    baseline_path, worker_path = Path(inputs['baseline_worker_file']), Path(inputs['worker_module_path'])
    _require(baseline_path.resolve() == (BASELINE / 'animation_export_worker.py').resolve()
             and worker_path.resolve() == (STAGING / 'animation_composition_export_worker.py').resolve()
             and worker_path.is_file(), 'Use the fixed staged worker and frozen .24 exporter.')
    temporary = tempfile.TemporaryDirectory(prefix='cdesigner-composition-')
    root, stage = Path(temporary.name), Path(temporary.name) / 'stage'
    stage.mkdir()
    source_action = inputs['source_action']
    job = {'temporary': temporary, 'root': root, 'stage': stage, 'destination': destination,
        'metadata': metadata, 'started': time.monotonic(), 'source_blend': bpy.data.filepath,
        'source_package': str(source_action.get('character_designer_unity_animation_package', '')),
        'source_package_sha256': str(source_action.get('character_designer_unity_animation_package_sha256', '')),
        'action': source_action.name, 'process': None, 'log': None, 'composition_job': True,
        'composition': inputs['composition'], 'composition_export': inputs['composition_export'],
        'effective_values': inputs['effective_values']}
    try:
        if launch_proof is not None:
            fields = {'_worklist_fingerprint', '_worklist_composition',
                      '_worklist_composition_metadata', '_worklist_export_implementation'}
            _require(type(launch_proof) is dict and set(launch_proof) == fields,
                     'Composition launch needs the exact immutable collection proof fields.')
            frozen = json.loads(contract.canonical_bytes(launch_proof))
            _require(frozen['_worklist_composition'] == inputs['composition'] and
                     frozen['_worklist_fingerprint'].get('fingerprint_known') is True,
                     'Collection launch belongs to another or unproved composition B.')
            job.update(frozen)
        job['_worklist_composition_export'] = json.loads(contract.canonical_bytes(inputs['composition_export']))
        snapshot = root / 'animation.blend'
        _prepare_snapshot_editor(rig, original)
        bpy.data.libraries.write(str(snapshot), {rig, action, *inputs['dependency_objects']},
                                 path_remap='ABSOLUTE', compress=True)
        private = {'scope': _worker_scope(inputs['scope']), 'packet': inputs['packet'],
            'dependency_objects': [obj.name for obj in inputs['dependency_objects']],
            'composition': inputs['composition'], 'composition_export': inputs['composition_export'],
            'effective_values': inputs['effective_values'], 'snapshot_sha256': _sha256(snapshot),
            'baseline_worker_file': str(baseline_path), 'baseline_worker_sha256': _sha256(baseline_path),
            'baseline_model_sha256': _sha256(baseline_path.with_name('unity_export_worker.py'))}
        spec = {'rig': rig.name, 'action': action.name, 'action_slot': inputs['action_slot'],
            **timing, 'name': source_action.name, 'stage': str(stage), 'filename': name,
            'unsupported_channels': [], 'composition_job': private}
        if export_rig_name is not None:
            spec['export_rig_name'] = worker._export_rig_name(export_rig_name)
        (root / 'job.json').write_text(json.dumps(spec, allow_nan=False), encoding='utf-8')
        job['log'] = (root / 'worker.log').open('w', encoding='utf-8')
        command = [bpy.app.binary_path, '--background', '--factory-startup', '--disable-autoexec',
            '--threads', '2', str(snapshot), '--python-exit-code', '1', '--python', str(worker_path),
            '--', str(root / 'job.json')]
        job['process'] = subprocess.Popen(command, stdout=job['log'], stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        exporter._ACTIVE_JOB = job
        return job
    except Exception:
        exporter._dispose(job)
        raise


def poll_isolated_export(job):
    """Check sampled provenance before the frozen atomic FBX/metadata publish."""
    exporter = _host('animation_export')
    try:
        if job['process'].poll() is None:
            if time.monotonic() - job['started'] > 600:
                cancel_isolated_export(job)
                raise ValueError('Composition export timed out; previous files were preserved.')
            return None
        result_path = job['stage'] / 'result.json'
        result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
        _require(job['process'].returncode == 0 and result.get('ok') is True,
                 result.get('error') or 'Isolated composition export failed; no files were published.')
        validate_export_receipt(result, job['composition'], job['composition_export'], result.get('effective_values'))
        _require(result.get('effective_values') == job['effective_values'],
                 'Worker B contents differ from the independent foreground isolated measurement.')
        published = exporter._publish(job, result)
        # Frozen _publish retains **result and stages metadata before FBX. Check
        # its actual public file before advancing Link/worklist publication.
        persisted = json.loads(Path(published['metadata']).read_text(encoding='utf-8'))
        validate_export_receipt(persisted, job['composition'], job['composition_export'], persisted.get('effective_values'))
        _require(persisted.get('effective_values') == job['effective_values'],
                 'Published numeric B evidence is incomplete; its revision was not advanced.')
        callback = job.get('_publish_callback')
        return callback(job, published) if callback else published
    finally:
        # Keep pending jobs alive, and dispose completed/failed jobs normally.
        if job.get('process') and job['process'].poll() is not None:
            exporter._dispose(job)


def cancel_isolated_export(job=None):
    return _host('animation_export').cancel_export(job)
