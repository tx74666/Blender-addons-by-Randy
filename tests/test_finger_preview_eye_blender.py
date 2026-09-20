"""Ring preview eye follows the drawable overlay, independently of reference axes."""
import sys
import json
from pathlib import Path
from types import SimpleNamespace

import bpy
import bmesh

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_workflow_blender import bound_fixture, character_designer, C, bank, definition, layout, work, ui
from character_designer import finger_definition_ui as guide_ui


def fixture():
    ui.hide(); guide_ui.hide(invalidate=True)
    obj, rig, _ = bound_fixture()
    work.prepare(C)
    C.view_layer.update()
    ui.hide()
    return obj, rig


def eye():
    calls = []
    class Panel:
        def row(self, **kwargs): return self
        def box(self, **kwargs): return self
        def label(self, **kwargs): pass
        def prop(self, *args, **kwargs): pass
        def operator(self, name, **kwargs):
            op = SimpleNamespace()
            calls.append((op, kwargs))
            return op
    ui.draw_controls(Panel(), C)
    return next(kwargs for op, kwargs in calls if op.action == 'PREVIEW')


def expect(visible, enabled=None):
    enabled = visible if enabled is None else enabled
    assert ui.joint_preview_visible(C) is visible
    button = eye()
    assert button['icon'] == ('HIDE_OFF' if enabled else 'HIDE_ON')
    assert button['depress'] is enabled


def toggle():
    assert bpy.ops.character_designer.finger_workflow(action='PREVIEW') == {'FINISHED'}


def test_toggle_icons_and_reference_independence():
    obj, _ = fixture()
    baseline = layout.fingerprint(obj)
    objects = set(bpy.data.objects.keys())
    guide_ui.show(invalidate=False)
    frames = guide_ui.display_frames(C)
    expect(False)
    for _ in range(3):
        toggle(); expect(True)
        current = guide_ui.display_frames(C)
        assert guide_ui._visible and [f['path'] for f in current] == [f['path'] for f in frames]
        toggle(); expect(False)
        assert ui._preview is None and guide_ui._visible
    assert set(bpy.data.objects.keys()) == objects
    assert layout.fingerprint(obj) == baseline


def test_bend_is_not_ring_visibility_or_a_first_click_sink():
    fixture()
    ui.show_bend(C)
    expect(False)
    toggle(); expect(True)
    assert ui._preview['kind'] == 'RINGS'
    toggle(); expect(False)


def test_stale_finger_hidden_object_and_invalidation():
    obj, _ = fixture()
    ui.show(C); expect(True)
    bank.select(C, 'MIDDLE')
    work.prepare(C)
    expect(False, True)  # Do not draw Index as Middle; monitoring stays enabled.
    ui.refresh(C); ui._refresh(); expect(True)
    assert ui._preview['active'] == 'MIDDLE.L'
    obj.hide_set(True); expect(False, True)
    obj.hide_set(False)
    ui.show(C); expect(True)
    if not bpy.app.timers.is_registered(ui._refresh): bpy.app.timers.register(ui._refresh, first_interval=10.)
    ui._invalidate()
    expect(False, True)
    assert not bpy.app.timers.is_registered(ui._refresh)
    ui._refresh(); expect(False, True)
    ui._resume(); ui._refresh(); expect(True)
    ui._dirty(C.scene, SimpleNamespace(updates=[SimpleNamespace(
        id=obj.data, is_updated_geometry=True, is_updated_transform=False)]))
    expect(True)
    ui._refresh(); expect(True)


def test_button_drawing_is_scan_free_and_failure_keeps_monitoring_enabled():
    obj, _ = fixture()
    ui.show(C)
    original = work.plans
    def forbidden(*args, **kwargs): raise AssertionError('Drawing rebuilt geometry')
    work.plans = forbidden
    try:
        for _ in range(100): expect(True)
        toggle(); expect(False)  # Hiding is also scan-free.
    finally: work.plans = original
    def failed(*args, **kwargs): raise ValueError('Test: joint recipe is stale')
    work.plans = failed
    try:
        op = SimpleNamespace(action='PREVIEW', all_fingers=False, report=lambda *args: None)
        assert ui.CHARACTERDESIGNER_OT_finger_workflow.execute(op, C) == {'CANCELLED'}
        expect(False, True)
        assert 'stale' in obj.character_designer_finger_workflow.status
    finally: work.plans = original


