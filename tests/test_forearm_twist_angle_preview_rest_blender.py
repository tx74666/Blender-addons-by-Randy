"""Rest-display lifecycle derivative; all files belong to a private fixture."""
import json
import math
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

import bpy
from mathutils import Vector


output = Path(sys.argv[sys.argv.index('--') + 1])
state = runpy.run_path(str(Path(__file__).with_name('test_forearm_twist_angle_preview_reference_blender.py')))
preview, runtime, session = (state[name] for name in ('preview', 'runtime', 'session'))
geometry = state['state']['geometry']
mesh, arm = session['mesh'], session['armature']
source_name, arm_name = mesh.name, arm.name
runtime._SESSION = session


def measured(_context):
    current_arm = runtime._SESSION['armature']
    evaluated = current_arm.evaluated_get(bpy.context.evaluated_depsgraph_get())
    transforms = [evaluated.pose.bones[name].matrix @ current_arm.data.bones[name].matrix_local.inverted()
                  for name in ('lower', 'hand')]
    return geometry.twist_angle(*transforms, Vector((0, 1, 0)))


runtime.current_twist_angle = measured


def author_inputs(current_arm):
    return {bone.name: preview._author_pose(bone) for bone in current_arm.pose.bones}


def start_rest():
    mesh[runtime.PREVIEW_KEY] = json.dumps({'target': 'hand'})
    arm.data.pose_position = 'REST'
    bpy.context.view_layer.update()
    return author_inputs(arm)


records = []
for label in ('Action Cancel', 'Action Confirm release'):
    before = start_rest()
    assert preview.release(bpy.context, runtime, session) is False
    assert arm.data.pose_position == 'REST'
    for degrees in (90, -120, 120, 0):
        result = preview.set_angle(bpy.context, runtime, session, math.radians(degrees))
        assert arm.data.pose_position == 'POSE'
        assert session['angle_preview']['original_pose_position'] == 'REST'
        assert session['angle_preview']['pose_position_changed'] is True
        assert author_inputs(arm) == before
        records.append({'label': label, 'degrees': degrees, **result})
    assert preview.release(bpy.context, runtime, session)
    assert arm.data.pose_position == 'REST'
    bpy.context.view_layer.update()
    assert author_inputs(arm) == before

before = start_rest()
with patch.object(preview, '_constraint_signature', side_effect=RuntimeError('injected Rest capture failure')):
    try:
        preview.set_angle(bpy.context, runtime, session, .5)
        raise AssertionError('Rest capture failure was swallowed')
    except RuntimeError:
        pass
assert arm.data.pose_position == 'REST'
assert preview.SOURCE_REF not in mesh
assert author_inputs(arm) == before
shared = bpy.data.objects.new('Actual shared Rest Armature', arm.data)
bpy.context.scene.collection.objects.link(shared)
try:
    preview.set_angle(bpy.context, runtime, session, .5)
    raise AssertionError('Shared Rest data was accepted')
except preview.AnglePreviewError as exc:
    assert 'shared Armature data' in str(exc)
assert arm.data.pose_position == 'REST' and preview.SOURCE_REF not in mesh
bpy.data.objects.remove(shared, do_unlink=True)

hand = arm.pose.bones['hand']
driver = hand.driver_add('rotation_quaternion', 0)
driver.driver.expression = '.92'
before = start_rest()
for degrees in (-90, 110):
    result = preview.set_angle(bpy.context, runtime, session, math.radians(degrees))
    assert arm.data.pose_position == 'POSE'
    assert author_inputs(arm) == before
    records.append({'label': 'Driver', 'degrees': degrees, **result})
assert driver.driver.expression == '.92'
assert preview.release(bpy.context, runtime, session)
assert arm.data.pose_position == 'REST' and author_inputs(arm) == before

# Serialized markers and native ID links survive a real save/reopen, without
# writing the artist file or registering any background service.
before = start_rest()
preview.set_angle(bpy.context, runtime, session, .6)
private_file = output / 'private_rest_preview.blend'
assert not private_file.exists()
bpy.ops.wm.save_as_mainfile(filepath=str(private_file), check_existing=False)
bpy.ops.wm.open_mainfile(filepath=str(private_file), use_scripts=True)
mesh, arm = bpy.data.objects[source_name], bpy.data.objects[arm_name]
marker = json.loads(mesh[runtime.PREVIEW_KEY])
assert marker['angle_preview']['original_pose_position'] == 'REST'
session = {'mesh': mesh, 'armature': arm, 'target': 'hand', 'angle_preview': marker['angle_preview']}
runtime._SESSION = session
assert preview.release(bpy.context, runtime, session)
assert arm.data.pose_position == 'REST'
bpy.context.view_layer.update()
assert author_inputs(arm) == before

# Undo restores the temporary POSE display plus its original REST receipt;
# losing the Python session then uses exactly the serialized cleanup contract.
bpy.context.preferences.edit.use_global_undo = True
before = start_rest()
session['chain'] = ['upper', 'lower', 'hand']
assert bpy.ops.ed.undo_push(message='Before Rest angle preview') == {'FINISHED'}
preview.set_angle(bpy.context, runtime, session, .7)
assert bpy.ops.ed.undo_push(message='Active Rest angle preview') == {'FINISHED'}
preview.release(bpy.context, runtime, session)
assert bpy.ops.ed.undo_push(message='Released Rest angle preview') == {'FINISHED'}
assert bpy.ops.ed.undo() == {'FINISHED'}
mesh, arm = bpy.data.objects[source_name], bpy.data.objects[arm_name]
marker = json.loads(mesh[runtime.PREVIEW_KEY])
assert arm.data.pose_position == 'POSE'
assert marker['angle_preview']['original_pose_position'] == 'REST'
session = {'mesh': mesh, 'armature': arm, 'target': 'hand', 'angle_preview': marker['angle_preview']}
runtime._SESSION = session
assert preview.release(bpy.context, runtime, session)
assert arm.data.pose_position == 'REST'
bpy.context.view_layer.update()
assert author_inputs(arm) == before
summary = {'module_rest_native_pass': True, 'angles': records, 'save_reopen_restored': True,
           'native_undo_marker_restored': True, 'shared_rest_data_refused': True,
           'failed_creation_restored_rest': True, 'scope': 'Module API; full runtime Confirm/Cancel remains integration validation.'}
(output / 'result.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
print('FOREARM_ANGLE_REST_PASS', json.dumps(summary), flush=True)
