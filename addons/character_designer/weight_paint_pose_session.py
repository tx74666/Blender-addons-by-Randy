"""Bounded checkpoints for an explicit, non-keyed weight-paint pose preview.

This module never installs switching, changes constraints, or reconstructs an
Original session. Its witnesses deliberately omit painted weight values.
"""

import math
import json

RAW_CHANNELS = ('rotation_mode', 'location', 'rotation_euler',
                'rotation_quaternion', 'rotation_axis_angle', 'scale')
_DRIVEN_TYPES = frozenset({'IK', 'COPY_ROTATION', 'COPY_TRANSFORMS',
                          'DAMPED_TRACK', 'LOCKED_TRACK', 'TRACK_TO', 'TRANSFORM'})


def identity(value):
    if value is None:
        return None
    return (type(value).__name__, value.as_pointer())


def freeze(value):
    if value is None or type(value) in (bool, int, float, str):
        return value
    if hasattr(value, 'as_pointer'):
        return identity(value)
    if hasattr(value, 'items'):
        return tuple((str(k), freeze(v)) for k, v in value.items())
    try:
        return tuple(freeze(v) for v in value)
    except TypeError as exc:
        raise ValueError('Unsupported author data in painting checkpoint.') from exc


def _properties(owner, excluded=()):
    excluded = set(excluded)
    try:
        keys = owner.keys()
    except TypeError as error:
        # Blender exposes keys() on every bpy_struct, including constraints
        # and modifiers which explicitly do not support custom properties.
        if "doesn't support IDProperties" not in str(error):
            raise
        return ()
    return tuple((key, freeze(owner[key])) for key in keys if key not in excluded)


def _rna(owner, excluded=()):
    excluded = set(excluded) | {'rna_type'}
    result = []
    for prop in owner.bl_rna.properties:
        name = prop.identifier
        if name in excluded or prop.type == 'COLLECTION':
            continue
        # Read-only evaluated RNA (is_valid, matrix, error_location...) is not
        # an authored definition and may change when another frame evaluates.
        if prop.is_readonly and prop.type != 'POINTER':
            continue
        result.append((name, freeze(getattr(owner, name))))
    return tuple(result)


def _curves(action):
    seen, curves = set(), []
    for curve in getattr(action, 'fcurves', ()):
        if curve.as_pointer() not in seen:
            seen.add(curve.as_pointer())
            curves.append(curve)
    for layer in getattr(action, 'layers', ()):
        for strip in layer.strips:
            for bag in getattr(strip, 'channelbags', ()):
                for curve in bag.fcurves:
                    if curve.as_pointer() not in seen:
                        seen.add(curve.as_pointer())
                        curves.append(curve)
    return tuple(curves)


def _curve(curve):
    driver = getattr(curve, 'driver', None)
    return (identity(curve), _rna(curve, {'driver'}),
            tuple(_rna(p) for p in curve.keyframe_points),
            tuple(_rna(p) for p in curve.sampled_points),
            tuple((identity(m), _rna(m)) for m in curve.modifiers),
            None if driver is None else (
                _rna(driver), tuple((_rna(v), tuple(_rna(t) for t in v.targets))
                                    for v in driver.variables)))


def _action(action):
    if action is None:
        return None
    return (identity(action), _properties(action),
            tuple((identity(slot), _rna(slot)) for slot in getattr(action, 'slots', ())),
            tuple((identity(layer), _rna(layer),
                   tuple((identity(strip), _rna(strip),
                          tuple((identity(bag), getattr(bag, 'slot_handle', None))
                                for bag in getattr(strip, 'channelbags', ())))
                         for strip in layer.strips)) for layer in getattr(action, 'layers', ())),
            tuple(_curve(curve) for curve in _curves(action)))


def _strip(strip, actions):
    action = getattr(strip, 'action', None)
    if action is not None:
        actions[action.as_pointer()] = action
    curves = tuple(getattr(strip, 'fcurves', ()))
    dynamic = set()
    for field, flag in (('strip_time', 'use_animated_time'), ('influence', 'use_animated_influence')):
        if getattr(strip, flag, False):
            path = strip.path_from_id(field)
            if any(not curve.mute and curve.data_path in {field, path} for curve in curves):
                dynamic.add(field)
    return (identity(strip), _rna(strip, dynamic), tuple(_strip(s, actions)
                                               for s in getattr(strip, 'strips', ())),
            tuple(_curve(c) for c in curves),
            tuple((identity(m), _rna(m)) for m in getattr(strip, 'modifiers', ())))