def test_prepare_opens_and_pending_updates_do_not_clear_new_overlay():
    obj, _ = fixture()
    expect(False)
    assert bpy.ops.character_designer.finger_workflow(action='PREPARE') == {'FINISHED'}
    C.view_layer.update(); expect(True)
    ui.hide(); expect(False)
    assert bpy.ops.character_designer.finger_workflow(action='PREPARE') == {'FINISHED'}
    C.view_layer.update(); expect(True)
    # Deterministically model a queued read/prepare update at publication time.
    context = SimpleNamespace(screen=C.screen, view_layer=SimpleNamespace(update=lambda:
        ui._dirty(C.scene, SimpleNamespace(updates=[SimpleNamespace(
            id=obj.data, is_updated_geometry=True, is_updated_transform=False)]))))
    previous = ui._preview
    ui._install(context, obj, previous['groups'], previous['labels'], rig=previous['rig'])
    expect(True)
    # A notification alone must not close monitoring or discard a valid frame.
    ui._dirty(C.scene, SimpleNamespace(updates=[SimpleNamespace(
        id=obj.data, is_updated_geometry=True, is_updated_transform=False)]))
    expect(True)
    ui._refresh(); expect(True)


def test_pending_parameter_refresh_revalidates_but_manual_hide_cancels():
    obj, _ = fixture()
    ui.show(C)
    pair = obj.character_designer_finger_workflow.pairs['INDEX']
    pair.joints[0].root_weight = .79
    update = SimpleNamespace(updates=[SimpleNamespace(
        id=obj.data, is_updated_geometry=True, is_updated_transform=False)])
    ui._dirty(C.scene, update)
    expect(True)  # The geometry is unchanged: no blank frame while refreshing.
    assert ui._refresh_request is not None
    ui._refresh(); expect(True)
    pair.joints[0].root_weight = .78
    ui.hide()
    assert ui._refresh_request is None
    ui._refresh(); expect(False)
    ui.show(C)
    pair.joints[0].root_weight = .77
    bm = bmesh.from_edit_mesh(obj.data); bm.verts.ensure_lookup_table()
    record = json.loads(obj.character_designer_finger_bank.slots['INDEX.L'].guide.record)
    bm.verts[record['basis']['coordinates'][0][0]].co.z += .01
    bmesh.update_edit_mesh(obj.data)
    ui._dirty(C.scene, update)
    ui._refresh(); expect(True)
    assert ui._preview.get('stale')  # Kept only as an explicitly labelled reference.
    assert obj.character_designer_finger_workflow.status


def test_joint_drag_continuity_invalid_ranges_recovery_and_failed_apply():
    obj, _ = fixture()
    ui.show(C)
    _, state, pair = work.settings(C)
    baseline = layout.fingerprint(obj)
    objects = set(bpy.data.objects.keys())
    update = SimpleNamespace(updates=[SimpleNamespace(
        id=obj.data, is_updated_geometry=True, is_updated_transform=False)])
    initial = pair.joints[0].position
    paths = []
    for t in (.41, .48, .53, .65, .70, .99, .01, .45, initial):
        pair.joints[0].position = t
        ui._dirty(C.scene, update)
        expect(True)
        ui._refresh()
        expect(True)
        paths.append(ui._preview['groups'])
        assert ui._refresh_request is None
        if t in (.65, .70, .99, .01):
            assert state.status and any('adjust spacing' in label or 'boundary shown' in label
                                        for _, label, _ in ui._preview['labels'])
    assert paths[0] != paths[1] and paths[-2] != paths[-1]
    assert not state.status
    # A legal ring position with ambiguous/missing bone correspondence does
    # not hide either marker, and cannot authorize incorrect automatic weights.
    pair.joints[0].position = .50
    ui._refresh(); expect(True)
    assert 'weights unavailable' in state.status
    op = SimpleNamespace(action='APPLY', all_fingers=False, report=lambda *args: None)
    assert ui.CHARACTERDESIGNER_OT_finger_workflow.execute(op, C) == {'CANCELLED'}
    C.view_layer.update()
    expect(True)
    pair.joints[0].position = initial
    ui._refresh(); expect(True)
    assert not state.status
    # Failed topology generation also preserves visible corrective feedback.
    pair.joints[0].position = pair.joints[1].position
    ui._refresh(); expect(True)
    assert ui.CHARACTERDESIGNER_OT_finger_workflow.execute(op, C) == {'CANCELLED'}
    C.view_layer.update()
    expect(True)
    assert layout.fingerprint(obj) == baseline and set(bpy.data.objects.keys()) == objects
    ui.hide()
    pair.joints[0].position = initial
    ui._refresh(); expect(False)  # Dragging must not undo an explicit manual Hide.


