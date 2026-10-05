"""Metadata-only Hair strand identities and complete reciprocal pair proofs.

Run serially in disposable Blender factory state. This script never opens an
artist file; its save/reopen check uses one temporary blend in this process.
"""
import copy
import importlib
import json
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import hair_bones_groups as groups
from character_designer import hair_bones_rig as hair
from character_designer import hair_bones_topology as topology
from character_designer import hair_strand_registry as registry
from character_designer import native_symmetry_pairs
from character_designer import symmetry_pairs
from character_designer.symmetry_pairs import VertexPairMap
from test_hair_bones_rig_blender import activate, build, make_armature, make_hair, plain_snapshot, reset


def expect_error(action, message=None):
    try:
        action()
    except ValueError as exc:
        if message is not None:
            assert message in str(exc), str(exc)
        return
    raise AssertionError('Expected conservative Hair registry rejection')


def empty_pairs(obj):
    return VertexPairMap((), (), tuple(range(len(obj.data.vertices))), (), 1, True, ('Unpaired fixture',))


def bound_fixture(*, strands=2, count=4, captured=False, symmetric=False, centered=False):
    reset()
    armature = make_armature()
    obj, plans = make_hair(strands=1 if symmetric else strands)
    if centered:
        # One actual symmetric triangular tube: one seam corner and one
        # opposite pair in every cross-section. The other strand stays separate.
        for row in range(7):
            for corner, (x, y) in enumerate(((0., .055), (.055, -.0275), (-.055, -.0275))):
                vertex = obj.data.vertices[row * 3 + corner]
                vertex.co.x, vertex.co.y = x, .015 * row * row + y
        first = dict(plans[0])
        first['centers'] = tuple((0., .015 * row * row, 2.0 - .19 * row) for row in range(1, 7))
        plans = (first,) + plans[1:]
        obj.data.update()
    if symmetric:
        points = [tuple(vertex.co) for vertex in obj.data.vertices]
        faces = [tuple(face.vertices) for face in obj.data.polygons]
        offset = len(points)
        points.extend((-x, y, z) for x, y, z in tuple(points))
        faces.extend(tuple(index + offset for index in reversed(face)) for face in tuple(faces))
        faces.append((0, 1, offset + 1, offset))
        obj.data.clear_geometry()
        obj.data.from_pydata(points, [], faces)
        obj.data.update()
        left = dict(plans[0])
        right = dict(left, signature='fixture-reflected-strand')
        right['layers'] = tuple(tuple(index + offset for index in layer) for layer in left['layers'])
        right['vertices'] = tuple(index + offset for index in left['vertices'])
        right['centers'] = tuple((-x, y, z) for x, y, z in left['centers'])
        plans = (left, right)
    if captured:
        plans = tuple(dict(plan, signature=topology._signature(plan['layers'])) for plan in plans)
        groups.capture_plans(obj, plans)
    result = build(obj, plans, bone_count=count, armature=armature, parent_bone='spine.006')
    activate(obj)
    return obj, plans, result


def initialize_unpaired(obj):
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
        return registry.initialize(obj)


def rewrite_geometry(obj, points, extra_edges=(), extra_faces=()):
    """Explicit fixture construction; never called as part of a registry read."""
    faces = [tuple(face.vertices) for face in obj.data.polygons]
    group_names = [group.name for group in obj.vertex_groups]
    memberships = [(vertex.index, group_names[entry.group], entry.weight)
                   for vertex in obj.data.vertices for entry in vertex.groups]
    obj.data.clear_geometry()
    obj.data.from_pydata(points, extra_edges, faces + list(extra_faces))
    obj.data.update()
    # Blender 5.1 clears object vertex-group definitions with this mesh rebuild.
    # The old core indices stay fixed, so restore this fixture's original bind.
    for name in group_names:
        if obj.vertex_groups.get(name) is None:
            obj.vertex_groups.new(name=name)
    for index, name, weight in memberships:
        obj.vertex_groups[name].add([index], weight, 'REPLACE')
    bpy.context.view_layer.update()


