"""Focused native graph/owner guard follow-up, no exporter or child worker."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from test_animation_export_inputs_blender import fixture, expect_error, witness
from character_designer import animation as ui
from character_designer import animation_export as host
from character_designer import animation_export_inputs as inputs


def graph_guards():
    rig, action = fixture()
    inputs.capture(rig, action)
    driver = rig.animation_data.drivers[0]
    assert len(driver.keyframe_points) > 0
    driver.keyframe_points[0].co.y += .1
    expect_error(lambda: inputs.prepare(rig, action, frame_start=1.), 'changed')
    cases = 1
    for name in ('use_inherit_rotation', 'use_local_location', 'use_relative_parent'):
        rig, action = fixture()
        inputs.capture(rig, action)
        bone = rig.data.bones['Hand']
        setattr(bone, name, not getattr(bone, name))
        expect_error(lambda: inputs.prepare(rig, action, frame_start=1.), 'changed')
        cases += 1
    rig, action = fixture()
    inputs.capture(rig, action)
    bag = action.layers[0].strips[0].channelbags[0]
    bag.fcurves.new(data_path='pose.bones["Hand"].rotation_euler', index=1)
    expect_error(lambda: inputs.prepare(rig, action, frame_start=1.), 'Empty enabled')
    cases += 1
    rig, action = fixture()
    nested = rig.pose.bones['Hand'].constraints.new('ARMATURE')
    target = nested.targets.new()
    target.target, target.subtarget = rig, 'Hips'
    expect_error(lambda: inputs.capture(rig, action), 'Nested')
    assert inputs.RECORD not in action
    return cases + 1


def owner_guards():
    rig, action = fixture()
    item = NS(rig=rig, side='CUSTOM', custom_action=action, custom_slot=rig.animation_data.action_slot.handle,
              composition_json='', new_animation=True, source_action=None)
    saved = NS(workspace_path='fixture', items=[item])
    context = NS(scene=NS(objects=bpy.context.scene.objects, character_designer_animation_worklist=saved),
                 view_layer=bpy.context.view_layer, screen=None, object=rig, mode='OBJECT', window=None)
    from character_designer import animation_physics as physics, animation_worklist as worklist
    from character_designer import forearm_twist, control_pose_blend_ui as blend
    before = witness(rig, action)
    with patch.object(ui, '_link_idle', return_value=True), patch.object(physics, 'status', return_value={}), \
            patch.object(worklist, 'current', return_value=item):
        assert ui._export_base_owner(context, rig, action) == item.custom_slot
        item.side = 'SOURCE'
        expect_error(lambda: ui._export_base_owner(context, rig, action), 'Source')
        item.side = 'CUSTOM'
        other = action.copy()
        expect_error(lambda: ui._export_base_owner(context, rig, other), 'Activate')
        with patch.object(forearm_twist, '_SESSION', {'armature': rig}):
            expect_error(lambda: ui._export_base_owner(context, rig, action), 'Forearm')
        with patch.object(blend, '_ACTIVE_OPERATORS', [NS(_session={'rig': rig})]):
            expect_error(lambda: ui._export_base_owner(context, rig, action), 'Blend Pose')
        with patch.object(blend, '_ACTIVE_OPERATORS', [NS(_session={'rig': object()})]):
            assert ui._export_base_owner(context, rig, action) == item.custom_slot
    assert witness(rig, action) == before and inputs.RECORD not in action
    return 4


def rigging_dispatch(folder):
    # Minimal host dispatch regression only; not a real Rigging-session proof.
    rig, action = fixture()
    from character_designer import hair_wiggle_adapter
    before = witness(rig, action)
    dto = {'fixture': 'already-validated-upstream'}
    with patch.object(inputs, 'prepare', side_effect=AssertionError('Ordinary preflight replaced Rigging DTO')), \
            patch.object(host.subprocess, 'Popen') as launch, \
            patch.object(hair_wiggle_adapter, 'status', return_value={'active': False}):
        job = host._begin_export(bpy.context, rig, action, folder / 'rigging.fbx',
                                frame_start=1., frame_end=5., rigging_export=dto)
        command = launch.call_args.args[0]
        assert any(str(value).endswith('animation_export_worker.py') for value in command)
        spec = json.loads((job['root'] / 'job.json').read_text())
        assert inputs.JOB_KEY not in spec and inputs.JOB_KEY not in job and job['rigging_export'] == dto
    assert witness(rig, action) == before
    host._dispose(job)
    return True


def main():
    folder = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    value = {'ok': True, 'native_graph_refusals': graph_guards(), 'owner_refusals': owner_guards(),
             'rigging_host_dispatch_preserved_only': rigging_dispatch(folder),
             'no_child_worker': True, 'no_fbx_export': True}
    (folder / 'result.json').write_text(json.dumps(value, indent=2), encoding='utf-8')
    print('EXPORT_BASE_GUARDS_OK', json.dumps(value))


if __name__ == '__main__':
    main()
