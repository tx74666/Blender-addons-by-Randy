"""Non-loop root capture, automatic distal discovery and old-ring sliding."""
import json
import sys
import tempfile
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_finger_layout_blender import fixture, layout, character_designer, snap


def configure():
    s = layout.state(bpy.context)
    s.joint_one, s.joint_two = .4, .7
    s.three_rings, s.between_rings = False, 0
    s.reverse = False
    return s


def test_irregular_root_and_automatic_tip():
    obj = fixture(rooted=True)
    configure()
    before = snap(obj)
    layout.capture(bpy.context)
    s = layout.state(bpy.context)
    record = json.loads(s.record)
    assert record['schema'] == 2
    assert abs(record['root'][2]+.4) < 1e-6
    assert abs(record['tip'][2]-3) < 1e-6
    assert abs(record['length']-3.4) < 1e-6
    assert abs(record['positions'][0]-.4/3.4) < 1e-6
    assert snap(obj) == before
    assert len(layout.build_layout(bpy.context)['rings']) == 2


def test_root_patch_only_and_nonmutating_rejection():
    obj = fixture(rooted=True)
    configure()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    for seq in (bm.faces, bm.edges, bm.verts):
        for item in seq: item.select_set(False)
    for i in (49, 50): bm.faces[i].select_set(True)
    bmesh.update_edit_mesh(obj.data)
    before = snap(obj)
    layout.capture(bpy.context)
    assert json.loads(layout.state(bpy.context).record)['tip'][2] == 3
    assert snap(obj) == before
    obj = fixture()  # Both ends capped: direction really is ambiguous.
    before = snap(obj)
    try:
        layout.capture(bpy.context)
        assert False, 'Ambiguous two-ended tube accepted'
    except ValueError as exc: assert 'capped' in str(exc) or 'ambiguous' in str(exc)
    assert snap(obj) == before


def test_slide_existing_rings_and_interpolate_data():
    obj = fixture(rooted=True)
    s = configure()
    layout.capture(bpy.context)
    # z=1.1 and 2.1, close to two old rings at z=1 and 2.
    s.joint_one, s.joint_two = 1.5/3.4, 2.5/3.4
    plan = layout.build_layout(bpy.context)
    assert len(plan['moves']) == 2, plan['moves']
    count = len(s.source.vertices)
    before = snap(obj)
    layout.apply_layout(bpy.context)
    assert snap(obj) != before
    bpy.ops.object.mode_set(mode='OBJECT')
    assert len(obj.data.vertices) == count
    for vi in (16, 32):
        point = obj.data.vertices[vi]
        assert abs(point.co.z-(1.1 if vi == 16 else 2.1)) < 1e-5
        assert abs(obj.vertex_groups['finger.L'].weight(vi)-point.co.z/3) < 1e-5
        assert abs(obj.data.attributes['ArtistFloat'].data[vi].value-point.co.z) < 1e-5
        for poly in obj.data.polygons:
            for li in poly.loop_indices:
                if obj.data.loops[li].vertex_index == vi:
                    assert abs(obj.data.uv_layers['ArtistUV'].data[li].uv.y-point.co.z/3) < 1e-5
    # All palm/cap and disconnected geometry is untouched.
    for vi in list(range(8))+list(range(48, count)):
        assert (obj.data.vertices[vi].co-s.source.vertices[vi].co).length < 1e-6
    bpy.ops.object.mode_set(mode='EDIT')
    after = snap(obj)
    layout.apply_layout(bpy.context)
    assert snap(obj) == after
    s.three_rings, s.between_rings = True, 1
    plan = layout.build_layout(bpy.context)
    layout.apply_layout(bpy.context)
    obj.update_from_editmode()
    for ring in plan['rings']:
        for point in ring['points']:
            assert min((v.co-point).length for v in obj.data.vertices) < 1e-5
    after = snap(obj)
    layout.apply_layout(bpy.context)
    assert snap(obj) == after


def test_protected_targets_and_rollback():
    obj = fixture(rooted=True)
    s = configure()
    layout.capture(bpy.context)
    before = snap(obj)
    s.joint_one = .05
    assert any(r['blocked'] for r in layout.build_layout(bpy.context)['rings'])
    try:
        layout.apply_layout(bpy.context)
        assert False, 'Virtual root was treated as editable geometry'
    except ValueError as exc: assert 'protected' in str(exc)
    assert snap(obj) == before
    s.joint_one = .4
    def fail(): raise RuntimeError('Injected commit failure')
    try:
        layout.apply_layout(bpy.context, after_commit=fail)
        assert False
    except RuntimeError as exc: assert 'Injected' in str(exc)
    assert snap(obj) == before and not s.applied and obj.mode == 'EDIT'