def _animation(owner):
    ad = getattr(owner, 'animation_data', None)
    if ad is None:
        return None
    actions = {}
    if ad.action is not None:
        actions[ad.action.as_pointer()] = ad.action
    tracks = tuple((identity(t), _rna(t), tuple(_strip(s, actions) for s in t.strips))
                   for t in ad.nla_tracks)
    return (identity(ad), _rna(ad), tracks,
            tuple(_curve(c) for c in ad.drivers),
            tuple(_action(a) for a in actions.values()))


def animated_paths(owner):
    ad = getattr(owner, 'animation_data', None)
    if ad is None:
        return set()
    actions = {}
    if ad.action is not None:
        actions[ad.action.as_pointer()] = ad.action
    for track in ad.nla_tracks:
        for strip in track.strips:
            _strip(strip, actions)
    return {c.data_path for c in ad.drivers} | {
        c.data_path for action in actions.values() for c in _curves(action)}


def _numeric(value):
    if type(value) is bool:
        return value
    if type(value) in (float, int):
        if not math.isfinite(value):
            raise ValueError('The current pose contains a non-finite control value.')
        return value
    try:
        values = tuple(value)
    except TypeError:
        return None
    if values and all(type(v) in (bool, int, float) and math.isfinite(v) for v in values):
        return values
    return None


def property_names(rig):
    """Only existing numeric PoseBone properties addressed by animation/drivers.

    This includes existing ik_fk properties when their real switch drivers
    reference them. Unrelated metadata and numeric user data are witnesses,
    not values which a Finish operation may overwrite.
    """
    paths = animated_paths(rig)
    ad = getattr(rig, 'animation_data', None)
    for curve in ad.drivers if ad else ():
        for variable in curve.driver.variables:
            for target in variable.targets:
                if target.id == rig:
                    paths.add(target.data_path)
    result = {}
    for pb in rig.pose.bones:
        names = []
        prefix = pb.path_from_id()
        for key in pb.keys():
            value = _numeric(pb[key])
            base = prefix + '[' + json.dumps(key, ensure_ascii=False) + ']'
            addressed = base in paths
            if isinstance(value, tuple) and not addressed:
                for path in paths:
                    suffix = path[len(base):] if path.startswith(base) else ''
                    index = suffix[1:-1]
                    if (suffix.startswith('[') and suffix.endswith(']') and index
                            and all('0' <= digit <= '9' for digit in index)
                            and len(index) <= 10 and int(index) < len(value)):
                        addressed = True
                        break
            if value is not None and addressed:
                names.append(key)
        result[pb.name] = tuple(names)
    return result


def raw_pose(rig, properties):
    return tuple((pb.name, identity(pb),
                  tuple((name, freeze(getattr(pb, name))) for name in RAW_CHANNELS),
                  tuple((name, _numeric(pb[name])) for name in properties.get(pb.name, ())))
                 for pb in rig.pose.bones)


def numeric_kinds(rig, properties):
    return tuple((pb.name, tuple((key, type(pb[key]).__name__, getattr(pb[key], 'typecode', None))
                                for key in properties.get(pb.name, ()))) for pb in rig.pose.bones)


def _constraint(pb, constraint, animated):
    dynamic = {p.identifier for p in constraint.bl_rna.properties
               if p.identifier != 'rna_type' and not p.is_readonly
               and constraint.path_from_id(p.identifier) in animated}
    return (identity(constraint), _rna(constraint, dynamic), _properties(constraint))


def author_witness(mesh, rig, properties):
    animated = animated_paths(rig)
    keys = getattr(mesh.data, 'shape_keys', None)
    owners = (rig, rig.data, mesh, mesh.data, keys)
    return (
        tuple((identity(owner), _animation(owner)) for owner in owners if owner is not None),
        (identity(rig.data), identity(rig.parent), rig.parent_type, rig.parent_bone,
         tuple((identity(b), b.name, identity(b.parent), freeze(b.matrix_local),
                b.use_connect, b.use_deform, b.inherit_scale, b.use_inherit_rotation,
                b.use_local_location, _properties(b)) for b in rig.data.bones)),
        tuple((identity(c), c.name, identity(c.parent), tuple(identity(b) for b in c.bones))
              for c in rig.data.collections_all),
        tuple((pb.name, identity(pb), freeze(pb.lock_location), freeze(pb.lock_rotation),
               pb.lock_rotation_w, pb.lock_rotations_4d, freeze(pb.lock_scale),
               _properties(pb, properties.get(pb.name, ())),
               tuple(_constraint(pb, con, animated) for con in pb.constraints))
              for pb in rig.pose.bones),
        (identity(mesh.data), len(mesh.data.vertices),
         tuple((tuple(e.vertices),) for e in mesh.data.edges),
         tuple(tuple(p.vertices) for p in mesh.data.polygons),
         tuple((identity(g), g.name, g.index) for g in mesh.vertex_groups),
         tuple((identity(m), _rna(m), _properties(m)) for m in mesh.modifiers),
         None if keys is None else tuple((identity(k), k.name, identity(k.relative_key),
                                         k.vertex_group, len(k.data)) for k in keys.key_blocks)),
        _properties(rig), _properties(rig.data), _properties(mesh))


