"""One reversible operation for the character's supported body controls."""
from contextlib import contextmanager

from . import (body_setup_plan, body_setup_transaction, character_setup,
               control_colors, bone_collections, limb_ik)


def plan(context, rig):
    return body_setup_plan.plan(context, rig)


def has_generated(rig):
    if rig is None or rig.type != 'ARMATURE':
        return False
    from . import widget_collections
    # Detection deliberately does not validate: damaged setups still need a
    # visible Remove entry and a precise preflight error when it is used.
    modules = widget_collections._modules()
    owners = {module.OWNER_VALUE for module in modules}
    return (any(bone.get(limb_ik.OWNER_KEY) in owners for bone in rig.data.bones)
            or any(getattr(module, 'RECORD_KEY', '') in rig.data for module in modules)
            or any(pb.custom_shape and pb.custom_shape.get(limb_ik.OWNER_KEY) in owners
                   for pb in rig.pose.bones))


@contextmanager
def _runtime_paused():
    # Runtime corrective keys must never evaluate an intermediate dismantled
    # graph. Their native Mesh/Key datablocks are outside the body transaction.
    from . import forearm_twist
    previous = forearm_twist._BUSY
    forearm_twist._BUSY = True
    try:
        yield
    finally:
        forearm_twist._BUSY = previous


def _require_rig(context, rig):
    if context.mode not in {'OBJECT', 'POSE'}:
        raise ValueError('Finish the current edit and use Body Setup in Object or Pose Mode.')
    active = limb_ik._require_active_armature(context, limb_ik._settings(context), analyzed=False)
    if active != rig:
        raise ValueError('Make the intended character armature active first.')


def _native_skin(rig):
    return {bone.name: rig.pose.bones[bone.name].matrix @ bone.matrix_local.inverted()
            for bone in rig.data.bones if bone.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS}


def _verify_skin(rig, before, tolerance=1e-4):
    for name, skin in before.items():
        bone = rig.data.bones.get(name)
        if bone is None:
            raise ValueError(f'Body Setup lost original bone {name}.')
        current = rig.pose.bones[name].matrix @ bone.matrix_local.inverted()
        error = max(abs(a-b) for row_a, row_b in zip(skin, current) for a, b in zip(row_a, row_b))
        if error > tolerance:
            raise ValueError(f'Body Setup changed the current deformation of {name} ({error:.6g}); changes were rolled back.')


def _limbs(context, rig, kwargs):
    from . import limb_ik_fk
    skin = _native_skin(rig)
    inventory = limb_ik._validate_inventory(rig)
    state = limb_ik._neutralize_existing_master(context, rig)
    try:
        plans = []
        for item in kwargs['chains']:
            chain = limb_ik.LimbChain(item['kind'], item['side'], *(item[role] for role in limb_ik.ROLES))
            upper, lower = rig.pose.bones[chain.upper], rig.pose.bones[chain.lower]
            direction = limb_ik._project_perpendicular(lower.head-upper.head, lower.tail-upper.head).normalized()
            plans.append(limb_ik._build_plan(rig, chain, item['pole_distance'], direction))
        schema = (limb_ik.DIRECT_PREROLL_SCHEMA if kwargs['method'] == 'DIRECT_PREROLL'
                  else limb_ik.ROLL_DECOUPLED_SCHEMA)
        limb_ik._mode_set(context, rig, 'POSE')
        limb_ik._build_plans(context, rig, plans, schema=schema,
                             target_rotation_version=inventory['target_rotation_version'] if inventory['rigs'] else None)
    finally:
        limb_ik._restore_master_state(context, rig, state)
    for item in kwargs['chains']:
        names = [item[role] for role in limb_ik.ROLES]
        desired = {name: skin[name] @ rig.data.bones[name].matrix_local for name in names}
        limb_ik_fk.switch_limb(context, rig, (item['kind'], item['side']), 'IK',
                               keyframe=False, desired_pose=desired)


