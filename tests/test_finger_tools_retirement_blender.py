"""Retired joint topology stays absent; retained finger tools remain independent."""
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import (
    bound_fixture, hands_fixture, capture_all, fingerprint, character_designer,
    C, bank, definition, targets, rig_state, assert_rig_equal,
)
from test_finger_topology_adapt_blender import split_ring, spatial_state, assert_frames
from character_designer import finger_bone_tools as bones, finger_definition_ui as guides


RETIRED_MODULES = {
    'finger_workflow', 'finger_workflow_ui', 'finger_workflow_rebase',
    'finger_layout', 'finger_layout_ui', 'finger_joint', 'finger_joint_plan',
    'finger_ring_slide', 'finger_subdivision', 'finger_preview_proof',
}
RETIRED_PROPERTIES = {
    'character_designer_finger_joint', 'character_designer_finger_layout',
    'character_designer_finger_workflow',
}
RETIRED_OPERATORS = (
    'finger_joint', 'finger_layout', 'finger_workflow',
)


def _assert_retired_absent():
    for owner in (bpy.types.Object, bpy.types.Scene, bpy.types.WindowManager):
        for prop in RETIRED_PROPERTIES:
            assert not hasattr(owner, prop), (owner, prop)
    for name in RETIRED_OPERATORS:
        try:
            getattr(bpy.ops.character_designer, name).get_rna_type()
        except (KeyError, RuntimeError, AttributeError):
            pass
        else:
            raise AssertionError('Retired operator remains registered: '+name)
    for name in RETIRED_MODULES:
        assert 'character_designer.'+name not in sys.modules, name
    for name in dir(bpy.app.handlers):
        callbacks = getattr(bpy.app.handlers, name)
        if isinstance(callbacks, list):
            assert all(getattr(fn, '__module__', '').rsplit('.', 1)[-1] not in RETIRED_MODULES
                       for fn in callbacks), name


def _refresh_bend():
    if bones._request is not None:
        bones._refresh()


def test_no_retired_rna_operators_modules_or_callbacks():
    _assert_retired_absent()
    assert hasattr(bpy.types.Object, 'character_designer_finger_bank')
    assert hasattr(bpy.types.Scene, 'character_designer_finger_definition')
    bpy.ops.character_designer.finger_bone_tools.get_rna_type()
    bpy.ops.character_designer.mirror_selected_region.get_rna_type()
    calls = []

    class Layout:
        def row(self, **_): return self
        def column(self, **_): return self
        def box(self): return self
        def label(self, **kwargs): calls.append(('label', kwargs.get('text', '')))
        def operator(self, name, **kwargs):
            calls.append((name, kwargs.get('text', '')))
            return SimpleNamespace()

    obj, _, _ = bound_fixture()
    before = fingerprint(obj)
    bones.draw_controls(Layout(), C)
    assert {text for _, text in calls} >= {
        'Bone Chain', 'Align Active Finger', 'Relax Bones', 'Bone Roll',
        'Calibrate All', 'Calibrate Selected', 'Preview Bend',
    }
    assert not any('Joint Topology' in text or 'Support Rings' in text or 'Prepare Joints' in text
                   for _, text in calls)
    assert all(name in {'label', 'character_designer.finger_bone_tools', 'character_designer.finger_loop_marks'}
               for name, _ in calls)
    assert fingerprint(obj) == before


def test_basic_capture_and_manual_loop_adaptation_without_joint_setup():
    obj, chosen = hands_fixture()
    before = fingerprint(obj)
    capture_all(obj, chosen)
    assert fingerprint(obj) == before
    bank.select(C, 'INDEX', 'L')
    accepted = spatial_state(obj)
    split_ring(obj)
    edited = fingerprint(obj)
    result = bank.adapt_topology(C)
    assert result['adapted'] == ['INDEX.L'] and not result['failed'], result
    survey = json.loads(obj.character_designer_finger_bank.survey)
    assert len(survey['candidates']['INDEX.L']['rings']) == 8
    assert len(survey['candidates']['INDEX.R']['rings']) == 7
    assert not survey['warnings'] and spatial_state(obj) == accepted
    assert_frames(obj)
    assert fingerprint(obj) == edited, 'Adapting references changed the artist mesh'
    _assert_retired_absent()


