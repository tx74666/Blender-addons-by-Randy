"""Ordinary export entry: apply a sealed base only in the disposable snapshot.

The established exporter remains byte-identical for Composition and historical
fixtures; this wrapper supplies the explicit sparse-Action input policy.
"""
import importlib.util
import json
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

import bpy


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def export_job(job):
    inputs = _load('_cd_export_base_inputs', 'animation_export_inputs.py')
    worker = _load('_cd_export_base_worker', 'animation_export_worker.py')
    rig = bpy.data.objects.get(job.get('rig', ''))
    action = bpy.data.actions.get(job.get('action', ''))
    # The established worker must authenticate Direct sources, stop Wiggle and
    # remove the owned Dress simulation contribution before binding an Action
    # or seeking a frame. Add our input policy at that existing private seam;
    # do not move any author/surface admission behind an evaluation event.
    original_helpers = worker._dress_snapshot_helpers
    applied = []

    def prepared_helpers():
        helpers = original_helpers()

        def prepare(*args, **kwargs):
            value = helpers.prepare_animation_snapshot(*args, **kwargs)
            if applied:
                raise ValueError('Export base-pose preparation was called more than once.')
            receipt = inputs.apply(rig, action, job.get(inputs.JOB_KEY))
            applied.append(receipt)
            frame = receipt['reference_frame']
            integer = int(frame // 1)
            bpy.context.scene.frame_set(integer, subframe=frame - integer)
            return value

        return SimpleNamespace(prepare_animation_snapshot=prepare)

    worker._dress_snapshot_helpers = prepared_helpers
    result = worker.export_job(job)
    if len(applied) != 1:
        raise ValueError('The ordinary exporter did not apply its sealed base-pose contract.')
    result[inputs.JOB_KEY] = applied[0]
    result['warnings'] = [value for value in result.get('warnings', ())
                          if 'Unkeyed controls use the snapshot values.' not in value]
    result['warnings'].append('Only the selected Action/slot is evaluated; NLA is excluded. '
                              'Unkeyed inputs use its explicitly captured export base pose.')
    return result


def main():
    arguments = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if len(arguments) != 1:
        raise ValueError('Expected one sealed animation export job path.')
    job = json.loads(Path(arguments[0]).read_text(encoding='utf-8'))
    result_path = Path(job['stage']) / 'result.json'
    try:
        result = export_job(job)
    except Exception as error:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({'ok': False, 'error': str(error),
            'traceback': traceback.format_exc()}, indent=2), encoding='utf-8')
        raise
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('CDESIGNER_ANIMATION_EXPORT_OK', json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
