"""Shared real-RNA publication checks after frozen-package fixture setup."""
from pathlib import Path
from unittest.mock import patch


def run_public_variant_cases(report, report_path):
    import bpy
    from character_designer import control_pose_offset as offset, control_pose_assets as poses
    from character_designer import animation_worklist as worklist, animation_worklist_revision as revision
    from character_designer import animation_worklist_target as target
    checks = report['checks']
    def require(value, message):
        if not value:
            raise AssertionError(message)
    saved = bpy.context.scene.character_designer_animation_worklist
    rig = saved.rig
    old = worklist.current(bpy.context)
    old_id, old_action, old_slot = old.item_id, old.custom_action, old.custom_slot
    rig_name = rig.name
    bpy.context.scene.frame_set(4, subframe=.25)
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
    before_ids = {action.as_pointer() for action in bpy.data.actions}
    inert = offset.prepare_variant(bpy.context, rig)
    offset.rollback_variant(inert)
    require({action.as_pointer() for action in bpy.data.actions} == before_ids
            and rig.animation_data.action is old_action, 'Prepared-only cancellation changed IDs/binding.')
    checks['public_prepared_only_cancel_is_inert'] = True
    plan = offset.prepare_variant(bpy.context, rig, label='Pose Blend')
    staged = offset.new_variant_action(plan)
    rig.animation_data.action = staged
    rig.animation_data.action_slot = worklist._slot(staged, old_slot)
    native = poses.native_rest(rig)
    pb = next(pb for pb in rig.pose.bones if pb.name in native and not pb.constraints)
    bone_name = pb.name
    pb.location.x += .037
    require(pb.keyframe_insert(data_path='location', frame=4.25), 'Native staged key insertion failed.')
    context_before = (bpy.context.mode, tuple(obj.name for obj in bpy.context.selected_objects),
                      bpy.context.view_layer.objects.active.name)
    result = offset.publish_variant(plan, staged)
    pub_id = result['item_id']
    published = worklist.current(bpy.context)
    require(published.item_id == pub_id and published.custom_action is staged
            and rig.animation_data.action is staged and published.source_action is old.source_action,
            'Published Custom is detached from its row/source.')
    require(bpy.context.scene.frame_current == 4 and abs(bpy.context.scene.frame_subframe - .25) < 1e-8
            and context_before == (bpy.context.mode, tuple(obj.name for obj in bpy.context.selected_objects),
                                   bpy.context.view_layer.objects.active.name),
            'Publication jumped the frame, mode or selection.')
    if published.current_rig_revision:
        revision.validate(bpy.context, pub_id)
    else:
        worklist._check_baseline(published)
    checks['public_keyed_variant_same_subframe_binding_and_row'] = True
    worklist.activate(bpy.context, old_id, 'CUSTOM')
    require(rig.animation_data.action is old_action, 'Original Custom did not reactivate after keying.')
    worklist.activate(bpy.context, pub_id, 'CUSTOM')
    require(rig.animation_data.action is staged, 'Keyed Custom did not reactivate.')
    checks['public_keyed_variant_switch_back_and_return'] = True
    worklist.activate(bpy.context, old_id, 'SOURCE')
    source_ids = {action.as_pointer() for action in bpy.data.actions}
    try:
        offset.prepare_variant(bpy.context, rig)
        raise AssertionError('Source Auto Key was accepted.')
    except ValueError as error:
        require('Source' in str(error) or 'Custom' in str(error), str(error))
    require({action.as_pointer() for action in bpy.data.actions} == source_ids,
            'Source Auto Key refusal created an Action.')
    checks['public_source_autokey_refuses_before_copy'] = True
    worklist.activate(bpy.context, pub_id, 'CUSTOM')
    bpy.context.scene.frame_set(4, subframe=.25)
    bpy.context.scene.tool_settings.use_keyframe_insert_auto = True
    before = target._capture(bpy.context, rig)
    before_ids = {action.as_pointer() for action in bpy.data.actions}
    count = len(saved.items)
    failed = offset.prepare_variant(bpy.context, rig)
    failed_action = offset.new_variant_action(failed)
    rig.animation_data.action = failed_action
    rig.animation_data.action_slot = worklist._slot(failed_action, worklist.current(bpy.context).custom_slot)
    pb = rig.pose.bones[bone_name]
    pb.location.x += .031
    require(pb.keyframe_insert(data_path='location', frame=4.25), 'Staged fault key did not insert.')
    real_publish = offset._publish
    def fail_after_row(*args):
        real_publish(*args)
        raise RuntimeError('injected after owned row/checkpoint publication')
    with patch.object(offset, '_publish', fail_after_row):
        try:
            offset.publish_variant(failed, failed_action)
            raise AssertionError('Publication fault did not raise.')
        except RuntimeError as error:
            require('injected' in str(error), str(error))
    offset.rollback_variant(failed)
    offset.rollback_variant(failed)
    require({action.as_pointer() for action in bpy.data.actions} == before_ids
            and len(saved.items) == count and target._capture(bpy.context, rig) == before,
            'Publication rollback failed to restore fresh state or leaked owned IDs.')
    checks['public_publication_fault_fresh_rollback_owned_cleanup_idempotent'] = True
    filename = Path(report_path).parent / 'keyed_variant_private.blend'
    require('FINISHED' in bpy.ops.wm.save_as_mainfile(filepath=str(filename)), 'Keyed private save failed.')
    require('FINISHED' in bpy.ops.wm.open_mainfile(filepath=str(filename), use_scripts=True), 'Keyed private reopen failed.')
    saved = bpy.context.scene.character_designer_animation_worklist
    require(saved.rig.name == rig_name, 'Keyed rig identity/name disappeared on reopen.')
    for item_id in (pub_id, old_id, pub_id):
        item = worklist._item(saved, item_id)
        if item.current_rig_revision:
            revision.validate(bpy.context, item_id)
        worklist.activate(bpy.context, item_id, 'CUSTOM')
    checks['public_keyed_private_save_reopen_validate_and_switch'] = True
