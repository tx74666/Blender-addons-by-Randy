"""Explicit root entry for genuine two-A Sync, Loop Sync and a saved UI seed.

No process is launched on import. Run inside the authorized isolated Blender
process after Character hands over the serial native window. This entry never
opens or saves the artist X.blend and never rewrites a prepared source packet.
"""
import argparse
import ctypes
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback
import uuid

SCHEMA = 'x.validation.character-composition-first-stage/1'
FIELDS = {'schema', 'driverSha256', 'nativeFixture', 'physicalFixture',
          'loopAuthorFixture', 'tools', 'loopSyncDirectory', 'qaSeedFile'}
TOOLS = {'verify_character_composition_native', 'composition_sync_acceptance',
         'composition_timing_acceptance', 'composition_session_acceptance',
         'release_projection'}
PIN_FIELDS = {'path', 'sha256'}
MIN_AVAILABLE_BYTES = 200 * 1024 * 1024


def require(value, message):
    if not value:
        raise AssertionError(message)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def pinned(pin):
    require(type(pin) is dict and set(pin) == PIN_FIELDS
            and type(pin['path']) is str and Path(pin['path']).is_absolute()
            and type(pin['sha256']) is str and len(pin['sha256']) == 64,
            'Every source fixture/helper requires its explicit absolute path and SHA-256.')
    path = Path(pin['path']).resolve()
    require(path.is_file() and sha(path) == pin['sha256'], 'Pinned source changed: ' + str(path))
    return path


def validate_config(config):
    require(type(config) is dict and set(config) == FIELDS and config['schema'] == SCHEMA
            and type(config['tools']) is dict and set(config['tools']) == TOOLS,
            'Use the complete explicit first-stage fixture; actual prepared IDs are in its pinned child fixtures.')
    require(config['driverSha256'] == sha(__file__), 'The first-stage entry changed after fixture approval.')
    for field in ('nativeFixture', 'physicalFixture', 'loopAuthorFixture'):
        pinned(config[field])
    for name, pin in config['tools'].items():
        require(pinned(pin) == Path(__file__).resolve().with_name(name + '.py'),
                'Use the exact reviewed canonical helper: ' + name)
    for field in ('loopSyncDirectory', 'qaSeedFile'):
        require(type(config[field]) is str and Path(config[field]).is_absolute(),
                'Use explicit isolated output paths.')
    require(Path(config['qaSeedFile']).suffix.lower() == '.blend'
            and Path(config['qaSeedFile']).name.lower() != 'x.blend',
            'The UI seed must be a separate test .blend.')
    return config


class MemoryStatus(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                ('totalPhysical', ctypes.c_ulonglong), ('availablePhysical', ctypes.c_ulonglong),
                ('totalPageFile', ctypes.c_ulonglong), ('availablePageFile', ctypes.c_ulonglong),
                ('totalVirtual', ctypes.c_ulonglong), ('availableVirtual', ctypes.c_ulonglong),
                ('availableExtendedVirtual', ctypes.c_ulonglong)]


def memory_reading():
    require(os.name == 'nt', 'This root native fixture uses the actual Windows memory reader.')
    value = MemoryStatus()
    value.length = ctypes.sizeof(value)
    require(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)),
            'Cannot verify current memory before a heavy native operation.')
    return {'available_bytes': int(value.availablePhysical), 'total_bytes': int(value.totalPhysical),
            'used_percent': int(value.load), 'minimum_available_bytes': MIN_AVAILABLE_BYTES}


def check_memory(report, phase):
    reading = memory_reading()
    report.setdefault('resource_checks', []).append({'phase': phase, **reading})
    print('COMPOSITION_MEMORY ' + json.dumps({'phase': phase, **reading}), flush=True)
    if reading['used_percent'] >= 97:
        time.sleep(1.)
        reading = memory_reading()
        report['resource_checks'].append({'phase': phase + '_serial_recheck', **reading})
        print('COMPOSITION_MEMORY_RECHECK ' + json.dumps(reading), flush=True)
    require(reading['available_bytes'] >= MIN_AVAILABLE_BYTES,
            'Less than 200 MiB is available; no additional heavy operation was started.')


