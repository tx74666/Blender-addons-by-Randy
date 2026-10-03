"""Registered Action-export UI contract; disposable background Blender only.

The export backend and file selector are mocked. No child process, FBX worker,
renderer, Unity instance, native undo/playback, or production asset is used.
"""

from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
import character_designer
from character_designer import animation as ui, animation_export as backend
from character_designer.ui_constants import UI_PAGE_ANIMATION
from test_unity_animation_ui_blender import Layout


def fixture(folder):
    data = bpy.data.armatures.new('UI Export Skeleton')
    rig = bpy.data.objects.new('CoshaRig', data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    bone = data.edit_bones.new('Hips')
    bone.head, bone.tail = (0, 0, 0), (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    for frame, x in ((1.25, 0.), (25.75, .3)):
        rig.pose.bones['Hips'].location.x = x
        rig.pose.bones['Hips'].keyframe_insert('location', frame=frame)
    action = rig.animation_data.action
    action.name = 'Chosen Export Action'
    action['unity_loop_time'] = True
    action_copy = action.copy()
    action_copy.name = 'Other Action Selected Later'
    settings = bpy.context.window_manager.character_designer_animation
    settings.target, settings.unity_directory = rig, str(folder)
    settings.result_path = str(folder / 'Existing Kimodo Motion.bvh')
    settings.export_result_path = str(folder / 'Previous Action.fbx')
    settings.has_error = True
    bpy.context.window_manager.character_designer.ui_page = UI_PAGE_ANIMATION
    return rig, action, action_copy, settings


def panel():
    layout = Layout()
    ui.CHARACTERDESIGNER_PT_animation.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout


def stop_timer():
    if bpy.app.timers.is_registered(ui._poll_action_export):
        bpy.app.timers.unregister(ui._poll_action_export)


def tick():
    # Simulate the timer dispatcher without waiting or entering a GUI event loop.
    stop_timer()
    return ui._poll_action_export()


def test_contract(folder):
    rig, action, alternate, settings = fixture(folder)
    rna = bpy.ops.character_designer.animation_export.get_rna_type()
    expected = {'filepath': 'STRING', 'filter_glob': 'STRING', 'rig_name': 'STRING',
                'action_name': 'STRING', 'frame_start': 'FLOAT', 'frame_end': 'FLOAT', 'loop': 'BOOLEAN'}
    assert all(name in rna.properties and rna.properties[name].type == kind for name, kind in expected.items())
    assert 'export_result_path' in settings.bl_rna.properties
    assert 'character_designer.animation_export' in panel().buttons
    assert 'character_designer.animation_export_cancel' not in panel().buttons

    state = {'job': None, 'outcome': None, 'begins': [], 'cancels': []}

    def begin(context, selected_rig, selected_action, filepath, **options):
        assert state['job'] is None, 'UI attempted a second concurrent export'
        state['begins'].append((selected_rig, selected_action, filepath, options))
        state['job'] = object()
        return state['job']

    def poll(job):
        assert job is state['job']
        outcome = state['outcome']
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            state['job'] = None
        return outcome

    def cancel(job=None):
        assert job is None or job is state['job']
        state['cancels'].append(state['job'])
        state['job'] = None

    reports, chooser = [], []
    # Invoke captures the Action/rig once. No real file selector is opened in a
    # background process; the registered RNA is used for draw-property checks.
    operator = SimpleNamespace(report=lambda kind, message: reports.append((kind, message)), bl_rna=rna)
    wm_proxy = SimpleNamespace(character_designer_animation=settings, fileselect_add=chooser.append)
    context_proxy = SimpleNamespace(window_manager=wm_proxy, object=rig, scene=bpy.context.scene)
    original_result = settings.result_path
    before_data = rig.data
    with patch.object(backend, 'active_job', side_effect=lambda: state['job']), \
            patch.object(backend, 'begin_export', side_effect=begin), \
            patch.object(backend, 'poll_export', side_effect=poll), \
            patch.object(backend, 'cancel_export', side_effect=cancel), \
            patch.object(backend.subprocess, 'Popen', side_effect=AssertionError('A UI test started a process')):
        assert ui.CHARACTERDESIGNER_OT_animation_export.invoke(operator, context_proxy, None) == {'RUNNING_MODAL'}
        assert chooser == [operator] and not reports
        assert operator.rig_name == rig.name and operator.action_name == action.name
        assert (operator.frame_start, operator.frame_end, operator.loop) == (1.25, 25.75, True)
        assert operator.filepath.endswith('.fbx')
        operator.layout = Layout()
        ui.CHARACTERDESIGNER_OT_animation_export.draw(operator, context_proxy)
        assert set(operator.layout.fields) == {'frame_start', 'frame_end', 'loop'}
        assert any('NLA' in label for label in operator.layout.labels)
        assert any('Shape Keys' in label for label in operator.layout.labels)

        # Changing the active Action after opening the selector must not silently
        # export the newly selected Action under the old filename/range.
        rig.animation_data.action = alternate
        rig.animation_data.action_slot = alternate.slots[0]

        def execute():
            return bpy.ops.character_designer.animation_export(
                'EXEC_DEFAULT', filepath=operator.filepath, rig_name=operator.rig_name,
                action_name=operator.action_name, frame_start=operator.frame_start,
                frame_end=operator.frame_end, loop=operator.loop)

        assert bpy.ops.character_designer.animation_export.poll()
        assert execute() == {'FINISHED'}
        assert state['begins'][-1][0:2] == (rig, action)
        assert state['begins'][-1][3] == {'frame_start': 1.25, 'frame_end': 25.75, 'loop': True}
        assert rig.animation_data.action is alternate and rig.data is before_data
        assert bpy.app.timers.is_registered(ui._poll_action_export)
        assert not settings.has_error and ui._export_window_manager is bpy.context.window_manager
        assert not bpy.ops.character_designer.animation_export.poll()
        assert 'character_designer.animation_export_cancel' in panel().buttons
        assert 'character_designer.animation_export' not in panel().buttons
        assert tick() == .25 and state['job'] is not None

        state['outcome'] = {'filepath': str(folder / 'Chosen Action.fbx'),
                            'unsupported_channels': ['Shape Key animation: Face']}
        assert tick() is None and state['job'] is None
        assert settings.export_result_path == state['outcome']['filepath']
        assert settings.result_path == original_result, 'FBX export replaced the Kimodo result'
        assert 'Shape Key animation: Face' in settings.status and not settings.has_error
        assert ui._export_window_manager is None and bpy.ops.character_designer.animation_export.poll()

        previous_export = settings.export_result_path
        state['outcome'] = None
        assert execute() == {'FINISHED'}
        assert bpy.ops.character_designer.animation_export_cancel() == {'FINISHED'}
        assert len(state['cancels']) == 1 and state['job'] is None
        assert not bpy.app.timers.is_registered(ui._poll_action_export)
        assert ui._export_window_manager is None and not settings.has_error
        assert settings.export_result_path == previous_export and settings.result_path == original_result

        assert execute() == {'FINISHED'}
        state['outcome'] = backend.AnimationExportError('Expected worker failure')
        assert tick() is None and state['job'] is None
        assert len(state['cancels']) == 2 and settings.has_error
        assert settings.status == 'Expected worker failure'
        assert settings.export_result_path == previous_export and settings.result_path == original_result
        assert ui._export_window_manager is None

        # File loading/add-on shutdown must cancel its own pending job and timer.
        state['outcome'] = None
        assert execute() == {'FINISHED'}
        ui._animation_load_pre(None)
        assert state['job'] is None and len(state['cancels']) == 3
        assert ui._export_window_manager is None and not bpy.app.timers.is_registered(ui._poll_action_export)

        ui._job = SimpleNamespace(cancel=lambda: None)
        try:
            assert not bpy.ops.character_designer.animation_export.poll(), 'Local generation did not lock export'
        finally:
            ui._job = None
        rig.animation_data.action = None
        empty = SimpleNamespace(report=lambda kind, message: reports.append((kind, message)))
        assert ui.CHARACTERDESIGNER_OT_animation_export.invoke(empty, context_proxy, None) == {'CANCELLED'}
        assert len(chooser) == 1 and 'Select an Action' in reports[-1][1]
        rig.animation_data.action = alternate
        rig.animation_data.action_slot = alternate.slots[0]
        assert settings.result_path == original_result and rig.data is before_data


def test_link_controls(folder):
    from character_designer import animation_link
    rig, action, _, settings = fixture(folder)
    manifest = str(folder / 'character_animation_link.json')
    model = str(folder / 'Cosha.fbx')
    assert bpy.ops.character_designer.animation_link_import.get_rna_type()
    assert bpy.ops.character_designer.animation_link_model.get_rna_type()
    assert bpy.ops.character_designer.animation_link_sync.get_rna_type()
    assert 'character_designer.animation_link_import' in panel().buttons
    assert not bpy.ops.character_designer.animation_link_sync.poll()
    with patch.object(animation_link, 'load_link', return_value={'modelFile': model}), \
            patch.object(ui, '_link_import', return_value=SimpleNamespace(rig=rig, action=action)) as imported:
        assert bpy.ops.character_designer.animation_link_import('EXEC_DEFAULT', filepath=manifest) == {'FINISHED'}
        assert imported.call_args.args[1:] == (manifest, model)

    state = {'job': None}
    linked = {'action_name': action.name, 'manifest_path': manifest}
    def begin(context, selected_rig):
        assert selected_rig is rig and state['job'] is None
        state['job'] = {'linked': True}
        return state['job']
    def poll(job):
        assert job is state['job']
        state['job'] = None
        return {'filepath': str(folder / 'revision.fbx'), 'link_manifest': manifest}
    previous_result = settings.result_path
    with patch.object(animation_link, 'get_link', return_value=linked), \
            patch.object(animation_link, 'begin_linked_export', side_effect=begin), \
            patch.object(backend, 'active_job', side_effect=lambda: state['job']), \
            patch.object(backend, 'poll_export', side_effect=poll):
        assert 'character_designer.animation_link_sync' in panel().buttons
        assert 'character_designer.animation_link_import' not in panel().buttons
        assert bpy.ops.character_designer.animation_link_sync.poll()
        assert bpy.ops.character_designer.animation_link_sync('EXEC_DEFAULT') == {'FINISHED'}
        assert not bpy.ops.character_designer.animation_link_sync.poll()
        assert bpy.app.timers.is_registered(ui._poll_action_export)
        assert tick() is None
        assert 'Preview and Apply' in settings.status and not settings.has_error
        assert settings.result_path == previous_result
        assert settings.export_result_path.endswith('revision.fbx')
    # A damaged saved association must not break the sidebar or its operator poll.
    with patch.object(animation_link, 'get_link', side_effect=animation_link.AnimationLinkError('Expected damaged association')):
        assert not bpy.ops.character_designer.animation_link_sync.poll()
        assert any('Link needs attention' in label for label in panel().labels)


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    character_designer.register()
    character_designer.register()
    character_designer._validate_registration_integrity()
    assert bpy.app.handlers.load_pre.count(ui._animation_load_pre) == 1
    try:
        with tempfile.TemporaryDirectory(prefix='cdesigner-export-ui-') as folder:
            test_contract(Path(folder))
            test_link_controls(Path(folder))
        character_designer._validate_registration_integrity()
    finally:
        stop_timer()
        ui._job = None
        ui._export_window_manager = None
        character_designer.unregister()
    assert ui._animation_load_pre not in bpy.app.handlers.load_pre
    assert not bpy.app.timers.is_registered(ui._poll_action_export)
    print('ANIMATION_EXPORT_UI_TESTS_PASSED')


if __name__ == '__main__':
    main()