def test_initialize_read_is_metadata_only_and_legacy_capture_unchanged():
    obj, _plans, _result = bound_fixture(captured=True)
    before = plain_snapshot(obj)
    capture, binding = obj[groups.GROUPS_KEY], obj[hair.RECORD_KEY]
    data = registry.initialize(obj)  # Exercise actual native topology lookup.
    assert plain_snapshot(obj) == before
    assert obj[groups.GROUPS_KEY] == capture and obj[hair.RECORD_KEY] == binding
    assert data['source_uid'] == uuid.UUID(json.loads(binding)['source_id']).hex
    assert len(data['strands']) == 2
    saved = obj[registry.REGISTRY_KEY]
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', side_effect=AssertionError('Read rebuilt pair proof')):
        assert registry.read(obj) == data
        assert registry.initialize(obj) == data
        assert registry.read(obj, validate=False) == json.loads(saved)
    assert obj[registry.REGISTRY_KEY] == saved and plain_snapshot(obj) == before
    detached = registry.read(obj)
    detached['strands'][0]['bones'][0] = 'Changed only detached result'
    assert obj[registry.REGISTRY_KEY] == saved


def test_rename_basis_coordinate_edits_and_reload_preserve_identity():
    obj, _plans, result = bound_fixture()
    first = initialize_unpaired(obj)
    saved = obj[registry.REGISTRY_KEY]
    identity = [(strand['strand_id'], strand['chain_id'], strand['order']) for strand in first['strands']]
    obj.name = 'Artist renamed Hair'
    result['armature'].name = 'Artist renamed Armature'
    for index, name in enumerate(first['strands'][0]['bones']):
        result['armature'].data.bones[name].name = f'Artist chain segment {index + 1}'
    result['armature'].data.bones['spine.006'].name = 'Artist Head Anchor'
    obj.shape_key_add(name='Basis')
    expression = obj.shape_key_add(name='Artist asymmetry')
    expression.data[5].co.y += 0.03
    obj.data.shape_keys.reference_key.data[4].co.x += 0.001
    obj.data.vertices[4].co.x += 0.001
    bpy.context.view_layer.update()
    before = plain_snapshot(obj)
    reloaded = importlib.reload(registry)
    current = reloaded.read(obj)
    assert [(strand['strand_id'], strand['chain_id'], strand['order']) for strand in current['strands']] == identity
    assert current['strands'][0]['bones'][0] == 'Artist chain segment 1'
    assert current['strands'][0]['anchor']['name'] == 'Artist Head Anchor'
    assert obj[registry.REGISTRY_KEY] == saved and plain_snapshot(obj) == before


def test_source_copy_and_bone_ownership_parent_changes_are_rejected():
    obj, _plans, result = bound_fixture()
    data = initialize_unpaired(obj)
    clone = obj.copy()
    clone.data = obj.data.copy()
    bpy.context.scene.collection.objects.link(clone)
    expect_error(lambda: registry.read(clone), 'missing, extra or reassigned')
    assert clone[registry.REGISTRY_KEY] == obj[registry.REGISTRY_KEY]
    bone = result['armature'].data.bones[data['strands'][0]['bones'][0]]
    saved = obj[registry.REGISTRY_KEY]
    bone[hair.SOURCE_KEY] = clone
    expect_error(lambda: registry.reconcile(obj), 'missing, extra or reassigned')
    bone[hair.SOURCE_KEY] = obj
    bone[hair.OWNER_KEY] = 'foreign-owner'
    expect_error(lambda: registry.read(obj), 'source ownership changed')
    bone[hair.OWNER_KEY] = hair.OWNER_VALUE
    activate(result['armature'], 'EDIT')
    result['armature'].data.edit_bones[bone.name].parent = None
    activate(obj)
    expect_error(lambda: registry.reconcile(obj), 'recorded parent')
    assert obj[registry.REGISTRY_KEY] == saved


