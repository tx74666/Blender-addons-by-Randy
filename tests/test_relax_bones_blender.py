"""Relax real selected Edit Bone chains without Finger Basic Setup or a mesh."""
import json
import math
import sys
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
import character_designer
from character_designer import finger_chain as chain, finger_bone_tools as tools

C = bpy.context


def fixture():
    if C.object and C.object.mode != 'OBJECT': bpy.ops.object.mode_set(mode='OBJECT')
    for obj in list(bpy.data.objects): bpy.data.objects.remove(obj, do_unlink=True)
    armature = bpy.data.armatures.new('Relax Test Bones')
    rig = bpy.data.objects.new('Arbitrary Rig', armature)
    C.collection.objects.link(rig)
    rig.select_set(True)
    C.view_layer.objects.active = rig
    C.scene.character_designer_setup.rig = None
    C.scene.character_designer_setup.body = None
    C.scene.character_designer_finger_setup = None
    bpy.ops.object.mode_set(mode='EDIT')
    return rig


def add_chain(rig, label='Cable', side='', count=3, amplitude=.45, x=3.):
    fractions = [0., .14, .61, 1.] if count == 3 else [0., .10, .43, .78, 1.]
    sign = -1 if side == 'R' else 1
    nodes = [Vector((sign*x, 3*t, amplitude*math.sin(math.pi*t))) for t in fractions]
    names, previous = [], None
    for i in range(count):
        name = f'{label}_{i+1:02d}'+('.'+side if side else '')
        bone = rig.data.edit_bones.new(name)
        bone.head, bone.tail = nodes[i], nodes[i+1]
        bone.parent, bone.use_connect = previous, previous is not None
        bone.roll, bone.use_deform = .17+i*.11, i != 1
        names.append(name)
        previous = bone
    return names


def select(rig, names, active=None):
    rig.data.use_mirror_x = False
    for bone in rig.data.edit_bones:
        bone.select = bone.select_head = bone.select_tail = False
    for name in names:
        bone = rig.data.edit_bones[name]
        bone.select = bone.select_head = bone.select_tail = True
    rig.data.edit_bones.active = rig.data.edit_bones.get(active) if active else None


def state(rig):
    return {bone.name: dict(head=tuple(bone.head), tail=tuple(bone.tail), roll=bone.roll,
             parent=bone.parent.name if bone.parent else None, connected=bone.use_connect,
             deform=bone.use_deform, lock=getattr(bone, 'lock', False), hide=bone.hide,
             selected=(bone.select, bone.select_head, bone.select_tail))
            for bone in rig.data.edit_bones}


def context_state(rig):
    active = rig.data.edit_bones.active
    return C.mode, C.object.name, tuple(o.name for o in C.selected_objects), active.name if active else None, rig.data.use_mirror_x


def metadata(rig):
    value = rig.get(chain.RELAX_STATE)
    if hasattr(value, 'to_dict'): value = value.to_dict()
    return chain.RELAX_STATE in rig, json.dumps(value, sort_keys=True)


def nodes(saved, names):
    return [Vector(saved[name]['head']) for name in names]+[Vector(saved[names[-1]]['tail'])]


def resample(points):
    lengths = [(b-a).length for a, b in zip(points, points[1:])]
    total = sum(lengths)
    result = [points[0].copy()]
    for i in range(1, len(points)-1):
        distance = total*i/(len(points)-1)
        for a, b, length in zip(points, points[1:], lengths):
            if distance <= length:
                result.append(a.lerp(b, distance/length))
                break
            distance -= length
    return result+[points[-1].copy()]


def invariant(before, after, chains):
    assert before.keys() == after.keys(), 'Relax changed bone identities'
    affected = {name for names in chains for name in names}
    for name in before:
        for field in ('roll', 'parent', 'connected', 'deform', 'lock', 'hide', 'selected'):
            assert before[name][field] == after[name][field], (name, field)
        if name not in affected: assert before[name] == after[name], name
    for names in chains:
        assert before[names[0]]['head'] == after[names[0]]['head']
        assert before[names[-1]]['tail'] == after[names[-1]]['tail']
        for a, b in zip(names, names[1:]):
            assert (Vector(after[a]['tail'])-Vector(after[b]['head'])).length < 1e-6


