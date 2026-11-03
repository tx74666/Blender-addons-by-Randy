"""Identified private CurrentRig dependency derivative of the pinned wrist fixture.

The inherited native Rest factory originally uses an unused packet marker. This
derivative supplies a real valid packet ONLY at its initial private _store input
boundary, before any history/checkpoint exists. No existing history is rebased,
no product admission is bypassed, and no artist file is opened or saved.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import bpy

ROOT = Path(r'D:\Blender\Projects\Character\X\validation\wrist_latency_20261102')
SUPPORT = ROOT / 'wrist_latency_support01.py'
SUPPORT_SHA = 'a7118686a50b53a11ef35529d6aae7a6675d2dac78e2fb12ec512c6acdc30e3f'


def require(value, message):
    if not value:
        raise AssertionError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def references(values):
    return {key: (value.bl_rna.identifier, value.name_full) for key, value in values.items()}


def mesh_state(mesh):
    return dict(vertices=[tuple(v.co) for v in mesh.data.vertices],
        polygons=[list(p.vertices) for p in mesh.data.polygons],
        groups=[(group.name, group.lock_weight) for group in mesh.vertex_groups],
        weights=[[(group.group, group.weight) for group in v.groups] for v in mesh.data.vertices],
        keys=[(key.name, key.value, [tuple(v.co) for v in key.data])
              for key in mesh.data.shape_keys.key_blocks] if mesh.data.shape_keys else [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    directory = args.evidence_dir.resolve(strict=True)
    require(directory.is_relative_to(ROOT) and not (directory / 'result.json').exists(), 'Use new private evidence.')
    require(sha(SUPPORT) == SUPPORT_SHA, 'Pinned support changed.')
    sys.path.insert(0, str(args.source_root.resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import character_designer
    character_designer.register()
    spec = importlib.util.spec_from_file_location('dependency_current_native_support', SUPPORT)
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
    api = support.api(args.source_root)
    from character_designer import animation_worklist_dependencies as dependencies
    import test_unity_animation_blender as packet_factory
    original_store = api.owner.target._store
    provisioned = []

    def initial_store(rig, record):
        if record['packet_path'] == 'unused-current-leg-rest-native-fixture.json':
            require(not provisioned and api.owner.target.SESSION not in rig,
                    'Initial derivative input boundary must precede all historical ownership.')
            packet_path, _expected, _raw = packet_factory.package(rig, directory, 'initial-private-packet.json')
            packet = api.owner.ua.load_package(packet_path)
            record = dict(record, packet_path=packet['_path'], packet_sha256=packet['_sha256'])
            provisioned.append(dict(path=str(packet_path), sha256=packet['_sha256'],
                                    boundary='initial private _store input only'))
        return original_store(rig, record)

    with patch.object(api.owner.target, '_store', initial_store):
        fixture = api.baseline.uniform_current(api, directory / 'fixture')
    require(len(provisioned) == 1, 'The initial valid-packet derivative did not execute exactly once.')
    row, rig = fixture.row, fixture.rig
    api.owner.revision.validate(bpy.context, row.item_id)
    checkpoint = fixture.action[api.owner.revision.CHECKPOINT]
    before = dict(session=rig[api.owner.target.SESSION],
        checkpoint=checkpoint[api.owner.revision.RECORD],
        refs=references(dict(checkpoint[api.owner.revision.REFS])),
        history=fixture.historical_witness(), body=fixture.body_witness(),
        mesh=mesh_state(fixture.mesh),
        source_file=row.source_file, source_hash=row.source_hash,
        source_fingerprint=api.owner.fingerprint.fingerprint_action(row.source_action, row.source_slot),
        custom_fingerprint=api.owner.fingerprint.fingerprint_action(row.custom_action, row.custom_slot),
        native_ids=(rig.as_pointer(), row.source_action.as_pointer(), row.custom_action.as_pointer()))
    original = directory / 'original'
    original.mkdir()
    saved_file = original / 'current.blend'
    require(bpy.ops.wm.save_as_mainfile(filepath=str(saved_file), check_existing=False) == {'FINISHED'}, 'Private save failed.')
    package = api.owner.worklist.package_dependencies(bpy.context, original / 'dependencies')
    result = api.owner.worklist.rebind_dependencies(bpy.context, package['manifest_path'])
    require(result['portable_paths'], 'CurrentRig native linked library paths are not portable.')
    require(rig[api.owner.target.SESSION] == before['session']
            and checkpoint[api.owner.revision.RECORD] == before['checkpoint'], 'Historical raw bytes changed.')
    require(fixture.historical_witness() == before['history'] and fixture.body_witness() == before['body'],
            'Relocation changed native history, paint, mesh or body owners.')
    require((rig.as_pointer(), row.source_action.as_pointer(), row.custom_action.as_pointer()) == before['native_ids'],
            'Relocation replaced a native Source, Custom or rig ID.')
    api.owner.revision.validate(bpy.context, row.item_id)
    # Source must be usable offline, including the deliberate missing original.
    source_cache = directory / 'fixture' / 'owned_source_cache'
    source_cache.rename(directory / 'fixture' / 'source-cache-retained')
    Path(provisioned[0]['path']).rename(directory / 'initial-private-packet-retained.json')
    api.owner.revision.validate(bpy.context, row.item_id)
    require(bpy.ops.wm.save_as_mainfile(filepath=str(saved_file), check_existing=False) == {'FINISHED'}, 'Private save failed.')
    moved = directory / 'moved'
    shutil.copytree(original, moved)
    original.rename(directory / 'original-retained')
    require(bpy.ops.wm.open_mainfile(filepath=str(moved / 'current.blend'), use_scripts=True) == {'FINISHED'}, 'Moved private file failed to open.')
    saved = api.owner.worklist.state(bpy.context)
    row, rig = saved.items[0], saved.rig
    api.owner.revision.validate(bpy.context, row.item_id)
    checkpoint = row.custom_action[api.owner.revision.CHECKPOINT]
    require(rig[api.owner.target.SESSION] == before['session']
            and checkpoint[api.owner.revision.RECORD] == before['checkpoint'], 'Native reopen altered immutable history.')
    require(references(dict(checkpoint[api.owner.revision.REFS])) == before['refs'], 'Native checkpoint reference identities changed.')
    require(row.source_file == before['source_file'] and row.source_hash == before['source_hash'], 'Logical Source identity changed.')
    require(api.owner.fingerprint.fingerprint_action(row.source_action, row.source_slot) == before['source_fingerprint'],
            'Native reopened Source curves differ.')
    require(api.owner.fingerprint.fingerprint_action(row.custom_action, row.custom_slot) == before['custom_fingerprint'],
            'Native reopened Custom curves differ.')
    mesh = next(obj for obj in bpy.context.scene.objects if obj.type == 'MESH' and obj.data.shape_keys)
    require(mesh_state(mesh) == before['mesh'], 'Native reopened geometry, weights or shape keys differ.')
    require(Path(dependencies.source_path(row)).is_relative_to(moved), 'Source did not resolve under moved package.')
    record = api.owner.target._load(rig)
    require(Path(dependencies.packet_path(rig, record)).is_relative_to(moved), 'Recovery packet did not resolve under moved package.')
    require(api.owner.ua.load_package(dependencies.packet_path(rig, record))['_sha256'] == record['packet_sha256'],
            'Reopened recovery packet content changed.')
    # Native Save As remaps native Library paths, while JSON retains its prior
    # relative string; the exact saved absolute fallback must keep both usable.
    saveas = directory / 'saveas'
    saveas.mkdir()
    require(bpy.ops.wm.save_as_mainfile(filepath=str(saveas / 'current.blend'), check_existing=False,
                                      relative_remap=True) == {'FINISHED'}, 'Private CurrentRig Save As failed.')
    row = api.owner.worklist.state(bpy.context).items[0]
    api.owner.revision.validate(bpy.context, row.item_id)
    record = api.owner.target._load(row.rig)
    require(api.owner.ua.load_package(dependencies.packet_path(row.rig, record))['_sha256'] == record['packet_sha256'],
            'Recovery packet unavailable after native CurrentRig Save As.')
    report = dict(passed=True, pid=os.getpid(), support_sha256=SUPPORT_SHA,
        script_sha256=sha(__file__), packet_factory_sha256=sha(packet_factory.__file__),
        initial_packet_derivative=provisioned, package=package, rebind=result,
        checks=['genuine CurrentRig revision.validate before/after', 'historical raw session byte-exact',
                'checkpoint byte-exact', 'native rig/Source/Custom IDs unchanged during relocation',
                'history/paint/shape-key owners unchanged during relocation',
                'original dependencies made unavailable', 'moved private save reopened',
                'native checkpoint references revalidated', 'Source and Custom curve fingerprints exact',
                'mesh/weights/shape-key arrays exact across private reopen',
                'recovery packet SHA verified under moved tree', 'CurrentRig native Save As fallback'],
        limits=['Private formal small CurrentRig fixture, not artist Original-session acceptance.',
                'Historical fixture row has no Source claim; no baseline was invented.',
                'Its unused initial packet marker was replaced before initial history creation; old history was never rewritten.'])
    (directory / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('DEPENDENCY_CURRENT_NATIVE_RESULT=' + json.dumps(report), flush=True)
    character_designer.unregister()


if __name__ == '__main__':
    main()
