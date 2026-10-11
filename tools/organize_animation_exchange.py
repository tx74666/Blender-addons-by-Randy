"""Create readable entries without moving the animation exchange's source data.

Default: inspect bounded metadata and write an evidence plan only. --write also
writes the explicit workspace reference, README and this tool's ownership record.
A separate Windows shortcut writer must verify existing TargetPath values against
expected_previous_target before creating/updating the planned .lnk files.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import runpy
import stat
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 1024 * 1024
OWNER_NAME = '.animation_exchange_owner.json'
OWNER_SCHEMA = 'cdesigner.readable-animation-entries-owner/1'
BATCH = re.compile(r'(\d{8})-(\d{6})-([0-9a-f]{12})')
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9]|LPT[1-9])', re.I)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def no_links(path):
    for entry in (path, *path.parents):
        try:
            attributes = entry.lstat()
        except FileNotFoundError:
            continue
        if entry.is_symlink() or getattr(attributes, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
            raise ValueError('Preserve the linked/junction path and choose a regular directory: ' + str(entry))


def absolute(path):
    result = Path(path).absolute()
    no_links(result)
    return result.resolve()


def child(output, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or ':' in str(relative):
        raise ValueError('Unsafe output entry: ' + str(relative))
    target = output / relative
    no_links(target)
    if not target.resolve().is_relative_to(output):
        raise ValueError('The output entry escapes its directory: ' + str(target))
    if len(str(target).encode('utf-16-le')) // 2 >= 250:
        raise ValueError('Choose a shorter readable output directory: ' + str(target))
    return target


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def bounded(path):
    no_links(path)
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Metadata exceeds 1 MiB: ' + str(path))
    record = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_json,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))
    if not isinstance(record, dict):
        raise ValueError('Expected metadata object: ' + str(path))
    return record, raw


def label(value, fallback):
    if not isinstance(value, str):
        raise ValueError('Readable label must be text.')
    name = INVALID_NAME.sub('_', value).strip().rstrip('. ')[:80].rstrip('. ')
    if not name or name in {'.', '..'}:
        name = fallback
    if DEVICE.fullmatch(name.split('.')[0].rstrip('. ')):
        name = fallback + ' ' + name
    return name


def json_bytes(record):
    return (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    no_links(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.entry-', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help='Real character_animation_workspace.json index.')
    parser.add_argument('--project-name', required=True, help='Readable project label, e.g. RandomRealm2.')
    parser.add_argument('--output', required=True, help='Dedicated readable character entry directory.')
    parser.add_argument('--evidence-dir', required=True, help='Evidence directory outside the source exchange/output.')
    parser.add_argument('--write', action='store_true', help='Write reference/README/ownership; never create shortcuts or change source data.')
    args = parser.parse_args(argv)
    source, output, evidence = map(absolute, (args.workspace, args.output, args.evidence_dir))
    if source.name != 'character_animation_workspace.json' or source.parent.parent.parent.name != 'Workspaces':
        raise ValueError('Choose the real index under CharacterTuning/Workspaces/<project>/<character>.')
    exchange = source.parent.parent.parent.parent
    if exchange.name != 'CharacterTuning' or evidence.is_relative_to(exchange) or evidence.is_relative_to(output):
        raise ValueError('Use an evidence directory outside CharacterTuning and the readable output.')
    if source.is_relative_to(output) or output == exchange or output.is_relative_to(exchange):
        raise ValueError('Place readable entries outside the original CharacterTuning directory.')
    if output.exists() and not output.is_dir():
        raise ValueError('The readable output is occupied by a file; it was preserved.')
    sources = []

    def read(path):
        record, raw = bounded(path)
        sources.append({'path': str(path), 'bytes': len(raw), 'sha256': digest(raw)})
        return record, raw

    sys.dont_write_bytecode = True
    reader_path = ROOT / 'addons' / 'character_designer' / 'animation_workspace.py'
    reader = runpy.run_path(str(reader_path))
    workspace, raw = read(source)
    reader['parse_workspace'](raw)
    reference = {key: workspace[key] for key in ('workspaceId', 'projectId', 'targetGuid', 'targetName')}
    reference.update(schema=reader['REFERENCE_SCHEMA'], workspaceManifest=str(source))
    reader['_parse_reference'](json_bytes(reference))
    owner_path = child(output, OWNER_NAME)
    owner = {'schema': OWNER_SCHEMA, 'reference': reference, 'files': {}, 'shortcuts': {}}
    old_owner_raw = None
    if owner_path.exists():
        previous, old_owner_raw = bounded(owner_path)
        if previous.get('schema') != OWNER_SCHEMA or previous.get('reference') != reference:
            raise ValueError('The readable directory belongs to another entry; it was preserved.')
        if not isinstance(previous.get('files'), dict) or not isinstance(previous.get('shortcuts'), dict):
            raise ValueError('The entry ownership record is invalid; it was preserved.')
        if any(not isinstance(value, str) for value in previous['shortcuts'].values()):
            raise ValueError('An owned shortcut has no valid target; it was preserved.')
        for relative in (*previous['files'], *previous['shortcuts']):
            child(output, relative)
        owner = previous
    current = {str(absolute(clip['linkManifest'])): clip for clip in workspace['clips'] if clip.get('linkManifest')}
    folders = sorted(path for path in exchange.iterdir() if path.is_dir() and BATCH.fullmatch(path.name))
    if len(folders) > 2048:
        raise ValueError('Too many exchange batches; choose a dedicated exchange root.')
    shortcuts, used, seen_current = [], set(), set()
    for folder in folders:
        no_links(folder)
        latest, _ = read(folder / 'character_designer_animation_latest.json')
        if latest.get('schema') != 'cdesigner.animation-latest/1':
            raise ValueError('Unsupported latest metadata: ' + str(folder))
        link_path = folder / 'character_animation_link.json'
        link = read(link_path)[0] if link_path.exists() else None
        legacy = link is None
        if link:
            if link.get('schema') != 'randomrealm.animation-link/1':
                raise ValueError('Unsupported Link metadata: ' + str(link_path))
            if link.get('targetGuid') != workspace['targetGuid']:
                continue
            if link.get('sourcePackage') != latest.get('file') or absolute(link['sourcePackage']).parent != folder:
                raise ValueError('The Link and latest pointer disagree: ' + str(folder))
        elif latest.get('targetName') not in {workspace['targetName'], workspace['targetName'] + '.Player'}:
            continue
        item = current.get(str(link_path))
        if item is not None:
            if link is None:
                raise ValueError('The current catalog has no Link: ' + str(link_path))
            for field in ('workItemId', 'clipGuid', 'clipLocalId', 'baseClipGuid', 'baseClipLocalId'):
                if item.get(field) != link.get(field):
                    raise ValueError('The current catalog and Link route disagree: ' + field)
            if workspace['modelSha256'] != link.get('modelSha256'):
                raise ValueError('The current catalog and Link model differ.')
            seen_current.add(str(link_path))
            section = 'Prepared'
            name = label(item['clipName'], 'Animation')
        else:
            section = 'History'
            match = BATCH.fullmatch(folder.name)
            stamp = datetime.strptime(match[1] + match[2], '%Y%m%d%H%M%S')
            name = label(latest['clipName'], 'Animation') + (' (Legacy)' if legacy else '')
            name += ' - ' + stamp.strftime('%Y-%m-%d %H-%M-%S UTC')
        stem, sequence = name, 1
        relative = section + '/' + stem + '.lnk'
        while relative.casefold() in used:
            sequence += 1
            relative = section + '/' + stem + ' ' + str(sequence) + '.lnk'
        used.add(relative.casefold())
        target = child(output, relative)
        previous_target = owner['shortcuts'].get(relative)
        if target.exists() and (not target.is_file() or previous_target is None):
            raise ValueError('An unowned shortcut path is occupied; it was preserved: ' + str(target))
        shortcuts.append({'relative_path': relative, 'path': str(target), 'target': str(folder),
                          'section': section, 'clip_name': latest['clipName'], 'legacy': legacy,
                          'link_id': link.get('linkId') if link else None,
                          'work_item_id': link.get('workItemId') if link else None,
                          'expected_previous_target': previous_target if target.exists() else None,
                          'verify_existing_target_before_write': target.exists(),
                          'operation': 'update_or_keep_owned' if target.exists() else 'create'})
    if seen_current != set(current):
        raise ValueError('A current catalog Link is outside the dated batches or was not found; preserve existing entries.')
    project_name = label(args.project_name, 'Project')
    reference_name = label(workspace['targetName'], 'Character') + '.workspace.json'
    readme = ('Animation entries: ' + project_name + ' / ' + workspace['targetName'] + '\n\n'
              'Open ' + reference_name + ' using Character Designer Connect. It reads the original workspace index.\n'
              'Prepared contains folder shortcuts for the current workspace catalog.\n'
              'History contains prior preparation folders; Legacy marks a packet without a Link manifest.\n'
              'Shortcuts open existing exchange folders; these entries do not move or copy animation data.\n'
              'Existing Custom edits, source paths and internal identities stay unchanged.\n'
              'After Unity Prepare changes the catalog or batch folders, rerun this organizer and its shortcut plan.\n'
              'The workspace reference reads the live index; folder shortcuts are a snapshot, with no watcher.\n'
              'Old shortcuts are preserved, including entries removed from a later catalog.\n'
              'The latest shortcut plan identifies current Prepared entries. History is not automatically imported.\n').encode('utf-8')
    writes = {reference_name: json_bytes(reference), 'README.txt': readme}
    destination_before = {}
    for relative, proposed in writes.items():
        path = child(output, relative)
        if path.exists():
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                raise ValueError('An entry path is occupied; it was preserved: ' + str(path))
            old = path.read_bytes()
            destination_before[relative] = digest(old)
            if old != proposed and owner['files'].get(relative) != digest(old):
                raise ValueError('An unowned or edited entry differs; it was preserved: ' + str(path))
        owner['files'][relative] = digest(proposed)
    for entry in shortcuts:
        # Do not advance an existing shortcut's ownership target before the
        # separate writer verifies and actually updates the Windows shortcut.
        owner['shortcuts'].setdefault(entry['relative_path'], entry['target'])
    for item in sources:
        _, raw = bounded(Path(item['path']))
        if digest(raw) != item['sha256']:
            raise ValueError('Source metadata changed during planning. Rerun with fresh inputs.')
    if folders != sorted(path for path in exchange.iterdir() if path.is_dir() and BATCH.fullmatch(path.name)):
        raise ValueError('Exchange batches changed during planning. Rerun with fresh inputs.')
    if owner_path.exists() and owner_path.read_bytes() != old_owner_raw:
        raise ValueError('Entry ownership changed during planning; rerun.')
    plan = {'schema': 'cdesigner.animation-exchange-shortcut-plan/1', 'mode': 'write' if args.write else 'dry-run',
            'workspace_manifest': str(source), 'output': str(output), 'reference': reference,
            'reference_path': str(child(output, reference_name)), 'source_metadata': sources,
            'current_catalog': [item['clipName'] for item in workspace['clips']], 'shortcuts': shortcuts,
            'counts': {'prepared': sum(item['section'] == 'Prepared' for item in shortcuts),
                       'history': sum(item['section'] == 'History' for item in shortcuts)},
            'organizer_sha256': digest(Path(__file__).read_bytes()),
            'reader_sha256': digest(reader_path.read_bytes()), 'destination_before': destination_before,
            'entry_files': {name: digest(raw) for name, raw in writes.items()}, 'owner_path': str(owner_path),
            'preserved_previous_shortcuts': sorted(set(owner['shortcuts']) - {item['relative_path'] for item in shortcuts}),
            'source_data_modified': False, 'packets_or_blends_read': False, 'shortcuts_written': False,
            'limits': ['Workspace reference reads the live source index; shortcuts require rerun after preparation changes.',
                       'Shortcut writer must verify existing TargetPath against expected_previous_target before writing.',
                       'After each verified shortcut write, update its ownership target in owner_path; the organizer does not advance existing targets.',
                       'No existing shortcut is deleted; no source path, identity, Custom edit or catalog is rewritten.']}
    if args.write:
        for relative, raw in writes.items():
            target = child(output, relative)
            current_hash = digest(target.read_bytes()) if target.exists() else None
            if current_hash != destination_before.get(relative):
                raise ValueError('An output entry changed after planning; it was preserved: ' + str(target))
            atomic_write(target, raw)
        atomic_write(owner_path, json_bytes(owner))
        loaded = reader['load_workspace'](child(output, reference_name))
        if loaded['_manifest_path'] != str(source) or loaded['workspaceId'] != workspace['workspaceId']:
            raise ValueError('Readable reference verification failed; preserve the written entries for inspection.')
    evidence.mkdir(parents=True, exist_ok=True)
    atomic_write(evidence / 'shortcut_plan.json', json_bytes(plan))
    print(json.dumps({'mode': plan['mode'], 'evidence': str(evidence / 'shortcut_plan.json'),
                      'current_catalog': plan['current_catalog'], **plan['counts'],
                      'reference_written': args.write, 'shortcuts_written': False}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print('Animation entry organization stopped: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
