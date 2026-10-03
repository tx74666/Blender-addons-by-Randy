"""Native tests for coordinate-only symmetry repair, including asymmetric keys.

Run in an isolated factory-startup Blender; never opens or saves an artist file.
"""
import math
from pathlib import Path
import sys
import traceback
from unittest.mock import patch

import bmesh
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'addons'))
from character_designer import refine_symmetry as refine


PAIR_INDICES = tuple((row * 5 + column, row * 5 + 4 - column)
                     for row in range(4) for column in range(2)) + ((20, 21), (22, 23), (24, 25))
CENTER_INDICES = (2, 7, 12, 17)
EPSILON = 2e-6


def reset():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.context.scene.tool_settings.use_mesh_automerge = False
    bpy.context.scene.tool_settings.use_proportional_edit = False
    bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)


def fixture(*, isolated=False, drift=True, keys=True):
    """Bilateral topology with unequal end/row features, independent of positions.

    The triangle at one end and two-vertex chains on row one prevent the
    additional row-reversal automorphism of a plain rectangular grid.
    """
    reset()
    vertices = [(float(column - 2), row + row * row * .1,
                 row * row * .02 + abs(column - 2) * .1)
                for row in range(4) for column in range(5)]
    faces = [(row * 5 + column, row * 5 + column + 1,
              (row + 1) * 5 + column + 1, (row + 1) * 5 + column)
             for row in range(3) for column in range(4)]
    vertices.extend(((-1.7, -.7, .3), (1.7, -.7, .3),
                     (-2.3, 1.3, .1), (2.3, 1.3, .1),
                     (-2.6, 1.5, .15), (2.6, 1.5, .15)))
    faces.extend(((0, 1, 20), (3, 4, 21)))
    edges = [(5, 22), (22, 24), (9, 23), (23, 25)]
    if isolated:
        # Three identical loose topology signatures remain ambiguous in
        # Blender Topology Mirror. Exact mirrored coordinates for two of them
        # must not become an automatic nearest-vertex pairing fallback.
        vertices.extend(((-8., 5., 1.), (8., 5., 1.), (11., 8., 2.)))
    if drift:
        for _negative, positive in PAIR_INDICES[1:]:
            x, y, z = vertices[positive]
            vertices[positive] = (x + .13, y + .04, z - .03)
        for index in CENTER_INDICES:
            x, y, z = vertices[index]
            vertices[index] = (2e-7, y, z)
    mesh = bpy.data.meshes.new('Refine Artist Mesh')
    mesh.from_pydata(vertices, edges, faces)
    mesh.update()
    obj = bpy.data.objects.new('Refine Artist Character', mesh)
    bpy.context.collection.objects.link(obj)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    for vertex in mesh.vertices:
        vertex.select = True
    uv = mesh.uv_layers.new(name='Artist UV')
    for index, item in enumerate(uv.data):
        item.uv = (index / 73., index / 127.)
    for name, domain, kind in (('Artist Index', 'POINT', 'INT'),
                               ('Artist Crease', 'EDGE', 'FLOAT'),
                               ('Artist Corner', 'CORNER', 'FLOAT_VECTOR')):
        attribute = mesh.attributes.new(name, kind, domain)
        for index, item in enumerate(attribute.data):
            if kind == 'FLOAT_VECTOR':
                item.vector = (index * .001, -.003, .4)
            else:
                item.value = index if kind == 'INT' else index / 101.
    for index, edge in enumerate(mesh.edges):
        edge.use_seam = index % 3 == 0
        edge.use_edge_sharp = index % 4 == 0
    mask = obj.vertex_groups.new(name='ArtistMask')
    left = obj.vertex_groups.new(name='Arm.L')
    right = obj.vertex_groups.new(name='Arm.R')
    for index in range(len(vertices)):
        mask.add([index], .1 + index * .01, 'REPLACE')
    left.add([positive for _, positive in PAIR_INDICES], .73, 'REPLACE')
    right.add([negative for negative, _ in PAIR_INDICES], .62, 'REPLACE')
    armature = bpy.data.armatures.new('Existing Artist Armature')
    rig = bpy.data.objects.new('Existing Artist Rig', armature)
    bpy.context.collection.objects.link(rig)
    obj.parent = rig
    modifier = obj.modifiers.new('Existing Armature', 'ARMATURE')
    modifier.object = rig
    modifier.use_deform_preserve_volume = True
    obj.location = (2., -3., 1.)
    obj.scale = (1.2, .8, 1.3)
    obj.rotation_euler = (.1, -.2, .3)
    if keys:
        basis = obj.shape_key_add(name='Basis')
        blink_left = obj.shape_key_add(name='Blink_L')
        blink_right = obj.shape_key_add(name='Blink_R')
        smile = obj.shape_key_add(name='Smile_L')
        chain = obj.shape_key_add(name='Relative To Blink')
        for index, point in enumerate(basis.data):
            old = point.co.copy()
            if old.x > 0:
                blink_left.data[index].co += Vector((.012, -.023, .047 + index * .001))
                smile.data[index].co += Vector((-.03, .04 + index * .002, .015))
            elif old.x < 0:
                blink_right.data[index].co += Vector((-.018, -.041, .031 + index * .001))
            chain.data[index].co += Vector((index * .003, -.02, .011))
        chain.relative_key = blink_left
        chain.vertex_group = 'ArtistMask'
        blink_left.value = .32
        blink_right.value = .14
        smile.value = .22
        chain.value = .1
        blink_left.keyframe_insert('value', frame=1)
        blink_left.value = .57
        blink_left.keyframe_insert('value', frame=12)
        obj.active_shape_key_index = 2
        obj.show_only_shape_key = False
    bpy.context.view_layer.update()
    return obj