def test_full_chain_pairs_require_every_vertex_every_layer_and_reciprocity():
    obj, _plans, _result = bound_fixture(symmetric=True)
    live, _record, geometry = registry._live(obj)
    first, second = live['strands']
    offset = len(obj.data.vertices) // 2
    pair_map = VertexPairMap(tuple((index + offset, index) for index in first['vertices']), (), (), (), 1, True)
    registry._topology_pairs(live, geometry[0], pair_map)
    registry._validate_links(live)
    assert [strand['side'] for strand in live['strands']] == ['L', 'R']
    assert first['mirror_id'] == second['strand_id'] and second['mirror_id'] == first['strand_id']
    assert first['pair_id'] == second['pair_id'] and first['pair_proof'] == 'TOPOLOGY'
    assert len(first['vertex_map']) == len(first['vertices'])
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=pair_map):
        saved = registry.initialize(obj)
    assert saved['strands'][0]['pair_proof'] == 'TOPOLOGY'
    invalid = copy.deepcopy(saved)
    invalid['strands'][0]['vertex_map'].pop()
    expect_error(lambda: registry._validate_links(invalid), 'partial or nonreciprocal')

    partial, _, _ = registry._live(obj)
    incomplete = VertexPairMap(pair_map.pairs[:-1], (), (), (), 1, True)
    registry._topology_pairs(partial, geometry[0], incomplete)
    assert all(strand['side'] == 'U' and not strand['vertex_map'] for strand in partial['strands'])
    wrong_layers, _, _ = registry._live(obj)
    wrong_layers['strands'][1]['layers'] = list(reversed(wrong_layers['strands'][1]['layers']))
    registry._topology_pairs(wrong_layers, geometry[0], pair_map)
    assert all(strand['side'] == 'U' for strand in wrong_layers['strands'])
    duplicate, _, _ = registry._live(obj)
    duplicate['strands'].append(copy.deepcopy(duplicate['strands'][1]))
    registry._topology_pairs(duplicate, geometry[0], pair_map)
    assert all(strand['side'] == 'U' for strand in duplicate['strands'])


def test_self_mapped_full_strand_is_center_and_manual_center_is_rejected():
    obj, _plans, _result = bound_fixture(centered=True)
    data, _, geometry = registry._live(obj)
    center, other = data['strands']
    # Opposite corners exchange WITHIN one complete central strand, while
    # its longitudinal seam maps to itself.
    mapping = VertexPairMap(tuple((layer[2], layer[1]) for layer in center['layers']),
                            tuple(layer[0] for layer in center['layers']),
                            tuple(other['vertices']), (), 1, True)
    registry._topology_pairs(data, geometry[0], mapping)
    assert center['side'] == 'C' and center['mirror_id'] == center['strand_id']
    assert center['pair_proof'] == 'TOPOLOGY' and other['side'] == 'U'
    registry._commit(obj, data)
    expect_error(lambda: registry.manual_pair(obj, center['strand_id'], other['strand_id']), 'Center strands')


def test_existing_native_mirror_provenance_has_priority():
    from test_hair_bones_mirror_controls_blender import build_fixture
    _source, _plans, _original, result, _chains, _indices, _baseline = build_fixture()
    obj = result['mesh']
    activate(obj)
    before = plain_snapshot(obj)
    binding = obj[hair.RECORD_KEY]
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', side_effect=AssertionError('Ignored Mirror provenance')):
        data = registry.initialize(obj)
    assert {strand['side'] for strand in data['strands']} == {'L', 'R', 'C'}
    assert all(strand['pair_proof'] == 'MIRROR' for strand in data['strands'])
    assert obj[hair.RECORD_KEY] == binding and plain_snapshot(obj) == before
    mirror = next(item for item in obj.modifiers if item.type == 'MIRROR')
    mirror.use_mirror_vertex_groups = False
    expect_error(lambda: registry.read(obj))