def test_bend_master_eye_and_warm_side_switches_are_cached_and_read_only():
    bones.hide()
    obj, rig, _ = bound_fixture()
    C.scene.character_designer_finger_definition.overlays_enabled = True
    guides.show(invalidate=False)
    guides.display_frames(C)
    bones.show_bend(C)
    left = bones._preview
    assert left and left['side'] == 'L' and len(left['labels']) == 1
    bpy.ops.character_designer.finger_setup(action='SIDE')
    guides.display_frames(C)
    _refresh_bend()
    right = bones._preview
    assert right and right['side'] == 'R'
    bpy.ops.character_designer.finger_setup(action='SIDE')
    guides.display_frames(C)
    _refresh_bend()
    assert bones._preview is left
    before, rest = fingerprint(obj), rig_state(rig)
    # Snapshot reads above may notify the depsgraph; settle before guarding warm
    # controls so the measured operations have stable inputs.
    C.view_layer.update()
    if bones._request is not None: _refresh_bend()
    bones.show_bend(C)
    left = bones._preview
    bpy.ops.character_designer.finger_setup(action='SIDE')
    _refresh_bend()
    right = bones._preview
    bpy.ops.character_designer.finger_setup(action='SIDE')
    _refresh_bend()
    left = bones._preview
    with patch.object(definition, '_snapshot', side_effect=AssertionError('Warm eye scanned geometry')), \
         patch.object(definition, 'frame', side_effect=AssertionError('Warm eye rebuilt a frame')), \
         patch.object(targets, 'resolve', side_effect=AssertionError('Warm side resolved bones')):
        for _ in range(20):
            bpy.ops.character_designer.finger_definition(action='OVERLAYS')
            assert bones._preview is None and bones._request is None
            bpy.ops.character_designer.finger_definition(action='OVERLAYS')
            assert bones._preview is left
        for _ in range(10):
            bpy.ops.character_designer.finger_setup(action='SIDE')
            assert bones._preview is right
            bpy.ops.character_designer.finger_setup(action='SIDE')
            assert bones._preview is left
    assert fingerprint(obj) == before
    assert_rig_equal(rest, rig_state(rig))
    # Explicitly hiding Bend stays hidden across a cycle of the master eye.
    assert bpy.ops.character_designer.finger_bone_tools(action='BEND') == {'FINISHED'}
    assert bones._intent is None
    bpy.ops.character_designer.finger_definition(action='OVERLAYS')
    bpy.ops.character_designer.finger_definition(action='OVERLAYS')
    assert bones._preview is None and bones._intent is None


def test_register_unregister_cancels_runtime_without_changing_mesh():
    bones.hide()
    obj, rig, _ = bound_fixture()
    before, rest = fingerprint(obj), rig_state(rig)
    timers = []
    register_timer = bpy.app.timers.register

    def observed(callback, *args, **kwargs):
        if getattr(callback, '__module__', '').startswith('character_designer'):
            timers.append(callback)
        return register_timer(callback, *args, **kwargs)

    for _ in range(2):
        with patch.object(bpy.app.timers, 'register', side_effect=observed):
            character_designer.register()
            character_designer.register()
            _assert_retired_absent()
            bones.show_bend(C)
            bones.invalidate(C)
            assert bones._request is not None
        character_designer.unregister()
        assert bones._preview is None and bones._intent is None and bones._request is None
        assert not bones._saved_previews and not bones._handles
        assert not bpy.app.timers.is_registered(bones._refresh)
        assert all(not bpy.app.timers.is_registered(callback) for callback in timers)
        assert bones._dirty not in bpy.app.handlers.depsgraph_update_post
        for callbacks in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre, bpy.app.handlers.redo_pre):
            assert bones._reset not in callbacks
        assert not hasattr(bpy.types.Object, 'character_designer_finger_bank')
        assert fingerprint(obj) == before
        assert_rig_equal(rest, rig_state(rig))
        character_designer.register()
    _assert_retired_absent()


def main():
    character_designer.register()
    tests = [value for name, value in globals().items() if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('FINGER_TOOLS_RETIREMENT_TESTS_PASSED', len(tests), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
