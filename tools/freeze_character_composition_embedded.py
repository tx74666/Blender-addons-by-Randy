"""Freeze an approved narrow embedded C# overlay onto the .25 Python candidate.

This makes a source-only candidate; it builds/installs nothing. Native
acceptance and a separately approved final delivery manifest remain required.
"""
import argparse
import hashlib
import json
from pathlib import Path

from release_projection import ROOT, load_source


EXPECTED = {
    'unity_runtime/Runtime/CharacterAnimationComposition.cs',
    'unity_runtime/Runtime/CharacterAnimationComposition.cs.meta',
    'unity_runtime/Editor/CharacterAnimationCompositionJson.cs',
    'unity_runtime/Editor/CharacterAnimationCompositionJson.cs.meta',
    'unity_runtime/Editor/CharacterAnimationWeaponContext.cs',
    'unity_runtime/Editor/CharacterAnimationWeaponContext.cs.meta',
    'unity_runtime/Editor/CharacterAnimationTransfer.cs',
    'unity_runtime/Editor/CharacterAnimationReturn.cs',
}


def sha(content):
    return hashlib.sha256(content).hexdigest()


def freeze(base_manifest, overlay_manifest, overlay_sha256, output):
    base = load_source('character_designer', base_manifest)
    if base.version != '0.77.25' or len(base.files) != 167:
        raise ValueError('Use the complete frozen .25 Python candidate.')
    raw = Path(overlay_manifest).read_bytes()
    if sha(raw) != overlay_sha256:
        raise ValueError('The approved embedded overlay manifest changed.')
    overlay = json.loads(raw)
    if (overlay.get('schema') != 'character-composition-embedded-unity-overlay/1'
            or overlay.get('version') != '0.77.25' or overlay.get('sourceOnlyCandidate') is not True
            or overlay.get('completeRelease') is not False or overlay.get('nativeValidation') != 'pending'
            or not isinstance(overlay.get('files'), list)
            or len(overlay['files']) != len(EXPECTED)
            or {row['path'] for row in overlay['files']} != EXPECTED):
        raise ValueError('Use the approved exact eight-file source-only overlay.')
    canonical = ROOT / 'addons' / 'character_designer'
    payload, audit = dict(base.files), {}
    for row in overlay['files']:
        relative = Path(row['path'])
        source = canonical / relative
        if source.resolve() != Path(row['canonicalSource']).resolve():
            raise ValueError('Embedded source belongs to another package.')
        content = source.read_bytes()
        if sha(content) != row['sha256']:
            raise ValueError('Canonical embedded source changed: ' + row['path'])
        payload[relative] = content
        audit[row['path']] = {'canonical_source': str(source), 'sha256': row['sha256']}
    for entry in audit.values():
        if sha(Path(entry['canonical_source']).read_bytes()) != entry['sha256']:
            raise ValueError('Canonical embedded source changed during freezing.')
    output = Path(output).resolve()
    if output.exists() or output == canonical or output.is_relative_to(canonical):
        raise ValueError('Use a fresh directory outside canonical sources.')
    package = output / 'character_designer'
    package.mkdir(parents=True)
    for relative, content in payload.items():
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = {'schema_version': 1, 'module': 'character_designer', 'version': base.version,
        'source_root': str(package), 'files': {path.as_posix(): sha(content)
            for path, content in sorted(payload.items())},
        'source_provenance': {'purpose': 'source-only complete package candidate',
            'complete_release': False, 'native_validation': 'pending',
            'python_projection': str(Path(base_manifest).resolve()),
            'python_projection_sha256': base.projection_sha256,
            'embedded_overlay_manifest': str(Path(overlay_manifest).resolve()),
            'embedded_overlay_manifest_sha256': overlay_sha256,
            'canonical_embedded_overlays': audit}}
    target = output / 'candidate_projection.json'
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    locked = load_source('character_designer', target)
    return {'manifest': str(target), 'sha256': locked.projection_sha256,
            'files': len(locked.files), 'embedded_overlays': len(audit),
            'complete_release': False, 'native_validation': 'pending'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-projection', type=Path, required=True)
    parser.add_argument('--embedded-manifest', type=Path, required=True)
    parser.add_argument('--embedded-manifest-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.python_projection, args.embedded_manifest,
                           args.embedded_manifest_sha256, args.output)))
