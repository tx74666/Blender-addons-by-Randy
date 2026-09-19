"""Two-joint layout: read-only preview, transactional updates and artist data."""
import math
import tempfile
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
import character_designer
from character_designer import finger_layout as layout
from character_designer.mesh_mirror import _fingerprint


def fixture(rooted=False):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    # Closed end caps are deliberately outside the selected top strip.
    vertices = [(math.cos(j*math.tau/8)*(.3+.03*math.sin(i)),
                 math.sin(j*math.tau/8)*.22, i*.5)
                for i in range(7) for j in range(8)]
    faces = [(i*8+j, i*8+(j+1)%8, (i+1)*8+(j+1)%8, (i+1)*8+j)
             for i in range(6) for j in range(8)]
    faces += [tuple(reversed(range(8))), tuple(range(48, 56))]
    root_faces = []
    if rooted:
        faces.pop(48)
        # Deliberately irregular triangulated palm transition, not a 3-to-1 fan.
        for z, radius in ((-.4, .35), (-3., 2.)):
            vertices += [(math.cos(j*math.tau/8)*radius, math.sin(j*math.tau/8)*radius, z) for j in range(8)]
        for j in range(8):
            a, b, c, d = j, (j+1)%8, 56+(j+1)%8, 56+j
            if j == 0: root_faces.extend((len(faces), len(faces)+1))
            faces.extend(((a, d, c), (a, c, b)))
            faces.append((56+j, 64+j, 64+(j+1)%8, 56+(j+1)%8))
        faces.append(tuple(reversed(range(64, 72))))
    extra = len(vertices)
    vertices += [(5, 0, 0), (6, 0, 0), (5, 1, 0)]
    faces += [(extra, extra+1, extra+2)]
    mesh = bpy.data.meshes.new('FingerLayoutFixture')
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new('FingerLayoutFixture', mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Artist')
    for i, point in enumerate(key.data):
        point.co.x += .01*i
    key.value = .35
    uv = mesh.uv_layers.new(name='ArtistUV')
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            vi = mesh.loops[li].vertex_index
            uv.data[li].uv = (vi % 8 / 8, vertices[vi][2]/3)
        poly.use_smooth = True
    group = obj.vertex_groups.new(name='finger.L')
    for v in mesh.vertices:
        group.add([v.index], max(0., min(1., v.co.z/3)), 'REPLACE')
    mesh.attributes.new('ArtistFloat', 'FLOAT', 'POINT')
    for d, v in zip(mesh.attributes['ArtistFloat'].data, mesh.vertices):
        d.value = v.co.z
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    for seq in (bm.faces, bm.edges, bm.verts):
        for item in seq: item.select_set(False)
    for i in range(6): bm.faces[i*8].select_set(True)
    if rooted:
        for i in root_faces: bm.faces[i].select_set(True)
    bmesh.update_edit_mesh(mesh)
    return obj


def snap(obj):
    return layout.fingerprint(obj)


def test_preview_update_data():
    obj = fixture()
    before = snap(obj)
    layout.capture(bpy.context, legacy=True)
    settings = layout.state(bpy.context)
    settings.joint_one, settings.joint_two = .31, .69
    settings.three_rings = True
    settings.width_one, settings.width_two = .035, .025
    settings.between_rings = 2
    plan = layout.build_layout(bpy.context)
    assert len(plan['rings']) == 8  # 2 centers + 4 flank rings + 2 fillers.
    assert {r['kind'] for r in plan['rings']} == {'JOINT_1', 'JOINT_2', 'SUPPORT', 'BETWEEN'}
    assert snap(obj) == before, 'Preview modified mesh/data'
    saved = [[tuple(v.co) for v in k.data] for k in obj.data.shape_keys.key_blocks]
    expected_rings = [list(map(Vector, r['points'])) for r in plan['rings']]
    layout.apply_layout(bpy.context)
    obj.update_from_editmode()
    assert len(obj.data.vertices) == 59+8*8
    for key, coords in zip(obj.data.shape_keys.key_blocks, saved):
        assert len(key.data) == len(obj.data.vertices)
        assert all((v.co-Vector(co)).length < 1e-6 for v, co in zip(key.data, coords))
    assert obj.data.shape_keys.key_blocks['Artist'].value == settings.source.shape_keys.key_blocks['Artist'].value
    for ring in expected_rings:
        assert all(min((v.co-co).length for v in obj.data.vertices) < 1e-6 for co in ring)
    bpy.ops.object.mode_set(mode='OBJECT')
    for v in obj.data.vertices[59:]:
        assert abs(obj.vertex_groups['finger.L'].weight(v.index)-v.co.z/3) < 1e-5
        assert abs(obj.data.attributes['ArtistFloat'].data[v.index].value-v.co.z) < 1e-5
    for poly in obj.data.polygons:
        for li in poly.loop_indices:
            vi = obj.data.loops[li].vertex_index
            assert abs(obj.data.uv_layers['ArtistUV'].data[li].uv.y-obj.data.vertices[vi].co.z/3) < 1e-5
    # New Shape Key points use the same longitudinal interpolation, not Basis.
    for v in obj.data.vertices[59:]:
        assert obj.data.shape_keys.key_blocks['Artist'].data[v.index].co.x > v.co.x+.02
    bpy.ops.object.mode_set(mode='EDIT')
    once = snap(obj)
    layout.apply_layout(bpy.context)
    assert snap(obj) == once, 'Repeated update accumulated rings'
    settings.between_rings = 0
    settings.three_rings = False
    settings.joint_one = .27
    layout.apply_layout(bpy.context)
    obj.update_from_editmode()
    assert len(obj.data.vertices) == 59+2*8
    assert len(obj.data.polygons[-1].vertices) in (3, 4, 8)


def test_guards_and_rollback():
    obj = fixture()
    layout.capture(bpy.context, legacy=True)
    settings = layout.state(bpy.context)
    before = snap(obj)
    settings.joint_one, settings.joint_two = .65, .35
    try:
        layout.apply_layout(bpy.context)
        assert False, 'Crossed joints accepted'
    except ValueError: pass
    assert snap(obj) == before
    settings.joint_one, settings.joint_two = .3, .7
    def fail(): raise RuntimeError('Injected commit failure')
    try:
        layout.apply_layout(bpy.context, after_commit=fail)
        assert False, 'Failure not injected'
    except RuntimeError as exc: assert 'Injected' in str(exc)
    assert obj.mode == 'EDIT' and snap(obj) == before
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[-1].co.x += .01
    bmesh.update_edit_mesh(obj.data)
    changed = snap(obj)
    try:
        layout.apply_layout(bpy.context)
        assert False, 'Stale source accepted'
    except ValueError as exc: assert 'changed' in str(exc)
    assert snap(obj) == changed


def test_source_ring_reuse_and_direction():
    obj = fixture()
    layout.capture(bpy.context, legacy=True)
    s = layout.state(bpy.context)
    s.three_rings, s.between_rings = False, 0
    s.joint_one, s.joint_two = 1/3, 2/3
    layout.apply_layout(bpy.context)
    obj.update_from_editmode()
    assert len(obj.data.vertices) == 59
    s.joint_one, s.joint_two = .2, .65
    before = layout.build_layout(bpy.context)
    s.reverse = not s.reverse
    after = layout.build_layout(bpy.context)
    assert abs(before['rings'][0]['points'][0][2] + after['rings'][0]['points'][0][2]-3) < 1e-5


def test_invalid_strip_and_stale_attributes():
    obj = fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.faces[49].select_set(True)  # Fingertip cap must not silently be rebuilt.
    before = snap(obj)
    try:
        layout.capture(bpy.context, legacy=True)
        assert False, 'Nonquad selection accepted'
    except ValueError: pass
    assert snap(obj) == before
    obj = fixture()
    layout.capture(bpy.context, legacy=True)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.data.attributes['ArtistFloat'].data[58].value = 77
    bpy.ops.object.mode_set(mode='EDIT')
    before = snap(obj)
    try:
        layout.apply_layout(bpy.context)
        assert False, 'An outside attribute edit would be overwritten'
    except ValueError as exc: assert 'changed' in str(exc)
    assert snap(obj) == before


def test_save_reopen_update():
    obj = fixture()
    layout.capture(bpy.context, legacy=True)
    s = layout.state(bpy.context)
    s.reverse = False
    s.joint_one, s.joint_two = .3, .7
    s.three_rings, s.between_rings = True, 1
    layout.apply_layout(bpy.context)
    before = snap(obj)
    with tempfile.TemporaryDirectory(prefix='finger-layout-') as directory:
        path = str(Path(directory)/'fixture.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        obj = bpy.context.edit_object
        assert obj and layout.state(bpy.context).source
        assert snap(obj) == before
        layout.apply_layout(bpy.context)
        assert snap(obj) == before


def test_custom_normals_and_uv_seams():
    obj = fixture()
    bpy.ops.object.mode_set(mode='OBJECT')
    expected = Vector((.2, .3, 1)).normalized()
    # Seams, sharp edges and a deliberate UV discontinuity must survive.
    for e in obj.data.edges:
        if all(i % 8 == 0 for i in e.vertices):
            e.use_seam = True
            e.use_edge_sharp = True
    obj.data.normals_split_custom_set([expected]*len(obj.data.loops))
    bpy.ops.object.mode_set(mode='EDIT')
    layout.capture(bpy.context, legacy=True)
    layout.apply_layout(bpy.context)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert obj.data.has_custom_normals
    # Blender's packed angular normals are quantized, especially at a loop
    # normal-space pole. Even the initial encode differs by ~0.004 here.
    assert all((n.vector-expected).length < .015 for n in obj.data.corner_normals)
    assert sum(e.use_seam for e in obj.data.edges) > 6
    bpy.ops.object.mode_set(mode='EDIT')


def test_unbound_transforms_and_open_band():
    obj = fixture()
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.shape_key_clear()
    obj.vertex_groups.clear()
    obj.matrix_world = Matrix.Translation((3, -2, 1)) @ Matrix.Rotation(.5, 4, 'Y') @ Matrix.Diagonal((-2, .7, 1.5, 1))
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='EDIT')
    layout.capture(bpy.context, legacy=True)
    original = [tuple(v.co) for v in layout.state(bpy.context).source.vertices]
    layout.apply_layout(bpy.context)
    obj.update_from_editmode()
    assert not obj.data.shape_keys and not obj.vertex_groups and not obj.modifiers
    assert [tuple(v.co) for v in obj.data.vertices[:len(original)]] == original
    obj = fixture()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[3]], context='FACES_ONLY')
    bmesh.update_edit_mesh(obj.data)
    before = snap(obj)
    try:
        layout.capture(bpy.context, legacy=True)
        assert False, 'Open finger band accepted'
    except ValueError as exc: assert 'open' in str(exc) or 'non-manifold' in str(exc)
    assert snap(obj) == before


def main():
    character_designer.register()
    try:
        tests = [test_preview_update_data, test_guards_and_rollback, test_source_ring_reuse_and_direction,
                 test_invalid_strip_and_stale_attributes, test_save_reopen_update,
                 test_custom_normals_and_uv_seams, test_unbound_transforms_and_open_band]
        for test in tests:
            test()
            print('PASS', test.__name__, flush=True)
        print('FINGER_LAYOUT_PASSED', len(tests), flush=True)
    finally:
        character_designer.unregister()


if __name__ == '__main__': main()