def _action_output_curves(action, slot):
    """Read only the assigned slot, never another slot's destinations."""
    if action is None:
        return ()
    if not tuple(getattr(action, 'slots', ())):
        return tuple(c for c in getattr(action, 'fcurves', ()) if not c.mute)
    if slot is None:
        return ()
    handle = slot.handle
    return tuple(c for layer in action.layers if not getattr(layer, 'mute', False)
                 for strip in layer.strips if not getattr(strip, 'mute', False)
                 for bag in getattr(strip, 'channelbags', ()) if bag.slot_handle == handle
                 for c in bag.fcurves if not c.mute)


def _output_curves(owner):
    """Numeric destinations only; variable targets remain exact author inputs."""
    ad = getattr(owner, 'animation_data', None)
    if ad is None:
        return ()
    curves = [c for c in ad.drivers if not c.mute]
    curves.extend(_action_output_curves(ad.action, getattr(ad, 'action_slot', None)))
    def visit(strip):
        if getattr(strip, 'mute', False):
            return
        curves.extend(_action_output_curves(getattr(strip, 'action', None),
                                            getattr(strip, 'action_slot', None)))
        for child in getattr(strip, 'strips', ()):
            visit(child)
    if getattr(ad, 'use_nla', True):
        for track in ad.nla_tracks:
            if not track.mute:
                for strip in track.strips:
                    visit(strip)
    return tuple(curves)


def _rig_output_components(rig):
    """Map exact rig custom-property output paths to their addressed components."""
    curves = _output_curves(rig)
    result = {}
    for key in rig.keys():
        base = '[' + json.dumps(key, ensure_ascii=False) + ']'
        matching = [c for c in curves if c.data_path == base or c.data_path.startswith(base + '[')]
        if not matching:
            continue
        value = _numeric(rig[key])
        if value is None:
            continue
        indices = set()
        for curve in matching:
            if curve.data_path == base:
                index = int(curve.array_index)
            else:
                suffix = curve.data_path[len(base):]
                digits = suffix[1:-1]
                if (not suffix.startswith('[') or not suffix.endswith(']') or not digits
                        or len(digits) > 10 or not all('0' <= digit <= '9' for digit in digits)):
                    continue
                index = int(digits)
            if (isinstance(value, tuple) and 0 <= index < len(value)) or (not isinstance(value, tuple) and index == 0):
                indices.add(index)
        if indices:
            result[key] = tuple(sorted(indices))
    return result


def _rig_output_kinds(rig):
    return tuple((key, type(rig[key]).__name__, getattr(rig[key], 'typecode', None), indices)
                 for key, indices in _rig_output_components(rig).items())


def _same_author_value(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, tuple):
        return len(left) == len(right) and all(_same_author_value(a, b) for a, b in zip(left, right))
    return left == right


def _author_matches(rig, saved, current):
    """Keep the old witness schema; normalize only witnessed evaluated outputs.

    Legacy scalar snapshots retain full type proof. Old array snapshots lack
    storage-kind evidence, so a changed array remains a strict refusal.
    """
    original = saved['witness']
    outputs = _rig_output_components(rig)
    if _same_author_value(original, current):
        return True
    if not isinstance(original, tuple) or len(original) != 8 or len(current) != 8:
        return False
    kinds = {row[0]: row[1:] for row in saved.get('rig_output_kinds', ())}
    before = dict(original[5])
    adjusted = []
    for key, value in current[5]:
        baseline = before.get(key)
        if key in outputs and baseline is not None:
            numeric = _numeric(value)
            prior = _numeric(baseline)
            if numeric is not None and prior is not None and type(numeric) is type(prior):
                if isinstance(prior, tuple):
                    if (key in kinds and len(numeric) == len(prior)
                            and all(type(a) is type(b) for a, b in zip(numeric, prior))):
                        selected = set(outputs[key])
                        value = tuple(old if index in selected else now
                                      for index, (now, old) in enumerate(zip(numeric, prior)))
                else:
                    value = baseline
        adjusted.append((key, value))
    normalized = current[:5] + (tuple(adjusted),) + current[6:]
    return _same_author_value(original, normalized)


