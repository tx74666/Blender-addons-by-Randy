"""Headless tests for rig-independent Mirror Selected Region.

blender --background --factory-startup --python tests/test_mesh_mirror_blender.py
"""
import sys
from pathlib import Path
import traceback
from types import SimpleNamespace

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'addons'))
from character_designer import mesh_mirror as mirror


def reset():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)


def make(vertices, faces, edges=()):
    reset()
    mesh = bpy.data.meshes.new('MirrorFixture')
    mesh.from_pydata(vertices, edges, faces)
    mesh.update()
    obj = bpy.data.objects.new('UnboundHair', mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def select(obj, faces):
    if obj.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    for seq in (bm.verts, bm.edges, bm.faces):
        seq.ensure_lookup_table()
    for seq in (bm.faces, bm.edges, bm.verts):
        for item in seq:
            item.select_set(False)
    for i in faces:
        bm.faces[i].select_set(True)
    bmesh.update_edit_mesh(obj.data)


def apply(obj, **kwargs):
    plan = mirror.build_plan(bpy.context, **kwargs)
    bpy.ops.object.mode_set(mode='OBJECT')
    selection = mirror.apply_plan(plan)
    return plan, selection


def tetra(x=2):
    return ([(x, -1, -1), (x, 1, -1), (x, 0, 1), (x + 1, 0, 0)],
            [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)])


def test_closed_unbound_twice():
    obj = make(*tetra())
    original_name = obj.data.name
    original = tuple(tuple(v.co) for v in obj.data.vertices)
    select(obj, range(4))
    plan, selection = apply(obj)
    assert not plan.candidates and not obj.vertex_groups and not obj.modifiers
    assert len(obj.data.polygons) == 8 and len(obj.data.vertices) == 8
    assert obj.data.name == original_name
    assert tuple(tuple(v.co) for v in obj.data.vertices[:4]) == original
    select(obj, range(4))
    plan, selection = apply(obj)
    assert len(plan.target_faces) == 4
    assert len(obj.data.polygons) == 8 and len(obj.data.vertices) == 8
    assert obj.data.name == original_name
    assert selection == (0, 1, 2, 3)


def test_mirror_name_cleans_legacy_suffixes():
    obj = make(*tetra())
    obj.data.name = 'HandMesh.Mirror.Mirror'
    select(obj, range(4))
    apply(obj)
    assert obj.data.name == 'HandMesh'


def test_different_topology_attributes_shapes_groups_loose_data():
    verts, faces = tetra()
    # An opposite tetra with a subdivided cap: same surface, different topology.
    verts += [(-v[0], v[1], v[2]) for v in verts] + [(-2, 0, -1/3), (10, 10, 10), (11, 10, 10)]
    faces += [(4, 5, 8), (5, 6, 8), (6, 4, 8), (4, 7, 5), (5, 7, 6), (6, 7, 4)]
    obj = make(verts, faces, [(9, 10)])
    mesh = obj.data
    obj.shape_key_add(name='Basis')
    shape = obj.shape_key_add(name='Artist')
    shape.data[3].co.z += .25
    shape.data[10].co.z += .7
    shape.value = .3
    uv = mesh.uv_layers.new(name='UV Artist')
    for i, d in enumerate(uv.data):
        d.uv = (i / 100, i / 200)
    for name, domain in [('crease_edge', 'EDGE'), ('bevel_weight_edge', 'EDGE'), ('ArtistFloat', 'POINT')]:
        a = mesh.attributes.new(name, 'FLOAT', domain)
        for i, d in enumerate(a.data):
            d.value = i / 100
    for i, e in enumerate(mesh.edges):
        e.use_seam = i % 2 == 0
        e.use_edge_sharp = i % 3 == 0
        if hasattr(e, 'use_freestyle_mark'):
            e.use_freestyle_mark = i % 2 == 1
    material = bpy.data.materials.new('Artist Material')
    mesh.materials.append(material)
    for p in mesh.polygons:
        p.use_smooth = True
        if hasattr(p, 'use_freestyle_mark'):
            p.use_freestyle_mark = True
    group = obj.vertex_groups.new(name='hair.L')
    group.add((0, 1, 2, 3), .7, 'REPLACE')
    shared = obj.vertex_groups.new(name='ArtistMask')
    shared.add(tuple(range(len(verts))), .3, 'REPLACE')
    select(obj, range(4))
    plan, _ = apply(obj)
    assert len(plan.target_faces) == 6
    assert len(obj.data.vertices) == 10 and len(obj.data.polygons) == 8
    assert 'hair.R' in obj.vertex_groups
    assert len([e for e in obj.data.edges if e.is_loose]) == 1
    assert obj.data.shape_keys.key_blocks['Artist'].data[5].co.z == shape.data[10].co.z
    assert obj.data.uv_layers.active.name == 'UV Artist'
    assert obj.data.materials[0] == material
    assert 'crease_edge' in obj.data.attributes
    assert all(p.use_smooth for p in obj.data.polygons)
    assert all(getattr(p, 'use_freestyle_mark', True) for p in obj.data.polygons)
    select(obj, range(4))
    apply(obj)
    assert len(obj.data.vertices) == 10


