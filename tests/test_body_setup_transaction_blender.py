"""Composite rollback keeps native IDs and restores edited owned resources."""
import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import body_setup_transaction as transaction
from character_designer import limb_ik, limb_ik_fk, root_control, eye_controls, torso_controls
from character_designer import spine_ik_fk, foot_controls, limb_fk_visuals, widget_collections
import test_root_control_blender as root_tests
import test_limb_ik_fk_blender as limb_tests
import test_limb_ik_blender as base


def update(rig):
    rig.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()


def matrices(rig):
    update(rig)
    return {pb.name: pb.matrix.copy() for pb in rig.pose.bones}


def check(before, rig):
    update(rig)
    assert set(before) == set(rig.pose.bones.keys())
    error = max(abs(rig.pose.bones[name].matrix[i][j] - matrix[i][j])
                for name, matrix in before.items() for i in range(4) for j in range(4))
    assert error < 4e-5, error
    limb_ik._validate_inventory(rig)
    for module in (root_control, eye_controls, torso_controls, spine_ik_fk, foot_controls, limb_fk_visuals):
        module.validate(rig)


def no_backups():
    assert not [mesh.name for mesh in bpy.data.meshes if mesh.name.startswith('__CD_BODY_TRANSACTION__')]


def test_noop_capture_preserves_resource_users_and_pose():
    rig = root_tests.fixture()
    root_control.build(bpy.context, rig)
    empty = bpy.data.objects.new('Artist reference Empty', None)
    bpy.context.scene.collection.objects.link(empty)
    empty_id = empty.as_pointer()
    native_rest = {bone.name: tuple(value for row in bone.matrix_local for value in row)
                   for bone in rig.data.bones
                   if bone.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS}
    before = matrices(rig)
    users = {obj.name: (obj.users, obj.data.users) for obj in bpy.data.objects if obj.type == 'MESH'}
    snap = transaction.capture(bpy.context, rig)
    assert users == {obj.name: (obj.users, obj.data.users) for obj in bpy.data.objects if obj.type == 'MESH'}
    transaction.restore(bpy.context, rig, snap)
    check(before, rig)
    transaction.assert_original_ids(snap)
    assert empty.as_pointer() == empty_id and empty.data is None
    assert native_rest == {name: tuple(value for row in rig.data.bones[name].matrix_local for value in row)
                           for name in native_rest}
    transaction.discard(snap)
    no_backups()


def test_partial_removal_restores_graph_and_edited_widgets():
    rig = root_tests.fixture()
    root_control.build(bpy.context, rig)
    master = rig.pose.bones['CTRL_master']
    master.location = (.13, -.07, .02)
    master.rotation_euler = (.12, -.19, .21)
    master[root_control.SCALE_PROPERTY] = 1.06
    before = matrices(rig)
    eyes = eye_controls.get_record(rig)
    widget = rig.pose.bones[eyes['master']].custom_shape
    widget.data.vertices[0].co += Vector((.019, .017, -.011))
    attribute = widget.data.attributes.new('artist_value', 'FLOAT', 'POINT')
    attribute.data[0].value = .731
    widget['artist_note'] = 'retain edited geometry'
    geometry = [tuple(vertex.co) for vertex in widget.data.vertices]
    records = {key: value for key, value in rig.data.items() if isinstance(value, str)}
    native = bpy.data.objects['Spine Weight Fixture']
    ids = (rig.as_pointer(), rig.data.as_pointer(), native.as_pointer(), native.data.as_pointer())
    snap = transaction.capture(bpy.context, rig)
    eye_controls.remove(bpy.context, rig)
    spine_ik_fk.remove(bpy.context, rig)
    transaction.restore(bpy.context, rig, snap)
    check(before, rig)
    assert ids == (rig.as_pointer(), rig.data.as_pointer(), native.as_pointer(), native.data.as_pointer())
    assert records == {key: value for key, value in rig.data.items() if isinstance(value, str)}
    restored = rig.pose.bones[eyes['master']].custom_shape
    assert [tuple(vertex.co) for vertex in restored.data.vertices] == geometry
    assert abs(restored.data.attributes['artist_value'].data[0].value - .731) < 1e-6
    assert restored['artist_note'] == 'retain edited geometry'
    transaction.discard(snap)
    no_backups()


def test_complete_limb_removal_restores_rest_drivers_and_artist_refs():
    rig, key, _rig = limb_tests.build('DIRECT_PREROLL', 'LEFT_ARM')
    before = matrices(rig)
    target = rig.pose.bones[_rig['target'].name]
    target_name = target.name
    target.hide = True
    target['artist_prop'] = .25
    target.id_properties_ui('artist_prop').update(min=-2., max=3., description='Artist setting')
    rest = {bone.name: tuple(tuple(row) for row in bone.matrix_local) for bone in rig.data.bones}
    snap = transaction.capture(bpy.context, rig)
    limb_ik._remove_owned(bpy.context, rig)
    transaction.restore(bpy.context, rig, snap)
    check(before, rig)
    assert max(abs(rig.data.bones[name].matrix_local[i][j] - matrix[i][j])
               for name, matrix in rest.items() for i in range(4) for j in range(4)) < 1e-6
    restored = rig.pose.bones[target_name]
    assert restored.hide and restored['artist_prop'] == .25
    assert restored.id_properties_ui('artist_prop').as_dict()['description'] == 'Artist setting'
    transaction.discard(snap)
    no_backups()


