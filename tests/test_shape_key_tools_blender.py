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


def test_multiple_chosen_keys_use_pre_write_relative_key_snapshot():
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
        assert_close(dependent.data[index].co, before_smile[index])
    for index in (1, 3):
        assert_close(smile.data[index].co, before_smile[index])
        assert_close(dependent.data[index].co, before_dependent[index])
    bm = shape_key_tools.bmesh.from_edit_mesh(obj.data)
    dependent_layer = bm.verts.layers.shape.get(dependent.name)
    if dependent_layer is None:
        raise AssertionError("Edit BMesh did not expose the non-active Shape Key layer")
    for index in (0, 2):
        assert_close(bm.verts[index][dependent_layer], before_smile[index])


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
    selected = {vertex.index for vertex in obj.data.vertices if vertex.select}
    if selected != {0, 1, 2, 3}:
        raise AssertionError(f"Mirror counterpart selection was not expanded: {selected}")


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
        test_multiple_chosen_keys_use_pre_write_relative_key_snapshot,
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
