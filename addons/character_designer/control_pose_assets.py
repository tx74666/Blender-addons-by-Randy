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
# A synchronous neutral-match capability is revoked in finally, never persisted.
_NATURAL_NEUTRAL_CAPABILITIES = {}


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


def _pose_rest_scope(saved, current, names):
    """A regional asset depends on its keyed bones and both ancestry chains.

    Sibling branches keep the current pose and Rest. In particular a hand Pose
    does not reinterpret an edited leg or neck. Walking both records means a
    reparented destination cannot evade the source-parent proof.
    """
    scope = set(names)
    for rest in (saved, current):
        for name in names:
            seen = set()
            while name is not None:
                if name in seen or name not in rest:
                    raise ValueError('This saved Pose has an invalid native parent chain; no changes were applied.')
                seen.add(name)
                scope.add(name)
                name = rest[name]['parent']
    return scope


def _connected_pose_compatible(rig, name, values, previous, current):
    """Admit only a geometrically unchanged, zero-translation connection edit.

    The caller still compares the Rest matrices, lengths, parent and inheritance
    exactly. Connected bones suppress translation; a complete zero location
    has the same meaning on either side of this flag. Unkeyed ancestors retain
    their current channels and must also have zero translation. No Rest or
    saved asset/session record is rewritten by this compatibility check.
    """
    if previous['parent'] is None or current['parent'] is None or not hasattr(values, 'get'):
        return False
    fields = values.get(name)
    location = (fields.get('location', {}) if fields is not None
                else {index: value for index, value in enumerate(rig.pose.bones[name].location)})
    return (set(location) == {0, 1, 2}
            and all(type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e-6
                    for value in location.values()))


def _compatible(rig, names, metadata=None):
    if metadata is None and BASELINE not in rig:
        raise ValueError('Run Update once to record this rig\'s native Pose compatibility baseline.')
    saved = metadata if metadata is not None else json.loads(rig[BASELINE])
    current = native_rest(rig)
    if set(current) != set(saved['rest']):
        raise ValueError('Native skeleton structure differs from this Pose\'s baseline; this Pose was not applied.')
    missing = set(names) - set(current)
    if missing:
        raise ValueError('Pose uses missing or non-native bones: ' + ', '.join(sorted(missing)[:4]))
    scope = set(saved['rest'])
    if metadata is not None:
        # Reuse the complete saved Rest schema proof without treating the asset
        # record as a live Original session. Its unrelated branches are evidence
        # of authoring context, not destinations of this regional Pose.
        from . import body_original_mode as original
        try:
            original._validate_session_rest(rig, {'version': 1, 'bones': sorted(rig.data.bones.keys()),
                                                  'rest': saved['rest']}, current=saved['rest'])
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError('This saved Pose has invalid native Rest compatibility data; no changes were applied.') from exc
        if any(not original._valid_rest_entry(entry) for entry in current.values()):
            raise ValueError('The current native Rest contains invalid or non-finite data; this Pose was not applied.')
        scope = _pose_rest_scope(saved['rest'], current, names)
    # Legacy Action-only assets retain their generation-time whole-rig proof.
    for name in sorted(scope):
        old = saved['rest'][name]
        now = current[name]
        relations = [key for key in ('parent', 'connected', 'inherit_scale', 'inherit_rotation', 'local_location')
                     if now[key] != old[key]]
        if (metadata is not None and relations == ['connected']
                and _connected_pose_compatible(rig, name, names, old, now)):
            relations = []
        if (relations
                or abs(now['length'] - old['length']) > 1e-6
                or max(abs(a-b) for x, y in zip(now['matrix'], old['matrix']) for a, b in zip(x, y)) > 2e-6):
            detail = ' (' + ', '.join(relations) + ')' if relations else ''
            raise ValueError(f'Native Rest or structure changed at {name}{detail}; this Pose was not applied.')
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
            if bone.use_connect:
                # Native Pose evaluation ignores connected-bone location,
                # including offsets produced by native Action.flip_with_pose.
                # The Apply path still writes the exact authored raw channels.
                fields['location'] = (0., 0., 0.)
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
    pole_angles = [(con, float(con.pole_angle)) for pb in rig.pose.bones
                   for con in pb.constraints if con.type == 'IK']
    pole_metadata = match._leg_pole_metadata(rig)
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
        for con, angle in pole_angles:
            con.pole_angle = angle
        match._restore_leg_pole_metadata(rig, pole_metadata)
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
           stable_ik_bend_limbs=(), authored_names=(), stable_ik_seeds=None, _pose_only_owner=False):
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
    if _pose_only_owner:
        bone_collections._frame_visibility(context.scene, objects=(rig,))
    else:
        bone_collections._frame_visibility(context.scene)
    return modes


