"""Resolve canonical sources or a complete, hash-locked approved projection.

A projection is a frozen release input, not a development checkout. Both build
and deployment read this resolver's verified byte payload rather than gathering
unapproved changes from the current canonical package.
"""

import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


ROOT = Path(__file__).resolve().parents[1]
_MODULES = frozenset({'random_realm_builder_exporter', 'character_designer'})
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_VERSION = re.compile(r'^[0-9]+(?:\.[0-9]+){2}$')


class SourceProjectionError(ValueError):
    """The explicitly selected release projection cannot be verified."""


@dataclass(frozen=True)
class ReleaseSource:
    root: Path
    files: dict
    version: str
    projection_sha256: str | None = None
    source_provenance: dict | None = None


def _version_bytes(content, label):
    tree = ast.parse(content, filename=str(label))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == 'bl_info'
            for target in node.targets
        ):
            info = ast.literal_eval(node.value)
            return '.'.join(str(part) for part in info['version'])
    raise ValueError(f'Missing bl_info: {label}')


def version_of(source):
    return _version_bytes((source / '__init__.py').read_bytes(), source)


def shipped_files(folder):
    """Keep the existing build/deploy exclusions for caches and hidden files."""
    return {
        path.relative_to(folder): path
        for path in folder.rglob('*')
        if path.is_file()
        and not any(part.startswith('.') or part == '__pycache__'
                    for part in path.relative_to(folder).parts)
        and path.suffix not in {'.pyc', '.pyo'}
    }


def _module(module):
    if module not in _MODULES:
        raise SourceProjectionError(f'Unsupported release module: {module!r}')


def _safe_relative(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise SourceProjectionError(f'Unsafe projection file path: {name!r}')
    path = PurePosixPath(name)
    if (path.is_absolute() or path.as_posix() != name
            or any(part in {'', '.', '..', '__pycache__'} or part.startswith('.') for part in path.parts)
            or path.suffix in {'.pyc', '.pyo'}):
        raise SourceProjectionError(f'Unsafe projection file path: {name!r}')
    return Path(*path.parts)


def _reparse(path):
    attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    return path.is_symlink() or bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def _safe_source(raw, canonical):
    if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
        raise SourceProjectionError('Projection source_root must be an absolute directory path.')
    path = Path(raw)
    if not path.is_dir():
        raise SourceProjectionError(f'Projection source_root is not a directory: {path}')
    if _reparse(path):
        raise SourceProjectionError('Projection source_root must not be a symlink or reparse point.')
    source = path.resolve()
    if source == canonical or source.is_relative_to(canonical):
        raise SourceProjectionError('An approved projection must be frozen outside the canonical package.')
    # Do not let a directory junction traverse another tree or cycle. Check
    # entries before descent, including hidden/cache entries that will not ship.
    for directory, directories, files in os.walk(source, followlinks=False):
        for name in (*directories, *files):
            entry = Path(directory) / name
            if _reparse(entry) or not entry.resolve().is_relative_to(source):
                raise SourceProjectionError(f'Projection contains a symlink, reparse point or escaping path: {entry}')
    return source


def _read_manifest(path):
    path = Path(path)
    try:
        content = path.read_bytes()
        manifest = json.loads(content)
    except (OSError, ValueError, TypeError) as exc:
        raise SourceProjectionError(f'Cannot read projection manifest: {path}') from exc
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        raise SourceProjectionError('Unsupported projection manifest schema_version.')
    if not isinstance(manifest.get('source_provenance'), dict) or not manifest['source_provenance']:
        raise SourceProjectionError('Projection source_provenance must be a nonempty audit object.')
    return manifest, hashlib.sha256(content).hexdigest()


def load_source(module, projection=None, *, root=ROOT):
    """Read one effective package and return its immutable input byte snapshot."""
    _module(module)
    canonical = (Path(root) / 'addons' / module).resolve()
    if projection is None:
        files = {relative: path.read_bytes() for relative, path in shipped_files(canonical).items()}
        return ReleaseSource(canonical, files, _version_bytes(files[Path('__init__.py')], canonical))
    manifest, digest = _read_manifest(projection)
    if manifest.get('module') != module:
        raise SourceProjectionError(f'Projection module must match the requested module {module!r}.')
    version = manifest.get('version')
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise SourceProjectionError('Projection version must be a three-part version string.')
    source = _safe_source(manifest.get('source_root'), canonical)
    expected = manifest.get('files')
    if not isinstance(expected, dict) or not expected:
        raise SourceProjectionError('Projection files must contain the complete file SHA256 map.')
    hashes = {}
    for name, checksum in expected.items():
        relative = _safe_relative(name)
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            raise SourceProjectionError(f'Invalid SHA256 for projection file {name!r}.')
        hashes[relative] = checksum
    paths = shipped_files(source)
    if set(paths) != set(hashes):
        missing = sorted(str(path) for path in set(hashes) - set(paths))
        extra = sorted(str(path) for path in set(paths) - set(hashes))
        raise SourceProjectionError(f'Projection file set differs; missing={missing}, extra={extra}.')
    files = {}
    for relative, path in paths.items():
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != hashes[relative]:
            raise SourceProjectionError(f'Projection SHA256 mismatch: {relative.as_posix()}')
        files[relative] = content
    try:
        actual_version = _version_bytes(files[Path('__init__.py')], source)
    except (KeyError, ValueError, TypeError, SyntaxError) as exc:
        raise SourceProjectionError('Projection has no valid package version in __init__.py.') from exc
    if actual_version != version:
        raise SourceProjectionError(f'Projection version differs: expected {version}, package has {actual_version}.')
    return ReleaseSource(source, files, actual_version, digest, manifest['source_provenance'])


def resolve_source(module, projection=None, *, root=ROOT):
    """Return the selected source directory; validate projections completely."""
    _module(module)
    if projection is None:
        return (Path(root) / 'addons' / module).resolve()
    return load_source(module, projection, root=root).root
