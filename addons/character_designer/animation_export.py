"""Export one explicit Action from an isolated snapshot, without saving the scene."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time

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


def begin_export(context, rig, action, filepath, *, frame_start, frame_end, loop=False, sample_rate=None,
                 export_rig_name=None):
    global _ACTIVE_JOB
    from . import unity_export
    if _ACTIVE_JOB or unity_export._ACTIVE_JOB:
        raise AnimationExportError('Wait for the current character export to finish.')
    if rig is None or rig.type != 'ARMATURE' or rig.name not in context.scene.objects:
        raise AnimationExportError('Choose the character Armature and an Action first.')
    if rig.mode == 'EDIT' or (rig.animation_data and rig.animation_data.use_tweak_mode):
        raise AnimationExportError('Leave Edit Mode and NLA Tweak Mode before exporting an Action.')
    if action is None or action.library:
        raise AnimationExportError('Choose a local editable Action.')
    if not all(math.isfinite(value) for value in (frame_start, frame_end)) or frame_end <= frame_start:
        raise AnimationExportError('End Frame must be after Start Frame.')
    fps = context.scene.render.fps / context.scene.render.fps_base
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
        'fps': context.scene.render.fps, 'fps_base': context.scene.render.fps_base,
        'sample_rate': float(rate), 'unit_scale': context.scene.unit_settings.scale_length,
        'loop': bool(loop), 'name': action.name, 'stage': str(stage), 'filename': filename,
        'unsupported_channels': omitted,
        'dress_surfaces': dress_surfaces,
    }
    if export_rig_name is not None:
        spec['export_rig_name'] = export_rig_name
    job = {'temporary': temporary, 'root': root, 'stage': stage, 'destination': destination,
           'metadata': metadata, 'started': time.monotonic(), 'source_package': source_package,
           'source_package_sha256': source_hash,
           'source_blend': bpy.data.filepath, 'action': action.name, 'process': None, 'log': None}
    try:
        from . import hair_wiggle_adapter
        if hair_wiggle_adapter.status(context).get('active'):
            hair_wiggle_adapter.stop_preview(context, reason='Animation export ends transient Hair preview.')
        snapshot = root / 'animation.blend'
        spec['dress_snapshot_path'] = str(snapshot)
        bpy.data.libraries.write(str(snapshot), {rig, action} | dress_scene_roots,
                                path_remap='ABSOLUTE', compress=True)
        (root / 'job.json').write_text(json.dumps(spec), encoding='utf-8')
        job['log'] = (root / 'worker.log').open('w', encoding='utf-8')
        command = [bpy.app.binary_path, '--background', '--factory-startup', '--disable-autoexec',
                   '--threads', '2', str(snapshot), '--python-exit-code', '1', '--python',
                   str(Path(__file__).with_name('animation_export_worker.py')), '--', str(root / 'job.json')]
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
    destination, metadata = job['destination'], job['metadata']
    source = job['stage'] / destination.name
    if not source.is_file() or not source.stat().st_size:
        raise AnimationExportError('The worker did not produce a complete animation FBX.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {**result, 'source_blend': job['source_blend'], 'action': job['action'],
              'origin': 'unity-edit' if job['source_package'] else 'blender-original',
              'source_package': job['source_package'],
              'source_package_sha256': job['source_package_sha256'],
              'channels': 'skeletal-only',
              'limitations': 'No Shape Key, material, camera, audio or animation-event export.'}
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