def apply(context, rig, action):
    return apply_channels(context, rig, channels(action, rig), metadata=asset_metadata(action))


def _free_finger_plan(context, rig, desired, changed, values, metadata):
    """Prove an ordinary, complete native-finger edit before skipping Body matching.

    This is the same editing domain as free native finger manipulation, with
    managed Rest proof retained by desired_pose. Uncertain domains keep the
    existing matcher; no Original checkpoint is advanced by a regional edit.
    """
    from . import animation_pose_test as test, control_pose_capture as capture
    from . import forearm_original_inventory, bone_collections
    if (not values or metadata is None or not isinstance(metadata, dict)
            or set(metadata.get('names', ())) != set(values)
            or not set(changed).issubset(values)
            or context.scene.tool_settings.use_keyframe_insert_auto
            or test.natural_active(rig)
            or getattr(getattr(context, 'screen', None), 'is_animation_playing', False)
            or context.object != rig or context.mode not in {'OBJECT', 'POSE'}):
        return None
    names = set(values)
    if any(not _complete_native_transforms(values[name])
           or rig.pose.bones[name].constraints
           or rig.data.bones[name].bbone_segments != 1 for name in names):
        return None
    # A generated limb source can have no constraint of its own yet still be
    # controlled by a child's IK. Reuse the validated native finger identity,
    # rather than treating every constraint-free Body bone as ordinary FK.
    inventory = forearm_original_inventory.validate(rig)
    fingers = set(capture._finger_names(rig, inventory))
    original_group = rig.data.collections_all.get('Original')
    if original_group is None or original_group.get(bone_collections.GROUP_KEY) != 'Original':
        return None
    if not names.issubset(fingers & capture._body_names(rig, inventory)):
        return None
    descendants = {child.name for name in names
                   for child in rig.pose.bones[name].children_recursive}
    if not descendants.issubset(names):
        return None
    # No hidden rest-data evaluator or direct/dependent finger driver may
    # obtain this shortcut. The general transform-driver guard still runs.
    data_animation = rig.data.animation_data
    if data_animation and data_animation.drivers:
        return None
    pose_paths = tuple(rig.pose.bones[name].path_from_id() for name in names)
    bone_paths = tuple(rig.data.bones[name].path_from_id() for name in names)
    for curve in getattr(rig.animation_data, 'drivers', ()):
        if (not curve.data_path.startswith('pose.bones[')
                or curve.data_path.startswith(pose_paths)):
            return None
        for variable in curve.driver.variables:
            for target in variable.targets:
                if (target.id == rig and (target.bone_target in names
                        or target.data_path.startswith(pose_paths))
                        or target.id == rig.data and target.data_path.startswith(bone_paths)):
                    return None
    if any(name not in names and _difference(matrix, rig.pose.bones[name].matrix) > 1e-7
           for name, matrix in desired.items()):
        return None
    bases = []
    for name in sorted(names, key=lambda n: len(rig.pose.bones[n].parent_recursive)):
        pb = rig.pose.bones[name]
        parent = pb.parent
        parent_matrix = desired.get(parent.name, parent.matrix) if parent else Matrix.Identity(4)
        location = (tuple(values[name]['location'][index] for index in range(3))
                    if pb.bone.use_connect else None)
        bases.append((name, _convert(pb.bone, desired[name], parent_matrix, invert=True), location))
    return bases


