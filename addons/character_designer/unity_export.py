"""Character-scoped Unity export: snapshot first, publish only complete results.

The worker runs in a separate Blender process. No export modifier, rig, pose,
material, or shape-key conversion ever touches the artist's live datablocks.
"""
import hashlib
from array import array
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

import bpy

from . import character_setup

SCHEMA = "cdesigner.unity-character.v1"
_ACTIVE_JOB = None


class ExportError(ValueError):
    pass


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _descends(obj, ancestor):
    seen = set()
    while obj is not None and obj not in seen:
        if obj == ancestor:
            return True
        seen.add(obj)
        obj = obj.parent
    return False


def _armatures(obj):
    return {mod.object for mod in obj.modifiers
            if mod.type == 'ARMATURE' and mod.object is not None}


def _binding_armatures(obj):
    """Visible skinning modifiers, independent of the object's own visibility."""
    return {mod.object for mod in obj.modifiers
            if mod.type == 'ARMATURE' and mod.object is not None
            and mod.show_viewport and (mod.use_vertex_groups or mod.use_bone_envelopes)}


def _character_armatures(scene, rig):
    if rig is None or rig.type != 'ARMATURE' or rig.name not in scene.objects:
        raise ExportError('Set the character Main Rig in Character Setup first.')
    return {rig} | {obj for obj in scene.objects
                    if obj.type == 'ARMATURE' and _descends(obj.parent, rig)}


def _helpers(scene):
    shapes = {pb.custom_shape for obj in scene.objects if obj.type == 'ARMATURE'
              for pb in obj.pose.bones if pb.custom_shape is not None}
    for obj in scene.objects:
        # Ownership roles, not names or visibility: hidden clothes still export.
        roles = [str(obj.get(key, '')).upper() for key in obj.keys()
                 if str(key).startswith('character_designer') and str(key).endswith('role')]
        if any(any(token in role for token in ('WIDGET', 'COLLIDER', 'PROXY', 'GUIDE')) for role in roles):
            shapes.add(obj)
        if any(c.get('character_designer_widget_container_role') for c in obj.users_collection):
            shapes.add(obj)
    return shapes


def bound_meshes(context, rig):
    """Return meshes with a working Armature binding; saved hints cannot add any."""
    scene = context.scene
    rigs = _character_armatures(scene, rig)
    helpers = _helpers(scene)
    meshes = []
    for obj in scene.objects:
        if obj.type != 'MESH' or obj in helpers:
            continue
        bound = _binding_armatures(obj)
        if bound & rigs:
            if bound - rigs:
                raise ExportError(f'{obj.name}: linked to more than this character rig.')
            meshes.append(obj)
    return sorted(meshes, key=lambda obj: obj.name)


def collect_character(context, rig, config):
    scene = context.scene
    rigs = _character_armatures(scene, rig)
    helpers = _helpers(scene)
    eligible = set(bound_meshes(context, rig))
    objects = set(eligible)
    warnings = []
    setup = character_setup.settings(context)
    for entry in config.extras:
        obj = entry.object
        if not entry.enabled:
            if obj == rig or (setup and obj == setup.body and obj in eligible):
                raise ExportError('The main rig and body cannot be excluded.')
            if obj is not None:
                objects.discard(obj)
            continue
        if obj is None or obj.name not in scene.objects:
            raise ExportError('A saved export reference is missing; clear it under Objects.')
        if obj.type != 'MESH' or obj in helpers:
            raise ExportError(f'{obj.name}: a controller or helper cannot be exported; clear this reference under Objects.')
        if _binding_armatures(obj) - rigs:
            raise ExportError(f'{obj.name}: bound to another rig; use that character export profile.')
        if obj not in eligible:
            warnings.append(f'{obj.name}: skipped; no enabled Armature binding to this character.')
    # Old registrations and extra references cannot bypass the binding requirement.
    if setup:
        for item in setup.assets:
            obj = item.object
            if (obj is not None and obj.name in scene.objects and obj not in objects
                    and obj not in eligible and obj not in helpers):
                warnings.append(f'{obj.name}: skipped; no enabled Armature binding to this character.')
    meshes = [o for o in objects if o.type == 'MESH']
    if not meshes:
        raise ExportError('No bound character meshes found. Enable an Armature binding to the Main Rig first.')
    for obj in meshes:
        if not obj.data.vertices:
            raise ExportError(f'{obj.name}: mesh has no vertices.')
        objects.update(_binding_armatures(obj))
    # Only keep accessory rigs used by selected meshes, plus their rig ancestors.
    # A character may contain other attached helper rigs without exportable skins.
    for arm in tuple(objects):
        if arm.type == 'ARMATURE':
            parent = arm.parent
            while parent is not None:
                if parent in rigs:
                    objects.add(parent)
                parent = parent.parent
    objects.add(rig)
    return {'objects': sorted(objects, key=lambda obj: (obj.type != 'ARMATURE', obj.name)),
            'warnings': list(dict.fromkeys(warnings))}


