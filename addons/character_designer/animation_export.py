"""Export one explicit Action from an isolated snapshot, without saving the scene."""
import hashlib
import json
import math
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time
from contextlib import nullcontext

import bpy

from .unity_export import _filename


class AnimationExportError(ValueError):
    pass


_ACTIVE_JOB = None


def active_job():
    return _ACTIVE_JOB


def _slot(rig, action):
    ad = rig.animation_data
    if ad and ad.action == action and ad.action_slot:
        return ad.action_slot.handle
    slots = [slot for slot in action.slots if slot.target_id_type == 'OBJECT']
    if len(slots) != 1:
        raise AnimationExportError('Assign this Action and its intended slot to the character before exporting.')
    return slots[0].handle


def _action_frame_clock(scene, action):
    from . import unity_animation
    try:
        native_rate = unity_animation.native_action_frame_rate(action)
    except (ValueError, TypeError) as error:
        raise AnimationExportError(str(error)) from error
    if native_rate is None:
        return scene.render.fps, scene.render.fps_base
    fps = max(1, int(round(native_rate)))
    return fps, fps / native_rate


def _freeze_rigging_export(rig, action, value, session):
    """Seal explicit source inputs before snapshotting or starting a worker."""
    from . import animation_rigging_contract as contract
    if session is None or session['base_action'] != action:
        raise AnimationExportError('The Rigging inputs belong to another base Action.')
    slot = _slot(rig, action)
    if slot != session['base_slot']:
        raise AnimationExportError('The Rigging inputs belong to another base Action slot.')
    return contract.validate_export(value, session['capture'],
        source_packet_sha256=str(action.get('character_designer_unity_animation_package_sha256', '')),
        work_item_id=session['work_item_id'], new_action_id=session['new_action_id'],
        source_action=action.name, source_action_slot=slot,
        target_input_action=session['input_action'].name, target_input_action_slot=session['input_slot'])


def begin_export(context, rig, action, filepath, *, frame_start, frame_end, loop=False, sample_rate=None,
                 export_rig_name=None, rigging_export=None, link_identity=None):
    """Export the independent base, alongside explicitly owned Target inputs."""
    session = None
    if rig is not None and 'character_designer_rigging_session_v1' in rig:
        from . import unity_animation_rigging as rigging
        session = rigging.current(rig)
    if session is not None and rigging_export is None:
        raise AnimationExportError('Use Worklist Sync to preserve the separate Rigging inputs with this base animation.')
    frozen = (_freeze_rigging_export(rig, action, rigging_export, session)
              if rigging_export is not None else None)
    if frozen is not None:
        from .unity_animation import native_action_frame_rate
        rate = native_action_frame_rate(action)
        capture = session['capture']
        expected_end = 1. + capture['duration'] * capture['sampleRate']
        if (rate != capture['sampleRate']
                or type(frame_start) not in (int, float) or type(frame_end) not in (int, float)
                or not math.isclose(frame_start, 1., rel_tol=0., abs_tol=1e-6)
                or not math.isclose(frame_end, expected_end, rel_tol=1e-6, abs_tol=1e-6)
                or sample_rate is not None and (type(sample_rate) not in (int, float)
                    or sample_rate != capture['sampleRate'])
                or type(loop) is not bool or loop != capture['loopTime']):
            raise AnimationExportError('Keep the captured base time range, sample rate and loop setting when syncing Rigging inputs.')
    boundary = rigging.base_only(context, rig, action=action) if session is not None else nullcontext()
    with boundary:
        return _begin_export(context, rig, action, filepath, frame_start=frame_start, frame_end=frame_end,
            loop=loop, sample_rate=sample_rate, export_rig_name=export_rig_name, rigging_export=frozen,
            link_identity=link_identity)


