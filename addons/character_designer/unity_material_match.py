"""File exchange for explicit Unity Material matching; no Blender scene writes.

Unity owns selection, remapping and receipts. A selected recipe alone never
claims that the current model has been matched. All paths stay in the chosen
Unity project, and export manifests remain owned by the existing exporter.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

HEX = re.compile(r'^[0-9a-fA-F]{32}$')
TIMEOUT = 180
MANIFEST_SCHEMA = 'cdesigner.unity-character.v1'
IDENTITY = ('project_root', 'model_path', 'export_asset_id', 'source_material')


def _same_path(a, b):
    if not a or not b:
        return False
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def project_for(directory):
    directory = Path(directory).expanduser().resolve()
    for root in (directory, *directory.parents):
        if (root / 'Assets').is_dir() and (root / 'ProjectSettings').is_dir():
            if not directory.is_relative_to((root / 'Assets').resolve()):
                raise ValueError('Choose an export folder inside this Unity project Assets folder.')
            return root
    raise ValueError('The export folder is not inside a Unity project. Export to Assets first.')


def _inside(root, path):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('The material exchange path points outside the Unity project.')
    return path


def _read(path):
    stat = path.stat()
    if stat.st_size > 1024 * 1024:
        raise ValueError('The material exchange file is too large.')
    return _read_version(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=32)
def _read_version(path, modified, size):
    path = Path(path)
    record = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(record, dict):
        raise ValueError('The material exchange record is invalid.')
    return record


def context_for(directory, filename, asset_id, source_material):
    root = project_for(directory)
    if not HEX.fullmatch(asset_id or '') or not source_material or '\x00' in source_material:
        raise ValueError('Export this character once before matching its material.')
    model = _inside(root / 'Assets', Path(directory) / filename)
    if (Path(filename).name != filename or not filename.lower().endswith('.fbx')
            or not model.is_file()):
        raise ValueError('Export this character FBX before matching its material.')
    report = _read(model.with_suffix('.cdesigner.json'))
    if (report.get('schema') != MANIFEST_SCHEMA or report.get('asset_id') != asset_id
            or report.get('filename') != filename
            or source_material not in report.get('materials', [])):
        raise ValueError('Re-export this character before matching the reported source material.')
    return {'project_root': root.as_posix(), 'model_path': model.relative_to(root).as_posix(),
            'export_asset_id': asset_id, 'source_material': source_material}


def _exchange(identity):
    root = Path(identity['project_root']).resolve()
    return _inside(root, root / '.codex' / 'character-material-matches')


def _request_path(request, suffix):
    if not HEX.fullmatch(request.get('request_id', '')):
        raise ValueError('Invalid material matching request identity.')
    exchange = _exchange(request)
    return _inside(Path(request['project_root']), exchange / 'requests' / (request['request_id'] + suffix))


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def create_request(directory, filename, asset_id, source_material):
    request = {**context_for(directory, filename, asset_id, source_material),
               'schema': 'cdesigner.material-match-request/1',
               'request_id': uuid.uuid4().hex, 'created_unix': int(time.time())}
    _atomic(_request_path(request, '.json'), request)
    return request


def cancel_request(request):
    path = _request_path(request, '.cancel')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def _identity_matches(record, identity):
    return (all(record.get(key) == identity.get(key) for key in IDENTITY if key != 'project_root')
            and isinstance(record.get('project_root'), str)
            and _same_path(record['project_root'], identity['project_root']))


def _material(record, root):
    guid, local_id = record.get('material_guid'), record.get('material_local_id')
    if (not isinstance(guid, str) or not HEX.fullmatch(guid)
            or type(local_id) is not int or local_id <= 0):
        raise ValueError('Unity returned an invalid Material identity.')
    relative = record.get('material_path', '')
    if (not isinstance(relative, str) or not relative.startswith('Assets/')
            or not relative.lower().endswith('.mat')):
        raise ValueError('Choose a saved Unity Material asset inside Assets.')
    material = _inside(root / 'Assets', root / relative)
    meta = material.with_name(material.name + '.meta')
    if not material.is_file() or not meta.is_file():
        raise ValueError('The selected Unity Material is missing; match it again.')
    stat = meta.stat()
    if _meta_guid(str(meta), stat.st_mtime_ns, stat.st_size) != guid.lower():
        raise ValueError('The saved Unity Material reference changed; match it again.')


@lru_cache(maxsize=32)
def _meta_guid(path, modified, size):
    match = re.search(r'^guid: ([0-9a-fA-F]{32})\s*$', Path(path).read_text(encoding='utf-8-sig'), re.MULTILINE)
    return match.group(1).lower() if match else None


@lru_cache(maxsize=16)
def _file_hash(path, modified, size):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def model_hash(identity):
    root = Path(identity['project_root'])
    model = _inside(root / 'Assets', root / identity['model_path'])
    stat = model.stat()
    return _file_hash(str(model), stat.st_mtime_ns, stat.st_size)


def manifest_hash(identity):
    root = Path(identity['project_root'])
    report = _inside(root / 'Assets', root / identity['model_path']).with_suffix('.cdesigner.json')
    stat = report.stat()
    return _file_hash(str(report), stat.st_mtime_ns, stat.st_size)


def read_result(request):
    if _request_path(request, '.cancel').exists():
        return {**request, 'status': 'cancelled'}
    path = _request_path(request, '.result.json')
    if not path.is_file():
        return None
    record = _read(path)
    if (record.get('schema') != 'cdesigner.material-match-result/1'
            or record.get('request_id') != request['request_id']
            or not _identity_matches(record, request)
            or record.get('status') not in {'matched', 'cancelled', 'failed'}):
        raise ValueError('Unity returned a result for a different material request.')
    if record['status'] == 'matched':
        _material(record, Path(request['project_root']))
        if not isinstance(record.get('material_name'), str) or not record['material_name'].strip():
            raise ValueError('Unity returned an incomplete Material result.')
        if record.get('fbx_sha256') != model_hash(request):
            raise ValueError('The model changed while matching; check the new export in Unity.')
        if record.get('manifest_sha256') != manifest_hash(request):
            raise ValueError('The export choices changed while matching; check the current export in Unity.')
    return record


def receipt_path(identity):
    if not HEX.fullmatch(identity.get('export_asset_id', '')):
        raise ValueError('Invalid material matching export profile.')
    name = hashlib.sha256(identity['source_material'].encode('utf-8')).hexdigest() + '.json'
    return _inside(Path(identity['project_root']),
                   _exchange(identity) / 'receipts' / identity['export_asset_id'] / name)


def binding_status(directory, filename, asset_id, source_material, binding):
    try:
        identity = context_for(directory, filename, asset_id, source_material)
        if (binding.get('export_asset_id') != identity['export_asset_id']
                or binding.get('source_material') != identity['source_material']
                or not _same_path(binding.get('project_root', ''), identity['project_root'])):
            return 'Review Match', 'The export project changed.'
        _material(binding, Path(identity['project_root']))
        receipt = _read(receipt_path(identity))
        if (receipt.get('schema') != 'cdesigner.material-match-receipt/1'
                or not _identity_matches(receipt, identity)
                or receipt.get('material_guid') != binding.get('material_guid')
                or receipt.get('material_local_id') != binding.get('material_local_id')):
            return 'Awaiting Unity', 'Unity has not confirmed this pairing.'
        if receipt.get('status') != 'matched':
            return 'Review Match', str(receipt.get('error') or 'Unity needs to check this pairing.')
        _material(receipt, Path(identity['project_root']))
        if receipt.get('fbx_sha256') != model_hash(identity):
            return 'Awaiting Unity', 'Unity has not confirmed the current export.'
        if receipt.get('manifest_sha256') != manifest_hash(identity):
            return 'Awaiting Unity', 'Unity has not confirmed the current export choices.'
        return 'Matched', str(receipt.get('material_name') or binding.get('material_name') or source_material)
    except (OSError, ValueError, TypeError, KeyError):
        return 'Review Match', 'Export or match again to check this reference.'


def export_bindings(entries, directory, asset_id, used_names, simple_names):
    """Validate recipes before an export snapshot; Unity verifies actual binding."""
    result, seen = [], set()
    for entry in entries:
        name = entry['source_material']
        if name not in used_names:
            continue
        if name in simple_names:
            continue  # Explicit simplified exports temporarily suspend this recipe.
        if name in seen:
            raise ValueError(name + ': duplicate saved Unity Material matches.')
        root = project_for(directory)
        if (not _same_path(entry.get('project_root', ''), root)
                or entry.get('export_asset_id') != asset_id):
            raise ValueError(name + ': match the material in this export project first.')
        _material(entry, root)
        result.append({key: entry[key] for key in
                       ('source_material', 'material_guid', 'material_local_id', 'project_root', 'export_asset_id')})
        seen.add(name)
    return result
