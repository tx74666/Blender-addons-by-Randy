"""Explicit, hash-verified relocation of ordinary Worklist dependencies.

Packages contain linked Source libraries and the adopted Main Rig's recovery
packet. They do not save a .blend, copy Unity publication routes, reload a
library, or relax any Source, Rest, Action or recovery ownership check.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from types import SimpleNamespace


SCHEMA = 'character-designer.worklist-dependencies/1'
MANIFEST = 'character_designer_dependencies.json'
LOCATIONS = 'character_designer_worklist_dependency_locations_v1'
LOCATION_SCHEMA = 'character-designer.worklist-dependency-locations/1'
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _sha(value):
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise ValueError('A dependency has no exact saved SHA256; nothing was relocated.')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _copy_verified(source, destination, expected):
    """Publish a verified new file; never overwrite even an invalid old cache."""
    expected = _sha(expected)
    source, destination = Path(source), Path(destination)
    if _hash(source) != expected:
        raise ValueError('A dependency changed before copying: ' + str(source))
    if destination.exists():
        if not destination.is_file() or _hash(destination) != expected:
            raise ValueError('An existing dependency cache differs; it was kept: ' + str(destination))
        return str(destination.resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.copy-', dir=destination.parent)
    try:
        with os.fdopen(descriptor, 'wb') as output, source.open('rb') as incoming:
            shutil.copyfileobj(incoming, output, 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if _hash(temporary) != expected or _hash(source) != expected:
            raise ValueError('A dependency changed while copying; no copy was published.')
        try:
            # A hard link publishes this complete temporary atomically and fails
            # if another writer already owns the destination. Both are local.
            os.link(temporary, destination)
        except FileExistsError:
            if not destination.is_file() or _hash(destination) != expected:
                raise ValueError('Another writer created a different dependency cache; it was kept.')
        if _hash(destination) != expected:
            raise ValueError('The published dependency did not retain its saved hash.')
        return str(destination.resolve())
    finally:
        Path(temporary).unlink(missing_ok=True)


def pin_recovery_packet(packet, directory=None):
    """Freeze the exact admitted packet for a new Main Rig session.

    Existing sessions keep their original strict path/hash check. Only an
    explicit package relocation can change their dependency location.
    """
    if directory is None:
        import bpy
        directory = bpy.utils.user_resource('DATAFILES',
            path='character_designer/animation_recovery_packets', create=True)
    digest = _sha(packet['_sha256'])
    return _copy_verified(packet['_path'], Path(directory) / (digest + '.cdanim.json'), digest)


def _rows(saved):
    return [(kind, row) for kind, values in
            (('active', saved.items), ('retired', getattr(saved, 'retired_items', ())))
            for row in values]


def _locations(rig):
    raw = rig.get(LOCATIONS) if rig is not None else None
    value = json.loads(raw) if raw is not None else dict(schema=LOCATION_SCHEMA, sources=[], packets=[])
    if (not isinstance(value, dict) or set(value) != {'schema', 'sources', 'packets'}
            or value['schema'] != LOCATION_SCHEMA):
        raise ValueError('The dependency location record is invalid; saved identities were kept.')
    for kind in ('sources', 'packets'):
        if not isinstance(value[kind], list):
            raise ValueError('The dependency locations are incomplete.')
        keys = set()
        for entry in value[kind]:
            if not isinstance(entry, dict) or set(entry) != {'logical', 'sha256', 'path', 'absolute_path'}:
                raise ValueError('A dependency location is incomplete.')
            _sha(entry['sha256'])
            if any(type(entry[key]) is not str or not entry[key]
                   for key in ('logical', 'path', 'absolute_path')):
                raise ValueError('A dependency location has no exact path.')
            key = (entry['logical'], entry['sha256'])
            if key in keys:
                raise ValueError('A dependency has several physical locations; nothing was guessed.')
            keys.add(key)
    return value


def _resolve(rig, kind, logical, digest):
    import bpy
    for entry in _locations(rig)[kind]:
        if entry['logical'] == logical and entry['sha256'] == digest:
            # Native Save As remaps Library.filepath but cannot remap opaque
            # JSON strings. A moved directory uses the new relative location;
            # Save As can retain the exact original physical copy. Routine
            # reads still perform their original full hash check once.
            if (entry['path'].startswith('//')
                    and not Path(bpy.path.abspath(entry['path'])).is_file()
                    and Path(entry['absolute_path']).is_file()
                    and _hash(entry['absolute_path']) == digest):
                return entry['absolute_path']
            return entry['path']
    return logical


def source_path(row):
    """Resolve only an explicit same-hash locator, preserving the logical row."""
    import bpy
    rig = getattr(row, 'rig', None)
    resolved = bpy.path.abspath(_resolve(rig, 'sources', row.source_file, row.source_hash))
    if any(
            entry['logical'] == row.source_file and entry['sha256'] == row.source_hash
            for entry in _locations(rig)['sources']):
        action = row.source_action
        if action is not None and action.library is not None:
            native = bpy.path.abspath(action.library.filepath)
            if (Path(native).resolve() != Path(resolved).resolve()
                    and Path(native).is_file() and _hash(native) == row.source_hash):
                return native
    return resolved


def packet_path(rig, record):
    import bpy
    resolved = bpy.path.abspath(_resolve(rig, 'packets', record['packet_path'], record['packet_sha256']))
    if Path(resolved).is_file():
        return resolved
    locations = _locations(rig)
    packet = next((entry for entry in locations['packets'] if
        entry['logical'] == record['packet_path'] and entry['sha256'] == record['packet_sha256']), None)
    if packet is not None:
        # After a directory move followed by Save As, even the old absolute
        # fallback can be gone. An already loaded, same-hash native Source is
        # an exact package anchor; names/counts never establish this identity.
        for scene in bpy.data.scenes:
            saved = getattr(scene, 'character_designer_animation_worklist', None)
            if saved is None or saved.rig is not rig:
                continue
            for _kind, row in _rows(saved):
                source = next((entry for entry in locations['sources'] if
                    entry['logical'] == row.source_file and entry['sha256'] == row.source_hash), None)
                if source is None or row.source_action is None or row.source_action.library is None:
                    continue
                actual = Path(bpy.path.abspath(row.source_action.library.filepath))
                if not actual.is_file() or _hash(actual) != row.source_hash:
                    continue
                relative = os.path.relpath(packet['absolute_path'], Path(source['absolute_path']).parent)
                candidate = (actual.parent / relative).resolve()
                if candidate.is_file() and _hash(candidate) == record['packet_sha256']:
                    return str(candidate)
    return resolved


def _historical_rows(context, rig, rows, target):
    """Read exact checkpoint-owned rows without changing historical permissions.

    Full current-rig native admission follows relocation. This read deliberately
    does not require the old physical path, so a moved package can be located.
    """
    from . import animation_worklist_revision as revision
    result, checkpoints = [], set()
    for _kind, row in rows:
        if not getattr(row, 'current_rig_revision', ''):
            continue
        custom = row.custom_action
        token = row.current_rig_revision
        checkpoint = custom.get(revision.CHECKPOINT) if custom is not None else None
        if (custom is None or custom.library is not None or custom.get(revision.TOKEN) != token
                or checkpoint is None or checkpoint.library is not None or not checkpoint.use_fake_user):
            raise ValueError('A current-rig checkpoint lost its exact local Action owner.')
        pointer = checkpoint.as_pointer() if hasattr(checkpoint, 'as_pointer') else id(checkpoint)
        if pointer in checkpoints:
            continue
        checkpoints.add(pointer)
        envelope = json.loads(checkpoint[revision.RECORD])
        if (set(envelope) != {'payload', 'sha256'}
                or envelope['sha256'] != revision._digest(envelope['payload'])):
            raise ValueError('A current-rig checkpoint changed; its paths were kept.')
        references, history = dict(checkpoint[revision.REFS]), dict(rig[target.REFS])
        value = revision._decode(envelope['payload'], references, history, rig,
                                 custom=custom, token=token)
        if (value['schema'] != revision.SCHEMA or value['rig'] is not rig
                or value['scene'] is not context.scene
                or value['view_layer'] != context.view_layer.name
                or rig[target.SESSION] != value['history_raw']
                or set(history) != set(value['history_refs'])
                or any(history[key] is not owner for key, owner in value['history_refs'].items())):
            raise ValueError('The exact current-rig history changed; no dependency was reassigned.')
        result.extend(('history:' + value['revision_id'], SimpleNamespace(**values))
                      for values in value['old_rows'])
    return result


def _admit(context):
    import bpy
    from . import animation_worklist as worklist, animation_worklist_target as target
    worklist._idle(context)
    saved = worklist.state(context)
    rows = _rows(saved)
    rig = saved.rig
    if rig is None or any(row.rig is not rig for _kind, row in rows):
        raise ValueError('Keep one exact worklist rig before packaging its dependencies.')
    rows += _historical_rows(context, rig, rows, target)
    if len({(kind, row.item_id) for kind, row in rows}) != len(rows):
        raise ValueError('Duplicate work-item identities cannot be packaged or relocated.')
    if 'character_designer_rigging_session_v1' in rig:
        raise ValueError('Finish Rigging Inputs before packaging ordinary animation dependencies.')
    for _kind, row in rows:
        custom = row.custom_action
        if (getattr(row, 'composition_json', '') or getattr(row, 'composition_sha256', '')
                or getattr(row, 'application_role', '')
                or custom is not None and any(key in custom for key in (
                    'character_designer_rigging_capture_v1', 'character_designer_rigging_input_action_v1'))):
            raise ValueError('Composition and captured Rigging work need their own '
                             'dependency workflow; no ordinary package was created or rebound.')
        if getattr(row, 'workspace_path', ''):
            raise ValueError('Attached Rigging work keeps external authenticated inputs; '
                             'ordinary dependency packaging does not relocate that work.')
    record = target._load(rig) if target.is_adopted(rig) else None
    return bpy, worklist, target, saved, rows, record


def _source(row, worklist, bpy, *, files, historical=False):
    if historical and row.source_action is None and not row.source_file and not row.source_hash:
        # Old local-only rows can legitimately have no Source. Keep that exact
        # absence; it grants neither baseline trust nor publication authority.
        return None
    if getattr(row, 'new_animation', False):
        worklist._check_baseline(row)
        return None
    action = row.source_action
    if (action is None or action.library is None or getattr(action, 'is_missing', False)
            or getattr(action.library, 'is_missing', False)):
        raise ValueError('The loaded read-only Source is missing; no library was reloaded or made editable.')
    library = action.library
    if Path(bpy.path.abspath(library.filepath)).resolve() != Path(source_path(row)).resolve():
        raise ValueError('A Source library lost its recorded path; no unrelated library was redirected.')
    _sha(row.source_hash)
    worklist._slot(action, row.source_slot)
    if files:
        worklist._check_baseline(row)
    return library


def _row_identity(kind, row):
    return dict(kind=kind, item_id=row.item_id,
                work_item_id=getattr(row, 'work_item_id', ''), clip_key=row.clip_key,
                new_animation=bool(getattr(row, 'new_animation', False)),
                source_sha256=row.source_hash,
                logical_path=row.source_file,
                source_slot=row.source_slot,
                source_action=row.source_action.name if row.source_action is not None else '')


def _source_content(row):
    from .animation_worklist_fingerprint import fingerprint_action
    proof = fingerprint_action(row.source_action, row.source_slot)
    if not proof.get('fingerprint_known') or not proof.get('action_fingerprint'):
        raise ValueError('This Source needs a complete loaded-curve proof before dependency relocation: '
                         + proof.get('proof_reason', 'unknown'))
    return _sha(proof['action_fingerprint'])


def _session_fingerprint(raw):
    value = json.loads(raw)
    value.pop('packet_path')
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def package_dependencies(context, directory):
    """Create one new dependency directory; return its manifest and counts."""
    bpy, worklist, target, saved, rows, record = _admit(context)
    _validate_current(context, rows)
    sources = []
    for kind, row in rows:
        library = _source(row, worklist, bpy, files=True, historical=kind.startswith('history:'))
        entry = _row_identity(kind, row)
        entry['path'] = 'sources/' + row.source_hash + '.blend' if library is not None else ''
        entry['source_content_sha256'] = _source_content(row) if library is not None else ''
        sources.append(entry)
    recovery = None
    if record is not None:
        digest = _sha(record['packet_sha256'])
        if _hash(packet_path(saved.rig, record)) != digest:
            raise ValueError('The saved recovery packet changed; no replacement was invented.')
        recovery = dict(sha256=digest, path='packets/' + digest + '.cdanim.json',
                        session_fingerprint=_session_fingerprint(saved.rig[target.SESSION]))
    if not any(entry['path'] for entry in sources) and recovery is None:
        raise ValueError('This worklist has no external Source or recovery dependencies to package.')
    root = Path(directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    for (_kind, row), entry in zip(rows, sources):
        if entry['path']:
            _copy_verified(source_path(row), root / entry['path'], entry['source_sha256'])
    if recovery is not None:
        _copy_verified(packet_path(saved.rig, record), root / recovery['path'], recovery['sha256'])
    manifest = dict(schema=SCHEMA, workspace_identity=saved.workspace_identity,
                    blend_included=False, rows=sources, recovery=recovery)
    path = root / MANIFEST
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(_json(manifest) + '\n')
    return dict(manifest_path=str(path), rows=len(rows),
                sources=len({entry['path'] for entry in sources if entry['path']}),
                packets=int(recovery is not None))


def _package_path(root, relative):
    if type(relative) is not str or '\\' in relative:
        raise ValueError('A package dependency path is not a relative portable path.')
    parts = PurePosixPath(relative)
    if not relative or parts.is_absolute() or '..' in parts.parts or ':' in relative:
        raise ValueError('A package dependency path escapes its directory.')
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('A package dependency escapes its directory or is not a file.')
    return path


def rebind_dependencies(context, manifest_path):
    """Relocate identical loaded libraries and the exact session transactionally."""
    bpy, worklist, target, saved, rows, record = _admit(context)
    path = Path(manifest_path).expanduser().resolve(strict=True)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('The dependency manifest is too large.')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(manifest, dict) or set(manifest) != {
            'schema', 'workspace_identity', 'blend_included', 'rows', 'recovery'}
            or manifest['schema'] != SCHEMA or manifest['blend_included'] is not False
            or manifest['workspace_identity'] != saved.workspace_identity
            or not isinstance(manifest['rows'], list)):
        raise ValueError('This dependency manifest does not belong to the saved worklist.')
    expected = {_json(_row_identity(kind, row)): row for kind, row in rows}
    found, row_paths, libraries = set(), [], {}
    for entry in manifest['rows']:
        if not isinstance(entry, dict) or 'path' not in entry or 'source_content_sha256' not in entry:
            raise ValueError('A dependency row is incomplete.')
        identity = dict(entry)
        relative = identity.pop('path')
        content = identity.pop('source_content_sha256')
        key = _json(identity)
        if key not in expected or key in found:
            raise ValueError('Package rows changed or belong to another worklist; nothing was rebound.')
        found.add(key)
        row = expected[key]
        library = _source(row, worklist, bpy, files=False, historical=identity['kind'].startswith('history:'))
        if library is None:
            if relative or content:
                raise ValueError('A new animation cannot acquire an invented Source dependency.')
            continue
        if _source_content(row) != _sha(content):
            raise ValueError('The loaded Source curves differ from this package; no library path was changed.')
        candidate = _package_path(path.parent, relative)
        if _hash(candidate) != row.source_hash:
            raise ValueError('A packaged Source changed; its existing linked identity was kept.')
        previous = libraries.get(library)
        if previous is not None and previous != str(candidate):
            raise ValueError('One shared Source library cannot be redirected to two different files.')
        libraries[library] = str(candidate)
        row_paths.append((row, str(candidate)))
    if found != set(expected):
        raise ValueError('The package does not contain every active and retired worklist row.')
    # A library may be shared, but a different scene's row must not retain a
    # stale source_file after this scene explicitly relocates it.
    our_rows = {row.as_pointer() if hasattr(row, 'as_pointer') else id(row) for _kind, row in rows}
    for scene in bpy.data.scenes:
        settings = getattr(scene, 'character_designer_animation_worklist', None)
        for _kind, row in _rows(settings) if settings is not None else ():
            pointer = row.as_pointer() if hasattr(row, 'as_pointer') else id(row)
            if (pointer not in our_rows and row.source_action is not None
                    and row.source_action.library in libraries):
                raise ValueError('Another scene shares this Source library; relocate its complete ownership together.')
    recovery = manifest['recovery']
    if (record is None) != (recovery is None):
        raise ValueError('The package and current Main Rig recovery session differ.')
    if record is not None:
        if (not isinstance(recovery, dict) or set(recovery) != {
                'sha256', 'path', 'session_fingerprint'}
                or recovery['sha256'] != record['packet_sha256']
                or recovery['session_fingerprint'] != _session_fingerprint(saved.rig[target.SESSION])):
            raise ValueError('The package belongs to a different recovery checkpoint.')
        candidate = _package_path(path.parent, recovery['path'])
        if _hash(candidate) != _sha(record['packet_sha256']):
            raise ValueError('The packaged recovery packet changed; the checkpoint was kept.')
        packet_candidate = str(candidate)
    old_paths = [(library, library.filepath) for library in libraries]
    old_locator = saved.rig.get(LOCATIONS)
    locations = _locations(saved.rig)
    def native_path(physical):
        if getattr(bpy.data, 'filepath', ''):
            return bpy.path.relpath(physical)
        return physical
    def locate(kind, logical, digest, physical):
        locations[kind] = [entry for entry in locations[kind]
                           if (entry['logical'], entry['sha256']) != (logical, digest)]
        locations[kind].append(dict(logical=logical, sha256=digest,
                                    path=native_path(physical), absolute_path=physical))
    for row, candidate in row_paths:
        locate('sources', row.source_file, row.source_hash, candidate)
    if record is not None:
        locate('packets', record['packet_path'], record['packet_sha256'], packet_candidate)
    try:
        for library, candidate in libraries.items():
            library.filepath = native_path(candidate)
        saved.rig[LOCATIONS] = _json(locations)
        for kind, row in rows:
            if not (kind.startswith('history:') and row.source_action is None
                    and not row.source_file and not row.source_hash):
                worklist._check_baseline(row)
        _validate_current(context, rows)
        if record is not None:
            from . import unity_animation as ua
            restored_packet = ua.load_package(packet_path(saved.rig, record))
            if restored_packet['_sha256'] != record['packet_sha256']:
                raise ValueError('The recovery packet no longer matches its unchanged checkpoint.')
            if not any(getattr(row, 'current_rig_revision', '') for _kind, row in rows):
                target._validate_record(context, saved.rig, target._load(saved.rig))
    except Exception as error:
        rollback_errors = []
        for owner, field, value in [(library, 'filepath', old) for library, old in old_paths]:
            try:
                setattr(owner, field, value)
            except Exception as rollback:
                rollback_errors.append(str(rollback))
        try:
            if old_locator is None:
                saved.rig.pop(LOCATIONS, None)
            else:
                saved.rig[LOCATIONS] = old_locator
        except Exception as rollback:
            rollback_errors.append(str(rollback))
        if rollback_errors:
            raise RuntimeError('Dependency relocation stopped; restoring its original paths needs attention: '
                               + '; '.join(rollback_errors)) from error
        raise
    return dict(manifest_path=str(path), rows=len(rows), sources=len(libraries),
                packets=int(record is not None),
                portable_paths=bool(getattr(bpy.data, 'filepath', '')) and all(
                    native_path(candidate).startswith('//') for _row, candidate in row_paths)
                    and (record is None or native_path(packet_candidate).startswith('//')))


def _validate_current(context, rows):
    for kind, row in rows:
        if getattr(row, 'current_rig_revision', ''):
            from . import animation_worklist_revision as revision
            if kind == 'active':
                revision.validate(context, row.item_id)
            else:
                revision._read(context, row, revision._modules())
