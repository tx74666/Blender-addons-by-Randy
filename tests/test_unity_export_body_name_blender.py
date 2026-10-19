"""Body-object export names and existing no-worker Unity export regressions.

Run only in a disposable Blender through run_trusted_blender_validation.py.
Arguments after --: --source-root PATH, --report PATH, optionally --base-test
PATH. Copy test_unity_export_blender.py beside this test when freezing inputs,
or pass its pinned copy explicitly. No worker, FBX round trip or Unity is run.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

import bpy


ROOT = Path(__file__).resolve().parents[1]
EXPORTER = LEGACY = PACKAGE_ROOT = None


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _package_root(source_root):
    root = Path(source_root).resolve()
    package = root if (root / 'unity_export.py').is_file() else root / 'character_designer'
    if not (package / '__init__.py').is_file():
        raise ValueError('Use the complete frozen character_designer package or its parent.')
    return package


def _loaded_sources(package):
    sources = {}
    for name, module in tuple(sys.modules.items()):
        if name != 'character_designer' and not name.startswith('character_designer.'):
            continue
        filename = getattr(module, '__file__', None)
        if filename is None:
            continue
        path = Path(filename).resolve()
        if not path.is_relative_to(package):
            raise AssertionError(f'Loaded source escaped the frozen package: {name}: {path}')
        sources[name] = {'path': str(path), 'sha256': _sha(path)}
    return sources


def _load(options):
    global EXPORTER, LEGACY, PACKAGE_ROOT
    PACKAGE_ROOT = _package_root(options.source_root)
    sys.path.insert(0, str(PACKAGE_ROOT.parent))
    package = importlib.import_module('character_designer')
    EXPORTER = importlib.import_module('character_designer.unity_export')
    _loaded_sources(PACKAGE_ROOT)
    base = options.base_test.resolve()
    if not base.is_file():
        raise ValueError('Provide the pinned original test with --base-test or copy it beside this test.')
    spec = importlib.util.spec_from_file_location('_cd_body_name_legacy_fixture', base)
    LEGACY = importlib.util.module_from_spec(spec)
    # The old fixture adds its own repository path. All production imports
    # must already resolve to the complete package above; restore search paths
    # afterwards so future relative imports also stay in that package.
    before_path = list(sys.path)
    try:
        spec.loader.exec_module(LEGACY)
    finally:
        sys.path[:] = before_path
    assert LEGACY.exporter is EXPORTER
    assert Path(package.__file__).resolve() == PACKAGE_ROOT / '__init__.py'
    assert Path(EXPORTER.__file__).resolve() == PACKAGE_ROOT / 'unity_export.py'
    _loaded_sources(PACKAGE_ROOT)
    return package, base


def _config_state(config):
    return config.directory, config.asset_id, config.filename


def _assert_named_target(fixture, filename):
    before = LEGACY.digest()
    config = _config_state(fixture.config)
    directory = Path(bpy.path.abspath(fixture.config.directory)).expanduser().resolve()
    existed = directory.exists()
    files = LEGACY.folder_bytes(directory)
    assert EXPORTER._model_body(bpy.context, fixture.rig) == fixture.body
    assert EXPORTER.model_filename(bpy.context, fixture.rig) == filename
    assert EXPORTER._target(fixture.config, fixture.rig, bpy.context) == (directory, filename)
    assert EXPORTER._target(fixture.config, fixture.rig) == (directory, filename)
    assert _config_state(fixture.config) == config, 'Name lookup changed export configuration'
    assert LEGACY.digest() == before, 'Name lookup changed artist scene data'
    assert directory.exists() == existed and LEGACY.folder_bytes(directory) == files


def _assert_rejected(fixture, fragment):
    before = LEGACY.digest()
    config = _config_state(fixture.config)
    directory = Path(fixture.config.directory)
    files = LEGACY.folder_bytes(directory)
    LEGACY.fail(lambda: EXPORTER.model_filename(bpy.context, fixture.rig), fragment)
    LEGACY.fail(lambda: EXPORTER._target(fixture.config, fixture.rig, bpy.context), fragment)
    assert _config_state(fixture.config) == config
    assert LEGACY.digest() == before
    assert LEGACY.folder_bytes(directory) == files


def test_body_object_name_authority(directory):
    with LEGACY.fixture(directory / 'body-authority') as f:
        f.body.name = 'ModelCosha'
        f.body.data.name = 'DifferentMeshData'
        f.rig.name = 'DifferentCharacterRig'
        f.config.filename = 'OldFolder/Invalid?Override'
        f.config.asset_id = 'unchanged-existing-profile'
        assert f.body.name not in {f.rig.name, f.body.data.name, f.config.filename}
        _assert_named_target(f, 'ModelCosha.fbx')


def test_body_rename_updates_target(directory):
    with LEGACY.fixture(directory / 'renamed-body') as f:
        f.config.filename = 'OldNameMustNotWin'
        _assert_named_target(f, 'Body.fbx')
        f.body.name = 'UpdatedCharacter'
        _assert_named_target(f, 'UpdatedCharacter.fbx')
        f.body.name = 'AlreadyHasExtension.FBX'
        _assert_named_target(f, 'AlreadyHasExtension.fbx')


def test_unicode_body_name(directory):
    with LEGACY.fixture(directory / 'unicode-body') as f:
        f.body.name = '角色_靴子_é'
        _assert_named_target(f, '角色_靴子_é.fbx')


def test_invalid_body_name_rejection(directory):
    with LEGACY.fixture(directory / 'invalid-body') as f:
        f.config.filename = 'ValidLegacyNameCannotRescueInvalidBody'
        for name in ('../escape', 'folder/model', 'folder\\model', 'NUL',
                     'CON.fbx', 'stream:name', 'broken?', 'bad*', 'trailing.'):
            f.body.name = name
            _assert_rejected(f, 'Rename the Body')


def test_missing_body_rejection(directory):
    with LEGACY.fixture(directory / 'missing-body') as f:
        f.source.body = None
        _assert_rejected(f, 'Body')


def test_wrong_setup_rig_rejection(directory):
    with LEGACY.fixture(directory / 'wrong-setup-rig') as f:
        f.source.rig = f.other
        _assert_rejected(f, 'Main Rig')


def test_offscene_body_rejection(directory):
    with LEGACY.fixture(directory / 'offscene-body') as f:
        data = bpy.data.meshes.new('ForeignSceneMesh')
        body = bpy.data.objects.new('ForeignSceneBody', data)
        foreign_scene = bpy.data.scenes.new('BodyNameForeignScene')
        foreign_scene.collection.objects.link(body)
        f.source.body = body
        assert body.name not in bpy.context.scene.objects
        _assert_rejected(f, 'Body')


def test_non_mesh_body_rejection(directory):
    with LEGACY.fixture(directory / 'non-mesh-body') as f:
        f.source.body = f.rig
        _assert_rejected(f, 'Body')


def test_unbound_body_precedes_target_and_worker(directory):
    from character_designer import hair_wiggle_adapter
    with LEGACY.fixture(directory / 'unbound-body') as f:
        f.source.body = f.extra
        f.config.asset_id = 'keep-existing-asset-id'
        before = LEGACY.digest()
        config = _config_state(f.config)
        assert not EXPORTER.export_running()
        with patch.object(hair_wiggle_adapter, 'status', return_value={'active': False}), \
                patch.object(EXPORTER, '_target', side_effect=AssertionError('Unbound Body reached destination lookup')):
            LEGACY.fail(lambda: EXPORTER.begin_export(bpy.context, f.rig, f.config), 'enabled Armature binding')
        assert not EXPORTER.export_running()
        assert LEGACY.digest() == before
        assert _config_state(f.config) == config
        assert not Path(f.config.directory).exists()


def test_named_target_ownership_and_meta_preservation(directory):
    with LEGACY.fixture(directory / 'ownership') as f:
        f.body.name = 'OwnedCharacter'
        f.config.filename = 'UnusedOldFilename'
        f.config.asset_id = 'current-owner'
        config = _config_state(f.config)
        target, filename = EXPORTER._target(f.config, f.rig, bpy.context)
        target.mkdir(parents=True)
        output = target / filename
        output.write_bytes(b'Existing character FBX fixture, not a real export')
        meta = target / (filename + '.meta')
        meta.write_bytes(b'guid: body-name-sentinel-must-survive')
        before = LEGACY.folder_bytes(target)
        LEGACY.fail(lambda: EXPORTER._previous(target, filename, f.config.asset_id), 'without')
        assert LEGACY.folder_bytes(target) == before
        manifest = target / (Path(filename).stem + '.cdesigner.json')
        record = {'schema': EXPORTER.SCHEMA, 'asset_id': 'foreign-owner',
                  'files': {filename: EXPORTER._hash(output)}}
        manifest.write_text(json.dumps(record), encoding='utf-8')
        before = LEGACY.folder_bytes(target)
        LEGACY.fail(lambda: EXPORTER._previous(target, filename, f.config.asset_id), 'different')
        assert LEGACY.folder_bytes(target) == before
        record['asset_id'] = f.config.asset_id
        record['files'][filename] = '0' * 64
        manifest.write_text(json.dumps(record), encoding='utf-8')
        before = LEGACY.folder_bytes(target)
        LEGACY.fail(lambda: EXPORTER._previous(target, filename, f.config.asset_id), 'outside')
        assert LEGACY.folder_bytes(target) == before
        record['files'][filename] = EXPORTER._hash(output)
        manifest.write_text(json.dumps(record), encoding='utf-8')
        before = LEGACY.folder_bytes(target)
        assert EXPORTER._previous(target, filename, f.config.asset_id) == record
        assert LEGACY.folder_bytes(target) == before
        assert meta.read_bytes() == b'guid: body-name-sentinel-must-survive'
        assert _config_state(f.config) == config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, default=ROOT / 'addons')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--base-test', type=Path,
                        default=Path(__file__).with_name('test_unity_export_blender.py'))
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    options = parser.parse_args(arguments)
    report = {'schema': 'unity-export-body-name-tests-v1',
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'blender_version': bpy.app.version_string,
              'blender_build_hash': bpy.app.build_hash.decode('ascii', errors='replace'),
              'script': {'path': str(Path(__file__).resolve()), 'sha256': _sha(__file__)},
              'worker_roundtrip_run': False, 'unity_project_used': False,
              'meta_scope': 'Sentinels in temporary fixtures only; exporter calls must preserve their bytes.',
              'results': [], 'passed': False}
    try:
        package, base = _load(options)
        report['source_root'] = str(PACKAGE_ROOT)
        report['addon_version'] = list(package.bl_info['version'])
        report['base_test'] = {'path': str(base), 'sha256': _sha(base)}
        tests = [test_body_object_name_authority, test_body_rename_updates_target,
                 test_unicode_body_name, test_invalid_body_name_rejection,
                 test_missing_body_rejection, test_wrong_setup_rig_rejection,
                 test_offscene_body_rejection, test_non_mesh_body_rejection,
                 test_unbound_body_precedes_target_and_worker,
                 test_named_target_ownership_and_meta_preservation,
                 LEGACY.test_character_scope, LEGACY.test_publication,
                 LEGACY.test_publication_rollback, LEGACY.test_report_messages]
        report['planned_test_count'] = len(tests)
        with tempfile.TemporaryDirectory(prefix='cdesigner-body-name-test-') as temporary:
            directory = Path(temporary)
            backups = directory / 'backups'
            backups.mkdir()
            original_resource = bpy.utils.user_resource

            def test_resource(kind, *, path='', create=False):
                if kind == 'DATAFILES' and path == 'character_designer/export_backups':
                    return str(backups)
                return original_resource(kind, path=path, create=create)

            with patch.object(bpy.utils, 'user_resource', test_resource), \
                    patch.object(EXPORTER.subprocess, 'Popen', side_effect=AssertionError('Worker launch forbidden in this suite')) as worker:
                for test in tests:
                    try:
                        test(directory)
                    except Exception:
                        report['results'].append({'name': test.__name__, 'passed': False,
                                                  'traceback': traceback.format_exc()})
                        raise
                    report['results'].append({'name': test.__name__, 'passed': True})
                    print('PASS ' + test.__name__, flush=True)
                worker.assert_not_called()
        report['passed'] = True
        report['new_body_name_tests'] = 10
        report['existing_no_worker_regressions'] = 4
    except Exception:
        report['error'] = traceback.format_exc()
        raise
    finally:
        if PACKAGE_ROOT is not None:
            report['loaded_sources'] = _loaded_sources(PACKAGE_ROOT)
        report['completed_test_count'] = sum(item['passed'] for item in report['results'])
        report['completed_utc'] = datetime.now(timezone.utc).isoformat()
        if options.report:
            options.report.parent.mkdir(parents=True, exist_ok=True)
            options.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('UNITY_EXPORT_BODY_NAME_TESTS_PASS 14 (10 new + 4 existing; no worker)', flush=True)


if __name__ == '__main__':
    main()
