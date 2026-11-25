"""Private native tests of model-export current Hair proof after reference trim.

Uses tiny generated fixtures only. Never opens artist or Unity files. Set
X_HAIR_EXPORT_STAGE to the explicit private character_designer package directory
and X_HAIR_EXPORT_TEST_RESULT to a new JSON evidence path before trusted launch.
The transport markers below are fixture construction, not recovery authority.
"""
import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy

REPOSITORY = Path(__file__).resolve().parents[1]
STAGE = Path(os.environ['X_HAIR_EXPORT_STAGE']).resolve(strict=True)
RESULT = Path(os.environ['X_HAIR_EXPORT_TEST_RESULT']).resolve()
if RESULT.exists():
    raise RuntimeError('Use a new native result path')
sys.path.insert(0, str(STAGE.parent))
package = importlib.import_module('character_designer')
assert Path(package.__file__).resolve().parent == STAGE, 'Private package was not selected'
sys.path.insert(0, str(REPOSITORY / 'tests'))
from character_designer import hair_bones_rig as hair, hair_bones_binding as binding
from character_designer import hair_strand_registry as registry
from character_designer import hair_replace_authority as authority, animation_worklist_target as target
from character_designer import unity_export as exporter, unity_export_worker as worker
from test_hair_motion_export_blender import fixture
from test_hair_bones_rig_blender import activate
from test_unity_export_blender import digest


def primitive(value):
    if isinstance(value, bpy.types.ID):
        return [value.bl_rna.identifier, value.name_full, value.as_pointer()]
    if value is None or type(value) in (bool, str, int, float):
        return value
    if hasattr(value, 'items'):
        return {str(key): primitive(item) for key, item in sorted(value.items())}
    if hasattr(value, '__len__') and hasattr(value, '__getitem__'):
        return [primitive(value[index]) for index in range(len(value))]
    raise TypeError(type(value).__name__)


def fingerprint():
    result = {'native': digest(), 'properties': [], 'actions': []}
    for obj in bpy.data.objects:
        result['properties'].append((obj.name, primitive(dict(obj.items())),
                                     primitive(dict(obj.data.items())) if obj.data else None))
        if obj.type == 'ARMATURE':
            result['properties'].append((obj.name, [(bone.name, primitive(dict(bone.items())),
                bone.use_connect, bone.inherit_scale, bone.use_inherit_rotation,
                bone.use_local_location) for bone in obj.data.bones]))
    for action in bpy.data.actions:
        bags = [bag for layer in action.layers for strip in layer.strips for bag in strip.channelbags]
        curves = [curve for bag in bags for curve in bag.fcurves] if action.is_action_layered else action.fcurves
        result['actions'].append((action.name, primitive(dict(action.items())), [
            (curve.data_path, curve.array_index, [(list(point.co), list(point.handle_left),
                list(point.handle_right), point.interpolation) for point in curve.keyframe_points]) for curve in curves]))
    return hashlib.sha256(json.dumps(primitive(result), ensure_ascii=False, sort_keys=True).encode('utf8')).hexdigest()


def unchanged(call):
    before = fingerprint()
    try:
        return call()
    finally:
        assert fingerprint() == before, 'Read changed authored arrays/custom properties/context'


def refused(call, fragment=''):
    try:
        unchanged(call)
    except ValueError as error:
        assert fragment.casefold() in str(error).casefold(), str(error)
        return str(error)
    raise AssertionError('Expected conservative refusal')


def trim(f):
    # Model the existing explicit transported state. These markers do not grant
    # backend, Original, Action or recovery authority; ordinary read must refuse.
    f.source[binding.BINDING_KEY] = '{"private_fixture":true}'
    f.authority[authority.RECORD] = '{"private_fixture":"reviewed-reference-trim"}'
    f.authority[target.SESSION] = '{"private_fixture":"no-recovery-references"}'
    assert authority.REFS not in f.authority and target.REFS not in f.authority


def normal(directory):
    f = fixture(directory)
    ordinary = unchanged(lambda: registry.read(f.source, validate=True))
    assert unchanged(lambda: registry.read_for_model_export(f.source)) == ordinary


