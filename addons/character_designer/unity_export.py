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
_DRESS_RECORD_KEY = 'character_designer_skirt_v1'
_DRESS_OWNER_KEY = 'character_designer_skirt_owner'
_DRESS_SOURCE_KEY = 'character_designer_skirt_source'
_DRESS_RIG_KEY = 'character_designer_skirt_armature'
_DRESS_ROLE_KEY = 'character_designer_skirt_surface_role'
_DRESS_BACKEND = 'ACTUAL_SURFACE_DELTA_V1'
_DRESS_ROLES = {'CLOTH_PROXY', 'BODY_ATTACHMENT', 'NEUTRAL_RIG',
                'NEUTRAL_WIRE', 'NEUTRAL_SURFACE', 'TRACKER'}


class ExportError(ValueError):
    pass


def _dress_record(obj):
    """Inspect saved metadata only; collect/draw never load surface services."""
    if obj.type != 'MESH' or _DRESS_RECORD_KEY not in obj:
        return None
    try:
        raw = obj[_DRESS_RECORD_KEY]
        if type(raw) is not str:
            raise ValueError()
        record = json.loads(raw)
        if type(record) is not dict:
            raise ValueError()
        physics = record.get('physics')
        if physics is None:
            return None
        if type(physics) is not dict:
            raise ValueError()
        backend = physics.get('backend', 'LEGACY_CAGE')
        if backend == 'DIRECT_MAIN_CLOTH_V1':
            raise ExportError(f'{obj.name}: Direct Dress export validation is pending. Keep the editable source; baked vertex physics cannot be returned as a bone-only animation.')
        if backend == 'LEGACY_CAGE':
            if 'surface' in physics:
                raise ValueError()
            return None
        if type(backend) is not str or backend != _DRESS_BACKEND:
            raise ValueError()
        surface = physics.get('surface')
        if (type(record.get('version')) is not int or record['version'] != 1 or type(record.get('owner')) is not str
                or not record['owner'] or type(surface) is not dict
                or type(surface.get('version')) is not int or surface['version'] != 1
                or type(surface.get('roles')) is not dict):
            raise ValueError()
        inventory = []
        for role, names in surface['roles'].items():
            if (role not in _DRESS_ROLES or type(names) is not list
                    or any(type(name) is not str or not name for name in names)):
                raise ValueError()
            inventory.extend(names)
        if len(inventory) != len(set(inventory)):
            raise ValueError()
        colliders = physics.get('colliders')
        if (type(colliders) is not list or any(type(name) is not str or not name for name in colliders)
                or len(colliders) != len(set(colliders))):
            raise ValueError()
        return record
    except ExportError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise ExportError(f'{obj.name}: saved Dress surface metadata is invalid; restore its owned setup before export.') from exc


def _dress_surface_helper(obj, *, records=None):
    marked = _DRESS_ROLE_KEY in obj
    role = obj.get(_DRESS_ROLE_KEY)
    source = obj.get(_DRESS_SOURCE_KEY)
    if not marked and (source is None or source is obj or source.type != 'MESH'):
        return False
    if (marked and (type(role) is not str or role not in _DRESS_ROLES)
            or source is None or source is obj or source.type != 'MESH'):
        raise ExportError(f'{obj.name}: the Dress helper role or source is invalid.')
    if records is not None and source in records:
        record = records[source]
    else:
        record = _dress_record(source)
        if records is not None:
            records[source] = record
    if not marked and (record is None or obj.name not in record['physics']['colliders']):
        return False
    if (record is None or obj.get(_DRESS_OWNER_KEY) != record['owner']
            or source.get(_DRESS_OWNER_KEY) != record['owner']
            or marked and obj.name not in record['physics']['surface']['roles'].get(role, ())):
        raise ExportError(f'{obj.name}: the Dress helper identity is outside its saved owner inventory.')
    return True


