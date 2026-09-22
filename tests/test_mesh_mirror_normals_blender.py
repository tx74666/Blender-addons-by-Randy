"""Custom-normal preservation through local mirror replacement.

Run with Blender 5.2 or newer; these fixtures exercise free corner normals,
including discontinuities which must not implicitly mark smooth edges sharp.
"""
import sys
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_mesh_mirror_blender import make, select, tetra, mirror


TOLERANCE = 2e-6


def _coordinate_key(co):
    return tuple(round(float(value), 5) for value in co)


def _face_key(coords):
    return tuple(sorted(_coordinate_key(co) for co in coords))


def _install_normals(mesh, packed=False):
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    for edge in mesh.edges:
        edge.use_edge_sharp = edge.index % 4 == 0
        edge.use_seam = edge.index % 3 == 0
    # Each corner has its own direction, including multiple directions at the
    # same vertex. These must survive without the setter adding sharp edges.
    normals = [Vector((.13 + .027 * (i % 5), -.24 + .031 * (i % 7),
                       .9 + .011 * (i % 3))).normalized()
               for i in range(len(mesh.loops))]
    if packed:
        mesh.normals_split_custom_set(normals)
    else:
        attribute = mesh.attributes.new('custom_normal', 'FLOAT_VECTOR', 'CORNER')
        for item, normal in zip(attribute.data, normals):
            item.vector = normal
    mesh.update()
    assert mesh.has_custom_normals
    if not packed:
        assert all((normal.vector - wanted).length < TOLERANCE
                   for normal, wanted in zip(mesh.corner_normals, normals)), 'free-normal fixture unsupported'


def _artist_data(obj):
    mesh = obj.data
    uv = mesh.uv_layers.new(name='Artist UV')
    corners = mesh.attributes.new('Artist Corners', 'FLOAT', 'CORNER')
    edge_values = mesh.attributes.new('crease_edge', 'FLOAT', 'EDGE')
    # Adding attributes can invalidate earlier RNA wrappers; reacquire them.
    uv = mesh.uv_layers['Artist UV']
    corners = mesh.attributes['Artist Corners']
    for i, item in enumerate(uv.data):
        item.uv = (.013 * i, .021 * i)
    for i, item in enumerate(corners.data):
        item.value = .017 * i
    for i, item in enumerate(edge_values.data):
        item.value = .023 * i
    obj.shape_key_add(name='Basis')
    shape = obj.shape_key_add(name='Artist Shape')
    for item in shape.data:
        item.co.z += .04 + .007 * item.co.y
    shape.value = .27
    source = obj.vertex_groups.new(name='finger.L')
    source.add(tuple(v.index for v in mesh.vertices if v.co.x > 0), .73, 'REPLACE')
    mask = obj.vertex_groups.new(name='ArtistMask')
    mask.add(tuple(range(len(mesh.vertices))), .21, 'REPLACE')


def _patch_fixture(packed=False):
    # A connected sheet, with an extra source-side ring at X=1.5. The target
    # patch has one quad, the source has two; the attachment at +/-1 is fixed.
    xs = (-2., -1., 0., 1., 1.5, 2.)
    vertices = [(x, y, .03 * x * x + .05 * y) for x in xs for y in (0., 1.)]
    faces = [(2*i, 2*i+2, 2*i+3, 2*i+1) for i in range(len(xs)-1)]
    obj = make(vertices, faces)
    _install_normals(obj.data, packed)
    _artist_data(obj)
    return obj, (3, 4)


def _snapshot(mesh):
    return {
        'faces': [
            {'coords': [mesh.vertices[mesh.loops[i].vertex_index].co.copy()
                        for i in polygon.loop_indices],
             'normals': [mesh.corner_normals[i].vector.copy() for i in polygon.loop_indices],
             'uv': [tuple(mesh.uv_layers['Artist UV'].data[i].uv) for i in polygon.loop_indices],
             'values': [mesh.attributes['Artist Corners'].data[i].value for i in polygon.loop_indices],
             'smooth': polygon.use_smooth}
            for polygon in mesh.polygons],
        'edges': [(tuple(mesh.vertices[i].co.copy() for i in edge.vertices),
                   edge.use_edge_sharp, edge.use_seam,
                   mesh.attributes['crease_edge'].data[edge.index].value)
                  for edge in mesh.edges],
    }


def _check_mapped_data(mesh, before, plan):
    """Derive the expected result from geometry, not production origin tables."""
    expected_faces = {}
    normal_matrix = plan.reflection.to_3x3().inverted().transposed()
    for index, face in enumerate(before['faces']):
        if index not in plan.target_faces:
            expected_faces[_face_key(face['coords'])] = (face, False)
        if index in plan.source_faces:
            expected_faces[_face_key([plan.reflection @ co for co in face['coords']])] = (face, True)
    assert len(mesh.polygons) == len(expected_faces)
    for polygon in mesh.polygons:
        coords = [mesh.vertices[mesh.loops[i].vertex_index].co for i in polygon.loop_indices]
        face, reflected = expected_faces[_face_key(coords)]
        expected_coords = [plan.reflection @ co if reflected else co for co in face['coords']]
        assert polygon.use_smooth == face['smooth']
        for i, co in zip(polygon.loop_indices, coords):
            origin = next(j for j, expected in enumerate(expected_coords) if (co-expected).length < 1e-5)
            normal = face['normals'][origin]
            expected = (normal_matrix @ normal).normalized() if reflected else normal
            assert (mesh.corner_normals[i].vector-expected).length < TOLERANCE, (i, reflected)
            assert tuple(mesh.uv_layers['Artist UV'].data[i].uv) == face['uv'][origin]
            assert mesh.attributes['Artist Corners'].data[i].value == face['values'][origin]

    # Source and retained edges keep their flags; new edges use source flags.
    expected_edges = {_face_key(coords): (sharp, seam, crease)
                      for coords, sharp, seam, crease in before['edges']}
    source_edge_keys = set()
    for index in plan.source_faces:
        coords = before['faces'][index]['coords']
        source_edge_keys.update(_face_key((co, coords[(i+1) % len(coords)]))
                                for i, co in enumerate(coords))
    for coords, sharp, seam, crease in before['edges']:
        if _face_key(coords) in source_edge_keys:
            expected_edges[_face_key([plan.reflection @ co for co in coords])] = (sharp, seam, crease)
    for edge in mesh.edges:
        key = _face_key([mesh.vertices[i].co for i in edge.vertices])
        assert (edge.use_edge_sharp, edge.use_seam,
                mesh.attributes['crease_edge'].data[edge.index].value) == expected_edges[key], edge.index


