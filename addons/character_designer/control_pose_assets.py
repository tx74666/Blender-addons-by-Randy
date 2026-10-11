"""Apply native-bone pose assets through the existing generated control graph.

The generation-time native skeleton is the legacy compatibility baseline. Old
Action-only assets cannot prove their historical authoring Rest; the baseline
detects subsequent structural edits without modifying any Pose asset.
"""
import ast
import json
import math
import re
from contextlib import contextmanager

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector
from . import limb_ik, limb_ik_fk as match

BASELINE = 'character_designer_pose_rest_v1'
ASSET_METADATA = 'character_designer_pose_asset_v1'
_PATH = re.compile(r'^pose\.bones\[("(?:\\.|[^"\\])*")\]\.([a-z_]+)$')
_BONE_PATH = re.compile(r'^pose\.bones\[("(?:\\.|[^"\\])*")\]')
_TRANSFORMS = {'location': 3, 'rotation_euler': 3, 'rotation_quaternion': 4, 'rotation_axis_angle': 4, 'scale': 3}
_BBONE = {'bbone_curveinx': 1, 'bbone_curveoutx': 1, 'bbone_curveinz': 1, 'bbone_curveoutz': 1,
          'bbone_rollin': 1, 'bbone_rollout': 1, 'bbone_scalein': 3, 'bbone_scaleout': 3,
          'bbone_easein': 1, 'bbone_easeout': 1}
_FIELDS = _TRANSFORMS | _BBONE
_KEYMAPS = []
_REPLACED = []


def native_rest(rig):
    from . import skirt_rig
    native = {b.name for b in rig.data.bones
              if b.get(limb_ik.OWNER_KEY) not in limb_ik.GENERATED_CONTROL_OWNERS
              and not b.get(skirt_rig.OWNER_KEY)}
    return {b.name: {'matrix': [list(row) for row in b.matrix_local],
                     'length': b.length, 'parent': b.parent.name if b.parent and b.parent.name in native else None,
                     'connected': b.use_connect, 'inherit_scale': b.inherit_scale,
                     'inherit_rotation': b.use_inherit_rotation, 'local_location': b.use_local_location}
            for b in rig.data.bones if b.name in native}


def capture_baseline(rig):
    """Never silently bless later Rest edits during Update or a rebuild."""
    if BASELINE not in rig:
        rig[BASELINE] = json.dumps({'version': 1, 'object': rig.name, 'rest': native_rest(rig)})


def asset_metadata(action):
    """Only our versioned native assets opt into mode-independent matching."""
    if ASSET_METADATA not in action:
        return None
    try:
        saved = json.loads(action[ASSET_METADATA])
        if (not isinstance(saved, dict) or type(saved.get('version')) is not int
                or saved['version'] != 1 or not isinstance(saved.get('rest'), dict)
                or not saved['rest'] or not isinstance(saved.get('source_object'), str)
                or not saved['source_object'] or not isinstance(saved.get('source_slot'), str)
                or not saved['source_slot'] or not isinstance(saved.get('names'), list)
                or not saved['names'] or any(not isinstance(n, str) or not n for n in saved['names'])
                or len(saved['names']) != len(set(saved['names']))
                or ('stable_ik_bend' in saved and type(saved['stable_ik_bend']) is not bool)):
            raise ValueError
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('This saved Pose has invalid compatibility data; no changes were applied.') from exc
    _validate_stable_ik_seed_metadata(saved)
    return saved


def _compatible(rig, names, metadata=None):
    if metadata is None and BASELINE not in rig:
        raise ValueError('Run Update once to record this rig\'s native Pose compatibility baseline.')
    saved = metadata if metadata is not None else json.loads(rig[BASELINE])
    current = native_rest(rig)
    if set(current) != set(saved['rest']):
        raise ValueError('Native skeleton structure differs from this Pose\'s baseline; this Pose was not applied.')
    if metadata is not None:
        # Reuse the complete Rest schema proof; generated controls may have been
        # rebuilt or renamed, so only the authoring native skeleton is compared.
        from . import body_original_mode as original
        original._validate_session_rest(rig, {'version': 1, 'bones': sorted(rig.data.bones.keys()),
                                              'rest': saved['rest']}, current=current)
    # Check the whole source skeleton: a changed ancestor also changes a local pose.
    for name, old in saved['rest'].items():
        now = current[name]
        if (any(now[k] != old[k] for k in ('parent', 'connected', 'inherit_scale', 'inherit_rotation', 'local_location'))
                or abs(now['length'] - old['length']) > 1e-6
                or max(abs(a-b) for x, y in zip(now['matrix'], old['matrix']) for a, b in zip(x, y)) > 2e-6):
            raise ValueError(f'Native Rest or structure changed at {name}; this Pose was not applied.')
    missing = set(names) - set(current)
    if missing:
        raise ValueError('Pose uses missing or non-native bones: ' + ', '.join(sorted(missing)[:4]))
    return saved


def _curves(action, rig):
    from .animation_retarget import _curves as curves
    slots = [s for s in action.slots if s.target_id_type == 'OBJECT']
    metadata = asset_metadata(action)
    origin = json.loads(rig.get(BASELINE, '{}')).get('object', rig.name)
    identifiers = {metadata['source_slot']} if metadata else {'OB' + rig.name, 'OB' + origin}
    candidates = [s for s in slots if s.identifier in identifiers]
    if slots and len(candidates) != 1:
        raise ValueError('This Pose was saved for another or an ambiguous rig; its source skeleton cannot be verified.')
    return curves(action, candidates[0] if candidates else None)


