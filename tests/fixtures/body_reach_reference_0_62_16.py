"""Frozen pre-optimization implementation for numerical differential tests only."""
def _match_ik(context, armature, inventory, rig, desired, *, calibrated_rest=False, reach_offset=None):
    target = armature.pose.bones[rig["target"].name]
    pole = armature.pose.bones[rig["pole"].name]
    end = armature.pose.bones[rig["chain"][2]]
    changed = {target.name, pole.name}
    desired_end = desired[end.name]
    solver_position = desired_end.translation + (reach_offset if reach_offset is not None else Vector())
    _set_solver_position(context, armature, rig, solver_position)
    if not calibrated_rest:
        pole_matrix = pole.matrix.copy()
        pole_matrix.translation = _pole_position(armature, rig, desired)
        _set_matrix(context, armature, pole, pole_matrix)

    if inventory["schema"] == _limb().ROLL_DECOUPLED_SCHEMA:
        # The ORI frames carry the freely authored FK roll. Their existing
        # tracking constraints still aim toward the unchanged IK mechanism.
        for role, source in (("ori_upper", rig["chain"][0]), ("ori_lower", rig["chain"][1])):
            pb = armature.pose.bones[rig[role].name]
            _set_matrix(context, armature, pb, desired[source])
            changed.add(pb.name)

    target[PROPERTY] = 1.0
    _update(context, armature)
    _match_pole_plane(context, armature, rig, desired,precise=calibrated_rest)
    end_constraint = next(con for _pb, con, record in rig["entries"] if record["role"] == "END_ROTATION")
    offset = rig.get("auto_offset_rotation")
    if rig.get("foot_controls") and rig['foot_controls'].get('auto_follow') == 1 and rig['auto_align']:
        from . import foot_controls
        foot_controls.match_auto_rotation(context, armature, rig, desired_end)
    elif rig.get("foot_controls"):
        # Reverse-foot owns both end-rotation paths in world space. Its fixed
        # pivots must keep the foot orientation, including in Auto display
        # mode; interpreting the solver as a local offset would double roll.
        solver = armature.pose.bones[rig["solver_target"].name]
        desired_world = armature.matrix_world @ desired_end
        solver_world = armature.matrix_world @ solver.matrix
        target_world = armature.matrix_world @ target.matrix
        delta = desired_world.to_quaternion().normalized() @ solver_world.to_quaternion().normalized().inverted()
        pivot = solver_world.translation.copy()
        transform = Matrix.Translation(pivot) @ delta.to_matrix().to_4x4() @ Matrix.Translation(-pivot)
        _set_matrix(context, armature, target, armature.matrix_world.inverted_safe() @ transform @ target_world)
    elif rig["auto_align"]:
        if offset is None:
            raise _error("Rebuild this rig to add the Auto Align rotation offset before matching IK.")
        # Keep the FK end's natural local transform and solve only the visible
        # target offset. It is usually identity after IK->FK baked that offset.
        old_mute = offset.mute
        offset.mute = True
        _update(context, armature)
        natural = end.matrix.copy()
        if rig.get("auto_rotation_space", "LOCAL") == "PARENT_DELTA":
            _set_matrix(context, armature, target,
                        _limb()._auto_target_rotation_matrix(armature, target, natural, desired_end))
        else:
            natural_local = armature.convert_space(pose_bone=end, matrix=natural, from_space="POSE", to_space="LOCAL")
            desired_local = armature.convert_space(pose_bone=end, matrix=desired_end, from_space="POSE", to_space="LOCAL")
            rotation = natural_local.to_quaternion().inverted() @ desired_local.to_quaternion()
            basis = target.matrix_basis.copy()
            target.matrix_basis = Matrix.LocRotScale(basis.translation, rotation.normalized(), basis.to_scale())
        offset.mute = old_mute
        _update(context, armature)
    elif end_constraint.target_space == "LOCAL_OWNER_ORIENT":
        desired_local = armature.convert_space(pose_bone=end, matrix=desired_end, from_space="POSE", to_space="LOCAL")
        basis = target.matrix_basis.copy()
        target.matrix_basis = Matrix.LocRotScale(basis.translation, desired_local.to_quaternion(), basis.to_scale())
        _update(context, armature)
        # LOCAL_OWNER_ORIENT additionally transports between the hand and
        # target's different rest axes. Correct its evaluated world residual,
        # using the same contract as the existing Auto Align handoff.
        wanted_world = armature.matrix_world @ desired_end
        for _iteration in range(8):
            current_world = armature.matrix_world @ end.matrix
            if _rotation_error(current_world, wanted_world) <= 3.0e-4:
                break
            correction = wanted_world.to_quaternion().normalized() @ current_world.to_quaternion().normalized().inverted()
            target_world = armature.matrix_world @ target.matrix
            corrected = Matrix.LocRotScale(target_world.translation, correction @ target_world.to_quaternion().normalized(), target_world.to_scale())
            _set_matrix(context, armature, target, armature.matrix_world.inverted_safe() @ corrected)
    else:
        solver = armature.pose.bones[rig["solver_target"].name]
        delta = desired_end.to_quaternion() @ solver.matrix.to_quaternion().inverted()
        pivot = solver.matrix.translation.copy()
        transform = Matrix.Translation(pivot) @ delta.to_matrix().to_4x4() @ Matrix.Translation(-pivot)
        _set_matrix(context, armature, target, transform @ target.matrix)
    _set_solver_position(context, armature, rig, solver_position)
    return changed

def _refine_calibrated_reach(context, armature, inventory, rig, desired):
    """Fit float32 shallow-limb reach using generated controls only.

    A sub-micron target distance residual can amplify into a visible elbow
    residual on a nearly straight chain. Bound the search to a few parts per
    million of limb length; keep the best evaluated full pose, never relax the
    builder's native Rest, skin or surface checks.
    """
    start,joint,end=[desired[n].translation for n in rig['chain']]
    axis=end-start
    length=(joint-start).length+(end-joint).length
    if length<1e-8 or _limb()._project_perpendicular(joint-start,axis).length>length*.01:
        return
    axis.normalize()
    errors=_pose_errors(armature,desired)
    if errors[0]<=length*2e-6 and errors[1]<=1e-5:return
    controls=[armature.pose.bones[rig[k].name] for k in ('target','pole')]
    baseline=[p.matrix_basis.copy() for p in controls]
    best=[m.copy() for m in baseline]
    def score():
        position,rotation,scale=_pose_errors(armature,desired)
        return position/length+rotation+scale
    best_score=score();best_offset=0.;bound=length*4e-6;step=bound/4
    for _level in range(3):
        center=best_offset
        for index in range(-4,5):
            offset=max(-bound,min(bound,center+step*index))
            for pb,matrix in zip(controls,baseline):pb.matrix_basis=matrix
            _update(context,armature)
            _match_ik(context,armature,inventory,rig,desired,calibrated_rest=True,reach_offset=axis*offset)
            error=score()
            if error<best_score:
                best_score,best_offset=error,offset
                best=[p.matrix_basis.copy() for p in controls]
        step*=.25
    for pb,matrix in zip(controls,best):pb.matrix_basis=matrix
    _update(context,armature)