def _target_route(record):
    from .animation_workspace import target_slot_id
    try:
        slot = target_slot_id(record)
    except ValueError as exc:
        raise AnimationExportError(str(exc)) from exc
    if not slot:
        return {}
    target = record.get('targetGuid')
    if not isinstance(target, str) or re.fullmatch(r'[0-9a-fA-F]{32}', target) is None:
        raise AnimationExportError('A semantic animation slot requires its exact targetGuid.')
    return {'targetGuid': target, 'targetSlotId': slot}


def _authenticated_dress_export(rig, action, slot, admission, surfaces, worker):
    """Read-only compatibility contract for the existing owned Dress closure.

The ordinary retained-base contract deliberately does not cover Curve/Hook
inputs. This explicit native proof preserves their previous snapshot semantics;
it is neither an arbitrary-external-target exception nor a determinism claim.
"""
    entries = admission.get('sources', ())
    if not entries:
        return None
    from . import skirt_rig as skirt, skirt_surface as surface
    from . import skirt_physics as physics, unity_animation_dress as dress
    from . import dress_export_snapshot as boundary
    def require(value, message):
        if not value:
            raise AnimationExportError('Authenticated Dress export: ' + message)
    require(len(entries) <= 64, 'the owned source inventory exceeds its bound.')
    require(rig.parent is None and not rig.constraints and not rig.modifiers,
            'use the existing local unparented skeletal rig without object constraints/modifiers.')
    surface_by_name = {proof['source']: proof for proof in surfaces}
    source_rows, wire_rows, targets, known_bones = [], [], {}, set()
    def target_for(constraint, value):
        targets[int(constraint.as_pointer())] = value
    for entry in entries:
        source = bpy.data.objects.get(entry['name'])
        require(source is not None and source.type == 'MESH' and not source.library
                and not source.override_library and not source.data.library
                and source.get(skirt.RIG_KEY) is rig, 'a source was removed, linked or reassigned.')
        record = skirt.read_record(source)
        require(record is not None and record['owner'] == entry['owner']
                and source.get(skirt.OWNER_KEY) == record['owner'], 'source/native ownership differs.')
        require(type(record['chain_count']) is int and 3 <= record['chain_count'] <= 64
                and record['chain_count'] == len(record['chains']) == len(record['controls']['chains'])
                and type(record['segment_count']) is int and 1 <= record['segment_count'] <= 32
                and all(len(chain[layer]) == record['segment_count']
                        for chain in record['chains'] for layer in ('manual', 'phys', 'def')),
                'the generated chain inventory differs.')
        control, deform, mechanism = skirt._bone_collection_layout(record)
        entire = control | deform | mechanism
        require(not known_bones.intersection(entire), 'two sources share generated bones.')
        known_bones.update(entire)
        if skirt.is_shared(record):
            dress._hierarchy(rig, record)
        else:
            require(rig.get(skirt.OWNER_KEY) == record['owner'] and rig.get(skirt.SOURCE_KEY) is source
                    and rig.data.get(skirt.OWNER_KEY) == record['owner']
                    and (rig.data.get(skirt.SOURCE_KEY) is None or rig.data.get(skirt.SOURCE_KEY) is source)
                    and rig.data.users == 1
                    and set(rig.data.bones.keys()) == entire, 'the separate Dress rig is not exactly source-owned.')
            waist = record['controls']['waist']
            parents = {waist: None, record['controls']['mid']: waist, record['controls']['hem']: waist}
            connected = {name: False for name in parents}
            for item in record['controls']['chains']:
                for level in ('mid', 'hem'):
                    parents[item[level]], connected[item[level]] = record['controls'][level], False
            for chain in record['chains']:
                for layer in ('manual', 'phys', 'def'):
                    for index, name in enumerate(chain[layer]):
                        parents[name], connected[name] = chain[layer][index - 1] if index else waist, index > 0
            require(all((rig.data.bones[name].parent.name if rig.data.bones[name].parent else None) == parent
                        and rig.data.bones[name].use_connect == connected[name]
                        and rig.data.bones[name].use_inherit_rotation
                        and rig.data.bones[name].use_local_location and rig.data.bones[name].inherit_scale == 'FULL'
                        for name, parent in parents.items()), 'the separate Dress hierarchy or inheritance changed.')
        # Reuse the complete native wire, point-Hook, bind, correction and
        # generated influence-driver validators; no pose/frame evaluation.
        surface._deform_proof(source, rig, record)
        boundary._influence_animation_guard(source, action)
        physics._physics_bone_animation(rig, record)
        prefixes = tuple(rig.pose.bones[name].path_from_id() for chain in record['chains'] for name in chain['phys'])
        selected_slot = next((item for item in action.slots if item.handle == slot), None)
        require(not any(curve.data_path.startswith(prefixes) for curve in worker._curves(action, selected_slot)),
                'preserve authored animation on the Dress physics bones before skeletal export.')
        for chain in record['chains']:
            for name in chain['def']:
                constraint = rig.pose.bones[name].constraints['Skirt physics delta']
                index = list(rig.pose.bones[name].constraints).index(constraint)
                aliases = (constraint.path_from_id(), rig.pose.bones[name].path_from_id() + '.constraints[' + str(index) + ']')
                require(not any(boundary._targets_constraint(curve.data_path, aliases)
                                for curve in worker._curves(action, selected_slot)),
                        'preserve authored physical constraint channels before skeletal export.')
        physical = record.get('physics')
        if physical is not None:
            if physical.get('backend', 'LEGACY_CAGE') == boundary.BACKEND:
                require(source.name in surface_by_name, 'the actual-surface source lacks its captured native proof.')
                tracker = bpy.data.objects[physical['surface']['roles']['TRACKER'][0]]
            else:
                require(physical.get('backend', 'LEGACY_CAGE') == 'LEGACY_CAGE', 'the physics backend is unsupported.')
                physics.validate_physics(source)
                holder, _identifier, _path = skirt.physics_control(source)
                require(type(holder.get('physics_influence')) in (int, float)
                        and holder['physics_influence'] == 0., 'legacy Cloth requires its existing Manual endpoint.')
                tracker = bpy.data.objects[physical['proxy']]
            for chain in record['chains']:
                for name in chain['phys']:
                    constraint = rig.pose.bones[name].constraints[0]
                    require(constraint.type == 'DAMPED_TRACK' and constraint.target is tracker,
                            'the physical tracker is not the freshly validated owned endpoint.')
                    target_for(constraint, tracker)
        else:
            require(all(not rig.pose.bones[name].constraints for chain in record['chains'] for name in chain['phys']),
                    'an inactive generated physics bone has an unknown constraint.')
        for index, chain in enumerate(record['chains']):
            wire = surface._generated_wire(source, rig, record, index)
            require(all(mod.show_viewport and mod.show_render for mod in wire.modifiers), 'a generated point Hook is disabled.')
            for name in chain['manual']:
                constraints = tuple(rig.pose.bones[name].constraints)
                if name != chain['manual'][-1]:
                    require(not constraints, 'an intermediate manual bone has an extra constraint.')
                    continue
                require(len(constraints) == 1, 'the terminal manual spline has an extra constraint.')
                constraint = constraints[0]
                require(constraint.name == 'Skirt manual wire' and constraint.type == 'SPLINE_IK'
                        and constraint.target is wire and not constraint.mute and constraint.influence == 1.
                        and constraint.chain_count == record['segment_count'] and constraint.use_even_divisions
                        and not constraint.use_curve_radius and not constraint.use_chain_offset
                        and constraint.use_original_scale and constraint.y_scale_mode == 'FIT_CURVE'
                        and constraint.xz_scale_mode == 'NONE', 'the generated manual Spline IK settings changed.')
                target_for(constraint, wire)
            wire_rows.append({'name': wire.name, 'source': source.name, 'owner': record['owner'],
                'parent_type': wire.parent_type, 'parent_bone': wire.parent_bone,
                'basis': [float(v) for row in wire.matrix_basis for v in row],
                'parent_inverse': [float(v) for row in wire.matrix_parent_inverse for v in row],
                'points': [[float(v) for v in point.co] for point in wire.data.splines[0].points],
                'hooks': [[mod.subtarget, [float(v) for row in mod.matrix_inverse for v in row]] for mod in wire.modifiers]})
        require(all(not rig.pose.bones[name].constraints for name in control), 'a generated Dress control has extra constraints.')
        for chain in record['chains']:
            for name in chain['def']:
                rotation = rig.pose.bones[name].constraints['Skirt physics delta']
                matches = [curve for curve in rig.animation_data.drivers
                           if curve.data_path == rotation.path_from_id() + '.influence']
                require(len(matches) == 1, 'the generated physical influence driver is ambiguous.')
                dress._identity_driver_curve(matches[0])
        source_rows.append({'source': source.name, 'owner': record['owner'], 'shared': skirt.is_shared(record),
                            'record_sha256': hashlib.sha256(source[skirt.RECORD_KEY].encode('utf-8')).hexdigest(),
                            'backend': physical.get('backend', 'LEGACY_CAGE') if physical else 'MANUAL'})
    require(set(surface_by_name) <= {row['source'] for row in source_rows}, 'a captured surface is outside this source inventory.')
    # Inspect every target, including muted constraints which an Action could
    # later enable. Only the exact native endpoints proved above may be external.
    for constraint in list(rig.constraints) + [con for pb in rig.pose.bones for con in pb.constraints]:
        require(constraint.type != 'ACTION', 'an Action constraint is outside the owned Dress snapshot contract.')
        for prop in constraint.bl_rna.properties:
            if prop.type == 'COLLECTION':
                require(not len(getattr(constraint, prop.identifier)), 'a nested constraint target is unsupported.')
            if prop.type == 'POINTER' and prop.identifier != 'rna_type':
                value = getattr(constraint, prop.identifier)
                if isinstance(value, bpy.types.ID) and value is not rig and value is not rig.data:
                    require(prop.identifier == 'target' and targets.get(int(constraint.as_pointer())) is value,
                            'an unknown external target drives this rig; bake that input separately.')
    for owner in (rig, rig.data):
        ad = owner.animation_data
        for curve in ad.drivers if ad else ():
            for variable in curve.driver.variables:
                for target in variable.targets:
                    require(target.id is rig or target.id is rig.data, 'an unknown external driver input drives this rig.')
    payload = {'sources': source_rows, 'wires': wire_rows, 'surface_proofs': surfaces}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    return {'schema': 'cdesigner.authenticated-dress-snapshot/1',
            'policy': 'existing-authenticated-Dress snapshot; retained base unsupported',
            'retained_base_supported': False, 'sha256': digest, 'sources': source_rows,
            'wire_count': len(wire_rows), 'surface_count': len(surfaces)}