def _capture_dress_surfaces(objects):
    if any(_dress_surface_helper(obj) for obj in objects):
        raise ExportError('A Dress surface helper is inside the explicit export inventory.')
    sources = [(obj, record) for obj in objects
               if (record := _dress_record(obj)) is not None]
    if not sources:
        return []
    try:
        from . import skirt_surface
    except ImportError as exc:
        raise ExportError('Restore the validated Dress surface export service before exporting.') from exc
    if getattr(skirt_surface, 'BACKEND', None) != _DRESS_BACKEND:
        raise ExportError('The installed Dress surface export service has a different backend contract.')
    result = []
    for source, record in sources:
        rig = source.get(_DRESS_RIG_KEY)
        if rig not in objects or rig.type != 'ARMATURE' or _dress_surface_helper(rig):
            raise ExportError(f'{source.name}: its original Dress skinning rig is outside the export inventory.')
        try:
            proof = json.loads(json.dumps(skirt_surface.export_capture(source), allow_nan=False))
            if (type(proof) is not dict or type(proof.get('version')) is not int or proof['version'] != 1
                    or proof.get('backend') != _DRESS_BACKEND or proof.get('source') != source.name
                    or proof.get('owner') != record['owner'] or proof.get('rig') != rig.name):
                raise ValueError('The surface capture does not match the selected source and rig.')
        except (ValueError, TypeError) as exc:
            raise ExportError(f'{source.name}: Dress surface export proof is invalid: {exc}') from exc
        result.append(proof)
    return result


def _dress_publication(job, result):
    captured = job.get('dress_surfaces', [])
    actual = result.get('dress_surfaces', [])
    expected = [{'source': proof['source'], 'owner': proof['owner'], 'backend': _DRESS_BACKEND,
                 'static_source_rest': True, 'simulation_baked': False} for proof in captured]
    if (type(actual) is not list or actual != expected
            or any(type(item) is not dict or item.get('static_source_rest') is not True
                   or item.get('simulation_baked') is not False for item in actual)):
        raise ExportError('The worker did not preserve the captured Dress static export boundary; no files were published.')


def _model_snapshot_datablocks(objects, dress_surfaces):
    """Retain native Dress membership proof without expanding FBX selection."""
    roots = set(objects)
    if not dress_surfaces:
        return roots
    try:
        from .dress_export_snapshot import snapshot_scene_roots
        roots.update(snapshot_scene_roots(dress_surfaces))
    except (ImportError, ValueError, RuntimeError, ReferenceError) as exc:
        raise ExportError('The Dress snapshot cannot preserve its native Scene proof: ' + str(exc)) from exc
    return roots


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
    if _dress_surface_helper(rig):
        raise ExportError('A Dress surface helper cannot be the exported Main Rig.')
    return {rig} | {obj for obj in scene.objects
                    if obj.type == 'ARMATURE' and _descends(obj.parent, rig)
                    and not _dress_surface_helper(obj)}


def _helpers(scene, *, candidates=None):
    # Every scene rig can reference a character mesh as a custom shape. Read
    # those pointers afresh, but inspect object metadata only where it matters.
    candidates = None if candidates is None else set(candidates)
    if candidates is not None and not candidates:
        return set()
    shapes = set()
    for obj in scene.objects:
        if obj.type == 'ARMATURE':
            for pb in obj.pose.bones:
                shape = pb.custom_shape
                if shape is not None and (candidates is None or shape in candidates):
                    shapes.add(shape)
    dress_records = {}
    for obj in scene.objects if candidates is None else candidates:
        # Ownership roles, not names or visibility: hidden clothes still export.
        if _dress_surface_helper(obj, records=dress_records):
            shapes.add(obj)
        roles = [str(obj.get(key, '')).upper() for key in obj.keys()
                 if str(key).startswith('character_designer') and str(key).endswith('role')]
        if any(any(token in role for token in ('WIDGET', 'COLLIDER', 'PROXY', 'GUIDE')) for role in roles):
            shapes.add(obj)
        if any(c.get('character_designer_widget_container_role') for c in obj.users_collection):
            shapes.add(obj)
    return shapes


def _bound_meshes(scene, rigs, helpers, *, _bindings=None):
    """Discover bindings using this operation's freshly inspected scene scope."""
    meshes = []
    for obj in scene.objects if _bindings is None else _bindings:
        if obj.type != 'MESH' or obj in helpers:
            continue
        bound = _binding_armatures(obj) if _bindings is None else _bindings[obj]
        if bound & rigs:
            if bound - rigs:
                raise ExportError(f'{obj.name}: linked to more than this character rig.')
            meshes.append(obj)
    return sorted(meshes, key=lambda obj: obj.name)


def _collection_scope(context, rig, *, references=()):
    """One synchronous inspection; never retained between redraws or exports."""
    scene = context.scene
    rigs = _character_armatures(scene, rig)
    bindings = {obj: _binding_armatures(obj) for obj in scene.objects if obj.type == 'MESH'}
    candidates = {obj for obj, bound in bindings.items() if bound & rigs}
    candidates.update(obj for obj in references if obj is not None and obj.name in scene.objects)
    helpers = _helpers(scene, candidates=candidates)
    return {'rigs': rigs, 'helpers': helpers,
            'bindings': bindings,
            'eligible': _bound_meshes(scene, rigs, helpers, _bindings=bindings)}


