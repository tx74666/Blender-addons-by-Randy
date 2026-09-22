"""Explicit named-chain bone mirroring is independent of Capture and topology."""
import json
import sys
import traceback
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from finger_tools_fixtures import (
    bound_fixture, C, bank, definition, targets, fingerprint, close, character_designer,
)
from test_finger_loop_marks_blender import selection, slot_state
from character_designer import finger_bone_mirror as mirror
from character_designer import finger_chain as chain, finger_detect as detect


def fixture():
    obj, rig, _ = bound_fixture()
    rig.data.use_mirror_x = True
    bank.select(C, 'INDEX', 'L')
    # Capture contents/errors are deliberately unusable: the explicit bone
    # action uses Main Rig, active identity and present named bone chains only.
    for side in ('L', 'R'):
        slot = obj.character_designer_finger_bank.slots['INDEX.'+side]
        slot.guide.record = ''
        slot.error = 'Stale mesh Capture; geometry is intentionally not synchronized'
        slot.bones = 'obsolete bone binding'
    return obj, rig


@contextmanager
def no_geometry():
    with ExitStack() as stack:
        for module, names in (
                (definition, ('_snapshot', '_validate_mesh', 'frame')),
                (bank, ('validate_local', 'remap_record', 'recheck', 'adapt_topology', 'survey')),
                (detect, ('census',)),
                (targets, ('resolve', 'pair'))):
            for name in names:
                stack.enter_context(patch.object(module, name,
                    side_effect=AssertionError(f'Bone mirror read mesh/Capture: {module.__name__}.{name}')))
        yield


def rest(rig):
    with targets.edit_rig(C, rig):
        result = chain._snapshot_edit(rig)
        for bone in rig.data.edit_bones:
            result[bone.name]['axes'] = tuple(axis.copy() for axis in (bone.x_axis, bone.y_axis, bone.z_axis))
    return result


def same_rest(before, after, *, names=None):
    assert set(before) == set(after)
    for name in before if names is None else names:
        a, b = before[name], after[name]
        for field in ('head', 'tail'): close(a[field], b[field])
        assert abs(a['roll']-b['roll']) < 2e-6, name
        assert all(a[field] == b[field] for field in ('parent', 'connected', 'deform')), name
        for x, y in zip(a['axes'], b['axes']): close(x, y)


def context_state(obj, rig):
    state = (C.mode, C.view_layer.objects.active.name,
             tuple(sorted(item.name for item in C.selected_objects)), rig.data.use_mirror_x)
    if rig.mode == 'EDIT':
        return state, rig.data.edit_bones.active.name if rig.data.edit_bones.active else '', tuple(
            (bone.name, bone.select, bone.select_head, bone.select_tail) for bone in rig.data.edit_bones)
    return state, selection(obj) if obj.mode == 'EDIT' else None


def change_source(rig, side='L'):
    with targets.edit_rig(C, rig):
        rig.data.use_mirror_x = False
        bones = [rig.data.edit_bones[f'f_index.{i:02d}.{side}'] for i in (1, 2, 3)]
        for bone in bones: bone.use_connect = False
        nodes = [bones[0].head.copy(), bones[1].head+Vector((.012, .021, .016)),
                 bones[2].head+Vector((-.014, -.011, .029)), bones[-1].tail+Vector((.018, 0, .008))]
        for i, bone in enumerate(bones):
            bone.head, bone.tail, bone.roll = nodes[i], nodes[i+1], (.21, -.43, 1.17)[i]
        for side_name in ('L', 'R'):
            for i in (2, 3): rig.data.edit_bones[f'f_index.{i:02d}.{side_name}'].use_connect = True


