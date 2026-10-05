"""Isolated factory proof for the bounded Worklist hot activation; no artist save."""

import argparse
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import bpy


VALIDATION = Path('D:/Blender/Projects/Character/X/Validation/animation_collection_20261005').resolve()
HOT_SCRIPT = VALIDATION / 'activate_minimal_live.py'
HOT_SHA = '2cc4929c15fc9fb9196be7b846991063871b9fdaba8b86f4d01a4e6280100225'
BASE_SHA = 'f3b00d2e92fa9954d7816aff9ff7bb099b06395cafffa5e274e1d69b6006f9e7'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unpack(path, destination, expected=None):
    """Write only reviewed package members to this already-owned sandbox."""
    files = {}
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            require(member.filename.startswith('character_designer/'), 'Unexpected archive prefix.')
            relative = Path(member.filename.removeprefix('character_designer/'))
            require(relative.parts and not relative.is_absolute() and '..' not in relative.parts,
                    'Unsafe archive member.')
            target = (destination / 'character_designer' / relative).resolve()
            require(target.is_relative_to(destination), 'Archive member escaped the owned sandbox.')
            name = relative.as_posix()
            require(name not in files, 'Duplicate archive member.')
            data = archive.read(member)
            files[name] = hashlib.sha256(data).hexdigest()
            if expected is not None:
                require(expected.get(name) == files[name], 'Candidate archive differs from inventory: ' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    if expected is not None:
        require(files == expected, 'Candidate archive omitted or added inventory files.')
    return files


def check_scan_ui(addon, fixture, fixture_hash, report_path):
    """Separate, disposable native UI QA after all hot-preservation assertions."""
    ui, worklist = addon.animation_worklist_ui, addon.animation_worklist
    window, original = bpy.context.window, bpy.context.scene
    workspace = original.character_designer_animation_worklist.workspace_path
    require(window is not None and workspace, 'The populated fixture needs an owning window/workspace.')
    require(not report_path.exists(), 'UI evidence is never overwritten.')
    actions = tuple(action.as_pointer() for action in bpy.data.actions)
    owned = bpy.data.scenes.new('Character Designer Scan UI QA')
    result = dict(status='failed', after_hot_preservation=True, saved=False, fixture=str(fixture))
    try:
        window.scene = owned
        saved = worklist.connect(bpy.context, workspace)  # Metadata only; no model or Action imports.
        require(not saved.items and not owned.objects, 'UI QA must start empty.')
        # A QA-only invalid row exercises the actual native button while baseline
        # validation blocks it before any Action fingerprint or export work.
        row = saved.items.add()
        row.name, row.item_id, row.clip_key = 'QA invalid row (no Action)', 'qa-invalid-item', 'qa-nonexistent-clip'
        require('FINISHED' in bpy.ops.character_designer.worklist_scan_changes(), 'Native Scan did not defer.')
        require(ui.scan_pending() and saved.status.startswith('Scanning animations')
                and not ui.CHARACTERDESIGNER_OT_worklist_scan_changes.poll(bpy.context)
                and not addon.animation._link_idle(), 'Pending feedback/reentry guard failed.')
        require(ui.CHARACTERDESIGNER_OT_worklist_cancel_collection.poll(bpy.context)
                and 'FINISHED' in bpy.ops.character_designer.worklist_cancel_collection()
                and not ui.scan_pending() and not bpy.app.timers.is_registered(ui._poll_scan), 'Owned Cancel failed.')
        require('FINISHED' in bpy.ops.character_designer.worklist_scan_changes(), 'Second native Scan did not defer.')
        require(ui._poll_scan() is None and saved.scan_completed and not saved.has_error
                and not ui.scan_pending() and row.scan_state == 'BLOCKED' and row.scan_reason
                and saved.status.startswith('Scan: 0 Changed, 0 Unchanged, 0 Unknown, 1 Blocked'),
                'Invalid-row native callback did not complete with Blocked feedback.')
        require(not addon.animation_worklist_collection.running() and addon.animation_export.active_job() is None,
                'UI QA unexpectedly started collection/export work.')
        result.update(status='passed', pending_feedback=True, cancel_before_callback=True,
                      blocked_entry_scan_completed=True, actions_imported=0, workers_started=0)
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        ui._cancel_pending_scan()
        if bpy.app.timers.is_registered(ui._poll_scan):
            bpy.app.timers.unregister(ui._poll_scan)  # Manual callback return is not processed by timer scheduler.
        window.scene = original
        bpy.data.scenes.remove(owned)
        result['owned_test_scene_discarded'] = True
        result['actions_untouched'] = actions == tuple(action.as_pointer() for action in bpy.data.actions)
        result['fixture_unchanged'] = sha(fixture) == fixture_hash
        if not result['actions_untouched'] or not result['fixture_unchanged']:
            result['status'] = 'failed'
        report_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
        require(result['actions_untouched'] and result['fixture_unchanged'], 'UI QA changed protected Action/file inputs.')


def main():
    require(bpy.app.background and not bpy.data.filepath, 'Use fresh --background --factory-startup only.')
    require('character_designer' not in sys.modules, 'Character Designer is already loaded.')
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--fail-after-all', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    fixture, output, inventory_path = (Path(value).resolve() for value in (
        args.fixture, args.output, args.inventory))
    require(fixture.is_relative_to(VALIDATION) and fixture.name == 'partial_worklist.blend'
            and fixture.is_file(), 'Use the generated private populated Worklist fixture only.')
    require(output.is_relative_to(VALIDATION) and output.suffix == '.json' and not output.exists(),
            'Use a fresh JSON report inside this Validation folder.')
    require(inventory_path.is_relative_to(VALIDATION), 'Inventory must belong to this private validation.')
    require(sha(HOT_SCRIPT) == HOT_SHA, 'Reviewed activation script changed; review before updating the hash.')
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    require(inventory['base_sha256'] == BASE_SHA and sha(inventory['base']) == BASE_SHA,
            'The frozen 0.76.2 base changed.')
    require(sha(inventory['candidate']) == inventory['candidate_sha256'], 'Candidate archive changed.')
    sandbox = (output.parent / 'sandbox').resolve()
    require(sandbox.is_relative_to(VALIDATION) and not sandbox.exists(), 'Sandbox must be fresh and owned here.')
    sandbox.mkdir(parents=True, exist_ok=False)
    addons = sandbox / 'addons'
    require(len(unpack(inventory['base'], addons)) == 144, 'Unexpected frozen base file count.')
    sys.path.insert(0, str(addons))
    addon = importlib.import_module('character_designer')
    require(Path(addon.__file__).resolve().is_relative_to(addons), 'Imported another installed add-on.')
    require(tuple(addon.bl_info['version']) == (0, 76, 2), 'The sandbox did not load 0.76.2.')
    addon.register()
    fixture_hash = sha(fixture)
    bpy.ops.wm.open_mainfile(filepath=str(fixture))
    require(Path(bpy.data.filepath).resolve() == fixture, 'Only the private fixture may open.')
    require(sum(len(scene.character_designer_animation_worklist.items) for scene in bpy.data.scenes) >= 2,
            'The native fixture must contain at least two saved Worklist rows.')
    old_classes = tuple(addon.CLASSES)
    old_pointer = bpy.types.Scene.bl_rna.properties['character_designer_animation_worklist'].fixed_type
    old_dirty = bpy.data.is_dirty
    require(len(unpack(inventory['candidate'], addons, inventory['file_sha256'])) == 147,
            'Unexpected candidate file count.')
    spec = importlib.util.spec_from_file_location('_cd_minimal_activation_reviewed', HOT_SCRIPT)
    hot = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = hot
    spec.loader.exec_module(hot)  # Definitions only; run is explicit below.
    report = hot.run(inventory_path, report_path=output,
                     _fail_at='after_all' if args.fail_after_all else None)
    expected = 'rolled_back' if args.fail_after_all else 'passed'
    require(report['status'] == expected, 'Activation/rollback failed: ' + json.dumps(report))
    require(report['preserved'] and report['old_registered_classes_preserved'], 'Native preservation failed.')
    require(not args.fail_after_all or report['rollback_complete'], 'Full injected rollback was not proven.')
    require(tuple(addon.CLASSES[:len(old_classes)]) == old_classes
            and all(addon._registered_rna_class(cls) is cls for cls in old_classes), 'Old class identity changed.')
    require(bpy.types.Scene.bl_rna.properties['character_designer_animation_worklist'].fixed_type == old_pointer,
            'Saved Worklist RNA pointer type changed.')
    require(Path(bpy.data.filepath).resolve() == fixture and bpy.data.is_dirty == old_dirty
            and sha(fixture) == fixture_hash and report['saved'] is False, 'The fixture was changed or saved.')
    require(sha(HOT_SCRIPT) == HOT_SHA, 'Activation script changed during validation.')
    if not args.fail_after_all:
        check_scan_ui(addon, fixture, fixture_hash, output.with_name(output.stem + '_ui.json'))
    print('MINIMAL_ACTIVATION_QA ' + expected + ' ' + str(output))


if __name__ == '__main__':
    main()