def coordinates(obj):
    keys = obj.data.shape_keys
    basis = keys.reference_key.data if keys else obj.data.vertices
    return tuple(tuple(point.co) for point in basis)


def key_coordinates(obj):
    keys = obj.data.shape_keys
    return tuple((key.name, tuple(tuple(point.co) for point in key.data))
                 for key in keys.key_blocks) if keys else ()


def key_deltas(obj):
    basis = coordinates(obj)
    return {name: tuple(tuple(Vector(point) - Vector(base)) for point, base in zip(points, basis))
            for name, points in key_coordinates(obj) if name != 'Basis'}


def preserved(obj):
    mesh = obj.data
    attributes = []
    for attribute in mesh.attributes:
        if attribute.name == 'position' or attribute.name.startswith(('.select', '.hide')):
            continue
        values = []
        for item in attribute.data:
            value = next(getattr(item, name) for name in ('value', 'vector', 'color', 'uv')
                         if hasattr(item, name))
            values.append(tuple(value) if hasattr(value, '__len__') else value)
        attributes.append((attribute.name, attribute.domain, attribute.data_type, tuple(values)))
    keys = mesh.shape_keys
    return (
        obj.as_pointer(), mesh.as_pointer(), mesh.name,
        tuple((vertex.index, tuple((group.group, group.weight) for group in vertex.groups))
              for vertex in mesh.vertices),
        tuple((edge.index, tuple(edge.vertices), edge.use_seam, edge.use_edge_sharp) for edge in mesh.edges),
        tuple((face.index, tuple(face.vertices), tuple(face.loop_indices), face.material_index, face.use_smooth)
              for face in mesh.polygons),
        tuple((loop.index, loop.vertex_index, loop.edge_index) for loop in mesh.loops),
        tuple((layer.name, tuple(tuple(item.uv) for item in layer.data)) for layer in mesh.uv_layers),
        tuple(sorted(attributes)),
        tuple((group.name, group.index, group.lock_weight) for group in obj.vertex_groups),
        obj.parent.as_pointer() if obj.parent else 0, obj.parent_type, obj.parent_bone,
        tuple(tuple(row) for row in obj.matrix_parent_inverse), tuple(tuple(row) for row in obj.matrix_world),
        tuple((modifier.name, modifier.type,
               modifier.object.as_pointer() if modifier.type == 'ARMATURE' and modifier.object else 0)
              for modifier in obj.modifiers),
        (keys.as_pointer(), keys.use_relative,
         keys.animation_data.action.as_pointer() if keys.animation_data and keys.animation_data.action else 0,
         tuple((key.name, key.value, key.relative_key.name, key.vertex_group,
                key.slider_min, key.slider_max, key.mute, key.interpolation)
               for key in keys.key_blocks)) if keys else (),
        obj.active_shape_key_index, obj.show_only_shape_key,
        mesh.use_mirror_x, mesh.use_mirror_y, mesh.use_mirror_z, mesh.use_mirror_topology,
    )


