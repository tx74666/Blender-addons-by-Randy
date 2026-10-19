"""Pure, immutable candidate publication for the Unity partial-update receiver.

This module neither imports Blender nor edits a formal Unity bundle. The caller
supplies stable 32-hex rig/part identities and a complete, independently checked
FBX renderer inventory. No identity is generated or inferred from a display
name. The receiver still validates the actual imported geometry, mapping and
application authority before accepting any update.

Capture the base before running the isolated full-context export worker; publish
its complete output against that same snapshot afterward. An absent receiver
ledger means an empty revision, while an unreadable or invalid ledger is an
error. A completed candidate is published only at the project's existing
``.codex/character-update-inbox/<asset>/<update>/candidate.json`` location.
"""

from dataclasses import dataclass
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile


SCHEMA = 'cdesigner.unity-character-update.v1'
BASE_SCHEMA = 'cdesigner.unity-character.v1'
RECEIVER_SCHEMA = 'randomrealm.character-partial-receiver/1'
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PARTS, MAX_FILES, MAX_SLOTS = 2048, 1024, 128
_ID = re.compile(r'[0-9a-fA-F]{32}\Z')
_DIGEST = re.compile(r'[0-9a-fA-F]{64}\Z')
_INVALID_FILE = set('<>:"\\|?*')
_DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z', re.I)


class CandidateError(ValueError):
    """Incomplete, ambiguous, changed or unsafe candidate input."""


@dataclass(frozen=True)
class Part:
    part_id: str
    renderer_path: str
    object_name: str
    material_slots: tuple

    def record(self):
        return {'part_id': self.part_id, 'renderer_path': self.renderer_path,
                'object_name': self.object_name, 'material_slots': list(self.material_slots)}


@dataclass(frozen=True)
class FilePin:
    path: Path
    sha256: str | None
    size: int | None


@dataclass(frozen=True)
class BaseSnapshot:
    unity_project: Path
    model_file: Path
    manifest_file: Path
    asset_id: str
    rig_id: str
    parts: tuple
    model_sha256: str
    manifest_sha256: str
    receiver_revision: str
    file_pins: tuple
    receiver_state_pin: FilePin


def _need(condition, message):
    if not condition:
        raise CandidateError(message)


def _text(value, label, maximum=1024, *, empty=False):
    _need(type(value) is str, label + ' must be a string.')
    try:
        length = len(value.encode('utf-16-le')) // 2
    except UnicodeError as exc:
        raise CandidateError(label + ' contains invalid Unicode.') from exc
    _need(length <= maximum and (empty or bool(value.strip()))
          and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value),
          label + ' is empty, oversized or contains control characters.')
    return value


def _identity(value, label):
    _need(type(value) is str and _ID.fullmatch(value) is not None,
          label + ' requires an existing stable 32-hex identity.')
    return value.lower()


def _digest(value, label):
    _need(type(value) is str and _DIGEST.fullmatch(value) is not None,
          label + ' requires a SHA-256 digest.')
    return value.lower()


def _absolute(value, label):
    _need(isinstance(value, (str, os.PathLike)), label + ' requires an absolute path.')
    value = _text(os.fspath(value), label, 4096)
    if os.name == 'nt':
        if value.startswith('\\\\?\\UNC\\'):
            value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'):
            value = value[4:]
        _need(not value.startswith('\\\\.\\'), 'Device paths are not model or candidate paths.')
    path = Path(value)
    _need(path.is_absolute(), label + ' requires an absolute path.')
    return Path(os.path.abspath(path))


def _io(path):
    """Use long-path I/O even inside a Windows host without longPathAware.

    Blender's embedded Python inherits Blender.exe's manifest; a normal Python
    executable accepting the same path is not sufficient validation. Public
    paths and manifest values stay conventional absolute paths for the receiver.
    """
    if os.name != 'nt':
        return path
    value = os.fspath(path)
    if value.startswith('\\\\?\\'):
        return Path(value)
    if value.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + value[2:])
    return Path('\\\\?\\' + value)


def _same(first, second):
    return str(first).casefold() == str(second).casefold()


def _within(path, folder):
    prefix = str(folder).rstrip('/\\').casefold() + os.sep
    return str(path).casefold().startswith(prefix)