def _scope_references(config, setup):
    """Saved references still need fresh helper checks, including unbound ones."""
    return [entry.object for entry in config.extras] + (
        [item.object for item in setup.assets] if setup else [])


def bound_meshes(context, rig):
    """Return meshes with a working Armature binding; saved hints cannot add any."""
    return _collection_scope(context, rig)['eligible']


def collect_character(context, rig, config, *, _scope=None):
    # The panel shares only its current draw's inspection. Operators/export
    # callers always inspect afresh; no scene-change cache authorizes export.
    scene = context.scene
    setup = character_setup.settings(context)
    scope = _collection_scope(context, rig, references=_scope_references(config, setup)) if _scope is None else _scope
    rigs, helpers = scope['rigs'], scope['helpers']
    eligible = set(scope['eligible'])
    objects = set(eligible)
    warnings = []
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
        if scope['bindings'][obj] - rigs:
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
        _dress_record(obj)
        if not obj.data.vertices:
            raise ExportError(f'{obj.name}: mesh has no vertices.')
        objects.update(scope['bindings'][obj])
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


def _capture_hair_motion(objects):
    """Read the selected source's current native proof before snapshotting."""
    from . import hair_bones_rig, hair_motion_profiles, hair_strand_registry
    sources = [obj for obj in objects if obj.type == 'MESH' and hair_motion_profiles.PROFILE_KEY in obj]
    if len(sources) > 1:
        raise ExportError('The first Hair motion export supports one configured Hair source per character.')
    if not sources:
        return None
    source = sources[0]
    armature = source.get(hair_bones_rig.RIG_KEY)
    if armature not in objects or armature.type != 'ARMATURE':
        raise ExportError(f'{source.name}: its authoritative Hair armature is outside the collected export.')
    try:
        registry = hair_strand_registry.read(source, validate=True)
        profiles = hair_motion_profiles.effective_all(source, registry=registry)
        # Validate the detached semantic contract now, before starting any worker.
        # These identity bindings are proof-only, not a guessed final FBX mapping.
        from . import hair_motion_export
        hair_motion_export.build_payload(source, registry, profiles,
            {name: name for strand in registry['strands'] for name in strand['bones']},
            'preflight-only', '0' * 64,
            {'source_meters_per_unit': 1.0, 'export_meters_per_unit': 1.0})
    except ValueError as exc:
        raise ExportError(f'{source.name}: Hair motion proof/settings are invalid: {exc}') from exc
    from . import bl_info
    return {'source': source.name, 'rig': armature.name, 'registry': registry, 'profiles': profiles,
            'tool_version': '.'.join(str(part) for part in bl_info['version'])}