def assert_reflection(saved, source, opposite):
    for a, b in zip(source, opposite):
        for field in ('head', 'tail'):
            x, y, z = saved[a][field]
            assert (Vector((-x, y, z))-Vector(saved[b][field])).length < 1e-6, (a, b, field)


def refused(rig, action, *, error_type=ValueError):
    before, context_before, cache_before = state(rig), context_state(rig), metadata(rig)
    try: action()
    except error_type: pass
    else: raise AssertionError('Unsafe selected bone operation was accepted')
    assert state(rig) == before
    assert context_state(rig) == context_before
    assert metadata(rig) == cache_before


def test_arbitrary_three_and_four_bone_chains_preserve_curvature_and_identity():
    for count in (3, 4):
        rig = fixture()
        names = add_chain(rig, label='Ribbon', count=count)
        add_chain(rig, label='Untouched', x=8.)
        select(rig, names, names[1])
        before, context_before = state(rig), context_state(rig)
        result = chain.relax(C)
        after = state(rig)
        assert result['changed'] > 0
        invariant(before, after, [names])
        original, current = nodes(before, names), nodes(after, names)
        expected = resample(original)
        assert all((a-b).length < 2e-6 for a, b in zip(current, expected))
        assert any(abs(p.z-current[0].z) > .05 for p in current[1:-1])
        old_turns = [(b-a).cross(c-b) for a, b, c in zip(original, original[1:], original[2:])]
        new_turns = [(b-a).cross(c-b) for a, b, c in zip(current, current[1:], current[2:])]
        assert all(a.dot(b) > 0 for a, b in zip(old_turns, new_turns))
        assert context_state(rig) == context_before


def test_multiple_selected_chains_are_atomic_and_mirror_off_keeps_opposite():
    rig = fixture()
    left = add_chain(rig, side='L')
    right = add_chain(rig, side='R', amplitude=.25)
    other = add_chain(rig, label='Other', x=8., count=4)
    select(rig, left+other, left[0])
    before, context_before = state(rig), context_state(rig)
    result = chain.relax(C)
    after = state(rig)
    assert result['chains'] == 2 and result['changed'] > 0
    invariant(before, after, [left, other])
    assert all(after[name] == before[name] for name in right)
    assert context_state(rig) == context_before


def test_mirror_on_uses_selected_source_and_missing_opposite_is_reported():
    rig = fixture()
    left = add_chain(rig, side='L')
    right = add_chain(rig, side='R', amplitude=.23)
    unpaired = add_chain(rig, label='Unpaired', side='L', x=8.)
    select(rig, left+unpaired, left[1])
    rig.data.use_mirror_x = True
    before, context_before = state(rig), context_state(rig)
    result = chain.relax(C)
    after = state(rig)
    assert result['chains'] == 3
    assert result['skipped_mirrors'], 'Missing opposite should be reported, not silently created'
    assert 'Unpaired' in str(result['skipped_mirrors'])
    assert_reflection(after, left, right)
    expected = resample(nodes(before, left))
    assert all((a-b).length < 2e-6 for a, b in zip(nodes(after, left), expected))
    invariant(before, after, [left, right, unpaired])
    assert context_state(rig) == context_before


def test_both_sides_selected_deduplicate_and_choose_active_side_or_left():
    for active_side in ('R', None):
        rig = fixture()
        left = add_chain(rig, side='L', amplitude=.45)
        right = add_chain(rig, side='R', amplitude=.23)
        select(rig, left+right, right[1] if active_side else None)
        rig.data.use_mirror_x = True
        before, context_before = state(rig), context_state(rig)
        source = right if active_side else left
        expected = resample(nodes(before, source))
        result = chain.relax(C)
        after = state(rig)
        assert result['chains'] == 2
        assert all((a-b).length < 2e-6 for a, b in zip(nodes(after, source), expected))
        assert_reflection(after, left, right)
        invariant(before, after, [left, right])
        assert context_state(rig) == context_before