def whole_state(obj):
    return (preserved(obj), coordinates(obj), key_coordinates(obj),
            tuple(tuple(vertex.co) for vertex in obj.data.vertices),
            tuple(vertex.select for vertex in obj.data.vertices),
            tuple(edge.select for edge in obj.data.edges),
            tuple(face.select for face in obj.data.polygons))


def close(actual, expected, tolerance=EPSILON):
    assert (Vector(actual) - Vector(expected)).length <= tolerance, (actual, expected)


def assert_preserved_equal(actual, expected):
    differences = [(index, repr(old)[:160], repr(new)[:160])
                   for index, (new, old) in enumerate(zip(actual, expected)) if new != old]
    if actual[8] != expected[8]:
        old_attributes = {item[0]: item for item in expected[8]}
        new_attributes = {item[0]: item for item in actual[8]}
        attribute_differences = []
        for name in sorted(set(old_attributes) | set(new_attributes)):
            old, new = old_attributes.get(name), new_attributes.get(name)
            if old == new:
                continue
            if old is None or new is None:
                attribute_differences.append((name, 'created' if old is None else 'removed'))
            else:
                changed = [(i, value, new[3][i]) for i, value in enumerate(old[3])
                           if i < len(new[3]) and value != new[3][i]]
                attribute_differences.append((name, old[1:3], new[1:3], len(old[3]), len(new[3]), changed[:4]))
        differences.append(('attribute_details', attribute_differences))
    assert not differences, ('Protected field differences (index, before, after)', differences)


def assert_deltas_equal(obj, before):
    current = key_deltas(obj)
    assert set(current) == set(before)
    for name, deltas in before.items():
        for actual, expected in zip(current[name], deltas):
            close(actual, expected)


def mirror(point):
    x, y, z = point
    return (-x, y, z)


def select_vertices(obj, indices):
    chosen = set(indices)
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        for vertex in bm.verts:
            vertex.select = vertex.index in chosen
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        obj.update_from_editmode()
    else:
        for vertex in obj.data.vertices:
            vertex.select = vertex.index in chosen


def test_analyze_is_read_only_and_pairs_follow_topology():
    obj = fixture(isolated=True)
    before = whole_state(obj)
    analysis = refine.analyze(obj, selected_only=False)
    assert whole_state(obj) == before, 'Analyze changed artist data or selection'
    assert set(analysis.pairs) == set(PAIR_INDICES), analysis.pairs
    assert len({index for pair in analysis.pairs for index in pair}) == 2 * len(analysis.pairs)
    assert analysis.matched == len(PAIR_INDICES)
    assert analysis.misaligned == len(PAIR_INDICES) - 1
    assert set(analysis.centerline) == set(CENTER_INDICES), analysis.centerline
    assert set(analysis.unmatched) == {26, 27, 28}, analysis.unmatched
    assert abs(analysis.max_error - math.sqrt(.13**2 + .04**2 + .03**2)) < EPSILON
    assert not ({26, 27, 28} & set(analysis.misaligned_vertices)), 'Nearest-coordinate guesses leaked into pairing'