def test_seams_normals_and_discrete_data():
    obj = fixture(rooted=True)
    s = configure()
    s.joint_one, s.joint_two = 1.5/3.4, 2.5/3.4
    bpy.ops.object.mode_set(mode='OBJECT')
    # Protect one center ring as an authored transverse UV seam.
    for e in obj.data.edges:
        if all(16 <= vi < 24 for vi in e.vertices): e.use_seam = True
    obj.data.normals_split_custom_set([Vector((.2, .3, 1)).normalized()]*len(obj.data.loops))
    for attr_name, attr_type in (('Label', 'INT'), ('Mask', 'BOOLEAN'), ('Tint', 'FLOAT_COLOR')):
        obj.data.attributes.new(attr_name, attr_type, 'POINT')
    for v in obj.data.vertices:
        obj.data.attributes['Label'].data[v.index].value = v.index
        obj.data.attributes['Mask'].data[v.index].value = v.index % 2 == 0
        obj.data.attributes['Tint'].data[v.index].color = (.1, .2, .3, 1)
    bpy.ops.object.mode_set(mode='EDIT')
    plan = layout.capture(bpy.context)
    assert len(plan['moves']) == 1
    layout.apply_layout(bpy.context)
    bpy.ops.object.mode_set(mode='OBJECT')
    assert obj.data.has_custom_normals
    assert all((v.co-s.source.vertices[v.index].co).length < 1e-6 for v in obj.data.vertices[16:24])
    assert obj.data.attributes['Label'].data[32].value == 32
    assert obj.data.attributes['Mask'].data[32].value
    assert all((n.vector-Vector((.2, .3, 1)).normalized()).length < .015 for n in obj.data.corner_normals)


def test_unbound_transform_and_save_update():
    obj = fixture(rooted=True)
    configure()
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.shape_key_clear()
    obj.vertex_groups.clear()
    obj.matrix_world = Matrix.Translation((3, -2, 1)) @ Matrix.Rotation(.5, 4, 'Y') @ Matrix.Diagonal((-2, .7, 1.5, 1))
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode='EDIT')
    layout.capture(bpy.context)
    assert abs(json.loads(layout.state(bpy.context).record)['length']-5.1) < 1e-5
    layout.apply_layout(bpy.context)
    before = snap(obj)
    with tempfile.TemporaryDirectory(prefix='finger-range-') as directory:
        path = str(Path(directory)/'fixture.blend')
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bpy.ops.wm.open_mainfile(filepath=path)
        obj = bpy.context.edit_object
        assert not obj.data.shape_keys and not obj.vertex_groups
        layout.apply_layout(bpy.context)
        assert snap(obj) == before


def test_disconnected_and_open_tip_rejection():
    obj = fixture(rooted=True)
    configure()
    bm = bmesh.from_edit_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.faces[-1].select_set(True)
    bmesh.update_edit_mesh(obj.data)
    before = snap(obj)
    try:
        layout.capture(bpy.context)
        assert False
    except ValueError as exc: assert 'connected' in str(exc)
    assert snap(obj) == before
    bm.faces[-1].select_set(False)
    bmesh.ops.delete(bm, geom=[bm.faces[48]], context='FACES_ONLY')
    bmesh.update_edit_mesh(obj.data)
    before = snap(obj)
    try:
        layout.capture(bpy.context)
        assert False, 'A missing fingertip cap was guessed'
    except ValueError: pass
    assert snap(obj) == before


def main():
    character_designer.register()
    try:
        for test in (test_irregular_root_and_automatic_tip, test_root_patch_only_and_nonmutating_rejection,
                     test_slide_existing_rings_and_interpolate_data, test_protected_targets_and_rollback,
                     test_seams_normals_and_discrete_data, test_unbound_transform_and_save_update,
                     test_disconnected_and_open_tip_rejection):
            test()
            print('PASS', test.__name__, flush=True)
    finally: character_designer.unregister()


if __name__ == '__main__': main()
