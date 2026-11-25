"""One native INT16_2D attribute ABI fixture, no artist or Unity inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
import tempfile
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'addons/character_designer/unity_export.py'
FIXTURE = ROOT / 'tests/test_unity_rig_source_snapshot_blender.py'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(result):
    if result.exists(): raise RuntimeError('Preserve prior native evidence')
    result.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'x.rig-source-int16-native/1', 'source_sha256': sha(SOURCE),
        'script_sha256': sha(__file__), 'fixture_sha256': sha(FIXTURE),
        'blender_version': bpy.app.version_string, 'config': bpy.utils.user_resource('CONFIG'),
        'checks': [], 'snapshot_read_count': 0,
        'scope': 'One owned factory fixture; no artist/Unity/registration/deployment. Read witness checks literal attribute values plus the existing bounded fixture author/context witness.'}
    try:
        scope = runpy.run_path(str(FIXTURE), run_name='snapshot_fixture_only')
        rig, obj = scope['fixture']()
        attribute = obj.data.attributes.new(name='Native Signed Short Pair', type='INT16_2D', domain='POINT')
        initial = [(-32768, 32767), (-17, 23), (0, 0), (1234, -2345)]
        for item, pair in zip(attribute.data, initial): item.value = pair
        def values():
            return [list(item.value) for item in obj.data.attributes['Native Signed Short Pair'].data]
        report['native_attribute'] = {'data_type': attribute.data_type, 'domain': attribute.domain,
            'item_rna': attribute.data[0].bl_rna.identifier, 'values': values()}
        assert values() == [list(pair) for pair in initial], 'Native signed-short value assignment changed components'
        report['checks'].append('native_value_width2_signed_range')

        def read():
            before = values()
            try:
                return scope['digest'](scope['read'](rig, obj))
            finally:
                assert values() == before, 'Snapshot changed signed-short native values'
                report['snapshot_read_count'] += 1
        before = read()
        attribute.data[0].value = (-32767, 32767)
        assert read() != before, 'First INT16 component was omitted'
        report['checks'].append('first_component_change_detected')
        attribute.data[0].value = initial[0]
        assert read() == before, 'Restoring first component did not restore definition'
        attribute.data[0].value = (-32768, 32766)
        assert read() != before, 'Second INT16 component was omitted'
        report['checks'].append('second_component_change_detected')
        attribute.data[0].value = initial[0]
        assert read() == before
        with tempfile.TemporaryDirectory(prefix='int16-library-', dir=result.parent) as directory:
            library = Path(directory) / 'owned_int16.blend'
            bpy.data.libraries.write(str(library), {rig, obj}, path_remap='ABSOLUTE', fake_user=False, compress=False)
            bpy.ops.wm.open_mainfile(filepath=str(library), use_scripts=True)
            rig, obj = bpy.data.objects['Snapshot Rig'], bpy.data.objects['Snapshot Hair']
            assert values() == [list(pair) for pair in initial], 'Native library roundtrip changed signed-short values'
            assert read() == before, 'Native library roundtrip changed source definition'
        report['checks'].append('private_library_roundtrip_exact_values_and_definition')
        report['source_unchanged'] = sha(SOURCE) == report['source_sha256']
        report['passed'] = report['source_unchanged'] and len(report['checks']) == 4
    except Exception:
        report['passed'] = False
        report['error'] = traceback.format_exc()
    result.write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'result': str(result)}))
    if not report['passed']: raise RuntimeError('INT16_2D native check failed; see preserved evidence')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    main(args.result.resolve())