def _check_evaluated_outputs(rig, saved):
    if not isinstance(saved['witness'], tuple) or len(saved['witness']) != 8:
        return
    baseline = dict(saved['witness'][5])
    for key, indices in _rig_output_components(rig).items():
        if key not in baseline:
            continue
        value, original = _numeric(rig[key]), _numeric(baseline[key])
        pairs = ((value[index], original[index]) for index in indices) if isinstance(original, tuple) else ((value, original),)
        if not all(_same_author_value(a, b) for a, b in pairs):
            raise RuntimeError('Blender did not reevaluate the captured rig output at its saved pose and frame; recovery is retained.')


def capture(context, mesh, rig):
    properties = property_names(rig)
    return dict(frame=(context.scene.frame_current, context.scene.frame_subframe),
                properties=properties, pose=raw_pose(rig, properties),
                property_kinds=numeric_kinds(rig, properties),
                rig_output_kinds=_rig_output_kinds(rig),
                witness=author_witness(mesh, rig, properties))


def check(mesh, rig, saved):
    for name, pointer, _channels, values in saved.get('pose', ()):
        pb = rig.pose.bones.get(name)
        if pb is None or identity(pb) != pointer:
            raise ValueError('A captured Pose Bone changed; current author data was kept.')
        for key, original in values:
            current = _numeric(pb[key]) if key in pb else None
            if (current is None or type(current) is not type(original)
                    or (isinstance(original, tuple) and (
                        len(current) != len(original)
                        or tuple(type(v) for v in current) != tuple(type(v) for v in original)))):
                raise ValueError('A captured numeric pose property disappeared or changed type; '
                                 'current author data was kept.')
    if ('property_kinds' in saved
            and numeric_kinds(rig, saved['properties']) != saved['property_kinds']):
        raise ValueError('A captured numeric pose property changed storage type; current author data was kept.')
    if ('rig_output_kinds' in saved and _rig_output_kinds(rig) != saved['rig_output_kinds']):
        raise ValueError('A captured rig output changed storage type or destination; current author data was kept.')
    if not _author_matches(rig, saved, author_witness(mesh, rig, saved['properties'])):
        raise ValueError('Painting recovery stopped: Actions, slots, drivers, constraints or structure changed. '
                         'The current author data and painted weights were kept.')


def restore(context, mesh, rig, saved):
    """Restore captured existing raw channels, never matrices or keyed data."""
    check(mesh, rig, saved)
    frame, subframe = saved['frame']
    context.scene.frame_set(frame, subframe=subframe)
    check(mesh, rig, saved)
    for name, pointer, channels, values in saved['pose']:
        pb = rig.pose.bones.get(name)
        if pb is None or identity(pb) != pointer:
            raise ValueError('A captured Pose Bone changed before painting recovery.')
        for key, value in values:
            if key not in pb or _numeric(pb[key]) is None:
                raise ValueError('A captured numeric pose property changed type or disappeared.')
            if _numeric(pb[key]) != value:
                current = pb[key]
                if isinstance(value, tuple) and hasattr(current, 'typecode'):
                    # Keep the original IDPropertyArray and its f/d/i storage,
                    # UI metadata and owner instead of defining a new array.
                    for index, item in enumerate(value):
                        if current[index] != item:
                            current[index] = item
                else:
                    pb[key] = value
        for attr, value in channels:
            if freeze(getattr(pb, attr)) != value:
                setattr(pb, attr, value)
    rig.update_tag(refresh={'OBJECT'})
    context.view_layer.update()
    check(mesh, rig, saved)
    _check_evaluated_outputs(rig, saved)
    if raw_pose(rig, saved['properties']) != saved['pose']:
        raise RuntimeError('Blender did not restore the captured raw pose exactly; recovery is retained.')


def _influence(constraint):
    return not constraint.mute and float(constraint.influence) > 1e-6


def _chain(pb, count):
    result = []
    while pb is not None and (count == 0 or len(result) < count):
        result.append(pb.name)
        pb = pb.parent
    return tuple(result)


