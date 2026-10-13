"""Existing public ARM setup for an authenticated, freshly imported model only.

The prepared-source importer supplies its pre-import native ID inventory after
validating FBX identity, complete native bones, restored terminal tails and the
strict Unity mapping. Its outer transaction owns every new ID and removes them
on failure. This helper never adopts an artist Rig, repairs Rest, guesses a
Human mapping, creates an Action, or replaces an existing control graph.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re


def _require(value, message):
    if not value:
        raise ValueError(message)


def _file_sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _rows(matrix):
    result = [float(value) for row in matrix for value in row]
    _require(len(result) == 16 and all(math.isfinite(value) for value in result),
             'The fresh model has an invalid native coordinate frame.')
    return result


def _chains(rig, packet, capture):
    """Use the producer's Right/Left indices and exact paths, never a detector."""
    by_path = {}
    for index, bone in enumerate(packet['bones']):
        _require(bone['path'] not in by_path, 'The prepared packet has ambiguous bone paths.')
        by_path[bone['path']] = (index, bone)
    result, used = {}, set()
    _require(len(capture['bindings']) == 2, 'Prepare the two exact arm bindings first.')
    for side, binding in zip(('R', 'L'), capture['bindings']):
        for field in ('rigWeight', 'constraintWeight', 'targetPositionWeight',
                      'targetRotationWeight', 'hintWeight'):
            _require(type(binding[field]) in (int, float) and binding[field] in (0., 1.),
                     'Partial arm weights need explicit calibrated proof before setup.')
        _require(binding['maintainTargetPositionOffset'] is False
                 and binding['maintainTargetRotationOffset'] is False,
                 'Maintained Target offsets need explicit offset proof before setup.')
        names, indices = [], []
        for field in ('rootPath', 'midPath', 'tipPath'):
            _require(binding[field] in by_path, 'An arm binding is absent from the complete prepared packet.')
            index, bone = by_path[binding[field]]
            names.append(bone['name'])
            indices.append(index)
        _require(len(set(names)) == 3 and not used.intersection(names)
                 and all(name in rig.data.bones for name in names),
                 'Both arms need distinct existing native root/mid/tip bones.')
        _require(packet['bones'][indices[1]]['parent'] == indices[0]
                 and packet['bones'][indices[2]]['parent'] == indices[1]
                 and rig.data.bones[names[1]].parent == rig.data.bones[names[0]]
                 and rig.data.bones[names[2]].parent == rig.data.bones[names[1]]
                 and all(rig.data.bones[name].use_deform for name in names),
                 'Build only the exact direct native deform arm chains from the prepared packet.')
        used.update(names)
        result[side] = tuple(names)
    return result


def _mapping_header(rig, mapping, original_names):
    _require(set(mapping.indices) == set(original_names)
             and set(mapping.rest_world) == set(original_names),
             'Validate the complete fresh native skeleton before adding controls.')
    for name in original_names:
        _require(_rows(mapping.rest_world[name])
                 == _rows(rig.matrix_world @ rig.data.bones[name].matrix_local),
                 'The supplied mapping belongs to another native Rest skeleton.')
    return {'indices': dict(mapping.indices), 'conversion': _rows(mapping.conversion),
            'rest_world': {name: _rows(mapping.rest_world[name]) for name in original_names}}


