"""Animation refusal and artist surface data through both topology operations."""
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_topology_symmetry_blender as fixture
from character_designer import topology_symmetry as topology


def _fixture(repair=False):
    obj, rig = fixture.make_fixture()
    if repair:
        bm = fixture.bmesh.from_edit_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        fixture.bmesh.ops.delete(bm, geom=[f for f in bm.faces if 4 <= f.index < 10], context='FACES_ONLY')
        fixture.bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=True)
    return obj, rig


def _plan(repair):
    return (topology.build_topology_repair_plan(bpy.context, merge_distance=.1) if repair
            else topology.build_topology_mirror_plan(bpy.context))


def _apply(plan, repair):
    return (topology.apply_topology_repair_plan(plan) if repair
            else topology.apply_topology_mirror_plan(plan))


def _refuses(callback):
    try:
        callback()
    except topology.TopologySymmetryError as exc:
        assert 'Animated/driven' in str(exc), str(exc)
    else:
        raise AssertionError('Animated data was silently replaced')


def test_animation_guard_before_planning_and_after_stale_plan():
    for repair in (False, True):
        for kind in ('ACTION', 'DRIVER', 'NLA', 'MESH'):
            obj, _ = _fixture(repair)
            plan = _plan(repair)
            bpy.ops.object.mode_set(mode='OBJECT')
            mesh = obj.data
            keys = mesh.shape_keys
            key = keys.key_blocks['ArtistExpression']
            if kind in {'ACTION', 'NLA'}:
                key.keyframe_insert(data_path='value', frame=1)
                key.value = .8
                key.keyframe_insert(data_path='value', frame=10)
                if kind == 'NLA':
                    action = keys.animation_data.action
                    track = keys.animation_data.nla_tracks.new()
                    track.strips.new('Expression', 1, action)
                    keys.animation_data.action = None
            elif kind == 'DRIVER':
                key.driver_add('value').driver.expression = '.37'
            else:
                mesh['artist_value'] = .7
                mesh.keyframe_insert(data_path='["artist_value"]', frame=1)
            mesh_ids = {m.as_pointer() for m in bpy.data.meshes}
            before = topology.ws._capture_vertex_groups(obj)
            _refuses(lambda: _apply(plan, repair))
            assert obj.data is mesh and mesh.shape_keys is keys
            assert topology.ws._capture_vertex_groups(obj) == before
            bpy.ops.object.mode_set(mode='EDIT')
            _refuses(lambda: _plan(repair))
            assert bpy.context.mode == 'EDIT_MESH'
            assert {m.as_pointer() for m in bpy.data.meshes} == mesh_ids
            assert obj.data is mesh and mesh.shape_keys is keys


def _edge_key(points):
    return tuple(sorted(tuple(round(c, 5) for c in point) for point in points))