def test_explicit_manual_pair_and_count_mismatch_are_atomic():
    obj, _plans, _result = bound_fixture()
    data = initialize_unpaired(obj)
    left, right = data['strands']
    saved = obj[registry.REGISTRY_KEY]
    expect_error(lambda: registry.manual_pair(obj, left['strand_id'], left['strand_id']), 'different')
    assert obj[registry.REGISTRY_KEY] == saved
    paired = registry.manual_pair(obj, left['strand_id'], right['strand_id'])
    assert paired['strands'][0]['side'] == 'L' and paired['strands'][1]['side'] == 'R'
    assert all(strand['pair_proof'] == 'MANUAL' for strand in paired['strands'])
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
        assert registry.reconcile(obj)['strands'][0]['pair_id'] == paired['strands'][0]['pair_id']
    altered = copy.deepcopy(paired)
    altered['strands'][1]['bones'].pop()
    altered['strands'][1]['rest'].pop()
    expect_error(lambda: registry._validate_links(altered), 'count-compatible')


def test_rest_refresh_keeps_ids_but_cannot_bless_structural_edits():
    obj, _plans, result = bound_fixture()
    old = initialize_unpaired(obj)
    saved = obj[registry.REGISTRY_KEY]
    bone_name = old['strands'][0]['bones'][-1]
    activate(result['armature'], 'EDIT')
    result['armature'].data.edit_bones[bone_name].tail.x += 0.002
    activate(obj)
    expect_error(lambda: registry.read(obj), 'Rest or segmentation changed')
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
        current = registry.reconcile(obj)
    assert [strand['strand_id'] for strand in old['strands']] == [strand['strand_id'] for strand in current['strands']]
    assert old['strands'][0]['rest'] != current['strands'][0]['rest']
    assert obj[registry.REGISTRY_KEY] != saved
    saved = obj[registry.REGISTRY_KEY]
    result['armature'].data.bones[bone_name].inherit_scale = 'NONE'
    expect_error(lambda: registry.reconcile(obj), 'inheritance changed')
    assert obj[registry.REGISTRY_KEY] == saved


def test_same_signature_explicit_segment_change_preserves_ids_and_order():
    obj, _plans, result = bound_fixture()
    old = initialize_unpaired(obj)
    strand = old['strands'][0]
    names = strand['bones']
    activate(result['armature'], 'EDIT')
    edit_bones = result['armature'].data.edit_bones
    previous = edit_bones[names[-2]]
    previous.tail = edit_bones[names[-1]].tail
    edit_bones.remove(edit_bones[names[-1]])
    activate(obj)
    obj.vertex_groups.remove(obj.vertex_groups[names[-1]])
    binding = json.loads(obj[hair.RECORD_KEY])
    chain = binding['chains'][0]
    chain['bones'] = names[:-1]
    chain['count'] = chain['requested_count'] = len(names) - 1
    chain['rest'] = [hair._bone_state(result['armature'].data.bones[name]) for name in chain['bones']]
    obj[hair.RECORD_KEY] = json.dumps(binding)
    expect_error(lambda: registry.read(obj), 'Rest or segmentation changed')
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
        current = registry.reconcile(obj)
    assert current['strands'][0]['strand_id'] == strand['strand_id']
    assert current['strands'][0]['chain_id'] == strand['chain_id']
    assert current['strands'][0]['order'] == strand['order']
    assert len(current['strands'][0]['bones']) == len(names) - 1


def test_topology_changes_require_explicit_reconcile_and_no_reassignment():
    obj, _plans, _result = bound_fixture()
    old = initialize_unpaired(obj)
    saved = obj[registry.REGISTRY_KEY]
    obj.data.vertices.add(1)
    obj.data.vertices[-1].co = (3.0, 0.0, 1.0)
    obj.data.update()
    expect_error(lambda: registry.read(obj), 'topology changed')
    assert obj[registry.REGISTRY_KEY] == saved
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
        current = registry.reconcile(obj)
    assert current['topology'] != old['topology']
    assert [strand['strand_id'] for strand in current['strands']] == [strand['strand_id'] for strand in old['strands']]
    binding = json.loads(obj[hair.RECORD_KEY])
    binding['chains'][0]['layers'] = binding['chains'][1]['layers']
    binding['chains'][0]['vertices'] = binding['chains'][1]['vertices']
    obj[hair.RECORD_KEY] = json.dumps(binding)
    expect_error(lambda: registry.reconcile(obj), 'reassigned')


