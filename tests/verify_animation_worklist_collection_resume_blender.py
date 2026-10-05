"""Resume only the approved R7 private checkpoint; no import, re-edit or artist save.

Fresh factory background Blender: --python THIS_FILE -- --output NEW_VALIDATION_DIR
Optional --cancel-file must be inside that new output. Runtime proofs stay native.
"""

import argparse
import ctypes
import importlib
import json
from pathlib import Path
import sys
import time
import traceback

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_animation_worklist_collection_blender import ARTIST_FILE, plain_scene, sha


ROOT = Path('D:/Blender/Projects/Character/X/Validation/animation_collection_20261005').resolve()
FIXTURE = ROOT / 'native_r7_20261005_214820/worklist.blend'
PRIOR = FIXTURE.parent / 'collection_native_qa.json'
DIAGNOSIS = ROOT / 'r7_checkpoint_diagnosis.json'
INVENTORY = ROOT / 'candidate_source_r7_inventory.json'
PINS = {
    FIXTURE: '63e9b01d6cf55e6f15d7a4e71b4597b9e9bd58d6227a3ba70387685f1745f168',
    PRIOR: 'fe6ace66afa2e185912a49cc496d6ac1dd42a16e5c5b34d03a9c7dacc77693b3',
    DIAGNOSIS: '3962eb9b1eee783969dcc2e07eeb5cf89566396e14b93cf40992cdf099018aff',
    INVENTORY: 'fd139864fd07de15d8d4f614fe8e7028889daabd63b7f0f169aba30c6fae90d3',
}
LINK_IDS = {'Idle': '82a9b6fad735432fb05214f078e4a8eb', 'Walk_N': '21aa9c9ba9f6455c948004907cba4729'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cancel-file', type=Path)
    options = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    output = options.output.resolve()
    if not output.is_relative_to(ROOT) or output.exists() or output == ROOT:
        raise ValueError('Use a fresh owned output directory under this Validation root.')
    cancel_file = options.cancel_file.resolve() if options.cancel_file else None
    if cancel_file is not None and not cancel_file.is_relative_to(output):
        raise ValueError('Cancellation sentinel must belong to this output.')
    output.mkdir(parents=True)
    report_path = output / 'collection_resume_native_qa.json'
    started, native, stage = time.monotonic(), None, 'Pinned R7 gate'
    deadline = started + 600.
    report = dict(passed=False, checks=[], errors=[], timings=[], scans=[], memory_checks=[],
                  worker_processes=[], runner_sha=sha(__file__), output=str(output),
                  fixture=str(FIXTURE), evidence={str(path): digest for path, digest in PINS.items()})
    protected, processes, seen, checkpoints = {}, [], set(), []

    def flush():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

    def check(name, condition, details=None):
        report['checks'].append(dict(name=name, passed=bool(condition), details=details))
        flush()
        if not condition:
            raise AssertionError(name)

    def budget():
        if cancel_file is not None and cancel_file.exists():
            report['cancelled'] = True
            raise InterruptedError('Owned resume cancelled; completed publications will be retained.')
        if time.monotonic() > deadline:
            raise TimeoutError('The resume exceeded its 600 second budget.')

    def memory_gate(label):
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(name, ctypes.c_ulonglong)
                for name in ('total_physical', 'available_physical', 'total_pagefile', 'available_pagefile',
                             'total_virtual', 'available_virtual', 'extended')]
        value = MemoryStatus()
        value.length = ctypes.sizeof(value)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            raise OSError('GlobalMemoryStatusEx failed.')
        report['memory_checks'].append(dict(stage=label, available_bytes=value.available_physical,
            load_percent=value.load, at=time.monotonic() - started))
        flush()
        if value.available_physical < 200 * 1024 * 1024:
            raise MemoryError('Less than 200 MiB available before ' + label)

    def publications(saved):
        result = {}
        for item in saved.items:
            path = Path(item.manifest_path).resolve()
            check('Link remains in the exact approved private root', path.is_relative_to(qa_root))
            link = native.animation_link.load_link(path)
            result[str(path)] = dict(revision=link.get('revision', 0), manifest_sha=sha(path))
            for field in ('fbxFile', 'metadataFile'):
                if link.get(field):
                    candidate = Path(link[field]).resolve()
                    check('Publication stays within approved private root', candidate.is_relative_to(qa_root))
                    result[str(candidate)] = dict(sha=sha(candidate), bytes=candidate.stat().st_size)
        return result

    def scan(label):
        budget()
        memory_gate(label)
        began = time.monotonic()
        counts = native.animation_worklist_collection.scan_changes(bpy.context)
        report['timings'].append(dict(stage=label, seconds=time.monotonic() - began))
        report['scans'].append(dict(stage=label, counts=counts,
            items=[dict(name=item.name, state=item.scan_state, reason=item.scan_reason) for item in saved.items]))
        flush()
        budget()
        return counts

    try:
        check('Fresh factory background only', bpy.app.background and '--factory-startup' in sys.argv
              and not bpy.data.filepath and 'character_designer' not in sys.modules)
        check('Exact immutable approved evidence', all(path.is_relative_to(ROOT) and sha(path) == digest
              for path, digest in PINS.items()))
        prior = json.loads(PRIOR.read_text(encoding='utf-8'))
        diagnosis = json.loads(DIAGNOSIS.read_text(encoding='utf-8'))
        inventory = json.loads(INVENTORY.read_text(encoding='utf-8'))
        check('Only the diagnosed inactive-scene evaluation check failed', prior['passed'] is False
              and [entry['name'] for entry in prior['checks'] if not entry['passed']] == ['Factory author assets survive checkpoint']
              and len(prior['errors']) == 1 and prior['errors'][0]['stage'] == 'Save/reopen edited worklist'
              and prior['errors'][0]['message'] == 'Factory author assets survive checkpoint')
        required = {'Add Ready completed', 'Repeated Add Ready preserves Actions and membership',
                    'Actions, slots, sources, membership and receipt survive reopen',
                    'Exact native Action/Link association survives reopen', 'Baseline receipt records a complete native proof'}
        check('Import, eight full proofs, both edits and Save/Reopen already passed', required.issubset(
              {entry['name'] for entry in prior['checks'] if entry['passed']})
              and sum(entry['name'] == 'Native Action content is inspectable' and entry['passed'] for entry in prior['checks']) == 8
              and sum(entry['name'] == 'Custom edit changed only its own motion' and entry['passed'] for entry in prior['checks']) == 2)
        check('Diagnosis proves exact raw and evaluated state without tolerances', diagnosis['raw_exact'] is True
              and diagnosis['evaluated_exact_after_update'] is True and diagnosis['before'] == diagnosis['after_update']
              and Path(diagnosis['fixture']).resolve() == FIXTURE)
        protected = {entry['path']: entry['before'] for entry in prior['protected_inputs']}
        check('Protected cache/model/Source inputs still exact', all(entry['before'] == entry['after']
              and sha(entry['path']) == entry['before'] for entry in prior['protected_inputs']))
        check('Artist remains exact and was never the checkpoint', Path(prior['artist_file']).resolve() == ARTIST_FILE
              and prior['artist_sha_before'] == prior['artist_sha_after'] == sha(ARTIST_FILE))
        protected[str(ARTIST_FILE)] = prior['artist_sha_before']
        protected.update({str(path): digest for path, digest in PINS.items()})
        qa_root = Path(prior['qa_publication_root']).resolve()
        check('Approved publication root is the existing private AW pair',
              str(qa_root) == 'D:\\Unity Projects\\RandomRealm2\\Logs\\CharacterTuning\\AW\\Pairs\\d948988d')
        candidate = Path(inventory['candidate']).resolve()
        check('Exact frozen R7 candidate archive', candidate.is_relative_to(ROOT / 'candidate_source_r7')
              and inventory['candidate_sha256'] == 'f7c0961da6281414609021b06dbe56459aff5382733c49f9c98f2f19a7c0191c'
              and sha(candidate) == inventory['candidate_sha256'])
        addons = candidate.parent.parent / 'addons'
        package = addons / 'character_designer'
        actual_files = {path.relative_to(package).as_posix() for path in package.rglob('*')
                        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
        check('All 147 frozen runtime files match inventory', len(inventory['file_sha256']) == 147
              and actual_files == set(inventory['file_sha256']) and all(sha(package / name) == digest
              for name, digest in inventory['file_sha256'].items()))
        budget()
        memory_gate('Open approved private checkpoint')
        sys.path.insert(0, str(addons))
        native = importlib.import_module('character_designer')
        check('Loaded only frozen R7', Path(native.__file__).resolve() == package / '__init__.py'
              and tuple(native.bl_info['version']) == (0, 76, 3))
        native.register()
        native._validate_registration_integrity()
        for name in ('animation', 'animation_export', 'animation_link', 'animation_worklist', 'animation_worklist_collection'):
            importlib.import_module('character_designer.' + name)
        check('Only approved private checkpoint opens', 'FINISHED' in bpy.ops.wm.open_mainfile(filepath=str(FIXTURE))
              and Path(bpy.data.filepath).resolve() == FIXTURE)
        saved = native.animation_worklist.state(bpy.context)
        edits = {edit['clip']: edit for edit in prior['custom_edits']}
        check('Two exact saved rows and Link UUIDs', len(saved.items) == 2 and set(edits) == set(LINK_IDS)
              and {item.name for item in saved.items} == set(LINK_IDS)
              and all(item.item_id == edits[item.name]['item_id']
                      and json.loads(item.link_identity)['linkId'] == LINK_IDS[item.name] for item in saved.items))
        for item in saved.items:
            native.animation_worklist._check_baseline(item)
            native.animation_worklist._slot(item.source_action, item.source_slot)
            native.animation_worklist._slot(item.custom_action, item.custom_slot)
        check('One shared rig, read-only Sources and local independent Customs', all(
              item.rig is saved.rig and item.source_action.library is not None and not item.source_action.is_editable
              and item.custom_action.library is None and item.custom_action.name == saved.target_name + ' · ' + item.name + ' · Custom'
              for item in saved.items) and len({item.custom_action.as_pointer() for item in saved.items}) == 2)
        check('Evaluated factory author state is exactly original', plain_scene(bpy.data.scenes['Scene']) == diagnosis['before'])
        initial = publications(saved)
        check('Baseline publications still match completed R7', initial == prior['publication_baseline'])
        protected.update({path: value['sha'] for path, value in initial.items() if 'sha' in value})
        report.update(prior_report=str(PRIOR), candidate_addons=str(addons), publication_baseline=initial,
                      custom_edits=prior['custom_edits'], reused_full_proofs=8)
        stage = 'Resume edited scan'
        check('One explicit Unknown and one Changed', scan(stage) == dict(CHANGED=1, UNCHANGED=0, UNKNOWN=1, BLOCKED=0))
        for item in saved.items:
            item.sync_selected = item.scan_state in {'UNKNOWN', 'CHANGED'}
        queue = native.animation_worklist_collection.begin_sync_changed(bpy.context)
        check('Both edits queued with Unknown explicitly selected', queue.total == 2)
        stage, item_started, completed = 'Resume serial Sync', {}, set()
        while queue.busy:
            budget()
            job = native.animation_export.active_job()
            if job is not None:
                token = (job['process'].pid, job['started'])
                if token not in seen:
                    check('Real workers stay serial', all(process.poll() is not None for process in processes))
                    seen.add(token)
                    processes.append(job['process'])
                    report['worker_processes'].append(dict(pid=job['process'].pid, action=job['action'], destination=str(job['destination'])))
                native.animation._poll_action_export()
            if queue.running is None and queue.remaining_keys:
                memory_gate('Next owned Sync')
                item_started.setdefault(queue.remaining_keys[0], time.monotonic())
            native.animation_worklist_collection._poll_collection()
            for success in queue.successes:
                if success.token not in completed:
                    completed.add(success.token)
                    report['timings'].append(dict(stage=stage, key=success.key, seconds=time.monotonic() - item_started[success.key]))
                    flush()
            time.sleep(.05)
        native.animation_worklist_collection._poll_collection()
        check('Two native Sync publications completed', queue.state == 'COMPLETED' and len(queue.successes) == 2
              and len(processes) == 2 and all(process.poll() == 0 for process in processes))
        receipts = [json.loads(item.last_synced_receipt) for item in saved.items]
        check('Both successful receipts contain full native proof', all(receipt['proof']['fingerprint_known'] for receipt in receipts))
        final = publications(saved)
        check('Both exact Link revisions advanced', all(final[item.manifest_path]['revision'] > initial[item.manifest_path]['revision']
              and native.animation_link.load_link(item.manifest_path)['linkId'] == LINK_IDS[item.name] for item in saved.items))
        stage = 'Resume unchanged no-op'
        check('Both receipts scan truly Unchanged', scan(stage) == dict(CHANGED=0, UNCHANGED=2, UNKNOWN=0, BLOCKED=0))
        no_op = native.animation_worklist_collection.begin_sync_changed(bpy.context)
        check('Repeated Sync has no worker or publication', no_op.total == 0 and not no_op.busy
              and native.animation_export.active_job() is None and publications(saved) == final)
        check('Factory author remains exactly intact', plain_scene(bpy.data.scenes['Scene']) == diagnosis['before'])
        budget()
        stage = 'Save new owned resume output'
        result = output / 'worklist.blend'
        check('Save only a new owned worklist', 'FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(result), copy=False, check_existing=False))
        checkpoints.append(str(result))
        report.update(passed=True, publication_final=final, receipts=receipts,
                      worklist_file=dict(path=str(result), sha=sha(result), bytes=result.stat().st_size))
    except Exception as exc:
        report['errors'].append(dict(stage=stage, type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc()))
        if native is not None:
            try:
                native.animation_worklist_collection.stop()
                if native.animation_export.active_job() is not None:
                    native.animation_export.cancel_export()
                if Path(bpy.data.filepath).resolve() == FIXTURE:
                    partial = output / 'partial_worklist.blend'
                    if 'FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(partial), copy=False, check_existing=False):
                        checkpoints.append(str(partial))
            except Exception as cleanup:
                report['errors'].append(dict(stage='Preserve resume', message=str(cleanup)))
    finally:
        if native is not None:
            for callback in (native.animation_worklist_collection._poll_collection, native.animation._poll_action_export):
                if bpy.app.timers.is_registered(callback):
                    bpy.app.timers.unregister(callback)
        report['protected_inputs'] = [dict(path=path, before=digest, after=sha(path) if Path(path).is_file() else None)
                                      for path, digest in protected.items()]
        unchanged = all(entry['before'] == entry['after'] for entry in report['protected_inputs'])
        report['checks'].append(dict(name='Approved checkpoint/evidence, artist and all previous outputs remain exact', passed=unchanged))
        report['passed'] = report['passed'] and unchanged
        report.update(checkpoints=checkpoints, elapsed_seconds=time.monotonic() - started)
        flush()
        print('COLLECTION_RESUME_NATIVE_QA ' + json.dumps(dict(passed=report['passed'], report=str(report_path))))
    if not report['passed']:
        raise RuntimeError('Native resume failed; inspect ' + str(report_path))


if __name__ == '__main__':
    main()