def channels(action, rig):
    values = {}
    frames = set()
    for curve in _curves(action, rig):
        if curve.mute:
            continue
        found = _PATH.fullmatch(curve.data_path)
        if not found or found[2] not in _FIELDS:
            raise ValueError('This Pose contains unsupported non-transform channels; no changes were applied.')
        name, prop = ast.literal_eval(found[1]), found[2]
        size = _FIELDS[prop]
        if not 0 <= curve.array_index < size or curve.modifiers:
            raise ValueError('This Pose contains unsupported transform curves.')
        frames.update(round(p.co.x, 6) for p in curve.keyframe_points)
        key = (name, prop, curve.array_index)
        if key in values:
            raise ValueError('This Pose has duplicate transform channels.')
        values[key] = curve
    if not values or len(frames) != 1:
        raise ValueError('Choose a single-frame Pose asset, not an animation Action.')
    frame = next(iter(frames))
    result = {}
    for (name, prop, index), curve in values.items():
        value = float(curve.evaluate(frame))
        if not math.isfinite(value):
            raise ValueError('This Pose contains non-finite transforms.')
        result.setdefault(name, {}).setdefault(prop, {})[index] = value
    metadata = asset_metadata(action)
    if metadata is not None and set(result) != set(metadata['names']):
        raise ValueError('This saved Pose\'s bone channels differ from its recorded region; no changes were applied.')
    return result


def _convert(bone, matrix, parent_matrix, *, invert=False):
    kwargs = {'parent_matrix': parent_matrix, 'parent_matrix_local': bone.parent.matrix_local} if bone.parent else {}
    return bone.convert_local_to_pose(matrix, bone.matrix_local, invert=invert, **kwargs)


def desired_pose(rig, values, metadata=None):
    """Apply only keyed channels to evaluated native FK space, then propagate."""
    _compatible(rig, values, metadata)
    current = {p.name: p.matrix.copy() for p in rig.pose.bones}
    native = native_rest(rig)
    result = {}
    for name in sorted(native, key=lambda n: len(rig.pose.bones[n].parent_recursive)):
        pb, bone = rig.pose.bones[name], rig.data.bones[name]
        parent = bone.parent
        parent_now = current[parent.name] if parent else Matrix.Identity(4)
        parent_wanted = result.get(parent.name, parent_now) if parent else parent_now
        if name not in values and parent_wanted == parent_now:
            result[name] = current[name]
            continue
        basis = _convert(bone, current[name], parent_now, invert=True)
        if name in values:
            loc, rot, scale = basis.decompose()
            mode = pb.rotation_mode if pb.rotation_mode not in {'QUATERNION', 'AXIS_ANGLE'} else 'XYZ'
            axis, angle = rot.to_axis_angle()
            fields = {'location': list(loc), 'scale': list(scale), 'rotation_quaternion': list(rot),
                      'rotation_euler': list(rot.to_euler(mode)), 'rotation_axis_angle': [angle, *axis]}
            rotations = [key for key in values[name] if key.startswith('rotation_')]
            if len(rotations) > 1:
                raise ValueError(f'{name}: this Pose mixes rotation representations.')
            for prop, entries in values[name].items():
                if prop not in _TRANSFORMS:
                    continue
                for index, value in entries.items():
                    fields[prop][index] = value
            if rotations:
                prop = rotations[0]
                v = fields[prop]
                rot = (Quaternion(v) if prop == 'rotation_quaternion' else Euler(v, mode).to_quaternion()
                       if prop == 'rotation_euler' else Quaternion(Vector(v[1:]), v[0]))
                if rot.magnitude < 1e-8:
                    raise ValueError(f'{name}: this Pose has a zero rotation quaternion.')
            basis = Matrix.LocRotScale(Vector(fields['location']), rot.normalized(), Vector(fields['scale']))
        result[name] = _convert(bone, basis, parent_wanted)
    return result


def _difference(a, b):
    return max(abs(x-y) for ar, br in zip(a, b) for x, y in zip(ar, br))


@contextmanager
def _transaction(context, rig):
    from . import bone_collections, bone_display
    from . import body_original_mode as original
    session = rig.get(original.SESSION)
    before = {p.name: (p.rotation_mode, p.matrix_basis.copy(), p.get('ik_fk')) for p in rig.pose.bones}
    channels_before = {p.name: {key: list(getattr(p,key)) for key in _TRANSFORMS} for p in rig.pose.bones}
    mutes = [(con, con.mute) for pb in rig.pose.bones for con in pb.constraints]
    bends = {p.name: {k: list(getattr(p,k)) if size>1 else getattr(p,k) for k,size in _BBONE.items()}
             for p in rig.pose.bones}
    layout = bone_collections.snapshot_layout(rig)
    display = bone_display._snapshot(rig)
    had_animation = rig.animation_data is not None
    old_action = rig.animation_data.action if had_animation else None
    old_slot = rig.animation_data.action_slot if had_animation else None
    try:
        yield before
    except Exception:
        if session is not None:
            rig[original.SESSION] = session
        for con, mute in mutes:
            con.mute = mute
        if rig.animation_data:
            failed = rig.animation_data.action
            rig.animation_data.action = old_action
            if old_action and old_slot:
                rig.animation_data.action_slot = old_slot
            if failed and failed != old_action:
                failed.use_fake_user = False
                if failed.users == 0:
                    bpy.data.actions.remove(failed)
        if not had_animation and rig.animation_data and not rig.animation_data.drivers and not rig.animation_data.nla_tracks:
            rig.animation_data_clear()
        for name, (mode, basis, ik_fk) in before.items():
            pb = rig.pose.bones[name]
            pb.rotation_mode, pb.matrix_basis = mode, basis
            for key, value in channels_before[name].items():
                setattr(pb, key, value)
            if ik_fk is not None:
                pb['ik_fk'] = ik_fk
            for key, value in bends[name].items():
                setattr(pb, key, value)
        bone_collections.restore_layout(rig, layout)
        bone_display._restore(rig, display)
        bone_collections._FRAME_CACHE.clear()
        match._update(context, rig)
        raise