def test_write_and_late_validation_failures_roll_back_exact_metadata():
    obj, _plans, _result = bound_fixture()
    original_write = registry._write
    before = plain_snapshot(obj)
    def fail_after_write(source, data):
        original_write(source, data)
        raise RuntimeError('Injected post-write failure')
    with patch.object(registry, '_write', side_effect=fail_after_write):
        try:
            initialize_unpaired(obj)
        except RuntimeError as exc:
            assert 'post-write' in str(exc)
        else:
            raise AssertionError('Missing injected write failure')
    assert registry.REGISTRY_KEY not in obj and plain_snapshot(obj) == before
    data = initialize_unpaired(obj)
    saved = obj[registry.REGISTRY_KEY]
    before = plain_snapshot(obj)
    with patch.object(registry, 'read', side_effect=RuntimeError('Injected final proof failure')):
        try:
            registry._commit(obj, data)
        except RuntimeError as exc:
            assert 'final proof' in str(exc)
        else:
            raise AssertionError('Missing injected final validation failure')
    assert obj[registry.REGISTRY_KEY] == saved and plain_snapshot(obj) == before


def test_exact_geometry_recovers_wrong_native_self_table_and_preserves_coordinate_edits():
    obj, _plans, _result = bound_fixture(symmetric=True)
    before = plain_snapshot(obj)
    # The real native wrapper rejects self indices away from X=0. Exact
    # geometry plus complete incidence must recover the two whole strands.
    with patch.object(native_symmetry_pairs, '_lookup_for_topology', return_value=tuple(range(len(obj.data.vertices)))), \
            patch.object(symmetry_pairs, 'build_vertex_pairs', side_effect=AssertionError('Geometry needed no graph fallback')):
        data = registry.initialize(obj)
    assert plain_snapshot(obj) == before
    assert [strand['side'] for strand in data['strands']] == ['L', 'R']
    assert all(strand['pair_proof'] == 'GEOMETRY' for strand in data['strands'])
    assert all(strand['boundary_map'] for strand in data['strands'])
    saved = obj[registry.REGISTRY_KEY]
    identities = [(strand['strand_id'], strand['mirror_id'], strand['vertex_map'], strand['boundary_map']) for strand in data['strands']]
    obj.data.vertices[data['strands'][0]['vertices'][2]].co.y += .003
    obj.data.update()
    current = registry.read(obj)
    assert [(strand['strand_id'], strand['mirror_id'], strand['vertex_map'], strand['boundary_map'])
            for strand in current['strands']] == identities
    assert obj[registry.REGISTRY_KEY] == saved
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', side_effect=AssertionError('Reconcile re-guessed a proven pair')), \
            patch.object(symmetry_pairs, 'build_vertex_pairs', side_effect=AssertionError('Reconcile re-guessed a proven pair')):
        current = registry.reconcile(obj)
    assert current['strands'][0]['pair_proof'] == 'GEOMETRY'
    assert current['strands'][0]['mirror_id'] == data['strands'][0]['mirror_id']


def test_ambiguous_exact_coordinates_cannot_force_a_geometry_pair():
    obj, _plans, _result = bound_fixture(symmetric=True)
    points = [tuple(vertex.co) for vertex in obj.data.vertices]
    points.append(points[3])
    rewrite_geometry(obj, points)
    before = plain_snapshot(obj)
    empty = empty_pairs(obj)
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty), \
            patch.object(symmetry_pairs, 'build_vertex_pairs', return_value=empty):
        data = registry.initialize(obj)
    assert all(strand['side'] == 'U' and not strand['vertex_map'] for strand in data['strands'])
    assert plain_snapshot(obj) == before