def test_edit_analyze_reads_live_basis_without_flushing_or_moving_data():
    obj = fixture()
    obj.active_shape_key_index = 0
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[3].co.y += .05

    def live_state():
        return (
            tuple((tuple(vertex.co), vertex.select, vertex.hide) for vertex in bm.verts),
            tuple((name, tuple(tuple(vertex[bm.verts.layers.shape[name]]) for vertex in bm.verts))
                  for name in bm.verts.layers.shape.keys()),
            tuple((name, tuple(tuple(loop[bm.loops.layers.uv[name]].uv)
                              for face in bm.faces for loop in face.loops))
                  for name in bm.loops.layers.uv.keys()),
            tuple((tuple(v.index for v in edge.verts), edge.select, edge.hide) for edge in bm.edges),
            tuple((tuple(v.index for v in face.verts), face.select, face.hide) for face in bm.faces),
        )

    old_live = live_state()
    old_stored = coordinates(obj), key_coordinates(obj)
    analysis = refine.analyze(obj, selected_only=False)
    expected = max((bm.verts[negative].co - Vector(mirror(bm.verts[positive].co))).length
                   for negative, positive in PAIR_INDICES)
    assert abs(analysis.max_error - expected) <= EPSILON
    assert live_state() == old_live, 'Analyze moved live Edit data or its selection'
    assert (coordinates(obj), key_coordinates(obj)) == old_stored, 'Analyze flushed Edit data into stored Shape Keys'
    bpy.ops.object.mode_set(mode='OBJECT')


def test_average_repairs_with_minimum_movement_and_preserves_all_artist_data():
    obj = fixture(isolated=True)
    old, before, deltas = coordinates(obj), preserved(obj), key_deltas(obj)
    plan = refine.plan(obj, mode='AVERAGE', selected_only=False)
    assert coordinates(obj) == old, 'Planning/preview must not move the mesh'
    validation = refine.apply(obj, plan)
    assert validation.ordinary_mirror_ready and validation.misaligned == 0
    current = coordinates(obj)
    for negative, positive in PAIR_INDICES:
        average = (Vector(old[negative]) + Vector(mirror(old[positive]))) * .5
        close(current[negative], average)
        close(current[positive], mirror(average))
        close(Vector(current[negative]) - Vector(old[negative]),
              -Vector(mirror(Vector(current[positive]) - Vector(old[positive]))))
    for index in CENTER_INDICES:
        assert current[index][0] == 0.
        assert current[index][1:] == old[index][1:]
    assert current[26:] == old[26:], 'Unmatched vertices were moved'
    assert preserved(obj) == before, 'Coordinate repair changed topology or artist relationships'
    assert_deltas_equal(obj, deltas)


def test_directional_modes_copy_the_correct_anatomical_side():
    for mode, source_position, target_position in (('LEFT_TO_RIGHT', 1, 0), ('RIGHT_TO_LEFT', 0, 1)):
        obj = fixture()
        old, before, deltas = coordinates(obj), preserved(obj), key_deltas(obj)
        plan = refine.plan(obj, mode=mode, selected_only=False)
        validation = refine.apply(obj, plan)
        assert validation.ordinary_mirror_ready
        current = coordinates(obj)
        for pair in PAIR_INDICES:
            source, target = pair[source_position], pair[target_position]
            assert current[source] == old[source], (mode, source)
            close(current[target], mirror(old[source]))
        assert preserved(obj) == before
        assert_deltas_equal(obj, deltas)


def test_default_selected_region_is_strict_and_average_requires_both_vertices():
    obj = fixture()
    chosen = set(PAIR_INDICES[2]) | {CENTER_INDICES[0]}
    select_vertices(obj, chosen)
    old, before_keys = coordinates(obj), key_coordinates(obj)
    plan = refine.plan(obj, mode='AVERAGE')
    assert set(plan.positions).issubset(chosen), plan.positions
    refine.apply(obj, plan)
    current = coordinates(obj)
    for index in set(range(len(old))) - chosen:
        assert current[index] == old[index], index
        for (name, points), (old_name, old_points) in zip(key_coordinates(obj), before_keys):
            assert name == old_name and points[index] == old_points[index], (name, index)
    negative, positive = PAIR_INDICES[2]
    close(current[negative], mirror(current[positive]))

    obj = fixture()
    select_vertices(obj, [PAIR_INDICES[2][1]])
    before = whole_state(obj)
    try:
        refine.plan(obj, mode='AVERAGE')
    except refine.RefineSymmetryError:
        pass
    else:
        raise AssertionError('Average silently expanded a one-sided selected region')
    assert whole_state(obj) == before