def _prepare(obj, source_faces, **kwargs):
    select(obj, source_faces)
    plan = mirror.build_plan(bpy.context, **kwargs)
    assert not plan.needs_choice
    bpy.ops.object.mode_set(mode='OBJECT')
    return plan, _snapshot(obj.data)


def test_free_normals_local_patch_and_artist_data():
    obj, source_faces = _patch_fixture()
    plan, before = _prepare(obj, source_faces, target_candidate=1)
    assert len(plan.target_faces) == 1 and len(plan.source_faces) == 2
    shape_before = {key.name: key.value for key in obj.data.shape_keys.key_blocks}
    mirror.apply_plan(plan)
    _check_mapped_data(obj.data, before, plan)
    assert {key.name: key.value for key in obj.data.shape_keys.key_blocks} == shape_before
    for basis, shaped in zip(obj.data.shape_keys.key_blocks['Basis'].data,
                             obj.data.shape_keys.key_blocks['Artist Shape'].data):
        assert abs((shaped.co-basis.co).z-(.04+.007*basis.co.y)) < 1e-6
    assert 'finger.R' in obj.vertex_groups
    for vertex in obj.data.vertices:
        assert abs(obj.vertex_groups['ArtistMask'].weight(vertex.index)-.21) < 1e-6
        if vertex.co.x <= -1:
            assert abs(obj.vertex_groups['finger.R'].weight(vertex.index)-.73) < 1e-6


def test_packed_source_normals_local_patch():
    obj, source_faces = _patch_fixture(packed=True)
    plan, before = _prepare(obj, source_faces, target_candidate=1)
    mirror.apply_plan(plan)
    _check_mapped_data(obj.data, before, plan)


def test_free_normals_inverse_transpose_reference():
    obj = make(*tetra())
    _install_normals(obj.data)
    _artist_data(obj)
    obj.matrix_world = Matrix.Translation((4, 2, 3)) @ Matrix.Rotation(.3, 4, 'Z') @ Matrix.Diagonal((2, 1, .6, 1))
    reference = bpy.data.objects.new('Normal Reflection Plane', None)
    bpy.context.collection.objects.link(reference)
    reference.matrix_world = Matrix.Translation((4, 2, 3)) @ Matrix.Rotation(.7, 4, 'Z')
    bpy.context.view_layer.update()
    plan, before = _prepare(obj, range(4), reference=reference)
    mirror.apply_plan(plan)
    _check_mapped_data(obj.data, before, plan)


def test_edit_mode_round_trip_and_repeat_mirror():
    obj, source_faces = _patch_fixture()
    plan, before = _prepare(obj, source_faces, target_candidate=1)
    mirror.apply_plan(plan)
    for _ in range(3):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.mode_set(mode='OBJECT')
        _check_mapped_data(obj.data, before, plan)
    source_faces = [polygon.index for polygon in obj.data.polygons
                    if min(obj.data.vertices[i].co.x for i in polygon.vertices) >= 1]
    counts = len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons)
    second, before_second = _prepare(obj, source_faces, target_candidate=1)
    mirror.apply_plan(second)
    assert (len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons)) == counts
    _check_mapped_data(obj.data, before_second, second)


def test_damaged_staged_normals_refuse_and_rollback():
    obj, source_faces = _patch_fixture()
    plan, _ = _prepare(obj, source_faces, target_candidate=1)
    old = obj.data
    before = mirror._fingerprint(obj)
    groups = mirror.weights._capture_vertex_groups(obj)
    counts = len(bpy.data.meshes), len(bpy.data.objects)
    verify = mirror._verify_staged

    def corrupt_then_verify(staging, *args):
        mesh = staging.data
        attribute = mesh.attributes.get('custom_normal')
        if attribute and attribute.data_type == 'FLOAT_VECTOR' and attribute.domain == 'CORNER':
            attribute.data[0].vector = -mesh.corner_normals[0].vector.copy()
            mesh.update()
        else:
            normals = [normal.vector.copy() for normal in mesh.corner_normals]
            normals[0].negate()
            mesh.normals_split_custom_set(normals)
        return verify(staging, *args)

    with patch.object(mirror, '_verify_staged', side_effect=corrupt_then_verify):
        try:
            mirror.apply_plan(plan)
        except mirror.MirrorError as exc:
            assert 'normal' in str(exc).lower(), str(exc)
        else:
            raise AssertionError('Corrupted corner normal was accepted')
    assert obj.data == old and mirror._fingerprint(obj) == before
    assert mirror.weights._capture_vertex_groups(obj) == groups
    assert (len(bpy.data.meshes), len(bpy.data.objects)) == counts


def main():
    tests = [value for name, value in globals().items() if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('MESH_MIRROR_NORMALS_TESTS_PASSED', len(tests), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