def _apply_free_finger_plan(context, rig, desired, plan):
    """One graph evaluation for a proven closed native FK finger region."""
    for name, basis, location in plan:
        pb = rig.pose.bones[name]
        pb.matrix_basis = basis
        if location is not None:
            # Native Action Apply writes these keyed channels even though a
            # connected bone ignores them when evaluating its visible pose.
            # Keep their exact authored values instead of matrix-roundtrip noise.
            pb.location = location
    match._update(context, rig)
    errors = {name: _difference(rig.pose.bones[name].matrix, matrix)
              for name, matrix in desired.items()}
    worst = max(errors, key=errors.get)
    if (not all(limb_ik._matrix_is_finite(rig.pose.bones[name].matrix) for name in desired)
            or errors[worst] > 4e-4):
        raise ValueError(f'Pose could not match {worst} without a jump ({errors[worst]:.4g}); no changes kept.')
    return {}


def _match_original(context, rig, desired, changed, *, preserve_modes=False,
                    authored_values=None, metadata=None):
    """Validate controllers with live constraints, then resume native editing.

    Original's paused constraints must not make an invalid IK solve appear to
    match. Keep its display/locks and Dress session intact; publish matched Body
    channels into its existing return checkpoint only after complete validation.
    """
    from . import body_original_mode as original
    saved = original._require(context, rig)
    original._validate_session_rest(rig, saved)
    relations = original._resolve(rig, saved['constraints'])
    if preserve_modes and all(con.mute for con, _entry in relations):
        plan = _free_finger_plan(context, rig, desired, changed, authored_values, metadata)
        if plan is not None:
            # Preserve both old checkpoints verbatim. Original Return merges
            # the existing native edits and these new finger edits together,
            # just as it does for a manual/native finger Pose application.
            return _apply_free_finger_plan(context, rig, desired, plan)
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


def apply_channels(context, rig, values, *, metadata=None, _pose_only_owner=False):
    from . import animation_pose_test
    if animation_pose_test.natural_active(rig):
        raise ValueError('Choose Pose to finish the static Rest test before applying this Pose.')
    from . import forearm_twist
    with forearm_twist.defer_runtime(context, flush_on_exit=False) as refresh:
        try:
            return _apply_channels(context, rig, values, metadata=metadata, refresh=refresh,
                                   _pose_only_owner=_pose_only_owner)
        except Exception:
            refresh()
            raise


def _natural_neutral_owner(context, rig):
    from . import animation_pose_test as pose, body_original_mode as original
    if (not pose.natural_active(rig) or original.active(rig)
            or context.scene.tool_settings.use_keyframe_insert_auto):
        raise ValueError('Natural neutral matching needs its owned Controls test with Auto Key off.')
    pose._admission(context, rig, natural=True)
    pose._natural_plan(rig)
    action, record, _refs = pose._natural_matching_record(context, rig)
    if not pose._natural_record(record):
        raise ValueError('The active test does not own Natural neutral matching.')
    return action


def _apply_natural_neutral(context, rig, values, *, metadata):
    """Only the owned ALL_BODY neutral reset may bypass Natural asset refusal."""
    from . import animation_pose_test as pose, control_pose_capture as capture
    from . import forearm_original_inventory, forearm_twist
    action = _natural_neutral_owner(context, rig)
    inventory = forearm_original_inventory.validate(rig)
    names = capture._resolve_names(rig, inventory, 'ALL_BODY', True)
    expected = {name: {'location': dict(enumerate((0., 0., 0.))),
                       'rotation_quaternion': dict(enumerate((1., 0., 0., 0.))),
                       'scale': dict(enumerate((1., 1., 1.)))} for name in names}
    expected_metadata = {'version': 1, 'source_object': rig.name, 'source_slot': 'OB' + rig.name,
                         'rest': native_rest(rig), 'scope': 'ALL_BODY',
                         'names': names, 'include_fingers': True}
    if values != expected or metadata != expected_metadata:
        raise ValueError('Natural neutral matching refused a non-neutral or changed Body payload.')
    capability = object()
    _NATURAL_NEUTRAL_CAPABILITIES[capability] = (context, rig, values, metadata, action,
        pose._digest({'values': values, 'metadata': metadata}))
    try:
        with forearm_twist.defer_runtime(context, flush_on_exit=False) as refresh:
            try:
                return _apply_channels(context, rig, values, metadata=metadata, refresh=refresh,
                                       _pose_only_owner=True, _natural_neutral=capability)
            except Exception:
                refresh()
                raise
    finally:
        _NATURAL_NEUTRAL_CAPABILITIES.pop(capability, None)