def test_directional_selected_target_can_read_unselected_source():
    for mode, target_offset, source_offset in (('LEFT_TO_RIGHT', 0, 1), ('RIGHT_TO_LEFT', 1, 0)):
        obj = fixture()
        pair = PAIR_INDICES[3]
        target, source = pair[target_offset], pair[source_offset]
        select_vertices(obj, [target])
        old, before = coordinates(obj), key_coordinates(obj)
        plan = refine.plan(obj, mode=mode)
        assert set(plan.positions) == {target}, plan.positions
        validation = refine.apply(obj, plan)
        assert validation.ordinary_mirror_ready
        current = coordinates(obj)
        close(current[target], mirror(old[source]))
        for index in range(len(old)):
            if index != target:
                assert current[index] == old[index], (mode, index)
                for (_name, points), (_old_name, old_points) in zip(key_coordinates(obj), before):
                    assert points[index] == old_points[index], (mode, index)


def test_centerline_tolerance_does_not_snap_off_plane_or_unselected_vertices():
    obj = fixture(drift=False)
    basis = obj.data.shape_keys.reference_key
    for index, x in ((2, 4e-7), (7, 2e-4), (12, -7e-7)):
        basis.data[index].co.x = obj.data.vertices[index].co.x = x
    select_vertices(obj, [2, 7])
    old = coordinates(obj)
    analysis = refine.analyze(obj, centerline_tolerance=1e-6)
    assert 2 in analysis.centerline and 7 not in analysis.centerline
    plan = refine.plan(obj, centerline_tolerance=1e-6)
    refine.apply(obj, plan)
    current = coordinates(obj)
    assert current[2][0] == 0.
    assert current[7] == old[7] and current[12] == old[12]


def test_preview_refuses_changed_basis_without_any_write():
    for key_name in ('Basis', 'Blink_L'):
        obj = fixture()
        plan = refine.plan(obj, selected_only=False)
        obj.data.shape_keys.key_blocks[key_name].data[3].co.y += .002
        if key_name == 'Basis':
            obj.data.vertices[3].co.y += .002
        changed = whole_state(obj)
        try:
            refine.apply(obj, plan)
        except refine.RefineSymmetryError:
            pass
        else:
            raise AssertionError(f'A stale preview overwrote the artist {key_name} edit')
        assert whole_state(obj) == changed


def test_validation_failure_rolls_back_basis_mesh_and_every_shape_key():
    for edit_mode in (False, True):
        obj = fixture()
        if edit_mode:
            bpy.ops.object.mode_set(mode='EDIT')
        plan = refine.plan(obj, selected_only=False)
        if edit_mode:
            obj.update_from_editmode()
        before = whole_state(obj)
        counts = len(bpy.data.meshes), len(bpy.data.objects), len(bpy.data.shape_keys)

        def injected_failure(*_args, **_kwargs):
            # The hook runs after writing; prove the failure occurs after an
            # actual coordinate change rather than a preflight rejection.
            if edit_mode:
                obj.update_from_editmode()
            assert coordinates(obj) != before[1]
            raise RuntimeError('Injected post-write validation failure')

        with patch.object(refine, 'validate', side_effect=injected_failure):
            try:
                refine.apply(obj, plan)
            except refine.RefineSymmetryError as exc:
                assert 'Injected post-write' in str(exc), str(exc)
            else:
                raise AssertionError('A validation failure did not fail the repair')
        if edit_mode:
            obj.update_from_editmode()
        assert whole_state(obj) == before, ('Rollback incomplete', edit_mode)
        assert (len(bpy.data.meshes), len(bpy.data.objects), len(bpy.data.shape_keys)) == counts
        assert obj.mode == ('EDIT' if edit_mode else 'OBJECT')


