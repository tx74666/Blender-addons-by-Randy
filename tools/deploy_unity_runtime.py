"""Install the bundled Unity companion; preserve Unity GUIDs and external edits.

Stop Unity Play Mode before deployment. This file utility does not control Unity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    project = Path(args.project).resolve()
    if not (project / 'ProjectSettings/ProjectVersion.txt').is_file() or not (project / 'Assets').is_dir():
        raise SystemExit('Expected an existing Unity project with Assets and ProjectSettings.')
    source = Path(__file__).resolve().parents[1] / 'addons/character_designer/unity_runtime'
    target = project / 'Assets/CharacterDesigner'
    marker = target / '.cdesigner-runtime.json'
    prior = json.loads(marker.read_text(encoding='utf8')) if marker.exists() else {}
    files = {p.relative_to(source).as_posix(): p for p in source.rglob('*') if p.is_file()
             and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.meta'}}
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
    marker.write_text(json.dumps({name: digest(path) for name, path in files.items()}, indent=2), encoding='utf8')
    print(json.dumps({'files': len(files), 'updated': len(mismatches), 'target': str(target)}))


if __name__ == '__main__':
    main()
