"""Joint marks distinguish a local proof cap from the real palm surface.

Disposable fixtures only. This file does not open or save an artist blend.
Run serially with the other Blender fixture suites.
"""
import copy
import json
import sys
import traceback
from pathlib import Path
from unittest.mock import patch

from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_loop_marks_blender import (
    fixture, mark_pair, slot_state, rolls, chain_names, assert_chain, refused,
    marks, C, bank, definition, targets, fingerprint, rig_state,
    assert_rig_equal, character_designer,
)
from character_designer import finger_internal as internal, finger_range as ranges


def root_fixture(*, lateral=0., changed_ray=False):
    obj, rig = fixture()
    record = json.loads(obj.character_designer_finger_bank.slots['INDEX.L'].guide.record)
    root, tip = Vector(record['body']['root']), Vector(record['body']['tip'])
    direction = (tip-root).normalized()
    with targets.edit_rig(C, rig):
        bones = [rig.data.edit_bones[name] for name in chain_names('INDEX', 'L')]
        bones[0].head = root-direction*.12+Vector((0, 0, lateral))
        if changed_ray:
            # The old proximal ray differs from the planned straight chain.
            # Only actual current local faces can authorize this change.
            joint = bones[0].tail.copy()+Vector((0, 0, .025))
            bones[0].tail, bones[1].head = joint, joint
    saved = mark_pair(obj, first=2 if lateral > .07 else 1, second=5)
    return obj, rig, saved


def local_references(obj, bm):
    saved = marks.records(obj, 'INDEX.L')
    loops = [marks._recover(bm, saved[str(number)], number) for number in (1, 2)]
    fresh = marks._fresh_reference(bm, loops, marks._reference(obj, 'INDEX.L', None))
    # The fixture has one real flared proximal band. Extend only this band's
    # current faces to prove the counterexample is inside actual local anatomy;
    # do not use a whole-hand closed component or historical captured faces.
    body = fresh['body']
    ring = [bm.verts[index] for index in body['rings'][0]]
    entry = bm.edges.get((ring[0], ring[1]))
    face = next(face for face in entry.link_faces if face.index not in body['faces'])
    row, mapping, band = ranges.quad_band(face, entry)
    outer = [mapping[vertex] for vertex in row]
    extended = copy.deepcopy(body)
    extended['rings'].insert(0, [vertex.index for vertex in outer])
    extended['faces'] = sorted(set(body['faces']) | {face.index for face in band})
    extended['root'] = list(ranges._center(outer))
    extended['length'] += (Vector(body['root'])-Vector(extended['root'])).length
    return fresh, extended


def test_proximal_fixed_root_is_inside_actual_flare_but_outside_virtual_sleeve_cap():
    obj, rig, _ = root_fixture()
    before = rig_state(rig)
    root = Vector(before['bones']['f_index.01.L']['head'])
    first_joint = Vector(before['bones']['f_index.01.L']['tail'])
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        fresh, local_body = local_references(obj, bm)
        sleeve = internal.Volume(bm, fresh['body'])
        actual_local = internal.Volume(bm, local_body)
        assert not sleeve.inside(root), 'Fixture must reproduce the artificial-cap false negative'
        assert actual_local.inside(root), 'Root must be inside the actual proximal flared surface'
        assert actual_local.certify((root, first_joint), .0005) is not None
    finally:
        bm.free()


def test_align_keeps_unchanged_proximal_prefix_and_certifies_changed_in_sleeve_span():
    obj, rig, saved = root_fixture()
    before, old_rolls, mesh, stored = rig_state(rig), rolls(rig), fingerprint(obj), obj[marks.PROPERTY]
    bindings = slot_state(obj)
    # For the straight fixture, mark centers are also its fixed internal axis.
    wanted = [Vector(saved[str(number)]['center']) for number in (1, 2)]
    with patch.object(definition, '_validate_mesh', side_effect=AssertionError('Old Capture proof')), \
         patch.object(bank, 'remap_record', side_effect=AssertionError('Old Capture remap')), \
         patch.object(internal, '_component', side_effect=AssertionError('Whole-hand surface traversal')):
        result = marks.align(C)
    assert result['keys'] == ['INDEX.L'] and result['chains'] == 1
    names = chain_names('INDEX', 'L')
    assert_chain(rig, before, old_rolls, names, wanted)
    assert_rig_equal(before, rig_state(rig), names=[name for name in before['bones'] if name not in names])
    assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == stored
    after = slot_state(obj)
    assert all(after[key] == value for key, value in bindings.items() if key != 'INDEX.L')


