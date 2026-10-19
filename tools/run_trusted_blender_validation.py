"""Launch trusted validation with a new, isolated Blender CONFIG directory.

Factory startup alone does not isolate native Save Preferences. Even a GUI
Save Preferences action must target this launch's evidence directory.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

REPOSITORY = Path(__file__).resolve().parents[1]
X_PROJECT = Path(r'D:\Blender\Projects\Character\X')
SCRIPT_ROOTS = (REPOSITORY / 'tools', REPOSITORY / 'tests', X_PROJECT / 'validation')


def _user_config_source(blender, explicit, environment):
    if explicit is not None:
        return explicit.resolve(strict=True)
    if environment.get('BLENDER_USER_CONFIG'):
        return Path(environment['BLENDER_USER_CONFIG']).resolve(strict=True)
    # Infer only an unambiguous installed resource version, without launching
    # Blender. An explicit source is required for unusual/custom installations.
    versions = [path.name for path in blender.parent.iterdir()
                if path.is_dir() and re.fullmatch(r'\d+\.\d+', path.name)
                and (path / 'scripts').is_dir()]
    if len(versions) != 1 or not environment.get('APPDATA'):
        raise ValueError('Use --user-config-source to identify the saved startup/preferences directory.')
    return (Path(environment['APPDATA']) / 'Blender Foundation' / 'Blender'
            / versions[0] / 'config').resolve(strict=True)


def _config_copies(source):
    """Read and pin startup inputs; never modify their original files."""
    if not source.is_dir():
        raise ValueError('The user configuration source must be an existing directory.')
    if not (source / 'userpref.blend').is_file():
        raise ValueError('--user-startup requires an existing saved userpref.blend.')
    copies = {}
    for name in ('userpref.blend', 'startup.blend'):
        path = source / name
        if not path.exists():
            continue
        data = path.read_bytes()
        if path.read_bytes() != data:
            raise ValueError('A saved configuration input changed while being copied: ' + str(path))
        copies[name] = {'data': data, 'source': str(path.resolve(strict=True)),
                        'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    return copies


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', type=Path, default=Path(r'D:\Blender5.2\blender.exe'))
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--background', action='store_true')
    parser.add_argument('--user-startup', action='store_true',
                        help='Copy saved startup/preferences into an isolated CONFIG for a startup smoke check.')
    parser.add_argument('--user-config-source', type=Path,
                        help='Read-only source CONFIG for --user-startup; no preference changes are written back.')
    parser.add_argument('--stdout', type=Path, required=True)
    parser.add_argument('--stderr', type=Path, required=True)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('script_arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    script, blender = args.script.resolve(strict=True), args.blender.resolve(strict=True)
    if not any(script.is_relative_to(root.resolve()) for root in SCRIPT_ROOTS):
        parser.error('Use a local X/add-on development script in the authorized source directories.')
    if script.suffix != '.py':
        parser.error('--script must be a Python file.')
    outputs = [args.stdout.resolve(), args.stderr.resolve(), args.record.resolve()]
    if len(set(outputs)) != 3 or any(path.exists() for path in outputs):
        parser.error('Use three distinct new output filenames; existing evidence is preserved.')
    if any(not path.is_relative_to(X_PROJECT.resolve()) for path in outputs):
        parser.error('Keep X validation logs and launch records inside X.')
    config_path = outputs[2].with_name(outputs[2].stem + '_config')
    if (config_path in outputs or os.path.lexists(config_path)
            or not config_path.resolve().is_relative_to(X_PROJECT.resolve())):
        parser.error('Use a new record stem: its isolated CONFIG directory must not exist or escape X.')
    if args.user_config_source is not None and not args.user_startup:
        parser.error('--user-config-source requires --user-startup.')
    environment = dict(os.environ)
    source, copies = None, {}
    try:
        if args.user_startup:
            source = _user_config_source(blender, args.user_config_source, environment)
            copies = _config_copies(source)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    # A dedicated new directory protects explicit native preference saves as
    # well as autosave. Reject occupied paths, including empty dirs/symlinks.
    config_path.mkdir(exist_ok=False)
    if config_path.is_symlink() or not config_path.resolve().is_relative_to(X_PROJECT.resolve()):
        parser.error('The isolated CONFIG resolved outside the evidence workspace.')
    copied_files = {}
    for name, entry in copies.items():
        destination = config_path / name
        with destination.open('xb') as stream:
            stream.write(entry['data'])
        if destination.read_bytes() != entry['data'] or Path(entry['source']).read_bytes() != entry['data']:
            parser.error('The isolated configuration copy failed readback or its source changed.')
        copied_files[name] = {key: value for key, value in entry.items() if key != 'data'}
        copied_files[name]['isolated_copy'] = str(destination)
    environment['BLENDER_USER_CONFIG'] = str(config_path)
    command = [str(blender)]
    if not args.user_startup:
        command.append('--factory-startup')
    command.extend(('--enable-autoexec', '--threads', '1', '--python-exit-code', '1'))
    if args.background:
        command.append('--background')
    command.extend(('--python', str(script)))
    forwarded = args.script_arguments
    if forwarded[:1] == ['--']:
        forwarded = forwarded[1:]
    if forwarded:
        command.extend(('--', *forwarded))
    with outputs[0].open('xb') as stdout, outputs[1].open('xb') as stderr:
        process = subprocess.Popen(command, cwd=script.parent, stdout=stdout, stderr=stderr,
            env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    record = {'schema': 'x.trusted-blender-validation-launch/1',
        'started_utc': datetime.now(timezone.utc).isoformat(), 'pid': process.pid,
        'command': command, 'script_sha256': hashlib.sha256(script.read_bytes()).hexdigest(),
        'user_authorization': '2026-10-18: allow automatic scripts for the user-owned X/development workflow.',
        'script_auto_execution': 'enabled for this process; file-loading scripts must also use use_scripts=True',
        'preferences_saved_by_launcher': False,
        'factory_startup': not args.user_startup,
        'config_isolation': {'enabled': True, 'directory': str(config_path),
            'environment_variable': 'BLENDER_USER_CONFIG',
            'mode': 'copied_user_startup' if args.user_startup else 'empty_factory',
            'source_config': str(source) if source is not None else None,
            'copied_files': copied_files, 'original_config_written': False,
            'scope': 'CONFIG only; installed scripts and other resources retain their existing paths.'},
        'stdout': str(outputs[0]), 'stderr': str(outputs[1])}
    with outputs[2].open('x', encoding='utf-8') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'pid': process.pid, 'record': str(outputs[2])}))


if __name__ == '__main__':
    main()
