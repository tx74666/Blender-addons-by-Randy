"""Publish a verified animation at one explicitly bound, existing Unity FBX."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile


_GUID = re.compile(r'^guid:[ \t]*([0-9a-f]{32})[ \t]*$', re.MULTILINE)
_TAKES = re.compile(r'^[ \t]+takeName:[ \t]*(.*)$', re.MULTILINE)


def _hash(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def asset(path):
    """Return the canonical Assets path and imported GUID without writing files."""
    path = Path(path).expanduser().resolve(strict=True)
    if path.suffix.casefold() != '.fbx' or not path.is_file():
        raise ValueError('Choose an existing Unity animation FBX.')
    if not any(parent.name == 'Assets' and (parent.parent / 'ProjectSettings' / 'ProjectVersion.txt').is_file()
               for parent in path.parents):
        raise ValueError('Choose an FBX inside a Unity project’s Assets folder.')
    if path.with_suffix('.cdesigner.json').exists():
        raise ValueError('Choose an animation-only FBX. This path belongs to a character model export.')
    meta = path.with_suffix(path.suffix + '.meta')
    if meta.is_symlink() or not meta.is_file():
        raise ValueError('Let Unity import this FBX first so its .meta and GUID already exist.')
    metadata = meta.read_bytes()
    text = metadata.decode('utf-8-sig').replace('\r\n', '\n')
    guids = _GUID.findall(text)
    if len(guids) != 1 or 'ModelImporter:' not in text:
        raise ValueError('The FBX needs one valid Unity ModelImporter GUID.')
    if re.search(r'^[ \t]+importAnimation:[ \t]*0\s*$', text, re.MULTILINE):
        raise ValueError('Enable animation import on this Unity FBX before assigning it.')
    takes = _TAKES.findall(text)
    if len(takes) > 1 or (takes and takes[0].strip() not in ('', 'CDesigner Animation Export')):
        raise ValueError('Choose a single-action FBX with compatible clip import settings.')
    return path, guids[0], hashlib.sha256(metadata).hexdigest()


@dataclass(frozen=True)
class Destination:
    path: Path
    guid: str
    meta_hash: str
    fbx_hash: str


def pin(path, guid):
    path, actual_guid, meta_hash = asset(path)
    if guid != actual_guid:
        raise ValueError('Unity’s FBX GUID changed. Assign its destination again.')
    return Destination(path, guid, meta_hash, _hash(path))


def publish(destination, staged_fbx, expected_hash):
    """Replace only the FBX after the frozen file and importer checks pass."""
    staged_fbx = Path(staged_fbx)
    with tempfile.TemporaryDirectory(prefix='.cdesigner-queue-', dir=destination.path.parent) as folder:
        candidate = Path(folder) / destination.path.name
        digest = hashlib.sha256()
        with staged_fbx.open('rb') as incoming, candidate.open('xb') as output:
            for block in iter(lambda: incoming.read(1024 * 1024), b''):
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        if not candidate.stat().st_size or digest.hexdigest() != expected_hash:
            raise ValueError('The staged FBX does not match the completed export.')
        if pin(destination.path, destination.guid) != destination:
            raise ValueError('The Unity FBX or its importer changed during export. Retry from the queue.')
        if destination.fbx_hash != expected_hash:
            os.replace(candidate, destination.path)