def verify_mirrored(before, after, rig, source_names, *, plane_point=(0, 0, 0), plane_normal=(1, 0, 0)):
    world, inverse = rig.matrix_world.copy(), rig.matrix_world.inverted()
    normal, point = Vector(plane_normal).normalized(), Vector(plane_point)
    def reflected_point(value):
        current = world @ value
        return inverse @ (current-normal*2*(current-point).dot(normal))
    def reflected_axis(value):
        current = (world.to_3x3() @ value).normalized()
        return (inverse.to_3x3() @ (current-normal*2*current.dot(normal))).normalized()
    targets_names = [bpy.utils.flip_name(name) for name in source_names]
    for name, target in zip(source_names, targets_names):
        source, actual = before[name], after[target]
        close(actual['head'], reflected_point(source['head']))
        close(actual['tail'], reflected_point(source['tail']))
        wanted = [reflected_axis(axis) for axis in source['axes']]
        wanted[0].negate()
        for actual_axis, wanted_axis in zip(actual['axes'], wanted): close(actual_axis, wanted_axis, tolerance=2e-5)
        assert all(actual[field] == before[target][field] for field in ('parent', 'connected', 'deform'))
    same_rest(before, after, names=[name for name in before if name not in targets_names])


def refused_without_writes(obj, rig, text=''):
    mesh, before, slots, context = fingerprint(obj), rest(rig), slot_state(obj), context_state(obj, rig)
    with no_geometry():
        try: mirror.mirror(C)
        except ValueError as exc:
            assert not text or text.casefold() in str(exc).casefold(), str(exc)
        else: raise AssertionError('Invalid bone mirror was accepted')
    assert fingerprint(obj) == mesh and slot_state(obj) == slots
    assert context_state(obj, rig) == context
    same_rest(before, rest(rig))


def test_active_side_mirrors_existing_bones_and_roll_without_capture_or_mesh_reads():
    obj, rig = fixture()
    change_source(rig)
    mesh, before, slots, context = fingerprint(obj), rest(rig), slot_state(obj), context_state(obj, rig)
    with no_geometry(): result = mirror.mirror(C)
    assert result == {'source': 'INDEX.L', 'target': 'INDEX.R', 'bones': 3}
    verify_mirrored(before, rest(rig), rig, [f'f_index.{i:02d}.L' for i in (1, 2, 3)])
    assert fingerprint(obj) == mesh and context_state(obj, rig) == context
    after = slot_state(obj)
    assert all(value == after[key] for key, value in slots.items() if key != 'INDEX.R')
    assert after['INDEX.R'][:4] == slots['INDEX.R'][:4] and after['INDEX.R'][5:] == slots['INDEX.R'][5:]
    binding = json.loads(obj.character_designer_finger_bank.slots['INDEX.R'].bones)
    assert binding['rig'] == rig.name and len(binding['chain']) == 3


def test_rotated_mirror_plane_preserves_positive_and_negative_local_x_bend():
    obj, rig = fixture()
    change_source(rig)
    empty = bpy.data.objects.new('ExplicitMirrorPlane', None)
    C.collection.objects.link(empty)
    empty.matrix_world = Matrix.Translation((.07, -.03, .09)) @ Matrix.Rotation(.31, 4, 'Z') @ Matrix.Rotation(.19, 4, 'Y')
    modifier = obj.modifiers.new('Explicit Hand Mirror', 'MIRROR')
    modifier.mirror_object = empty
    rig.matrix_world = Matrix.Translation((.04, .06, -.02)) @ Matrix.Rotation(.13, 4, 'X') @ Matrix.Scale(1.1, 4)
    obj.matrix_world = Matrix.Translation((-.025, .011, 0)) @ Matrix.Rotation(-.08, 4, 'Z')
    C.view_layer.update()
    before, mesh, context = rest(rig), fingerprint(obj), context_state(obj, rig)
    with no_geometry(): mirror.mirror(C)
    after = rest(rig)
    source_names = [f'f_index.{i:02d}.L' for i in (1, 2, 3)]
    verify_mirrored(before, after, rig, source_names,
                    plane_point=empty.matrix_world.translation,
                    plane_normal=empty.matrix_world.to_3x3().col[0])
    # Behavioral oracle: actually rotate each bone's longitudinal direction by
    # the same signed Local X angle. The resulting directions, not just the
    # rest axes, must be geometric reflections through the world-space plane.
    normal = empty.matrix_world.to_3x3().col[0].normalized()
    world = rig.matrix_world.to_3x3()
    for name in source_names:
        source_x, source_y, _ = before[name]['axes']
        target_x, target_y, _ = after[bpy.utils.flip_name(name)]['axes']
        for angle in (.37, -.37):
            source_bent = (world @ (Quaternion(source_x, angle) @ source_y)).normalized()
            expected = source_bent-normal*(2*source_bent.dot(normal))
            target_bent = (world @ (Quaternion(target_x, angle) @ target_y)).normalized()
            close(target_bent, expected, tolerance=2e-5)
    assert fingerprint(obj) == mesh and context_state(obj, rig) == context