def test_centerline_and_cross_plane():
    obj = make([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [(0, 1, 2, 3)])
    select(obj, [0])
    apply(obj)
    assert len(obj.data.vertices) == 6 and len(obj.data.polygons) == 2
    select(obj, [0])
    plan, _ = apply(obj)
    assert len(plan.target_faces) == 1 and len(obj.data.vertices) == 6
    select(obj, [0, 1])
    try:
        mirror.build_plan(bpy.context)
        assert False, 'must reject both sides'
    except mirror.MirrorError as exc:
        assert 'both sides' in str(exc)


def test_ambiguous_nearby_strands():
    vertices, faces = tetra()
    for shift in (0, .04):
        offset = len(vertices)
        vertices += [(-v[0], v[1]+shift, v[2]) for v in vertices[:4]]
        faces += [tuple(i+offset for i in reversed(f)) for f in faces[:4]]
    obj = make(vertices, faces)
    select(obj, range(4))
    plan = mirror.build_plan(bpy.context)
    assert plan.needs_choice and len(plan.candidates) == 2
    old = obj.data
    bpy.ops.object.mode_set(mode='OBJECT')
    try:
        mirror.apply_plan(plan)
        assert False, 'must not modify ambiguous targets'
    except mirror.MirrorError:
        assert obj.data == old
    select(obj, range(4))
    plan, _ = apply(obj, target_candidate=1)
    assert len(obj.data.polygons) == 12
    # The other close strand is not replaced by the explicit choice.
    assert any(abs(v.co.y - 1.04) < 1e-6 for v in obj.data.vertices)


def test_reference_object_transforms():
    obj = make(*tetra())
    obj.matrix_world = Matrix.Translation((4, 2, 3)) @ Matrix.Rotation(.3, 4, 'Z') @ Matrix.Diagonal((2, 1, .6, 1))
    ref = bpy.data.objects.new('Symmetry Empty', None)
    bpy.context.collection.objects.link(ref)
    ref.matrix_world = Matrix.Translation((4, 2, 3)) @ Matrix.Rotation(.7, 4, 'Z')
    bpy.context.view_layer.update()
    source = [obj.matrix_world @ v.co for v in obj.data.vertices]
    select(obj, range(4))
    plan, _ = apply(obj, reference=ref)
    normal = ref.matrix_world.to_3x3() @ Vector((1, 0, 0))
    origin = ref.matrix_world.translation
    for i, world in enumerate(source):
        expected = world - 2 * normal.dot(world-origin) * normal
        actual = obj.matrix_world @ obj.data.vertices[i+4].co
        assert (expected-actual).length < 1e-5


def test_staged_failure_leaves_everything_unchanged():
    obj = make(*tetra())
    group = obj.vertex_groups.new(name='hair.L')
    group.add((0, 1, 2, 3), .8, 'REPLACE')
    obj.shape_key_add(name='Basis')
    select(obj, range(4))
    plan = mirror.build_plan(bpy.context)
    bpy.ops.object.mode_set(mode='OBJECT')
    before, old, count = mirror._fingerprint(obj), obj.data, len(bpy.data.meshes)
    original_verify = mirror._verify_staged
    def fail(*args):
        raise RuntimeError('injected verification failure')
    mirror._verify_staged = fail
    try:
        mirror.apply_plan(plan)
        assert False
    except RuntimeError as exc:
        assert 'injected' in str(exc)
    finally:
        mirror._verify_staged = original_verify
    assert obj.data == old and mirror._fingerprint(obj) == before
    assert len(bpy.data.meshes) == count and len(obj.vertex_groups) == 1


def test_connected_patch_and_missing_opposite_interior():
    # Continuous strip through X=0. Only the outermost +X quad is selected;
    # its attachment edge must connect to the corresponding -X edge.
    vertices = [(x, y, 0) for x in (-2, -1, 0, 1, 2) for y in (0, 1)]
    faces = [(2*i, 2*i+2, 2*i+3, 2*i+1) for i in range(4)]
    obj = make(vertices, faces)
    select(obj, [3])
    plan = mirror.build_plan(bpy.context)
    assert len(plan.candidates) == 1 and plan.candidates[0].faces == (0,)
    # The rest of a body can touch the bounding box at the seam; explicit
    # candidate selection must still safely preserve that body.
    plan, _ = apply(obj, target_candidate=1)
    assert len(obj.data.vertices) == 10 and len(obj.data.polygons) == 4
    # Remove only the old opposite patch. The seam remains, so this is a repair.
    obj = make(vertices[2:], [tuple(v-2 for v in f) for f in faces[1:]])
    select(obj, [2])
    plan = mirror.build_plan(bpy.context)
    assert not plan.candidates and not plan.needs_choice
    plan, _ = apply(obj)
    assert len(obj.data.polygons) == 4 and len(obj.data.vertices) == 10


def test_custom_normals_and_color_attributes():
    obj = make(*tetra())
    mesh = obj.data
    for p in mesh.polygons:
        p.use_smooth = True
    source_normal = Vector((.2, .4, .9)).normalized()
    mesh.normals_split_custom_set([tuple(source_normal)] * len(mesh.loops))
    for data_type in ('FLOAT_COLOR', 'BYTE_COLOR'):
        attr = mesh.color_attributes.new(name=data_type, type=data_type, domain='CORNER')
        for i, item in enumerate(attr.data):
            item.color = (.12, .3, .7, 1)
    mesh.color_attributes.active_color = mesh.color_attributes['BYTE_COLOR']
    mesh.color_attributes.render_color_index = 0
    select(obj, range(4))
    plan, _ = apply(obj)
    assert obj.data.has_custom_normals
    assert all((n.vector-source_normal).length < 1e-3 for n in obj.data.corner_normals[:12])
    target_normal = Vector((-source_normal.x, source_normal.y, source_normal.z))
    assert all((n.vector-target_normal).length < 1e-3 for n in obj.data.corner_normals[12:])
    assert obj.data.color_attributes.active_color.name == 'BYTE_COLOR'


def test_locked_weights_and_unsupported_keys_refuse():
    obj = make(*tetra())
    group = obj.vertex_groups.new(name='Mask')
    group.add((0, 1), .5, 'REPLACE')
    group.lock_weight = True
    select(obj, range(4))
    plan = mirror.build_plan(bpy.context)
    bpy.ops.object.mode_set(mode='OBJECT')
    before = mirror._fingerprint(obj)
    try:
        mirror.apply_plan(plan)
        assert False
    except mirror.MirrorError as exc:
        assert 'Locked' in str(exc)
    assert mirror._fingerprint(obj) == before
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Animated')
    key.keyframe_insert(data_path='value', frame=1)
    select(obj, range(4))
    try:
        mirror.build_plan(bpy.context)
        assert False
    except mirror.MirrorError as exc:
        assert 'Animated' in str(exc)


def test_preview_operator_and_runtime_lifecycle():
    import character_designer
    from character_designer import mesh_mirror_ui as ui
    character_designer.register()
    character_designer.register()
    obj = make(*tetra())
    select(obj, range(4))
    plan = mirror.build_plan(bpy.context)
    batches, labels = ui.preview_geometry(plan)
    assert len(batches) == 3 and any('Mirror plane' in label for _, label, _ in labels)
    original = mirror.legacy._geometry_fingerprint(obj)
    assert bpy.ops.character_designer.mesh_mirror_preview() == {'FINISHED'}
    assert ui._valid_preview() is not None
    assert mirror.legacy._geometry_fingerprint(obj) == original
    assert bpy.ops.character_designer.mesh_mirror_preview() == {'FINISHED'}
    assert ui._preview is None
    assert bpy.ops.character_designer.mirror_selected_region() == {'FINISHED'}
    assert obj.mode == 'EDIT'
    assert bpy.ops.character_designer.mirror_selected_region() == {'FINISHED'}
    obj.update_from_editmode()
    assert len(obj.data.vertices) == 8
    assert 'reference_name' in bpy.ops.character_designer.mesh_mirror_settings.get_rna_type().properties
    character_designer.unregister()
    assert not hasattr(bpy.types.Scene, 'character_designer_mesh_mirror')
    assert ui._invalidate_preview not in bpy.app.handlers.undo_pre


def test_commit_failure_rolls_back_and_stale_plan_refuses():
    obj = make(*tetra())
    group = obj.vertex_groups.new(name='hair.L')
    group.add((0, 1), .7, 'REPLACE')
    select(obj, range(4))
    plan = mirror.build_plan(bpy.context)
    bpy.ops.object.mode_set(mode='OBJECT')
    old, old_name, before = obj.data, obj.data.name, mirror._fingerprint(obj)
    count = len(bpy.data.meshes)
    def fail(selection):
        bpy.ops.object.mode_set(mode='EDIT')
        raise RuntimeError('injected after commit')
    try:
        mirror.apply_plan(plan, after_commit=fail)
        assert False
    except RuntimeError as exc:
        assert 'after commit' in str(exc)
    assert obj.data == old and obj.data.name == old_name and len(obj.vertex_groups) == 1
    assert mirror._fingerprint(obj) == before and len(bpy.data.meshes) == count
    obj.data.vertices[0].co.z += .05
    edited = mirror._fingerprint(obj)
    try:
        mirror.apply_plan(plan)
        assert False
    except mirror.MirrorError as exc:
        assert 'changed since preview' in str(exc)
    assert mirror._fingerprint(obj) == edited


def test_live_mirror_modifier_and_locked_key_refuse():
    obj = make(*tetra())
    modifier = obj.modifiers.new('Existing Mirror', 'MIRROR')
    select(obj, range(4))
    try:
        mirror.build_plan(bpy.context)
        assert False
    except mirror.MirrorError as exc:
        assert 'already generates' in str(exc)
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.modifiers.remove(modifier)
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='Locked Artist Key')
    key.lock_shape = True
    select(obj, range(4))
    try:
        mirror.build_plan(bpy.context)
        assert False
    except mirror.MirrorError as exc:
        assert 'locked' in str(exc)


def test_group_verification_ignores_zero_memberships_and_float_noise():
    expected = {'Mask': {0: .5, 1: 0.0}}
    equivalent = (
        SimpleNamespace(name='Mask', weights=((0, .5000004), (1, 0.0))),
    )
    assert not mirror._group_verification_diff(expected, equivalent)

    changed = (SimpleNamespace(name='Mask', weights=((0, .502),)),)
    diff = mirror._group_verification_diff(expected, changed)
    assert diff and diff[0]['name'] == 'Mask'
    assert diff[0]['changed'] == ((0, .5, .502),)


def main():
    tests = [value for name, value in globals().items() if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('MESH_MIRROR_TESTS_PASSED', len(tests), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
