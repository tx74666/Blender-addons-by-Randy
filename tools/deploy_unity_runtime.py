"""Install the bundled Unity companion; preserve Unity GUIDs and external edits.

Stop Unity Play Mode before deployment. This file utility does not control Unity.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_inside(path, root):
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise SystemExit('Companion path leaves its allowed directory: ' + str(path))


def require_destination(path, root):
    require_inside(path, root)
    if path.resolve() != path.absolute() or (path.is_file() and path.stat().st_nlink > 1):
        raise SystemExit('Companion destination uses a filesystem link: ' + str(path))


def select_files(files, requested, source, target, project):
    """Validate the entire explicit allowlist before reading or writing targets."""
    selected = {}
    for raw in requested:
        name = raw.replace('\\', '/')
        windows_path = PureWindowsPath(raw)
        if (not name or PurePosixPath(name).is_absolute() or windows_path.drive or
                windows_path.root or any(part in {'', '.', '..'} for part in name.split('/'))):
            raise SystemExit('--only requires a relative companion file path: ' + raw)
        if name.lower().endswith('.meta') or name not in files:
            raise SystemExit('Unknown or excluded companion file in --only: ' + raw)
        require_inside(files[name], source)
        selected[name] = files[name]

    require_destination(target, project / 'Assets')
    require_inside(project / 'Assets', project)
    require_destination(target / '.cdesigner-runtime.json', target)
    for name in selected:
        require_destination(target / name, target)
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--only', action='append', metavar='RELATIVE_PATH',
                        help='Deploy/check only this companion file; repeat for multiple files.')
    args = parser.parse_args()
    project = Path(args.project).resolve()
    if not (project / 'ProjectSettings/ProjectVersion.txt').is_file() or not (project / 'Assets').is_dir():
        raise SystemExit('Expected an existing Unity project with Assets and ProjectSettings.')
    source = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_runtime'
    target = project / 'Assets/CharacterDesigner'
    marker = target / '.cdesigner-runtime.json'
    files = {p.relative_to(source).as_posix(): p for p in source.rglob('*') if p.is_file()
             and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.meta'}}
    if args.only:
        files = select_files(files, args.only, source, target, project)
    prior = json.loads(marker.read_text(encoding='utf8')) if marker.exists() else {}
    if not isinstance(prior, dict):
        raise SystemExit('Expected a companion deployment marker object: ' + str(marker))
    mismatches = [name for name, path in files.items() if not (target/name).is_file() or digest(target/name) != digest(path)]
    if args.check:
        print(json.dumps({'files': len(files), 'different': mismatches, 'target': str(target)}, indent=2))
        raise SystemExit(bool(mismatches))
    for name in mismatches:
        destination = target / name
        if destination.exists() and (name not in prior or digest(destination) != prior[name]):
            raise SystemExit('Companion file changed outside this installer: ' + str(destination))
    for name in mismatches:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(files[name], destination)
    target.mkdir(parents=True, exist_ok=True)
    record = dict(prior) if args.only else {}
    record.update({name: digest(path) for name, path in files.items()})
    marker.write_text(json.dumps(record, indent=2), encoding='utf8')
    print(json.dumps({'files': len(files), 'updated': len(mismatches), 'target': str(target)}))


if __name__ == '__main__':
    main()
