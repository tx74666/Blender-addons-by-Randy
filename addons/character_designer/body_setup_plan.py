"""Read-only discovery for the combined Body Setup workflow."""
from . import (body_detail_visuals, character_setup, eye_controls, foot_controls,
               head_neck_visuals, limb_fk_visuals, limb_ik, root_control,
               spine_ik_fk, torso_controls)


COMPONENT_KEYS = ('LIMBS', 'ROOT', 'FEET_L', 'FEET_R', 'TORSO', 'SPINE',
                  'EYES', 'FK_RINGS', 'HEAD_NECK', 'BODY_DETAIL')
_ERRORS = (ValueError, RuntimeError, KeyError, TypeError, ReferenceError)


def _entry(component_key, status='SKIP', reason='No matching anatomy.', **kwargs):
    return {'key': component_key, 'status': status, 'reason': reason, 'kwargs': kwargs}


def _named(armature, aliases):
    return [bone.name for bone in armature.data.bones
            if not bone.get(limb_ik.OWNER_KEY) and character_setup._bone_name(bone.name) in aliases]


def _central(context, armature, role):
    status = character_setup.bone_mapping_status(context, role, armature)
    if status['name']:
        return status['name'], None
    state = 'SKIP' if status['status'] == 'MISSING' else 'NEEDS_MAPPING'
    return None, (state, status['message'])


def _native_inputs(armature, names):
    if any(armature.pose.bones[name].constraints for name in names):
        return 'Existing native constraints are kept unchanged.'
    if torso_controls._animated(armature, set(names)):
        return 'Existing native animation or drivers are kept unchanged.'
    return None


def _display_inputs(armature, names):
    for name in names:
        pb = armature.pose.bones[name]
        if pb.custom_shape is not None or pb.custom_shape_transform is not None:
            return f'Existing artist display on {name} is kept unchanged.'
        if limb_fk_visuals._animated_display(armature, pb):
            return f'Animated display on {name} is kept unchanged.'
    return None


def _animated_limb_ancestor(armature, names):
    """New IK targets must not freeze a limb's already animated parent space."""
    sources = set(names)
    ancestors = {parent.name for name in names for parent in armature.data.bones[name].parent_recursive
                 if parent.name not in sources}
    paths = ('location', 'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle', 'scale', 'matrix_basis')
    prefixes = {armature.pose.bones[name].path_from_id(field)
                for name in ancestors for field in paths}
    curves = [curve for action in limb_ik._actions_for_id(armature)
              for curve in limb_ik._fcurves_for_action(action)]
    if armature.animation_data:
        curves.extend(armature.animation_data.drivers)
    if any(curve.data_path in prefixes for curve in curves):
        return 'Existing ancestor transform animation is kept; adapt or bake that animation before adding IK.'
    return None


def _preflight_limbs(context, armature, inventory, chains, settings):
    """Check the same source geometry and resources the limb builder will use."""
    schema = inventory['schema'] or limb_ik.ROLL_DECOUPLED_SCHEMA
    direct = limb_ik._is_direct_preroll_schema(schema)
    if direct and root_control.get_record(armature):
        raise limb_ik.LimbIKError('Remove the existing Direct Root before adding missing limbs, then generate Body Setup again.')
    if inventory['rigs']:
        if inventory['target_rotation_version'] != limb_ik._default_target_rotation_version(schema):
            raise limb_ik.LimbIKError('Rebuild the older limb controls separately before adding missing limbs.')
        limb_ik._removal_resources(context, armature, inventory)
    plans, used = [], set()
    for item in chains:
        chain = limb_ik.LimbChain(item['kind'], item['side'], *(item[role] for role in limb_ik.ROLES))
        if used.intersection(chain.names):
            raise limb_ik.LimbIKError('Limb mappings overlap; choose a separate native chain for each limb.')
        used.update(chain.names)
        # Unified Setup preserves the current bend. Reorienting a Pole to its
        # anatomical default remains an explicit Advanced operation.
        upper, lower = armature.pose.bones[chain.upper], armature.pose.bones[chain.lower]
        direction = limb_ik._project_perpendicular(lower.head-upper.head, lower.tail-upper.head).normalized()
        item['pole_distance'] = float(settings.pole_distance_ratio) if settings is not None else .75
        item['pole_direction'] = list(direction)
        if direct:
            if any(not limb_ik._pose_bone_is_at_rest(armature, name) for name in chain.names):
                raise limb_ik.LimbIKError(f'{chain.side} {chain.kind.title()} is posed. Adding Direct limbs requires their unconstrained Rest pose.')
            if any(child.name != chain.lower and child.use_connect for child in armature.data.bones[chain.upper].children):
                raise limb_ik.LimbIKError(f'{chain.upper} has a connected sibling that Direct Pre-Roll would move.')
            if limb_ik._direct_preroll_analysis(armature, chain, direction)['status'] == 'UNSTABLE':
                raise limb_ik.LimbIKError(f'{chain.side} {chain.kind.title()} needs a small Rest bend before adding Direct controls.')
        plans.append(limb_ik._build_plan(armature, chain, item['pole_distance'], direction))
    limb_ik._preflight_plans(context, armature, plans, inventory, schema=schema)


