"""Regressions against Blender's own Topology Mirror, not a substitute matcher.

Run in factory-startup background Blender. Artist buffers are fixtures and native
selection probes run on disposable clones; no existing .blend is opened or saved.
"""
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
sys.path.insert(0, str(ROOT / 'tests'))
from character_designer import refine_symmetry as refine
from character_designer import native_symmetry_pairs as native
from test_refine_symmetry_blender import (
    assert_deltas_equal, assert_preserved_equal, close, coordinates, fixture,
    key_coordinates, key_deltas, preserved, reset, select_vertices, whole_state,
)


def mirrored(coordinate, axis):
    result = list(coordinate)
    result['XYZ'.index(axis)] *= -1
    return tuple(result)


def axis_fixture(axis='X', **kwargs):
    obj = fixture(**kwargs)
    dimension = 'XYZ'.index(axis)
    if dimension:
        sequences = [obj.data.vertices]
        if obj.data.shape_keys:
            sequences.extend(key.data for key in obj.data.shape_keys.key_blocks)
        for points in sequences:
            for point in points:
                co = list(point.co)
                co[0], co[dimension] = co[dimension], co[0]
                point.co = co
        obj.data.update()
    return obj


def native_single_vertex_lookup(obj, axis, *, topology):
    """Independent native oracle: one selected vertex per operator invocation.

    This intentionally does not use the production bit-mask extraction. The
    clone has the same vertex indices, edges and faces and explicit Basis coords.
    """
    assert obj.mode == 'OBJECT'
    before = whole_state(obj)
    prior_active = bpy.context.view_layer.objects.active
    prior_selection = [(item, item.select_get())
                       for item in bpy.context.view_layer.objects]
    prior_select_mode = tuple(bpy.context.scene.tool_settings.mesh_select_mode)
    clone = obj.copy()
    mesh = obj.data.copy()
    clone.data = mesh
    bpy.context.collection.objects.link(clone)
    try:
        if mesh.shape_keys:
            clone.shape_key_clear()
        for point, co in zip(mesh.vertices, coordinates(obj)):
            point.co = co
        clone.parent = None
        clone.modifiers.clear()
        clone.hide_viewport = False
        clone.hide_set(False)
        for item in (*mesh.vertices, *mesh.edges, *mesh.polygons):
            item.hide = False
            item.select = False
        mesh.use_mirror_topology = topology
        for dimension in 'xyz':
            setattr(mesh, f'use_mirror_{dimension}', dimension == axis.lower())
        for item, _selected in prior_selection:
            item.select_set(False)
        clone.select_set(True)
        bpy.context.view_layer.objects.active = clone
        bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)
        bpy.ops.object.mode_set(mode='EDIT')
        lookup = []
        for source in range(len(mesh.vertices)):
            bpy.ops.mesh.select_all(action='DESELECT')
            bm = bmesh.from_edit_mesh(mesh)
            bm.verts.ensure_lookup_table()
            bm.verts[source].select = True
            bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
            result = bpy.ops.mesh.select_mirror(axis={axis}, extend=False)
            assert result == {'FINISHED'}, (axis, topology, source, result)
            selected = [v.index for v in bm.verts if v.select]
            assert len(selected) <= 1, (source, selected)
            lookup.append(selected[0] if selected else -1)
        return tuple(lookup)
    finally:
        if clone.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.data.objects.remove(clone, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
        for item, selected in prior_selection:
            item.select_set(selected)
        bpy.context.view_layer.objects.active = prior_active
        bpy.context.scene.tool_settings.mesh_select_mode = prior_select_mode
        assert whole_state(obj) == before, 'Native oracle changed artist data'


def accepted_pairs(lookup, basis, axis):
    dimension = 'XYZ'.index(axis)
    pairs = []
    for index, target in enumerate(lookup):
        if index >= target or target < 0 or lookup[target] != index:
            continue
        first, second = basis[index][dimension], basis[target][dimension]
        if first * second < 0:
            pairs.append((index, target) if first < 0 else (target, index))
    return tuple(sorted(pairs))


def live_snapshot(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    return (
        tuple((tuple(v.co), v.select, v.hide) for v in bm.verts),
        tuple((name, tuple(tuple(v[bm.verts.layers.shape[name]]) for v in bm.verts))
              for name in bm.verts.layers.shape.keys()),
        tuple((tuple(v.index for v in e.verts), e.select, e.hide) for e in bm.edges),
        tuple((tuple(v.index for v in f.verts), f.select, f.hide) for f in bm.faces),
        tuple(v.index for v in bm.select_history),
        tuple(bm.verts.layers.int.keys()),
    )


def test_native_table_equals_independent_single_vertex_operator_on_all_axes():
    for axis in 'XYZ':
        obj = axis_fixture(axis)
        expected = native_single_vertex_lookup(obj, axis, topology=True)
        basis = coordinates(obj)
        edges = tuple(tuple(e.vertices) for e in obj.data.edges)
        faces = tuple(tuple(f.vertices) for f in obj.data.polygons)
        before = whole_state(obj)
        counts = len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.meshes)
        actual = native.native_vertex_lookup(basis, edges, faces, axis=axis)
        assert tuple(actual) == expected, (axis, actual, expected)
        distorted = tuple(tuple(value + (index + 1) * .173 for value in co)
                          for index, co in enumerate(basis))
        assert tuple(native.native_vertex_lookup(distorted, edges, faces, axis=axis)) == expected
        report = refine.analyze(obj, selected_only=False, axis=axis)
        pairs = accepted_pairs(expected, basis, axis)
        assert len(pairs) >= 6, ('Fixture did not exercise enough native pairs', axis, pairs)
        assert tuple(sorted(report.pairs)) == pairs, (axis, report.pairs, pairs)
        assert whole_state(obj) == before, 'Analyze changed artist coordinates or selection'
        assert (len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.meshes)) == counts


