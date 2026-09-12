"""Complete Body teardown preserves skinning and respects animation/dependencies."""
import os
import sys
import json

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, 'addons'), os.path.join(ROOT, 'tests')]
from character_designer import body_setup_removal as service, body_setup_transaction as transaction
from character_designer import limb_ik, root_control, limb_ik_fk, limb_fk_visuals, forearm_twist
import test_root_control_blender as fixtures
import test_limb_ik_fk_blender as limb_tests


def update(rig):
    rig.update_tag(refresh={'OBJECT'})
    bpy.context.view_layer.update()


def run(rig, **options):
    plan = service.preflight(bpy.context, rig, **options)
    snap = transaction.capture(bpy.context, rig)
    old = forearm_twist._BUSY
    forearm_twist._BUSY = True
    try:
        result = service.execute(bpy.context, rig, plan)
        transaction.assert_original_ids(snap)
        return result, snap
    except Exception:
        transaction.restore(bpy.context, rig, snap)
        transaction.discard(snap)
        raise
    finally:
        forearm_twist._BUSY = old


def bind_probe_surface(rig, radius=1.):
    """Check displaced points along every limb, not only the torso fixture."""
    vertices, groups = [], []
    for bone in rig.data.bones:
        if not bone.use_deform or bone.get(limb_ik.OWNER_KEY) in limb_ik.GENERATED_CONTROL_OWNERS:
            continue
        indices = []
        for local in ((.06 * radius, 0, .02 * radius),
                      (-.05 * radius, bone.length * .5, -.035 * radius),
                      (.04 * radius, bone.length, .03 * radius)):
            indices.append(len(vertices))
            vertices.append(bone.matrix_local @ Vector(local))
        groups.append((bone.name, indices))
    mesh = bpy.data.meshes.new('Whole Body Skin Probes')
    mesh.from_pydata(vertices, [], [])
    obj = bpy.data.objects.new(mesh.name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new('Armature', 'ARMATURE').object = rig
    for name, indices in groups:
        obj.vertex_groups.new(name=name).add(indices, 1., 'REPLACE')
    update(rig)
    return obj


def test_complete_posed_body_removal_and_rollback():
    rig = fixtures.fixture()
    root_control.build(bpy.context, rig)
    limb_fk_visuals.build(bpy.context, rig)
    master = rig.pose.bones['CTRL_master']
    master.location, master.rotation_euler = (.13, -.04, .1), (0, 0, 0)
    master[root_control.SCALE_PROPERTY] = 1.0
    for key, data in limb_ik._validate_inventory(rig)['rigs'].items():
        if key[0] == 'ARM':
            rig.pose.bones[data['target'].name].location.y -= .035
    update(rig)
    bind_probe_surface(rig)
    before = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
    native = {pb.name: pb.matrix @ pb.bone.matrix_local.inverted()
              for pb in rig.pose.bones if pb.bone.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS}
    mesh = bpy.data.objects['Spine Weight Fixture']
    evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    vertices = [vertex.co.copy() for vertex in evaluated.data.vertices]
    result, snap = run(rig, keep_native_rest=True)
    assert result['bones_removed'] and not limb_ik._validate_inventory(rig)['rigs']
    assert set(rig.data.bones.keys()) == set(native)
    assert result['skin_error'] <= service.SKIN_MATRIX_TOLERANCE
    assert result['surface_error'] <= service.SURFACE_TOLERANCE
    print('POSED_REMOVAL_ERRORS', result['skin_error'], result['surface_error'], flush=True)
    evaluated = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert max((a - b.co).length for a, b in zip(vertices, evaluated.data.vertices)) < 2e-5
    transaction.restore(bpy.context, rig, snap)
    transaction.discard(snap)
    fixtures.root._verify_pose(rig, before)
    limb_ik._validate_inventory(rig)


def test_mixed_fk_blend_and_stale_manual_size_anchor():
    rig = fixtures.fixture()
    root_control.build(bpy.context, rig)
    limb_fk_visuals.fit_ik_sizes(bpy.context, rig)
    inventory = limb_ik._validate_inventory(rig)
    for key, data in inventory['rigs'].items():
        if key[0] == 'ARM':
            pb = rig.pose.bones[data['target'].name]
            pb.bone[limb_ik.AUTO_ALIGN_KEY] = False
            pb.custom_shape_transform = None
            for _owner, con, entry in data['entries']:
                if entry['role'] == 'END_ROTATION':
                    con.mute = False
                elif entry['role'] == 'AUTO_OFFSET_ROTATION':
                    con.mute = True
            pb[limb_ik_fk.PROPERTY] = .5
    update(rig)
    result, snap = run(rig, keep_native_rest=True)
    assert result['retained_rest'] and service.PRESERVED_REST_KEY in rig.data
    assert limb_fk_visuals.IK_SIZE_RECORD_KEY not in rig.data
    transaction.discard(snap)


def test_external_bone_constraint_and_animation_refused_before_mutation():
    rig, key, data = limb_tests.build('DIRECT_PREROLL', 'LEFT_ARM')
    root = rig.pose.bones['Hips']
    con = root.constraints.new('COPY_LOCATION')
    con.target, con.subtarget = rig, data['target'].name
    try:
        service.preflight(bpy.context, rig)
    except ValueError:
        pass
    else:
        raise AssertionError('Foreign constraint should block removal')
    root.constraints.remove(con)
    rig.pose.bones[data['target'].name].keyframe_insert('location', frame=1)
    names = set(rig.data.bones.keys())
    try:
        service.preflight(bpy.context, rig)
    except ValueError as exc:
        assert 'animation' in str(exc).lower()
    else:
        raise AssertionError('Animation should block removal')
    assert names == set(rig.data.bones.keys())


def test_corrective_metadata_kept_or_safely_rebased():
    rig, key, data = limb_tests.build('DIRECT_PREROLL', 'LEFT_ARM')
    mesh_data = bpy.data.meshes.new('Calibrated Body')
    mesh_data.from_pydata([(0, 0, 0)], [], [])
    mesh = bpy.data.objects.new('Calibrated Body', mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    record = {'armature': rig.name, 'chain': list(data['chain']),
              'rest': forearm_twist._rest_signature(rig, data['chain']), 'key': 'existing_artist_key'}
    mesh[forearm_twist.RECORD_KEY] = json.dumps({'L': record})
    raw = mesh[forearm_twist.RECORD_KEY]
    before = forearm_twist._rest_signature(rig, data['chain'])
    result, snap = run(rig, keep_native_rest=True)
    assert result['correctives_preserved'] == 1
    assert forearm_twist._records(mesh)['L']['rest'] == forearm_twist._rest_signature(rig, data['chain'])
    assert forearm_twist._rest_signature(rig, data['chain']) == before
    assert max(abs(a - b) for (_, first), (_, second) in zip(before, forearm_twist._rest_signature(rig, data['chain']))
               for a, b in zip(first, second)) < 1e-6
    transaction.restore(bpy.context, rig, snap)
    assert mesh[forearm_twist.RECORD_KEY] == raw
    assert forearm_twist._rest_signature(rig, data['chain']) == before
    transaction.discard(snap)


def test_surface_limit_refuses_and_recovers_complete_graph():
    rig = fixtures.fixture()
    root_control.build(bpy.context, rig)
    # A long off-axis attachment magnifies an otherwise small shear residual.
    # The matrix threshold alone must never authorize visible surface drift.
    bind_probe_surface(rig, radius=300.)
    data = limb_ik._validate_inventory(rig)['rigs'][('ARM', 'L')]
    rig.pose.bones[data['target'].name].location.y -= .035
    update(rig)
    before = {pb.name: pb.matrix.copy() for pb in rig.pose.bones}
    ids = {obj.name: obj.as_pointer() for obj in bpy.data.objects
           if obj.name == 'Whole Body Skin Probes' or obj == rig}
    try:
        run(rig, keep_native_rest=True)
    except ValueError as exc:
        assert 'would change' in str(exc), str(exc)
    else:
        raise AssertionError('An enlarged surface deviation must block removal')
    fixtures.root._verify_pose(rig, before)
    assert ids == {name: bpy.data.objects[name].as_pointer() for name in ids}
    assert root_control.validate(rig)


def test_stable_generated_master_and_native_roots_remove_together():
    rig, key, data = limb_tests.build('ROLL_DECOUPLED', 'LEFT_ARM')
    bind_probe_surface(rig)
    master = rig.pose.bones[limb_ik.MASTER_NAME]
    master.location = (.1, -.2, .03)
    update(rig)
    assert rig.data.bones['Hips'].parent is None
    assert limb_ik._validate_inventory(rig)['master_records']
    result, snap = run(rig, keep_native_rest=True)
    assert limb_ik.MASTER_NAME not in rig.data.bones
    assert not rig.pose.bones['Hips'].constraints
    assert result['surface_error'] <= service.SURFACE_TOLERANCE
    transaction.restore(bpy.context, rig, snap)
    assert limb_ik._validate_inventory(rig)['master_records']
    transaction.discard(snap)


def test_kept_rest_preserves_already_stale_muted_calibration():
    rig, key, data = limb_tests.build('DIRECT_PREROLL', 'LEFT_ARM')
    mesh_data = bpy.data.meshes.new('Stale Calibration Body')
    mesh_data.from_pydata([(0, 0, 0)], [], [])
    mesh = bpy.data.objects.new(mesh_data.name, mesh_data)
    bpy.context.scene.collection.objects.link(mesh)
    mesh.shape_key_add(name='Basis')
    corrective = mesh.shape_key_add(name='Existing Stale Corrective')
    corrective.data[0].co = (.013, -.027, .041)
    corrective.mute = True
    original_rest = forearm_twist._rest_signature(rig, data['chain'])
    stale_rest = json.loads(json.dumps(original_rest))
    stale_rest[0][1][0] += .01
    record = {'armature': rig.name, 'chain': list(data['chain']),
              'rest': stale_rest, 'topology': 'older topology', 'key': corrective.name, 'enabled': True}
    mesh[forearm_twist.RECORD_KEY] = json.dumps({'L': record})
    raw = mesh[forearm_twist.RECORD_KEY]
    key_pointer, coordinates = corrective.as_pointer(), tuple(corrective.data[0].co)
    try:
        service.preflight(bpy.context, rig, keep_native_rest=False)
    except ValueError as exc:
        assert 'Rest frame' in str(exc)
    else:
        raise AssertionError('Changing Rest must still guard stale calibrations')
    result, snap = run(rig, keep_native_rest=True)
    assert result['retained_rest'] and result['correctives_preserved'] == 1
    assert mesh[forearm_twist.RECORD_KEY] == raw
    assert forearm_twist._rest_signature(rig, data['chain']) == original_rest
    assert corrective.as_pointer() == key_pointer and corrective.mute
    assert tuple(corrective.data[0].co) == coordinates
    transaction.restore(bpy.context, rig, snap)
    assert mesh[forearm_twist.RECORD_KEY] == raw
    assert corrective.as_pointer() == key_pointer and corrective.mute
    assert tuple(corrective.data[0].co) == coordinates
    transaction.discard(snap)


if __name__ == '__main__':
    for test in (test_complete_posed_body_removal_and_rollback,
                 test_mixed_fk_blend_and_stale_manual_size_anchor,
                 test_external_bone_constraint_and_animation_refused_before_mutation,
                 test_corrective_metadata_kept_or_safely_rebased,
                 test_stable_generated_master_and_native_roots_remove_together,
                 test_surface_limit_refuses_and_recovers_complete_graph,
                 test_kept_rest_preserves_already_stale_muted_calibration):
        test()
        print('PASS', test.__name__, flush=True)
    print('BODY_SETUP_REMOVAL_TESTS_PASS 7', flush=True)