def _insert_key(pb, path, frame):
    if not pb.keyframe_insert(data_path=path, frame=frame, group=pb.name):
        raise ValueError(f'Could not key {pb.name}; Pose application was rolled back.')


def _auto_key(context, rig, before, values):
    if not context.scene.tool_settings.use_keyframe_insert_auto:
        return
    animation = rig.animation_data_create()
    previous, slot = animation.action, animation.action_slot
    staged = previous.copy() if previous else bpy.data.actions.new(rig.name + ' Poses')
    if ASSET_METADATA in staged:
        del staged[ASSET_METADATA]
    if staged.asset_data:
        staged.asset_clear()
    staged.use_fake_user = False
    animation.action = staged
    if slot:
        animation.action_slot = staged.slots[slot.identifier]
    frame = context.scene.frame_current + context.scene.frame_subframe
    for pb in rig.pose.bones:
        _mode, basis, ik_fk = before[pb.name]
        paths = set()
        if _difference(basis, pb.matrix_basis) > 1e-7:
            rotation = ('rotation_quaternion' if pb.rotation_mode == 'QUATERNION' else
                        'rotation_axis_angle' if pb.rotation_mode == 'AXIS_ANGLE' else 'rotation_euler')
            paths.update(('location', rotation, 'scale'))
        paths.update(p for p in values.get(pb.name, {}) if p in _BBONE)
        if ik_fk is not None and ik_fk != pb.get('ik_fk'):
            paths.add('["ik_fk"]')
        for path in paths:
            _insert_key(pb, path, frame)


def _complete_native_transforms(fields):
    rotations = [name for name in fields if name.startswith('rotation_')]
    return (set(fields.get('location', ())) == set(range(3))
            and set(fields.get('scale', ())) == set(range(3))
            and len(rotations) == 1
            and rotations[0] in _TRANSFORMS
            and set(fields[rotations[0]]) == set(range(_TRANSFORMS[rotations[0]])))