def _apply_channels(context, rig, values, *, metadata=None, refresh=lambda: None,
                    _pose_only_owner=False, _natural_neutral=None):
    from . import animation_pose_test
    from . import body_original_mode as original
    if animation_pose_test.natural_active(rig) or _natural_neutral is not None:
        proof = (_NATURAL_NEUTRAL_CAPABILITIES.get(_natural_neutral)
                 if type(_natural_neutral) is object else None)
        if (proof is None or not _pose_only_owner
                or not all(current is saved for current, saved in
                           zip((context, rig, values, metadata), proof[:4]))
                or _natural_neutral_owner(context, rig) is not proof[4]
                or animation_pose_test._digest({'values': values, 'metadata': metadata}) != proof[5]):
            raise ValueError('Choose Pose to finish the static Rest test before applying this Pose.')
    if metadata is not None and 'stable_ik_bend' in metadata and type(metadata['stable_ik_bend']) is not bool:
        raise ValueError('This saved Pose has invalid stable IK bend data; no changes were applied.')
    seeds = _validate_stable_ik_seed_metadata(metadata) if metadata is not None else {}
    if type(_pose_only_owner) is not bool:
        raise ValueError('Invalid Pose recovery ownership option; no changes were applied.')
    pose_owner = False
    if (_pose_only_owner and context.mode in {'OBJECT', 'POSE'}
            and not rig.library and not rig.data.library):
        from . import animation_pose_test
        pose_owner = animation_pose_test._pose_owner(rig)
    if (context.mode not in {'OBJECT', 'POSE'} or rig.library or rig.data.library
            or (not pose_owner if _pose_only_owner
                else rig.data.users != 1)):
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
            modes = _match_original(context, rig, desired, changed, preserve_modes=metadata is not None,
                                    authored_values=values if _natural_neutral is None else None,
                                    metadata=metadata)
        elif metadata is not None:
            plan = (_free_finger_plan(context, rig, desired, changed, values, metadata)
                    if _natural_neutral is None else None)
            if plan is not None:
                modes = _apply_free_finger_plan(context, rig, desired, plan)
            else:
                modes = _match(context, rig, desired, changed, sync_controls=True, preserve_modes=True,
                               stable_ik_bend_limbs=stable, authored_names=values, stable_ik_seeds=seeds,
                               _pose_only_owner=_pose_only_owner)
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
    from . import animation_pose_test
    if animation_pose_test.natural_active(rig):
        raise ValueError('Choose Pose to finish the static Rest test before applying or blending a Pose asset.')
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
        from . import animation_pose_test
        if animation_pose_test.natural_active(context.object):
            self.report({'WARNING'}, 'Choose Pose to finish the static Rest test before applying a Pose asset.')
            return {'CANCELLED'}
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
            original_names = set(values)
            if context.scene.tool_settings.use_keyframe_insert_auto:
                from . import animation_pose_test
                rig = context.object
                saved = getattr(context.scene, 'character_designer_animation_worklist', None)
                animation = rig.animation_data
                action = animation.action if animation else None
                worklist_owner = bool(action is not None and saved is not None and any(
                    row.rig == rig and (row.source_action == action or row.custom_action == action)
                    for row in saved.items))
                if animation_pose_test.active(rig) or worklist_owner:
                    # The older generic Auto Key copy cannot preserve a Pose
                    # test token or publish a worklist-owned Custom variant.
                    # Share native-style selected-region and mirror transfer
                    # semantics, retaining Apply's exact factor 1.
                    from . import control_pose_blend
                    session = control_pose_blend.begin(context, rig=rig, values=values,
                        metadata=metadata, flipped=self.flipped, respect_selection=True)
                    try:
                        control_pose_blend.preview(context, session, 1.)
                        control_pose_blend.confirm(context, session)
                    except Exception as error:
                        if not session.get('closed', False):
                            try:
                                control_pose_blend.cancel(context, session)
                            except Exception as recovery_error:
                                raise RuntimeError('Apply Pose failed; its owned recovery needs review: '
                                                   + str(recovery_error)) from error
                        raise
                    return {'FINISHED'}
            if self.flipped:
                from .control_pose_mirror import mirrored_channels
                match._update(context, context.object)
                values = (mirrored_channels(context.object, values, metadata=metadata) if metadata is not None
                          else mirrored_channels(context.object, values))
                metadata = getattr(values, 'metadata', metadata)
            from . import control_pose_blend
            values = control_pose_blend._selected_values(context.object, values, flipped=self.flipped,
                original_names=original_names, respect_selection=True)
            if metadata is not None:
                metadata = dict(metadata, names=sorted(values))
            # A normal Pose test may deliberately retain verified current-data
            # references. Reuse its exact ownership admission, never infer that
            # arbitrary shared Armature data is safe to write.
            from . import animation_pose_test
            pose_only_owner = (context.object.data.users != 1
                               and animation_pose_test._pose_owner(context.object))
            if metadata is not None:
                apply_channels(context, context.object, values, metadata=metadata,
                               _pose_only_owner=pose_only_owner)
            else:
                apply_channels(context, context.object, values, _pose_only_owner=pose_only_owner)
            return {'FINISHED'}
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}