def test_exact_geometry_center_keeps_complete_internal_involution():
    obj, _plans, _result = bound_fixture(centered=True)
    before = plain_snapshot(obj)
    empty = empty_pairs(obj)
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty), \
            patch.object(symmetry_pairs, 'build_vertex_pairs', return_value=empty):
        data = registry.initialize(obj)
    center, other = data['strands']
    assert center['pair_proof'] == 'GEOMETRY' and center['side'] == 'C'
    assert center['mirror_id'] == center['strand_id'] and other['side'] == 'U'
    mapping = dict(center['vertex_map'])
    boundary = dict(center['boundary_map'])
    assert any(index != target for index, target in mapping.items())
    assert all(mapping[target] == index for index, target in mapping.items())
    assert boundary and all(boundary[target] == index for index, target in boundary.items())
    assert registry.read(obj) == data and plain_snapshot(obj) == before


def test_geometry_requires_exact_layers_and_complete_boundary_edge_face_incidence():
    obj, _plans, _result = bound_fixture(symmetric=True)
    data, _, geometry = registry._live(obj)
    data['strands'][1]['layers'] = list(reversed(data['strands'][1]['layers']))
    registry._lookup_pairs(data, geometry[0], registry._exact_lookup(geometry[0]), proof='GEOMETRY', graph=registry._mesh_graph(*geometry))
    assert all(strand['side'] == 'U' for strand in data['strands'])

    # Both core domains/layers are exactly mirrored, and all core vertex
    # degrees remain equal. Their outside face incidence is deliberately
    # connected to different reflected boundary corners.
    points = [tuple(vertex.co) for vertex in obj.data.vertices]
    offset, start = len(points) // 2, len(points)
    points.extend(((2., 4., 5.), (3., 6., 7.), (-2., 4., 5.), (-3., 6., 7.)))
    rewrite_geometry(obj, points, extra_faces=((3, 4, start, start + 1),
                     (offset + 3, offset + 4, start + 3, start + 2)))
    data, _, geometry = registry._live(obj)
    first, second = data['strands']
    assert {registry._exact_lookup(geometry[0])[index] for index in first['vertices']} == set(second['vertices'])
    graph = registry._mesh_graph(*geometry)
    assert all(len(graph['adjacency'][index]) == len(graph['adjacency'][registry._exact_lookup(geometry[0])[index]])
               for index in first['vertices'])
    before = plain_snapshot(obj)
    empty = empty_pairs(obj)
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty), \
            patch.object(symmetry_pairs, 'build_vertex_pairs', return_value=empty):
        current = registry.initialize(obj)
    assert all(strand['side'] == 'U' for strand in current['strands'])
    assert plain_snapshot(obj) == before


def test_existing_unique_graph_prover_recovers_geometry_misaligned_whole_pair():
    obj, _plans, _result = bound_fixture(symmetric=True)
    points = [tuple(vertex.co) for vertex in obj.data.vertices]
    offset, tag = len(points) // 2, len(points)
    # Distinguish the first root corner structurally on BOTH sides. This
    # removes the tube's transverse graph automorphism without using names
    # or position to choose among possible vertices.
    points.extend(((.7, 2., 3.1), (-.7, 2., 3.1)))
    rewrite_geometry(obj, points, extra_edges=((0, tag), (offset, tag + 1)))
    obj.data.vertices[4].co.y += .001
    obj.data.update()
    before = plain_snapshot(obj)
    original = symmetry_pairs.build_vertex_pairs
    calls = []
    def prove_graph(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs == {'centerline_tolerance': 0.0}
        return original(*args, **kwargs)
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)), \
            patch.object(symmetry_pairs, 'build_vertex_pairs', side_effect=prove_graph):
        data = registry.initialize(obj)
    assert calls and all(strand['pair_proof'] == 'GRAPH' for strand in data['strands'])
    assert all(strand['boundary_map'] for strand in data['strands'])
    assert plain_snapshot(obj) == before
    assert registry.read(obj)['strands'][0]['mirror_id'] == data['strands'][0]['mirror_id']