def _validate_stable_ik_seed_metadata(metadata):
    """Validate explicit, Rest-compatible reference seeds without writing RNA."""
    if 'stable_ik_seed' not in metadata:
        return {}
    seeds = metadata['stable_ik_seed']
    modes = {'QUATERNION', 'AXIS_ANGLE', 'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
    try:
        if not isinstance(seeds, dict) or not seeds or set(seeds) - {'ARM/L', 'ARM/R'}:
            raise ValueError
        for sources in seeds.values():
            if not isinstance(sources, dict) or len(sources) != 3:
                raise ValueError
            for name, saved in sources.items():
                if (not isinstance(name, str) or not name or not isinstance(saved, dict)
                        or set(saved) != {'rotation_mode', 'matrix_basis'} or saved['rotation_mode'] not in modes):
                    raise ValueError
                rows = saved['matrix_basis']
                if (not isinstance(rows, list) or len(rows) != 4
                        or any(not isinstance(row, list) or len(row) != 4 for row in rows)
                        or any(type(value) not in {int, float} or not math.isfinite(value) for row in rows for value in row)
                        or any(abs(rows[3][index] - expected) > 1e-8
                               for index, expected in enumerate((0., 0., 0., 1.)))):
                    raise ValueError
                basis = Matrix(rows)
                if not limb_ik._matrix_is_finite(basis):
                    raise ValueError
                rebuilt = Matrix.LocRotScale(*basis.decompose())
                determinant = basis.to_3x3().determinant()
                if (not limb_ik._matrix_is_finite(rebuilt)
                        or not math.isfinite(determinant) or determinant <= 1e-10
                        or _difference(basis, rebuilt) > 2e-6):
                    raise ValueError
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError('This saved Pose has invalid stable IK seed data; no changes were applied.') from exc
    return seeds


def _validate_stable_ik_seed_inventory(rig, inventory, seeds, *, applied=()):
    native = set(native_rest(rig))
    for name, sources in seeds.items():
        key = tuple(name.split('/'))
        if (set(sources) - native
                or any(limb_ik._side_from_name(source) not in {'', key[1]} for source in sources)):
            raise ValueError('This saved Pose has different stable IK seed bones; no changes were applied.')
        if key not in applied:
            continue
        entry = inventory['rigs'].get(key)
        if entry is None or set(sources) != set(entry['chain']):
            raise ValueError('This saved Pose has different stable IK seed bones; no changes were applied.')


def _stable_ik_bend_supported(rig, inventory, entry):
    """Prove the ordinary Direct source/control graph before keeping its seed."""
    if (inventory['schema'] not in {limb_ik.LEGACY_DIRECT_PREROLL_SCHEMA, limb_ik.DIRECT_PREROLL_SCHEMA}
            or not entry.get('direct_rest') or entry.get('foot_controls')
            or entry['solver_target'].name != entry['target'].name
            or rig.parent is not None or rig.constraints
            or (rig.data.animation_data and rig.data.animation_data.drivers)):
        return False
    chain = tuple(entry['chain'])
    if len(chain) != 3 or len(set(chain)) != 3:
        return False
    bones = [rig.pose.bones[name] for name in chain]
    if bones[1].parent != bones[0] or bones[2].parent != bones[1]:
        return False
    owned = {con.as_pointer(): (pb, con, record) for pb, con, record in entry['entries']}
    switch_paths = set()
    for pb in bones:
        for con in pb.constraints:
            record = owned.get(con.as_pointer())
            if (record is None or record[0] != pb or not match.is_switch_constraint(rig, *record)
                    or not match.validate_constraint_influence(rig, *record)):
                return False
            switch_paths.add(con.path_from_id('influence'))
    proof = match._fk_parent_dependencies(rig, bones[0].parent, set(chain))
    if proof is None:
        return False
    dependencies, root_scale = proof
    controls = [rig.pose.bones[entry[name].name] for name in ('target', 'pole')]
    if controls[0] == controls[1] or any(pb.constraints for pb in controls):
        return False
    from . import root_control
    root = root_control.get_record(rig)
    parents = {pb.parent.name: pb.parent for pb in controls if pb.parent}
    if parents and (root is None or set(parents) != {root['master']}):
        return False
    if parents:
        master = parents[root['master']]
        if master.parent is not None or master.constraints:
            return False
        root_control._validate_scale_drivers(rig, master)
        root_scale = master.path_from_id('scale')
    prefixes = tuple(pb.path_from_id() for pb in (*bones, *dependencies.values(), *controls, *parents.values()))
    for curve in rig.animation_data.drivers if rig.animation_data else ():
        path = curve.data_path
        if path in switch_paths or path == root_scale:
            continue
        if (any(path == prefix or path.startswith(prefix + '.') or path.startswith(prefix + '[')
                for prefix in prefixes)
                or path.startswith(('location', 'rotation_', 'scale', 'delta_', 'matrix_', 'constraints['))):
            return False
    return True


def _match_stable_ik_bend(context, rig, inventory, key, entry, wanted, desired, authored, seeds):
    """Apply an explicitly tagged flat preset through its current IK seed.

    A tiny FK elbow bend can imply an unwanted future IK plane. This opt-in
    preset semantic permits a bounded upper/lower adjustment while preserving
    the exact authored wrist; ordinary native Pose matching remains exact.
    """
    upper, lower, end = entry['chain']
    start, joint, finish = (wanted[name].translation for name in entry['chain'])
    axis = finish - start
    reach = axis.length
    if reach < 1e-8:
        return False
    axis.normalize()
    radial = joint - start - axis * (joint - start).dot(axis)
    if radial.length > reach * .02:
        return False
    if not _stable_ik_bend_supported(rig, inventory, entry):
        raise ValueError('This flat Pose needs supported Direct IK controls for its stable bend; no changes kept.')
    seed = seeds.get('/'.join(key))
    upper_pb = rig.pose.bones[upper]
    basis = Matrix(seed[upper]['matrix_basis']) if seed is not None else upper_pb.matrix_basis.copy()
    parent = upper_pb.parent.matrix if upper_pb.parent else Matrix.Identity(4)
    reference = _convert(upper_pb.bone, basis, parent).to_3x3()
    authored_frame = wanted[upper].to_3x3()
    reference_axis = reference.col[1].normalized()
    authored_axis = authored_frame.col[1].normalized()
    swing = reference_axis.rotation_difference(authored_axis)
    transported = limb_ik._project_perpendicular(swing @ reference.col[0], authored_axis)
    authored_roll = limb_ik._project_perpendicular(authored_frame.col[0], authored_axis)
    if (min(transported.length, authored_roll.length) < 1e-8
            or abs(limb_ik._signed_angle(transported, authored_roll, authored_axis)) > math.radians(3.)):
        raise ValueError('This flat Pose has incompatible axial twist for its stable IK seed; no changes kept.')
    plane = {name: matrix.copy() for name, matrix in wanted.items()}
    plane[lower].translation = start + axis * (joint - start).dot(axis)
    if seed is not None:
        # Only this asset's explicit reference is restored. Never invent a
        # neutral Identity seed for a calibrated or deliberately posed rig.
        for name in entry['chain']:
            pb = rig.pose.bones[name]
            pb.rotation_mode = seed[name]['rotation_mode']
            pb.matrix_basis = Matrix(seed[name]['matrix_basis'])
        match._update(context, rig)
    match._match_ik(context, rig, inventory, entry, plane)
    match._match_toe(context, rig, entry, wanted)
    match._update(context, rig)
    position, rotation, scale = match._pose_errors(rig, wanted)
    wrist = rig.pose.bones[end].matrix
    if (position > reach * .02 or rotation > math.radians(3.) or scale > match.SCALE_TOLERANCE
            or _difference(wrist, wanted[end]) > 4e-4
            or (wrist.translation - wanted[end].translation).length > 1e-5):
        raise ValueError('This flat Pose needs more than the bounded IK bend adjustment; no changes kept.')
    before = {name: matrix.copy() for name, matrix in desired.items()}
    adjusted = {upper, lower}
    for name in (upper, lower):
        wanted[name] = desired[name] = rig.pose.bones[name].matrix.copy()
    # Unkeyed native children retain their original parent-relative pose.
    # The authored wrist remains exact, so its fingers retain that exact frame.
    for name in sorted(desired, key=lambda n: len(rig.pose.bones[n].parent_recursive)):
        parent = rig.data.bones[name].parent
        if name in entry['chain'] or name in authored or parent is None or parent.name not in adjusted:
            continue
        bone = rig.data.bones[name]
        basis = _convert(bone, before[name], before[parent.name], invert=True)
        desired[name] = _convert(bone, basis, desired[parent.name])
        adjusted.add(name)
    match._verify(rig, wanted)
    return True


def _match(context, rig, desired, changed, *, preserve_modes=False, precise_limbs=(), sync_controls=False,
           stable_ik_bend_limbs=(), authored_names=(), stable_ik_seeds=None):
    from . import torso_controls, spine_ik_fk, eye_controls, bone_collections, root_control, body_original_mode
    inventory = limb_ik._validate_inventory(rig)
    stable_sources = {name for key in stable_ik_bend_limbs if key[0] == 'ARM' and key in inventory['rigs']
                      for name in match.pose_names(inventory['rigs'][key])}
    torso, spine, eyes = torso_controls.validate(rig), spine_ik_fk.validate(rig), eye_controls.validate(rig)
    def torso_match():
        old_value = rig.pose.bones[spine['chest']].get(spine_ik_fk.PROPERTY, 0.) if spine else 0.
        if spine:
            # Exact FK controls can represent every saved native spine pose.
            rig.pose.bones[spine['chest']][spine_ik_fk.PROPERTY] = 0.0
            match._update(context, rig)
        for source in torso['sources']:
            match._set_matrix(context, rig, rig.pose.bones[torso['controls'][source]], desired[source])
        if spine:
            dormant = {name: rig.pose.bones[name].matrix_basis.copy() for name in spine['bones'].values()}
            if preserve_modes and old_value <= 1e-6:
                match._verify(rig, {n: desired[n] for n in spine['sources']})
                modes['SPINE'] = 'FK'
                return
            try:
                spine_ik_fk._seed_ik(context, rig, spine, desired)
                rig.pose.bones[spine['chest']][spine_ik_fk.PROPERTY] = old_value
                match._update(context, rig)
                match._verify(rig, {n: desired[n] for n in spine['sources']})
                if any(_difference(rig.pose.bones[n].matrix, desired[n]) > 1e-5 for n in spine['sources']):
                    raise limb_ik.LimbIKError('The saved spine pose needs exact FK matching.')
            except limb_ik.LimbIKError:
                if preserve_modes:
                    raise ValueError('This spine pose cannot match the current IK mode without a jump; use FK for this pose or undo the last pose edit.')
                for name, basis in dormant.items():
                    rig.pose.bones[name].matrix_basis = basis
                spine_ik_fk._match_fk(context, rig, spine, desired)
            modes['SPINE'] = spine_ik_fk.mode_for_rig(rig)
    def limb_match(key, entry):
        names = match.pose_names(entry)
        wanted = {name: desired[name] for name in names}
        previous = match.mode_for_rig(rig, entry)
        previous_value = rig.pose.bones[entry['target'].name].get(match.PROPERTY, 1.0)
        if match.VERSION_KEY not in rig.pose.bones[entry['target'].name].bone:
            raise ValueError('Update this legacy rig before applying Pose assets through its controls.')
        if (key in stable_ik_bend_limbs and key[0] == 'ARM' and previous == 'IK'
                and key not in precise_limbs and not body_original_mode.active(rig)
                and _match_stable_ik_bend(context, rig, inventory, key, entry, wanted, desired,
                                        set(authored_names), stable_ik_seeds or {})):
            modes['/'.join(key)] = 'IK'
            return
        match.switch_limb(context, rig, key, 'FK', keyframe=False, desired_pose=wanted)
        if previous == 'FK' and sync_controls:
            # Seed the dormant target and Pole through the same matching path.
            # This enables IK briefly; FK must take over again before verification.
            match._match_ik(context, rig, inventory, entry, wanted,
                            precise=key in precise_limbs)
            match._match_fk(context, rig, entry, wanted)
            match._match_toe(context, rig, entry, wanted)
            match._verify(rig, wanted)
        if previous != 'FK':
            try:
                match.switch_limb(context, rig, key, 'IK', keyframe=False, desired_pose=wanted,
                                  precise=key in precise_limbs)
                if previous == 'BLEND':
                    rig.pose.bones[entry['target'].name][match.PROPERTY] = previous_value
                    match._update(context, rig)
                    match._verify(rig, wanted)
                if any(_difference(rig.pose.bones[n].matrix, wanted[n]) > 3e-4 for n in wanted):
                    raise limb_ik.LimbIKError('The saved limb pose needs exact FK matching.')
            except limb_ik.LimbIKError:
                if preserve_modes:
                    raise ValueError('This limb pose cannot match the current IK mode without a jump; use FK for this pose or undo the last pose edit.')
                # Some authored FK twist/stretch cannot be expressed by this IK
                # solver. Keep an exact FK match and its visible FK controls.
                # Position dormant IK controls too; no user mode switch needed.
                match._match_ik(context, rig, inventory, entry, wanted)
                match._match_fk(context, rig, entry, wanted)
                match._match_toe(context, rig, entry, wanted)
                match._verify(rig, wanted)
        modes['/'.join(key)] = match.mode_for_rig(rig, entry)
    modes, handled = {}, set()
    root_follow = {pb.name: con for pb, con, record in limb_ik._owned_constraint_records(rig)
                   if record.get('role') == 'MASTER_FOLLOW'}
    root = root_control.validate(rig)
    if root:
        root_follow.update({e['owner']: rig.pose.bones[e['owner']].constraints[e['name']]
                            for e in root['constraints']})
    limb_by_source = {name: (key, entry) for key, entry in inventory['rigs'].items()
                      for name in match.pose_names(entry)}
    eye_by_source = dict(zip(eyes['sources'], ('L','R'))) if eyes else {}
    # Process each native parent before its descendants. Matching a whole chain
    # at its first source is safe; its descendants then use the final parent.
    for name in sorted(desired, key=lambda n: len(rig.pose.bones[n].parent_recursive)):
        if name in handled:
            continue
        if name in limb_by_source:
            key, entry = limb_by_source[name]
            names = set(match.pose_names(entry))
            if changed.intersection(names):
                limb_match(key, entry)
            handled.update(names)
        elif torso and name in torso['sources']:
            if changed.intersection(torso['sources']):
                torso_match()
            handled.update(torso['sources'])
        elif name in eye_by_source:
            if name in changed:
                target = rig.pose.bones[eyes['targets'][eye_by_source[name]]]
                matrix = target.matrix.copy()
                distance = max((matrix.translation-rig.pose.bones[name].matrix.translation).length, .01)
                matrix.translation = desired[name].translation + desired[name].to_3x3().col[1].normalized() * distance
                match._set_matrix(context, rig, target, matrix)
        elif name in changed:
            pb = rig.pose.bones[name]
            follow = root_follow.get(name)
            if pb.constraints and (follow is None or list(pb.constraints) != [follow]):
                raise ValueError(f'{name} has an unsupported constraint; this Pose was not applied.')
            if (stable_sources and name not in authored_names and not pb.constraints
                    and any(parent.name in stable_sources for parent in pb.parent_recursive)
                    and _difference(pb.matrix, desired[name]) <= 4e-4):
                # These free descendants already follow their matched parent.
                # Reassigning a connected finger's evaluated matrix would clear
                # dormant artist location channels that this preset never keyed.
                continue
            matrix = (rig.pose.bones[follow.subtarget].matrix.inverted() @ desired[name]
                      if follow is not None else desired[name])
            match._set_matrix(context, rig, pb, matrix)
    errors = {name: _difference(rig.pose.bones[name].matrix, matrix) for name, matrix in desired.items()}
    worst = max(errors, key=errors.get)
    if errors[worst] > 4e-4:
        raise ValueError(f'Pose could not match {worst} without a jump ({errors[worst]:.4g}); no changes kept.')
    bone_collections._FRAME_CACHE.clear()
    bone_collections._frame_visibility(context.scene)
    return modes


def apply(context, rig, action):
    return apply_channels(context, rig, channels(action, rig), metadata=asset_metadata(action))


def _match_original(context, rig, desired, changed, *, preserve_modes=False):
    """Validate controllers with live constraints, then resume native editing.

    Original's paused constraints must not make an invalid IK solve appear to
    match. Keep its display/locks and Dress session intact; publish matched Body
    channels into its existing return checkpoint only after complete validation.
    """
    from . import body_original_mode as original
    saved = original._require(context, rig)
    original._validate_session_rest(rig, saved)
    relations = original._resolve(rig, saved['constraints'])
    original._restore_channels(rig, saved['channels'])
    for con, entry in relations:
        con.mute = entry['mute']
    match._update(context, rig)
    changed = set(changed) | {n for n in desired if _difference(desired[n], rig.pose.bones[n].matrix) > 1e-7}
    modes = _match(context, rig, desired, changed, sync_controls=True, preserve_modes=preserve_modes)
    saved['channels'] = original._channels(rig)
    for con, _entry in relations:
        con.mute = True
    match._update(context, rig)
    original._bake_sources(context, rig, desired, saved['constraints'])
    original._verify(rig, desired)
    saved['entered_channels'] = original._channels(rig)
    rig[original.SESSION] = json.dumps(saved, separators=(',', ':'))
    return modes


def apply_channels(context, rig, values, *, metadata=None):
    from . import forearm_twist
    with forearm_twist.defer_runtime(context, flush_on_exit=False) as refresh:
        try:
            return _apply_channels(context, rig, values, metadata=metadata, refresh=refresh)
        except Exception:
            refresh()
            raise


def _apply_channels(context, rig, values, *, metadata=None, refresh=lambda: None):
    from . import body_original_mode as original
    if metadata is not None and 'stable_ik_bend' in metadata and type(metadata['stable_ik_bend']) is not bool:
        raise ValueError('This saved Pose has invalid stable IK bend data; no changes were applied.')
    seeds = _validate_stable_ik_seed_metadata(metadata) if metadata is not None else {}
    if context.mode not in {'OBJECT', 'POSE'} or rig.library or rig.data.library or rig.data.users != 1:
        raise ValueError('Apply this Pose to a local, single-user rig in Object or Pose Mode.')
    match._update(context, rig)
    desired = desired_pose(rig, values, metadata)
    changed = {name for name, matrix in desired.items() if _difference(matrix, rig.pose.bones[name].matrix) > 1e-7}
    if metadata is not None:
        # Even an identical visible pose must synchronize stale dormant controls.
        changed.update(values)
    # Drivers on artist transform channels cannot be safely overwritten by a pose.
    if rig.animation_data:
        bbone_paths = {rig.pose.bones[n].path_from_id(prop) for n, fields in values.items()
                       for prop in fields if prop in _BBONE}
        for curve in rig.animation_data.drivers:
            if curve.data_path in bbone_paths or any(curve.data_path.startswith(rig.pose.bones[name].path_from_id())
                   and any('.' + p in curve.data_path for p in ('location', 'rotation_', 'scale')) for name in changed):
                raise ValueError('A transform driver controls this Pose region; no changes were applied.')
    stable = ()
    inventory = None
    if metadata is not None and metadata.get('stable_ik_bend', False) and not original.active(rig):
        inventory = limb_ik._validate_inventory(rig)
        stable = tuple(key for key, entry in inventory['rigs'].items() if key[0] == 'ARM'
                       and all(name in values and _complete_native_transforms(values[name])
                               for name in entry['chain']))
    if seeds:
        applied = tuple(key for key in stable if match.mode_for_rig(rig, inventory['rigs'][key]) == 'IK')
        _validate_stable_ik_seed_inventory(rig, inventory, seeds, applied=applied)
    with _transaction(context, rig) as before:
        if original.active(rig):
            modes = _match_original(context, rig, desired, changed, preserve_modes=metadata is not None)
        elif metadata is not None:
            modes = _match(context, rig, desired, changed, sync_controls=True, preserve_modes=True,
                           stable_ik_bend_limbs=stable, authored_names=values, stable_ik_seeds=seeds)
        else:
            modes = _match(context, rig, desired, changed)
        for name, fields in values.items():
            pb = rig.pose.bones[name]
            for prop, entries in fields.items():
                if prop not in _BBONE:
                    continue
                if _BBONE[prop] == 1:
                    setattr(pb, prop, entries[0])
                else:
                    for index, value in entries.items():
                        getattr(pb, prop)[index] = value
        match._update(context, rig)
        for name, fields in values.items():
            for prop, entries in fields.items():
                if prop in _BBONE:
                    current = getattr(rig.pose.bones[name], prop)
                    if any(abs((current if _BBONE[prop] == 1 else current[i]) - v) > 2e-6 for i, v in entries.items()):
                        raise ValueError(f'{name}: Blender could not apply {prop}; Pose was rolled back.')
        _auto_key(context, rig, before, values)
        refresh()
    return {'bones': len(values), 'modes': modes}


class _AssetChannels(dict):
    def __init__(self, values, metadata):
        super().__init__(values)
        self.metadata = metadata


def _asset_channels(asset, rig):
    def read(action):
        metadata = asset_metadata(action)
        native = set(native_rest(rig))
        names = {ast.literal_eval(found[1]) for curve in _curves(action, rig)
                 if (found := _BONE_PATH.match(curve.data_path))}
        if names - set(rig.pose.bones.keys()):
            raise ValueError('This Pose refers to missing bones; no changes were applied.')
        if metadata is not None:
            if names - native:
                raise ValueError('This saved Pose contains generated-control channels; no changes were applied.')
            return _AssetChannels(channels(action, rig), metadata)
        return None if names - native else channels(action, rig)
    if asset.local_id:
        return read(asset.local_id)
    if not asset.full_library_path:
        raise ValueError('The Pose asset file is unavailable.')
    # Read into Blender's temporary Main; only plain channel values escape it.
    # The asset and any referenced datablocks never enter the character file.
    with bpy.types.BlendData.temp_data() as data:
        with data.libraries.load(asset.full_library_path) as (src, dst):
            if asset.name not in src.actions:
                raise ValueError('The Pose asset no longer exists in that file.')
            dst.actions = [asset.name]
        return read(dst.actions[0])


def _needs_control_matching(rig, values, flipped=False):
    """Free native channels need no generated-control compatibility baseline.

    Fingers commonly remain ordinary FK children of a controlled hand. Native
    application is sufficient even when their ancestors have generated controls;
    only constraints on the actual destinations need the matching service.
    """
    names = {bpy.utils.flip_name(name) if flipped else name for name in values}
    missing = names - set(rig.pose.bones.keys())
    if missing:
        raise ValueError('This Pose has no opposite bone: ' + ', '.join(sorted(missing)[:4]))
    return any(rig.pose.bones[name].constraints for name in names)


def _apply_native(context, values, flipped):
    # Blender filters Action channels by destination selection. A selected
    # arm/controller can exclude every finger in an otherwise valid hand Pose.
    # Keep intentional partial selections; use the Pose's destinations only
    # when the artist's selection has no overlap. Mirror an authored-side
    # subset before considering that fallback.
    rig = context.object
    selections = [(pb if hasattr(pb, 'select') else pb.bone) for pb in rig.pose.bones]
    before = [(bone, bone.select) for bone in selections]
    active = rig.data.bones.active
    selected = {bone.name for bone, state in before if state}
    sources = {name for name in values if bpy.utils.flip_name(name) != name}
    targets = {bpy.utils.flip_name(name) for name in sources}
    destinations = {bpy.utils.flip_name(name) if flipped else name for name in values}
    transfer = flipped and bool(selected & sources) and not bool(selected & targets)
    fallback = bool(destinations and selected) and not transfer and not bool(selected & destinations)
    try:
        if transfer or fallback:
            wanted = ((selected - sources) | {bpy.utils.flip_name(name) for name in selected & sources}
                      if transfer else destinations)
            for bone in selections:
                bone.select = bone.name in wanted
        return bpy.ops.poselib.apply_pose_asset(flipped=flipped)
    finally:
        if transfer or fallback:
            for bone, state in before:
                bone.select = state
            rig.data.bones.active = active


class CHARACTERDESIGNER_OT_apply_control_pose(bpy.types.Operator):
    bl_idname = 'character_designer.apply_control_pose'
    bl_label = 'Apply Pose'
    bl_description = 'Apply a Pose, matching generated controls when needed; Shift-double-click mirrors it'
    bl_options = {'REGISTER', 'UNDO'}

    flipped: bpy.props.BoolProperty(name='Flipped', description='Apply the pose to the opposite side', default=False)

    @classmethod
    def poll(cls, context):
        obj, asset = context.object, getattr(context, 'asset', None)
        return bool(obj and obj.type == 'ARMATURE' and obj.mode == 'POSE' and asset
                    and asset.id_type == 'ACTION')

    def execute(self, context):
        from . import body_setup
        local = getattr(context.asset, 'local_id', None)
        managed = local is not None and ASSET_METADATA in local
        if not body_setup.has_generated(context.object) and not managed:
            return bpy.ops.poselib.apply_pose_asset(flipped=self.flipped)
        asset = context.asset
        try:
            values = _asset_channels(asset, context.object)
            if values is None:
                return bpy.ops.poselib.apply_pose_asset(flipped=self.flipped)
            metadata = getattr(values, 'metadata', None)
            if metadata is None and not _needs_control_matching(context.object, values, self.flipped):
                # Control-authored assets and unconstrained native channels
                # already work through Blender, including rigs predating the
                # generated-control Rest baseline. Resolve an unrelated
                # selection while preserving native partial selection,
                # mirror, auto-key and undo behavior for those assets.
                return _apply_native(context, values, self.flipped)
            if self.flipped:
                from .control_pose_mirror import mirrored_channels
                match._update(context, context.object)
                values = (mirrored_channels(context.object, values, metadata=metadata) if metadata is not None
                          else mirrored_channels(context.object, values))
            if metadata is not None:
                apply_channels(context, context.object, values, metadata=metadata)
            else:
                apply_channels(context, context.object, values)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def _menu(self, context):
    from . import body_setup
    if CHARACTERDESIGNER_OT_apply_control_pose.poll(context) and body_setup.has_generated(context.object):
        self.layout.separator()
        for flipped, label in ((False, 'Apply Pose (Character Designer)'),
                               (True, 'Apply Mirrored Pose (Character Designer)')):
            self.layout.operator(CHARACTERDESIGNER_OT_apply_control_pose.bl_idname,
                                 text=label, icon='POSE_HLT').flipped = flipped


def _route_double_click():
    import sys
    official = sys.modules.get('pose_library.keymaps')
    if official is None:
        return
    # Blender's merged user keymap can order the original add-on binding before
    # a new head=True item. Disable only the owned official default binding and
    # restore its activation on unregister; user bindings keep their settings.
    for km, item in official.addon_keymaps:
        if (km.name == 'Asset Browser Main' and item.idname == 'poselib.apply_pose_asset'
                and item.active and item.type == 'LEFTMOUSE' and item.value == 'DOUBLE_CLICK'
                and not (item.ctrl or item.alt or item.shift or item.oskey or item.any)):
            _REPLACED.append(item)
            item.active = False
    return None


def register():
    if not CHARACTERDESIGNER_OT_apply_control_pose.is_registered:
        bpy.utils.register_class(CHARACTERDESIGNER_OT_apply_control_pose)
    config = bpy.context.window_manager.keyconfigs.addon
    if config and not _KEYMAPS:
        km = config.keymaps.get('Asset Browser Main') or config.keymaps.new(name='Asset Browser Main', space_type='FILE_BROWSER')
        for flipped in (False, True):
            kmi = km.keymap_items.new(CHARACTERDESIGNER_OT_apply_control_pose.bl_idname,
                                     'LEFTMOUSE', 'DOUBLE_CLICK', shift=flipped, head=True)
            kmi.properties.flipped = flipped
            _KEYMAPS.append((km, kmi))
        bpy.types.ASSETBROWSER_MT_context_menu.append(_menu)
    _route_double_click()
    if not bpy.app.background and not bpy.app.timers.is_registered(_route_double_click):
        bpy.app.timers.register(_route_double_click, first_interval=1.)


def unregister():
    if bpy.app.timers.is_registered(_route_double_click):
        bpy.app.timers.unregister(_route_double_click)
    for item in _REPLACED:
        try:
            if item.idname == 'poselib.apply_pose_asset' and not item.active:
                item.active = True
        except ReferenceError:
            pass
    _REPLACED.clear()
    for km, kmi in _KEYMAPS:
        km.keymap_items.remove(kmi)
    if _KEYMAPS:
        bpy.types.ASSETBROWSER_MT_context_menu.remove(_menu)
    _KEYMAPS.clear()
    if CHARACTERDESIGNER_OT_apply_control_pose.is_registered:
        bpy.utils.unregister_class(CHARACTERDESIGNER_OT_apply_control_pose)