def _menu(self, context):
    from . import control_pose_blend_ui
    control_pose_blend_ui.draw_extra_menu(self, context)


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
    from . import control_pose_blend_ui
    if all(cls.is_registered for cls in control_pose_blend_ui.CLASSES):
        control_pose_blend_ui.route_native_menu()
    return None


def register():
    from . import control_pose_blend_ui
    created_apply = not CHARACTERDESIGNER_OT_apply_control_pose.is_registered
    ui_before = control_pose_blend_ui._registration_state()
    key_count, replaced_count = len(_KEYMAPS), len(_REPLACED)
    timer_existed = bpy.app.timers.is_registered(_route_double_click)
    added_menu = False
    try:
        if created_apply:
            bpy.utils.register_class(CHARACTERDESIGNER_OT_apply_control_pose)
        control_pose_blend_ui.register()
        config = bpy.context.window_manager.keyconfigs.addon
        if config and not _KEYMAPS:
            km = config.keymaps.get('Asset Browser Main') or config.keymaps.new(name='Asset Browser Main', space_type='FILE_BROWSER')
            for flipped in (False, True):
                kmi = km.keymap_items.new(CHARACTERDESIGNER_OT_apply_control_pose.bl_idname,
                                         'LEFTMOUSE', 'DOUBLE_CLICK', shift=flipped, head=True)
                kmi.properties.flipped = flipped
                _KEYMAPS.append((km, kmi))
            bpy.types.ASSETBROWSER_MT_context_menu.append(_menu)
            added_menu = True
        _route_double_click()
        if not bpy.app.background and not bpy.app.timers.is_registered(_route_double_click):
            bpy.app.timers.register(_route_double_click, first_interval=1.)
    except Exception:
        if not timer_existed and bpy.app.timers.is_registered(_route_double_click):
            bpy.app.timers.unregister(_route_double_click)
        for item in _REPLACED[replaced_count:]:
            item.active = True
        del _REPLACED[replaced_count:]
        if added_menu:
            bpy.types.ASSETBROWSER_MT_context_menu.remove(_menu)
        for km, item in reversed(_KEYMAPS[key_count:]):
            km.keymap_items.remove(item)
        del _KEYMAPS[key_count:]
        control_pose_blend_ui._rollback_registration(ui_before)
        if created_apply and CHARACTERDESIGNER_OT_apply_control_pose.is_registered:
            bpy.utils.unregister_class(CHARACTERDESIGNER_OT_apply_control_pose)
        raise


def unregister():
    from . import control_pose_blend_ui
    control_pose_blend_ui.unregister()
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