def preview_targets(rig, deform_names):
    """Resolve existing local native controls; no name-prefix guesses or switch.

    Managed rigs use their actual registered public target/pole and ownership
    fields. Native IK may use a real same-rig target/pole directly. External or
    unrecognized driving relations remain unavailable instead of being muted.
    """
    from . import limb_ik as api
    deform = set(deform_names)
    controls, driven, unresolved = set(), set(), set()
    for pb in rig.pose.bones:
        try:
            registry = api._constraint_registry(pb, strict=True)
        except (AttributeError, ValueError, TypeError, RuntimeError):
            registry = {}
        for con in pb.constraints:
            if con.type not in _DRIVEN_TYPES or not _influence(con):
                continue
            entry = registry.get(con.name)
            if isinstance(entry, dict) and entry.get('owner') == api.OWNER_VALUE:
                chain = tuple(entry.get('chain', ()))
                if not deform.intersection(chain):
                    if pb.name in deform:
                        # A registered Root/Master relation is still a driven
                        # source. It is not made editable by missing limb fields.
                        driven.add(pb.name)
                        target_name = entry.get('target')
                        target_bone = (rig.data.bones.get(target_name)
                                       if isinstance(target_name, str) else None)
                        armature_id = rig.data.get(api.ARMATURE_ID_KEY)
                        if (entry.get('role') == 'MASTER_FOLLOW'
                                and entry.get('root') == pb.name
                                and entry.get('armature_id') == armature_id and armature_id
                                and con.type == 'COPY_TRANSFORMS' and con.target == rig
                                and con.subtarget == target_name and target_bone is not None
                                and api._owned(target_bone, armature_id, role='MASTER')):
                            controls.add(target_name)
                        else:
                            unresolved.add(pb.name)
                    continue
                kind, side, rid = entry.get('kind'), entry.get('side'), entry.get('rig_id')
                target, pole = entry.get('target'), entry.get('pole')
                armature_id = rig.data.get(api.ARMATURE_ID_KEY)
                valid = (kind in {'ARM', 'LEG'} and side in {'L', 'R'} and len(chain) == 3
                         and entry.get('armature_id') == armature_id and armature_id
                         and con.target == rig and con.subtarget in {
                             target, entry.get('solver_target'), entry.get('mch_upper'),
                             entry.get('mch_lower')})
                candidates = ((target, api.LIMB_SPEC[kind]['target_role']), (pole, 'POLE')) if valid else ()
                for name, role in candidates:
                    b = rig.data.bones.get(name) if isinstance(name, str) else None
                    bound_chain = b.get(api.CHAIN_KEY) if b is not None else None
                    if isinstance(bound_chain, str):
                        try:
                            bound_chain = json.loads(bound_chain)
                        except (ValueError, TypeError):
                            bound_chain = None
                    if (b is None or not api._owned(b, armature_id, role=role, rig_id=rid)
                            or b.get(api.KIND_KEY) != kind or b.get(api.SIDE_KEY) != side
                            or not isinstance(bound_chain, (list, tuple)) or tuple(bound_chain) != chain):
                        valid = False
                        break
                target_pose = rig.pose.bones.get(target) if isinstance(target, str) else None
                if valid and target_pose is not None:
                    value = float(target_pose.get('ik_fk', 1.0))
                    if value <= 1e-6:
                        continue
                    driven.update(chain)
                    controls.update((target, pole))
                else:
                    driven.update(chain)
                    unresolved.update(deform.intersection(chain))
                continue
            if con.type == 'IK':
                chain = set(_chain(pb, int(con.chain_count)))
                if not deform.intersection(chain):
                    continue
                driven.update(chain)
                target = rig.pose.bones.get(con.subtarget) if con.target == rig else None
                pole = (rig.pose.bones.get(con.pole_subtarget)
                        if con.pole_target == rig and con.pole_subtarget else None)
                if target is not None and target.name not in chain and (
                        con.pole_target is None or pole is not None):
                    controls.add(target.name)
                    if pole is not None:
                        controls.add(pole.name)
                else:
                    unresolved.update(deform.intersection(chain))
            elif pb.name in deform:
                driven.add(pb.name)
                unresolved.add(pb.name)
    sources = deform - driven
    names = tuple(pb.name for pb in rig.pose.bones if pb.name in sources | controls)
    if not names:
        raise ValueError('These driven bones have no verified local pose controls. '
                         'Their constraints and locks were kept unchanged.')
    return dict(names=names, controls=tuple(sorted(controls)),
                sources=tuple(sorted(sources)), unresolved=tuple(sorted(unresolved)))
