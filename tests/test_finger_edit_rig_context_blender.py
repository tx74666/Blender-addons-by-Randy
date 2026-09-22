"""Edit Rig reuses live single-object edit data and restores caller context."""
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_relax_bones_blender import fixture, add_chain, select, state, context_state, C, character_designer
from character_designer import finger_targets as targets, finger_chain as chain


@contextmanager
def mode_calls(*, forbidden=False):
    """Spy on this context manager's operator calls without patching bpy RNA."""
    call = Mock(side_effect=AssertionError('Already editing: mode_set must not run')) if forbidden else Mock(wraps=bpy.ops.object.mode_set)
    proxy = SimpleNamespace(ops=SimpleNamespace(object=SimpleNamespace(mode_set=call)))
    with patch.object(targets, 'bpy', proxy):
        yield call


def test_single_edit_rig_keeps_edit_data_and_makes_zero_mode_switches():
    rig = fixture()
    names = add_chain(rig)
    select(rig, names, names[1])
    before = context_state(rig)
    pointers = {b.name: b.as_pointer() for b in rig.data.edit_bones}
    roll = rig.data.edit_bones[names[1]].roll
    with mode_calls(forbidden=True) as calls:
        with targets.edit_rig(C, rig):
            assert targets._single_edit_rig(C, rig)
            rig.data.edit_bones[names[1]].roll += .07
        assert calls.call_count == 0
    assert context_state(rig) == before
    assert {b.name: b.as_pointer() for b in rig.data.edit_bones} == pointers
    assert abs(rig.data.edit_bones[names[1]].roll-roll-.07) < 1e-6


def test_single_edit_exception_restores_active_selection_and_mirror():
    rig = fixture()
    names = add_chain(rig)
    unrelated = add_chain(rig, label='OldActive', x=8.)
    select(rig, names, unrelated[0])
    for name in unrelated:
        bone = rig.data.edit_bones[name]
        bone.select = bone.select_head = bone.select_tail = False
    rig.data.use_mirror_x = True
    before, context_before = state(rig), context_state(rig)
    with mode_calls(forbidden=True) as calls:
        try:
            with targets.edit_rig(C, rig):
                rig.data.use_mirror_x = False
                rig.data.edit_bones.active = rig.data.edit_bones[names[-1]]
                for bone in rig.data.edit_bones:
                    bone.select = bone.select_head = bone.select_tail = False
                raise RuntimeError('Injected edit callback failure')
        except RuntimeError as exc:
            assert 'Injected' in str(exc)
        else: raise AssertionError('Callback error was swallowed')
        assert calls.call_count == 0
    assert state(rig) == before
    assert context_state(rig) == context_before


def test_selected_relax_stays_in_edit_mode_for_entire_operation():
    rig = fixture()
    names = add_chain(rig)
    select(rig, names, names[1])
    before = context_state(rig)
    with mode_calls(forbidden=True) as calls:
        result = chain.relax(C)
        assert calls.call_count == 0
    assert result['changed'] > 0
    assert context_state(rig) == before


def test_object_mode_entry_still_restores_object_context_and_selection():
    rig = fixture()
    names = add_chain(rig)
    select(rig, names[:2], names[0])
    bpy.ops.object.mode_set(mode='OBJECT')
    rig.data.use_mirror_x = True
    selected = {b.name: b.select for b in rig.pose.bones}
    active = rig.data.bones.active.name
    objects = {obj.name for obj in C.selected_objects}
    with mode_calls() as calls:
        with targets.edit_rig(C, rig):
            assert C.mode == 'EDIT_ARMATURE'
            rig.data.use_mirror_x = False
            for bone in rig.data.edit_bones:
                bone.select = bone.select_head = bone.select_tail = False
        assert [call.kwargs['mode'] for call in calls.call_args_list] == ['EDIT', 'OBJECT']
    assert C.mode == 'OBJECT' and C.object == rig
    assert {obj.name for obj in C.selected_objects} == objects
    assert {b.name: b.select for b in rig.pose.bones} == selected
    assert rig.data.bones.active.name == active
    assert rig.data.use_mirror_x


def test_multi_object_edit_uses_existing_isolation_path_and_restores_both():
    rig = fixture()
    names = add_chain(rig)
    select(rig, names, names[1])
    bpy.ops.object.mode_set(mode='OBJECT')
    data = bpy.data.armatures.new('Other Edit Armature')
    other = bpy.data.objects.new('Other Edit Armature', data)
    C.collection.objects.link(other)
    other.select_set(True)
    bpy.ops.object.mode_set(mode='EDIT')
    other_names = add_chain(other, label='Other', x=9.)
    select(other, other_names[:2], other_names[0])
    assert set(C.objects_in_mode) == {rig, other}
    before, other_before, context_before = state(rig), state(other), context_state(rig)
    other_active = other.data.edit_bones.active.name
    with mode_calls() as calls:
        with targets.edit_rig(C, rig):
            assert tuple(C.objects_in_mode) == (rig,)
            assert C.object == rig
        assert [call.kwargs['mode'] for call in calls.call_args_list] == ['OBJECT', 'EDIT', 'OBJECT', 'EDIT']
    assert set(C.objects_in_mode) == {rig, other}
    assert state(rig) == before and state(other) == other_before
    assert other.data.edit_bones.active.name == other_active
    assert context_state(rig) == context_before


def test_callback_that_leaves_edit_mode_is_restored_even_on_exception():
    rig = fixture()
    names = add_chain(rig)
    select(rig, names, names[1])
    before, context_before = state(rig), context_state(rig)
    with mode_calls() as calls:
        try:
            with targets.edit_rig(C, rig):
                bpy.ops.object.mode_set(mode='OBJECT')
                raise RuntimeError('Callback changed mode')
        except RuntimeError as exc:
            assert 'Callback' in str(exc)
        else: raise AssertionError('Callback error was swallowed')
        # Only the recovery uses the context manager's mode operator proxy.
        assert [call.kwargs['mode'] for call in calls.call_args_list] == ['EDIT']
    assert state(rig) == before
    assert context_state(rig) == context_before


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('FINGER_EDIT_RIG_CONTEXT_PASS', flush=True)
