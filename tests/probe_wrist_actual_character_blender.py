"""Actual character native transform-operator regression matrix (never saves input).

Run Blender --factory-startup -b X.blend --python this_file -- --output report.json.
LOCAL Y uses Blender's native transform operator after the second Y constraint,
not simulated keyboard events. Skin checks evaluate the deformed control cage and
compare it against independent linear skinning of the same shape-key input.
"""
import argparse, hashlib, json, math, os, sys
from pathlib import Path
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'addons'), str(ROOT / 'tests')]
from character_designer import limb_ik, forearm_twist
from test_wrist_rotation_blender import select, update, rotation_vector
from test_limb_ik_blender import ensure_registered


def matrix_error(a, b):
    return max(abs(a[i][j]-b[i][j]) for i in range(4) for j in range(4))


def run(out):
    source = Path(bpy.data.filepath)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    ensure_registered()
    rig = bpy.data.objects['CoshaRig']
    mesh = bpy.data.objects['Cosha']
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    rig.hide_set(False)
    rig.select_set(True)
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='POSE')
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = False
    bpy.context.scene.transform_orientation_slots[0].type = 'GLOBAL'
    bpy.context.scene.tool_settings.use_proportional_edit = False
    bpy.context.scene.tool_settings.use_proportional_edit_objects = False
    for mod in mesh.modifiers:
        if mod.type == 'SUBSURF':
            mod.show_viewport = False  # evaluate original control-cage indices
    assert [(m.type, m.use_deform_preserve_volume) for m in mesh.modifiers if m.type == 'ARMATURE'] == [('ARMATURE', False)]
    assert rig.animation_data.action is None, 'Probe does not detach authored animation'
    w = bpy.context.window
    a = next(a for a in w.screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in a.regions if r.type == 'WINDOW')
    view = a.spaces.active.region_3d.view_rotation.copy()
    before_rest = {b.name: b.matrix_local.copy() for b in rig.data.bones}
    before_weights = [[(g.group, g.weight) for g in v.groups] for v in mesh.data.vertices]
    initial_bases = {b.name: b.matrix_basis.copy() for b in rig.pose.bones}
    update(rig)
    before_pose = {b.name: b.matrix.copy() for b in rig.pose.bones}
    report = dict(source=str(source), source_sha256=source_hash, runtime_source=limb_ik.__file__,
                  keyboard_events=False, operator_equivalent='transform.rotate LOCAL/Y (second Y under Global)',
                  mesh_evaluation='Cosha actual skinned control cage, Subdivision disabled in disposable process',
                  as_saved=[], before_sync_posed=[], cases=[], failures=[])

    def rotate(target, axis, orientation, angle):
        select(rig, target)
        with bpy.context.temp_override(window=w, area=a, region=region):
            assert bpy.ops.transform.rotate(value=angle, orient_axis=axis, orient_type=orientation,
                constraint_axis=tuple(c == axis for c in 'XYZ'), use_proportional_edit=False) == {'FINISHED'}
        update(rig)

    # Reproduce the saved-state failure before invoking any migration/update.
    for side in 'LR':
        target = rig.pose.bones['CTRL_hand_IK.'+side]
        hand = rig.pose.bones['hand.'+side]
        basis = target.matrix_basis.copy()
        old = rig.matrix_world @ hand.matrix
        expected = old.col[1].xyz.normalized() * .19
        row = dict(side=side, at_shape=target.use_transform_at_custom_shape,
                   around_shape=target.use_transform_around_custom_shape,
                   custom_shape_transform=target.custom_shape_transform.name if target.custom_shape_transform else None)
        rotate(target, 'Y', 'LOCAL', .19)
        row['rotation_error_rad'] = (rotation_vector(old, rig.matrix_world @ hand.matrix) - expected).length
        row['position_error'] = ((rig.matrix_world @ hand.matrix).translation-old.translation).length
        report['as_saved'].append(row)
        target.matrix_basis = basis
        update(rig)
    for side in 'LR':
        target = rig.pose.bones['CTRL_hand_IK.'+side]
        hand = rig.pose.bones['hand.'+side]
        upper = rig.pose.bones['upper_arm.'+side]
        lower = rig.pose.bones['forearm.'+side]
        for pose in ('flat','raised','bent','root_rotated'):
            for name,basis in initial_bases.items():
                rig.pose.bones[name].matrix_basis = basis
            update(rig)
            sign = 1 if side == 'L' else -1
            length = upper.bone.length + lower.bone.length
            offset = {'flat':(sign*.985,0,0), 'raised':(sign*.65,0,.69),
                      'bent':(sign*.46,-.34,-.25), 'root_rotated':(sign*.46,-.34,-.25)}[pose]
            m = target.matrix.copy()
            m.translation = upper.head + Vector(offset)*length
            target.matrix = m
            update(rig)
            if pose == 'root_rotated':
                root = rig.pose.bones['CTRL_master']
                root.rotation_mode = 'XYZ'
                root.rotation_euler = (.21,-.13,1.17)
                update(rig)
            before = rig.matrix_world @ hand.matrix
            expected = before.col[1].xyz.normalized()*.19
            rotate(target,'Y','LOCAL',.19)
            report['before_sync_posed'].append(dict(side=side,pose=pose,
                rotation_error_rad=(rotation_vector(before,rig.matrix_world@hand.matrix)-expected).length))
    for name,basis in initial_bases.items():
        rig.pose.bones[name].matrix_basis = basis
    update(rig)
    report['sync_changed'] = limb_ik.sync_wrist_local_axes(rig)
    update(rig)
    report['sync_pose_error'] = max(matrix_error(pb.matrix, before_pose[pb.name]) for pb in rig.pose.bones)
    assert report['sync_pose_error'] < 3e-5
    assert limb_ik.sync_wrist_local_axes(rig) == []

    forearm_twist.update_runtime(bpy.context.scene)
    bpy.context.view_layer.update()
    report['prior_calibration_runtime_error'] = forearm_twist._ERRORS.get(mesh.name)
    report['prior_calibration_key_mutes'] = {k.name:k.mute for k in mesh.data.shape_keys.key_blocks if k.name.startswith('CD Forearm Twist')}

    deform = {b.name:b for b in rig.data.bones if b.use_deform}
    group_names = {g.index:g.name for g in mesh.vertex_groups}
    weights = {}
    for v in mesh.data.vertices:
        pairs = [(group_names[g.group], g.weight) for g in v.groups if group_names[g.group] in deform and g.weight>0]
        total = sum(weight for _, weight in pairs)
        weights[v.index] = [(name, weight/total) for name, weight in pairs] if total else []
    samples = {}
    for side in 'LR':
        names = {'hand.'+side, 'forearm.'+side}
        indices = [i for i, ws in weights.items() if sum(weight for name,weight in ws if name in names) > .03]
        hand_core = [i for i in indices if dict(weights[i]).get('hand.'+side, 0) > .5]
        samples[side] = (indices, hand_core)
    report['sample_counts'] = {s:dict(all=len(a), hand_dominant=len(b)) for s,(a,b) in samples.items()}

    def refresh_mesh():
        forearm_twist.update_runtime(bpy.context.scene)
        bpy.context.view_layer.update()
        return mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())

    def input_positions(indices):
        keys = mesh.data.shape_keys
        result = {i:keys.reference_key.data[i].co.copy() for i in indices}
        for key in keys.key_blocks:
            if key == keys.reference_key or key.mute or abs(key.value)<1e-12:
                continue
            assert not key.vertex_group, 'Extend the independent oracle for masked shape keys'
            for i in indices:
                result[i] += (key.data[i].co-key.relative_key.data[i].co) * key.value
        return result

    def expected_skin(indices):
        inputs = input_positions(indices)
        mesh_to_rig = rig.matrix_world.inverted() @ mesh.matrix_world
        rig_to_world = rig.matrix_world
        transforms = {name:rig.pose.bones[name].matrix @ deform[name].matrix_local.inverted() for name in deform}
        result = {}
        for i in indices:
            co = mesh_to_rig @ inputs[i]
            ws = weights[i]
            skinned = sum((transforms[n] @ co * weight for n,weight in ws), Vector()) if ws else co
            result[i] = rig_to_world @ skinned
        return result

    settings = bpy.context.window_manager.character_designer_limb_ik
    for side in 'LR':
        settings.selected_limb = 'LEFT_ARM' if side == 'L' else 'RIGHT_ARM'
        target = rig.pose.bones['CTRL_hand_IK.'+side]
        hand = rig.pose.bones['hand.'+side]
        upper = rig.pose.bones['upper_arm.'+side]
        lower = rig.pose.bones['forearm.'+side]
        for auto in (True, False):
            for name,basis in initial_bases.items():
                rig.pose.bones[name].matrix_basis = basis
            update(rig)
            limb_ik._set_auto_align_selected_target(bpy.context, rig, settings, enabled=auto)
            bases = {b.name:b.matrix_basis.copy() for b in rig.pose.bones}
            for pose in ('flat', 'raised', 'bent', 'root_rotated'):
                for name,basis in bases.items():
                    rig.pose.bones[name].matrix_basis = basis
                update(rig)
                sign = 1 if side == 'L' else -1
                length = upper.bone.length + lower.bone.length
                shoulder = upper.head.copy()
                offset = {'flat':(sign*.985,0,0), 'raised':(sign*.65,0,.69),
                          'bent':(sign*.46,-.34,-.25), 'root_rotated':(sign*.46,-.34,-.25)}[pose]
                target_matrix = target.matrix.copy()
                target_matrix.translation = shoulder + Vector(offset)*length
                target.matrix = target_matrix
                update(rig)
                if pose == 'root_rotated':
                    root = rig.pose.bones['CTRL_master']
                    root.rotation_mode = 'XYZ'
                    root.rotation_euler = (.21,-.13,1.17)
                    update(rig)
                # Capture actual pose rather than treating the label as proof.
                pose_info = dict(upper_y=list((rig.matrix_world@upper.matrix).col[1].xyz.normalized()),
                                 forearm_y=list((rig.matrix_world@lower.matrix).col[1].xyz.normalized()),
                                 hand_y=list((rig.matrix_world@hand.matrix).col[1].xyz.normalized()),
                                 elbow_angle_deg=math.degrees(upper.y_axis.angle(lower.y_axis)))
                target_basis = target.matrix_basis.copy()
                for orientation, axis in (('LOCAL','Y'),('GLOBAL','X'),('GLOBAL','Y'),('GLOBAL','Z'),('VIEW','Z')):
                    for angle_sign in (-1,1):
                        target.matrix_basis = target_basis
                        update(rig)
                        evaluated = refresh_mesh()
                        indices, hand_core = samples[side]
                        old_skin = {i:evaluated.matrix_world@evaluated.data.vertices[i].co for i in indices}
                        old_expected = expected_skin(indices)
                        before = rig.matrix_world @ hand.matrix
                        input_before = rig.matrix_world @ target.matrix
                        display_before = rig.matrix_world @ (target.custom_shape_transform or target).matrix
                        direction = (before.col[1].xyz.normalized() if orientation=='LOCAL' else
                                     view@Vector((0,0,1)) if orientation=='VIEW' else
                                     Vector(tuple(float(c==axis) for c in 'XYZ')))
                        angle = .19*angle_sign
                        rotate(target, axis, orientation, angle)
                        evaluated = refresh_mesh()
                        after = rig.matrix_world @ hand.matrix
                        display_after = rig.matrix_world @ (target.custom_shape_transform or target).matrix
                        actual_skin = {i:evaluated.matrix_world@evaluated.data.vertices[i].co for i in indices}
                        predicted = expected_skin(indices)
                        row = dict(side=side,auto_align=auto,pose=pose,orientation=orientation,axis=axis,sign=angle_sign,
                                   rotation_error_rad=(rotation_vector(before,after)-direction*angle).length,
                                   display_rotation_error_rad=(rotation_vector(display_before,display_after)-direction*angle).length,
                                   input_rotation_error_rad=(rotation_vector(input_before,rig.matrix_world@target.matrix)-direction*angle).length,
                                   hand_origin_shift=(after.translation-before.translation).length,
                                   skin_oracle_error=max((actual_skin[i]-predicted[i]).length for i in indices),
                                   skin_delta_error=max(((actual_skin[i]-old_skin[i])-(predicted[i]-old_expected[i])).length for i in indices),
                                   hand_dominant_max_movement=max(((actual_skin[i]-old_skin[i]).length for i in hand_core),default=0),
                                   pose_geometry=pose_info)
                        report['cases'].append(row)
                        if not (row['rotation_error_rad']<8e-5 and row['display_rotation_error_rad']<8e-5 and
                                row['hand_origin_shift']<3e-5 and row['skin_oracle_error']<3e-5 and row['skin_delta_error']<3e-5 and
                                row['hand_dominant_max_movement']>1e-4):
                            report['failures'].append(row)
                print('WRIST_POSE_DONE',side,auto,pose,flush=True)
            # Restore natural mode before reapplying the original basis.
            for name,basis in bases.items():
                rig.pose.bones[name].matrix_basis = basis
            update(rig)
            limb_ik._set_auto_align_selected_target(bpy.context,rig,settings,enabled=True)
    report['weights_unchanged'] = before_weights == [[(g.group,g.weight) for g in v.groups] for v in mesh.data.vertices]
    report['rest_bones_unchanged'] = all(matrix_error(b.matrix_local,before_rest[b.name])==0 for b in rig.data.bones)
    report['source_unchanged'] = source_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    report['ok'] = not report['failures'] and report['weights_unchanged'] and report['rest_bones_unchanged'] and report['source_unchanged']
    out.write_text(json.dumps(report,indent=2),encoding='utf8')
    print('WRIST_ACTUAL_MATRIX',report['ok'],len(report['cases']),str(out),flush=True)
    assert report['ok'], str(report['failures'][:2])


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    run(args.output)