def _guard_path(path):
    """Reject links/junctions in existing ancestors and aliased regular files."""
    path = _absolute(path, 'File')
    for item in (path, *path.parents):
        if not os.path.lexists(_io(item)):
            continue
        info = _io(item).lstat()
        _need(not stat.S_ISLNK(info.st_mode)
              and not (getattr(info, 'st_file_attributes', 0) & 0x400),
              'Candidate validation does not follow filesystem links: ' + str(item))
        if stat.S_ISREG(info.st_mode):
            _need(info.st_nlink == 1, 'Hard-linked files are not accepted: ' + str(item))
    return path


def _relative(value, label='File', *, payload=False):
    value = _text(value, label, 1024)
    segments = value.split('/')
    _need(not value.startswith('/') and '\\' not in value
          and all(segment and segment not in {'.', '..'}
                  and not any(c in _INVALID_FILE for c in segment)
                  and not segment.endswith(('.', ' ')) and _DEVICE.fullmatch(segment) is None
                  for segment in segments), label + ' is not a safe relative path.')
    _need(not value.casefold().endswith('.meta'), 'Unity .meta files are not candidate payloads.')
    if payload:
        _need(value.casefold() != 'candidate.json' and not segments[0].startswith('.'),
              'A payload uses a reserved candidate publication name.')
    return value


def _owned(folder, relative, *, payload=False):
    relative = _relative(relative, payload=payload)
    path = _absolute(folder / relative, 'Owned file')
    _need(_within(path, folder), 'An owned file escapes its directory.')
    return _guard_path(path)