def test_named_left_is_default_source_even_when_it_is_on_negative_x():
    rig = fixture()
    left = add_chain(rig, side='L', amplitude=.45, x=-3.)
    right = add_chain(rig, side='R', amplitude=.23, x=-3.)
    select(rig, left+right)
    rig.data.use_mirror_x = True
    before, context_before = state(rig), context_state(rig)
    expected = resample(nodes(before, left))
    assert C.object.data.edit_bones.active is None
    assert before[left[0]]['head'][0] < 0
    result = chain.relax(C)
    after = state(rig)
    assert result['chains'] == 2
    assert all((a-b).length < 2e-6 for a, b in zip(nodes(after, left), expected))
    assert_reflection(after, left, right)
    invariant(before, after, [left, right])
    assert context_state(rig) == context_before


def test_unrelated_corrupt_basic_bone_records_do_not_block_public_relax():
    rig = fixture()
    names = add_chain(rig, label='Independent')
    select(rig, names, names[1])
    mesh = bpy.data.meshes.new('Old Broken Setup')
    body = bpy.data.objects.new('Old Broken Setup', mesh)
    C.collection.objects.link(body)
    bank = body.character_designer_finger_bank
    bank.active, bank.survey = 'MIDDLE.L', '{"candidates":{},"warnings":{}}'
    values = [
        '{broken JSON',
        '[]',
        'null',
        json.dumps({'rig': rig.name, 'chain': 'invalid chain'}),
        json.dumps({'rig': rig.name, 'chain': [None]}),
        json.dumps({'rig': rig.name, 'chain': [dict(name=names[0], parent='',
                       deform=True, head=None, tail=[0, 1, 0])]}),
    ]
    for index, value in enumerate(values):
        slot = bank.slots.add()
        slot.name, slot.bones, slot.error = f'Broken{index}', value, 'Legacy binding is invalid'
    C.scene.character_designer_finger_setup = body
    C.scene.character_designer_setup.body = body
    before, context_before = state(rig), context_state(rig)
    assert bpy.ops.character_designer.finger_bone_tools(action='RELAX_BONES') == {'FINISHED'}
    after = state(rig)
    assert any(before[name]['head'] != after[name]['head'] for name in names[1:])
    invariant(before, after, [names])
    assert context_state(rig) == context_before
    assert [slot.bones for slot in bank.slots] == values, 'Unrelated broken bindings should stay untouched'


def test_previously_active_unselected_bone_does_not_become_selected():
    rig = fixture()
    chosen = add_chain(rig, label='Selected')
    unrelated = add_chain(rig, label='OldActive', x=8.)
    select(rig, chosen, unrelated[0])
    # Setting active may select it in Blender. Reproduce the real state after
    # the artist subsequently deselects it and selects a different chain.
    for name in unrelated:
        bone = rig.data.edit_bones[name]
        bone.select = bone.select_head = bone.select_tail = False
    assert rig.data.edit_bones.active.name == unrelated[0]
    assert not rig.data.edit_bones[unrelated[0]].select
    before, context_before = state(rig), context_state(rig)
    result = chain.relax(C)
    after = state(rig)
    assert result['chains'] == 1 and result['changed'] > 0
    invariant(before, after, [chosen])
    assert context_state(rig) == context_before
    assert not rig.data.edit_bones[unrelated[0]].select


def test_public_operator_ignores_missing_or_invalid_middle_basic_setup():
    for stale_setup in (False, True):
        rig = fixture()
        names = add_chain(rig, label='NotAFinger')
        select(rig, names, names[0])
        if stale_setup:
            mesh = bpy.data.meshes.new('Unrelated Body')
            body = bpy.data.objects.new('Unrelated Body', mesh)
            C.collection.objects.link(body)
            bank = body.character_designer_finger_bank
            bank.active, bank.survey = 'MIDDLE.L', '{"candidates":{},"warnings":{}}'
            slot = bank.slots.add()
            slot.name, slot.error = 'MIDDLE.L', 'Reference is intentionally invalid'
            C.scene.character_designer_finger_setup = body
            C.scene.character_designer_setup.body = body
        before, context_before = state(rig), context_state(rig)
        assert bpy.ops.character_designer.finger_bone_tools(action='RELAX_BONES') == {'FINISHED'}
        after = state(rig)
        assert any(before[name]['head'] != after[name]['head'] for name in names[1:])
        invariant(before, after, [names])
        assert context_state(rig) == context_before