def _filename(value, rig):
    value = value.strip() or rig.name.removesuffix('Rig') or rig.name
    if value.casefold().endswith('.fbx'):
        value = value[:-4]
    if (not value or value in {'.', '..'} or value[-1] in ' .'
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', value)
            or value.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                             *(f'COM{i}' for i in range(1, 10)),
                                             *(f'LPT{i}' for i in range(1, 10))}):
        raise ExportError('Use a simple export filename, without folders or reserved characters.')
    return value + '.fbx'


def _target(config, rig):
    if not config.directory.strip():
        raise ExportError('Choose a Unity export folder first.')
    directory = Path(bpy.path.abspath(config.directory)).expanduser().resolve()
    if directory.exists() and not directory.is_dir():
        raise ExportError('The export folder points to a file.')
    if directory == directory.parent:
        raise ExportError('Choose a character subfolder, not the drive root.')
    return directory, _filename(config.filename, rig)


def _safe_file(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ExportError('Invalid file path in the export manifest.')
    path = (root / relative).resolve()
    if path == root.resolve() or not path.is_relative_to(root.resolve()) or relative.casefold().endswith('.meta'):
        raise ExportError('Export files must stay in the target folder and must not replace Unity .meta files.')
    return path


def _previous(directory, filename, asset_id):
    manifest = directory / (Path(filename).stem + '.cdesigner.json')
    if not manifest.exists():
        if (directory / filename).exists():
            raise ExportError('That FBX already exists without this tool\'s manifest. Choose another filename.')
        return None
    try:
        record = json.loads(manifest.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise ExportError('The existing character export manifest cannot be read.') from exc
    if not isinstance(record, dict) or record.get('schema') != SCHEMA or record.get('asset_id') != asset_id:
        raise ExportError('This filename belongs to a different export profile. Choose another filename.')
    if not isinstance(record.get('files'), dict):
        raise ExportError('The existing export file inventory is invalid.')
    for relative, expected in record.get('files', {}).items():
        path = _safe_file(directory, relative)
        if path.exists() and _hash(path) != expected:
            raise ExportError(f'{relative} was changed outside the exporter; keep it or choose a new export filename.')
    return record


def _owned_keys(objects):
    from . import forearm_twist
    result = {}
    if forearm_twist._SESSION is not None:
        raise ExportError('Confirm or cancel the current forearm calibration preview before exporting.')
    for obj in objects:
        if obj.get(forearm_twist.PREVIEW_KEY):
            raise ExportError(f'{obj.name}: finish the temporary calibration preview first.')
        if obj.type != 'MESH':
            continue
        records = forearm_twist._records(obj)
        keys = []
        for side, record in records.items():
            key = forearm_twist._managed_key(obj, side, record, repair_name=False)
            if key is not None:
                keys.append(key.name)
        if keys:
            result[obj.name] = keys
    return result


def export_running():
    return _ACTIVE_JOB is not None


def _image_buffers(objects, root):
    # Blend serialization does not retain every unsaved texture-paint buffer.
    # Copy pixel values read-only; the worker restores these on its own images.
    from .unity_export_worker import _material_images
    materials = {slot.material for obj in objects if obj.type == 'MESH'
                 for slot in obj.material_slots if slot.material is not None}
    images = set()
    for material in materials:
        images.update(_material_images(material))
    result = {}
    for image in sorted(images, key=lambda item: item.name):
        if image.source != 'GENERATED' and not (image.source == 'FILE' and image.is_dirty):
            continue
        count = len(image.pixels)
        if not count:
            raise ExportError(f'{image.name}: no pixels available for the export snapshot.')
        pixels = array('f', [0.0]) * count
        image.pixels.foreach_get(pixels)
        path = root / f'image-{len(result)}.float32'
        with path.open('wb') as stream:
            pixels.tofile(stream)
        result[image.name] = {'path': str(path), 'width': image.size[0],
                             'height': image.size[1], 'count': count,
                             'float': image.is_float}
    return result


def begin_export(context, rig, config):
    global _ACTIVE_JOB
    if export_running():
        raise ExportError('A character export is already running.')
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ExportError('Finish Edit Mode before exporting; the current pose is preserved.')
    collected = collect_character(context, rig, config)
    directory, filename = _target(config, rig)
    asset_id = config.asset_id or uuid.uuid4().hex
    prior = _previous(directory, filename, asset_id)
    objects = collected['objects']
    owned_keys = _owned_keys(objects)
    from . import unity_forearm
    forearm = {obj.name: unity_forearm.capture(obj) for obj in objects
               if obj.type == 'MESH' and obj.name in owned_keys}
    temporary = tempfile.TemporaryDirectory(prefix='cdesigner-unity-')
    root = Path(temporary.name)
    stage = root / 'out'
    stage.mkdir()
    manifest = directory / (Path(filename).stem + '.cdesigner.json')
    job = {'temporary': temporary, 'root': root, 'stage': stage,
           'directory': directory, 'filename': filename, 'asset_id': asset_id,
           'prior': prior, 'manifest_hash': _hash(manifest) if manifest.exists() else None,
           'rig': rig, 'config': config, 'started': time.monotonic(), 'process': None,
           'objects': [obj.name for obj in objects]}
    try:
        specification = {
            'objects': job['objects'], 'rig': rig.name,
            'rigs': [obj.name for obj in objects if obj.type == 'ARMATURE'],
            'filename': filename, 'stage': str(stage),
            'unit_scale': context.scene.unit_settings.scale_length,
            'owned_keys': owned_keys, 'warnings': collected['warnings'],
            'forearm': forearm,
            'had_forearm': bool(prior and prior.get('forearm_correction')),
            'simple_materials': sorted({entry.material.name for entry in getattr(config, 'simple_materials', [])
                                        if entry.material is not None and any(
                                            slot.material == entry.material for obj in objects if obj.type == 'MESH'
                                            for slot in obj.material_slots)}),
            'image_buffers': _image_buffers(objects, root),
        }
        snapshot = root / 'character.blend'
        bpy.data.libraries.write(str(snapshot), set(objects), path_remap='ABSOLUTE', fake_user=False, compress=True)
        (root / 'job.json').write_text(json.dumps(specification, ensure_ascii=False), encoding='utf-8')
        log = open(root / 'worker.log', 'w', encoding='utf-8')
        job['log'] = log
        command = [bpy.app.binary_path, '--background', '--factory-startup', '--disable-autoexec',
                   str(snapshot), '--python-exit-code', '1', '--python',
                   str(Path(__file__).with_name('unity_export_worker.py')), '--', str(root / 'job.json')]
        job['process'] = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        config.asset_id = asset_id
        _ACTIVE_JOB = job
        return job
    except Exception:
        if job.get('log'):
            job['log'].close()
        temporary.cleanup()
        raise


def report_messages(report):
    """Separate expected scope skips, including reports from earlier versions."""
    warnings, notices = [], list(report.get('notices', []))
    for message in report.get('warnings', []):
        (notices if ': skipped;' in message else warnings).append(message)
    return list(dict.fromkeys(warnings)), list(dict.fromkeys(notices))


def _publish(job, result):
    directory, stage, filename = job['directory'], job['stage'], job['filename']
    manifest_name = Path(filename).stem + '.cdesigner.json'
    manifest = directory / manifest_name
    # A second export or an artist edit during the worker run must not be lost.
    current_hash = _hash(manifest) if manifest.exists() else None
    if current_hash != job['manifest_hash']:
        raise ExportError('The destination changed during export; retry after reviewing it.')
    prior = _previous(directory, filename, job['asset_id'])
    files = result.get('files', [])
    if filename not in files or not (_safe_file(stage, filename).stat().st_size > 32):
        raise ExportError('The export worker did not produce a valid FBX.')
    if len(files) != len(set(files)):
        raise ExportError('Duplicate output filenames in worker result.')
    for relative in files:
        source, target = _safe_file(stage, relative), _safe_file(directory, relative)
        if not source.is_file():
            raise ExportError(f'Export output is missing: {relative}')
        if target.exists() and (not prior or relative not in prior.get('files', {})):
            raise ExportError(f'{relative} already exists and is not owned by this export.')
    report = {**result, 'schema': SCHEMA, 'asset_id': job['asset_id'],
              'source_rig': job['rig'].name, 'filename': filename,
              'files': {relative: _hash(_safe_file(stage, relative)) for relative in files},
              'unity_status': 'Exported; Unity import has not been verified.',
              'animation_status': 'Rest model only; animation export is not included.',
              'updated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    # Keep stale owned outputs rather than deleting assets whose GUIDs may be referenced.
    stale = set((prior or {}).get('files', {})) - set(files)
    if stale:
        report.setdefault('warnings', []).append('Old files retained for reference safety: ' + ', '.join(sorted(stale)))
        for relative in sorted(stale):
            path = _safe_file(directory, relative)
            if path.exists():
                report['files'][relative] = _hash(path)
    report['warnings'], report['notices'] = report_messages(report)
    (stage / manifest_name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    files = files + [manifest_name]
    directory.mkdir(parents=True, exist_ok=True)
    backup_root = Path(bpy.utils.user_resource('DATAFILES', path='character_designer/export_backups', create=True))
    backup = backup_root / (time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
    backup.mkdir(parents=True)
    existed, changed = {}, []
    try:
        for relative in files:
            target = _safe_file(directory, relative)
            existed[relative] = target.exists()
            if target.exists():
                saved = _safe_file(backup, relative)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
        (backup / 'restore.json').write_text(json.dumps({'target': str(directory), 'existed': existed}, indent=2), encoding='utf-8')
        for relative in files:
            target = _safe_file(directory, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Unity ignores dot-prefixed staging files; .meta files are never touched.
            scratch = target.with_name('.cdesigner-' + uuid.uuid4().hex + '.tmp')
            try:
                shutil.copy2(_safe_file(stage, relative), scratch)
                os.replace(scratch, target)
                changed.append(relative)
            finally:
                if scratch.exists():
                    scratch.unlink()
    except Exception:
        for relative in reversed(changed):
            target = _safe_file(directory, relative)
            if existed[relative]:
                shutil.copy2(_safe_file(backup, relative), target)
            else:
                target.unlink(missing_ok=True)
        raise
    return {'filepath': str(directory / filename), 'report_path': str(manifest),
            'warnings': report['warnings'], 'notices': report['notices'],
            'objects': job['objects'], 'backup': str(backup)}


def _dispose(job):
    global _ACTIVE_JOB
    if job.get('log'):
        job['log'].close()
    job['temporary'].cleanup()
    if _ACTIVE_JOB is job:
        _ACTIVE_JOB = None


def poll_export(job):
    process = job['process']
    if process.poll() is None:
        if time.monotonic() - job['started'] > 600:
            cancel_export(job)
            raise ExportError('Export timed out. No new files were published.')
        return None
    try:
        path = job['stage'] / 'result.json'
        result = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        if process.returncode or not result.get('ok'):
            log = (job['root'] / 'worker.log').read_text(encoding='utf-8', errors='replace')
            raise ExportError(result.get('error') or ('Export worker failed: ' + log[-1800:]))
        return _publish(job, result)
    finally:
        _dispose(job)


def cancel_export(job):
    process = job.get('process')
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    _dispose(job)


def stop_exports():
    if _ACTIVE_JOB is not None:
        cancel_export(_ACTIVE_JOB)


def export_character(context, rig, config):
    """Synchronous API for background validation; the UI polls a modal job."""
    job = begin_export(context, rig, config)
    while True:
        result = poll_export(job)
        if result is not None:
            return result
        time.sleep(.1)
