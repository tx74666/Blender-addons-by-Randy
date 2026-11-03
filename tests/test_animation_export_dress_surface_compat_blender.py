"""Trusted-open derivative of the existing actual-surface public export QA.

The historical harness is not rewritten. Its native input preservation,
physical Action/NLA guards, independent Manual endpoint and 2e-4 output gate
run unchanged. Only open_mainfile calls explicitly enable trusted scripts.
An 183KB synthetic historical fixture is copied with its Automatic endpoint
restored; no artist or large Cosha scene is used.
"""
import ast
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import skirt_rig as skirt, skirt_surface as surface
from test_animation_export_dress_compat_blender import guard_cases


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    folder = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    extra_guards = guard_cases(folder)
    original = Path('D:/Blender/Projects/Character/X/validation/animation_physics_20261027/surface01/physics_surface_actual_private.blend')
    original_sha = sha(original)
    bpy.ops.wm.open_mainfile(filepath=str(original), use_scripts=True, load_ui=False)
    source = bpy.data.objects['Physics Surface Dress']
    record = skirt.read_record(source)
    rig = source[skirt.RIG_KEY]
    surface.validate(source, rig, record)
    holder, _identifier, _path = skirt.physics_control(source)
    holder['physics_influence'] = 1.
    surface.set_mode(source, record, 'AUTOMATIC')
    surface.validate(source, rig, record)
    copy = folder / 'synthetic_automatic_input.blend'
    assert 'FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(copy), check_existing=False)
    harness_path = ROOT / 'tests/test_dress_animation_export_blender.py'
    tree = ast.parse(harness_path.read_bytes())
    changed = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == 'bpy.ops.wm.open_mainfile':
            node.keywords = [keyword for keyword in node.keywords if keyword.arg != 'use_scripts']
            node.keywords.append(ast.keyword(arg='use_scripts', value=ast.Constant(value=True)))
            changed.append(node.lineno)
    assert changed
    env = {'__name__': '_trusted_existing_dress_surface_harness', '__file__': str(harness_path)}
    exec(compile(ast.fix_missing_locations(tree), str(harness_path) + '<trusted-open-derivative>', 'exec'), env)
    report = {'success': False, 'original_historical_input': str(original), 'original_sha256': original_sha,
              'synthetic_copy_sha256': sha(copy), 'historical_harness_sha256': sha(harness_path),
              'derivative': 'Only open_mainfile adds use_scripts=True; no gate/math/source-witness edits.',
              'trusted_open_lines': changed, 'extra_public_muted_target_and_native_guards': extra_guards}
    try:
        env['run'](SimpleNamespace(input_blend=copy, output=folder / 'public_harness',
                                   source='Physics Surface Dress'), report)
        assert sha(original) == original_sha
    except Exception as error:
        report['compatibility_error'] = repr(error)
        raise
    finally:
        (folder / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('DRESS_SURFACE_PUBLIC_COMPAT_OK', json.dumps(report))


if __name__ == '__main__':
    main()