def test_repeated_relax_uses_rig_cache_without_straightening_further():
    rig = fixture()
    names = add_chain(rig, label='Curve', count=4)
    select(rig, names, names[1])
    chain.relax(C)
    after, cache_after = state(rig), metadata(rig)
    assert cache_after[0], 'Relax provenance belongs to the actual armature object'
    for _ in range(5):
        assert chain.relax(C)['changed'] == 0
        assert state(rig) == after
        assert metadata(rig) == cache_after


def test_selection_branch_gap_lock_and_wrong_mode_refuse_without_mutation():
    for defect in ('none', 'branch', 'gap', 'lock'):
        rig = fixture()
        names = add_chain(rig)
        select(rig, names if defect != 'none' else (), names[0] if defect != 'none' else None)
        if defect == 'branch':
            branch = rig.data.edit_bones.new('SelectedBranch')
            branch.head, branch.tail = rig.data.edit_bones[names[0]].tail, Vector((3.6, 1., .3))
            branch.parent, branch.use_connect = rig.data.edit_bones[names[0]], True
            branch.select = branch.select_head = branch.select_tail = True
        elif defect == 'gap':
            bone = rig.data.edit_bones[names[1]]
            bone.use_connect = False
            bone.head += Vector((.4, 0, 0))
        elif defect == 'lock':
            rig.data.edit_bones[names[1]].lock = True
        refused(rig, lambda: chain.relax(C))
    rig = fixture()
    names = add_chain(rig)
    select(rig, names, names[0])
    before = state(rig)
    bpy.ops.object.mode_set(mode='OBJECT')
    try: chain.relax(C)
    except ValueError: pass
    else: raise AssertionError('Relax requires actual Edit Bone selection')
    assert C.mode == 'OBJECT'
    bpy.ops.object.mode_set(mode='EDIT')
    assert state(rig) == before


def test_unreliable_mirrored_endpoints_refuse_the_entire_transaction():
    rig = fixture()
    left = add_chain(rig, side='L')
    right = add_chain(rig, side='R')
    rig.data.edit_bones[right[-1]].tail += Vector((0, 1., .5))
    select(rig, left, left[1])
    rig.data.use_mirror_x = True
    refused(rig, lambda: chain.relax(C))


def test_connected_unselected_child_that_would_move_refuses():
    rig = fixture()
    names = add_chain(rig)
    branch = rig.data.edit_bones.new('MustStayHere')
    branch.head = rig.data.edit_bones[names[0]].tail
    branch.tail = branch.head+Vector((.4, .1, 0))
    branch.parent, branch.use_connect = rig.data.edit_bones[names[0]], True
    select(rig, names, names[1])
    refused(rig, lambda: chain.relax(C))


def test_injected_write_failure_restores_bones_selection_and_existing_cache():
    for existing_cache in (False, True):
        rig = fixture()
        names = add_chain(rig, count=4)
        select(rig, names, names[1])
        if existing_cache:
            chain.relax(C)
            # A genuine later rest edit must invalidate the earlier result;
            # failed re-relax must retain both that edit and its old metadata.
            rig.data.edit_bones[names[1]].tail += Vector((0, 0, .02))
        original = chain.apply
        def failed_apply(context, plan, **kwargs):
            def fail(): raise RuntimeError('Injected after selected bone write')
            return original(context, plan, after_write=fail)
        with patch.object(chain, 'apply', side_effect=failed_apply) as calls:
            refused(rig, lambda: chain.relax(C), error_type=RuntimeError)
            assert calls.call_count == 1, 'Relax must commit all selected and mirrored chains atomically'


if __name__ == '__main__':
    character_designer.register()
    for name, test in list(globals().items()):
        if name.startswith('test_'):
            test()
            print('PASS', name, flush=True)
    print('RELAX_BONES_PASS', flush=True)
