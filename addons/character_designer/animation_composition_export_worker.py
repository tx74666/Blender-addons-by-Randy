"""Disposable staged B worker. The frozen .24 export guard is unchanged."""
import importlib.util
import importlib
import json
from pathlib import Path
import sys
import os
import traceback
from types import SimpleNamespace

STAGING = Path(__file__).resolve().parent
if (STAGING / '__init__.py').is_file():
    # Standalone Blender --python has no package context. Establish the exact
    # sibling package before importing helpers; refuse another installed copy.
    package = STAGING.name
    loaded = sys.modules.get(package)
    if loaded is not None and Path(loaded.__file__).resolve().parent != STAGING:
        raise ValueError('A different add-on package was loaded before the staged worker.')
    sys.path.insert(0, str(STAGING.parent))
    composition = importlib.import_module(package + '.animation_composition_export')
else:
    # Explicit isolated draft fixture seam only. Released workers always take
    # the package branch and have no dependency on X or a validation tree.
    mount = os.environ.get('CHARACTER_DESIGNER_COMPOSITION_TEST_ADDON_ROOT', '')
    if not mount or not (Path(mount) / '__init__.py').is_file() or Path(mount).name != 'character_designer':
        raise ValueError('Draft worker needs an explicit approved isolated test add-on mount.')
    sys.path.insert(0, str(Path(mount).resolve().parent))
    sys.path.insert(0, str(STAGING))
    composition = importlib.import_module('animation_composition_export')


def export_job(job):
    import bpy
    from mathutils import Matrix
    private = job.get('composition_job')
    composition._require(isinstance(private, dict), 'Missing canonical B worker proof.')
    path = Path(private['baseline_worker_file']).resolve()
    composition._require(path == (composition.BASELINE / 'animation_export_worker.py').resolve()
        and composition._sha256(path) == private['baseline_worker_sha256'] == composition.APPROVED_WORKER_SHA256
        and composition._sha256(path.with_name('unity_export_worker.py')) == private['baseline_model_sha256'] == composition.APPROVED_MODEL_SHA256
        and composition._sha256(bpy.data.filepath) == private['snapshot_sha256'],
        'Canonical B snapshot or frozen .24 exporter changed before evaluation.')
    spec = importlib.util.spec_from_file_location('_cd_composition_worker_07724', path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    # Model helper uses sibling-file imports; remaining CD helpers must resolve
    # only against this same frozen .24 package, never canonical pending drafts.
    sys.path.insert(0, str(path.parent.parent))
    rig, action = bpy.data.objects.get(job['rig']), bpy.data.actions.get(job['action'])
    composition._require(rig is not None and action is not None and not rig.parent,
                         'The isolated B rig or Action is missing.')
    raw = private['scope']
    canonical = {(row[0], row[1]): row[2] for row in raw['canonical_defaults']}
    scope = SimpleNamespace(native_names=tuple(raw['native_names']), path_by_native=raw['path_by_native'],
        channels=tuple(tuple(pair) for pair in raw['channels']), canonical_defaults=canonical,
        fixed_properties=tuple(tuple(row) for row in raw['fixed_properties']),
        object_matrix=Matrix(raw['object_matrix']), mapping=SimpleNamespace(
            indices=raw['mapping']['indices'], conversion=Matrix(raw['mapping']['conversion']),
            rest_world={name: Matrix(value) for name, value in raw['mapping']['rest_world'].items()}))
    wires = tuple(bpy.data.objects.get(name) for name in private['dependency_objects'])
    composition._require(all(wire is not None for wire in wires), 'An exact owned B wire is missing.')
    composition._require(set(bpy.data.objects) == {rig, *wires},
                         'Snapshot contains unowned objects; A evaluation was not excluded.')
    composition._require(set(bpy.data.actions) == {action},
                         'Snapshot contains unowned Actions; A evaluation was not excluded.')
    composition.validate_packet_composition(private['composition'], private['packet'])
    composition._require(composition.source_rest_sha256(private['packet']) == private['composition']['sourceRestSha256'],
                         'Worker B packet Rest identity changed.')
    composition._require(set((curve.data_path, curve.array_index) for curve in worker._curves(
        action, worker._slot(rig, action, job['action_slot']))) <= set(scope.channels),
        'Worker B Action contains out-of-mask input channels.')
    # Seed before any frame_set/snapshot_world in the baseline worker. These
    # writes affect only the unsaved disposable process, including unkeyed input.
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    for name, prop, value in scope.fixed_properties:
        rig.pose.bones[name][prop] = value
    rig.matrix_world = scope.object_matrix.copy()
    ad = rig.animation_data_create()
    for track in tuple(ad.nla_tracks):
        ad.nla_tracks.remove(track)
    ad.action = action
    slot = worker._slot(rig, action, job['action_slot'])
    if slot is not None:
        ad.action_slot = slot
    ad.use_nla, ad.action_blend_type, ad.action_influence = False, 'REPLACE', 1.
    composition._check_closed_dependencies(rig, wires, worker)
    scene = bpy.context.scene
    for obj in (rig, *wires):
        if obj.name not in scene.objects:
            scene.collection.objects.link(obj)
    scene.render.fps, scene.render.fps_base = job['fps'], job['fps_base']
    scene.unit_settings.scale_length = job['unit_scale']
    bpy.context.view_layer.update()
    measured = composition._sample_effective(bpy.context, rig, private['packet'], scope, private['composition'])
    composition._require(measured['effectiveValues'] == private['effective_values'] and
        measured['effectiveChannelsSha256'] == private['composition_export']['effectiveChannelsSha256'],
        'Disposable worker B values differ from the isolated preparation; publication refused.')
    result = worker.export_job(job)
    result.update(composition=private['composition'], composition_export=private['composition_export'],
                  effective_values=measured['effectiveValues'])
    composition.validate_export_receipt(result, private['composition'], private['composition_export'], measured['effectiveValues'])
    return result


def main():
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if len(args) != 1:
        raise ValueError('Expected one prepared composition export job JSON.')
    job = json.loads(Path(args[0]).read_text(encoding='utf-8'))
    result_path = Path(job['stage']) / 'result.json'
    try:
        result = export_job(job)
    except Exception as error:
        result_path.write_text(json.dumps({'ok': False, 'error': str(error),
            'traceback': traceback.format_exc()}), encoding='utf-8')
        raise
    result_path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    print('CDESIGNER_COMPOSITION_EXPORT_OK', json.dumps({'ok': True, 'effectiveChannelsSha256':
        result['composition_export']['effectiveChannelsSha256']}))


if __name__ == '__main__':
    main()