def transported(directory):
    f = fixture(directory)
    trim(f)
    refused(lambda: registry.read(f.source, validate=True), 'authority is incomplete')
    actual = unchanged(lambda: registry.read_for_model_export(f.source))
    assert actual == f.registry
    assert unchanged(lambda: registry._read_transported_selection(f.source)) == actual
    assert authority.REFS not in f.authority and target.REFS not in f.authority


def wrong_owner(directory):
    f = fixture(directory); trim(f)
    f.authority.data.bones[f.registry['strands'][0]['bones'][0]][hair.OWNER_KEY] = 'foreign-owner'
    refused(lambda: registry.read_for_model_export(f.source), 'ownership changed')


def changed_rest(directory):
    f = fixture(directory); trim(f)
    name = f.registry['strands'][0]['bones'][-1]
    activate(f.authority, 'EDIT')
    f.authority.data.edit_bones[name].tail.x += 0.01
    activate(f.source)
    refused(lambda: registry.read_for_model_export(f.source), 'Rest or segmentation changed')


def changed_topology(directory):
    f = fixture(directory); trim(f)
    f.source.data.edges.add(1)
    refused(lambda: registry.read_for_model_export(f.source))


def missing_group(directory):
    f = fixture(directory); trim(f)
    f.source.vertex_groups.remove(f.source.vertex_groups[f.registry['strands'][0]['bones'][0]])
    refused(lambda: registry.read_for_model_export(f.source), 'vertex group')


def invalid_mirror(directory):
    f = fixture(directory); trim(f)
    saved = json.loads(f.source[registry.REGISTRY_KEY])
    saved['strands'][0]['mirror_id'] = 'f' * 32
    f.source[registry.REGISTRY_KEY] = json.dumps(saved)
    refused(lambda: registry.read_for_model_export(f.source))


def snapshot_recheck(directory):
    f = fixture(directory); trim(f)
    capture = unchanged(lambda: exporter._capture_hair_motion([f.source, f.authority]))
    assert capture['registry'] == f.registry
    assert unchanged(lambda: worker._capture_hair_snapshot({'hair_motion': capture},
                      [f.source, f.authority])) == capture
    f.authority.data.bones[f.registry['strands'][0]['bones'][0]][hair.OWNER_KEY] = 'foreign-owner'
    refused(lambda: worker._capture_hair_snapshot({'hair_motion': capture},
             [f.source, f.authority]), 'ownership changed')


def private_save_reopen(directory):
    f = fixture(directory); trim(f)
    source_name = f.source.name
    file = directory / 'transported.blend'
    file.parent.mkdir(parents=True)
    assert bpy.ops.wm.save_as_mainfile(filepath=str(file)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(file), use_scripts=True) == {'FINISHED'}
    source = bpy.data.objects[source_name]
    activate(source)
    refused(lambda: registry.read(source, validate=True), 'authority is incomplete')
    assert unchanged(lambda: registry.read_for_model_export(source)) == f.registry
    assert authority.REFS not in source.get(hair.RIG_KEY) and target.REFS not in source.get(hair.RIG_KEY)


def main():
    report = {'schema': 'x.hair-model-export-reader-native/1', 'passed': False,
              'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'pid': os.getpid(), 'blender_version': bpy.app.version_string,
              'stage': str(STAGE), 'cases': [],
              'scope': 'Tiny isolated generated native fixtures. Model export reader + worker snapshot proof only; no artist, Unity, deployment, release, evaluated deformation or recovery authority.'}
    tests = (normal, transported, wrong_owner, changed_rest, changed_topology,
             missing_group, invalid_mirror, snapshot_recheck, private_save_reopen)
    try:
        with tempfile.TemporaryDirectory(prefix='hair-model-export-reader-') as temporary:
            root = Path(temporary)
            for test in tests:
                test(root / test.__name__)
                report['cases'].append({'name': test.__name__, 'passed': True})
                print('PASS', test.__name__, flush=True)
        report['passed'] = True
    except Exception as error:
        report.update(error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        with RESULT.open('x', encoding='utf8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print('HAIR_MODEL_EXPORT_READER_NATIVE_OK', len(report['cases']), flush=True)


if __name__ == '__main__':
    main()
