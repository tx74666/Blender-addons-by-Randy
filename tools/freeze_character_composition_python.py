"""Freeze explicit canonical Python overlays onto a verified .24 test base.

This creates a native-test input, never installs an add-on or publishes a
release. Additional C# overlays and successful native acceptance are required
before a complete .25 release can be built/deployed.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path

from release_projection import ROOT, load_source


OVERLAYS = (
    '__init__.py', 'animation_composition.py', 'animation_composition_contract.py',
    'animation_composition_controls.py', 'animation_composition_export.py',
    'animation_composition_export_worker.py', 'animation_composition_ui.py',
    'animation_composition_weapon.py', 'animation_composition_weapon_geometry.py',
    'animation_worklist.py', 'animation_worklist_ui.py', 'animation_workspace.py',
    'animation_worklist_collection.py', 'animation_worklist_new.py', 'animation_link.py',
)


def freeze(base_projection, output):
    base = load_source('character_designer', base_projection)
    if base.version != '0.77.24':
        raise ValueError('Composition tests require the approved complete .24 base.')
    canonical = ROOT / 'addons' / 'character_designer'
    payload = dict(base.files)
    audit = {}
    for name in OVERLAYS:
        path = canonical / name
        content = path.read_bytes()
        ast.parse(content, filename=str(path))
        audit[name] = {'canonical_source': str(path), 'sha256': hashlib.sha256(content).hexdigest()}
        payload[Path(name)] = content
    for relative, content in payload.items():
        if relative.suffix == '.py':
            ast.parse(content, filename=str(relative))
    for name, entry in audit.items():
        if hashlib.sha256((canonical / name).read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Canonical source changed during freezing: ' + name)
    output = Path(output).resolve()
    if output.exists() or output == canonical or output.is_relative_to(canonical):
        raise ValueError('Use a fresh directory outside the canonical package.')
    package = output / 'character_designer'
    package.mkdir(parents=True)
    for relative, content in payload.items():
        destination = package / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    manifest = {
        'schema_version': 1, 'module': 'character_designer', 'version': '0.77.25',
        'source_root': str(package),
        'source_provenance': {
            'purpose': 'isolated native-test input', 'native_validation': 'pending',
            'complete_release': False, 'base_projection': str(Path(base_projection).resolve()),
            'base_projection_sha256': base.projection_sha256,
            'canonical_python_overlays': audit,
            'limits': 'C# release overlays, native acceptance, build and deployment are separate steps.'},
        'files': {relative.as_posix(): hashlib.sha256(content).hexdigest()
                  for relative, content in sorted(payload.items())},
    }
    target = output / 'python_projection.json'
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    locked = load_source('character_designer', target)
    return {'manifest': str(target), 'sha256': locked.projection_sha256,
            'files': len(locked.files), 'overlays': len(audit), 'version': locked.version}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-projection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.base_projection, args.output), ensure_ascii=False))