def test_edit_mode_nonbasis_active_key_retains_every_original_delta():
    obj = fixture()
    initial, original_coordinates = preserved(obj), (coordinates(obj), key_coordinates(obj))
    deltas = key_deltas(obj)
    # Blender itself creates these three derived UV selection attributes on a
    # no-op mode round-trip. Establish that native baseline independently; all
    # original artist attributes and coordinate buffers must remain exact.
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.object.mode_set(mode='OBJECT')
    before = preserved(obj)
    additions = {item[0] for item in before[8]} - {item[0] for item in initial[8]}
    assert additions == {'.uv_select_edge', '.uv_select_face', '.uv_select_vert'}, additions
    assert all(initial[index] == before[index] for index in range(len(initial)) if index != 8)
    assert all(attribute in before[8] for attribute in initial[8])
    assert (coordinates(obj), key_coordinates(obj)) == original_coordinates
    bpy.ops.object.mode_set(mode='EDIT')
    plan = refine.plan(obj, selected_only=False)
    validation = refine.apply(obj, plan)
    assert validation.ordinary_mirror_ready and obj.mode == 'EDIT'
    obj.update_from_editmode()
    assert_deltas_equal(obj, deltas)
    # Mode round-trips must not replay an old BMesh Shape Key layer over the
    # correctly repaired datablock, including the active Blink_R shape.
    bpy.ops.object.mode_set(mode='OBJECT')
    # Mesh RNA exposes empty UV/attribute buffers while BMesh owns Edit data;
    # compare the authoring snapshot after synchronizing back to Object Mode.
    assert_preserved_equal(preserved(obj), before)
    repaired = coordinates(obj), key_coordinates(obj)
    for _ in range(2):
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.mode_set(mode='OBJECT')
        assert (coordinates(obj), key_coordinates(obj)) == repaired
    assert_deltas_equal(obj, deltas)


def test_plain_mesh_keeps_same_datablock_and_mirror_settings():
    obj = fixture(keys=False)
    obj.data.use_mirror_x = True
    obj.data.use_mirror_y = True
    obj.data.use_mirror_z = False
    obj.data.use_mirror_topology = True
    before = preserved(obj)
    counts = len(bpy.data.meshes), len(bpy.data.objects), len(bpy.data.shape_keys)
    validation = refine.apply(obj, refine.plan(obj, selected_only=False))
    assert validation.ordinary_mirror_ready
    assert preserved(obj) == before and obj.data.shape_keys is None
    assert (len(bpy.data.meshes), len(bpy.data.objects), len(bpy.data.shape_keys)) == counts
    for negative, positive in PAIR_INDICES:
        close(obj.data.vertices[negative].co, mirror(obj.data.vertices[positive].co))


def test_ordinary_mirror_ambiguity_validation_refuses_and_rolls_back():
    obj = fixture(isolated=True)
    basis = obj.data.shape_keys.reference_key
    # A loose artist vertex at the planned repaired coordinate makes native
    # spatial matching ambiguous even though its topology remains unpaired.
    negative, positive = PAIR_INDICES[2]
    repaired_negative = (basis.data[negative].co + Vector(mirror(basis.data[positive].co))) * .5
    basis.data[26].co = obj.data.vertices[26].co = repaired_negative
    before = whole_state(obj)
    try:
        refine.apply(obj, refine.plan(obj, selected_only=False))
    except refine.RefineSymmetryError:
        pass
    else:
        raise AssertionError('Ambiguous ordinary X Mirror was reported as validated')
    assert whole_state(obj) == before