def _limbs(context, armature, inventory):
    existing = inventory['rigs']
    settings = limb_ik._settings(context)
    mapped = settings is not None and settings.armature == armature
    missing = [(kind, side) for kind in limb_ik.KINDS for side in limb_ik.SIDES
               if (kind, side) not in existing]
    analysis = limb_ik.analyze_armature(armature) if missing else None
    chains, skipped, problems = [], [], []
    for kind, side in missing:
        try:
            chain = limb_ik._chain_from_settings(settings, armature, kind, side) if mapped else None
        except _ERRORS as exc:
            problems.append(str(exc))
            continue
        detected = analysis['limbs'][kind][side]
        if chain is None:
            if detected['status'] in {'AMBIGUOUS', 'LOW_CONFIDENCE'}:
                problems.append(f'{side} {kind.title()}: choose its three native bones in Advanced.')
                continue
            if detected['status'] != 'READY':
                skipped.append(f'{side} {kind.title()} missing')
                continue
            chain = limb_ik.LimbChain(kind, side, *(detected[role] for role in limb_ik.ROLES))
        protected = (_native_inputs(armature, chain.names)
                     or _animated_limb_ancestor(armature, chain.names))
        if protected:
            skipped.append(f'{side} {kind.title()}: {protected}')
            continue
        chains.append({'kind': kind, 'side': side, **dict(zip(limb_ik.ROLES, chain.names))})
    method = 'DIRECT_PREROLL' if limb_ik._is_direct_preroll_schema(inventory['schema']) else 'ROLL_DECOUPLED'
    if problems:
        return _entry('LIMBS', 'NEEDS_MAPPING', ' '.join(problems), method=method, chains=chains), chains
    if chains and existing and inventory['schema'] not in {limb_ik.DIRECT_PREROLL_SCHEMA, limb_ik.ROLL_DECOUPLED_SCHEMA}:
        return _entry('LIMBS', 'BLOCKED', 'Keep this older limb implementation; upgrade it separately before adding limbs.'), []
    if chains:
        try:
            _preflight_limbs(context, armature, inventory, chains, settings if mapped else None)
        except _ERRORS as exc:
            return _entry('LIMBS', 'BLOCKED', str(exc), method=method, chains=chains), []
    reason = ('Add missing detected limbs.' if chains else 'Existing limb controls are kept.' if existing else 'No unprotected limb chains found.')
    if skipped:
        reason += ' ' + '; '.join(skipped) + '.'
    return _entry('LIMBS', 'ADD' if chains else 'REUSE' if existing else 'SKIP', reason,
                  method=method, chains=chains), chains


