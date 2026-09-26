"""Blender regression tests for local Shape Key deformation cleanup."""

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addons"))

import character_designer
from character_designer import shape_key_tools
from character_designer.ui_constants import UI_PAGE_MISC


EPSILON = 1.0e-7


def assert_close(actual, expected):
    if (actual - expected).length > EPSILON:
        raise AssertionError(f"Expected {tuple(expected)}, got {tuple(actual)}")


def reset_scene():
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)


def make_fixture(symmetric=False):
    reset_scene()
    mesh = bpy.data.meshes.new("ShapeKeyCleanupMesh_Data")
    coordinates = (
        [(-2.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)]
        if symmetric
        else [(-2.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (1.1, 0.15, 0.0), (2.2, 0.25, 0.0)]
    )
    mesh.from_pydata(
        coordinates,
        [],
        [(0, 1, 2, 3)],
    )
    mesh.update()
    obj = bpy.data.objects.new("ShapeKeyCleanupMesh", mesh)
    bpy.context.scene.collection.objects.link(obj)
    basis = obj.shape_key_add(name="Basis")
    smile = obj.shape_key_add(name="Smile")
    smile.data[0].co.y = 0.5
    smile.data[1].co.y = 0.75
    dependent = obj.shape_key_add(name="Dependent")
    dependent.relative_key = smile
    for index in range(len(mesh.vertices)):
        dependent.data[index].co = smile.data[index].co
    dependent.data[0].co.y += 0.25
    dependent.data[2].co.y += 0.4
    obj.active_shape_key_index = 1
    basis.select = False
    smile.select = True
    dependent.select = False

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.context.window_manager.character_designer.ui_page = UI_PAGE_MISC
    bpy.ops.object.mode_set(mode="EDIT")
    return obj, basis, smile, dependent


def select_vertices(obj, indices):
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for vertex in bm.verts:
        vertex.select = vertex.index in set(indices)
    bm.select_flush_mode()
    shape_key_tools.bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def coordinates(key):
    return tuple(vertex.co.copy() for vertex in key.data)


def test_chosen_key_clears_only_selected_vertices():
    obj, basis, smile, dependent = make_fixture()
    before_smile = coordinates(smile)
    before_dependent = coordinates(dependent)
    select_vertices(obj, (0, 2))
    result = bpy.ops.character_designer.clear_shape_key_selected()
    if result != {"FINISHED"}:
        raise AssertionError(f"Active Shape Key cleanup failed: {result}")
    for index in (0, 2):
        assert_close(smile.data[index].co, basis.data[index].co)
    for index in (1, 3):
        assert_close(smile.data[index].co, before_smile[index])
    if coordinates(dependent) != before_dependent:
        raise AssertionError("Active-key cleanup changed another Shape Key")
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    smile_layer = bm.verts.layers.shape.get(smile.name)
    if smile_layer is None:
        raise AssertionError("Edit BMesh did not expose the selected Shape Key layer")
    for index in (0, 2):
        assert_close(bm.verts[index][smile_layer], basis.data[index].co)
        assert_close(bm.verts[index].co, basis.data[index].co)
    bpy.ops.object.mode_set(mode="OBJECT")
    for index in (0, 2):
        assert_close(smile.data[index].co, basis.data[index].co)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    smile_layer = bm.verts.layers.shape.get(smile.name)
    if smile_layer is None:
        raise AssertionError("Shape Key layer disappeared after mode switch")
    for index in (0, 2):
        assert_close(bm.verts[index][smile_layer], basis.data[index].co)
        assert_close(bm.verts[index].co, basis.data[index].co)


def test_multiple_chosen_keys_clear_to_final_relative_key_and_are_idempotent():
    obj, basis, smile, dependent = make_fixture()
    before_smile = coordinates(smile)
    before_dependent = coordinates(dependent)
    dependent.select = True
    select_vertices(obj, (0, 2))
    result = bpy.ops.character_designer.clear_shape_key_selected()
    if result != {"FINISHED"}:
        raise AssertionError(f"All Shape Keys cleanup failed: {result}")
    for index in (0, 2):
        assert_close(smile.data[index].co, basis.data[index].co)
        assert_close(dependent.data[index].co, smile.data[index].co)
    for index in (1, 3):
        assert_close(smile.data[index].co, before_smile[index])
        assert_close(dependent.data[index].co, before_dependent[index])
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    dependent_layer = bm.verts.layers.shape.get(dependent.name)
    if dependent_layer is None:
        raise AssertionError("Edit BMesh did not expose the non-active Shape Key layer")
    for index in (0, 2):
        assert_close(bm.verts[index][dependent_layer], basis.data[index].co)
    assert shape_key_tools.build_shape_key_clear_plan(bpy.context).changed_count == 0
    assert bpy.ops.character_designer.clear_shape_key_selected() == {'FINISHED'}
    bpy.ops.object.mode_set(mode='OBJECT')
    smile.value, dependent.value = 0, 1
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    for index in (0, 2):
        assert_close(evaluated.data.vertices[index].co, basis.data[index].co)
    for index in (1, 3):
        assert_close(dependent.data[index].co, before_dependent[index])


def test_selected_dependency_chain_stops_at_unselected_key():
    obj, basis, smile, dependent = make_fixture()
    bpy.ops.object.mode_set(mode='OBJECT')
    grandchild = obj.shape_key_add(name='Grandchild')
    grandchild.relative_key = dependent
    grandchild.data[0].co.y = 2.0
    untouched = obj.shape_key_add(name='Untouched')
    untouched.data[0].co.y = 3.0
    before_untouched = coordinates(untouched)
    smile.select, dependent.select, grandchild.select, untouched.select = False, False, True, False
    # Child precedes its selected parent in list order; resolution must use dependencies.
    obj.active_shape_key_index = 3
    bpy.ops.object.shape_key_move(type='UP')
    basis, smile, dependent, grandchild, untouched = (
        obj.data.shape_keys.key_blocks[name]
        for name in ('Basis', 'Smile', 'Dependent', 'Grandchild', 'Untouched')
    )
    dependent.select = True
    assert list(obj.data.shape_keys.key_blocks.keys()).index('Grandchild') < list(obj.data.shape_keys.key_blocks.keys()).index('Dependent')
    bpy.ops.object.mode_set(mode='EDIT')
    select_vertices(obj, (0,))
    assert bpy.ops.character_designer.clear_shape_key_selected() == {'FINISHED'}
    bpy.ops.object.mode_set(mode='OBJECT')
    assert_close(dependent.data[0].co, smile.data[0].co)
    assert_close(grandchild.data[0].co, smile.data[0].co)
    assert smile.data[0].co.y == 0.5
    assert coordinates(untouched) == before_untouched
    smile.value, dependent.value, grandchild.value, untouched.value = 0, 1, 1, 0
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert_close(evaluated.data.vertices[0].co, basis.data[0].co)


def test_live_edit_baselines_preserve_pending_edits_without_double_propagation():
    for clear_parent, clear_child in ((False, True), (True, False), (True, True)):
        obj, basis, smile, dependent = make_fixture()
        smile.select, dependent.select = clear_parent, clear_child
        select_vertices(obj, (0,))
        bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.verts[0].co.y = 1.5
        assert bpy.ops.character_designer.clear_shape_key_selected() == {'FINISHED'}
        bpy.ops.object.mode_set(mode='OBJECT')
        assert smile.data[0].co.y == (0.0 if clear_parent else 1.5)
        expected_child = (smile.data[0].co.y if clear_child else 1.75)
        assert dependent.data[0].co.y == expected_child

    obj, basis, smile, dependent = make_fixture()
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.active_shape_key_index = 0
    dependent.select = True
    bpy.ops.object.mode_set(mode='EDIT')
    select_vertices(obj, (0,))
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.y = 1.0
    assert bpy.ops.character_designer.clear_shape_key_selected() == {'FINISHED'}
    bpy.ops.object.mode_set(mode='OBJECT')
    assert basis.data[0].co.y == 1.0
    assert_close(smile.data[0].co, basis.data[0].co)
    assert_close(dependent.data[0].co, basis.data[0].co)


def test_cycles_and_stale_live_edits_refuse_without_cleanup_writes():
    obj, _basis, smile, dependent = make_fixture()
    smile.relative_key = dependent
    dependent.select = True
    select_vertices(obj, (0,))
    before = coordinates(smile), coordinates(dependent)
    assert bpy.ops.character_designer.clear_shape_key_selected() == {'CANCELLED'}
    assert (coordinates(smile), coordinates(dependent)) == before

    obj, _basis, smile, dependent = make_fixture()
    select_vertices(obj, (0,))
    plan = shape_key_tools.build_shape_key_clear_plan(bpy.context)
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.y = 1.5
    try:
        shape_key_tools.apply_shape_key_clear_plan(plan)
    except shape_key_tools.ShapeKeyCleanupError as exc:
        assert 'changed after planning' in str(exc)
    else:
        raise AssertionError('A stale plan overwrote an artist edit')
    assert bm.verts[0].co.y == 1.5
    bpy.ops.object.mode_set(mode='OBJECT')
    assert smile.data[0].co.y == 1.5 and dependent.data[0].co.y == 1.75


def test_failed_cleanup_restores_latest_live_edit():
    obj, _basis, smile, dependent = make_fixture()
    select_vertices(obj, (0,))
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.verts[0].co.y = 1.5
    plan = shape_key_tools.build_shape_key_clear_plan(bpy.context)
    verify = shape_key_tools._verify_records
    failed = False

    def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise shape_key_tools.ShapeKeyCleanupError('Injected verification failure')
        return verify(*args)

    shape_key_tools._verify_records = fail_once
    try:
        try:
            shape_key_tools.apply_shape_key_clear_plan(plan)
        except shape_key_tools.ShapeKeyCleanupError as exc:
            assert 'rolled back' in str(exc)
        else:
            raise AssertionError('Expected the injected verification failure')
    finally:
        shape_key_tools._verify_records = verify
    assert bm.verts[0].co.y == 1.5
    bpy.ops.object.mode_set(mode='OBJECT')
    assert smile.data[0].co.y == 1.5 and dependent.data[0].co.y == 1.75


def test_enabled_x_mirror_clears_and_selects_real_counterparts():
    obj, basis, smile, _dependent = make_fixture(symmetric=True)
    mirror = obj.modifiers.new(name="Artist Mirror", type="MIRROR")
    mirror.use_axis[0] = True
    mirror.use_mirror_merge = False
    select_vertices(obj, (0, 1))
    preview = shape_key_tools.build_shape_key_clear_plan(bpy.context)
    if set(preview.vertex_indices) != {0, 1, 2, 3}:
        raise AssertionError("Mirror preview did not include both real sides")
    result = bpy.ops.character_designer.clear_shape_key_selected()
    if result != {"FINISHED"}:
        raise AssertionError(f"Mirrored Shape Key cleanup failed: {result}")
    for index in range(4):
        assert_close(smile.data[index].co, basis.data[index].co)
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    selected = {vertex.index for vertex in bm.verts if vertex.select}
    if selected != {0, 1, 2, 3}:
        raise AssertionError(f"Mirror counterpart selection was not expanded: {selected}")
    bpy.ops.object.mode_set(mode='OBJECT')
    assert {vertex.index for vertex in obj.data.vertices if vertex.select} == {0, 1, 2, 3}


def test_full_mesh_symmetry_clears_real_counterparts_without_modifier():
    obj, basis, smile, _dependent = make_fixture(symmetric=True)
    select_vertices(obj, (0, 1))
    preview = shape_key_tools.build_shape_key_clear_plan(bpy.context)
    if set(preview.vertex_indices) != {0, 1, 2, 3}:
        raise AssertionError("Full-mesh symmetry preview did not include both real sides")
    result = bpy.ops.character_designer.clear_shape_key_selected()
    if result != {"FINISHED"}:
        raise AssertionError(f"Full-mesh mirrored cleanup failed: {result}")
    for index in range(4):
        assert_close(smile.data[index].co, basis.data[index].co)


def test_empty_selection_refuses_without_writes():
    obj, _basis, smile, dependent = make_fixture()
    before = coordinates(smile), coordinates(dependent)
    select_vertices(obj, ())
    result = bpy.ops.character_designer.clear_shape_key_selected()
    if result != {"CANCELLED"}:
        raise AssertionError(f"Empty Shape Key selection did not cancel: {result}")
    if (coordinates(smile), coordinates(dependent)) != before:
        raise AssertionError("Empty Shape Key selection changed data")


def test_registration_and_poll():
    reset_scene()
    assert bpy.ops.character_designer.clear_shape_key_selected.get_rna_type()
    try:
        bpy.ops.character_designer.clear_shape_keys_selected.get_rna_type()
    except KeyError:
        pass
    else:
        raise AssertionError("Removed All Keys operator is still registered")
    assert hasattr(bpy.types, shape_key_tools.CHARACTERDESIGNER_PT_shape_key_tools.bl_idname)
    assert not shape_key_tools.CHARACTERDESIGNER_OT_clear_shape_key_selected.poll(bpy.context)


def main():
    character_designer.register()
    tests = (
        test_chosen_key_clears_only_selected_vertices,
        test_multiple_chosen_keys_clear_to_final_relative_key_and_are_idempotent,
        test_selected_dependency_chain_stops_at_unselected_key,
        test_live_edit_baselines_preserve_pending_edits_without_double_propagation,
        test_cycles_and_stale_live_edits_refuse_without_cleanup_writes,
        test_failed_cleanup_restores_latest_live_edit,
        test_enabled_x_mirror_clears_and_selects_real_counterparts,
        test_full_mesh_symmetry_clears_real_counterparts_without_modifier,
        test_empty_selection_refuses_without_writes,
        test_registration_and_poll,
    )
    try:
        for test in tests:
            test()
            print(f"PASS {test.__name__}")
    finally:
        reset_scene()
        character_designer.unregister()
    print(f"PASS Shape Key Tools {len(tests)} tests")


if __name__ == "__main__":
    main()