def test_mirrored_roll_preserves_calibrated_positive_local_x_bend_side():
    obj, rig, _ = bound_fixture()
    bank.select(C, 'INDEX', 'L')
    rig.data.use_mirror_x = True
    calibrated = targets.calibrate(C)
    assert calibrated['success'].get('INDEX') == 3, calibrated
    before, mesh = rest(rig), fingerprint(obj)
    with no_geometry(): mirror.mirror(C)
    after = rest(rig)
    verify_mirrored(before, after, rig, [f'f_index.{i:02d}.L' for i in (1, 2, 3)])
    for side in ('L', 'R'):
        guide = obj.character_designer_finger_bank.slots['INDEX.'+side].guide
        normal = Vector(json.loads(guide.record)['basis']['normal'])
        # The existing Calibrate command promises a positive X turn toward
        # this captured bend side. Verify the promise also on the mirrored mate.
        world_bend = obj.matrix_world.to_3x3().inverted().transposed() @ normal
        bend = rig.matrix_world.to_3x3().inverted() @ world_bend*(1 if guide.flip_bend else -1)
        for i in (1, 2, 3):
            x, y, _ = after[f'f_index.{i:02d}.{side}']['axes']
            wanted = (bend-y*bend.dot(y)).normalized()
            actual = (Quaternion(x, .05) @ y-y).normalized()
            assert actual.dot(wanted) > .99, (side, i, actual, wanted)
    assert fingerprint(obj) == mesh and rig.data.use_mirror_x


def test_active_right_can_mirror_four_segment_chain_in_armature_edit_mode():
    obj, rig = fixture()
    with targets.edit_rig(C, rig):
        rig.data.use_mirror_x = False
        for side, sign in (('L', 1), ('R', -1)):
            old = [rig.data.edit_bones[f'f_index.{i:02d}.{side}'] for i in (1, 2, 3)]
            root, tip = old[0].head.copy(), old[-1].tail.copy()
            for bone in reversed(old): rig.data.edit_bones.remove(bone)
            nodes = [root]+[root.lerp(tip, i/4)+Vector((sign*.006*i, 0, .011*i)) for i in (1, 2, 3)]+[tip]
            parent = None
            for i in range(4):
                bone = rig.data.edit_bones.new(f'f_index.{i+1:02d}.{side}')
                bone.head, bone.tail = nodes[i], nodes[i+1]
                bone.parent, bone.use_connect = parent, bool(parent)
                bone.roll = .17*(i+1) if side == 'R' else -.11
                parent = bone
    bank.select(C, 'INDEX', 'R')
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.select_set(False)
    rig.select_set(True)
    C.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in rig.data.edit_bones: bone.select = False
    selected = rig.data.edit_bones['f_index.02.L']
    selected.select_head, selected.select_tail, selected.select = True, True, True
    rig.data.edit_bones.active = selected
    # Selection on L must not override the explicitly displayed active R side.
    mesh, before, context = fingerprint(obj), rest(rig), context_state(obj, rig)
    with no_geometry(): result = mirror.mirror(C)
    assert result == {'source': 'INDEX.R', 'target': 'INDEX.L', 'bones': 4}
    verify_mirrored(before, rest(rig), rig, [f'f_index.{i:02d}.R' for i in (1, 2, 3, 4)])
    assert fingerprint(obj) == mesh and context_state(obj, rig) == context