def test_strict_reader_rejects_tampered_mapped_topology_and_boundary_proofs():
    obj, _plans, _result = bound_fixture(symmetric=True)
    with patch.object(native_symmetry_pairs, 'build_vertex_pairs', return_value=empty_pairs(obj)):
        data = registry.initialize(obj)
    assert data['strands'][0]['pair_proof'] == 'GEOMETRY'
    broken = copy.deepcopy(data)
    for strand in broken['strands']:
        strand['boundary_map'] = []
    obj[registry.REGISTRY_KEY] = json.dumps(broken)
    registry.read(obj, validate=False)  # Reciprocal empty maps alone cannot prove the current boundary.
    expect_error(lambda: registry.read(obj), 'boundary incidence')
    broken = copy.deepcopy(data)
    lookup = dict(broken['strands'][0]['vertex_map'])
    a, b = broken['strands'][0]['layers'][0][:2]
    lookup[a], lookup[b] = lookup[b], lookup[a]
    broken['strands'][0]['vertex_map'] = [[index, lookup[index]] for index in broken['strands'][0]['vertices']]
    reverse = {value: key for key, value in lookup.items()}
    broken['strands'][1]['vertex_map'] = [[index, reverse[index]] for index in broken['strands'][1]['vertices']]
    obj[registry.REGISTRY_KEY] = json.dumps(broken)
    registry.read(obj, validate=False)  # Complete reciprocal layer sets, but incorrect edge/face cycles.
    expect_error(lambda: registry.read(obj), 'edges, faces')


def test_native_save_reopen_preserves_source_links_and_stable_ids():
    obj, _plans, _result = bound_fixture(captured=True)
    data = initialize_unpaired(obj)
    name = obj.name
    raw, capture, binding = obj[registry.REGISTRY_KEY], obj[groups.GROUPS_KEY], obj[hair.RECORD_KEY]
    with tempfile.TemporaryDirectory(prefix='cd_hair_strand_registry_') as directory:
        path = str(Path(directory) / 'registry_roundtrip.blend')
        result = bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
        assert result == {'FINISHED'}
        result = bpy.ops.wm.open_mainfile(filepath=path, load_ui=False)
        assert result == {'FINISHED'}
        obj = bpy.data.objects[name]
        current = importlib.reload(registry).read(obj)
        assert current == data
        assert obj[registry.REGISTRY_KEY] == raw and obj[groups.GROUPS_KEY] == capture and obj[hair.RECORD_KEY] == binding
        armature = obj[hair.RIG_KEY]
        assert all(armature.data.bones[name][hair.SOURCE_KEY] is obj
                   for strand in current['strands'] for name in strand['bones'])


def main():
    tests = (test_initialize_read_is_metadata_only_and_legacy_capture_unchanged,
             test_rename_basis_coordinate_edits_and_reload_preserve_identity,
             test_source_copy_and_bone_ownership_parent_changes_are_rejected,
             test_full_chain_pairs_require_every_vertex_every_layer_and_reciprocity,
             test_self_mapped_full_strand_is_center_and_manual_center_is_rejected,
             test_existing_native_mirror_provenance_has_priority,
             test_explicit_manual_pair_and_count_mismatch_are_atomic,
             test_rest_refresh_keeps_ids_but_cannot_bless_structural_edits,
             test_same_signature_explicit_segment_change_preserves_ids_and_order,
             test_topology_changes_require_explicit_reconcile_and_no_reassignment,
             test_write_and_late_validation_failures_roll_back_exact_metadata,
             test_exact_geometry_recovers_wrong_native_self_table_and_preserves_coordinate_edits,
             test_ambiguous_exact_coordinates_cannot_force_a_geometry_pair,
             test_exact_geometry_center_keeps_complete_internal_involution,
             test_geometry_requires_exact_layers_and_complete_boundary_edge_face_incidence,
             test_existing_unique_graph_prover_recovers_geometry_misaligned_whole_pair,
             test_strict_reader_rejects_tampered_mapped_topology_and_boundary_proofs,
             test_native_save_reopen_preserves_source_links_and_stable_ids)
    for test in tests:
        test()
        print('PASS', test.__name__)
    print('HAIR_STRAND_REGISTRY_TESTS_OK', len(tests))


if __name__ == '__main__':
    main()