def test_native_analysis_does_not_flush_unsynced_edit_basis_or_selection():
    obj = axis_fixture('X')
    obj.active_shape_key_index = 0
    select_vertices(obj, [3, 8, 17])
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[3].co.y += .087
    bm.select_history.add(bm.verts[8])
    before = live_snapshot(obj), coordinates(obj), key_coordinates(obj)
    counts = len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.meshes)
    report = refine.analyze(obj, selected_only=False)
    assert report.matched >= 6
    assert obj.mode == 'EDIT' and bpy.context.view_layer.objects.active is obj
    assert (live_snapshot(obj), coordinates(obj), key_coordinates(obj)) == before
    assert (len(bpy.data.scenes), len(bpy.data.objects), len(bpy.data.meshes)) == counts
    bpy.ops.object.mode_set(mode='OBJECT')


def test_native_same_side_pair_is_reported_unmatched_and_never_forced_to_plane():
    obj = axis_fixture('X')
    lookup = native_single_vertex_lookup(obj, 'X', topology=True)
    negative, positive = accepted_pairs(lookup, coordinates(obj), 'X')[0]
    obj.data.vertices[negative].co.x = obj.data.shape_keys.reference_key.data[negative].co.x = 1.3
    select_vertices(obj, [negative, positive])
    before = whole_state(obj)
    report = refine.analyze(obj)
    assert not report.pairs and not report.centerline
    assert set(report.unmatched) == {negative, positive}, report
    try:
        refine.plan(obj)
    except refine.RefineSymmetryError:
        pass
    else:
        raise AssertionError('Same-side native pair was forced into an assumed mirror direction')
    assert whole_state(obj) == before


def test_native_pair_cache_tracks_connectivity_when_vertex_and_edge_counts_stay_equal():
    basis = ((-1., 0., 0.), (0., 0., 0.), (1., 0., 0.), (4., 3., 2.))
    snapshots = []
    for edges in (((0, 1), (2, 3)), ((0, 1), (1, 2))):
        reset()
        mesh = bpy.data.meshes.new('Same Counts Different Connectivity')
        mesh.from_pydata(basis, edges, [])
        obj = bpy.data.objects.new('Native Connectivity Fixture', mesh)
        bpy.context.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        expected = native_single_vertex_lookup(obj, 'X', topology=True)
        actual = tuple(native.native_vertex_lookup(basis, edges, ()))
        assert actual == expected, (edges, actual, expected)
        snapshots.append(actual)
    assert snapshots[0] != snapshots[1], 'The fixture did not distinguish connectivity'