def test_surface_data_retained_and_reflected():
    for repair in (False, True):
        obj, _ = _fixture(repair)
        bpy.ops.object.mode_set(mode='OBJECT')
        old = obj.data
        for name, domain in (('crease_edge', 'EDGE'), ('bevel_weight_edge', 'EDGE'),
                             ('bevel_weight_vertex', 'POINT')):
            attribute = old.attributes.new(name, 'FLOAT', domain)
            for i, item in enumerate(attribute.data):
                item.value = .03 * (i + 1)
        for edge in old.edges:
            edge.use_edge_sharp = edge.index % 2 == 0
            edge.use_seam = edge.index % 3 == 0
        normals = old.attributes.new('custom_normal', 'FLOAT_VECTOR', 'CORNER')
        for i, item in enumerate(normals.data):
            item.vector = Vector((.15 + i * .01, .2, 1)).normalized()
        old.update()
        expected_edges, expected_faces, expected_points = {}, {}, {}
        for edge in old.edges:
            points = [old.vertices[i].co.copy() for i in edge.vertices]
            # Source and the separate unedited centre triangle must be exact.
            if all(p.x >= 0 for p in points):
                values = (edge.use_edge_sharp, edge.use_seam,
                          old.attributes['crease_edge'].data[edge.index].value,
                          old.attributes['bevel_weight_edge'].data[edge.index].value)
                expected_edges[_edge_key(points)] = values
                if all(p.x > 0 for p in points):
                    expected_edges[_edge_key([topology._mirror_point(p) for p in points])] = values
        for vertex in old.vertices:
            if vertex.co.x >= 0:
                value = old.attributes['bevel_weight_vertex'].data[vertex.index].value
                expected_points[_edge_key([vertex.co])] = value
                if vertex.co.x > 0:
                    expected_points[_edge_key([topology._mirror_point(vertex.co)])] = value
        for face in old.polygons:
            points = [old.vertices[i].co.copy() for i in face.vertices]
            if all(p.x >= 0 for p in points):
                normals = [old.corner_normals[i].vector.copy() for i in face.loop_indices]
                expected_faces[_edge_key(points)] = {_edge_key([p]): n for p, n in zip(points, normals)}
                if all(p.x > 0 for p in points):
                    expected_faces[_edge_key([topology._mirror_point(p) for p in points])] = {
                        _edge_key([topology._mirror_point(p)]): topology._mirror_point(n)
                        for p, n in zip(points, normals)}
        bpy.ops.object.mode_set(mode='EDIT')
        plan = _plan(repair)
        bpy.ops.object.mode_set(mode='OBJECT')
        _apply(plan, repair)
        new = obj.data
        found_edges, found_faces, found_points = set(), set(), set()
        for edge in new.edges:
            key = _edge_key([new.vertices[i].co for i in edge.vertices])
            if key in expected_edges:
                values = expected_edges[key]
                assert (edge.use_edge_sharp, edge.use_seam) == values[:2]
                fixture.assert_close(new.attributes['crease_edge'].data[edge.index].value, values[2])
                fixture.assert_close(new.attributes['bevel_weight_edge'].data[edge.index].value, values[3])
                found_edges.add(key)
        for vertex in new.vertices:
            key = _edge_key([vertex.co])
            if key in expected_points:
                fixture.assert_close(new.attributes['bevel_weight_vertex'].data[vertex.index].value,
                                     expected_points[key])
                found_points.add(key)
        for face in new.polygons:
            key = _edge_key([new.vertices[i].co for i in face.vertices])
            if key in expected_faces:
                for loop_index in face.loop_indices:
                    point = new.vertices[new.loops[loop_index].vertex_index].co
                    expected = expected_faces[key][_edge_key([point])]
                    assert (new.corner_normals[loop_index].vector - expected).length < 2e-6
                found_faces.add(key)
        assert found_edges == set(expected_edges)
        assert found_faces == set(expected_faces)
        assert found_points == set(expected_points)


def test_failed_copy_and_commit_leave_no_replacement_mesh():
    for repair in (False, True):
        for method in ('_copy_attributes', '_verify_shape_keys'):
            obj, _ = _fixture(repair)
            plan = _plan(repair)
            bpy.ops.object.mode_set(mode='OBJECT')
            original = obj.data
            ids = {mesh.as_pointer() for mesh in bpy.data.meshes}
            groups = topology.ws._capture_vertex_groups(obj)
            with patch.object(topology, method, side_effect=RuntimeError('Injected preservation failure')):
                try:
                    _apply(plan, repair)
                except topology.TopologySymmetryError:
                    pass
                else:
                    raise AssertionError('Injected failure did not abort')
            assert obj.data is original
            assert topology.ws._capture_vertex_groups(obj) == groups
            assert {mesh.as_pointer() for mesh in bpy.data.meshes} == ids


if __name__ == '__main__':
    for test in (test_animation_guard_before_planning_and_after_stale_plan,
                 test_surface_data_retained_and_reflected,
                 test_failed_copy_and_commit_leave_no_replacement_mesh):
        test()
        print('PASS', test.__name__)
    print('PASS Topology preservation 3 tests')
