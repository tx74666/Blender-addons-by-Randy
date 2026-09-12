"""Unified actions remain distinct from daily posing and Advanced maintenance."""
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
import character_designer
from character_designer import body_setup, body_setup_ui, limb_ik, eye_ui, torso_ui
from character_designer import root_control, limb_fk_visuals
import test_limb_ik_blender as base


class Layout:
    def __init__(self, records=None, *, alert=False, enabled=True):
        self.records = records if records is not None else []
        self.alert, self.enabled = alert, enabled

    def row(self, **_kwargs):
        return Layout(self.records, alert=self.alert, enabled=self.enabled)

    box = column = row

    def label(self, **kwargs):
        self.records.append(('label', kwargs.get('text', '')))

    def prop(self, _data, name, **_kwargs):
        self.records.append(('property', name))

    def prop_search(self, data, name, *_args, **kwargs):
        self.prop(data, name, **kwargs)

    def operator(self, name, **kwargs):
        result = SimpleNamespace()
        self.records.append(('operator', name, kwargs.get('text', ''), self.alert, self.enabled, result))
        return result


def draw(panel):
    layout = Layout()
    panel.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout.records


def buttons(records):
    return [r for r in records if r[0] == 'operator']


def main():
    character_designer.register()
    base.reset_scene()
    rig = base.make_humanoid()
    bpy.ops.object.mode_set(mode='EDIT')
    for side in ('L', 'R'):
        foot = rig.data.edit_bones['foot.' + side]
        base.add_bone(rig.data.edit_bones, 'toe.' + side, foot.tail.copy(),
                      foot.tail + Vector((0, -.13, 0)), foot)
    bpy.ops.object.mode_set(mode='POSE')
    settings = limb_ik._settings(bpy.context)
    assert not settings.show_body_setup_advanced
    rna = limb_ik.CharacterDesignerLimbIKState.bl_rna.properties['show_body_setup_advanced']
    assert rna.is_skip_save and not rna.default
    before = {b.name: b.matrix_local.copy() for b in rig.data.bones}
    initial = buttons(draw(limb_ik.CHARACTERDESIGNER_PT_limb_ik))
    assert initial[0][2] == 'Generate Body Setup' and not initial[0][3]
    assert initial[1][2] == 'Remove Generated Controls' and initial[1][3] and not initial[1][4]
    assert initial[0][5].action == 'GENERATE' and initial[1][5].action == 'REMOVE'
    assert not any(r[1] == 'character_designer.limb_ik_analyze' for r in initial)
    assert not hasattr(bpy.types, 'CHARACTERDESIGNER_PT_eye_controls')
    assert not any(r[1] == 'character_designer.eye_controls' for r in initial)
    assert ('label', 'Included in Body Setup') in draw(torso_ui.CHARACTERDESIGNER_PT_torso_controls)

    calls = []
    proxy = SimpleNamespace(action='REMOVE', execute=lambda _context: calls.append('execute'))
    context = SimpleNamespace(window_manager=SimpleNamespace(
        invoke_confirm=lambda operator, event: calls.append(('confirm', operator, event)) or {'RUNNING_MODAL'}))
    assert body_setup_ui.CHARACTERDESIGNER_OT_body_setup.invoke(proxy, context, 'event') == {'RUNNING_MODAL'}
    assert calls == [('confirm', proxy, 'event')]
    calls.clear()
    proxy.action = 'GENERATE'
    body_setup_ui.CHARACTERDESIGNER_OT_body_setup.invoke(proxy, context, 'event')
    assert calls == ['execute']

    assert bpy.ops.character_designer.body_setup('EXEC_DEFAULT', action='GENERATE') == {'FINISHED'}
    assert body_setup.has_generated(rig)
    generated_names = set(rig.data.bones.keys())
    assert bpy.ops.character_designer.body_setup('EXEC_DEFAULT', action='GENERATE') == {'FINISHED'}
    assert set(rig.data.bones.keys()) == generated_names
    records = draw(limb_ik.CHARACTERDESIGNER_PT_limb_ik)
    daily = buttons(records)
    assert daily[0][2] == 'Update Body Setup' and not daily[0][3]
    assert daily[1][2] == 'Remove Generated Controls' and daily[1][3] and daily[1][4]
    assert not any(r[1] in {'character_designer.root_control',
                           'character_designer.head_neck_visuals',
                           'character_designer.body_detail_visuals'} for r in daily)
    assert ('property', '["uniform_scale"]') not in records and ('property', 'scale') not in records
    assert any(r[1] == 'character_designer.limb_ik_fk_switch' for r in daily)
    assert not any(r[1] in {'character_designer.limb_ik_build_all', 'character_designer.limb_ik_remove',
                          'character_designer.limb_fk_visuals'} for r in daily)
    assert not any(getattr(r[5], 'action', '') in {'BUILD', 'REMOVE'} for r in daily[2:])
    saved_record = rig.data[limb_fk_visuals.RECORD_KEY]
    try:
        rig.data[limb_fk_visuals.RECORD_KEY] = 'invalid recovery record'
        damaged = Layout()
        body_setup_ui.draw_actions(damaged, bpy.context)
        assert buttons(damaged.records)[1][4], 'A damaged inventory must not hide removal'
    finally:
        rig.data[limb_fk_visuals.RECORD_KEY] = saved_record
    settings.selected_limb = 'LEFT_LEG'
    leg = buttons(draw(limb_ik.CHARACTERDESIGNER_PT_limb_ik))
    assert {'Foot Roll', 'Toe Bend'} <= {r[2] for r in leg}
    assert not any(r[2] in {'Arrow Placement', 'Remove Foot Controls', 'Add Foot Controls'} for r in leg)
    assert bpy.ops.character_designer.root_control(action='SELECT') == {'FINISHED'}
    assert rig.data.bones.active.name == root_control.control_name(rig)

    settings.show_body_setup_advanced = True
    maintenance = buttons(draw(limb_ik.CHARACTERDESIGNER_PT_limb_ik))
    assert not any(r[1] in {'character_designer.root_control',
                           'character_designer.head_neck_visuals',
                           'character_designer.body_detail_visuals'}
                   and getattr(r[5], 'action', '').startswith('SELECT') for r in maintenance)
    assert any(r[1] == 'character_designer.limb_ik_analyze' for r in maintenance)
    assert any(r[1] == 'character_designer.limb_ik_remove' for r in maintenance)
    assert any(r[2] == 'Set Up Eye Bones...' for r in maintenance)
    settings.show_body_setup_advanced = False
    assert bpy.ops.character_designer.body_setup('EXEC_DEFAULT', action='REMOVE') == {'FINISHED'}
    assert not body_setup.has_generated(rig)
    assert set(rig.data.bones.keys()) == set(before)
    for name, matrix in before.items():
        assert max(abs(rig.data.bones[name].matrix_local[i][j] - matrix[i][j])
                   for i in range(4) for j in range(4)) < 2e-6, name
    print('BODY_SETUP_UI_PASSED', flush=True)


if __name__ == '__main__':
    main()