def test_lateral_exit_is_not_mistaken_for_an_artificial_root_cap():
    obj, rig, _ = root_fixture(lateral=.10)
    before, mesh, stored, bindings = rig_state(rig), fingerprint(obj), obj[marks.PROPERTY], slot_state(obj)
    # Both marked junctions are inside the narrow sleeve, but the root-to-first
    # span crosses the actual side surface near the palm/sleeve transition.
    refused(lambda: marks.align(C))
    assert_rig_equal(before, rig_state(rig))
    assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == stored and slot_state(obj) == bindings


def test_changed_proximal_ray_requires_current_local_surface_proof():
    obj, rig, saved = root_fixture(changed_ray=True)
    before, old_rolls, mesh, stored = rig_state(rig), rolls(rig), fingerprint(obj), obj[marks.PROPERTY]
    wanted = [Vector(saved[str(number)]['center']) for number in (1, 2)]
    root = Vector(before['bones']['f_index.01.L']['head'])
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        _, local_body = local_references(obj, bm)
        assert internal.Volume(bm, local_body).certify((root, wanted[0]), .0005) is not None
    finally:
        bm.free()
    with patch.object(definition, '_validate_mesh', side_effect=AssertionError('Old Capture proof')), \
         patch.object(bank, 'remap_record', side_effect=AssertionError('Old Capture remap')), \
         patch.object(internal, '_component', side_effect=AssertionError('Whole-hand surface traversal')):
        result = marks.align(C)
    assert result['keys'] == ['INDEX.L']
    assert_chain(rig, before, old_rolls, chain_names('INDEX', 'L'), wanted)
    assert fingerprint(obj) == mesh and obj[marks.PROPERTY] == stored


def test_coverage_merges_real_collinear_spans_but_rejects_gaps_and_lateral_changes():
    def point(x, y=0.): return Vector((x, y, 0.))
    tolerance = 1e-7
    segments = [(point(0.), point(.4)), (point(.4), point(1.))]
    assert marks._covered_span(point(.2), point(.8), segments, tolerance)
    assert not marks._covered_span(point(.2), point(.8),
                                  [(point(0.), point(.4)), (point(.6), point(1.))], tolerance)
    assert not marks._covered_span(point(.2), point(.8),
                                  [(point(0.), point(.4, .01)), (point(.4, .01), point(1.))], tolerance)
    assert not marks._covered_span(point(.2, 2e-6), point(.8, 2e-6), segments, tolerance)
    assert not marks._covered_span(point(-.01), point(.8), segments, tolerance)


def test_new_joint_outside_is_rejected_even_when_existing_path_is_unchanged():
    obj, rig, _ = root_fixture()
    bm = definition._snapshot(obj, definition._basis_name(obj))
    try:
        fresh, _ = local_references(obj, bm)
        body = fresh['body']
        root, tip = Vector(body['root']), Vector(body['tip'])
        nodes = [root, root.lerp(tip, .3)+Vector((0, 0, .2)), root.lerp(tip, .7), tip]
        names = chain_names('INDEX', 'L')
        message = refused(lambda: marks._certify(obj, rig, fresh, nodes, bm,
                                                original_nodes=nodes, bone_names=names))
        assert names[0] in message and names[1] in message
        assert 'marked joint' in message
    finally:
        bm.free()


def test_alignment_coverage_reads_actual_tails_instead_of_bridging_head_gaps():
    for lateral in (True, False):
        obj, rig, _ = root_fixture()
        with targets.edit_rig(C, rig):
            bone = rig.data.edit_bones['f_index.01.L']
            direction = (bone.tail-bone.head).normalized()
            bone.tail += Vector((0, 0, .02)) if lateral else -direction*.02
            expected_tail = bone.tail.copy()
        calls, covered = [], marks._covered_span
        def inspect(a, b, original_segments, tolerance):
            assert (original_segments[0][1]-expected_tail).length < 1e-7
            result = covered(a, b, original_segments, tolerance)
            calls.append(result)
            return result
        # Both deviations are smaller than the resolver's existing gap bound.
        # They require current volume proof rather than invented head-to-head
        # coverage, but the proposed straight fixture line is still safe.
        with patch.object(marks, '_covered_span', side_effect=inspect):
            assert marks.align(C)['keys'] == ['INDEX.L']
        assert len(calls) == 3 and not all(calls)


if __name__ == '__main__':
    try:
        character_designer.register()
        tests = [test for name, test in list(globals().items()) if name.startswith('test_')]
        for test in tests:
            test()
            print('PASS', test.__name__, flush=True)
        print('FINGER_MARK_ROOT_TESTS_PASSED', len(tests), flush=True)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
