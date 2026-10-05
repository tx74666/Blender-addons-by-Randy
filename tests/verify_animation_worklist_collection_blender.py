"""Real two-motion collection QA, isolated from artist scenes and Unity.

Run only in a fresh factory-startup process:
  blender --background --factory-startup --disable-autoexec --threads 1 \
    --python-exit-code 1 --python THIS_FILE -- \
    --candidate-addons ADDONS --workspace PRIVATE_WORKSPACE --output NEW_OUTPUT

By default the two Ready Links/packets are copied, preserving the full catalog.
An explicit --qa-publication-root permits publishing to the supplied private
AW/Pairs workspace's original Links. Source packets and models stay read-only.
The model is read-only. No importer, Action activation, worker or publication is
mocked. Timers are advanced explicitly by this script's one native thread.
Only this runner caches already-verified motion digests; runtime scan and
publication proofs are always computed afresh. An optional --cancel-file below
NEW_OUTPUT requests graceful cancellation at the next safe checkpoint.
"""

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import traceback
from types import SimpleNamespace
import uuid

import bpy


ARTIST_FILE = Path('D:/Blender/Projects/Character/X/X.blend')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def content_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def plain_scene(scene):
    """Factory author assets/context, excluding deliberately connected metadata."""
    layer = scene.view_layers[0]
    layer.update()  # Inactive scenes may have unevaluated matrix_world after open_mainfile.
    objects = []
    for obj in sorted(scene.objects, key=lambda value: value.name):
        item = dict(name=obj.name, type=obj.type, data=obj.data.name if obj.data else None,
                    matrix=[list(row) for row in obj.matrix_world],
                    selected=obj.select_get(view_layer=layer), mode=obj.mode)
        if obj.type == 'MESH':
            item['mesh'] = dict(vertices=[list(vertex.co) for vertex in obj.data.vertices],
                                edges=[list(edge.vertices) for edge in obj.data.edges],
                                faces=[list(face.vertices) for face in obj.data.polygons])
        objects.append(item)
    return dict(name=scene.name, objects=objects,
                active=layer.objects.active.name if layer.objects.active else None,
                frame=scene.frame_current, subframe=scene.frame_subframe,
                fps=scene.render.fps, fps_base=scene.render.fps_base,
                unit_scale=scene.unit_settings.scale_length)


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--candidate-addons', type=Path, required=True)
    args.add_argument('--workspace', type=Path, required=True)
    args.add_argument('--output', type=Path, required=True)
    args.add_argument('--qa-publication-root', type=Path)
    args.add_argument('--cancel-file', type=Path)
    options = args.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    output = options.output.resolve()
    qa_root = options.qa_publication_root.resolve() if options.qa_publication_root else None
    cancel_file = options.cancel_file.resolve() if options.cancel_file else None
    if cancel_file is not None and not cancel_file.is_relative_to(output):
        raise ValueError('A cancellation sentinel must remain inside this QA output.')
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / 'collection_native_qa.json'
    if report_path.exists() or (output / 'private_workspace').exists():
        raise RuntimeError('Choose a new QA output; existing evidence is never overwritten.')
    started = time.monotonic()
    deadline = started + 600.0
    report = dict(passed=False, checks=[], errors=[], timings=[], worker_processes=[],
                  artist_file=str(ARTIST_FILE), artist_sha_before=sha(ARTIST_FILE) if ARTIST_FILE.is_file() else None,
                  blender_version=bpy.app.version_string, runner_sha=sha(__file__),
                  candidate_addons=str(options.candidate_addons.resolve()),
                  supplied_workspace=str(options.workspace.resolve()), output=str(output),
                  qa_publication_root=str(qa_root) if qa_root else None, memory_checks=[],
                  motion_proof_policy='Full after Add, after own Custom edit and edited Custom reopen; '
                      'cached runner expectations otherwise; runtime publication/scan proofs never cached.',
                  cancellation_sentinel=str(cancel_file) if cancel_file else None)
    native = None
    protected = {}
    checkpoints = []
    owned_processes = []
    observed_jobs = set()
    original_links = []
    motion_cache = {}
    edited_customs = set()
    edit_specs = {}
    stage = 'preflight'

    def flush():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def check(name, condition, detail=None):
        entry = dict(name=name, passed=bool(condition))
        if detail is not None:
            entry['details'] = detail
        report['checks'].append(entry)
        flush()
        if not condition:
            raise AssertionError(name + (': ' + str(detail) if detail is not None else ''))

    def budget():
        if cancel_file is not None and cancel_file.exists():
            report['cancelled'] = True
            raise InterruptedError('QA cancellation requested; completed work will be retained.')
        if time.monotonic() > deadline:
            raise TimeoutError('The complete native QA exceeded its 600 second budget.')

    def memory_gate(label, key=None):
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ('total_physical', 'available_physical',
                'total_pagefile', 'available_pagefile', 'total_virtual', 'available_virtual', 'extended')]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError('GlobalMemoryStatusEx failed before ' + label)
        report['memory_checks'].append(dict(stage=label, key=key,
            available_bytes=status.available_physical, load_percent=status.load,
            at=time.monotonic() - started))
        flush()
        if status.available_physical < 200 * 1024 * 1024:
            raise MemoryError('Less than 200 MiB physical memory available before ' + label +
                              '; completed QA work is retained.')

    def observe_worker():
        job = native.animation_export.active_job()
        token = (job['process'].pid, job['started']) if job is not None else None
        if job is not None and token not in observed_jobs:
            observed_jobs.add(token)
            process = job['process']
            check('Owned workers are serial', not any(previous.poll() is None for previous in owned_processes))
            owned_processes.append(process)
            report['worker_processes'].append(dict(pid=process.pid, action=job['action'],
                                                   destination=str(job['destination']),
                                                   observed_at=time.monotonic() - started))
        return job

    def pump(queue=None, label='Sync baseline'):
        item_started = {}
        completed = set()
        if queue is None:
            began = time.monotonic()
        while (queue.busy if queue is not None else native.animation_export.active_job() is not None):
            budget()
            if observe_worker() is not None:
                native.animation._poll_action_export()
            if queue is not None:
                if queue.running is None and queue.remaining_keys:
                    memory_gate(label + ' next launch', queue.remaining_keys[0])
                    item_started.setdefault(queue.remaining_keys[0], time.monotonic())
                native.collection._poll_collection()
                observe_worker()
                for success in queue.successes:
                    if success.token not in completed:
                        completed.add(success.token)
                        report['timings'].append(dict(stage=label, key=success.key,
                            seconds=time.monotonic() - item_started.get(success.key, time.monotonic())))
                        flush()
            time.sleep(.05)
        if queue is None:
            report['timings'].append(dict(stage=label, seconds=time.monotonic() - began))
        else:
            native.collection._poll_collection()  # Release the completed batch owner.
            check(label + ' completed', queue.state == 'COMPLETED' and len(queue.successes) == queue.total,
                  dict(state=queue.state, total=queue.total, completed=len(queue.successes),
                       failure=queue.failure.message if queue.failure else None))
        for callback in (native.collection._poll_collection, native.animation._poll_action_export):
            if bpy.app.timers.is_registered(callback):
                bpy.app.timers.unregister(callback)
        budget()

    def action_reference(action):
        library = str(Path(bpy.path.abspath(action.library.filepath)).resolve()) if action.library else None
        return dict(name=action.name, library=library)

    def motion(item, side, *, full=False, reopened=False):
        budget()
        action, handle = getattr(item, side + '_action'), getattr(item, side + '_slot')
        key = (item.item_id, side)
        reference = action_reference(action)
        binding = (action.as_pointer(), handle, reference)
        check('Motion Action remains in native membership', any(
            action.as_pointer() == value.as_pointer() for value in bpy.data.actions))
        cached = motion_cache.get(key)
        if full:
            curves = native.export_worker._curves(action, native.worklist._slot(action, handle))
            key_points = sum(len(curve.keyframe_points) for curve in curves)
            sampled_points = sum(len(curve.sampled_points) for curve in curves)
            began = time.monotonic()
            try:
                result = native.fingerprint.fingerprint_action(action, handle)
            finally:
                report['timings'].append(dict(stage=stage, operation='Complete runner motion hash',
                    key=item.item_id, side=side, action=action.name, slot=handle,
                    curves=len(curves), keyframe_points=key_points, sampled_points=sampled_points,
                    points=key_points + sampled_points,
                    seconds=time.monotonic() - began))
                flush()
            check('Native Action content is inspectable', result['fingerprint_known'], result['proof_reason'])
            motion_cache[key] = dict(binding=binding, digest=result['action_fingerprint'])
        else:
            check('Motion has a prior complete native proof', cached is not None, dict(key=key))
            native.worklist._slot(action, handle)
            if reopened:
                check('Reopened Action preserves its exact reference and slot', binding[1:] == cached['binding'][1:])
                cached['binding'] = binding  # Only the private reopen changes native pointer identities.
            else:
                check('Cached motion preserves its exact Action pointer/reference/slot', binding == cached['binding'])
        if side == 'source':
            check('Linked Source remains immutable and its source file exact', action.library is not None
                  and not action.is_editable and sha(item.source_file) == protected[item.source_file])
        budget()
        return motion_cache[key]['digest']

    def entries(saved, *, reopened=False):
        return [dict(item_id=item.item_id, clip_key=item.clip_key, name=item.name,
                     source=motion(item, 'source', reopened=reopened),
                     custom=motion(item, 'custom', full=reopened and item.item_id in edited_customs,
                                   reopened=reopened),
                     source_reference=action_reference(item.source_action),
                     custom_reference=action_reference(item.custom_action),
                     source_file=item.source_file, source_hash=sha(item.source_file),
                     source_slot=item.source_slot, custom_slot=item.custom_slot,
                     link_identity=item.link_identity, manifest=item.manifest_path,
                     receipt=item.last_synced_receipt) for item in saved.items]

    def publications(saved):
        result = {}
        for item in saved.items:
            path = Path(item.manifest_path)
            link = native.links.load_link(path)
            result[str(path)] = dict(revision=link.get('revision', 0), manifest_sha=sha(path))
            for field in ('fbxFile', 'metadataFile'):
                if link.get(field):
                    candidate = Path(link[field]).resolve()
                    check('Publication remains inside private QA', candidate.is_relative_to(output) or
                          (qa_root is not None and candidate.is_relative_to(qa_root)))
                    result[str(candidate)] = dict(sha=sha(candidate), bytes=candidate.stat().st_size)
        return result

    def scan(label):
        budget()
        began = time.monotonic()
        counts = native.collection.scan_changes(bpy.context)
        report['timings'].append(dict(stage=label, seconds=time.monotonic() - began))
        report.setdefault('scans', []).append(dict(stage=label, counts=counts,
            items=[dict(name=item.name, state=item.scan_state, reason=item.scan_reason)
                   for item in native.worklist.state(bpy.context).items]))
        flush()
        budget()
        return counts

    try:
        check('Fresh factory-startup process', '--factory-startup' in sys.argv and not bpy.data.filepath)
        check('Artist file is not a QA destination', not output.is_relative_to(ARTIST_FILE) and output != ARTIST_FILE)
        addon_root = options.candidate_addons.resolve()
        if addon_root.name == 'character_designer':
            addon_root = addon_root.parent
        check('Candidate package exists', (addon_root / 'character_designer/__init__.py').is_file())
        check('No cached Character Designer package', 'character_designer' not in sys.modules)
        sys.path.insert(0, str(addon_root))
        import character_designer
        character_designer.register()
        character_designer._validate_registration_integrity()
        from character_designer import animation, animation_export, animation_link as links
        from character_designer import animation_workspace, animation_worklist as worklist
        from character_designer import animation_worklist_collection as collection
        from character_designer import animation_worklist_fingerprint as fingerprint
        from character_designer import animation_export_worker as export_worker
        native = SimpleNamespace(animation=animation, animation_export=animation_export, links=links,
                                 worklist=worklist, collection=collection, fingerprint=fingerprint,
                                 export_worker=export_worker)
        report['candidate_version'] = list(character_designer.bl_info['version'])
        report['candidate_path'] = character_designer.__file__
        supplied = animation_workspace.load_workspace(options.workspace.resolve())
        ready = [(index, clip) for index, clip in enumerate(supplied['clips'])
                 if clip.get('linkManifest') and not clip.get('needsRebuild')
                 and Path(clip['linkManifest']).is_file()]
        check('Exactly two Ready motions in full catalog', len(ready) == 2)
        report['catalog_count'] = len(supplied['clips'])
        if qa_root is not None:
            parts = [part.lower() for part in qa_root.parts]
            pairs = next((index for index in range(len(parts) - 1)
                          if parts[index:index + 2] == ['aw', 'pairs']), None)
            check('Explicit private root is below AW/Pairs', pairs is not None and
                  len(parts) > pairs + 2 and qa_root.is_dir())
            check('Original workspace and both Ready Links lie inside explicit QA root',
                  options.workspace.resolve().is_relative_to(qa_root) and all(
                      Path(clip['linkManifest']).resolve().is_relative_to(qa_root) for _, clip in ready))
        protected[str(options.workspace.resolve())] = sha(options.workspace)
        private = output / 'private_workspace'
        if qa_root is None:
            private.mkdir()
        copied = {key: value for key, value in supplied.items() if not key.startswith('_')}
        if qa_root is None:
            copied['workspaceId'] = str(uuid.uuid4())
        copied['clips'] = [dict(clip) for clip in supplied['clips']]
        packet_motions = []
        for index, clip in ready:
            link = links.load_link(clip['linkManifest'])
            source_packet = Path(link['sourcePackage']).resolve()
            manifest_path = Path(clip['linkManifest']).resolve()
            original_links.append(dict(path=str(manifest_path), sha=sha(manifest_path),
                link_id=link['linkId'], revision=link.get('revision', 0), name=clip.get('name')))
            if qa_root is None:
                protected[str(manifest_path)] = sha(manifest_path)
            protected[str(source_packet)] = sha(source_packet)
            for field in ('fbxFile', 'metadataFile'):
                if link.get(field):
                    original_output = Path(link[field]).resolve()
                    protected[str(original_output)] = sha(original_output)
            packet_data = json.loads(source_packet.read_text(encoding='utf-8-sig'))
            clip_name = packet_data.get('clipName')
            check('Exact private QA motion has an explicit forearm role', clip_name in {'Walk_N', 'Idle'})
            role, suffix = ('LeftLowerArm', 'L') if clip_name == 'Walk_N' else ('RightLowerArm', 'R')
            bone_name = 'forearm.' + suffix
            bone_path = ('CoshaRig/Hips/spine/Chest/UpperChest/shoulder.' + suffix +
                         '/upper_arm.' + suffix + '/' + bone_name)
            role_bones = [bone for bone in packet_data['bones'] if bone.get('humanRole') == role]
            check('Source packet role identifies one exact forearm path', len(role_bones) == 1
                  and role_bones[0]['name'] == bone_name and role_bones[0].get('path') == bone_path)
            edit_specs[worklist._clip_key(clip)] = dict(clip=clip_name, role=role, bone=bone_name, path=bone_path)
            packet_motions.append(content_sha(packet_data['frames']))
            del packet_data
            model = Path(link.get('modelFile') or supplied['modelFile']).resolve()
            protected[str(model)] = sha(model)
            if qa_root is not None:
                continue
            folder = private / ('clip_' + str(index + 1))
            folder.mkdir()
            packet = folder / source_packet.name
            shutil.copyfile(source_packet, packet)
            protected[str(packet)] = sha(packet)
            link_copy = {key: value for key, value in link.items()
                         if not key.startswith('_') and key not in links.OUTPUT_FIELDS}
            link_copy.update(linkId=str(uuid.uuid4()), revision=0, sourcePackage=str(packet))
            manifest = folder / links.MANIFEST_NAME
            manifest.write_text(json.dumps(link_copy, ensure_ascii=False, indent=2), encoding='utf-8')
            copied['clips'][index]['linkManifest'] = str(manifest)
        check('Packets contain two distinct real motions', len(set(packet_motions)) == 2)
        workspace_path = options.workspace.resolve() if qa_root is not None else private / animation_workspace.MANIFEST_NAME
        if qa_root is None:
            workspace_path.write_text(json.dumps(copied, ensure_ascii=False, indent=2), encoding='utf-8')
        report['private_workspace'] = str(workspace_path)
        report['qa_links_before'] = original_links
        author_scene = bpy.context.scene
        author_name = author_scene.name
        author_before = plain_scene(author_scene)
        stage = 'Add Ready'
        worklist.connect(bpy.context, str(workspace_path))
        check('Entire supplied catalog preserved', [item.clip_key for item in worklist.state(bpy.context).catalog] ==
              [worklist._clip_key(clip) for clip in supplied['clips']])
        queue = collection.begin_add_ready(bpy.context)
        check('Add Ready queued both motions', queue.total == 2)
        pump(queue, 'Add Ready')
        saved = worklist.state(bpy.context)
        check('One native editing rig shared by two entries', len(saved.items) == 2 and saved.rig is not None
              and all(item.rig is saved.rig for item in saved.items)
              and len([obj for obj in bpy.context.scene.objects if obj.type == 'ARMATURE']) == 1)
        check('Sources linked/read-only and Customs independent/local', all(
            item.source_action.library is not None and not item.source_action.is_editable
            and item.custom_action.library is None and item.custom_action is not item.source_action
            for item in saved.items) and len({item.custom_action.as_pointer() for item in saved.items}) == 2)
        check('Factory author assets/context unaffected by imports', plain_scene(author_scene) == author_before)
        for item in saved.items:
            protected[item.source_file] = sha(item.source_file)
            motion(item, 'source', full=True)
            motion(item, 'custom', full=True)
        before_repeat = entries(saved)
        repeated = collection.begin_add_ready(bpy.context)
        check('Repeated Add Ready has no new work', repeated.total == 0 and not repeated.busy)
        check('Repeated Add Ready preserves Actions and membership', entries(saved) == before_repeat)

        stage = 'Seed real publication'
        worklist.activate(bpy.context, saved.items[1].item_id, 'CUSTOM')
        memory_gate('Baseline export', saved.items[1].item_id)
        seed = worklist.sync(bpy.context)
        pump(label='Baseline export')
        check('Baseline receipt records a complete native proof',
              json.loads(saved.items[1].last_synced_receipt)['proof']['fingerprint_known'],
              json.loads(saved.items[1].last_synced_receipt)['proof']['proof_reason'])
        initial_publications = publications(saved)
        report['publication_baseline'] = initial_publications
        stage = 'Edit both Customs'
        for index in range(2):
            budget()
            item = saved.items[index]
            sources_before = [motion(row, 'source') for row in saved.items]
            other = saved.items[1 - index]
            other_before = motion(other, 'custom')
            # This is the full pre-edit proof obtained after Add, or the previous
            # verified edit. Only this runner's known edit invalidates that proof.
            own_before = motion(item, 'custom')
            worklist.activate(bpy.context, item.item_id, 'CUSTOM')
            spec = edit_specs[item.clip_key]
            bone = item.rig.pose.bones.get(spec['bone'])
            check('Exact packet forearm exists in the shared editing rig', bone is not None)
            ancestry = [parent.name for parent in reversed(bone.parent_recursive)] + [bone.name]
            check('Forearm hierarchy matches the exact source packet path',
                  '/'.join(['CoshaRig'] + ancestry) == spec['path'])
            field = ('rotation_quaternion' if bone.rotation_mode == 'QUATERNION' else
                     'rotation_axis_angle' if bone.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
            components = {1, 2, 3} if field == 'rotation_quaternion' else ({0} if field == 'rotation_axis_angle' else {0, 1, 2})
            slot = worklist._slot(item.custom_action, item.custom_slot)
            curves = export_worker._curves(item.custom_action, slot)
            matches = [curve for curve in curves if curve.data_path == bone.path_from_id() + '.' + field
                       and curve.array_index in components and not curve.mute and len(curve.keyframe_points) >= 2]
            check('Existing enabled exact forearm rotation components', bool(matches)
                  and len({curve.array_index for curve in matches}) == len(matches))
            middle = sum(item.custom_action.frame_range) / 2.
            # A small quaternion vector component gives an unambiguous angular
            # change after normalization; never offset quaternion W alone.
            target = min(matches, key=lambda curve: (abs(curve.evaluate(middle)), curve.array_index))
            check('Edited curve belongs only to its own Custom', not any(
                target.as_pointer() == curve.as_pointer() for curve in
                export_worker._curves(other.custom_action, worklist._slot(other.custom_action, other.custom_slot))))
            first, last = target.keyframe_points[0].co.x, target.keyframe_points[-1].co.x
            check('Forearm motion has an interior sample interval', first < last)
            phases = [(phase, first + (last - first) * phase) for phase in (.25, .5, .75)]
            before_values = {frame: target.evaluate(frame) for _phase, frame in phases}
            offset = .1
            del motion_cache[(item.item_id, 'custom')]
            edited_customs.add(item.item_id)
            updated = 0
            for point in target.keyframe_points:
                if phases[0][1] <= point.co.x <= phases[-1][1]:
                    point.co.y += offset
                    point.handle_left.y += offset
                    point.handle_right.y += offset
                    updated += 1
            inserted_frames = []
            for _phase, frame in phases:
                point = next((point for point in target.keyframe_points if abs(point.co.x - frame) < 1e-5), None)
                if point is None:
                    point = target.keyframe_points.insert(frame, before_values[frame] + offset, options={'FAST'})
                    point.interpolation = 'LINEAR'
                    inserted_frames.append(frame)
                else:
                    delta = before_values[frame] + offset - point.co.y
                    point.co.y += delta
                    point.handle_left.y += delta
                    point.handle_right.y += delta
            target.update()
            report.setdefault('custom_edits', []).append(dict(**spec, item_id=item.item_id, field=field,
                component=target.array_index, offset=offset, phases=[dict(phase=phase, frame=frame)
                for phase, frame in phases], updated_keys=updated, inserted_frames=inserted_frames))
            check('Custom edit changed only its own motion', motion(item, 'custom', full=True) != own_before
                  and motion(other, 'custom') == other_before)
            check('Both linked Sources remain exact', sources_before ==
                  [motion(row, 'source') for row in saved.items])
        stage = 'Save/reopen edited worklist'
        budget()
        worklist_path = output / 'worklist.blend'
        before_reopen = entries(saved)
        editing_name = bpy.context.scene.name
        association = dict(link=saved.rig.get(links.LINK_KEY), action=saved.rig.get(links.ACTION_KEY).name)
        check('Save private editing file succeeded', 'FINISHED' in bpy.ops.wm.save_as_mainfile(
            filepath=str(worklist_path), copy=False, check_existing=False))
        checkpoints.append(str(worklist_path))
        check('Open only private editing file succeeded', 'FINISHED' in bpy.ops.wm.open_mainfile(filepath=str(worklist_path)))
        check('Reopened the private worklist scene', bpy.context.scene.name == editing_name)
        saved = worklist.state(bpy.context)
        check('Actions, slots, sources, membership and receipt survive reopen',
              entries(saved, reopened=True) == before_reopen)
        check('Exact native Action/Link association survives reopen',
              saved.rig.get(links.LINK_KEY) == association['link'] and saved.rig.get(links.ACTION_KEY).name == association['action'])
        check('Factory author assets survive checkpoint', plain_scene(bpy.data.scenes[author_name]) == author_before)
        stage = 'Sync Unknown and Changed'
        counts = scan('Edited scan')
        check('Fresh unknown and previously published changed are distinguished',
              counts == dict(CHANGED=1, UNCHANGED=0, UNKNOWN=1, BLOCKED=0), report['scans'][-1])
        for item in saved.items:
            item.sync_selected = item.scan_state in {'UNKNOWN', 'CHANGED'}
        queue = collection.begin_sync_changed(bpy.context)
        check('Explicit Unknown plus Changed queued', queue.total == 2)
        pump(queue, 'Edited export')
        receipts = [json.loads(item.last_synced_receipt) for item in saved.items]
        check('Every successful receipt has a complete snapshot proof',
              all(receipt['proof']['fingerprint_known'] for receipt in receipts),
              [receipt['proof']['proof_reason'] for receipt in receipts])
        final_publications = publications(saved)
        report['publication_final'] = final_publications
        report['baseline_outputs_after'] = {path: sha(path) for path, value in initial_publications.items()
                                            if 'sha' in value}
        check('Prior immutable FBX/metadata outputs remain exact', all(
            report['baseline_outputs_after'][path] == value['sha']
            for path, value in initial_publications.items() if 'sha' in value))
        check('Both native Link revisions advanced', all(
            final_publications[item.manifest_path]['revision'] > initial_publications[item.manifest_path]['revision']
            for item in saved.items))
        stage = 'Unchanged no-op'
        counts = scan('After successful Sync')
        check('Both entries are truly Unchanged', counts == dict(CHANGED=0, UNCHANGED=2, UNKNOWN=0, BLOCKED=0),
              report['scans'][-1])
        no_op = collection.begin_sync_changed(bpy.context)
        check('Unchanged Sync creates no worker or output', no_op.total == 0 and not no_op.busy
              and animation_export.active_job() is None and publications(saved) == final_publications)
        check('Three real workers completed serially', len(owned_processes) == 3 and
              all(process.poll() == 0 for process in owned_processes))
        check('Factory author assets remain untouched', plain_scene(bpy.data.scenes[author_name]) == author_before)
        check('Save final private receipts succeeded', 'FINISHED' in bpy.ops.wm.save_as_mainfile(
            filepath=str(worklist_path), copy=False, check_existing=False))
        report['worklist_file'] = dict(path=str(worklist_path), sha=sha(worklist_path), bytes=worklist_path.stat().st_size)
        report['receipts'] = receipts
        report['passed'] = True
    except Exception as exc:
        report['errors'].append(dict(stage=stage, type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()))
        if native is not None:
            try:
                native.collection.cancel(message='Isolated QA failed; completed publications were retained.')
                if native.animation_export.active_job() is not None:
                    native.animation_export.cancel_export()
                native.collection.stop()
                if bpy.context.scene.character_designer_animation_worklist.items:
                    partial = output / 'partial_worklist.blend'
                    if 'FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(partial), copy=False, check_existing=False):
                        checkpoints.append(str(partial))
            except Exception as cleanup:
                report['errors'].append(dict(stage='Preserve completed work', type=type(cleanup).__name__, message=str(cleanup)))
    finally:
        report['qa_links_after'] = []
        for initial in original_links:
            try:
                path = Path(initial['path'])
                current = json.loads(path.read_text(encoding='utf-8-sig'))
                report['qa_links_after'].append(dict(path=str(path), sha=sha(path),
                    link_id=current['linkId'], revision=current.get('revision', 0)))
                if current['linkId'] != initial['link_id']:
                    report['passed'] = False
                    report['errors'].append(dict(stage='Link identity', message='Original private Link identity changed.'))
            except Exception as exc:
                report['passed'] = False
                report['errors'].append(dict(stage='Link evidence', message=str(exc)))
        report['protected_inputs'] = [dict(path=path, before=digest,
            after=sha(path) if Path(path).is_file() else None) for path, digest in protected.items()]
        report['artist_sha_after'] = sha(ARTIST_FILE) if ARTIST_FILE.is_file() else None
        report['checkpoints'] = checkpoints
        report['elapsed_seconds'] = time.monotonic() - started
        unchanged = (report['artist_sha_before'] == report['artist_sha_after'] and all(
            record['before'] == record['after'] for record in report['protected_inputs']))
        report['checks'].append(dict(name='Artist file, supplied inputs/model and private packets remain exact', passed=unchanged))
        if not unchanged:
            report['passed'] = False
            report['errors'].append(dict(stage='Protected files', message='A protected input changed during isolated QA.'))
        flush()
        print('COLLECTION_NATIVE_QA ' + json.dumps(dict(passed=report['passed'], report=str(report_path))))
    if not report['passed']:
        raise RuntimeError('Native collection QA failed; inspect ' + str(report_path))


if __name__ == '__main__':
    main()