def test_failed_isolated_native_lookup_restores_artist_context_and_removes_temporary_ids():
    obj = axis_fixture('X')
    basis = coordinates(obj)
    edges = tuple(tuple(e.vertices) for e in obj.data.edges)
    faces = tuple(tuple(f.vertices) for f in obj.data.polygons)
    select_vertices(obj, [3, 17])
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[3].co.z += .027
    before = live_snapshot(obj), coordinates(obj), key_coordinates(obj)
    counts = tuple(len(ids) for ids in (bpy.data.scenes, bpy.data.objects,
                                      bpy.data.meshes, bpy.data.shape_keys))
    native.clear_native_pair_cache()
    with patch.object(native.bmesh, 'update_edit_mesh', side_effect=RuntimeError('Injected native probe failure')):
        try:
            native.native_vertex_lookup(basis, edges, faces)
        except native.SymmetryPairError as error:
            assert 'Injected native probe failure' in str(error)
        else:
            raise AssertionError('Failed native extraction returned a guessed table')
    assert obj.mode == 'EDIT' and bpy.context.view_layer.objects.active is obj
    assert (live_snapshot(obj), coordinates(obj), key_coordinates(obj)) == before
    assert tuple(len(ids) for ids in (bpy.data.scenes, bpy.data.objects,
                                    bpy.data.meshes, bpy.data.shape_keys)) == counts
    bpy.ops.object.mode_set(mode='OBJECT')


def test_directional_native_pair_repairs_only_selected_target_and_keeps_all_keys():
    for axis in 'XYZ':
        for mode, source_offset, target_offset in (
            ('LEFT_TO_RIGHT', 1, 0), ('RIGHT_TO_LEFT', 0, 1),
        ):
            for edit_mode in (False, True):
                obj = axis_fixture(axis)
                obj.active_shape_key_index = 0
                pairs = accepted_pairs(native_single_vertex_lookup(obj, axis, topology=True),
                                       coordinates(obj), axis)
                # Choose a genuinely misaligned native pair, not an aligned no-op.
                pair = next(pair for pair in pairs
                            if (Vector(coordinates(obj)[pair[0]]) -
                                Vector(mirrored(coordinates(obj)[pair[1]], axis))).length > .01)
                source, target = pair[source_offset], pair[target_offset]
                if edit_mode:
                    bpy.ops.object.mode_set(mode='EDIT')
                    bpy.ops.object.mode_set(mode='OBJECT')
                select_vertices(obj, [target])
                before = whole_state(obj)
                deltas = key_deltas(obj)
                if edit_mode:
                    bpy.ops.object.mode_set(mode='EDIT')
                    bpy.ops.mesh.select_all(action='DESELECT')
                    bm = bmesh.from_edit_mesh(obj.data)
                    bm.verts.ensure_lookup_table()
                    bm.verts[target].select_set(True)
                    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
                plan = refine.plan(obj, mode=mode, axis=axis)
                assert set(plan.positions) == {target}, (axis, mode, plan.positions)
                validation = refine.apply(obj, plan)
                assert validation.ordinary_mirror_ready and validation.validated_pairs == 1
                assert obj.mode == ('EDIT' if edit_mode else 'OBJECT')
                if edit_mode:
                    bpy.ops.object.mode_set(mode='OBJECT')
                current = coordinates(obj)
                assert current[source] == before[1][source]
                close(current[target], mirrored(before[1][source], axis))
                for index in range(len(current)):
                    if index == target:
                        continue
                    assert current[index] == before[1][index], (axis, mode, index)
                    for (_name, points), (_old_name, old_points) in zip(key_coordinates(obj), before[2]):
                        assert points[index] == old_points[index], (axis, mode, _name, index)
                assert_preserved_equal(preserved(obj), before[0])
                assert_deltas_equal(obj, deltas)
                lookup = native_single_vertex_lookup(obj, axis, topology=False)
                assert lookup[source] == target and lookup[target] == source, (axis, mode, lookup, pair)