def test_each_joint_moves_independently_and_latest_edit_wins():
    obj, _ = fixture()
    ui.show(C)
    _, _, pair = work.settings(C)
    start = ui._preview['groups']
    for t in (.68, .69, .70, .72): pair.joints[1].position = t
    assert bpy.app.timers.is_registered(ui._refresh)
    ui._refresh(); expect(True)
    assert ui._preview['groups'] != start
    entries = work.plans(C, preview=True)
    for _, _, _, plan in entries:
        assert abs(next(r['t'] for r in plan['rings'] if r['joint'] == 1 and not r['flank'])-.72) < 1e-6


def test_rig_transform_and_bone_edit_do_not_turn_off_monitor():
    obj, rig = fixture();ui.show(C)
    before=layout.fingerprint(obj)
    rig.location.x += .02
    C.view_layer.update();expect(True)
    ui._refresh();expect(True)
    assert not ui._preview.get('stale')
    # Move/rotate a rest bone in a separate armature Edit context.
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False);rig.select_set(True);C.view_layer.objects.active=rig
    bpy.ops.object.mode_set(mode='EDIT')
    bone=next(b for b in rig.data.edit_bones if 'index' in b.name.lower())
    bone.roll += .12
    C.view_layer.update();ui.refresh(C);ui._refresh()
    assert ui.joint_preview_enabled(C)
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.select_set(False);obj.select_set(True);C.view_layer.objects.active=obj
    bpy.ops.object.mode_set(mode='EDIT');ui.refresh(C);ui._refresh();expect(True)
    assert layout.fingerprint(obj)==before


def test_bend_flip_failed_loop_and_bank_switch_preserve_eye():
    obj,_=fixture();ui.show(C)
    ui.show_bend(C);expect(True)
    assert ui._bend_preview is not None
    assert bpy.ops.character_designer.finger_workflow(action='FLIP')=={'FINISHED'}
    ui._refresh();expect(True)
    op=SimpleNamespace(action='LOOP', all_fingers=False, report=lambda *args:None)
    assert ui.CHARACTERDESIGNER_OT_finger_workflow.execute(op,C)=={'CANCELLED'}
    expect(True)
    assert bpy.ops.character_designer.finger_setup(action='SELECT',digit='MIDDLE')=={'FINISHED'}
    assert ui.joint_preview_enabled(C)
    assert bpy.ops.character_designer.finger_workflow(action='PREPARE')=={'FINISHED'}
    ui._refresh();expect(True)
    assert ui._preview['active']=='MIDDLE.L'


def test_source_change_warns_keeps_eye_and_still_refuses_overwrite():
    obj,_=fixture();ui.show(C)
    _,state,pair=work.settings(C)
    pair.joints[0].position=.50;ui._refresh();expect(True)
    bm=bmesh.from_edit_mesh(obj.data);bm.verts.ensure_lookup_table()
    original=bm.verts[0].co.copy()
    bm.verts[0].co.z+=.02;bmesh.update_edit_mesh(obj.data)
    C.view_layer.update();ui._refresh();expect(True)
    assert ui._preview.get('stale') and state.status.startswith('Monitoring:')
    changed=layout.fingerprint(obj)
    op=SimpleNamespace(action='APPLY',all_fingers=False,report=lambda *args:None)
    assert ui.CHARACTERDESIGNER_OT_finger_workflow.execute(op,C)=={'CANCELLED'}
    assert layout.fingerprint(obj)==changed
    expect(True)
    # Restoring the source recovers on its notification, without reopening eye.
    bm.verts[0].co=original;bmesh.update_edit_mesh(obj.data)
    C.view_layer.update();ui._refresh();expect(True)
    assert not ui._preview.get('stale')
    toggle();expect(False)
    C.view_layer.update();ui._resume();ui._refresh();expect(False)


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'): test(); print('PASS', name, flush=True)
    print('FINGER_PREVIEW_EYE_PASS', flush=True)