def prepare_fresh_arm_controls(context, rig, packet, mapping, *, existing_ids,
                               model_file, model_sha256):
    """Build two Stable arms before the first native-clock base import.

    ``existing_ids`` is the actual prepared-source importer's pre-import
    ``set(bpy.data.user_map())``. No public UI/draw caller may substitute an
    artist Rig or a fabricated empty inventory. On any exception the caller
    must run its existing fresh-ID cleanup; this helper does not delete shared
    IDs or claim recovery of a partially accepted fresh scene.
    """
    import bpy
    from . import animation_rigging_contract as contract
    from . import limb_ik, limb_ik_fk, unity_animation as ua
    from . import unity_animation_rigging as native

    _require(isinstance(existing_ids, (set, frozenset)) and bool(existing_ids),
             'Supply the actual pre-import native ID inventory.')
    _require(context.window is not None and context.scene not in existing_ids
             and rig not in existing_ids and rig.data not in existing_ids
             and rig.type == 'ARMATURE' and rig.library is None and rig.data.library is None
             and rig.override_library is None and rig.data.override_library is None
             and rig.data.users == 1 and context.object == rig
             and context.view_layer.objects.get(rig.name) == rig
             and all(collection not in existing_ids for collection in rig.users_collection),
             'Arm setup is restricted to this operation\'s new local model Rig and owning Scene.')
    memberships = [scene for scene in bpy.data.scenes if scene.objects.get(rig.name) == rig]
    _require(memberships == [context.scene] and context.mode in {'OBJECT', 'POSE'},
             'The new model Rig must belong only to its actual fresh Scene/view layer.')
    _require(not rig.animation_data or (rig.animation_data.action is None
             and not rig.animation_data.nla_tracks and not rig.animation_data.drivers
             and not rig.animation_data.use_tweak_mode),
             'Build fresh arm controls before importing or binding any base/input Action.')
    _require(not any(pb.constraints for pb in rig.pose.bones)
             and not rig.data.get(limb_ik.ARMATURE_ID_KEY)
             and not limb_ik._validate_inventory(rig)['rigs'],
             'The fresh model already contains a control graph; it was left untouched.')
    _require(mapping.controls is None, 'The initial prepared mapping must be the unconstrained native skeleton.')
    model = Path(model_file).expanduser().resolve()
    _require(model.suffix.lower() == '.fbx' and model.is_file()
             and isinstance(model_sha256, str) and re.fullmatch('[0-9a-f]{64}', model_sha256)
             and _file_sha(model) == model_sha256,
             'The exact prepared model FBX changed before arm setup.')
    _require(isinstance(packet.get('_sha256'), str)
             and re.fullmatch('[0-9a-f]{64}', packet['_sha256'])
             and _file_sha(Path(packet['_path'])) == packet['_sha256'],
             'The exact prepared source packet changed before arm setup.')
    capture = contract.validate_capture(packet['rigging'], packet=packet)
    chains = _chains(rig, packet, capture)
    original_data = rig.data
    original_rest = ua._rest_state(original_data)
    expected_indices = {bone['name']: index for index, bone in enumerate(packet['bones'])
                        if bone['name'] in original_rest}
    _require(len(expected_indices) == len(original_rest) and mapping.indices == expected_indices,
             'The initial mapping must retain every exact prepared native bone index.')
    before_mapping = _mapping_header(rig, mapping, original_rest)
    desired = {name: rig.pose.bones[name].matrix.copy() for name in original_rest}

    # A new armature UUID cannot own a pre-existing widget. Public Build also
    # checks these exact names; reject up front rather than reuse global shapes.
    _require(bpy.data.collections.get(limb_ik.WIDGET_COLLECTION_NAME) is None
             and all(bpy.data.objects.get(name) is None and bpy.data.meshes.get(name) is None
                     for name in limb_ik.WIDGET_NAMES.values()),
             'Existing global Body widget resources cannot be shared by this fresh import.')
    settings = limb_ik._settings(context)
    _require(settings is not None, 'Reload the complete add-on before public Arm setup.')
    fields = ('armature', 'analysis_json', 'direct_preroll_json',
              'pole_direction_armature_token', 'build_method', 'pole_distance_ratio',
              'right_arm_upper', 'right_arm_lower', 'right_arm_end',
              'left_arm_upper', 'left_arm_lower', 'left_arm_end',
              'right_arm_pole_direction', 'left_arm_pole_direction', 'last_level', 'last_message')
    saved = {name: (tuple(getattr(settings, name)) if name.endswith('_pole_direction')
                   else getattr(settings, name)) for name in fields}
    primary = None
    try:
        # Analyze supplies only the public, current native Rest stamp. The
        # detector's guessed limb names never enter our exact manual settings.
        analysis = limb_ik.analyze_armature(rig)
        settings.armature = rig
        settings.analysis_json = json.dumps(analysis, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        settings.direct_preroll_json = ''
        settings.pole_direction_armature_token = str(rig.data.as_pointer())
        settings.build_method = 'ROLL_DECOUPLED'
        settings.pole_distance_ratio = .75
        for side in ('R', 'L'):
            prefix = 'right' if side == 'R' else 'left'
            for role, name in zip(('upper', 'lower', 'end'), chains[side]):
                setattr(settings, prefix + '_arm_' + role, name)
            # These are the existing explicit native setup defaults, not
            # inferred Unity Hint channels or a new rest/pose repair.
            setattr(settings, prefix + '_arm_pole_direction', limb_ik.DEFAULT_POLE_DIRECTIONS['ARM', side])
        operator = bpy.ops.character_designer.limb_ik_build_arm
        _require(operator.poll(), 'The existing public Build Arm Rig operator is unavailable in this context.')
        returned = operator('EXEC_DEFAULT', False)
        status = str(settings.last_message)
        _require(returned == {'FINISHED'}, 'Public Build Arm Rig did not finish: ' + status)
        after_rest = ua._rest_state(rig.data)
        _require(rig.data == original_data
                 and {name: after_rest.get(name) for name in original_rest} == original_rest,
                 'Stable Arm setup changed original source Rest; discard only this fresh import.')
        inventory = limb_ik._validate_inventory(rig)
        _require(inventory['schema'] == limb_ik.ROLL_DECOUPLED_SCHEMA
                 and set(inventory['rigs']) == {('ARM', 'R'), ('ARM', 'L')}
                 and all(tuple(inventory['rigs']['ARM', side]['chain']) == chains[side] for side in ('R', 'L')),
                 'Public setup did not produce exactly the two authenticated Stable arm chains.')
        # Use the existing pose matcher to start both limbs in BASE/FK. No keys
        # or Action are created; the later native importer keys its own FK=0.
        for side in ('R', 'L'):
            limb_ik_fk.switch_limb(context, rig, ('ARM', side), 'FK', keyframe=False,
                desired_pose={name: desired[name] for name in chains[side]}, sync_display=False)
        after_mapping = ua._mapping(rig, packet, context.scene.unit_settings.scale_length)
        _require(_mapping_header(rig, after_mapping, original_rest) == before_mapping
                 and after_mapping.controls is not None
                 and all(limb_ik_fk.mode_for_rig(rig, inventory['rigs']['ARM', side]) == 'FK'
                         for side in ('R', 'L')),
                 'Arm setup changed the sealed mapping or did not leave BASE/FK active.')
        plan = native._binding_plan(rig, packet, capture, after_mapping)
        _require([row['native_side'] for row in plan] == ['R', 'L']
                 and all(tuple(row['chain']) == chains[row['native_side']] for row in plan),
                 'Generated controls failed the unchanged native Target/Hint binding admission.')
        _require(not rig.animation_data or rig.animation_data.action is None,
                 'Arm setup unexpectedly created a base Action.')
        _require(_file_sha(model) == model_sha256
                 and _file_sha(Path(packet['_path'])) == packet['_sha256'],
                 'A prepared input changed during public arm setup; discard this fresh import.')
        return {'schema': 'cdesigner.fresh-arm-rigging-setup/1',
            'source_packet_sha256': packet['_sha256'], 'model_file': str(model), 'model_sha256': model_sha256,
            'rig_name': rig.name, 'scene_name': context.scene.name, 'native_schema': inventory['schema'],
            'arm_chains': {side: list(names) for side, names in chains.items()},
            'native_binding_plan': plan, 'base_modes': ['FK', 'FK'],
            'original_rest_unchanged': True, 'source_mapping_unchanged': True,
            'public_operator_result': sorted(returned), 'public_operator_status': status}
    except Exception as error:
        primary = error
        raise
    finally:
        errors = []
        for name, value in saved.items():
            try:
                setattr(settings, name, value)
            except Exception as error:
                errors.append(name + ': ' + str(error))
        if errors:
            message = 'Original Arm setup edit buffer could not be restored: ' + '; '.join(errors)
            if primary is not None:
                primary.add_note(message)
            else:
                raise ValueError(message)