def test_missing_hidden_posed_and_ambiguous_counterparts_refuse_atomically():
    obj, rig = fixture()
    with targets.edit_rig(C, rig): rig.data.edit_bones['f_index.03.R'].name = 'UnmatchedCounterpart'
    refused_without_writes(obj, rig, 'same existing')
    with targets.edit_rig(C, rig):
        rig.data.edit_bones['UnmatchedCounterpart'].name = 'f_index.03.R'
        rig.data.edit_bones['f_index.02.R'].hide = True
    refused_without_writes(obj, rig, 'unhide')
    with targets.edit_rig(C, rig): rig.data.edit_bones['f_index.02.R'].hide = False
    posed = rig.pose.bones['f_index.02.R']
    posed.rotation_mode = 'XYZ'
    posed.rotation_euler.x = .2
    refused_without_writes(obj, rig, 'neutral')
    posed.rotation_euler.x = 0.
    with targets.edit_rig(C, rig):
        extra = rig.data.edit_bones.new('f_index.99.R')
        extra.head, extra.tail = (-3, 0, 0), (-3, .2, .1)
        extra.parent = rig.data.edit_bones['f_index.01.R']
    refused_without_writes(obj, rig, 'branches')


def test_unrelated_connected_child_is_not_dragged():
    obj, rig = fixture()
    change_source(rig)
    with targets.edit_rig(C, rig):
        parent = rig.data.edit_bones['f_index.03.R']
        child = rig.data.edit_bones.new('UnrelatedConnectedChild')
        child.head, child.tail = parent.tail.copy(), parent.tail+Vector((0, .1, 0))
        child.parent, child.use_connect = parent, True
    refused_without_writes(obj, rig, 'unrelated connected')


def test_failure_after_binding_write_restores_bones_roll_metadata_and_context():
    obj, rig = fixture()
    change_source(rig)
    before, mesh, slots, context = rest(rig), fingerprint(obj), slot_state(obj), context_state(obj, rig)
    original = mirror._commit_binding
    def fail_after_commit(*args):
        original(*args)
        raise ValueError('Injected mirror binding failure')
    with no_geometry(), patch.object(mirror, '_commit_binding', side_effect=fail_after_commit):
        try: mirror.mirror(C)
        except ValueError as exc: assert str(exc) == 'Injected mirror binding failure'
        else: raise AssertionError('Injected failure did not reach the transaction')
    same_rest(before, rest(rig))
    assert fingerprint(obj) == mesh and slot_state(obj) == slots and context_state(obj, rig) == context


def test_public_operator_is_one_undo_action_and_preserves_artist_mesh():
    obj, rig = fixture()
    change_source(rig)
    before, mesh = rest(rig), fingerprint(obj)
    assert {'REGISTER', 'UNDO'} <= mirror.CHARACTERDESIGNER_OT_finger_bone_mirror.bl_options
    with no_geometry(): assert bpy.ops.character_designer.finger_bone_mirror() == {'FINISHED'}
    verify_mirrored(before, rest(rig), rig, [f'f_index.{i:02d}.L' for i in (1, 2, 3)])
    assert fingerprint(obj) == mesh


def test_preview_refresh_failure_keeps_success_and_completed_bone_transaction():
    from character_designer import finger_bone_tools
    obj, rig = fixture()
    change_source(rig)
    before, mesh = rest(rig), fingerprint(obj)
    with no_geometry(), patch.object(finger_bone_tools, 'invalidate', side_effect=RuntimeError('Injected preview failure')):
        assert bpy.ops.character_designer.finger_bone_mirror() == {'FINISHED'}
    verify_mirrored(before, rest(rig), rig, [f'f_index.{i:02d}.L' for i in (1, 2, 3)])
    assert fingerprint(obj) == mesh
    state = obj.character_designer_finger_bank
    assert state.bone_status.startswith('Mirrored INDEX.L to INDEX.R')
    assert json.loads(state.slots['INDEX.R'].bones)['rig'] == rig.name


def main():
    character_designer.register()
    tests = [fn for name, fn in globals().items() if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('FINGER_BONE_MIRROR_PASS', len(tests), flush=True)


if __name__ == '__main__':
    try: main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