def _add(context, rig, entry):
    from . import (root_control, foot_controls, torso_controls, spine_ik_fk,
                   eye_controls, limb_fk_visuals, head_neck_visuals, body_detail_visuals)
    key, kwargs = entry['key'], dict(entry['kwargs'])
    if key == 'LIMBS':
        return _limbs(context, rig, kwargs)
    if key.startswith('FEET_'):
        kwargs['shoe'] = character_setup.footwear_reference(context, rig)
        return foot_controls.build(context, rig, **kwargs)
    module = {'ROOT': root_control, 'TORSO': torso_controls, 'SPINE': spine_ik_fk,
              'EYES': eye_controls, 'FK_RINGS': limb_fk_visuals,
              'HEAD_NECK': head_neck_visuals, 'BODY_DETAIL': body_detail_visuals}[key]
    return module.build(context, rig, **kwargs)


def _atomic(context, rig, operation):
    from . import bone_display, bone_display_sync
    with _runtime_paused():
        display_checkpoint = bone_display._checkpoint(bone_display._affected(context, rig))
        checkpoint = body_setup_transaction.capture(context, rig)
        previous_busy = bone_display_sync._BUSY
        bone_display_sync._BUSY = True
        try:
            bone_display.restore_view(rig)
            result = operation()
            body_setup_transaction.assert_original_ids(checkpoint)
            limb_ik._restore_context(context, rig, checkpoint['context'])
            return result
        except Exception as original:
            try:
                body_setup_transaction.restore(context, rig, checkpoint)
                bone_display._rollback(display_checkpoint)
            except Exception as recovery:
                raise RuntimeError(f'Body Setup failed: {original}. In-place recovery also failed: {recovery}.') from original
            raise
        finally:
            body_setup_transaction.discard(checkpoint)
            bone_display_sync._BUSY = previous_busy
            bone_display_sync.completed(rig)


def generate(context, rig):
    """Add supported missing components without rebuilding existing ones."""
    _require_rig(context, rig)
    proposed = plan(context, rig)
    if proposed['blocked']:
        raise ValueError(' '.join(f"{entry['key']}: {entry['reason']}" for entry in proposed['components']
                                  if entry['status'] in {'BLOCKED', 'NEEDS_MAPPING'}))
    result = {'created': [], 'reused': [item['key'] for item in proposed['components'] if item['status'] == 'REUSE'],
              'skipped': [item for item in proposed['components'] if item['status'] == 'SKIP'], 'plan': proposed}
    pending = [item for item in proposed['components'] if item['status'] == 'ADD']
    if not pending:
        def update_axes():
            result['updated'] = limb_ik.sync_wrist_local_axes(rig)
            return result
        result = _atomic(context, rig, update_axes)
        context.scene.character_designer_finger_definition.overlays_enabled = False
        return result
    context.view_layer.update()
    before = _native_skin(rig)

    def commit():
        layout = bone_collections.capture_managed_layout(rig)
        for entry in pending:
            _add(context, rig, entry)
            result['created'].append(entry['key'])
        result['updated'] = limb_ik.sync_wrist_local_axes(rig)
        bone_collections.finish_rig_edit(rig, layout)
        control_colors.sync(rig)
        context.view_layer.update()
        _verify_skin(rig, before)
        validated = plan(context, rig)
        if validated['blocked']:
            raise ValueError('The resulting Body Setup did not pass validation: ' + ' '.join(
                entry['reason'] for entry in validated['components'] if entry['status'] in {'BLOCKED', 'NEEDS_MAPPING'}))
        return result

    result = _atomic(context, rig, commit)
    # Match the Basic Setup eye only after the rig transaction succeeds.
    # Saved finger guides/marks and the rig's custom shapes stay intact.
    context.scene.character_designer_finger_definition.overlays_enabled = False
    return result


def remove(context, rig, *, keep_native_rest=True):
    """Remove owned body components together, retaining the visible character."""
    from . import body_setup_removal
    _require_rig(context, rig)
    prepared = body_setup_removal.preflight(context, rig, keep_native_rest=keep_native_rest)
    before = _native_skin(rig)

    def commit():
        result = body_setup_removal.execute(context, rig, prepared)
        result['display'] = bone_collections.show_original_after_removal(rig)
        control_colors.cleanup(rig)
        context.view_layer.update()
        _verify_skin(rig, before, tolerance=2e-4)
        return result

    return _atomic(context, rig, commit)


def restore_original_display(context, rig):
    """Repair a previously removed Body setup, including a temporary view."""
    _require_rig(context, rig)
    return _atomic(context, rig, lambda: bone_collections.show_original_after_removal(rig))