def begin_export(context, rig, config):
    global _ACTIVE_JOB
    if export_running():
        raise ExportError('A character export is already running.')
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ExportError('Finish Edit Mode before exporting; the current pose is preserved.')
    # Direct refusal must precede even transient author preview restoration.
    from . import dress_export_guard
    try:
        dress_export_guard.reject_model_sources(collect_character(context, rig, config)['objects'])
    except (ValueError, TypeError) as error:
        raise ExportError(str(error)) from error
    from . import hair_wiggle_adapter
    if hair_wiggle_adapter.status(context)['active']:
        try:
            hair_wiggle_adapter.stop_preview(context, reason='Stopped before the model export snapshot.')
        except ValueError as exc:
            raise ExportError('Hair preview could not restore its author state before export: ' + str(exc)) from exc
        if hair_wiggle_adapter.status(context)['active']:
            raise ExportError('Finish the active Hair preview before model export.')
    collected = collect_character(context, rig, config)
    directory, filename = _target(config, rig)
    asset_id = config.asset_id or uuid.uuid4().hex
    prior = _previous(directory, filename, asset_id)
    objects = collected['objects']
    dress_surfaces = _capture_dress_surfaces(objects)
    hair_motion = _capture_hair_motion(objects)
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
           'objects': [obj.name for obj in objects], 'hair_motion': hair_motion,
           'dress_surfaces': dress_surfaces}
    try:
        specification = {
            'objects': job['objects'], 'rig': rig.name,
            'rigs': [obj.name for obj in objects if obj.type == 'ARMATURE'],
            'filename': filename, 'stage': str(stage), 'asset_id': asset_id,
            'unit_scale': context.scene.unit_settings.scale_length,
            'owned_keys': owned_keys, 'warnings': collected['warnings'],
            'forearm': forearm,
            'hair_motion': hair_motion,
            'dress_surfaces': dress_surfaces,
            'had_forearm': bool(prior and prior.get('forearm_correction')),
            'simple_materials': sorted({entry.material.name for entry in getattr(config, 'simple_materials', [])
                                        if entry.material is not None and any(
                                            slot.material == entry.material for obj in objects if obj.type == 'MESH'
                                            for slot in obj.material_slots)}),
            'image_buffers': _image_buffers(objects, root),
        }
        snapshot = root / 'character.blend'
        bpy.data.libraries.write(str(snapshot), _model_snapshot_datablocks(objects, dress_surfaces),
                                 path_remap='ABSOLUTE', fake_user=False, compress=True)
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
    _dress_publication(job, result)
    hair_motion = result.get('hair_motion', {'active': False, 'file': None,
        'simulation_baked': False, 'status': 'No configured Hair motion source in this export.'})
    if (not isinstance(hair_motion, dict) or type(hair_motion.get('active')) is not bool
            or hair_motion.get('simulation_baked') is not False):
        raise ExportError('The worker Hair motion publication state is invalid.')
    if hair_motion['active']:
        from . import hair_motion_export
        relative = Path(filename).stem + '.hair-motion.json'
        if hair_motion.get('file') != relative or relative not in files or not job.get('hair_motion'):
            raise ExportError('The worker did not retain the captured Hair motion sidecar binding.')
        try:
            payload = hair_motion_export.verify_read(_safe_file(stage, relative), fbx_path=_safe_file(stage, filename))
        except ValueError as exc:
            raise ExportError('The Hair motion sidecar failed publication validation: ' + str(exc)) from exc
        if (payload['asset_id'] != job['asset_id']
                or payload['source_uid'] != job['hair_motion']['registry']['source_uid']):
            raise ExportError('The Hair motion sidecar belongs to another source or export profile.')
    elif job.get('hair_motion'):
        raise ExportError('Captured Hair motion settings were omitted by the worker; no files were published.')
    elif hair_motion.get('file') is not None or any(relative.endswith('.hair-motion.json') for relative in files):
        raise ExportError('An inactive Hair sidecar cannot be emitted as a new model export output.')
    for relative in files:
        source, target = _safe_file(stage, relative), _safe_file(directory, relative)
        if not source.is_file():
            raise ExportError(f'Export output is missing: {relative}')
        if target.exists() and (not prior or relative not in prior.get('files', {})):
            raise ExportError(f'{relative} already exists and is not owned by this export.')
    report = {**result, 'schema': SCHEMA, 'asset_id': job['asset_id'], 'hair_motion': hair_motion,
              'source_rig': job['rig'].name, 'filename': filename,
              'files': {relative: _hash(_safe_file(stage, relative)) for relative in files},
              'unity_status': 'Exported; Unity import has not been verified.',
              'animation_status': 'Rest model only; animation export is not included.',
              'updated_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    if report.get('forearm_correction', {}).get('meshes'):
        prefab = Path(filename).stem + '.Runtime.prefab'
        report['unity_runtime_usage'] = (
            f'Use {prefab} after the companion Unity importer completes; '
            'the runtime prefab is generated in Unity, not by this export.')
    # Keep stale owned outputs rather than deleting assets whose GUIDs may be referenced.
    stale = set((prior or {}).get('files', {})) - set(files)
    if stale:
        report.setdefault('warnings', []).append('Old files retained for reference safety: ' + ', '.join(sorted(stale)))
        for relative in sorted(stale):
            path = _safe_file(directory, relative)
            if path.exists():
                report['files'][relative] = _hash(path)
    inactive_hair = sorted(relative for relative in stale if relative.endswith('.hair-motion.json')
                           and _safe_file(directory, relative).is_file())
    if inactive_hair:
        report['hair_motion'] = {**hair_motion, 'inactive_files': inactive_hair,
            'status': ('Older inactive Hair sidecars are retained; apply only the current advertised file.'
                       if hair_motion['active'] else
                       'Hair motion is disabled in this export; retained older sidecars must not be applied.')}
        report.setdefault('warnings', []).append(
            'Inactive Hair motion sidecars retained for reference safety: ' + ', '.join(inactive_hair)
            + '. Apply only a sidecar advertised as active by the current character manifest.')
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