def test_failed_add_removes_only_new_helpers_and_widgets():
    rig, key, _rig = limb_tests.build('DIRECT_PREROLL', 'LEFT_LEG', toes=True)
    before = matrices(rig)
    original_objects = {obj.as_pointer() for obj in bpy.data.objects}
    original_meshes = {mesh.as_pointer() for mesh in bpy.data.meshes}
    snap = transaction.capture(bpy.context, rig)
    foot_controls.build(bpy.context, rig, key, toe_name='toe.L')
    root_control.build(bpy.context, rig)
    transaction.restore(bpy.context, rig, snap)
    check(before, rig)
    transaction.discard(snap)
    assert original_objects == {obj.as_pointer() for obj in bpy.data.objects}
    assert original_meshes == {mesh.as_pointer() for mesh in bpy.data.meshes}
    no_backups()


def test_entire_existing_body_teardown_can_be_recovered():
    rig = root_tests.fixture()
    root_control.build(bpy.context, rig)
    before = matrices(rig)
    all_objects = {obj.name for obj in bpy.data.objects}
    all_meshes = {mesh.name for mesh in bpy.data.meshes}
    snap = transaction.capture(bpy.context, rig)
    eye_controls.remove(bpy.context, rig)
    spine_ik_fk.remove(bpy.context, rig)
    torso_controls.remove(bpy.context, rig)
    for side in tuple(foot_controls.records(rig)):
        foot_controls.remove(bpy.context, rig, ('LEG', side))
    root_control.remove(bpy.context, rig)
    limb_ik._remove_owned(bpy.context, rig)
    transaction.restore(bpy.context, rig, snap)
    check(before, rig)
    transaction.discard(snap)
    assert {obj.name for obj in bpy.data.objects} == all_objects
    assert {mesh.name for mesh in bpy.data.meshes} == all_meshes
    no_backups()


def test_surviving_edited_mesh_and_native_animation_keep_ids():
    rig, _key, data = limb_tests.build('DIRECT_PREROLL', 'LEFT_ARM')
    target = rig.pose.bones[data['target'].name]
    widget = target.custom_shape
    mesh = widget.data
    mesh.clear_geometry()
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, .2)], [], [(0, 1, 2, 3)])
    mesh.polygons[0].use_smooth = True
    mesh.uv_layers.new(name='artist_uv')
    mesh.uv_layers[0].data[2].uv = (.7, .8)
    mesh.attributes.new('artist_float', 'FLOAT', 'POINT').data[1].value = .41
    geometry = transaction._mesh_geometry(mesh)
    rig.pose.bones['Hips'].keyframe_insert('rotation_quaternion', frame=1)
    action = rig.animation_data.action
    keys = [(curve.data_path, curve.array_index, tuple(tuple(point.co) for point in curve.keyframe_points))
            for curve in limb_ik._fcurves_for_action(action)]
    ids = (widget.as_pointer(), mesh.as_pointer(), action.as_pointer())
    snap = transaction.capture(bpy.context, rig)
    mesh.vertices[0].co = (9, 8, 7)
    mesh.uv_layers[0].data[2].uv = (.1, .2)
    mesh.attributes['artist_float'].data[1].value = .9
    target.location = (.2, .1, .3)
    transaction.restore(bpy.context, rig, snap)
    assert ids == (widget.as_pointer(), mesh.as_pointer(), rig.animation_data.action.as_pointer())
    actual_geometry = transaction._mesh_geometry(mesh)
    assert actual_geometry == geometry, {key: (geometry[key], actual_geometry[key])
                                         for key in geometry if geometry[key] != actual_geometry[key]}
    assert keys == [(curve.data_path, curve.array_index, tuple(tuple(point.co) for point in curve.keyframe_points))
                    for curve in limb_ik._fcurves_for_action(action)]
    transaction.discard(snap)
    no_backups()


def test_first_body_build_failure_restores_native_only_rig():
    base.reset_scene()
    rig = base.make_humanoid(roll_offset=.37)
    result, settings = base.analyze(rig)
    assert result == {'FINISHED'}
    settings.build_method, settings.selected_limb = 'DIRECT_PREROLL', 'LEFT_ARM'
    assert bpy.ops.character_designer.limb_ik_direct_preroll_check() == {'FINISHED'}
    before = matrices(rig)
    ids = {obj.name: obj.as_pointer() for obj in bpy.data.objects}
    collections = set(bpy.data.collections.keys())
    snap = transaction.capture(bpy.context, rig)
    assert bpy.ops.character_designer.limb_ik_build_selected() == {'FINISHED'}
    root_control.build(bpy.context, rig)
    transaction.restore(bpy.context, rig, snap)
    check(before, rig)
    transaction.discard(snap)
    assert ids == {obj.name: obj.as_pointer() for obj in bpy.data.objects}
    assert collections == set(bpy.data.collections.keys())
    no_backups()


if __name__ == '__main__':
    for test in (test_noop_capture_preserves_resource_users_and_pose,
                 test_partial_removal_restores_graph_and_edited_widgets,
                 test_complete_limb_removal_restores_rest_drivers_and_artist_refs,
                 test_failed_add_removes_only_new_helpers_and_widgets,
                 test_entire_existing_body_teardown_can_be_recovered,
                 test_surviving_edited_mesh_and_native_animation_keep_ids,
                 test_first_body_build_failure_restores_native_only_rig):
        test()
        print('PASS', test.__name__, flush=True)
    print('BODY_SETUP_TRANSACTION_TESTS_PASS 7', flush=True)