def _pin(path, *, absent=False):
    path = _guard_path(path)
    if not os.path.lexists(_io(path)):
        _need(absent, 'A required baseline or candidate file is missing: ' + str(path))
        return FilePin(path, None, None)
    before = _io(path).stat()
    _need(stat.S_ISREG(before.st_mode), 'A required file is not a regular file: ' + str(path))
    digest = hashlib.sha256()
    with _io(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    after = _io(path).stat()
    _need((before.st_size, before.st_mtime_ns, before.st_ino) ==
          (after.st_size, after.st_mtime_ns, after.st_ino),
          'A required file changed while it was being read: ' + str(path))
    _guard_path(path)
    return FilePin(path, digest.hexdigest(), after.st_size)


def _verify(pin):
    _need(_pin(pin.path, absent=pin.sha256 is None) == pin,
          'An accepted baseline or staged input changed: ' + str(pin.path))


def _pairs(pairs):
    result = {}
    for name, value in pairs:
        _need(name not in result, 'A JSON object contains duplicate keys: ' + name)
        result[name] = value
    return result


def _read_json(path):
    pin = _pin(path)
    _need(0 < pin.size <= MAX_JSON_BYTES, 'A JSON manifest is empty or exceeds 2 MiB.')
    try:
        record = json.loads(_io(path).read_bytes().decode('utf-8-sig'), object_pairs_hook=_pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(
                                CandidateError('Non-finite JSON value: ' + value)))
        encoded = json.dumps(record, allow_nan=False)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise CandidateError('A required JSON manifest is invalid: ' + str(path)) from exc
    _need(type(record) is dict and len(encoded) > 0, 'A required JSON manifest must be an object.')
    _verify(pin)
    return record, pin


def _parts(values):
    _need(type(values) in {list, tuple} and 0 < len(values) <= MAX_PARTS,
          'Declare the complete, bounded renderer context.')
    result, ids, paths = [], set(), set()
    for item in values:
        if isinstance(item, Part):
            item = item.record()
        _need(type(item) is dict, 'Each part declaration must be an object.')
        required = {'part_id', 'renderer_path', 'object_name', 'material_slots'}
        _need(set(item) == required, 'A part declaration has missing or unknown fields.')
        part_id = _identity(item['part_id'], 'part_id')
        renderer = _text(item['renderer_path'], 'renderer_path', 2048)
        _need('\\' not in renderer
              and all(segment and segment not in {'.', '..'} for segment in renderer.split('/')),
              'A renderer path is invalid.')
        slots = item['material_slots']
        _need(type(slots) in {list, tuple} and len(slots) <= MAX_SLOTS,
              'A material-slot inventory is missing or oversized.')
        slots = tuple(_text(slot, 'material slot', 1024) for slot in slots)
        _need(part_id not in ids and renderer not in paths, 'Part or renderer identities are ambiguous.')
        ids.add(part_id)
        paths.add(renderer)
        result.append(Part(part_id, renderer, _text(item['object_name'], 'object_name'), slots))
    return tuple(result)


def _mapping(parts):
    return {part.part_id: (part.renderer_path, part.material_slots) for part in parts}


def _project(model, supplied):
    if supplied is None:
        assets = next((parent for parent in model.parents if parent.name.casefold() == 'assets'
                       and _io(parent.parent / 'Assets').is_dir()
                       and _io(parent.parent / 'ProjectSettings').is_dir()), None)
        _need(assets is not None, 'The accepted model must identify its Unity Assets project.')
        project = assets.parent
    else:
        project = _absolute(supplied, 'Unity project')
    _guard_path(project)
    _need(_io(project / 'Assets').is_dir() and _io(project / 'ProjectSettings').is_dir(),
          'The accepted model does not identify an existing Unity project.')
    _guard_path(project / 'Assets')
    _guard_path(project / 'ProjectSettings')
    _need(_within(model, project / 'Assets'), 'The accepted model is outside this Unity Assets folder.')
    return project


def receiver_capabilities(model_file, *, unity_project=None):
    """Read a compatible receiver announcement without creating any files.

    Missing announcement returns ``None``. Existing malformed, foreign-project,
    redirected or differently versioned announcements are errors. The returned
    dictionary contains only validated fields; feature flags are exact Booleans,
    never truthy strings/numbers. This describes a protocol, not permission to
    bypass native provider/receiver validation or the required application review.
    """
    model = _guard_path(_absolute(model_file, 'Accepted model'))
    _need(model.suffix.casefold() == '.fbx', 'Receiver discovery requires a Unity FBX path.')
    project = _project(model, unity_project)
    path = _guard_path(project / '.codex' / 'character-update-receiver.json')
    if not os.path.lexists(_io(path)):
        return None
    record, _pin_value = _read_json(path)
    _need(record.get('schema') == RECEIVER_SCHEMA and record.get('candidate_schema') == SCHEMA,
          'The receiver announcement has an incompatible schema.')
    expected_paths = {'project_root': project,
                      'inbox': project / '.codex' / 'character-update-inbox',
                      'accepted_state_root': project / '.codex' / 'character-update-state'}
    result = {'schema': RECEIVER_SCHEMA, 'candidate_schema': SCHEMA}
    for key, expected in expected_paths.items():
        actual = _guard_path(_absolute(record.get(key), 'receiver ' + key))
        _need(_same(actual, expected), 'The receiver announcement redirects ' + key + '.')
        result[key] = str(expected)
    for key in ('requires_review', 'supports_rig', 'supports_forearm_mesh', 'supports_full_reconcile'):
        _need(type(record.get(key)) is bool, 'The receiver announcement needs an explicit Boolean: ' + key)
        result[key] = record[key]
    _need(result['requires_review'], 'This workflow requires explicit Unity update review.')
    supported = record.get('supported')
    _need(type(supported) is list and supported == ['mesh_same_skeleton'],
          'The receiver announcement does not describe this supported mesh-update protocol.')
    result['supported'] = list(supported)
    if 'native_verified_utc' in record:
        result['native_verified_utc'] = _text(record['native_verified_utc'], 'native_verified_utc', 128)
    if 'verification_report' in record:
        report = _guard_path(_absolute(record['verification_report'], 'verification_report'))
        expected = project / '.codex' / 'task-staging' / 'character-partial-update-20261019' / 'verification-runs'
        _need(_within(report, expected), 'The receiver verification report belongs to another workflow.')
        result['verification_report'] = str(report)
    return result


def require_full_update_allowed(model_file, *, asset_id, unity_project=None):
    """Refuse a formal full overwrite while any accepted partial ledger exists.

    Success returns ``None``; it does not perform a full export or reconciliation.
    An existing ledger is a blocker even if malformed, empty, linked, a directory,
    or accompanied by a claimed future full-reconcile capability. The current
    provider has no verified reconciliation implementation and must preserve it.
    Callers retaining legacy exports outside Unity may skip this strict helper;
    this helper itself only accepts an actual Unity Assets project path.
    """
    model = _guard_path(_absolute(model_file, 'Formal model'))
    _need(model.suffix.casefold() == '.fbx', 'Full-update review requires a Unity FBX path.')
    project = _project(model, unity_project)
    asset_id = _identity(asset_id, 'asset_id')
    path = project / '.codex' / 'character-update-state' / (asset_id + '.json')
    _guard_path(path)
    _need(not os.path.lexists(_io(path)),
          'This character has accepted partial updates. Preserve its full model until explicit reconciliation is supported.')


def capture_base(model_file, manifest_file, *, asset_id, rig_id, parts, unity_project=None):
    """Verify and pin an accepted full bundle and its exact receiver revision.

    ``parts`` must come from the caller's verified complete accepted FBX mapping,
    using persistent supplied IDs. For an existing receiver ledger its identities,
    paths and ordered material keys must match exactly. This helper cannot prove
    a first Unity imported mapping and never treats names as rename authority.
    """
    model = _guard_path(_absolute(model_file, 'Accepted model'))
    manifest = _guard_path(_absolute(manifest_file, 'Accepted manifest'))
    _need(model.suffix.casefold() == '.fbx' and
          _same(manifest, model.with_suffix('.cdesigner.json')),
          'Use the accepted FBX and its adjacent Character Designer manifest.')
    project = _project(model, unity_project)
    asset_id, rig_id = _identity(asset_id, 'asset_id'), _identity(rig_id, 'rig_id')
    parts = _parts(parts)
    record, manifest_pin = _read_json(manifest)
    _need(record.get('schema') == BASE_SCHEMA and record.get('ok') is True
          and _identity(record.get('asset_id'), 'accepted asset_id') == asset_id,
          'The accepted full model has another or incomplete character identity.')
    if 'filename' in record:
        _need(_relative(record['filename']).casefold() == model.name.casefold(),
              'The accepted manifest names another model; a rename does not migrate its identity.')
    files = record.get('files')
    _need(type(files) is dict and 0 < len(files) <= MAX_FILES,
          'The accepted full model has no complete bounded file inventory.')
    pins, aliases = [], set()
    model_pin = None
    for relative, digest in files.items():
        path = _owned(model.parent, relative)
        _need(not _same(path, manifest), 'The accepted manifest cannot hash itself as a payload.')
        alias = str(path).casefold()
        _need(alias not in aliases, 'The accepted inventory contains filesystem aliases.')
        aliases.add(alias)
        pin = _pin(path)
        _need(pin.sha256 == _digest(digest, relative), 'An accepted bundle file differs: ' + relative)
        pins.append(pin)
        if _same(path, model):
            model_pin = pin
    _need(model_pin is not None and model_pin.size > 32, 'The accepted FBX is missing or incomplete.')
    state_path = project / '.codex' / 'character-update-state' / (asset_id + '.json')
    state_pin = _pin(state_path, absent=True)
    revision = ''
    if state_pin.sha256 is not None:
        state, checked = _read_json(state_path)
        _need(checked == state_pin and state.get('schema') == RECEIVER_SCHEMA,
              'The accepted receiver state has an invalid schema or changed.')
        _need(_identity(state.get('asset_id'), 'receiver asset_id') == asset_id
              and _identity(state.get('rig_id'), 'receiver rig_id') == rig_id,
              'The receiver state belongs to another character or rig.')
        _identity(state.get('target_guid'), 'receiver target_guid')
        revision = _identity(state.get('receiver_revision'), 'receiver_revision')
        model_asset = _relative(state.get('model_asset'), 'receiver model_asset')
        _need(_same(_owned(project, model_asset), model)
              and _digest(state.get('model_sha256'), 'receiver model_sha256') == model_pin.sha256
              and _digest(state.get('manifest_sha256'), 'receiver manifest_sha256') == manifest_pin.sha256,
              'The receiver state does not describe this exact accepted full bundle.')
        saved_parts = state.get('parts')
        _need(type(saved_parts) is list and 0 < len(saved_parts) <= MAX_PARTS,
              'The receiver state has no complete registered part inventory.')
        mapped = _parts([{'part_id': item.get('part_id'), 'renderer_path': item.get('renderer_path'),
                          'material_slots': item.get('material_slots'), 'object_name': item.get('renderer_path')}
                         for item in saved_parts if type(item) is dict])
        _need(len(mapped) == len(saved_parts) and _mapping(mapped) == _mapping(parts),
              'Registered part identities, paths or material slots changed; do not infer a rename.')
    base = BaseSnapshot(project, model, manifest, asset_id, rig_id, parts,
                        model_pin.sha256, manifest_pin.sha256, revision,
                        tuple([manifest_pin, *pins]), state_pin)
    revalidate_base(base)
    return base


def revalidate_base(base):
    """Refuse changed formal files or a created/changed/disappeared receiver ledger."""
    _validate_snapshot(base)
    for pin in (*base.file_pins, base.receiver_state_pin):
        _verify(pin)


def _validate_snapshot(base):
    _need(isinstance(base, BaseSnapshot), 'Capture a trusted baseline before publishing a candidate.')
    _need(base.asset_id == _identity(base.asset_id, 'asset_id')
          and base.rig_id == _identity(base.rig_id, 'rig_id'), 'Baseline identities must be canonical.')
    pins = {str(pin.path).casefold(): pin for pin in base.file_pins}
    _need(len(pins) == len(base.file_pins), 'Baseline file pins contain duplicate aliases.')
    model = pins.get(str(base.model_file).casefold())
    manifest = pins.get(str(base.manifest_file).casefold())
    _need(model is not None and manifest is not None
          and model.sha256 == _digest(base.model_sha256, 'base model_sha256')
          and manifest.sha256 == _digest(base.manifest_sha256, 'base manifest_sha256'),
          'The baseline is missing its accepted model or manifest pins.')
    expected_state = base.unity_project / '.codex/character-update-state' / (base.asset_id + '.json')
    _need(_same(base.receiver_state_pin.path, expected_state), 'The baseline has another receiver ledger path.')
    if base.receiver_state_pin.sha256 is None:
        _need(base.receiver_revision == '', 'An absent receiver ledger must have an empty revision.')
    else:
        _identity(base.receiver_revision, 'receiver_revision')


def build_candidate(base, *, model_file, file_hashes, parts, mesh_ids, update_rig, update_id):
    """Build a validated receiver-compatible record without publishing anything."""
    _validate_snapshot(base)
    update_id = _identity(update_id, 'update_id')
    parts = _parts(parts)
    _need(_mapping(parts) == _mapping(base.parts),
          'The complete context differs from its captured stable part mapping.')
    _need(type(mesh_ids) in {list, tuple} and len(mesh_ids) <= MAX_PARTS,
          'Declare an explicit bounded mesh selection.')
    selected = [_identity(value, 'selected part_id') for value in mesh_ids]
    _need(len(selected) == len(set(selected)) and set(selected) <= set(_mapping(parts)),
          'The selection contains duplicate or unknown stable part identities.')
    _need(type(update_rig) is bool, 'update_rig must be an explicit Boolean.')
    model_file = _relative(model_file, 'Candidate model_file', payload=True)
    _need(Path(model_file).suffix.casefold() == '.fbx', 'The complete context model must be an FBX.')
    _need(type(file_hashes) is dict and 0 < len(file_hashes) <= MAX_FILES,
          'Declare the complete bounded candidate file inventory.')
    files, aliases = {}, set()
    for relative, digest in file_hashes.items():
        relative = _relative(relative, payload=True)
        _need(relative.casefold() not in aliases, 'The candidate inventory contains filesystem aliases.')
        aliases.add(relative.casefold())
        files[relative] = _digest(digest, relative)
    _need(model_file in files, 'The candidate inventory must pin its complete context FBX.')
    result = {'schema': SCHEMA, 'ok': True, 'update_id': update_id,
              'asset_id': base.asset_id, 'rig_id': base.rig_id,
              'base': {'model_file': str(base.model_file), 'model_sha256': base.model_sha256,
                       'manifest_sha256': base.manifest_sha256,
                       'receiver_revision': base.receiver_revision},
              'selection': {'mesh_ids': selected, 'update_rig': update_rig, 'delete_ids': []},
              'parts': [part.record() for part in parts], 'model_file': model_file, 'files': files}
    _need(len(_json_bytes(result)) <= MAX_JSON_BYTES, 'The candidate manifest exceeds 2 MiB.')
    return result


def _json_bytes(record):
    return (json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')


def _write_file(path, data):
    with _io(path).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _copy_file(pin, target):
    _io(target.parent).mkdir(parents=True, exist_ok=True)
    _guard_path(target.parent)
    with _io(pin.path).open('rb') as source, _io(target).open('xb') as output:
        shutil.copyfileobj(source, output, 1024 * 1024)
        output.flush()
        os.fsync(output.fileno())
    _need(_pin(target).sha256 == pin.sha256, 'A candidate payload failed copy verification.')
    _verify(pin)


def _rename_noreplace(source, target):
    """Atomic rename that never replaces an existing candidate/file identity."""
    if os.name == 'nt':
        os.rename(_io(source), _io(target))  # Windows rename refuses an existing destination.
        return
    if sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        _need(rename is not None, 'Atomic no-replace publication is unavailable on this platform.')
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
            code = ctypes.get_errno()
            if code in {errno.EEXIST, errno.ENOTEMPTY}:
                raise FileExistsError(code, os.strerror(code), str(target))
            raise OSError(code, os.strerror(code), str(target))
        return
    raise CandidateError('Atomic no-replace publication is unavailable on this platform.')


def _cleanup_owned(folder, parent):
    if folder is None or not os.path.lexists(_io(folder)):
        return
    _guard_path(folder)
    _need(folder.parent == parent and _within(folder, parent), 'Refusing to clean an unowned staging path.')
    _need(not _io(folder / 'candidate.json').exists(), 'A completed publication cannot be rolled back by the provider.')
    shutil.rmtree(_io(folder))


def publish_candidate(base, *, stage, model_file, files, parts, mesh_ids, update_rig, update_id):
    """Publish a complete candidate under the existing Unity project inbox.

    ``stage`` is the isolated worker output directory, ``model_file`` its FBX's
    relative name, and ``files`` the complete relative companion list. Copies are
    hashed and sources/baseline/ledger checked again before publication. The
    hidden staging directory never contains ``candidate.json``: its completed
    marker appears last in a newly claimed immutable update directory. Existing
    update IDs and every formal model/.meta/manifest remain untouched.
    """
    revalidate_base(base)
    stage = _guard_path(_absolute(stage, 'Worker stage'))
    _need(_io(stage).is_dir(), 'The full-context worker output directory is missing.')
    _need(type(files) in {list, tuple} and 0 < len(files) <= MAX_FILES,
          'Declare the complete worker output file list.')
    pins, hashes = [], {}
    for relative in files:
        relative = _relative(relative, payload=True)
        _need(relative.casefold() not in {name.casefold() for name in hashes},
              'The worker output contains duplicate filesystem aliases.')
        pin = _pin(_owned(stage, relative, payload=True))
        _need(pin.size > 0, 'The worker produced an empty payload.')
        pins.append((relative, pin))
        hashes[relative] = pin.sha256
    record = build_candidate(base, model_file=model_file, file_hashes=hashes, parts=parts,
                             mesh_ids=mesh_ids, update_rig=update_rig, update_id=update_id)
    model_pin = next(pin for relative, pin in pins if relative == record['model_file'])
    _need(model_pin.size > 32, 'The worker context FBX is incomplete.')
    inbox = _guard_path(base.unity_project / '.codex' / 'character-update-inbox' / base.asset_id)
    final = inbox / record['update_id']
    _need(not _within(inbox, base.model_file.parent) and not _same(inbox, base.model_file.parent),
          'A candidate cannot be published beside the live model bundle.')
    _need(not os.path.lexists(_io(final)), 'This immutable update identity already exists.')
    _io(inbox).mkdir(parents=True, exist_ok=True)
    _guard_path(inbox)
    pending = _absolute(tempfile.mkdtemp(prefix='.pending-' + record['update_id'] + '-', dir=_io(inbox)),
                        'Private candidate stage')
    owned = pending
    try:
        for relative, pin in pins:
            _copy_file(pin, pending / relative)
        marker = '.completed-candidate.json.pending'
        _write_file(pending / marker, _json_bytes(record))
        for _relative_name, pin in pins:
            _verify(pin)
        revalidate_base(base)
        _guard_path(inbox)
        _rename_noreplace(pending, final)
        owned = final
        # No receiver can discover this directory until the final marker rename.
        revalidate_base(base)
        for relative, pin in pins:
            _need(_pin(final / relative).sha256 == pin.sha256, 'A candidate copy changed before publication.')
        _rename_noreplace(final / marker, final / 'candidate.json')
        owned = None
        return final / 'candidate.json'
    except Exception:
        _cleanup_owned(owned, inbox)
        raise