def plan(context, rig):
    """Return ordered, serializable component decisions without editing scene state.

    Confirmed per-character Head/Hips choices and current rig-scoped limb/toe
    fields take precedence. Ambiguous or stale mappings block generation.
    Missing optional anatomy and existing artist inputs are reported as skips.
    """
    components = {key: _entry(key) for key in COMPONENT_KEYS}
    result = {'rig': rig.name if rig is not None else '', 'schema': limb_ik.ROLL_DECOUPLED_SCHEMA,
              'components': [], 'blocked': False}

    def put(component_key, status, reason, **kwargs):
        components[component_key] = _entry(component_key, status, reason, **kwargs)

    def finish():
        result['components'] = list(components.values())
        result['blocked'] = any(item['status'] in {'BLOCKED', 'NEEDS_MAPPING'} for item in result['components'])
        return result

    if (rig is None or rig.type != 'ARMATURE' or not rig.is_editable or rig.library or rig.override_library
            or rig.data.library or rig.data.users != 1 or rig.name not in context.scene.objects):
        put('LIMBS', 'BLOCKED', 'Choose a local, single-user armature in this scene.')
        return finish()
    try:
        inventory = limb_ik._validate_inventory(rig)
        result['schema'] = inventory['schema'] or limb_ik.ROLL_DECOUPLED_SCHEMA
        result['source_digest'] = limb_ik._armature_digest(rig)
        limb_entry, pending = _limbs(context, rig, inventory)
        components['LIMBS'] = limb_entry
    except _ERRORS as exc:
        put('LIMBS', 'BLOCKED', str(exc))
        return finish()
    available = {key: tuple(data['chain']) for key, data in inventory['rigs'].items()}
    available.update({(item['kind'], item['side']): tuple(item[role] for role in limb_ik.ROLES) for item in pending})
    controls_available = bool(available)
    hips, hips_problem = _central(context, rig, 'HIPS')
    head, head_problem = _central(context, rig, 'HEAD')
    settings = limb_ik._settings(context)

    def existing(key, module, *args):
        try:
            record = module.validate(rig, *args)
            if record:
                put(key, 'REUSE', 'Existing controls are kept unchanged.')
                return record
        except _ERRORS as exc:
            put(key, 'BLOCKED', str(exc))
            return True
        return None

    root = existing('ROOT', root_control)
    if not root:
        if inventory.get('master'):
            put('ROOT', 'REUSE', 'The existing whole-body Master is kept unchanged.')
        elif controls_available:
            put('ROOT', 'ADD', 'Add a whole-body Root; the Stable limb builder supplies its Master.')
        else:
            put('ROOT', 'SKIP', 'Root needs character limb controls.')

    for side in ('L', 'R'):
        key, part = ('LEG', side), 'FEET_' + side
        try:
            record = foot_controls.get_record(rig, key)
            if record:
                foot_controls.validate(rig, inventory)
                put(part, 'REUSE', 'Existing Foot Roll and Toe Bend are kept.')
                continue
            if key not in available:
                put(part, 'SKIP', 'No corresponding leg controls.')
                continue
            if key in inventory['rigs'] and inventory['schema'] not in {limb_ik.ROLL_DECOUPLED_SCHEMA, limb_ik.DIRECT_PREROLL_SCHEMA}:
                put(part, 'SKIP', 'Foot Controls needs a current leg implementation.')
                continue
            if key in inventory['rigs'] and inventory['rigs'][key].get('auto_offset_rotation') is None:
                put(part, 'SKIP', 'Rebuild this older leg separately before adding Foot Controls.')
                continue
            foot = rig.data.bones[available[key][2]]
            requested = (getattr(settings, 'left_foot_toe' if side == 'L' else 'right_foot_toe', '')
                         if settings is not None and settings.armature == rig else '')
            candidates = [bone.name for bone in foot.children if bone.use_deform and 'toe' in bone.name.lower()
                          and not bone.get(limb_ik.OWNER_KEY)]
            if not requested and not candidates:
                put(part, 'SKIP', 'No native deform toe child; keep the existing foot control.')
                continue
            if not requested and len(candidates) > 1:
                put(part, 'NEEDS_MAPPING', 'Choose one Toe Bone for this foot in Advanced.')
                continue
            toe = foot_controls.resolve_toe(rig, {'chain': available[key]}, requested or candidates[0])
            protected = _native_inputs(rig, [toe.name])
            put(part, 'SKIP' if protected else 'ADD', protected or 'Add Foot Roll and Toe Bend.', key=key, toe_name=toe.name)
        except _ERRORS as exc:
            put(part, 'BLOCKED', str(exc))

    torso = existing('TORSO', torso_controls, inventory)
    sources = torso['sources'] if isinstance(torso, dict) else None
    if not torso:
        if hips_problem:
            put('TORSO', *hips_problem)
        elif not controls_available:
            put('TORSO', 'SKIP', 'Spine Controls needs character limb controls.')
        else:
            current, chain, ambiguous = rig.data.bones[hips], [], False
            while True:
                candidates = [bone for bone in current.children if bone.use_deform and not bone.get(limb_ik.OWNER_KEY)
                              and any(word in bone.name.lower() for word in ('spine', 'chest', 'ribcage'))]
                if not candidates:
                    break
                if len(candidates) > 1:
                    ambiguous = True
                    break
                current = candidates[0]
                chain.append(current.name)
            if ambiguous:
                put('TORSO', 'NEEDS_MAPPING', 'More than one spine chain; choose the ordered chain explicitly.')
            elif len(chain) not in {3, 4}:
                put('TORSO', 'SKIP', 'Spine Controls needs three or four native spine segments.')
            else:
                try:
                    sources, _hips = torso_controls._resolve_chain(context, rig, chain, hips)
                    protected = _native_inputs(rig, sources)
                    put('TORSO', 'SKIP' if protected else 'ADD', protected or 'Add shared Bend and section controls.',
                        chain=sources, hips_name=hips)
                    if protected:
                        sources = None
                except _ERRORS as exc:
                    put('TORSO', 'BLOCKED', str(exc))

    if not existing('SPINE', spine_ik_fk, inventory):
        if not sources:
            put('SPINE', 'SKIP', 'A supported Spine Controls chain is needed.')
        else:
            names = set(sources) | (set(torso['bones'].values()) if isinstance(torso, dict) else set())
            try:
                spine_ik_fk._guard_animation(context, rig, names)
                if any((rig.data.bones[a].tail_local-rig.data.bones[b].head_local).length > 1e-5
                       for a, b in zip(sources, sources[1:])):
                    put('SPINE', 'SKIP', 'Spine segments do not meet; existing rest positions are preserved.')
                else:
                    put('SPINE', 'ADD', 'Add an IK branch in the current FK pose.')
            except _ERRORS as exc:
                put('SPINE', 'SKIP', str(exc))

    if not existing('EYES', eye_controls, inventory):
        if head_problem:
            put('EYES', *head_problem)
        else:
            eyes = {}
            for side, word in (('L', 'left'), ('R', 'right')):
                candidates = _named(rig, {f'eye.{side.lower()}', f'eye_{side.lower()}', f'{side.lower()}_eye', word+'_eye', word+'eye'})
                eyes[side] = candidates
            if any(len(values) > 1 for values in eyes.values()):
                put('EYES', 'NEEDS_MAPPING', 'More than one native eye candidate; choose the eye pair explicitly.')
            elif not all(eyes.values()):
                put('EYES', 'SKIP', 'Two native eye bones were not found.')
            elif not controls_available:
                put('EYES', 'SKIP', 'Eye Controls needs character limb controls.')
            else:
                try:
                    resolved = eye_controls.resolve_eyes(context, rig, head, eyes['L'][0], eyes['R'][0])
                    protected = _native_inputs(rig, resolved[1:])
                    put('EYES', 'SKIP' if protected else 'ADD', protected or 'Add shared gaze and two eye targets.',
                        head_name=resolved[0], left_name=resolved[1], right_name=resolved[2])
                except _ERRORS as exc:
                    put('EYES', 'BLOCKED', str(exc))

    rings = existing('FK_RINGS', limb_fk_visuals, inventory)
    if not rings:
        eligible = [name for chain in available.values() for name in chain[:2] if not _display_inputs(rig, [name])]
        put('FK_RINGS', 'ADD' if eligible else 'SKIP', 'Fit missing native FK rings; keep artist shapes.' if eligible else 'No unshaped native limb sections.')
    elif isinstance(rings, dict) and pending:
        put('FK_RINGS', 'ADD', 'Add rings for new limbs; keep existing rings and artist shapes.')

    if not existing('HEAD_NECK', head_neck_visuals):
        if head_problem:
            put('HEAD_NECK', *head_problem)
        else:
            try:
                resolved = head_neck_visuals.resolve_bones(context, rig, head_name=head, allow_partial=True)
                protected = _display_inputs(rig, [name for name in resolved if name])
                put('HEAD_NECK', 'SKIP' if protected else 'ADD', protected or ('Fit Head and Neck displays.' if resolved[1] else 'Fit Head; no recognized Neck was found.'),
                    head_name=resolved[0], neck_name=resolved[1], allow_partial=True)
            except _ERRORS as exc:
                put('HEAD_NECK', 'BLOCKED', str(exc))

    if not existing('BODY_DETAIL', body_detail_visuals):
        if hips_problem:
            put('BODY_DETAIL', *hips_problem)
        else:
            try:
                names = body_detail_visuals.resolve_bones(context, rig, hips_name=hips, allow_partial=True)
                protected = _display_inputs(rig, names.values())
                put('BODY_DETAIL', 'SKIP' if protected else 'ADD', protected or 'Fit Hips and any available native breast controls.',
                    hips_name=names['HIPS'], left_name=names.get('BREAST_L'), right_name=names.get('BREAST_R'), allow_partial=True)
            except _ERRORS as exc:
                put('BODY_DETAIL', 'NEEDS_MAPPING', str(exc))
    return finish()