def test_repaired_pairs_pass_native_mirror_with_topology_disabled():
    obj = fixture()
    refine.apply(obj, refine.plan(obj, selected_only=False))
    obj.active_shape_key_index = 0
    obj.data.use_mirror_topology = False
    obj.data.use_mirror_x = True
    bpy.ops.object.mode_set(mode='EDIT')
    for negative, positive in PAIR_INDICES:
        for source, expected in ((negative, positive), (positive, negative)):
            bpy.ops.mesh.select_all(action='DESELECT')
            bm = bmesh.from_edit_mesh(obj.data)
            bm.verts.ensure_lookup_table()
            bm.verts[source].select = True
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            result = bpy.ops.mesh.select_mirror(axis={'X'}, extend=False)
            assert result == {'FINISHED'}
            bm = bmesh.from_edit_mesh(obj.data)
            selected = {vertex.index for vertex in bm.verts if vertex.select}
            assert selected == {expected}, (source, expected, selected)
    bpy.ops.object.mode_set(mode='OBJECT')


def test_ui_analyze_select_preview_and_native_undo():
    import character_designer
    from character_designer import refine_symmetry_ui as ui
    character_designer.register()
    try:
        obj = fixture()
        settings = bpy.context.scene.character_designer_refine_symmetry
        assert settings.selected_only is True and settings.axis == 'X'
        assert settings.mode == 'LEFT_TO_RIGHT'
        # The first visible repair choice is directional. Exercise the
        # existing Average preview/selection/Undo flow explicitly as well.
        settings.mode = 'AVERAGE'
        before = whole_state(obj)
        assert bpy.ops.character_designer.analyze_refine_symmetry() == {'FINISHED'}
        assert whole_state(obj) == before
        assert ui._valid_analysis(bpy.context, force=True) is not None
        assert bpy.ops.character_designer.preview_refine_symmetry() == {'FINISHED'}
        assert whole_state(obj) == before
        assert ui._valid_preview(force=True) is not None
        batches = ui.preview_geometry(refine.plan(obj))
        assert any(positions for _kind, _color, positions in batches)
        assert bpy.ops.character_designer.preview_refine_symmetry() == {'FINISHED'}
        assert ui._preview is None
        result = refine.analyze(obj)
        assert bpy.ops.character_designer.select_refine_misaligned() == {'FINISHED'}
        assert {vertex.index for vertex in obj.data.vertices if vertex.select} == set(result.misaligned_vertices)
        assert coordinates(obj) == before[1] and key_coordinates(obj) == before[2]
        assert preserved(obj) == before[0]
        assert 'UNDO' in ui.CHARACTERDESIGNER_OT_refine_symmetry.bl_options
        assert 'UNDO' in ui.CHARACTERDESIGNER_OT_select_refine_misaligned.bl_options

        # Background Blender operators do not run through the interactive
        # event loop; explicit native checkpoints exercise Blender's own undo.
        select_vertices(obj, range(len(obj.data.vertices)))
        old = coordinates(obj), key_coordinates(obj), key_deltas(obj)
        name = obj.name
        bpy.context.preferences.edit.use_global_undo = True
        assert bpy.ops.ed.undo_push(message='Before Refine Symmetry') == {'FINISHED'}
        assert bpy.ops.character_designer.refine_symmetry() == {'FINISHED'}
        assert coordinates(obj) != old[0]
        assert_deltas_equal(obj, old[2])
        assert bpy.ops.ed.undo_push(message='After Refine Symmetry') == {'FINISHED'}
        assert bpy.ops.ed.undo() == {'FINISHED'}
        restored = bpy.data.objects[name]
        assert coordinates(restored) == old[0]
        assert key_coordinates(restored) == old[1]
    finally:
        character_designer.unregister()
    assert not hasattr(bpy.types.Scene, 'character_designer_refine_symmetry')
    assert ui._invalidate not in bpy.app.handlers.undo_pre
    assert ui._analysis is None and ui._preview is None


def main():
    tests = [value for name, value in globals().items() if name.startswith('test_')]
    for test in tests:
        test()
        print('PASS', test.__name__, flush=True)
    print('REFINE_SYMMETRY_TESTS_PASSED', len(tests), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
