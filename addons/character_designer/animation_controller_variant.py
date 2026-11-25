"""Generate a full IK alternative from the current character's editable FK Action."""
import json

import bpy

from . import animation_controller_isolation as isolation
from . import animation_controller_mapping as mapping
from . import controller_animation as controller
from . import forearm_twist


def from_fk(context, rig, action, slot, source_hash, progress):
    if not bpy.app.background:
        raise mapping.MappingNeeded('Full-clip IK needs an isolated preparation worker.')
    if action.get('character_designer_controller_representation') != 'FK':
        raise mapping.MappingNeeded('Choose the editable FK motion for this character.')
    try:
        origin = json.loads(action.get(mapping.RECORD, '{}'))
    except (TypeError, ValueError) as error:
        raise mapping.MappingNeeded('The FK motion has incomplete target mapping data.') from error
    if not isinstance(origin, dict) or origin.get('target_rest_sha256') != mapping._rest_fingerprint(rig):
        raise mapping.MappingNeeded('The FK motion was prepared for a different target Rest pose.')
    output = None
    try:
        with forearm_twist.defer_runtime(context, flush_on_exit=False):
            with isolation.isolate_target(context, rig) as private:
                built = controller.build_controller_channels(private.context, private.rig, action, slot,
                    pose_matching=True, daily_edit=True, progress=progress)
                if built['conversion_state'] != 'CONTROLLER':
                    raise mapping.MappingNeeded('The FK character has no usable IK controls.')
                output = action.copy()
                output.use_fake_user = False
                output.name = action.name + ' · IK'
                controller.apply_to_action(output, slot, built)
                diagnostics = dict(schema=mapping.SCHEMA, state='CONTROLLER', native_output_verified=True,
                    control_representation='IK', whole_clip_ik=True, source_action=action.name_full,
                    source_slot=slot, source_action_sha256=source_hash, target_rest_sha256=origin['target_rest_sha256'],
                    controller_conversion=built['diagnostics'], isolation=private.diagnostics)
                output['character_designer_controller_representation'] = 'IK'
                output[mapping.RECORD] = json.dumps(diagnostics, ensure_ascii=False, sort_keys=True, allow_nan=False)
        return mapping.MappingResult(output, slot, source_hash, diagnostics)
    except BaseException as error:
        if output is not None and output.users == 0:
            bpy.data.actions.remove(output)
        if isinstance(error, isolation.IsolationError):
            raise mapping.MappingNeeded('Full IK needs an isolated target control rig: ' + str(error)) from error
        raise