def test_average_on_y_and_z_keeps_asymmetric_active_key_deltas():
    for axis in 'YZ':
        obj = axis_fixture(axis)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.mode_set(mode='OBJECT')
        before = preserved(obj)
        deltas = key_deltas(obj)
        old = coordinates(obj)
        bpy.ops.object.mode_set(mode='EDIT')
        plan = refine.plan(obj, mode='AVERAGE', selected_only=False, axis=axis)
        assert plan.target_pairs
        validation = refine.apply(obj, plan)
        assert validation.ordinary_mirror_ready and validation.max_error == 0
        bpy.ops.object.mode_set(mode='OBJECT')
        current = coordinates(obj)
        for negative, positive in plan.target_pairs:
            expected = (Vector(old[negative]) + Vector(mirrored(old[positive], axis))) * .5
            close(current[negative], expected)
            close(current[positive], mirrored(expected, axis))
        assert_preserved_equal(preserved(obj), before)
        assert_deltas_equal(obj, deltas)
        lookup = native_single_vertex_lookup(obj, axis, topology=False)
        for a, b in plan.target_pairs:
            assert lookup[a] == b and lookup[b] == a, (axis, a, b, lookup)


def test_native_transform_mirrors_immediately_after_repair_without_mode_round_trip():
    for axis in 'XYZ':
        obj = axis_fixture(axis)
        obj.active_shape_key_index = 0
        obj.parent = None
        obj.matrix_world = Matrix.Identity(4)
        bpy.context.view_layer.update()
        pairs = accepted_pairs(native_single_vertex_lookup(obj, axis, topology=True),
                               coordinates(obj), axis)
        pair = pairs[1]
        select_vertices(obj, pair)
        bpy.ops.object.mode_set(mode='EDIT')
        validation = refine.apply(obj, refine.plan(obj, axis=axis))
        assert validation.ordinary_mirror_ready and obj.mode == 'EDIT'
        # Exercise the user's actual next action (G) on the very same BMesh,
        # so an old native geometry cache or stale live Shape Key layer fails.
        obj.data.use_mirror_topology = False
        for dimension in 'xyz':
            setattr(obj.data, f'use_mirror_{dimension}', dimension == axis.lower())
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for item in (*bm.faces, *bm.edges, *bm.verts):
            item.select_set(False)
        source, target = pair
        bm.verts[source].select_set(True)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        old = tuple(tuple(v.co) for v in bm.verts)
        movement = (.071, -.023, .037)
        result = bpy.ops.transform.translate(value=movement, mirror=True, use_proportional_edit=False)
        assert result == {'FINISHED'}, result
        close(bm.verts[source].co, Vector(old[source]) + Vector(movement))
        close(bm.verts[target].co, Vector(old[target]) + Vector(mirrored(movement, axis)))
        for index in range(len(old)):
            if index not in pair:
                close(bm.verts[index].co, old[index])
        bpy.ops.object.mode_set(mode='OBJECT')


def test_repeated_topology_can_be_unmatched_while_geometric_mirror_is_valid():
    reset()
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    before = whole_state(obj)
    topology = native_single_vertex_lookup(obj, 'X', topology=True)
    ordinary = native_single_vertex_lookup(obj, 'X', topology=False)
    assert all(index < 0 for index in topology), topology
    assert all(index >= 0 for index in ordinary), ordinary
    report = refine.analyze(obj, selected_only=False)
    assert report.matched == 0 and set(report.unmatched) == set(range(8)), report
    try:
        refine.plan(obj, selected_only=False)
    except refine.RefineSymmetryError:
        pass
    else:
        raise AssertionError('Unmatched native topology was replaced with geometric guesses')
    assert whole_state(obj) == before


def test_axis_validation_failure_fully_rolls_back_coordinates_and_keys():
    for axis in 'YZ':
        obj = axis_fixture(axis)
        before = whole_state(obj)
        plan = refine.plan(obj, selected_only=False, axis=axis)
        assert plan.positions
        with patch.object(refine, 'validate', side_effect=RuntimeError('Native axis validation test')):
            try:
                refine.apply(obj, plan)
            except refine.RefineSymmetryError as error:
                assert 'Native axis validation test' in str(error)
            else:
                raise AssertionError('Failed validation was reported as success')
        assert whole_state(obj) == before, ('Incomplete coordinate rollback', axis)


def main():
    tests = [value for name, value in globals().items() if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('REFINE_NATIVE_PAIR_TESTS_PASSED', len(tests), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