def _begin_export(context, rig, action, filepath, *, frame_start, frame_end, loop=False, sample_rate=None,
                  export_rig_name=None, rigging_export=None, link_identity=None):
    global _ACTIVE_JOB
    from . import unity_export
    target_route = _target_route({} if link_identity is None else link_identity)
    if _ACTIVE_JOB or unity_export._ACTIVE_JOB:
        raise AnimationExportError('Wait for the current character export to finish.')
    if rig is None or rig.type != 'ARMATURE' or rig.name not in context.scene.objects:
        raise AnimationExportError('Choose the character Armature and an Action first.')
    if rig.mode == 'EDIT' or (rig.animation_data and rig.animation_data.use_tweak_mode):
        raise AnimationExportError('Leave Edit Mode and NLA Tweak Mode before exporting an Action.')
    if action is None or action.library:
        raise AnimationExportError('Choose a local editable Action.')
    frame_fps, frame_fps_base = _action_frame_clock(context.scene, action)
    from . import dress_export_guard
    try:
        direct_admission = dress_export_guard.animation_host_admission(
            bpy.data.objects, rig, action, _slot(rig, action))
    except (ValueError, TypeError) as error:
        raise AnimationExportError(str(error)) from error
    if not all(math.isfinite(value) for value in (frame_start, frame_end)) or frame_end <= frame_start:
        raise AnimationExportError('End Frame must be after Start Frame.')
    fps = frame_fps / frame_fps_base
    rate = fps if sample_rate is None else sample_rate
    if not math.isfinite(rate) or not 1 <= rate <= 240:
        raise AnimationExportError('Sampling must be between 1 and 240 frames per second.')
    if math.ceil((frame_end - frame_start) / fps * rate) > 20000:
        raise AnimationExportError('Choose a shorter Action range (at most 20,000 sample intervals).')
    destination = Path(bpy.path.abspath(str(filepath))).expanduser().resolve()
    filename = _filename(destination.name, rig)
    destination = destination.with_name(filename)
    metadata = destination.with_suffix('.animation.json')
    if destination.exists() or metadata.exists():
        raise AnimationExportError('That animation already exists. Choose a new filename to preserve it.')
    slot = _slot(rig, action)
    from . import animation_export_inputs
    # Meshes refer to the rig, not the other way round, and are deliberately not
    # copied into this small skeletal snapshot. Audit those reverse links here.
    from . import animation_export_worker as worker
    from .unity_export_worker import _is_control
    from . import dress_export_snapshot
    try:
        dress_surfaces = dress_export_snapshot.capture_animation_surfaces(context, rig, action)
        dress_scene_roots = dress_export_snapshot.snapshot_scene_roots(dress_surfaces)
    except (ValueError, TypeError) as error:
        raise AnimationExportError('Dress skeletal export boundary: ' + str(error)) from error
    dress_contract = (_authenticated_dress_export(rig, action, slot, direct_admission, dress_surfaces, worker)
                      if rigging_export is None else None)
    if dress_contract is not None and animation_export_inputs.RECORD in action:
        raise AnimationExportError('This Dress animation uses its existing snapshot inputs. '
                                   'Choose Clear Export Base Pose explicitly before exporting it.')
    # The established Rigging DTO and exact owned Dress closure retain their
    # specialized snapshot contracts. Neither adopts ordinary retained defaults.
    export_inputs = (animation_export_inputs.prepare(rig, action, slot, frame_start)
                     if rigging_export is None and dress_contract is None else None)
    omitted = worker._omitted_channels(rig, action, next((s for s in action.slots if s.handle == slot), None))
    discarded = {bone.name for bone in rig.data.bones if bone.use_deform and _is_control(bone)}
    if discarded:
        for mesh in context.scene.objects:
            if mesh.type != 'MESH' or not any(mod.type == 'ARMATURE' and mod.object == rig for mod in mesh.modifiers):
                continue
            indices = {group.index for group in mesh.vertex_groups if group.name in discarded}
            if indices and any(entry.group in indices and entry.weight > 1e-8
                               for vertex in mesh.data.vertices for entry in vertex.groups):
                raise AnimationExportError(mesh.name + ': a generated control has skin weights and cannot be omitted.')
    temporary = tempfile.TemporaryDirectory(prefix='cdesigner-action-')
    root = Path(temporary.name)
    stage = root / 'stage'
    stage.mkdir()
    source_package = str(action.get('character_designer_unity_animation_package', ''))
    source_hash = str(action.get('character_designer_unity_animation_package_sha256', ''))
    spec = {
        'rig': rig.name, 'action': action.name, 'action_slot': slot,
        'frame_start': float(frame_start), 'frame_end': float(frame_end),
        'fps': frame_fps, 'fps_base': frame_fps_base,
        'sample_rate': float(rate), 'unit_scale': context.scene.unit_settings.scale_length,
        'loop': bool(loop), 'name': action.name, 'stage': str(stage), 'filename': filename,
        'unsupported_channels': omitted,
        'dress_surfaces': dress_surfaces,
        **target_route,
    }
    if export_rig_name is not None:
        spec['export_rig_name'] = export_rig_name
    job = {'temporary': temporary, 'root': root, 'stage': stage, 'destination': destination,
           'metadata': metadata, 'started': time.monotonic(), 'source_package': source_package,
           'source_package_sha256': source_hash,
           'source_blend': bpy.data.filepath, 'action': action.name, 'action_slot': slot,
           'process': None, 'log': None}
    job.update(target_route)
    if export_inputs is not None:
        spec[animation_export_inputs.JOB_KEY] = export_inputs
        job[animation_export_inputs.JOB_KEY] = export_inputs
    if dress_contract is not None:
        spec['authenticated_dress_export'] = dress_contract
        job['authenticated_dress_export'] = dress_contract
    if rigging_export is not None:
        # This owned DTO was validated before launching. Publication never
        # resamples a changed live rig or reads the composed deform pose.
        job['rigging_export'] = rigging_export
    try:
        from . import hair_wiggle_adapter
        if hair_wiggle_adapter.status(context).get('active'):
            hair_wiggle_adapter.stop_preview(context, reason='Animation export ends transient Hair preview.')
        if dress_contract is not None:
            fresh_admission = dress_export_guard.animation_host_admission(bpy.data.objects, rig, action, slot)
            fresh_surfaces = dress_export_snapshot.capture_animation_surfaces(context, rig, action)
            fresh_contract = _authenticated_dress_export(rig, action, slot, fresh_admission, fresh_surfaces, worker)
            if fresh_admission != direct_admission or fresh_surfaces != dress_surfaces or fresh_contract != dress_contract:
                raise AnimationExportError('The authenticated Dress graph changed before snapshotting; retry with its intended inputs.')
        snapshot = root / 'animation.blend'
        spec['dress_snapshot_path'] = str(snapshot)
        snapshot_boundary = nullcontext()
        if 'character_designer_rigging_session_v1' in rig:
            from . import unity_animation_rigging as rigging
            snapshot_boundary = rigging.snapshot_base_refs(rig, action)
        with snapshot_boundary:
            bpy.data.libraries.write(str(snapshot), {rig, action} | dress_scene_roots,
                                    path_remap='ABSOLUTE', compress=True)
        spec[dress_export_guard.JOB_KEY] = dress_export_guard.bind_animation_snapshot(direct_admission, snapshot)
        (root / 'job.json').write_text(json.dumps(spec), encoding='utf-8')
        job['log'] = (root / 'worker.log').open('w', encoding='utf-8')
        worker_file = 'animation_export_inputs_worker.py' if export_inputs is not None else 'animation_export_worker.py'
        command = [bpy.app.binary_path, '--background', '--factory-startup', '--disable-autoexec',
                   '--threads', '2', str(snapshot), '--python-exit-code', '1', '--python',
                   str(Path(__file__).with_name(worker_file)), '--', str(root / 'job.json')]
        job['process'] = subprocess.Popen(command, stdout=job['log'], stderr=subprocess.STDOUT,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _ACTIVE_JOB = job
        return job
    except Exception:
        _dispose(job)
        raise


def _dispose(job):
    global _ACTIVE_JOB
    callback = job.pop('_dispose_callback', None)
    try:
        if job.get('log'):
            job['log'].close()
        job['temporary'].cleanup()
    finally:
        if _ACTIVE_JOB is job:
            _ACTIVE_JOB = None
        if callback:
            callback()


def _publish(job, result):
    from . import animation_export_inputs
    target_route = _target_route(job)
    returned_route = _target_route(result)
    if returned_route and returned_route != target_route:
        raise AnimationExportError('Worker animation routing differs from the frozen destination.')
    sealed = job.get(animation_export_inputs.JOB_KEY)
    if sealed is not None:
        expected = {'schema': animation_export_inputs.SCHEMA, 'sha256': sealed['sha256'],
                    'reference_frame': sealed['reference_frame'],
                    'unkeyed_components': sealed['unkeyed_components'],
                    'policy': 'captured-action-base-pose' if sealed['retained'] else 'fully-keyed-neutral-deltas'}
        if result.get(animation_export_inputs.JOB_KEY) != expected:
            raise AnimationExportError('Worker export base-pose proof differs from the sealed input contract.')
    destination, metadata = job['destination'], job['metadata']
    source = job['stage'] / destination.name
    if not source.is_file() or not source.stat().st_size:
        raise AnimationExportError('The worker did not produce a complete animation FBX.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {**result, 'source_blend': job['source_blend'], 'action': job['action'],
              'actionSlot': job.get('action_slot', -1),
              'origin': 'unity-edit' if job['source_package'] else 'blender-original',
              'source_package': job['source_package'],
              'source_package_sha256': job['source_package_sha256'],
              'channels': 'skeletal-only',
              'limitations': 'No Shape Key, material, camera, audio or animation-event export.'}
    report.update(target_route)
    if job.get('authenticated_dress_export') is not None:
        report['export_input_policy'] = job['authenticated_dress_export']
    if job.get('rigging_export') is not None:
        if job.get('action_slot') != job['rigging_export']['sourceActionSlot']:
            raise AnimationExportError('The frozen Rigging inputs belong to another exported base Action slot.')
        report['rigging'] = job['rigging_export']
    # Older imported Actions have no fixed source hash; leave it unknown rather
    # than attributing them to a possibly replaced packet at the same path.
    # Each staged file is on the destination volume. FBX is published last so
    # importers see only complete data; existing files are never replaced.
    published = []
    with tempfile.TemporaryDirectory(prefix='.cdesigner-action-', dir=destination.parent) as staging:
        staging = Path(staging)
        staged_fbx, staged_meta = staging / destination.name, staging / metadata.name
        digest = hashlib.sha256()
        with source.open('rb') as incoming, staged_fbx.open('xb') as outgoing:
            for block in iter(lambda: incoming.read(1024 * 1024), b''):
                outgoing.write(block)
                digest.update(block)
        report['fbx_sha256'] = digest.hexdigest()
        staged_meta.write_text(json.dumps(report, indent=2), encoding='utf-8')
        try:
            for src, dst in ((staged_meta, metadata), (staged_fbx, destination)):
                # Hard linking is atomic and never overwrites on either platform.
                os.link(src, dst)
                published.append(dst)
        except Exception:
            for path in reversed(published):
                path.unlink()
            raise
    return {**report, 'filepath': str(destination), 'metadata': str(metadata)}


def poll_export(job):
    try:
        pending = job['process'].poll() is None
    except Exception:
        cancel_export(job)
        raise
    if pending:
        if time.monotonic() - job['started'] > 600:
            cancel_export(job)
            raise AnimationExportError('Animation export timed out. The previous files were preserved.')
        return None
    try:
        result_path = job['stage'] / 'result.json'
        result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.is_file() else {}
        if job['process'].returncode != 0 or not result.get('ok', result.get('success', False)):
            log = (job['root'] / 'worker.log').read_text(encoding='utf-8', errors='replace')
            raise AnimationExportError(result.get('error') or log[-2000:] or 'Animation export failed.')
        published = _publish(job, result)
        callback = job.get('_publish_callback')
        return callback(job, published) if callback else published
    finally:
        _dispose(job)


def cancel_export(job=None):
    job = job or _ACTIVE_JOB
    if job is None:
        return
    try:
        process = job.get('process')
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    finally:
        _dispose(job)


def export_action(context, rig, action, filepath, **options):
    """Synchronous entry for isolated validation; UI uses begin/poll/cancel."""
    job = begin_export(context, rig, action, filepath, **options)
    while True:
        result = poll_export(job)
        if result is not None:
            return result
        time.sleep(0.1)