def run(config, report):
    validate_config(config)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    native, physical, timing = [importlib.import_module(name) for name in
        ('verify_character_composition_native', 'composition_sync_acceptance', 'composition_timing_acceptance')]
    load = lambda field: json.loads(pinned(config[field]).read_text(encoding='utf-8'))
    fixture = native.validate_fixture(load('nativeFixture'))
    sync = physical.validate_config(load('physicalFixture'))
    author = timing.validate_author_config(load('loopAuthorFixture'))
    require(len(sync['items']) == 2 and all(row['kind'] == 'SOURCE' for row in sync['items']),
            'First stage publishes the two actual SOURCE HoldPose variants.')
    require(uuid.UUID(author['holdLoopWorkItemId']) not in {uuid.UUID(value) for value in fixture['bWorkItemIds']}
            and author['expectedModelSha256'] == fixture['compositionModelSha256'],
            'The actual third Loop is distinct and uses the freshly prepared model.')
    evidence = Path(fixture['evidenceDirectory']).resolve()
    numeric_output = evidence / 'composition_native_report.json'
    require(not numeric_output.exists(), 'Keep prior numeric proof bytes; use fresh evidence.')
    workspace_snapshot = evidence / 'initial_workspace.json'
    require(not workspace_snapshot.exists(), 'Keep the exact initial prepared catalog bytes.')
    outputs = [Path(sync['evidenceDirectory']).resolve(), Path(author['evidenceDirectory']).resolve(),
               Path(config['loopSyncDirectory']).resolve(), Path(config['qaSeedFile']).resolve()]
    require(all(path.is_relative_to(evidence) and path != evidence for path in outputs)
            and len(set(outputs)) == len(outputs) and all(not path.exists() for path in outputs),
            'All first-stage outputs must be fresh distinct paths inside its owned evidence directory.')
    evidence.mkdir(parents=True, exist_ok=True)
    Path(author['evidenceDirectory']).mkdir(parents=True)
    workspace_source = native.pinned_file(fixture['compositionWorkspace'], fixture['compositionWorkspaceSha256'])
    workspace_snapshot.write_bytes(workspace_source.read_bytes())
    require(sha(workspace_snapshot) == fixture['compositionWorkspaceSha256'],
            'The initial workspace evidence copy differs from its actual prepared bytes.')
    check_memory(report, 'before_native_numeric')

    def before_worker(_context, item):
        check_memory(report, 'before_worker_' + item.work_item_id)

    def after_numeric(context, numeric):
        report['two_a_physical'] = physical.run(context, sync, numeric, before_worker=before_worker)
        check_memory(report, 'before_native_loop_authoring')
        report['loop_authoring'] = timing.author_hold_loop(context, author)
        proof = report['loop_authoring']['numeric_proof']
        report['loop_physical'] = physical.run_one(context, proof['authorization'],
            config['loopSyncDirectory'], proof, before_worker=before_worker)
        report['qa_seed'] = native.save_qa_seed(context, numeric, config['qaSeedFile'],
            physical_report_path=str(Path(sync['evidenceDirectory']) / 'composition_sync_report.json'))

    report['numeric'] = {'schema': native.REPORT_SCHEMA, 'completed': False,
                         'fixture': str(pinned(config['nativeFixture'])),
                         'fixture_sha256': config['nativeFixture']['sha256'],
                         'initial_workspace_snapshot': {'path': str(workspace_snapshot),
                             'sha256': fixture['compositionWorkspaceSha256']}}
    try:
        native.run(fixture, report['numeric'], after_numeric=after_numeric)
    finally:
        numeric_output.write_text(json.dumps(report['numeric'], ensure_ascii=False,
            allow_nan=False, indent=2), encoding='utf-8')
    require(report['numeric'].get('completed') is True, 'Historical original recovery did not complete.')
    report.update(completed=True, artist_save_performed=False,
                  original_scene_recovery_verified=True, source_packets_rewritten=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else sys.argv[1:]
    config_path = Path(parser.parse_args(argv).config).resolve()
    config = validate_config(json.loads(config_path.read_text(encoding='utf-8')))
    native_fixture = json.loads(pinned(config['nativeFixture']).read_text(encoding='utf-8'))
    output = Path(native_fixture['evidenceDirectory']).resolve() / 'composition_first_stage_report.json'
    require(not output.exists(), 'Retain earlier acceptance reports; use fresh evidence.')
    report = {'schema': 'x.validation.character-composition-first-stage-report/1',
              'completed': False, 'config': str(config_path), 'config_sha256': sha(config_path)}
    try:
        run(config, report)
    except Exception as error:
        report.update(error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
